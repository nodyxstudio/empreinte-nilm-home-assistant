"""Le cœur d'Empreinte : lit la puissance, détecte, regroupe, publie."""
from __future__ import annotations

import asyncio
import copy
import re
import logging
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import engine
from .engine import engine_median
from .const import (APPLIANCE_TIPS, CLIM_LAG_KWH, PROGRAMME_GAP_S, TIP_IDS, CLIM_MAX_W, VEILLE_NAME, HYP_MIN_DAYS, HYP_MIN_N, HYP_MIN_SCORE, AUTO_NAME_COHERENCE, AUTO_NAME_DAYS, AUTO_NAME_MARGIN, AUTO_NAME_MIN, AUTO_NAME_SCORE, BACKFILL_DAYS, CONF_CLIMATE, CONF_CLIMATE_ENERGY, CONF_EXTRA_LINES, CONF_FRIDGE_KWH, CONF_MEASURED, CONF_SHARED, CONF_POWER_ENTITY, DATA_DIR, DB_FILE, DOMAIN,
                    EVENTS_SENT_DAYS, PROCESS_EVERY_S, SAMPLES_KEEP_DAYS)
from .shared import SharedPlugs
from .store import Store

# Catégories : des appareils comptés ensemble, choisies par l'utilisateur. La première, « Électroménager »,
# reçoit d'office le four, les plaques, le lave-vaisselle, le lave-linge et le sèche-linge (une confusion entre
# four et plaques, ou entre lave-linge et lave-vaisselle, ne fausse pas leur total). Pas la cafetière, la bouilloire…
DEFAULT_CATS = [{"id": "menager", "name": "Électroménager", "dash": False, "since": None, "total": 0.0}]
MENAGER_RE = re.compile(r"lave[- ]?linge|lave[- ]?vaisselle|s[eè]che[- ]?linge|plaque|\bfour\b(?!.*micro)", re.I)

_LOGGER = logging.getLogger(__name__)
PROFILE_MAX_POINTS = 120
RESEGMENT_KEY = "resegment_v7"   # à changer quand la façon de mesurer change : tout est re-mesuré


def letter(n: int) -> str:
    return chr(65 + n % 26) + (str(n // 26 + 1) if n >= 26 else "")


LIVE_CONFIRM_S = 60
CYCLE_WIN_S = 600     # un saut de même taille dans les 10 min d'avant : ça clignote, ce n'est pas un chauffage


class Live:
    """Détection en direct, pour allumer les capteurs « en marche » sans attendre.

    Chaque saut de puissance vers le haut qui ressemble à un appareil validé l'allume ;
    un saut vers le bas de même taille l'éteint. Plusieurs appareils peuvent tourner en même temps.
    Un arrêt de moins de GAP_S (thermostat d'un four) ne compte pas comme une fin.
    """

    def __init__(self):
        self.prev = None
        self.running: dict[str, dict] = {}   # gid -> {start, power, off_since, floor}
        self.waiting: dict[str, dict] = {}   # sauts qui ressemblent à un appareil, à confirmer (voir LIVE_CONFIRM_S)
        self.recent: deque = deque()         # les 15 dernières minutes, pour connaître le fond
        self.edges: deque = deque()          # les sauts des 10 dernières minutes (instant, taille)

    def feed(self, t: float, p: float, cents: dict[str, dict], others: dict[str, dict] | None = None) -> bool:
        """cents : les appareils validés (ceux qu'on annonce en direct). others : les autres appareils nommés, pour ne pas
        annoncer le chauffage quand le saut ressemble davantage aux plaques."""
        changed = False
        floor = engine.quantile([q[1] for q in self.recent], 0.2) if self.recent else p
        self.recent.append((t, p))
        while self.recent[0][0] < t - engine.BASE_WIN_S:
            self.recent.popleft()
        while self.edges and self.edges[0][0] < t - CYCLE_WIN_S:
            self.edges.popleft()
        if self.prev is not None:
            d = p - self.prev
            if abs(d) >= engine.EDGE_W:
                self.edges.append((t, d))
            if d >= engine.EDGE_W:
                gid = self._best(d, {g: c["P"] for g, c in cents.items()
                                     if g not in self.running or self.running[g]["off_since"]}, 0.18)
                if gid:
                    r = self.running.get(gid)
                    if r and cents[gid].get("cyc", 0) >= 1:
                        r["off_since"] = None             # un four : son thermostat coupe et relance
                        changed = True
                    elif r:
                        del self.running[gid]             # un appareil qui ne clignote pas, qui s'éteint et se rallume :
                        changed = True                    # c'était autre chose (plaques, four…)
                    elif gid not in self.waiting:
                        self.waiting[gid] = {"start": t, "power": d, "off_since": None, "floor": floor, "before": self.prev}
            elif d <= -engine.EDGE_W:
                on = {g: r["power"] for g, r in self.running.items() if not r["off_since"]}
                gid = self._best(-d, on, 0.25)
                if gid:
                    self.running[gid]["off_since"] = t
                    changed = True
                elif on and abs(sum(on.values()) + d) <= max(250, 0.25 * -d):
                    for g in on:
                        self.running[g]["off_since"] = t
                    changed = True
        # À confirmer : le pic de démarrage d'un chauffage est passé après 1 min. Si la puissance reste bien plus haute,
        # le saut n'était que le début d'un plus gros appareil (une valeur reçue en pleine montée) : ce n'est pas lui.
        for g, r in list(self.waiting.items()):
            if p < r["floor"] + 0.5 * r["power"]:
                del self.waiting[g]
            elif t - r["start"] >= LIVE_CONFIRM_S:
                del self.waiting[g]
                step = p - r["before"]
                if step <= 1.6 * r["power"] and g in cents and not self._closer_other(step, cents[g]["P"], others)                         and not self._cycling(r, cents[g]):
                    self.running[g] = r
                    changed = True
        # la courbe est revenue à son fond : quoi qu'il se soit passé, il ne peut plus tourner
        for g, r in self.running.items():
            if not r["off_since"] and "floor" in r and p < r["floor"] + 0.5 * r["power"]:
                r["off_since"] = t
                changed = True
        for g in [g for g, r in self.running.items()
                  if (r["off_since"] and t - r["off_since"] > engine.GAP_S) or t - r["start"] > engine.MAX_EVENT_S]:
            del self.running[g]
            changed = True
        self.prev = p
        return changed

    def _cycling(self, r, c):
        """Un saut de même taille dans les 10 minutes d'avant : quelque chose s'allume et s'éteint à cette puissance (le
        thermostat des plaques ou du four). Un appareil qui ne clignote pas (chauffage) n'y est pour rien."""
        if c.get("cyc", 0) >= 1:
            return False
        return any(r["start"] - CYCLE_WIN_S <= te < r["start"] - 1 and abs(math.log(abs(d) / max(r["power"], 1))) < 0.3
                   for te, d in self.edges)

    @staticmethod
    def _closer_other(step, power, others):
        """Une fois l'appareil à son régime (après LIVE_CONFIRM_S), son saut ressemble-t-il plus à un autre appareil connu
        (les plaques vers 900 W plutôt que le chauffage vers 700 W) ? Alors on ne l'annonce pas : l'analyse tranchera."""
        mine = abs(math.log(max(step, 1) / max(power, 1)))
        return any(abs(math.log(max(step, 1) / max(o["P"], 1))) < mine for o in (others or {}).values())

    @staticmethod
    def _best(x, powers: dict, tol):
        best, bd = None, tol
        for g, pw in powers.items():
            d = abs(math.log(max(x, 1) / max(pw, 1)))
            if d < bd:
                best, bd = g, d
        return best

    def get(self, gid):
        """L'appareil reste « en marche » pendant une courte coupure ; sa puissance vaut alors 0."""
        return self.running.get(gid)


class Coordinator:
    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        opts = {**entry.data, **entry.options}
        self.power_entity = entry.data.get(CONF_POWER_ENTITY)
        self.climate_entity = opts.get(CONF_CLIMATE) or None          # réglages facultatifs (voir const.py)
        self.clim_energy = opts.get(CONF_CLIMATE_ENERGY) or None
        self.fridge_kwh = float(opts.get(CONF_FRIDGE_KWH) or 0)
        self.extra_ids: list[str] = list(opts.get(CONF_EXTRA_LINES) or [])
        self.shared_cfg: list[dict] = [p for p in (opts.get(CONF_SHARED) or []) if p.get("entity")]
        self.shared = SharedPlugs(self.shared_cfg)
        self.store: Store | None = None
        self.rows: dict[int, dict] = {}       # id -> start, end, base, prof
        self.evs: list[engine.Ev] = []
        self.sims: dict[int, float] = {}
        self.groups: dict[str, dict] = {}
        self.cents: dict[str, dict] = {}
        self.buffer: list[tuple[float, float]] = []
        self.processed_until = 0.0
        self.last_run = None
        self.last_error = None
        self.live = Live()
        self.lock = asyncio.Lock()
        self.undo: list = []           # pile d'annulation (20 niveaux)
        self.add_entities = {}
        self.entities: dict[str, list] = {}
        self._unsubs = []
        self.counter = 0
        self.pending: list[dict] = []        # « en ce moment, c'est… » en attente
        self.measured: list[str] = list(entry.options.get(CONF_MEASURED) or [])   # prises mesurées (PC…)
        self.sub_now: dict[str, float] = {}
        self.running_parts: list = []
        self.tracks: list[dict] = []          # appareils suivis de leur démarrage à leur arrêt
        self.hints: dict[int, str] = {}      # « peut-être » : id de l'utilisation -> nom proposé
        self.doubts: set[int] = set()        # « pas sûr » : gardées mais pas utilisées pour apprendre
        self.open_start = None
        self.learned_labels: dict[str, str] = {}
        self._mem: dict = {}
        self._renum: list = []               # analyse refaite : anciens numéros à reporter (voir _renumber)
        self._first = None

    # ------------------------------------------------------------ démarrage
    async def async_start(self):
        path = self.hass.config.path(DATA_DIR, DB_FILE)
        self.store = await self.hass.async_add_executor_job(Store, path)
        if CONF_MEASURED not in self.entry.options:
            self.measured = self._detect_measured()
        for p in self.shared_cfg:                      # la prise partagée est retirée de la courbe, comme une prise mesurée
            if p["entity"] not in self.measured:
                self.measured.append(p["entity"])
        for eid in self.measured:
            self.sub_now[eid] = self._num(self.hass.states.get(eid))
        if await self.hass.async_add_executor_job(self.store.get_meta, "sub_for") != sorted(self.measured):
            await self._fill_sub()
        await self.hass.async_add_executor_job(self._load)
        await self._backfill()
        self._unsubs.append(async_track_state_change_event(self.hass, [self.power_entity], self._on_state))
        if self.measured:
            self._unsubs.append(async_track_state_change_event(self.hass, self.measured, self._on_measured))
        self._unsubs.append(async_track_time_interval(self.hass, self.async_process, timedelta(seconds=PROCESS_EVERY_S)))
        self.hass.async_create_task(self.async_process())

    async def async_stop(self):
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        async with self.lock:
            if self.buffer:
                buf, self.buffer = self.buffer, []
                await self.hass.async_add_executor_job(self.store.add_samples, buf)
            await self.hass.async_add_executor_job(self.store.close)

    def _meta(self, k, default=None):
        """Une valeur gardée en mémoire (voir _load) : aucune lecture de la base."""
        v = self._mem.get(k)
        return copy.deepcopy(v) if v is not None else default

    def _set_meta(self, k, v):
        self._mem[k] = copy.deepcopy(v)
        self.store.set_meta(k, v)

    def _load(self):
        self.groups = {g["id"]: g for g in self.store.get_meta("groups", [])}
        self.processed_until = self.store.get_meta("processed_until", 0.0)
        self.counter = self.store.get_meta("group_counter", 0)
        self.pending = self.store.get_meta("pending", [])
        self.hints = {int(k): v for k, v in self.store.get_meta("hints", {}).items()}
        self.doubts = set(self.store.get_meta("doubts", []))
        self.tracks = self.store.get_meta("tracks", [])
        self.cats = self.store.get_meta("categories", None) or [dict(c) for c in DEFAULT_CATS]
        self.energy_log = self.store.get_meta("energy_log", [])
        self.lines = self.store.get_meta("lines", None) or {"last": {}, "kwh": {}, "eur": {}, "names": {}}
        # lues par l'affichage (sur le fil principal de HA) : gardées en mémoire, jamais relues dans la base à ce moment-là
        self._mem = {k: self.store.get_meta(k, None) for k in ('verdicts', 'program_minutes', 'program_history', 'tracks_done')}
        self.shared = SharedPlugs(self.shared_cfg, self.store.get_meta("shared", None))
        self._line_adjust: dict = {}
        self._ranged = False
        self._clim_until = 0.0
        self._t_load = time.time()
        self.ranking: dict = {}
        self.advice: dict = {}
        if not self.store.get_meta(RESEGMENT_KEY):
            self._resegment()
        self._renum = self.store.get_meta("renumber", [])
        if not self.store.get_meta("split_v1"):
            self._split_old()
        seen, dups = set(), []
        for r in self.store.events():
            # Un redémarrage de HA en pleine analyse peut enregistrer deux fois la même utilisation.
            key = (round(r["start"]), round(r["f"]["P"], -2))
            if key in seen:
                dups.append(r["id"])
                continue
            seen.add(key)
            self.rows[r["id"]] = {k: r[k] for k in ("start", "end", "base", "prof")}
            self.evs.append(engine.Ev(r["id"], r["f"], r["gid"], r["pinned"], r["excluded"]))
        if dups:
            self.store.delete_events(dups)
            _LOGGER.info("Empreinte : %s utilisation(s) en double supprimée(s)", len(dups))
        if not self.store.get_meta("cuts_v1"):
            self._fix_cuts()
        if not self.store.get_meta("spikes_v1"):
            self._fix_spikes()
        if not self.store.get_meta("overlap_v1"):
            n = self._dedupe_overlaps(list(self.evs))
            self.store.set_meta("overlap_v1", True)
            _LOGGER.info("Empreinte : %s chevauchement(s) d'un même appareil corrigé(s)", n)
        if not self.store.get_meta("features_v2"):
            # la lecture du palier a changé : on recalcule les mesures de chaque utilisation (réponses gardées)
            for e in self.evs:
                r = self.rows[e.id]
                loc = dt_util.as_local(dt_util.utc_from_timestamp(r["start"]))
                f = engine.features(r["prof"], loc.hour + loc.minute / 60)
                if e.f.get("split"):
                    f["split"] = True
                e.f = f
                self.store.update_event(e.id, r["start"], r["end"], r["prof"], f)
            self.store.set_meta("features_v2", True)
            self.store.set_meta("stitch_v1", False)
        if not self.store.get_meta("features_v4"):
            # Puissance lue après le pic de démarrage, forme lue sans lui. On recalcule les mesures de
            # chaque utilisation (les réponses et les rangements sont gardés)
            n = 0
            for e in self.evs:
                r = self.rows[e.id]
                loc = dt_util.as_local(dt_util.utc_from_timestamp(r["start"]))
                f = engine.features(r["prof"], loc.hour + loc.minute / 60)
                for flag in ("split", "tracked", "cut"):
                    if e.f.get(flag):
                        f[flag] = True
                n += f["P"] != e.f.get("P")
                e.f = f
                self.store.update_event(e.id, r["start"], r["end"], r["prof"], f)
            self.store.set_meta("features_v4", True)
            _LOGGER.info("Empreinte : mesures recalculées (%s puissance(s) corrigée(s))", n)
        if not self.store.get_meta("stitch_v1"):
            # une fois : recolle les reprises de thermostat déjà enregistrées en morceaux
            before = len(self.evs)
            self._stitch(list(self.evs))
            self.store.set_meta("hints", self.hints)
            self.store.set_meta("doubts", sorted(self.doubts))
            self.store.set_meta("stitch_v1", True)
            _LOGGER.info("Empreinte : %s reprise(s) d'appareil recollée(s)", before - len(self.evs))
        if not self.store.get_meta("recluster_v2"):
            # Réglages plus stricts : on refait tous les groupes automatiques (ce que l'utilisateur a validé reste).
            for e in self.evs:
                if not e.pinned:
                    e.gid = None
            self.groups = {gid: g for gid, g in self.groups.items() if g["status"] != "nouveau" or g.get("name")}
            self._cluster()
            self._save()
            self.store.set_meta("recluster_v2", True)
            _LOGGER.info("Empreinte : groupes recalculés avec les réglages plus stricts")
        self._recompute()

    async def _backfill(self):
        """Relit l'historique HA manquant (premier démarrage ou après une coupure)."""
        from homeassistant.components.recorder import get_instance, history

        last = await self.hass.async_add_executor_job(self.store.last_sample_ts)
        start_ts = max(last or 0, time.time() - BACKFILL_DAYS * 86400)
        start = dt_util.utc_from_timestamp(start_ts + 0.001)
        res = await get_instance(self.hass).async_add_executor_job(
            lambda: history.state_changes_during_period(
                self.hass, start, None, entity_id=self.power_entity, include_start_time_state=False))
        rows = []
        for st in res.get(self.power_entity, []):
            try:
                if float(st.state) > 0:      # 0 = le Linky qui redémarre après une coupure, pas une mesure
                    rows.append((st.last_updated.timestamp(), float(st.state)))
            except (TypeError, ValueError):
                continue
        subs = await self._sub_at([r[0] for r in rows], start)
        rows = [(t, p, s) for (t, p), s in zip(rows, subs)]
        if rows:
            await self.hass.async_add_executor_job(self.store.add_samples, rows)
            if not self.processed_until:
                self.processed_until = rows[0][0]
        _LOGGER.info("Empreinte : %s points relus depuis l'historique", len(rows))

    # ------------------------------------------------------------ prises mesurées (PC…)
    def _detect_measured(self) -> list[str]:
        """Par défaut : les prises connectées TP-Link/Tapo qui mesurent une puissance en W."""
        out = []
        for ent in er.async_get(self.hass).entities.values():
            if ent.platform != "tplink" or ent.domain != "sensor" or ent.disabled_by:
                continue
            # d'après la fiche de l'entité : au démarrage de HA, la prise n'a peut-être pas encore de valeur
            if (ent.device_class or ent.original_device_class) == "power":
                out.append(ent.entity_id)
        _LOGGER.info("Empreinte : prises mesurées soustraites de l'analyse : %s", out or "aucune")
        return out

    @staticmethod
    def _num(st):
        try:
            return float(st.state)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    @callback
    def _on_measured(self, event: Event):
        self.sub_now[event.data["entity_id"]] = self._num(event.data.get("new_state"))

    async def _sub_at(self, times: list[float], start) -> list[float]:
        """Somme des prises mesurées à chaque instant demandé (dernière valeur connue)."""
        if not self.measured or not times:
            return [0.0] * len(times)
        from homeassistant.components.recorder import get_instance, history
        res = await get_instance(self.hass).async_add_executor_job(
            lambda: history.state_changes_during_period(
                self.hass, start, None, entity_id=",".join(self.measured), include_start_time_state=True))
        series = []
        for eid in self.measured:
            pts = sorted((st.last_updated.timestamp(), self._num(st)) for st in res.get(eid, []))
            series.append(pts)
        out = []
        idx = [0] * len(series)
        for t in times:
            s = 0.0
            for k, pts in enumerate(series):
                while idx[k] + 1 < len(pts) and pts[idx[k] + 1][0] <= t:
                    idx[k] += 1
                if pts and pts[idx[k]][0] <= t:
                    s += pts[idx[k]][1]
            out.append(s)
        return out

    async def _fill_sub(self):
        """Complète l'historique enregistré avec la puissance des prises, puis fait tout re-mesurer."""
        times = await self.hass.async_add_executor_job(self.store.sample_times)
        if times:
            subs = await self._sub_at(times, dt_util.utc_from_timestamp(times[0] - 1))
            await self.hass.async_add_executor_job(self.store.set_sub, list(zip(times, subs)))
        await self.hass.async_add_executor_job(self.store.set_meta, "sub_for", sorted(self.measured))
        await self.hass.async_add_executor_job(self.store.set_meta, RESEGMENT_KEY, False)
        _LOGGER.info("Empreinte : %s points complétés avec les prises mesurées", len(times))

    # ------------------------------------------------------------ en direct
    @callback
    def _on_state(self, event: Event):
        st = event.data.get("new_state")
        if st is None:
            return
        try:
            p = float(st.state)
        except (TypeError, ValueError):
            return
        if p <= 0:                           # le Linky qui redémarre après une coupure : pas une mesure
            return
        t = st.last_updated.timestamp()
        sub = sum(self.sub_now.values())
        self.buffer.append((t, p, sub))
        valid = {gid: c for gid, c in self.cents.items() if self.groups.get(gid, {}).get("status") == "valide"}
        others = {gid: c for gid, c in self.cents.items() if gid not in valid and c.get("named") and self.groups.get(gid, {}).get("name")}
        if self.live.feed(t, p - sub, valid, others):
            self.push_entities()

    # ------------------------------------------------------------ analyse
    async def async_process(self, *_):
        if any(s.state not in ("off", "unavailable", "unknown", "fan_only") for s in self.hass.states.async_all("climate")):
            self._clim_until = time.time() + 600      # (le temps d'analyser ce qui vient de se terminer)
        async with self.lock:
            try:
                await self.hass.async_add_executor_job(self._process)
                self.last_run = time.time()
                self.last_error = None
            except Exception as err:  # on ne veut jamais casser HA
                _LOGGER.exception("Empreinte : erreur pendant l'analyse")
                self.last_error = str(err)
        await self.async_shared()
        self.ensure_entities()
        self.push_entities()
        await self.async_sync_energy()
        await self.async_lines()

    # ------------------------------------------------------------ prises partagées (lave-linge + sèche-linge…)
    async def async_shared(self):
        """Relit la courbe de chaque prise partagée depuis le dernier programme terminé et range les nouveaux."""
        if not self.shared.plugs:
            return
        try:
            from homeassistant.components.recorder import get_instance, history
            now = time.time()
            for p in self.shared.plugs:
                eid = p["entity"]
                start = dt_util.utc_from_timestamp(self.shared.since(eid, now, BACKFILL_DAYS * 86400))
                res = await get_instance(self.hass).async_add_executor_job(
                    lambda: history.state_changes_during_period(self.hass, start, None, entity_id=eid, include_start_time_state=True))
                pts = []
                for st in res.get(eid, []):
                    try:
                        pts.append((st.last_updated.timestamp(), float(st.state)))
                    except (TypeError, ValueError):
                        continue                              # indisponible : la valeur d'avant reste valable
                async with self.lock:
                    n = self.shared.ingest(eid, pts, now)
                if n:
                    _LOGGER.info("Empreinte : %s programme(s) de la prise %s rangé(s)", n, eid)
            await self.hass.async_add_executor_job(self.store.set_meta, "shared", self.shared.state())
        except Exception:  # une prise partagée ne doit jamais bloquer le reste
            _LOGGER.exception("Empreinte : impossible de lire une prise partagée")

    def _shared_action(self, msg):
        a = msg["action"]
        if a == "shared_label":
            self.shared.label(msg["rid"], msg.get("name") or None)
        elif a == "shared_dash":
            key = msg["key"]
            if key not in {self.shared.key(e, n) for e, n in self.shared.appliances()}:
                raise KeyError(key)
            self.shared.dash = [k for k in self.shared.dash if k != key] + ([key] if msg.get("on") else [])
        self.store.set_meta("shared", self.shared.state())

    def _shared_payload(self) -> list[dict]:
        week = time.time() - 7 * 86400
        out = []
        for p in self.shared.plugs:
            eid = p["entity"]
            st = self.hass.states.get(eid) if hasattr(self.hass, "states") else None
            runs = sorted((r for r in self.shared.runs if r["plug"] == eid), key=lambda r: r["start"], reverse=True)
            apps = []
            for n in p["names"]:
                key = self.shared.key(eid, n)
                mine = [r for r in runs if r.get("name") == n]
                apps.append({"name": n, "key": key, "kwh": self.shared.kwh(eid, n), "n": len(mine),
                             "kwh7": round(sum(r["wh"] for r in mine if r["start"] >= week) / 1000, 3),
                             "dash": key in self.shared.dash, "entities": self.entity_ids("sh:" + key),
                             "running": bool(self.shared.running(eid, n))})
            out.append({"entity": eid, "name": st.name if st else eid, "power": round(self.sub_now.get(eid, 0)),
                        "apps": apps, "open": self.shared.open.get(eid),
                        "runs": [{**{k: r.get(k) for k in ("id", "start", "end", "wh", "name", "guess", "conf", "user")}, "D": r["f"]["D"]}
                                 for r in runs[:20]]})
        return out

    # ------------------------------------------------------------ bilan par jour : le reste de la maison, les coûts
    async def async_lines(self):
        """Pour les graphiques « par jour » : chaque ligne du tableau Énergie (PC, clim,
        Électroménager…), son coût, et « le reste de la maison » (la maison moins ces lignes). Empilés, ils font
        exactement la consommation de la maison. Une ligne ajoutée au tableau Énergie est prise en compte toute seule.
        Calculé par petites différences, minute après minute, au prix du kWh réglé dans le tableau Énergie."""
        try:
            from homeassistant.components.energy.data import async_get_manager
            prefs = (await async_get_manager(self.hass)).data or {}
            grid = next((s for s in prefs.get("energy_sources", []) if s.get("type") == "grid"), None)
            if not grid or not grid.get("stat_energy_from"):
                return
            price = grid.get("number_energy_price")
            if grid.get("entity_energy_price"):
                st = self.hass.states.get(grid["entity_energy_price"])
                try:
                    price = float(st.state)
                except (AttributeError, TypeError, ValueError):
                    pass
            price = float(price or 0)
            house = grid["stat_energy_from"]
            devs = [(d["stat_consumption"], d.get("name") or d["stat_consumption"]) for d in prefs.get("device_consumption", [])]
            # les lignes estimées (Powercalc…) comptent comme les autres, sauf si elles sont déjà dans le tableau Énergie
            # (au démarrage, Powercalc arrive après nous : une ligne déjà suivie reste suivie, même sans capteur pour l'instant)
            L = self.lines
            devs += [(sid, name) for sid, name in self.extra_lines() if sid not in {s for s, _ in devs}
                     and (self.hass.states.get(sid) is not None or sid in L.get("names", {}))]
            now_ts = time.time()
            if "since" not in L:
                # depuis quand le bilan suit chaque ligne (pour les lignes d'avant : la création de son capteur de coût)
                L["since"] = {}
                for e in self.entities.get("lines", []):
                    created = getattr(getattr(e, "registry_entry", None), "created_at", None)
                    if getattr(e, "sid", None) and created:
                        L["since"][e.sid] = created.timestamp()
            seen = set()
            prev_clim = None
            self._clim_power(L, now_ts)
            for sid in [house] + [s for s, _ in devs]:
                st = self.hass.states.get(sid)
                try:
                    v = float(st.state)
                except (AttributeError, TypeError, ValueError):
                    continue
                if sid == self.clim_energy and L.get("clim_s") is not None:
                    if prev_clim is None:
                        prev_clim = L["last"].get(sid)
                    continue                          # compté plus bas, une fois connue la place laissée par la maison
                prev = L["last"].get(sid)
                L["last"][sid] = v
                if prev is None:
                    L["since"].setdefault(sid, now_ts)
                    continue
                d = v - prev
                if d < 0 or d > 50:              # compteur remis à zéro, ou saut impossible en une minute : ignoré
                    continue
                L["kwh"][sid] = L["kwh"].get(sid, 0) + d
                L["eur"][sid] = L["eur"].get(sid, 0) + d * price
                if d > 0:
                    seen.add(sid)
            # ce qu'on a retiré d'Empreinte (« Retirer », corrections) ne baisse pas ses compteurs : on en tient compte ici
            for sid, k in self._line_adjust.items():
                L["kwh"][sid] = L["kwh"].get(sid, 0) + k
                L["eur"][sid] = L["eur"].get(sid, 0) + k * price
                seen.add(sid)
            self._line_adjust = {}
            ops = []
            for op in L.get("ops", []):
                if not op["sid"] and op.get("target"):
                    op["sid"] = self._energy_entity(op["target"])
                if op["sid"] and op["sid"] not in {s for s, _ in devs}:
                    self._not_moved(op)               # pas une ligne du bilan : rien à replacer
                    continue
                if op["t_rest"] is None and op["sid"] in seen and op["made"] <= now_ts:
                    op["t_rest"] = now_ts
                ops.append(op)
            L["ops"] = ops
            self._apply_ops(L, price)
            # Le frigo (sa fiche) et les veilles (mesurées la nuit) sont des estimations : elles ne prennent jamais plus que ce
            # que la maison a laissé sans explication cette minute-ci. Le reste attend (« dette ») et se reprend ensuite :
            # le total ne change pas, mais aucune heure du non identifié ne passe sous zéro à cause d'elles.
            dev_now = [s for s, _ in devs]
            room = (L["kwh"].get(house, 0) - sum(L["kwh"].get(s, 0) for s in dev_now) - L.get("fridge_kwh", 0)
                    - L.get("retired_kwh", 0) - L.get("veille_kwh", 0))
            room = max(room - L.get("rest_kwh", room), 0.0)
            # La clim : son compteur n'avance que par paliers de 0,1 kWh, relevés avec 30 à 45 min de retard, et on la lisse
            # à vitesse régulière alors qu'elle s'arrête et repart. Elle ne prend donc jamais plus que ce que la maison a
            # laissé cette minute-ci ; le retard (au plus CLIM_LAG_KWH) se rattrape ensuite. Le total reste le sien.
            clim = self.clim_energy
            if clim in dev_now and L.get("clim_goal") is not None and prev_clim is not None:
                want = max(L["clim_goal"] - L["clim_s"], 0.0)
                take = min(want, room)
                take = max(take, want - CLIM_LAG_KWH)
                room = max(room - take, 0.0)
                L["clim_s"] = round(L["clim_s"] + take, 5)
                L["last"][clim] = L["clim_s"]
                L["kwh"][clim] = L["kwh"].get(clim, 0) + take
                L["eur"][clim] = L["eur"].get(clim, 0) + take * price
            elif L.get("clim_goal") is not None:
                L["clim_s"] = L["clim_goal"]
                if clim in dev_now and prev_clim is None:
                    L["last"][clim] = L["clim_s"]
                    L["since"].setdefault(clim, now_ts)
            for key, rate_w, t_key in (("fridge", self.fridge_kwh / 8.76 if self.fridge_kwh else 0, "t_fridge"),
                                       ("veille", L.get("veille_w") or 0, "t_veille")):
                if rate_w and L.get(t_key):
                    want = min(now_ts - L[t_key], 2 * 86400) / 3600 * rate_w / 1000 + L.get(key + "_debt", 0)
                    take = min(want, room)
                    room -= take
                    L[key + "_debt"] = min(want - take, 0.5)
                    L[key + "_kwh"] = L.get(key + "_kwh", 0) + take
                    L[key + "_eur"] = L.get(key + "_eur", 0) + take * price
                L[t_key] = now_ts
            dev_ids = [s for s, _ in devs]
            # une ligne qui s'en va garde ce qu'elle a compté (sinon le non identifié ferait un bond) ; si elle
            # revient, elle repart de zéro
            for sid in [s for s in L.get("names", {}) if s not in dev_ids]:
                L["retired_kwh"] = L.get("retired_kwh", 0) + L["kwh"].pop(sid, 0)
                L["retired_eur"] = L.get("retired_eur", 0) + L["eur"].pop(sid, 0)
                L["last"].pop(sid, None)
            L["names"] = {s: n for s, n in devs}
            L["rest_kwh"] = round(L["kwh"].get(house, 0) - sum(L["kwh"].get(s, 0) for s in dev_ids) - L.get("fridge_kwh", 0)
                                  - L.get("retired_kwh", 0) - L.get("veille_kwh", 0), 4)
            L["rest_eur"] = round(L["eur"].get(house, 0) - sum(L["eur"].get(s, 0) for s in dev_ids) - L.get("fridge_eur", 0)
                                  - L.get("retired_eur", 0) - L.get("veille_eur", 0), 4)
            L["price"] = price
            if time.time() - self.ranking.get("t", 0) > 900:        # toutes les 15 min
                try:
                    await self._async_ranking(devs, price, house)
                except Exception:  # le classement ne doit jamais bloquer le bilan
                    _LOGGER.exception("Empreinte : impossible de calculer le classement du mois")
                try:
                    await self._async_advice(devs, price, house)
                except Exception:  # les conseils non plus
                    _LOGGER.exception("Empreinte : impossible de calculer les conseils")
            await self.hass.async_add_executor_job(self.store.set_meta, "lines", L)
            self.ensure_line_entities()
            for e in self.entities.get("lines", []):
                if e.hass:
                    e.async_write_ha_state()
        except Exception:  # le bilan ne doit jamais bloquer l'analyse
            _LOGGER.exception("Empreinte : impossible de calculer le bilan par ligne")

    async def _async_ranking(self, devs, price, house=None):
        """Classement du mois : ce que chaque ligne du tableau Énergie (et le non identifié) a consommé depuis le
        1er du mois, du plus gros au plus petit, lu dans les statistiques de HA (celles des graphiques)."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistic_during_period, statistics_during_period
        except ImportError:
            return
        now = dt_util.now()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        since = self.lines.get("since", {})
        first = min(since.values(), default=start.timestamp())          # début du bilan par ligne
        rec = get_instance(self.hass)

        async def change(sid, frm, to=None):
            try:
                r = await rec.async_add_executor_job(statistic_during_period, self.hass, frm, to, sid, {"change"}, None)
                return max(float((r or {}).get("change") or 0), 0.0)
            except Exception:  # une statistique absente ne doit rien bloquer
                return 0.0

        async def per_year(sid, t0):
            # sur un an, au rythme des 30 derniers jours (ou depuis que la ligne est suivie et a des statistiques) ;
            # rien avant un jour de mesure
            frm = dt_util.utc_from_timestamp(max(now.timestamp() - 30 * 86400, t0))
            try:
                rows_h = (await rec.async_add_executor_job(
                    statistics_during_period, self.hass, frm, None, {sid}, "hour", None, {"change"})).get(sid) or []
            except Exception:  # une statistique absente ne doit rien bloquer
                return None
            if not rows_h:
                return None
            t_first = rows_h[0]["start"]
            t_first = t_first.timestamp() if hasattr(t_first, "timestamp") else float(t_first)
            days = (now.timestamp() - t_first) / 86400
            kwh = sum(max(r.get("change") or 0, 0) for r in rows_h)
            return kwh / days * 365 if days >= 1 else None

        rows = []
        for sid, name in devs:
            rows.append({"nom": name, "kwh": await change(sid, start), "an": await per_year(sid, since.get(sid, first))})
        if self.fridge_kwh:
            rows.append({"nom": "Frigo (estimé)", "kwh": await change("sensor.empreinte_bilan_frigo_energie", start),
                         "an": self.fridge_kwh})
        veille = await self._veille_w(rec, statistics_during_period, now)
        if veille:
            self.lines["veille_w"] = veille
        if self.lines.get("veille_w"):
            rows.append({"nom": VEILLE_NAME, "kwh": await change("sensor.empreinte_bilan_veille_energie", start),
                         "an": self.lines["veille_w"] * 8.76})
        # le non identifié sur un an : la maison moins tout le reste (les parts font bien 100 %)
        house_an = await per_year(house, first) if house else None
        rest_an = max(house_an - sum(r["an"] or 0 for r in rows), 0) if house_an is not None else None
        rows.append({"nom": "Non identifié", "kwh": await change("sensor.empreinte_bilan_reste_energie", start), "an": rest_an})
        key = "an" if house_an is not None else "kwh"
        tot = sum(r[key] or 0 for r in rows) or 1
        for r in rows:
            r["pct"] = round((r[key] or 0) / tot * 100)
            r["an_eur"] = round(r["an"] * price) if r["an"] is not None else None
            r["eur"] = round(r["kwh"] * price, 2)
            r["kwh"] = round(r["kwh"], 2)
            r["an"] = round(r["an"]) if r["an"] is not None else None
        rows.sort(key=lambda r: -(r[key] or 0))
        mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                "novembre", "décembre"][now.month - 1]
        jan1 = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.ranking = {"t": time.time(), "periode": f"{mois} {now.year}", "lignes": rows,
                        "an_eur": round(house_an * price) if house_an is not None else None,
                        # toute la maison, lue dans les mêmes statistiques que les graphiques, pour l'onglet Énergie
                        "annee_kwh": round(await change(house, jan1), 1) if house else None,
                        "mois_kwh": round(await change(house, start), 1) if house else None,
                        "jour_kwh": round(await change(house, day), 2) if house else None,
                        "hier_kwh": round(await change(house, day - timedelta(days=1), day), 2) if house else None,
                        "clim_jour_kwh": round(await change(self.clim_energy, day), 1) if self.clim_energy else None,
                        "clim_mois_kwh": round(await change(self.clim_energy, start), 1) if self.clim_energy else None,
                        "prix": price}

    async def _async_advice(self, devs, price, house):
        """Les économies possibles, calculées sur la vraie consommation de la maison, et ce mois comparé au précédent.
        Chaque conseil n'apparaît que si les mesures le montrent ; chaque gain est un calcul, dit comme tel."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistic_during_period, statistics_during_period
        rec = get_instance(self.hass)
        now = dt_util.now()
        ts = now.timestamp()

        async def daily(sid, frm, to):
            """Les jours où ce compteur existait, et ce qu'il a compté chacun de ces jours."""
            try:
                r = await rec.async_add_executor_job(statistics_during_period, self.hass, frm, to, {sid}, "day", None, {"change"})
                return [max(x.get("change") or 0, 0) for x in (r or {}).get(sid, [])]
            except Exception:  # une statistique absente ne doit rien bloquer
                return []

        async def change(sid, frm, to=None):
            try:
                r = await rec.async_add_executor_job(statistic_during_period, self.hass, frm, to, sid, {"change"}, None)
                return max(float((r or {}).get("change") or 0), 0.0)
            except Exception:  # une statistique absente ne doit rien bloquer
                return 0.0

        utc = dt_util.utc_from_timestamp
        tips = []
        # 1. les veilles permanentes : mesurées au plus bas des nuits
        vw = self.lines.get("veille_w") or 0
        if vw >= 15:
            tips.append({"id": "veilles", "icone": "mdi:power-sleep", "titre": "Veilles permanentes",
                         "constat": f"{round(vw)} W jour et nuit, soit {round(vw * 8.76 * price)} € par an",
                         "conseil": "Une multiprise à interrupteur (ou une prise connectée) derrière la télé et sa box : chaque 10 W coupés la nuit et en journée font gagner "
                                    f"{round(10 * 8.76 * price)} € par an. Elle coûte 10 à 15 € : rentabilisée en moins d'un an.",
                         "eur_an": round(vw / 2 * 8.76 * price), "base": "si la moitié de ces veilles peut être coupée", "sur": False})
        # 2. le PC allumé sans jouer (entre 10 et 110 W) : mesuré par sa prise
        if self.measured:
            rows = await self.hass.async_add_executor_job(self.store.samples, ts - 7 * 86400, ts)
            idle = tot = 0.0
            for (t, _p, sub), (t2, _q, _r) in zip(rows, rows[1:]):
                d = min(t2 - t, 600)
                tot += d
                if 10 <= sub < 110:
                    idle += d
            if tot > 3 * 86400:
                h_day = idle / tot * 24
                if h_day >= 1:
                    tips.append({"id": "pc", "icone": "mdi:desktop-tower-monitor", "titre": "PC allumé au repos",
                                 "constat": f"{h_day:.1f} h par jour vers 80 W sans jeu, soit {round(h_day * 0.08 * 365 * price)} € par an".replace(".", ","),
                                 "conseil": "Régler la mise en veille automatique après 15 min sans utilisation (Windows : Paramètres → Système → Alimentation).",
                                 "eur_an": round(h_day / 2 * 0.08 * 365 * price), "base": "si la moitié de ces heures était sans toi devant", "sur": False})
        # 3. la clim en chauffage : 1 °C de moins ≈ 7 % de chauffage en moins (ADEME)
        # (sur les jours où elle a vraiment chauffé : elle vient peut-être juste d'être allumée pour l'hiver)
        heating = [x for x in await daily(self.clim_energy, utc(ts - 15 * 86400), None) if x >= 0.3] if self.clim_energy else []
        if len(heating) >= 2:
            per_month = sum(heating) / len(heating) * 30
            st = self.hass.states.get(self.climate_entity) if self.climate_entity else None
            target = st.attributes.get("temperature") if st else None
            tips.append({"id": "clim", "icone": "mdi:thermometer-minus", "titre": "Climatisation",
                         "constat": f"≈ {per_month:.0f} kWh par mois de chauffe, soit {round(per_month * price)} € par mois",
                         "conseil": f"Régler 1 °C plus bas{f' ({target - 1:g} °C au lieu de {target:g} °C)'.replace('.', ',') if target else ''} : environ 7 % de chauffage en moins (ADEME). "
                                    "La nuit ou en ton absence, 16-17 °C suffisent.",
                         "eur_an": round(per_month * 0.07 * 5 * price), "base": "1 °C de moins, sur 5 mois de chauffe", "sur": True})
        # 4. le chauffage de la salle de bain : une résistance, 3 à 4 fois moins efficace que la clim
        heat = [e for e in self.evs if self.groups.get(e.gid, {}).get("name") == "Chauffage salle de bain" and self.rows[e.id]["start"] > ts - 30 * 86400]
        if len(heat) >= 5:
            seen = min((r["start"] for r in self.rows.values()), default=ts - 30 * 86400)      # (moins de 30 jours mesurés)
            n_day = len(heat) / max(1, min(30, (ts - seen) / 86400))
            kwh = sum(e.f["wh"] for e in heat) / 1000
            p_med = engine.engine_median([e.f["P"] for e in heat])
            d_med = engine.engine_median([e.f["D"] for e in heat])
            save = n_day * 365 * p_med * 300 / 3600 / 1000 * price
            tips.append({"id": "chauffage", "icone": "mdi:radiator", "titre": "Chauffage salle de bain",
                         "constat": f"{n_day:.1f} fois par jour, {round(d_med / 60)} min à {round(p_med)} W, soit {round(n_day * 365 * p_med * d_med / 3600 / 1000 * price)} € par an".replace(".", ","),
                         "conseil": "5 minutes de moins à chaque fois (l'allumer juste avant la douche, l'éteindre en sortant).",
                         "eur_an": round(save), "base": "5 min de moins par utilisation", "sur": True})
        # 5. les appareils reconnus : le bon programme ou le bon geste (ordres de grandeur de l'ADEME), sur leur vraie consommation
        seen = min((r["start"] for r in self.rows.values()), default=ts - 30 * 86400)
        span = max(1, min(30, (ts - seen) / 86400))
        for gid_name, tid, icon, pct, geste, base in APPLIANCE_TIPS:
            evs = [e for e in self.evs if self.groups.get(e.gid, {}).get("name") == gid_name and self.rows[e.id]["start"] > ts - 30 * 86400]
            if len(evs) < 2:
                continue
            kwh_year = sum(e.f["wh"] for e in evs) / 1000 / span * 365
            if kwh_year * price < 5:
                continue
            uses = self._programmes(evs, PROGRAMME_GAP_S.get(gid_name, 5400))
            per_week = uses / span * 7
            per_use = sum(e.f["wh"] for e in evs) / uses / 1000
            tips.append({"id": tid, "icone": icon, "titre": gid_name,
                         "constat": f"{per_week:.1f} fois par semaine, {per_use * price:.2f} € à chaque fois, soit {round(kwh_year * price)} € par an".replace(".", ","),
                         "conseil": geste, "eur_an": round(kwh_year * pct * price), "base": base, "sur": False})
        tips.sort(key=lambda x: -x["eur_an"])
        # Ce mois-ci comparé au précédent : par jour, pour comparer des mois pas encore finis
        # (par jour, et pour chaque ligne sur ses propres jours de mesure : une ligne ajoutée en cours de mois ne fausse rien)
        m0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        p0 = (m0 - timedelta(days=1)).replace(day=1)
        d_cur = max((ts - m0.timestamp()) / 86400, 0.5)

        async def per_day(sid):
            prev = await daily(sid, p0, m0)
            return await change(sid, m0) / d_cur, (sum(prev) / len(prev) if prev else None), len(prev)
        comp = {}
        if house:
            cur, prev, d_prev = await per_day(house)
            lines = []
            # (seulement les lignes mesurées ou reconnues : le frigo, les veilles et le non identifié sont des estimations
            # dont la définition a changé en cours de route, leur écart ne voudrait rien dire)
            for sid, name in devs:
                if sid in set(self.extra_ids):
                    continue
                a, b, n = await per_day(sid)
                if b is not None and n >= 3 and max(a, b) >= 0.05:
                    lines.append({"nom": name, "jour": round(a, 2), "avant": round(b, 2), "ecart": round(a - b, 2)})
            lines.sort(key=lambda x: x["ecart"])
            mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
            comp = {"mois": mois[now.month - 1], "mois_avant": mois[p0.month - 1], "jour": round(cur, 2), "avant": round(prev or 0, 2),
                    "pct": round(cur / prev * 100 - 100) if prev else None, "jours": round(d_cur, 1), "jours_avant": d_prev,
                    "eur_jour": round(cur * price, 2), "eur_avant": round((prev or 0) * price, 2), "lignes": lines}
        self.advice = {"t": ts, "conseils": tips, "total": sum(t["eur_an"] for t in tips), "comparaison": comp}

    def _programmes(self, evs, gap):
        """Combien de programmes : les phases de chauffe d'un même programme (un lave-vaisselle chauffe 2 ou 3 fois en
        1 à 3 h) ne comptent qu'une fois. Une phase qui démarre moins de `gap` s après la fin de la précédente en fait partie."""
        ivs = sorted((self.rows[e.id]["start"], self.rows[e.id].get("end", self.rows[e.id]["start"])) for e in evs)
        uses, end = 0, None
        for a, b in ivs:
            if end is None or a - end > gap:
                uses += 1
            end = b if end is None else max(end, b)
        return uses

    def _clim_power(self, L, now):
        """Puissance moyenne de la clim : 0,1 kWh entre deux paliers de son compteur, divisés par le temps passé. Si le
        palier suivant tarde, elle ne peut pas être plus haute que ce que ce retard permet."""
        if not self.clim_energy:
            return
        st = self.hass.states.get(self.clim_energy)
        try:
            v = float(st.state)
        except (AttributeError, TypeError, ValueError):
            return
        last = L.get("clim_v")
        if last is None or v < last:
            L["clim_v"], L["clim_t"], L["clim_w"] = v, now, 0
            L["clim_s"] = L["clim_goal"] = v
        elif v > last:
            # (le compteur est relevé toutes les 30 à 45 min : il avance parfois de 0,2 kWh d'un coup)
            L["clim_w"] = round((v - last) * 1000 / max((now - L["clim_t"]) / 3600, 1 / 60))
            L["clim_v"], L["clim_t"], L["clim_dv"] = v, now, round(v - last, 3)
        elif now - L["clim_t"] > 300:
            L["clim_w"] = min(L.get("clim_w", 0), round(max(L.get("clim_dv", 0.1), 0.1) * 1000 / ((now - L["clim_t"]) / 3600)))
        # l'énergie lissée : avance à la puissance moyenne, sans jamais sortir du palier où se trouve le vrai compteur
        # (la vraie consommation est entre la dernière valeur affichée et le prochain palier)
        if L.get("clim_goal") is None:                # (avant : l'objectif était la valeur lissée elle-même)
            L["clim_goal"] = L.get("clim_s", v)
        st_c = self.hass.states.get(self.climate_entity) if self.climate_entity else None
        rate = 0 if self.climate_entity and (st_c is None or st_c.state == "off") else L.get("clim_w", 0)
        s = L["clim_goal"] + rate * (now - L.get("clim_ts", now)) / 3600 / 1000
        L["clim_goal"], L["clim_ts"] = round(min(max(s, v), v + max(L.get("clim_dv", 0.1), 0.1)), 5), now
        L.setdefault("clim_s", L["clim_goal"])

    async def _veille_w(self, rec, stats, now):
        """Ce qui reste allumé en permanence (W) : le plus bas du non identifié entre 1 h et 5 h, sur les 7 dernières
        nuits (veilles et comptées déjà à part ajoutées : ce chiffre ne doit pas se mesurer lui-même)."""
        ids = {"sensor.empreinte_bilan_reste_energie", "sensor.empreinte_bilan_veille_energie"}
        try:
            r = await rec.async_add_executor_job(stats, self.hass, dt_util.utc_from_timestamp(now.timestamp() - 7 * 86400),
                                                 None, ids, "hour", None, {"change"})
        except Exception:  # une statistique absente ne doit rien bloquer
            return None
        hours = {}
        for rows in (r or {}).values():
            for x in rows:
                t = x["start"].timestamp() if hasattr(x["start"], "timestamp") else float(x["start"])
                if 1 <= dt_util.as_local(dt_util.utc_from_timestamp(t)).hour < 5:
                    hours[t] = hours.get(t, 0) + (x.get("change") or 0)
        if len(hours) < 8:                            # au moins 2 nuits
            return None
        return round(max(engine.quantile(sorted(hours.values()), 0.2) * 1000, 0), 1)

    @staticmethod
    def _spread(start, end, k, prof=None):
        """k kWh répartis par tranches de 5 min entre start et end : [(instant, kWh), …]. Avec sa courbe (un point toutes
        les 15 s), au prorata de ce qu'il tirait vraiment (un sèche-linge chauffe surtout au début) ; sinon au temps."""
        end = max(end, start + 1)
        out, t = [], start
        while t < end:
            nxt = min(end, (t // 300 + 1) * 300)
            out.append([t, nxt - t])
            t = nxt
        if prof and sum(max(x, 0) for x in prof) > 0:
            w = []
            for t0, dt in out:
                i0, i1 = int((t0 - start) // engine.STEP), int(-(-(t0 + dt - start) // engine.STEP))
                w.append(sum(max(x, 0) for x in prof[max(i0, 0):max(i1, i0 + 1)]))
            if sum(w) > 0:
                return [(t0, k * x / sum(w)) for (t0, _), x in zip(out, w)]
        return [(t0, k * dt / (end - start)) for t0, dt in out]

    def _rest_op(self, sid, k, where, t_line=None):
        """Le non identifié absorbe k kWh de la ligne sid maintenant (son compteur qui monte, ou un retrait), alors
        qu'ils appartiennent aux heures `where`. 10 min plus tard (le temps que HA l'enregistre), les statistiques de
        la ligne, de son coût et du non identifié sont remises à la bonne heure : plus de barre négative."""
        if sid in self.lines.get("names", {}):
            self.lines.setdefault("ops", []).append({"sid": sid, "k": k, "where": where, "t_line": t_line,
                                                      "t_rest": None, "made": time.time()})

    def _not_moved(self, op):
        """Ces kWh ne seront pas replacés : ils restent là où ils ont été comptés, un retrait devra les y reprendre."""
        for x in self.energy_log:
            if x["id"] == op.get("lid"):
                x.pop("where", None)

    def _apply_ops(self, L, price):
        now, keep, todo = time.time(), [], []
        for op in L.get("ops", []):
            if op["t_rest"] is None:
                if now - op["made"] < 3600:          # pas encore vu par le bilan (au-delà d'une heure : abandonné)
                    keep.append(op)
                else:
                    self._not_moved(op)
            elif now - max(op["t_rest"], op["t_line"] or 0) < 600:
                keep.append(op)
            else:
                todo.append(op)
        L["ops"] = keep
        if not todo:
            return
        lines = self.entities.get("lines", [])
        rest = {type(e).__name__: e.entity_id for e in lines if type(e).__name__ in ("RestEnergy", "RestCost")}
        cost = {e.sid: e.entity_id for e in lines if getattr(e, "sid", None)}
        adj = []
        for op in todo:
            moves = [(op["t_rest"], -op["k"])] + [tuple(w) for w in op["where"]]
            if op["t_line"]:
                adj += [(op["sid"], t, a, "kWh") for t, a in [(op["t_line"], -op["k"])] + [tuple(w) for w in op["where"]]]
            if op["sid"] in cost:
                adj += [(cost[op["sid"]], t, a * price, "EUR") for t, a in moves]
            if "RestEnergy" in rest:
                adj += [(rest["RestEnergy"], t, -a, "kWh") for t, a in moves]
            if "RestCost" in rest:
                adj += [(rest["RestCost"], t, -a * price, "EUR") for t, a in moves]
        try:
            from homeassistant.components.recorder import get_instance
            rec = get_instance(self.hass)
            for sid, t, a, unit in adj:
                if abs(a) > 1e-6:
                    rec.async_adjust_statistics(sid, dt_util.utc_from_timestamp(t - t % 300), round(a, 6), unit)
        except Exception:  # les statistiques ne doivent jamais bloquer le bilan
            _LOGGER.exception("Empreinte : impossible de remettre une utilisation à sa bonne heure")

    def extra_lines(self) -> list[tuple[str, str]]:
        """Les lignes estimées choisies dans les réglages : (capteur d'énergie, nom affiché)."""
        out = []
        for sid in self.extra_ids:
            st = self.hass.states.get(sid) if self.hass else None
            name = getattr(st, "name", None) if st is not None else None
            out.append((sid, name or self.lines.get("names", {}).get(sid) or sid))
        return out

    def line_cost(self, sid):
        return round(self.lines["eur"].get(sid, 0), 4)

    @callback
    def ensure_line_entities(self):
        if "sensor" not in self.add_entities:
            return
        from .sensor import LineCost, RestCost, RestEnergy
        have = {e.sid for e in self.entities.get("lines", []) if getattr(e, "sid", None)}
        new = []
        if not self.entities.get("lines"):
            from .sensor import Ranking
            new += [RestEnergy(self), RestCost(self), Ranking(self)]
        if self.fridge_kwh and not any(getattr(e, "fridge", False) for e in self.entities.get("lines", [])):
            from .sensor import FridgeCost, FridgeEnergy
            new += [FridgeEnergy(self), FridgeCost(self)]
        if not any(getattr(e, "veille", False) for e in self.entities.get("lines", [])):
            from .sensor import Advice, AdviceTip, Evolution, VeilleCost, VeilleEnergy
            new += [VeilleEnergy(self), VeilleCost(self), Advice(self), Evolution(self)]
            new += [AdviceTip(self, k + 1) for k in range(len(TIP_IDS))]
        if self.clim_energy and not any(getattr(e, "clim", False) for e in self.entities.get("lines", [])):
            from .sensor import ClimEnergy, ClimPower
            new += [ClimPower(self), ClimEnergy(self)]
        for sid, name in self.lines.get("names", {}).items():
            if sid not in have:
                new.append(LineCost(self, sid, name))
        if new:
            self.entities.setdefault("lines", []).extend(new)
            self.add_entities["sensor"](new)

    async def async_sync_energy(self):
        """Le tableau Énergie contient exactement les appareils Empreinte que l'utilisateur y a ajoutés (bouton
        « Ajouter au tableau Énergie »). Les autres appareils du tableau (PC, clim…) ne sont jamais touchés."""
        try:
            from homeassistant.components.energy.data import async_get_manager
            # au démarrage, tant que nos capteurs ne sont pas tous recréés, on ne touche pas au tableau Énergie
            # (sinon nos lignes en disparaîtraient une minute, et ce qui se calcule pendant ce temps serait faux)
            pending = [gid for gid, g in self.groups.items()
                       if g["status"] == "valide" and g.get("energy_dash") and not self.entity_ids(gid)]
            pending += [c["id"] for c in self.cats if c["dash"] and not self.entity_ids("cat:" + c["id"])]
            pending += [k for k in self.shared.dash if not self.entity_ids("sh:" + k)]
            if pending or "sensor" not in self.add_entities:
                return
            mgr = await async_get_manager(self.hass)
            cur = list((mgr.data or {}).get("device_consumption", []))
            want = {}
            for gid, g in self.groups.items():
                ids = self.entity_ids(gid)
                en = next((i for i in ids if i.endswith("_energie")), None)
                # l'électroménager dans le tableau Énergie : ses appareils n'y sont pas une deuxième fois
                if g["status"] == "valide" and g.get("energy_dash") and en and not self._cat(self.cat_of(gid)).get("dash"):
                    want[en] = (g["name"] or f"Groupe {g['letter']}", next((i for i in ids if i.endswith("_puissance")), None))
            for c in self.cats:
                ids = self.entity_ids("cat:" + c["id"])
                en = next((i for i in ids if i.endswith("_energie")), None)
                if c["dash"] and en:
                    want[en] = (c["name"], next((i for i in ids if i.endswith("_puissance")), None))
            for eid, n in self.shared.appliances():
                key = self.shared.key(eid, n)
                ids = self.entity_ids("sh:" + key)
                en = next((i for i in ids if i.endswith("_energie")), None)
                if key in self.shared.dash and en:
                    want[en] = (n, next((i for i in ids if i.endswith("_puissance")), None))
            ours = lambda sid: sid.startswith("sensor.empreinte_") and sid.endswith("_energie")
            nxt = []
            for d in cur:
                sid = d.get("stat_consumption", "")
                if ours(sid) and sid not in want:
                    continue
                nxt.append({**d, "name": want[sid][0]} if sid in want else d)
            for sid, (name, rate) in want.items():
                if not any(d.get("stat_consumption") == sid for d in nxt):
                    nxt.append({"stat_consumption": sid, "name": name, **({"stat_rate": rate} if rate else {})})
            if nxt != cur:
                await mgr.async_update({"device_consumption": nxt})
                _LOGGER.info("Empreinte : tableau Énergie mis à jour (%s appareil(s) Empreinte)", len(want))
        except Exception:  # le tableau Énergie ne doit jamais bloquer l'analyse
            _LOGGER.exception("Empreinte : impossible de mettre à jour le tableau Énergie")

    def _process(self):
        buf, self.buffer = self.buffer, []
        if buf:
            self.store.add_samples(buf)
        now = time.time()
        rows = self.store.samples(self.processed_until - 1800, now + 1)
        if len(rows) < 2:
            return
        ts = [r[0] for r in rows]
        ps = [r[1] - r[2] for r in rows]          # les prises mesurées (PC…) ne comptent pas
        found, open_start = engine.segment(ts, ps)
        self.running_parts = [x for x in (self._live_parts(ts, ps, open_start) if open_start else [])
                              if not self._is_clim(x["start"], x["p"])]
        self._auto_track()
        self._update_tracks()
        new = []
        recent = [r["start"] for r in self.rows.values() if r["start"] >= self.processed_until - 7 * 3600]
        for e in found:
            # Déjà enregistrée (éventuellement en plusieurs morceaux) : on ne la reprend pas.
            if e["start"] < self.processed_until - 1e-6 or any(e["start"] - 1 <= s < e["end"] for s in recent):
                continue
            for part in self._parts(e):
                if self._is_clim(part["start"], part["f"]["P"]):
                    continue
                eid = self.store.add_event(part["start"], part["end"], part["base"], part["prof"], part["f"])
                self.rows[eid] = {k: part[k] for k in ("start", "end", "base", "prof")}
                ev = engine.Ev(eid, part["f"])
                self.evs.append(ev)
                new.append(ev)
        self.processed_until = open_start if open_start is not None else ts[-1]
        self.open_start = open_start
        new = self._stitch(new)
        self._dedupe_overlaps([e for e in self.evs if self.rows[e.id]["end"] >= self.processed_until - 7 * 3600])
        if new:
            self._apply_relabel(new)
        again = self._renumber(new) if self._renum else set()
        if new or not self.cents or not self._ranged:       # aussi au démarrage : « probable » n'est pas enregistré
            self._ranged = True
            self._cluster()
        if self.pending and self._apply_pending():
            self._recompute()
        if new:
            for ev in new:
                if ev.id not in again:      # une ancienne utilisation ré-analysée : déjà comptée (ou pas) sous son ancien numéro
                    self._add_energy(ev.gid, ev.id, ev.f["wh"] / 1000)
        self._save()
        self._cats_update()
        if datetime.now().hour == 3:
            self.store.purge_samples(SAMPLES_KEEP_DAYS)

    def _is_clim(self, start, power) -> bool:
        """Une petite montée pendant qu'une clim tourne : c'est son compresseur, pas un appareil."""
        return power <= CLIM_MAX_W and start <= self._clim_until

    def _stitch(self, new: list) -> list:
        """Recolle à l'utilisation précédente un appareil qui reprend après une courte pause (thermostat).
        Retourne les nouvelles utilisations qui restent à part."""
        keep = []
        for ev in sorted(new, key=lambda e: self.rows[e.id]["start"]):
            r = self.rows[ev.id]
            before = [e for e in self.evs if e is not ev and self.rows[e.id]["end"] <= r["start"] + 30]
            prev = max(before, key=lambda e: self.rows[e.id]["end"], default=None)
            gap = r["start"] - self.rows[prev.id]["end"] if prev else None
            if not prev or not engine.should_stitch(prev.f, ev.f, gap):
                keep.append(ev)
                continue
            pr = self.rows[prev.id]
            prof = engine.stitch(pr["prof"], gap, r["prof"])
            loc = dt_util.as_local(dt_util.utc_from_timestamp(pr["start"]))
            f = engine.features(prof, loc.hour + loc.minute / 60)
            if prev.f.get("split") or ev.f.get("split"):
                f["split"] = True
            end = pr["start"] + len(prof) * engine.STEP
            self._add_energy(prev.gid, prev.id, ev.f["wh"] / 1000)
            prev.f = f
            self.rows[prev.id] = {**pr, "end": end, "prof": prof}
            self.store.update_event(prev.id, pr["start"], end, prof, f)
            self.store.delete_events([ev.id])
            self.evs.remove(ev)
            del self.rows[ev.id]
            self.hints.pop(ev.id, None)
            self.doubts.discard(ev.id)
        return keep

    # ------------------------------------------------------------ suivi d'un appareil (démarrage -> arrêt)
    @staticmethod
    def _pause_max(name: str) -> float:
        """Pause la plus longue avant de considérer l'appareil éteint : longue pour ceux qui chauffent en plusieurs fois."""
        return 75 * 60 if re.search(r"vaisselle|linge|s[eè]che", name or "", re.I) else 10 * 60

    def _used_minutes(self, name):
        """Durées déjà choisies et durées réellement mesurées pour cet appareil (pour les proposer)."""
        chosen = self._meta("program_minutes", {}).get((name or "").lower(), [])
        chosen = chosen if isinstance(chosen, list) else [chosen]
        seen = [h["minutes"] for h in reversed(self._meta("program_history", {}).get((name or "").lower(), []))]
        out = []
        for m in chosen + [round(x / 5) * 5 for x in seen]:
            if m and m not in out:
                out.append(m)
        return out[:5]

    def _start_track(self, name, at, power, user) -> bool:
        if user:
            # désigné par l'utilisateur : refusé seulement si c'est déjà le même suivi (même nom, ou même démarrage
            # et même puissance) ; un micro-ondes lancé pendant des plaques de puissance voisine est bien un autre
            dup = any(t["name"] == name or (abs(at - t["at"]) <= 30 and abs(t["p"] - power) / max(t["p"], power) <= 0.12)
                      for t in self.tracks)
        else:
            # trouvé tout seul : pas une reprise de même puissance d'un appareil suivi (thermostat)
            dup = any(abs(t["p"] - power) / max(t["p"], power) <= 0.25 and at >= t["at"] - 90 for t in self.tracks)
        if dup:
            return False
        # pas de durée par défaut : le programme peut changer d'une fois sur l'autre
        self.tracks.append({"id": f"t{int(at)}", "name": name, "at": at, "p": round(power), "user": user,
                            "pause_max": self._pause_max(name), "on": True, "since": at, "on_s": 0,
                            "planned_end": None, "minutes": None})
        self.store.set_meta("tracks", self.tracks)
        _LOGGER.info("Empreinte : suivi de « %s » à partir de %s", name,
                     dt_util.as_local(dt_util.utc_from_timestamp(at)).strftime("%H:%M"))
        return True

    def _auto_track(self):
        """Un appareil déjà connu et long (four, lave-vaisselle…) qui démarre est suivi tout seul, mais
        seulement si c'est net : pour une ressemblance moins nette, il est seulement proposé."""
        for x in self.running_parts:
            if x["end"] or not x.get("hint_group"):
                continue
            # on attend que le pic de démarrage soit passé : un chauffage à céramique tire 1,6 kW quelques secondes
            # (la puissance du lave-vaisselle) avant de se stabiliser vers 800 W
            if time.time() - x["start"] < engine.SETTLE_S + 30:
                continue
            g = self.groups.get(x["hint_group"])
            c = self.cents.get(x["hint_group"])
            if not (g and c and c["D"] >= 600):
                continue
            # ressemblance nette : puissance très proche (moitié de la tolérance des groupes) et même forme
            close = abs(x["p"] - c["P"]) / max(0.08 * max(x["p"], c["P"]), 120) <= 0.5
            # et sans hésitation : un autre appareil nommé de puissance voisine (chauffage et plaques vers 750 W) ?
            # alors on attend la fin de l'utilisation, la durée, la forme et l'heure les départagent
            rival = any(gid != x["hint_group"] and self.groups.get(gid, {}).get("name") and cc.get("P")
                        and abs(x["p"] - cc["P"]) / engine.power_scale({"P": x["p"]}, cc) <= 1.0
                        for gid, cc in self.cents.items())
            if rival:
                continue
            # des à-coups pour un appareil qui chauffe en continu (plaques prises pour le chauffage) : non ;
            # un appareil à thermostat peut, lui, commencer par chauffer en continu (préchauffage du four)
            same_shape = not (x["n"] > 1 and c["shape"] != "cycles")
            # un saut qui appartient déjà à un appareil suivi (reprise de thermostat…) n'est pas un nouvel appareil
            taken = any(any(a - 60 <= x["start"] <= b + 60 for a, b in t.get("ivs", [])) or
                        abs(x["start"] - t.get("since", 0)) <= 60 for t in self.tracks)
            # déjà suivi puis terminé (« C'est fini », coupure) : ce démarrage-là ne repart pas en suivi
            taken = taken or any(d["at"] - 120 <= x["start"] <= d["end"] + 60
                                 for d in self._meta("tracks_done", []))
            if close and same_shape and not taken:
                self._start_track(g["name"], x["start"], x["p"], user=False)

    def _update_tracks(self, force: str | None = None):
        if not self.tracks:
            return
        now = time.time()
        keep = []
        for tr in self.tracks:
            rows = self.store.samples(tr["at"] - 120, now + 1)
            known = [c["P"] for gid, c in self.cents.items()
                     if self.groups.get(gid, {}).get("name") and self.groups[gid]["name"] != tr["name"]]
            ts = [r[0] for r in rows]
            ps = [r[1] - r[2] for r in rows]
            cut = next((a for a, b in engine.cuts(ts) if b > tr["at"]), None)
            if cut is not None:
                # coupure de courant pendant le suivi : il s'arrête là ; ce qui reprend après est une autre histoire
                res = engine.track(ts, ps, tr["at"], tr["p"], max(cut, tr["at"] + 1), 0, known=known)
                ivs = [(a, min(b, cut)) for a, b in res["intervals"] if a < cut]
                if ivs:
                    self._finish_track(tr, ivs, cut=True)
                self._stopped(tr, cut)
                continue
            planned = tr.get("planned_end")
            if planned and now < planned:
                # programme en cours : on ne le déclare pas fini, même pendant une très longue pause
                res = engine.track(ts, ps, tr["at"], tr["p"], now, 10 ** 9, max_s=planned - tr["at"] + 3600, known=known)
            else:
                # après la fin prévue : il est fini dès qu'il est à l'arrêt depuis 5 min
                pause = min(tr["pause_max"], 300) if planned else tr["pause_max"]
                res = engine.track(ts, ps, tr["at"], tr["p"], now, pause,
                                   max_s=max(engine.TRACK_MAX_S, (planned or 0) - tr["at"] + 3600), known=known)
            ivs = res["intervals"]
            c = next((self.cents.get(gid) for gid, g in self.groups.items() if g.get("name") == tr["name"]), None)
            if not tr["user"] and c and abs(res["p"] - c["P"]) / max(res["p"], c["P"]) > 0.3:
                # reconnu tout seul, mais sa vraie puissance (une fois le pic passé) n'est pas celle de cet appareil :
                # ce n'était pas lui, on arrête le suivi sans rien enregistrer
                _LOGGER.info("Empreinte : suivi de « %s » annulé (%s W au lieu de %s W)", tr["name"], res["p"], c["P"])
                self._stopped(tr, now)
                continue
            if force == tr["id"] or tr.get("stop"):
                done, ivs = True, ivs or [(tr["at"], now)]
            else:
                done = res["done"]
            tr["on"], tr["since"] = res["on"], res["last_change"]
            tr["p"] = res.get("p", tr["p"])      # sa puissance stable, une fois le pic de démarrage passé
            tr["ivs"] = [[round(a), round(b)] for a, b in ivs]   # ses propres moments allumés
            tr["on_s"] = round(sum(b - a for a, b in ivs))
            if done and ivs:
                self._finish_track(tr, ivs)
            if done:
                self._stopped(tr, ivs[-1][1] if ivs else now)
            else:
                keep.append(tr)
        self.tracks = keep
        self.store.set_meta("tracks", self.tracks)

    # ------------------------------------------------------------ catégories (Électroménager…)
    def _cat(self, cid) -> dict:
        return next((c for c in self.cats if c["id"] == cid), {})

    def cat_of(self, gid):
        """Catégorie d'un appareil : celle choisie par l'utilisateur, sinon Électroménager pour les gros appareils."""
        g = self.groups.get(gid)
        if not g or not g.get("name"):
            return None
        if "cat" in g:
            return g["cat"] if self._cat(g["cat"]) else None
        return "menager" if self._cat("menager") and MENAGER_RE.search(g["name"]) else None

    def _cats_update(self):
        """Énergie de chaque catégorie depuis son ajout dans le tableau Énergie. Elle ne baisse jamais (compteur du
        tableau Énergie) ; une utilisation rangée plus tard dans un de ses appareils est ajoutée."""
        changed = False
        for c in self.cats:
            if not c.get("since"):
                continue
            done = c.setdefault("counted", {})
            for e in self.evs:
                if self.cat_of(e.gid) != c["id"] or self.rows[e.id]["end"] < c["since"]:
                    continue
                extra = e.f["wh"] / 1000 - done.get(str(e.id), 0)
                if extra > 1e-6:
                    done[str(e.id)] = round(e.f["wh"] / 1000, 4)
                    c["total"], changed = round(c["total"] + extra, 4), True
                    self._log_energy(("cat", c["id"]), c["name"], e.id, extra)
                elif extra < -0.005 and time.time() - self.rows[e.id]["end"] > 600:
                    # déjà comptée, puis rétrécie (une partie rendue à un autre appareil) : l'écart est retiré
                    done[str(e.id)] = round(e.f["wh"] / 1000, 4)
                    changed = True
                    self._shrink_energy(("cat", c["id"]), c["name"], e.id, -extra)
            if len(done) > 3000:
                c["counted"] = dict(list(done.items())[-2000:])
        if changed:
            self.store.set_meta("categories", self.cats)
        self._groups_update()
        self._auto_corrections()

    def _groups_update(self):
        """Un appareil validé, seul dans le tableau Énergie (le chauffage) : une utilisation rangée plus tard dans
        ce groupe, ou raccourcie, y est comptée comme pour une catégorie."""
        for gid, g in self.groups.items():
            if g["status"] != "valide" or not g.get("energy_dash") or self._cat(self.cat_of(gid)).get("dash"):
                continue
            sid = self._energy_entity(("g", gid))
            if sid not in self.lines.get("names", {}):
                continue                              # (au démarrage : pas encore prêt, à la minute suivante)
            if "counted" not in g:
                g["counted"] = {}
                for x in self.energy_log:
                    if x["target"] == ["g", gid] and not x["removed"]:
                        g["counted"][str(x["eid"])] = round(g["counted"].get(str(x["eid"]), 0) + x["kwh"], 4)
            # depuis quand cette ligne compte : à défaut, son premier ajout dans le journal
            since = self.lines.get("since", {}).get(sid)
            if since is None:
                since = min((x["t"] for x in self.energy_log if x["target"] == ["g", gid]), default=time.time())
            for e in self.evs:
                if e.gid != gid or self.rows[e.id]["end"] < since:
                    continue
                extra = e.f["wh"] / 1000 - g["counted"].get(str(e.id), 0)
                if extra > 1e-6:
                    g["counted"][str(e.id)] = round(e.f["wh"] / 1000, 4)
                    g["energy"] = round(g.get("energy", 0) + extra, 4)
                    self._log_energy(("g", gid), g["name"], e.id, extra)
                elif extra < -0.005 and time.time() - self.rows[e.id]["end"] > 600:
                    g["counted"][str(e.id)] = round(e.f["wh"] / 1000, 4)
                    self._shrink_energy(("g", gid), g["name"], e.id, -extra)

    def _auto_corrections(self):
        """Une utilisation déjà envoyée au tableau Énergie, puis corrigée (renommée, sortie de l'appareil ou de la
        catégorie, supprimée) : ses kWh en sont retirés tout seuls. Si elle rejoint un autre appareil ou une autre
        catégorie qui est dans le tableau Énergie, elle y est comptée normalement. HA doit avoir enregistré l'ajout
        (10 min) : sinon on réessaie à la minute suivante."""
        alive = {e.id: e for e in self.evs}
        changed = False
        waiting = {old for old, _ in self._renum}       # analyse refaite en cours : pas encore son nouveau numéro
        for x in self.energy_log:
            if x["removed"] or x.get("checked") or x["eid"] in waiting:
                continue
            kind, tid = x["target"]
            e = alive.get(x["eid"])
            still = bool(e) and (e.gid == tid if kind == "g" else self.cat_of(e.gid) == tid)
            if still:
                continue
            if time.time() - x["t"] < 600:
                continue                              # trop tôt pour HA : on réessaiera
            try:
                self._undo_energy(x["id"])
            except ValueError:
                x["checked"] = True
                continue
            except KeyError:
                if time.time() - self._t_load > 600:  # capteur disparu : rien à retirer (au démarrage : pas encore prêt)
                    x["checked"] = True
                continue
            x["auto"] = True
            changed = True
            if kind == "cat":
                self._cat(tid).get("counted", {}).pop(str(x["eid"]), None)
            elif tid in self.groups:
                self.groups[tid].get("counted", {}).pop(str(x["eid"]), None)
        if changed:
            self.store.set_meta("energy_log", self.energy_log)
            self.store.set_meta("categories", self.cats)

    # ------------------------------------------------------------ ce qui est envoyé au tableau Énergie
    def _add_energy(self, gid, eid, kwh):
        """Ajoute l'énergie d'une utilisation au compteur d'un appareil validé, et le note dans le journal."""
        g = self.groups.get(gid)
        if g and g["status"] == "valide" and kwh > 0:
            g["energy"] = round(g.get("energy", 0) + kwh, 4)
            if "counted" in g:
                g["counted"][str(eid)] = round(g["counted"].get(str(eid), 0) + kwh, 4)
            self._log_energy(("g", gid), g["name"], eid, kwh)

    def _log_energy(self, target, name, eid, kwh):
        """Journal « envoyé au tableau Énergie » : chaque ajout, pour pouvoir le retirer s'il était faux."""
        r = self.rows.get(eid, {})
        x = {"id": f"l{int(time.time() * 1000)}_{eid}_{target[1]}", "t": time.time(), "target": list(target),
             "name": name, "eid": eid, "start": r.get("start"), "end": r.get("end"), "kwh": round(kwh, 4), "removed": False}
        self.energy_log.append(x)
        # comptée après coup (utilisation d'un autre moment, ou comptée à sa fin) : remise à ses vraies minutes
        if r.get("start") and r.get("end") and (x["t"] - r["end"] > 60 or r["end"] - r["start"] > 300):
            # (juste après un démarrage, le capteur n'existe pas encore : le bilan le retrouvera à son passage)
            sid = self._energy_entity(target)
            if sid is None or sid in self.lines.get("names", {}):
                x["where"] = self._spread(r["start"], r["end"], x["kwh"], r.get("prof"))
                self.lines.setdefault("ops", []).append({"sid": sid, "target": list(target), "lid": x["id"], "k": x["kwh"],
                                                         "where": x["where"], "t_line": x["t"], "t_rest": None, "made": x["t"]})
        horizon = time.time() - 30 * 86400
        self.energy_log = [x for x in self.energy_log if x["t"] >= horizon][-500:]
        self.store.set_meta("energy_log", self.energy_log)

    def _shrink_energy(self, target, name, eid, k):
        """Une utilisation déjà comptée a rétréci : k kWh sont retirés du tableau Énergie, à ses heures, et rendus
        au non identifié (le compteur, lui, ne baisse jamais)."""
        sid, r = self._energy_entity(target), self.rows.get(eid)
        if not sid or not r:
            return
        from homeassistant.components.recorder import get_instance
        where = self._spread(r["start"], r["end"], k, r.get("prof"))
        for t0, a in where:
            self.hass.loop.call_soon_threadsafe(get_instance(self.hass).async_adjust_statistics,
                                                sid, dt_util.utc_from_timestamp(t0 - t0 % 300), -a, "kWh")
        if r["start"] >= self.lines.get("since", {}).get(sid, 0):
            self._line_adjust[sid] = self._line_adjust.get(sid, 0) - k
            self._rest_op(sid, -k, [(t0, -a) for t0, a in where])
        # les ajouts de cette utilisation sont diminués d'autant : un retrait plus tard ne reprendra que le reste
        left = k
        for x in reversed(self.energy_log):
            if left > 1e-6 and x["eid"] == eid and x["target"] == list(target) and not x["removed"] and x["kwh"] > 0:
                cut = min(left, x["kwh"])
                if x.get("where"):
                    x["where"] = [(t0, a * (x["kwh"] - cut) / x["kwh"]) for t0, a in x["where"]]
                x["kwh"], left = round(x["kwh"] - cut, 4), left - cut
        self.energy_log.append({"id": f"l{int(time.time() * 1000)}_{eid}_{target[1]}_r", "t": time.time(), "target": list(target),
                                "name": name, "eid": eid, "start": r["start"], "end": r["end"], "kwh": round(k, 4),
                                "removed": True, "auto": True, "checked": True, "where": where})
        self.store.set_meta("energy_log", self.energy_log)
        _LOGGER.info("Empreinte : %s kWh retirés de « %s » (utilisation raccourcie)", round(k, 4), name)

    def _in_dash(self, target) -> bool:
        """Ce capteur est-il vraiment dans le tableau Énergie ? (un appareil dont la catégorie y est n'y est pas seul)"""
        if target[0] == "cat":
            return bool(self._cat(target[1]).get("dash"))
        g = self.groups.get(target[1], {})
        return bool(g.get("energy_dash")) and g.get("status") == "valide" and not self._cat(self.cat_of(target[1])).get("dash")

    def _energy_entity(self, target):
        key = target[1] if target[0] == "g" else "cat:" + target[1]
        return next((i for i in self.entity_ids(key) if i.endswith("_energie")), None)

    def _undo_energy(self, lid, back=False):
        """Retire (ou remet) l'énergie d'un ajout dans les statistiques de Home Assistant, celles que lit le
        tableau Énergie, à partir du moment où elle avait été ajoutée. Le compteur, lui, ne baisse jamais."""
        x = next((x for x in self.energy_log if x["id"] == lid), None)
        if not x or x["removed"] == (not back):
            return
        sid = self._energy_entity(x["target"])
        if not sid:
            raise KeyError("capteur d'énergie")
        if time.time() - x["t"] < 600:
            raise ValueError("trop récent")
        from homeassistant.components.recorder import get_instance
        sign = 1 if back else -1
        where = x.get("where") or [(x["t"], x["kwh"])]          # là où ces kWh sont dans les statistiques
        for t0, a in where:
            self.hass.loop.call_soon_threadsafe(get_instance(self.hass).async_adjust_statistics,
                                                sid, dt_util.utc_from_timestamp(t0 - t0 % 300), sign * a, "kWh")
        # le non identifié les rend (ou les reprend) à ces mêmes heures, pas maintenant ; sauf si l'ajout date
        # d'avant que le bilan suive cette ligne : il ne les avait alors jamais perdus
        if x["t"] >= self.lines.get("since", {}).get(sid, 0):
            self._line_adjust[sid] = self._line_adjust.get(sid, 0) + sign * x["kwh"]
            self._rest_op(sid, sign * x["kwh"], [(t0, sign * a) for t0, a in where])
        x["removed"] = not back
        if back:
            x["checked"] = True        # remis par l'utilisateur : on ne le retire plus tout seul
        self.store.set_meta("energy_log", self.energy_log)
        _LOGGER.info("Empreinte : %s kWh %s « %s » dans le tableau Énergie (ajout de %s)", x["kwh"],
                     "remis dans" if back else "retirés de", x["name"],
                     dt_util.as_local(dt_util.utc_from_timestamp(x["t"])).strftime("%d/%m %H:%M"))

    def cat_power(self, cid) -> float:
        """Puissance d'une catégorie en ce moment : ses appareils suivis qui chauffent, plus ses capteurs validés."""
        names = {g["name"] for gid, g in self.groups.items() if self.cat_of(gid) == cid}
        tracked = {t["name"] for t in self.tracks}
        p = sum(t["p"] for t in self.tracks if t["on"] and t["name"] in names)
        for gid, r in self.live.running.items():
            if self.cat_of(gid) == cid and not r["off_since"] and self.groups[gid]["name"] not in tracked:
                p += r["power"]
        return round(p)

    def _stopped(self, tr, end):
        """Garde en mémoire un suivi terminé : le même démarrage ne relance pas un suivi automatique
        (après « C'est fini », l'appareil ne doit plus être compté, même si un autre tourne encore)."""
        done = [d for d in self._meta("tracks_done", []) if d["end"] > time.time() - 86400]
        done.append({"name": tr["name"], "at": tr["at"], "end": end, "p": tr["p"]})
        self._set_meta("tracks_done", done[-30:])
        gid = next((g for g, x in self.groups.items() if x.get("name") == tr["name"]), None)
        if gid and gid in self.live.running:
            del self.live.running[gid]       # le capteur « en marche » s'éteint aussi

    def _owns_start(self, start, ivs) -> bool:
        """Ce démarrage est-il un des siens (le début d'un de ses moments allumés) ?"""
        return any(abs(start - a) <= 45 for a, _ in ivs)

    def _other_piece(self, e, p, ivs) -> bool:
        """Un morceau déjà isolé comme un appareil à part (ses propres montée et descente, ou un autre appareil suivi
        jusqu'à son arrêt), qui ne démarre pas avec l'appareil suivi : c'est un autre appareil, même de puissance voisine."""
        same_power = abs(e.f["P"] - p) / max(e.f["P"], p) <= 0.15        # c'est lui, commencé avant le suivi
        return bool(e.f.get("split") or e.f.get("tracked")) and not self._owns_start(self.rows[e.id]["start"], ivs)             and not same_power

    def _finish_track(self, tr, ivs, cut=False):
        """L'appareil suivi est éteint : une seule utilisation, retirée des morceaux qui le contenaient."""
        start = ivs[0][0]
        prof = engine.track_profile(start, ivs, tr["p"])
        if not prof:
            return
        end = start + len(prof) * engine.STEP
        # on retire sa part des utilisations qui se chevauchent (sinon il serait compté deux fois), mais pas d'un
        # autre appareil déjà mis à part (micro-ondes lancé pendant les plaques) : lui ne contient pas l'appareil suivi
        for e in [e for e in self.evs if self.rows[e.id]["start"] < end and self.rows[e.id]["end"] > start
                  and not self._other_piece(e, tr["p"], ivs)]:
            r = self.rows[e.id]
            rest = list(r["prof"])
            for k in range(len(rest)):
                t = r["start"] + k * engine.STEP
                if any(a <= t < b for a, b in ivs):
                    rest[k] = max(0, rest[k] - tr["p"])
            # son pic de démarrage (chauffage à céramique : jusqu'à 2 fois sa puissance pendant moins d'une minute)
            # est à lui aussi. Seulement ce qui reste collé à son démarrage : un autre appareil lancé juste après
            # (micro-ondes 1 min après les plaques) commence après un creux et n'est pas touché.
            for a_, _ in ivs:
                k = max(0, round((a_ - r["start"]) / engine.STEP) - 1)
                k_end = round((a_ + engine.SETTLE_S - r["start"]) / engine.STEP) + 1
                while 0 <= k < min(len(rest), k_end + 1) and (k <= round((a_ - r["start"]) / engine.STEP) or rest[k] > 0):
                    if rest[k] <= 2 * tr["p"]:
                        rest[k] = 0
                    k += 1
            rest = engine.clean_rest(rest)
            keep = [k for k, v in enumerate(rest) if v >= 100]
            if not keep or max(rest) < engine.ON_W:
                self.store.delete_events([e.id])
                self.evs.remove(e)
                del self.rows[e.id]
                continue
            ka, kb = keep[0], keep[-1] + 1
            s0 = r["start"] + ka * engine.STEP
            loc = dt_util.as_local(dt_util.utc_from_timestamp(s0))
            e.f = engine.features(rest[ka:kb], loc.hour + loc.minute / 60)
            self.rows[e.id] = {**r, "start": s0, "end": s0 + (kb - ka) * engine.STEP, "prof": rest[ka:kb]}
            self.store.update_event(e.id, s0, s0 + (kb - ka) * engine.STEP, rest[ka:kb], e.f)
        loc = dt_util.as_local(dt_util.utc_from_timestamp(start))
        f = engine.features(prof, loc.hour + loc.minute / 60)
        f["tracked"] = True
        if cut:
            f["cut"] = True            # interrompu par une coupure de courant
        base = self.rows[self.evs[-1].id]["base"] if self.evs else 0
        eid = self.store.add_event(start, end, base, prof, f)
        self.rows[eid] = {"start": start, "end": end, "base": base, "prof": prof}
        ev = engine.Ev(eid, f)
        self.evs.append(ev)
        self._label({eid}, tr["name"])
        if not tr["user"]:
            ev.pinned = False          # trouvé tout seul : l'utilisateur peut encore corriger
        self._add_energy(ev.gid, eid, f["wh"] / 1000)
        if not cut:                    # un programme coupé ne sert pas à proposer des durées
            hist = self._meta("program_history", {})
            hist.setdefault(tr["name"].lower(), []).append({
                "start": round(start), "minutes": round((end - start) / 60), "chauffes": len(ivs),
                "chauffe_min": round(sum(b - a for a, b in ivs) / 60), "p": tr["p"], "prevue": tr.get("minutes")})
            hist[tr["name"].lower()] = hist[tr["name"].lower()][-50:]
            self._set_meta("program_history", hist)
        # ses propres étiquettes en attente sont réglées ; celles d'un autre appareil lancé juste après restent
        self.pending = [p for p in self.pending if not (p.get("at") and abs(p["at"] - tr["at"]) <= 120 and p["name"] == tr["name"])]
        self.store.set_meta("pending", self.pending)
        self._recompute()
        self._save()
        _LOGGER.info("Empreinte : « %s » éteint, %s min allumé entre %s et %s", tr["name"], round(f["D"] / 60),
                     loc.strftime("%H:%M"), dt_util.as_local(dt_util.utc_from_timestamp(end)).strftime("%H:%M"))

    def _live_parts(self, ts, ps, open_start) -> list[dict]:
        """Ce qui tourne en ce moment, appareil par appareil, avec une proposition de nom quand c'est possible."""
        i0 = next((i for i, t in enumerate(ts) if t >= open_start), len(ts) - 1)
        pre = [p for t, p in zip(ts, ps) if open_start - engine.PRE_S <= t < open_start] or [ps[max(0, i0 - 1)]]
        prof = engine.profile(ts, ps, i0, open_start, ts[-1] + engine.STEP, engine.engine_median(pre))
        out = []
        for c in engine.live_parts(prof):
            start = open_start + c["k0"] * engine.STEP
            loc = dt_util.as_local(dt_util.utc_from_timestamp(start))
            f = {"P": c["h"], "D": max(engine.STEP, (c["k1"] - c["k0"]) * engine.STEP), "on": 1 if c["n"] == 1 else 0.6,
                 "cyc": 2 * c["n"] - 1 if c["n"] > 1 else 0, "cv": 0.05, "h": loc.hour + loc.minute / 60}
            f["shape"] = engine.shape_of(f)
            hint, hint_group = self._hint_for(f)
            out.append({"start": start, "end": None if c["running"] else open_start + c["k1"] * engine.STEP,
                        "p": c["h"], "n": c["n"], "hint": hint, "hint_group": hint_group})
        return out

    def _hint_for(self, f):
        """(nom proposé, groupe) : un appareil déjà nommé de même puissance, sinon l'hypothèse générale si elle est nette."""
        best, bd = None, 1.0      # même tolérance de puissance que pour former un groupe
        for gid, c in self.cents.items():
            g = self.groups.get(gid)
            if g and g.get("name"):
                d = abs(f["P"] - c["P"]) / max(0.08 * max(f["P"], c["P"]), 120)
                if d < bd:
                    best, bd = gid, d
        if best:
            return self.groups[best]["name"], best
        hours = [0] * 24
        hours[int(f["h"]) % 24] = 1
        kind, score = engine.guess(f, hours)[0]
        return (self.label(kind) if score >= 0.5 else None), None

    def _pending_for(self, start, power):
        for p in self.pending:
            if p.get("at") and abs(p["at"] - start) <= 120 and abs((p.get("p") or 1) - power) / max(p.get("p") or 1, power) <= 0.35:
                return {"name": ("peut-être " if p.get("maybe") else "") + p["name"], "t": p["t"]}
        return None

    def _parts(self, e, min_edge=None) -> list[dict]:
        """Une utilisation, ou plusieurs si deux appareils tournaient en même temps."""
        # quand une clim tourne, les sauts de son compresseur ne sont pas des appareils qui démarrent ou s'arrêtent
        pieces = engine.split_session(e["prof"], min_edge or (CLIM_MAX_W if e["start"] <= self._clim_until else engine.EDGE_W))
        out = []
        for k0, prof in pieces:
            start = e["start"] + k0 * engine.STEP
            loc = dt_util.as_local(dt_util.utc_from_timestamp(start))
            f = engine.features(prof, loc.hour + loc.minute / 60)
            if len(pieces) > 1:
                f["split"] = True
                # le morceau est recoupé à plat : son pic se lit sur la courbe d'origine, au-dessus de ce qui tournait déjà
                f["spike"] = engine.start_spike(e["prof"][k0:k0 + 4], max(prof), e["prof"][k0 - 1] if k0 else 0)
            else:
                f["spike"] = engine.start_spike(prof)
            out.append({"start": start, "end": start + len(prof) * engine.STEP, "base": e["base"], "prof": prof, "f": f})
        return out

    def _resegment(self):
        """Refait toute l'analyse depuis le début (après un changement de la façon de mesurer).

        Les réponses de l'utilisateur (C'est sûr, Pas ça, Oui, Peut-être) sont notées avec l'heure de
        l'utilisation, puis reportées sur la nouvelle mesure de la même utilisation.
        """
        rows = self.store.events()
        relabel = [{"start": r["start"], "gid": r["gid"], "excluded": sorted(r["excluded"]),
                    "hint": self.hints.get(r["id"])} for r in rows if r["pinned"] or r["id"] in self.hints]
        relabel = [x | {"pinned": any(r["pinned"] and r["start"] == x["start"] for r in rows)} for x in relabel]
        kept = {x["gid"] for x in relabel if x["gid"] and x["pinned"]}
        # ce qui a déjà été compté dans le tableau Énergie suivra chaque utilisation sous son nouveau numéro (_renumber)
        self.store.set_meta("renumber", self.store.get_meta("renumber", []) + [[r["id"], r["start"]] for r in rows])
        self.store.delete_events([r["id"] for r in rows])
        self.groups = {gid: g for gid, g in self.groups.items() if gid in kept or g["status"] == "valide"}
        self.hints = {}
        self.processed_until = self.store.first_sample_ts() or 0.0
        for k in ("split_v1", "recluster_v2", RESEGMENT_KEY):
            self.store.set_meta(k, True)
        self.store.set_meta("relabel", relabel)
        self.store.set_meta("hints", {})
        self.store.set_meta("groups", list(self.groups.values()))
        self.store.set_meta("processed_until", self.processed_until)
        _LOGGER.info("Empreinte : analyse refaite depuis le début (%s utilisations, %s réponses à reporter)",
                     len(rows), len(relabel))

    def _renumber(self, new) -> set:
        """Après une analyse refaite, chaque utilisation est enregistrée sous un nouveau numéro. Ce qui avait déjà été
        compté pour l'ancienne (catégories, appareils, journal du tableau Énergie) passe à la nouvelle du même moment :
        sinon tout l'historique serait compté une seconde fois. Une ancienne utilisation qui n'a plus d'équivalente
        garde ce qui a été compté (rien n'est retiré du tableau Énergie)."""
        mp, wait, lost = {}, [], set()
        taken = set()
        for old, start in self._renum:
            match = min((e for e in new if e.id not in taken), key=lambda e: abs(self.rows[e.id]["start"] - start), default=None)
            if match and abs(self.rows[match.id]["start"] - start) <= 90:
                mp[old] = match.id
                taken.add(match.id)
            elif start > self.processed_until - 2 * 3600:
                wait.append([old, start])               # l'analyse n'est pas encore arrivée à ce moment-là
            else:
                lost.add(old)
        if not mp and not lost:
            return set()
        for owner in list(self.cats) + list(self.groups.values()):
            cnt = owner.get("counted")
            if not cnt:
                continue
            for old, nid in mp.items():
                if str(old) in cnt:
                    cnt[str(nid)] = max(cnt.pop(str(old)), cnt.get(str(nid), 0))
        for x in self.energy_log:
            if x["eid"] in mp:
                x["eid"] = mp[x["eid"]]
            elif x["eid"] in lost:
                x["checked"] = True                     # plus d'utilisation à corriger : on n'y touche plus
        self._renum = wait
        self.store.set_meta("renumber", wait)
        self.store.set_meta("categories", self.cats)
        self.store.set_meta("energy_log", self.energy_log)
        self._save()
        return set(mp.values())

    def _apply_relabel(self, new):
        """Reporte les réponses notées avant une nouvelle mesure sur les utilisations correspondantes."""
        todo = self.store.get_meta("relabel", [])
        if not todo:
            return
        for x in todo:
            match = min(new, key=lambda e: abs(self.rows[e.id]["start"] - x["start"]), default=None)
            if not match or abs(self.rows[match.id]["start"] - x["start"]) > 90:
                continue
            if x["pinned"]:
                match.gid = x["gid"] if x["gid"] in self.groups else None
                match.pinned = True
                match.excluded = set(x["excluded"])
            if x["hint"]:
                self.hints[match.id] = x["hint"]
        self.store.set_meta("relabel", [])
        self.store.set_meta("hints", self.hints)
        _LOGGER.info("Empreinte : %s réponse(s) reportée(s) sur la nouvelle mesure", len(todo))

    def _fix_cuts(self):
        """Une fois : applique les coupures (courant, HA éteint) aux utilisations déjà enregistrées.
        Une utilisation qui enjambe une coupure s'arrête à la coupure ; ce qui « démarre » juste au retour
        du courant (tout se rallume d'un coup) est retiré, sauf si l'utilisateur l'a nommé."""
        cuts = engine.cuts(self.store.sample_times())
        trimmed, removed = 0, []
        for e in list(self.evs):
            r = self.rows[e.id]
            for a, b in cuts:
                if r["start"] < a < r["end"]:
                    prof = r["prof"][:max(1, int((a - r["start"]) / engine.STEP) + 1)]
                    loc = dt_util.as_local(dt_util.utc_from_timestamp(r["start"]))
                    f = engine.features(prof, loc.hour + loc.minute / 60)
                    for k in ("split", "tracked"):
                        if e.f.get(k):
                            f[k] = True
                    f["cut"] = True
                    end = r["start"] + len(prof) * engine.STEP
                    e.f = f
                    self.rows[e.id] = {**r, "end": end, "prof": prof}
                    self.store.update_event(e.id, r["start"], end, prof, f)
                    trimmed += 1
                    break
                if b - 60 <= r["start"] <= b + engine.REARM_S + 60 and not e.pinned and e.id not in self.hints:
                    removed.append(e.id)
                    break
        if removed:
            self.store.delete_events(removed)
            self.evs = [e for e in self.evs if e.id not in removed]
            for i in removed:
                del self.rows[i]
        self.store.set_meta("cuts_v1", True)
        _LOGGER.info("Empreinte : %s coupure(s) ; %s utilisation(s) arrêtée(s) à la coupure, %s retirée(s) au retour du courant",
                     len(cuts), trimmed, len(removed))

    def _dedupe_overlaps(self, evs) -> int:
        """Filet de sécurité : deux utilisations du même appareil qui se chevauchent ne comptent la partie commune
        qu'une fois. On la garde dans celle que l'utilisateur a confirmée (sinon la plus courte, la plus précise)."""
        fixed = 0
        evs = sorted(evs, key=lambda e: self.rows[e.id]["start"])
        for i, a in enumerate(evs):
            for b in evs[i + 1:]:
                if a not in self.evs or b not in self.evs or not a.gid or a.gid != b.gid:
                    continue
                ra, rb = self.rows[a.id], self.rows[b.id]
                if rb["start"] >= ra["end"] - engine.STEP or ra["start"] >= rb["end"] - engine.STEP:
                    continue
                keep, other = (a, b) if (a.pinned, -len(ra["prof"])) >= (b.pinned, -len(rb["prof"])) else (b, a)
                rk, ro = self.rows[keep.id], self.rows[other.id]
                if self._both_real(rk, ro):
                    continue                    # le compteur montre bien les deux en même temps : rien en double
                rest = list(ro["prof"])
                for k in range(len(rest)):
                    j = round((ro["start"] + k * engine.STEP - rk["start"]) / engine.STEP)
                    if 0 <= j < len(rk["prof"]):
                        rest[k] = max(0, rest[k] - rk["prof"][j])
                rest = engine.clean_rest(rest)
                fixed += 1
                kept = [k for k, v in enumerate(rest) if v >= 100]
                if not kept or max(rest) < engine.ON_W:
                    self.store.delete_events([other.id])
                    self.evs.remove(other)
                    del self.rows[other.id]
                    continue
                ka, kb = kept[0], kept[-1] + 1
                s0 = ro["start"] + ka * engine.STEP
                loc = dt_util.as_local(dt_util.utc_from_timestamp(s0))
                f = engine.features(rest[ka:kb], loc.hour + loc.minute / 60)
                for flag in ("split", "tracked", "cut"):
                    if other.f.get(flag):
                        f[flag] = True
                other.f = f
                self.rows[other.id] = {**ro, "start": s0, "end": s0 + (kb - ka) * engine.STEP, "prof": rest[ka:kb]}
                self.store.update_event(other.id, s0, s0 + (kb - ka) * engine.STEP, rest[ka:kb], f)
        return fixed

    def _both_real(self, r1, r2) -> bool:
        """Deux utilisations qui se chevauchent : le compteur montrait-il vraiment les deux en même temps ?
        (oui : deux appareils, dont l'un a peut-être un mauvais nom ; non : la même consommation comptée deux fois)"""
        a, b = max(r1["start"], r2["start"]), min(r1["end"], r2["end"])
        rows = self.store.samples(a, b)
        if len(rows) < 2:
            return False                        # pas de mesure pour trancher : on évite le double comptage
        seen = engine_median([p - s for _, p, s in rows[1:]] or [rows[0][1] - rows[0][2]]) - min(r1["base"], r2["base"])
        both = []
        for k in range(max(1, round((b - a) / engine.STEP))):
            t = a + k * engine.STEP
            v = 0
            for r in (r1, r2):
                j = round((t - r["start"]) / engine.STEP)
                v += r["prof"][j] if 0 <= j < len(r["prof"]) else 0
            both.append(v)
        return engine_median(both) <= 1.15 * seen + 150

    def _fix_spikes(self):
        """Une fois : retire les petits morceaux (moins de 75 s, sans nom) laissés à part au démarrage d'un appareil
        nommé, quand c'est son pic de démarrage (au plus 2 fois sa puissance)."""
        removed = []
        for e in self.evs:
            r = self.rows[e.id]
            if e.gid or e.pinned or e.id in self.hints or e.f["D"] > 75:
                continue
            if any(o is not e and o.gid and self.groups.get(o.gid, {}).get("name") and
                   abs(self.rows[o.id]["start"] - r["start"]) <= 60 and e.f["P"] <= 2 * o.f["P"] for o in self.evs):
                removed.append(e.id)
        if removed:
            self.store.delete_events(removed)
            self.evs = [e for e in self.evs if e.id not in removed]
            for i in removed:
                del self.rows[i]
        self.store.set_meta("spikes_v1", True)
        _LOGGER.info("Empreinte : %s pic(s) de démarrage laissé(s) à part retiré(s)", len(removed))

    def _split_old(self):
        """Une seule fois : applique la séparation aux utilisations enregistrées avant elle."""
        n = 0
        for r in self.store.events():
            if r["gid"] or r["pinned"]:
                continue
            parts = self._parts(r)
            if len(parts) > 1:
                self.store.delete_events([r["id"]])
                for p in parts:
                    self.store.add_event(p["start"], p["end"], p["base"], p["prof"], p["f"])
                n += 1
        self.store.set_meta("split_v1", True)
        _LOGGER.info("Empreinte : %s utilisation(s) ancienne(s) séparée(s) en plusieurs appareils", n)

    def _cluster(self):
        self._recompute()
        examples = {}
        for e in self.evs:
            if "spike" not in e.f:                    # (utilisations enregistrées avant cette mesure)
                e.f["spike"] = engine.start_spike(self.rows[e.id]["prof"])
            if e.pinned and e.id not in self.doubts and e.gid and self.groups.get(e.gid, {}).get("name"):
                examples.setdefault(e.gid, []).append(e.f)
        engine.assign(self.evs, self.cents, examples, {gid: self.cat_of(gid) or gid for gid in examples})
        self._recompute()
        horizon = time.time() - EVENTS_SENT_DAYS * 86400
        free = [e for e in self.evs if e.gid is None and not e.pinned and self.rows[e.id]["start"] > horizon]
        for cl in engine.dbscan(free):
            g = self._new_group()
            for e in cl:
                e.gid = g["id"]
        for gid in [gid for gid, g in self.groups.items() if g["status"] == "nouveau" and not any(e.gid == gid for e in self.evs)]:
            del self.groups[gid]
        self._recompute()

    def _recompute(self):
        """Centres des groupes, ressemblances et hypothèses."""
        by = {}
        for e in self.evs:
            if e.gid:
                by.setdefault(e.gid, []).append(e)
        # Le centre d'un groupe vient des utilisations que l'utilisateur a confirmées, dès qu'il y en a 2.
        self.cents = {}
        for gid, mem in by.items():
            if gid in self.groups:
                sure = [e for e in mem if e.pinned and e.id not in self.doubts]
                # « pas sûr » ou seulement probable : on n'apprend pas dessus
                clean = [e for e in mem if e.id not in self.doubts and not e.probable] or mem
                # le centre se lit sur ce que l'utilisateur a confirmé (dès 2 confirmations), la tolérance aussi :
                # jamais sur les rangements automatiques, qui pourraient la faire dériver vers un autre appareil
                self.cents[gid] = engine.centroid([e.f for e in (sure if len(sure) >= 2 else clean)],
                                                  spread_from=[e.f for e in sure])
                if self.cents[gid]:
                    self.cents[gid]["named"] = bool(self.groups[gid].get("name"))
        learned = {f"user:{gid}": c for gid, c in self.cents.items()
                   if self.groups[gid].get("name") and not self.groups[gid].get("auto") and len(by[gid]) >= 2}
        self.learned_labels = {k: self.groups[k[5:]]["name"] for k in learned}
        self.sims = {}
        for e in self.evs:
            c = self.cents.get(e.gid)
            if c:
                self.sims[e.id] = round(engine.similarity(engine.dist(e.f, c)), 3)
        for gid, g in self.groups.items():
            c = self.cents.get(gid)
            if not c:
                continue
            mem = by.get(gid, [])
            hours = [0] * 24
            for e in mem:
                hours[int(e.f["h"]) % 24] += 1
            votes = {}
            for e in mem:
                if e.id in self.hints:
                    votes[self.hints[e.id]] = votes.get(self.hints[e.id], 0) + 1
            gs = engine.guess(c, hours, {k: v for k, v in learned.items() if k != f"user:{gid}"},
                              # un seul « peut-être » ne fait que pousser un peu ; il en faut 3 pour peser pleinement
                              {name: n / len(mem) * min(1, n / 3) for name, n in votes.items()})
            g["proof"] = self._proof(mem, hours, gs)
            g["proof"]["votes"] = votes
            pr = g["proof"]
            # Prudence : on ne propose un appareil que si le groupe est assez vu et que l'hypothèse est nette.
            mature = pr["n"] >= HYP_MIN_N and pr["days"] >= HYP_MIN_DAYS
            g["hyp"] = gs[0][0] if mature and gs[0][1] >= HYP_MIN_SCORE else None
            g["hyp_score"] = gs[0][1]
            g["alt"] = gs[1][0] if g["hyp"] and gs[1][1] >= 0.15 else None
            self._auto_name(g, gs)

    def label(self, key):
        if key.startswith("vote:"):
            return key[5:]
        return self.learned_labels.get(key) or (engine.KINDS[key][0] if key in engine.KINDS else key)

    def _proof(self, mem, hours, gs) -> dict:
        """Les chiffres qui justifient le groupe et son hypothèse, affichés dans l'interface."""
        ps = sorted(e.f["P"] for e in mem)
        ds = sorted(e.f["D"] for e in mem)
        days = {dt_util.as_local(dt_util.utc_from_timestamp(self.rows[e.id]["start"])).date() for e in mem}
        weekend = sum(1 for e in mem if dt_util.as_local(dt_util.utc_from_timestamp(self.rows[e.id]["start"])).weekday() >= 5)
        sims = [self.sims[e.id] for e in mem if e.id in self.sims]
        shapes = {}
        for e in mem:
            shapes[e.f["shape"]] = shapes.get(e.f["shape"], 0) + 1
        med = engine.quantile(ps, 0.5) or 1
        return {
            "n": len(mem), "days": len(days), "weekend": weekend,
            "p_lo": engine.quantile(ps, 0.1), "p_hi": engine.quantile(ps, 0.9), "p_spread": round((engine.quantile(ps, 0.9) - engine.quantile(ps, 0.1)) / med, 3),
            "d_lo": engine.quantile(ds, 0.1), "d_hi": engine.quantile(ds, 0.9),
            "coherence": round(sum(sims) / len(sims), 3) if sims else 0,
            "shapes": shapes, "hours": hours,
            "guesses": [[self.label(k), p] for k, p in gs[:3]],
        }

    def _auto_name(self, g, gs):
        """Nomme tout seul un groupe seulement quand tout concorde. L'utilisateur confirme ou corrige ensuite."""
        (kind, score), (_, second) = gs[0], gs[1]
        pr = g["proof"]
        if g["name"] or g["status"] != "nouveau":
            return
        if pr["n"] < AUTO_NAME_MIN or pr["days"] < AUTO_NAME_DAYS or pr["coherence"] < AUTO_NAME_COHERENCE:
            return
        if score < AUTO_NAME_SCORE or score - second < AUTO_NAME_MARGIN:
            return
        if kind.startswith("user:"):
            return     # ressemble à un appareil déjà nommé : c'est à l'utilisateur de fusionner
        label = self.label(kind)
        if any(o.get("name") == label for o in self.groups.values()):
            return
        g["name"], g["status"], g["auto"] = label, "nomme", True
        _LOGGER.info("Empreinte : groupe %s nommé automatiquement « %s » (%.0f %%)", g["letter"], label, score * 100)

    def _new_group(self) -> dict:
        used = {g["slot"] for g in self.groups.values()}
        slot = next((s for s in range(1, 9) if s not in used), 0)
        self.counter += 1
        gid = f"g{self.counter}"
        g = {"id": gid, "letter": letter(self.counter - 1), "slot": slot, "name": None, "status": "nouveau",
             "hyp": None, "hyp_score": 0, "alt": None, "energy": 0.0, "created": time.time()}
        self.groups[gid] = g
        return g

    def _save(self):
        self.store.set_meta("groups", list(self.groups.values()))
        self.store.set_meta("processed_until", self.processed_until)
        self.store.set_meta("group_counter", self.counter)
        self.store.save_assignments(self.evs)

    # ------------------------------------------------------------ actions de l'interface
    async def async_action(self, msg: dict):
        async with self.lock:
            await self.hass.async_add_executor_job(self._action, msg)
        for gid in list(self.entities):
            g = self.groups.get(gid)
            if gid.startswith("cat:") and self._cat(gid[4:]).get("dash"):
                continue
            if gid == "lines" or gid.startswith("sh:"):
                continue
            if not g or g["status"] != "valide":
                self.remove_entities(gid)
        self.ensure_entities()
        self.push_entities()
        await self.async_sync_energy()

    def _snapshot(self):
        self.undo.append((copy.deepcopy(self.groups), {e.id: (e.gid, e.pinned, set(e.excluded)) for e in self.evs}, self.counter,
                          copy.deepcopy(self.pending), dict(self.hints), set(self.doubts)))
        del self.undo[:-20]

    def _action(self, msg):
        a = msg["action"]
        if a.startswith("shared_"):                    # à part : pas dans la pile d'annulation des appareils de la courbe
            return self._shared_action(msg)
        self._ranged = False                          # chaque réponse sert aussi aux utilisations passées encore incertaines
        if a == "undo":
            if not self.undo:
                return
            self.groups, states, self.counter, self.pending, self.hints, self.doubts = self.undo.pop()
            self.store.set_meta("doubts", sorted(self.doubts))
            self.store.set_meta("hints", self.hints)
            self.store.set_meta("pending", self.pending)
            for e in self.evs:
                if e.id in states:
                    e.gid, e.pinned, e.excluded = states[e.id][0], states[e.id][1], set(states[e.id][2])
            self._recompute()
            self._save()
            return
        self._snapshot()
        gid = msg.get("gid")
        if a == "rename":
            g = self.groups[gid]
            g["name"] = msg["name"].strip()[:40]
            g["auto"] = False
            if g["status"] == "nouveau":
                g["status"] = "nomme"
        elif a == "move":
            target = msg.get("target")
            if target == "new":
                target = self._new_group()["id"]
            ids = {int(str(i).lstrip("e")) for i in msg["ids"]}
            for e in self.evs:
                if e.id in ids:
                    self._verdict(e, target)
                    if e.gid and e.gid != target:
                        e.excluded.add(e.gid)
                    e.gid, e.pinned = target, True
                    e.excluded.discard(target)
            self._dedupe_near(ids)
            self._drop_empty()
        elif a == "confirm":
            # « Oui, c'est ça » : l'utilisation reste dans ce groupe et sert de référence.
            ids = {int(str(i).lstrip("e")) for i in msg["ids"]}
            for e in self.evs:
                if e.id in ids and e.gid:
                    self._verdict(e, e.gid)
                    e.pinned = True
        elif a == "label":
            # « C'est… » : l'utilisateur dit ce que c'est, on range dans le groupe de ce nom (créé au besoin).
            self._label({int(str(i).lstrip("e")) for i in msg["ids"]}, msg["name"], judge=True)
        elif a == "unmaybe":
            # retire un « peut-être » mis par erreur
            for i in msg["ids"]:
                self.hints.pop(int(str(i).lstrip("e")), None)
            self.store.set_meta("hints", self.hints)
        elif a == "track_stop":
            # « C'est fini » : l'appareil suivi est éteint maintenant
            self._update_tracks(force=msg["tid"])
        elif a == "track_duration":
            # « Durée » : il reste N minutes à partir de maintenant (ce qu'affiche l'appareil) ;
            # la durée totale du programme est gardée pour la proposer une autre fois
            for t in self.tracks:
                if t["id"] == msg["tid"]:
                    t["planned_end"] = time.time() + int(msg["minutes"]) * 60
                    total = round((t["planned_end"] - t["at"]) / 60)
                    t["minutes"] = total
                    progs = self._meta("program_minutes", {})
                    old = progs.get(t["name"].lower())
                    used = [m for m in old if abs(m - total) > 5] if isinstance(old, list) else []
                    progs[t["name"].lower()] = ([total] + used)[:5]
                    self._set_meta("program_minutes", progs)
            self.store.set_meta("tracks", self.tracks)
        elif a == "track_cancel":
            self.tracks = [t for t in self.tracks if t["id"] != msg["tid"]]
            self.store.set_meta("tracks", self.tracks)
        elif a == "cancel_pending":
            # retire une seule étiquette en attente (repérée par l'instant où elle a été dite)
            self.pending = [p for p in self.pending if abs(p["t"] - msg["t"]) > 1e-3]
            self.store.set_meta("pending", self.pending)
        elif a == "doubt":
            # « Pas sûr » (ou l'enlever) : l'utilisation reste où elle est, sans servir de référence.
            for i in msg["ids"]:
                i = int(str(i).lstrip("e"))
                self.doubts.discard(i) if i in self.doubts else self.doubts.add(i)
                for e in self.evs:
                    if e.id == i and e.id in self.doubts:
                        e.pinned = False
            self.store.set_meta("doubts", sorted(self.doubts))
        elif a == "maybe":
            # « Peut-être… » : un indice, sans ranger ni verrouiller l'utilisation.
            for i in msg["ids"]:
                self.hints[int(str(i).lstrip("e"))] = msg["name"].strip()[:40]
            self.store.set_meta("hints", self.hints)
        elif a == "label_now":
            # « Ce qui tourne en ce moment (ou vient de s'arrêter), c'est… » : appliqué dès que l'utilisation est connue.
            # S'il tourne encore, on le suit jusqu'à son arrêt ; sinon (ou s'il ne peut pas être suivi) l'étiquette
            # attend la fin de l'utilisation. Elle n'est jamais perdue.
            at, pw = msg.get("at"), msg.get("p") or 1
            running = at and any(abs(x["start"] - at) <= 60 and abs(x["p"] - pw) / max(x["p"], pw) <= 0.35 and not x["end"]
                                 for x in self.running_parts)
            started = bool(running and not msg.get("maybe") and self.open_start is not None and at >= self.open_start - 60
                           and self._start_track(msg["name"].strip()[:40], at, pw, user=True))
            if not started:
                self.pending.append({"t": time.time(), "name": msg["name"].strip()[:40], "maybe": bool(msg.get("maybe")),
                                     "at": at, "p": msg.get("p")})
            self._apply_pending()
            self.store.set_meta("pending", self.pending)
        elif a == "label_at":
            # « À telle heure, c'était… » : pour une utilisation passée (hier, avant-hier), choisie par son heure
            t = msg["t"]
            inside = [e for e in self.evs if self.rows[e.id]["start"] - 60 <= t <= self.rows[e.id]["end"] + 60]
            near = inside or [e for e in self.evs if abs(self.rows[e.id]["start"] - t) <= 600]
            if not near:
                raise KeyError("aucune utilisation à cette heure")
            best = min(near, key=lambda e: abs(self.rows[e.id]["start"] - t))
            if msg.get("maybe"):
                self.hints[best.id] = msg["name"].strip()[:40]
                self.store.set_meta("hints", self.hints)
            else:
                self._label({best.id}, msg["name"], judge=True)
        elif a in ("energy_add", "energy_remove"):
            self.groups[gid]["energy_dash"] = a == "energy_add"
        elif a in ("energy_undo", "energy_redo"):
            # « Retirer » (ou « Remettre ») un ajout faux dans le tableau Énergie
            self._undo_energy(msg["lid"], back=a == "energy_redo")
        elif a == "cat_create":
            self.cats.append({"id": f"c{int(time.time() * 1000)}", "name": msg["name"].strip()[:40] or "Catégorie",
                              "dash": False, "since": None, "total": 0.0})
            self.store.set_meta("categories", self.cats)
        elif a == "cat_rename":
            self._cat(msg["cid"])["name"] = msg["name"].strip()[:40] or "Catégorie"
            self.store.set_meta("categories", self.cats)
            self.hass.loop.call_soon_threadsafe(self._rename_device, "cat:" + msg["cid"], self._cat(msg["cid"])["name"])
        elif a == "cat_delete":
            # ses appareils redeviennent sans catégorie (ils restent nommés, avec leurs utilisations)
            for gid_ in list(self.groups):
                if self.cat_of(gid_) == msg["cid"]:
                    self.groups[gid_]["cat"] = None
            self.cats = [c for c in self.cats if c["id"] != msg["cid"]]
            self.store.set_meta("categories", self.cats)
        elif a == "cat_set":
            # un appareil n'est que dans une catégorie à la fois : jamais compté deux fois
            self.groups[gid]["cat"] = msg.get("cid") or None
        elif a == "cat_dash":
            # catégorie dans le tableau Énergie : comptée à partir de maintenant, comme un vrai compteur
            c = self._cat(msg["cid"])
            c["dash"] = bool(msg.get("on"))
            if c["dash"] and not c.get("since"):
                c["since"], c["total"] = time.time(), 0.0
            self.store.set_meta("categories", self.cats)
        elif a == "merge":
            src, dst = msg["src"], msg["dst"]
            for e in self.evs:
                if e.gid == src:
                    e.gid = dst
                e.excluded.discard(src)
            a_, b_ = self.groups[src], self.groups[dst]
            if not b_["name"] and a_["name"]:
                b_["name"], b_["status"] = a_["name"], a_["status"]
            b_["energy"] = round(b_.get("energy", 0) + a_.get("energy", 0), 4)
            del self.groups[src]
        elif a == "split":
            ng = self._new_group()
            g = self.groups[gid]
            for e in self.evs:
                if e.gid == gid and e.f["P"] < msg["thr"]:
                    e.gid, e.pinned = ng["id"], True
            if g["name"]:
                ng["name"], ng["status"] = g["name"] + " (niveau bas)", "nomme"
        elif a == "validate":
            # « Valider » : capteurs créés ET ajouté dans le tableau Énergie (tableau Énergie), sauf s'il y est déjà compté
            # avec sa catégorie (jamais deux fois). « Retirer » dans Appareils l'enlève.
            self.groups[gid]["status"] = "valide"
            self.groups[gid]["auto"] = False
            self.groups[gid]["energy_dash"] = True
        elif a == "delete":
            for e in self.evs:
                if e.gid == gid:
                    e.gid, e.pinned = None, True
            del self.groups[gid]
        self._recompute()
        self._save()

    def _verdict(self, e, new_gid):
        """Ce que tu dis d'une utilisation rangée tout seul : juste ou faux. C'est la fiabilité mesurée, sur tes réponses."""
        old = self.groups.get(e.gid, {}).get("name") if e.gid else None
        if e.pinned or not old:
            return
        new = self.groups.get(new_gid, {}).get("name") if new_gid else None
        v = self._meta("verdicts", [])
        v.append({"t": time.time(), "start": self.rows[e.id]["start"], "auto": old, "user": new, "ok": new_gid == e.gid})
        self._set_meta("verdicts", [x for x in v if x["t"] > time.time() - 90 * 86400][-1000:])

    def _dedupe_near(self, ids):
        """Après une réponse de l'utilisateur : la protection anti-doublon sur les utilisations de la même journée."""
        spans = [(self.rows[e.id]["start"], self.rows[e.id]["end"]) for e in self.evs if e.id in ids]
        if spans:
            a, b = min(s for s, _ in spans) - 86400, max(e for _, e in spans) + 86400
            self._dedupe_overlaps([e for e in self.evs if self.rows[e.id]["end"] >= a and self.rows[e.id]["start"] <= b])

    def _label(self, ids: set, name: str, judge: bool = False):
        name = name.strip()[:40]
        g = next((x for x in self.groups.values() if (x.get("name") or "").lower() == name.lower()), None)
        if g is None:
            g = self._new_group()
            g["name"], g["status"] = name, "nomme"
        for e in self.evs:
            if e.id in ids:
                if judge:
                    self._verdict(e, g["id"])
                if e.gid and e.gid != g["id"]:
                    e.excluded.add(e.gid)
                e.gid, e.pinned = g["id"], True
                e.excluded.discard(g["id"])
        self._dedupe_near(ids)
        self._drop_empty()

    def _apply_pending(self) -> bool:
        """Range chaque « en ce moment, c'est… » dans l'utilisation qui tournait, ou venait de s'arrêter, à ce moment-là."""
        done, keep, now = False, [], time.time()
        for p in self.pending:
            t = p["t"]
            if p.get("at"):
                # un saut précis (heure + puissance) : le morceau qui démarre à ce moment-là avec cette puissance
                at, pw = p["at"], p.get("p") or 1
                # même moment ET même puissance (à 35 % près) : jamais un bloc mélangé avec autre chose
                cands = [e for e in self.evs if abs(self.rows[e.id]["start"] - at) <= 120
                         and abs(e.f["P"] - pw) / max(e.f["P"], pw) <= 0.35]
                still_running = self.open_start is not None and self.open_start <= at + 60
                score = lambda e: abs(self.rows[e.id]["start"] - at) / 60 + abs(e.f["P"] - pw) / max(e.f["P"], pw)
            else:
                inside = lambda e: self.rows[e.id]["start"] <= t <= self.rows[e.id]["end"] + 60
                cands = [e for e in self.evs if self.rows[e.id]["start"] <= t + 180 and self.rows[e.id]["end"] >= t - 900]
                still_running = self.open_start is not None and self.open_start <= t + 60 and not any(inside(e) for e in cands)
                # le morceau qui tournait à ce moment-là, sinon celui qui a démarré ou s'est arrêté le plus près
                score = lambda e: 0 if inside(e) else min(abs(self.rows[e.id]["start"] - t), abs(self.rows[e.id]["end"] - t))
            if p.get("at") and not cands and not still_running:
                # rien d'isolé à ce moment-là : on découpe l'utilisation mélangée en suivant l'indication
                carved = self._carve(p["at"], p.get("p") or 1)
                cands = [carved] if carved else []
            if cands and not still_running:
                best = min(cands, key=score)
                if p.get("maybe"):
                    self.hints[best.id] = p["name"]
                    self.store.set_meta("hints", self.hints)
                else:
                    self._label({best.id}, p["name"], judge=True)
                done = True
                _LOGGER.info("Empreinte : « %s » appliqué à l'utilisation de %s", p["name"],
                             dt_util.as_local(dt_util.utc_from_timestamp(self.rows[best.id]["start"])).strftime("%H:%M"))
            elif now - t < 6 * 3600:
                keep.append(p)
        self.pending = keep
        self.store.set_meta("pending", self.pending)
        return done

    def _carve(self, at: float, power: float):
        """Découpe guidée : sort d'une utilisation mélangée l'appareil que l'utilisateur a désigné."""
        boxes = [e for e in self.evs if self.rows[e.id]["start"] - 30 <= at <= self.rows[e.id]["end"]]
        for box in sorted(boxes, key=lambda e: self.rows[e.id]["end"] - self.rows[e.id]["start"], reverse=True):
            r = self.rows[box.id]
            res = engine.extract_component(r["prof"], round((at - r["start"]) / engine.STEP), power)
            if not res:
                continue
            k0, part, rest = res
            start = r["start"] + k0 * engine.STEP
            loc = dt_util.as_local(dt_util.utc_from_timestamp(start))
            f = engine.features(part, loc.hour + loc.minute / 60)
            f["split"] = True
            eid = self.store.add_event(start, start + len(part) * engine.STEP, r["base"], part, f)
            self.rows[eid] = {"start": start, "end": start + len(part) * engine.STEP, "base": r["base"], "prof": part}
            ev = engine.Ev(eid, f)
            self.evs.append(ev)
            # le reste de l'utilisation mélangée garde sa place, recalculé sans cet appareil
            keep = [k for k, v in enumerate(rest) if v >= 100]
            if not keep or max(rest) < engine.ON_W:
                self.store.delete_events([box.id])
                self.evs.remove(box)
                del self.rows[box.id]
            else:
                ka, kb = keep[0], keep[-1] + 1
                s0 = r["start"] + ka * engine.STEP
                loc = dt_util.as_local(dt_util.utc_from_timestamp(s0))
                box.f = engine.features(rest[ka:kb], loc.hour + loc.minute / 60)
                box.f["split"] = True
                self.rows[box.id] = {**r, "start": s0, "end": s0 + (kb - ka) * engine.STEP, "prof": rest[ka:kb]}
                self.store.update_event(box.id, s0, s0 + (kb - ka) * engine.STEP, rest[ka:kb], box.f)
            _LOGGER.info("Empreinte : appareil de %s W sorti de l'utilisation mélangée de %s (indiqué par l'utilisateur)",
                         part[0] if part else 0, dt_util.as_local(dt_util.utc_from_timestamp(r["start"])).strftime("%H:%M"))
            return ev
        return None

    def _drop_empty(self):
        for gid in [gid for gid, g in self.groups.items() if g["status"] != "valide" and not any(e.gid == gid for e in self.evs)]:
            del self.groups[gid]

    # ------------------------------------------------------------ données pour l'interface
    def state_payload(self) -> dict:
        horizon = time.time() - EVENTS_SENT_DAYS * 86400
        evs = []
        for e in self.evs:
            r = self.rows[e.id]
            if r["start"] < horizon:
                continue
            # morceau provisoire qui tombe sur les moments allumés d'un appareil suivi (au moins 60 %)
            tracked = None
            for t in self.tracks:
                on = sum(max(0, min(r["end"], b) - max(r["start"], a)) for a, b in t.get("ivs", []))
                if on >= 0.6 * max(1, r["end"] - r["start"]) and abs(e.f["P"] - t["p"]) / max(e.f["P"], t["p"]) <= 0.25                         and not self._other_piece(e, t["p"], t.get("ivs") or [[t["at"], time.time()]]):
                    tracked = t["name"]
            prof, st = r["prof"], engine.STEP
            if len(prof) > PROFILE_MAX_POINTS:
                k = math.ceil(len(prof) / PROFILE_MAX_POINTS)
                prof = [round(sum(prof[i:i + k]) / len(prof[i:i + k])) for i in range(0, len(prof), k)]
                st *= k
            evs.append({"id": f"e{e.id}", "start": round(r["start"] * 1000), "dur": e.f["D"], "p": e.f["P"],
                        "wh": e.f["wh"], "shape": e.f["shape"], "split": e.f.get("split", False), "hint": self.hints.get(e.id), "doubt": e.id in self.doubts, "tracked_by": tracked, "cut": e.f.get("cut", False), "probable": e.probable, "guess": self._guess_names(e),
                        "steps": [[k * engine.STEP, d] for k, d in engine._edges(r["prof"])] if len(engine._edges(r["prof"])) >= 3 else None, "gid": e.gid, "sim": self.sims.get(e.id),
                        "pinned": e.pinned, "st": st, "prof": prof})
        groups = []
        for g in self.groups.values():
            c = self.cents.get(g["id"])
            groups.append({**g, "conf": self._conf(g["id"]), **self._conf_detail(g["id"]), "cat": self.cat_of(g["id"]), "P": c["P"] if c else None, "D": c["D"] if c else None,
                           "shape": c["shape"] if c else None, "entities": self.entity_ids(g["id"])})
        return {
            "groups": groups, "events": evs, "kinds": {**{k: v[0] for k, v in engine.KINDS.items()}, **self.learned_labels},
            "status": {"verdicts": self._verdict_summary(), "categories": [{**{k: v for k, v in c.items() if k != "counted"}, "power": self.cat_power(c["id"])} for c in self.cats],
                       "energy_log": [{**x, "entity": self._energy_entity(x["target"]), "in_dash": self._in_dash(x["target"])}
                                      for x in self.energy_log[-200:]], "last_run": self.last_run, "error": self.last_error, "n_events": len(self.evs),
                       "first_sample": self._first, "power_entity": self.power_entity,
                       "live": [{"gid": g, "start": r["start"], "power": r["power"]} for g, r in self.live.running.items() if not r["off_since"]],
                       "can_undo": bool(self.undo),
                       "pending": self.pending, "running_since": self.open_start,
                       # ce qui tourne, sauf les morceaux de l'appareil suivi lui-même (ceux qui démarrent avec lui)
                       "running_parts": [{**x, "pending": self._pending_for(x["start"], x["p"])} for x in self.running_parts
                                         if not any(abs(x["p"] - t["p"]) / max(x["p"], t["p"]) <= 0.25
                                                    and self._owns_start(x["start"], t.get("ivs") or [[t["at"], 0]]) for t in self.tracks)],
                       "tracks": [{**{k: t.get(k) for k in ("id", "name", "at", "p", "on", "since", "on_s", "user", "planned_end", "minutes")},
                                   "used": self._used_minutes(t["name"])} for t in self.tracks],
                       "shared": self._shared_payload(),
                       "measured": [{"entity": m, "name": (self.hass.states.get(m).name if self.hass.states.get(m) else m),
                                     "power": round(self.sub_now.get(m, 0))} for m in self.measured]},
        }

    def _guess_names(self, e) -> list[str]:
        """Pas reconnu : les 2 appareils nommés les plus proches, à proposer en un clic (« Cafetière ? Micro-ondes ? »).
        Rien n'est rangé tout seul : c'est l'utilisateur qui tranche, et le programme apprend de sa réponse."""
        if e.gid and self.groups.get(e.gid, {}).get("name"):
            return []
        near = sorted((engine.dist(e.f, c), self.groups[gid]["name"]) for gid, c in self.cents.items()
                      if self.groups.get(gid, {}).get("name") and gid not in e.excluded)
        return [n for d, n in near[:2] if d <= 1.6 * engine.ASSIGN_MAX]

    def _verdict_summary(self) -> dict:
        """Fiabilité mesurée sur les 30 derniers jours : rangements automatiques que tu as confirmés ou corrigés."""
        v = [x for x in self._meta("verdicts", []) if x["t"] > time.time() - 30 * 86400]
        wrong = [x for x in v if not x["ok"]]
        return {"n": len(v), "ok": len(v) - len(wrong),
                "errors": [{"start": x["start"], "auto": x["auto"], "user": x["user"]} for x in wrong[-10:]]}

    def _conf(self, gid):
        """Certitude que ce qui est rangé ici est bien cet appareil (pour l'ajouter dans le tableau Énergie).

        Une utilisation que tu as confirmée compte pleinement ; les autres selon leur ressemblance en
        puissance et en forme (la durée, elle, change d'une fois sur l'autre : chauffage, programme…).
        Il en faut au moins 8, et chaque utilisation que tu as dû sortir de ce groupe la fait baisser."""
        mem = [e for e in self.evs if e.gid == gid]
        c = self.cents.get(gid)
        if not mem or not c:
            return 0
        s = []
        for e in mem:
            if e.pinned and e.id not in self.doubts:
                s.append(1.0)
                continue
            dp = abs(e.f["P"] - c["P"]) / max(0.08 * max(e.f["P"], c["P"]), 120)
            s.append(engine.similarity(dp) * (1 if e.f["shape"] == c["shape"] else 0.7) * (0.5 if e.id in self.doubts else 1))
        n, errors = len(mem), sum(1 for e in self.evs if gid in e.excluded)
        return round(sum(s) / n * min(1, n / 8) ** 0.5 * n / (n + 2 * errors), 3)

    def _conf_detail(self, gid):
        mem = [e for e in self.evs if e.gid == gid]
        return {"n_uses": len(mem), "n_sure": sum(1 for e in mem if e.pinned and e.id not in self.doubts),
                "n_fixed": sum(1 for e in self.evs if gid in e.excluded)}

    async def async_state(self):
        self._first = await self.hass.async_add_executor_job(self.store.first_sample_ts)
        return self.state_payload()

    async def async_day(self, day: str) -> dict:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt_util.get_default_time_zone())
        t0 = d.timestamp()
        t1 = (d + timedelta(days=1)).timestamp()
        rows = await self.hass.async_add_executor_job(self.store.samples, t0, t1)
        rows += [r for r in self.buffer if t0 <= r[0] < t1]
        n = round((t1 - t0) / engine.STEP)
        vals, subs, j, now = [], [], 0, time.time()
        for k in range(n):
            tk = t0 + k * engine.STEP
            while j + 1 < len(rows) and rows[j + 1][0] <= tk:
                j += 1
            ok = rows and rows[j][0] <= tk <= now and tk - rows[j][0] < 3600
            vals.append(round(rows[j][1]) if ok else None)
            subs.append(round(rows[j][2]) if ok and len(rows[j]) > 2 else 0)
        return {"start": round(t0 * 1000), "step": engine.STEP, "values": vals, "sub": subs}

    # ------------------------------------------------------------ capteurs HA
    def entity_ids(self, gid):
        return [e.entity_id for e in self.entities.get(gid, []) if e.entity_id]

    @callback
    def ensure_entities(self):
        from .binary_sensor import RunningSensor
        from .sensor import EnergySensor, PowerSensor
        if "sensor" not in self.add_entities or "binary_sensor" not in self.add_entities:
            return
        from .sensor import CatEnergy, CatPower
        for c in self.cats:
            if c["dash"] and "cat:" + c["id"] not in self.entities:
                self.entities["cat:" + c["id"]] = [CatPower(self, c["id"]), CatEnergy(self, c["id"])]
                self.add_entities["sensor"](self.entities["cat:" + c["id"]])
        for gid, g in self.groups.items():
            if g["status"] != "valide" or gid in self.entities:
                continue
            ents = [RunningSensor(self, gid), PowerSensor(self, gid), EnergySensor(self, gid)]
            self.entities[gid] = ents
            self.add_entities["binary_sensor"]([ents[0]])
            self.add_entities["sensor"](ents[1:])
        from .sensor import SharedEnergy, SharedPower
        from .binary_sensor import SharedRunning
        want = set()
        for eid, n in self.shared.appliances():
            key = "sh:" + self.shared.key(eid, n)
            want.add(key)
            if key not in self.entities:
                ents = [SharedRunning(self, eid, n), SharedPower(self, eid, n), SharedEnergy(self, eid, n)]
                self.entities[key] = ents
                self.add_entities["binary_sensor"]([ents[0]])
                self.add_entities["sensor"](ents[1:])
        self._drop_old_shared(want)
        dev_reg = dr.async_get(self.hass)
        for gid, g in self.groups.items():
            dev = self._device(gid)
            if dev and g["name"] and dev.name != g["name"]:
                dev_reg.async_update_device(dev.id, name=g["name"])

    @callback
    def _drop_old_shared(self, want: set):
        """Un appareil retiré d'une prise partagée (réglages) : ses capteurs disparaissent de HA."""
        reg = er.async_get(self.hass)
        if reg is None:
            return
        keep = {e.unique_id for k in want for e in self.entities.get(k, [])}
        for ent in er.async_entries_for_config_entry(reg, self.entry.entry_id):
            if (ent.unique_id or "").startswith(f"{DOMAIN}_sh_") and ent.unique_id not in keep:
                reg.async_remove(ent.entity_id)
        dev_reg = dr.async_get(self.hass)
        for dev in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            ids = [i[1] for i in dev.identifiers if i[0] == DOMAIN]
            if ids and ids[0].startswith("sh:") and ids[0] not in want:
                dev_reg.async_remove_device(dev.id)

    @callback
    def push_entities(self):
        for ents in self.entities.values():
            for e in ents:
                if e.hass:
                    e.async_write_ha_state()

    @callback
    def remove_entities(self, gid):
        ents = self.entities.pop(gid, [])
        reg = er.async_get(self.hass)
        for e in ents:
            if e.entity_id and reg.async_get(e.entity_id):
                reg.async_remove(e.entity_id)
        dev = self._device(gid)
        if dev:
            dr.async_get(self.hass).async_remove_device(dev.id)

    @callback
    def _rename_device(self, key, name):
        """Le nom affiché dans HA suit le nouveau nom (les identifiants des capteurs, eux, ne changent pas)."""
        dev = self._device(key)
        if dev:
            dr.async_get(self.hass).async_update_device(dev.id, name=name)

    def _device(self, gid):
        dev_reg = dr.async_get(self.hass)
        for dev in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            if (DOMAIN, gid) in dev.identifiers:
                return dev
        return None

    def live_for(self, gid):
        return self.live.get(gid)
