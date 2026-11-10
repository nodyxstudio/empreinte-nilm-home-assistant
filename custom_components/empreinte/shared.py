"""Prises partagées : une prise connectée qui alimente plusieurs appareils jamais allumés en même temps
(lave-linge et sèche-linge, par exemple).

La courbe de la prise est propre (aucun autre appareil de la maison dedans) : chaque programme y est découpé, puis
attribué à l'un des appareils de la prise. Au début, d'après des connaissances générales (un lave-linge chauffe au
début puis fait tourner son moteur ; un sèche-linge chauffe tout du long, ou tourne vers 600 W s'il est à pompe à
chaleur). Dès que l'utilisateur a confirmé un programme de chaque appareil, d'après ce qu'il a confirmé.

Aucune dépendance à Home Assistant : le coordinateur lui donne l'historique de la prise (instant, watts).
"""
from __future__ import annotations

import math
import unicodedata

STEP = 30            # s : la courbe de la prise est relue toutes les 30 s
ON_W = 10            # au-dessus, l'appareil est en marche (veille d'un lave-linge : 1 à 3 W)
GAP_S = 600          # 10 min sous ON_W : le programme est fini (pauses de trempage plus courtes)
CORE_W = 300         # fin « utile » d'un programme : la dernière minute au-dessus (sans l'anti-froissage)
HEAT_W = 1000        # une résistance qui chauffe
MIN_WH = 20          # en dessous : pas un programme (porte qui se déverrouille, test…)
MIN_CORE_S = 300
SURE = 0.75          # probabilité à atteindre pour attribuer seul ; sinon « à confirmer »
KEEP_DAYS = 400      # programmes gardés (au-delà, leur énergie reste comptée)


def _norm(name: str) -> str:
    return unicodedata.normalize("NFD", name or "").encode("ascii", "ignore").decode().lower()


def kind_of(name: str) -> str | None:
    """Le type d'appareil qu'un nom désigne, pour les connaissances de départ."""
    n = _norm(name)
    if "vaisselle" in n:
        return "lave_vaisselle"
    if "seche" in n and "linge" in n:
        return "seche_linge"
    if "linge" in n or "machine a laver" in n:
        return "lave_linge"
    return None


def resample(points: list[tuple[float, float]], start: float, end: float) -> list[int]:
    """Puissance toutes les STEP secondes entre start et end (la dernière valeur reçue reste valable)."""
    out, k, cur = [], 0, 0.0
    pts = sorted(points)
    t = start
    while t < end:
        while k < len(pts) and pts[k][0] <= t:
            cur = pts[k][1]
            k += 1
        out.append(round(cur))
        t += STEP
    return out


def find_runs(points: list[tuple[float, float]], now: float) -> tuple[list[dict], dict | None]:
    """Découpe l'historique de la prise en programmes. Retourne (programmes finis, programme en cours ou None).

    Chaque valeur reçue reste valable jusqu'à la suivante (la dernière : jusqu'à maintenant). Deux moments allumés
    séparés de moins de GAP_S font le même programme (trempage, pause entre lavage et essorage)."""
    pts = sorted((t, max(0.0, p)) for t, p in points if p is not None and t <= now)
    if not pts:
        return [], None
    ons = []                                             # [début, fin] des moments au-dessus de ON_W
    for i, (t, p) in enumerate(pts):
        t2 = pts[i + 1][0] if i + 1 < len(pts) else now
        if p >= ON_W and t2 > t:
            if ons and t - ons[-1][1] <= GAP_S:
                ons[-1][1] = t2
            else:
                ons.append([t, t2])
    done, open_run = [], None
    for a, b in ons:
        if now - b <= GAP_S:                             # allumé, ou arrêté depuis trop peu pour être sûr
            open_run = _measure(pts, a, min(now, max(b, a + STEP)))
        else:
            done.append(_measure(pts, a, b))
    return [r for r in done if r["wh"] >= MIN_WH and r["f"]["D"] >= MIN_CORE_S], open_run


def _measure(pts, start, end) -> dict:
    prof = resample(pts, start, end)
    wh = sum(prof) * STEP / 3600
    return {"start": start, "end": end, "wh": round(wh, 1), "f": features(prof), "prof": prof}


def features(prof: list[int]) -> dict:
    """Ce qui distingue les appareils d'une même prise : durée, énergie, quand et combien de temps ça chauffe,
    et ce que l'appareil tire le reste du temps (moteur, pompe à chaleur)."""
    core = len(prof)
    while core > 0 and prof[core - 1] < CORE_W:
        core -= 1
    core = max(core, 1)
    p = prof[:core]
    n = len(p)
    heat = [i for i, v in enumerate(p) if v >= HEAT_W]
    blocks, prev = 0, None
    for i in heat:
        if prev is None or i - prev > 180 // STEP:          # deux chauffes à plus de 3 min l'une de l'autre
            blocks += 1
        prev = i
    low = [v for v in p if ON_W <= v < HEAT_W]
    mid = [v for v in p if 300 <= v < HEAT_W]
    return {
        "D": n * STEP,
        "wh": round(sum(p) * STEP / 3600, 1),
        "heat": round(len(heat) / n, 3),
        "heat_pos": round(sum(heat) / len(heat) / n, 3) if heat else None,
        "blocks": blocks,
        "low": round(sum(low) / len(low)) if low else 0,
        "mid": round(len(mid) / n, 3),
        "peak": max(p) if p else 0,
    }


def _soft(x, lo, hi, width):
    """1 entre lo et hi, puis décroît doucement (écart compté en `width`)."""
    if x < lo:
        return math.exp(-0.5 * ((lo - x) / width) ** 2)
    if x > hi:
        return math.exp(-0.5 * ((x - hi) / width) ** 2)
    return 1.0


def prior(kind: str, f: dict) -> float:
    """Ressemblance générale d'un programme avec un type d'appareil (0 à 1)."""
    mins = f["D"] / 60
    if kind == "lave_linge":
        # une chauffe au début (ou aucune à froid), puis le moteur : pas d'énergie en continu
        s = _soft(mins, 25, 240, 30) * _soft(f["heat"], 0, 0.35, 0.1) * _soft(f["mid"], 0, 0.45, 0.15)
        if f["heat_pos"] is not None:
            s *= _soft(f["heat_pos"], 0, 0.45, 0.15)
        return s * _soft(f["blocks"], 0, 2, 1)
    if kind == "seche_linge":
        mins_ok = _soft(mins, 30, 300, 40)
        resist = _soft(f["heat"], 0.4, 1, 0.15) * (_soft(f["heat_pos"], 0.3, 0.7, 0.15) if f["heat_pos"] is not None else 0)
        pac = _soft(f["mid"], 0.55, 1, 0.15) * _soft(f["heat"], 0, 0.15, 0.1)        # pompe à chaleur : ~600 W stables
        return mins_ok * max(resist, pac)
    if kind == "lave_vaisselle":
        return _soft(mins, 60, 240, 30) * _soft(f["blocks"], 2, 3, 0.7) * _soft(f["heat"], 0.05, 0.4, 0.1)
    return 0.0


def _vec(f):
    return (math.log(max(f["D"], 60)), math.log(max(f["wh"], 1)), f["heat"] * 3, (f["heat_pos"] if f["heat_pos"] is not None else 0.5) * 2,
            math.log(max(f["low"], 1)), min(f["blocks"], 6) / 2, f["mid"] * 3)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(_vec(a), _vec(b))))


def classify(f: dict, names: list[str], examples: dict[str, list[dict]]) -> tuple[str | None, float]:
    """Le nom le plus probable parmi ceux de la prise, et sa probabilité. (None, 0) si on ne peut pas dire."""
    if len(names) == 1:
        return names[0], 1.0
    score = {}
    for n in names:
        ex = examples.get(n) or []
        if ex:
            d = min(_dist(f, e) for e in ex)
            score[n] = math.exp(-0.5 * (d / 0.8) ** 2)
            k = kind_of(n)
            if k:
                # peu d'exemples (un lavage à froid ne dit rien d'un lavage chaud) : les connaissances générales gardent
                # tout leur poids ; ensuite, ce qui a été confirmé compte davantage
                score[n] = max(score[n], prior(k, f)) if len(ex) < 3 else 0.7 * score[n] + 0.3 * prior(k, f)
        else:
            k = kind_of(n)
            score[n] = prior(k, f) if k else None
    known = {n: s for n, s in score.items() if s is not None}
    if not known:
        return None, 0.0
    if len(known) < len(names):
        # un appareil dont on ne sait rien : on ne choisit l'autre que si le programme lui ressemble vraiment
        n, s = max(known.items(), key=lambda kv: kv[1])
        return (n, round(s * 0.8, 2)) if s >= 0.5 else (None, 0.0)
    tot = sum(known.values())
    if tot <= 1e-6:
        return None, 0.0
    n, s = max(known.items(), key=lambda kv: kv[1])
    return n, round(s / tot, 2)


class SharedPlugs:
    """Les prises partagées, leurs programmes et l'énergie de chaque appareil."""

    def __init__(self, plugs: list[dict], state: dict | None = None):
        # plugs : [{"entity": "sensor.prise_buanderie_puissance", "names": ["Lave-linge", "Sèche-linge"]}]
        self.plugs = [{"entity": p["entity"], "names": [n for n in p.get("names", []) if n]} for p in plugs if p.get("entity")]
        st = state or {}
        self.runs: list[dict] = st.get("runs", [])
        self.until: dict[str, float] = st.get("until", {})
        self.dash: list[str] = st.get("dash", [])
        self.counter: int = st.get("counter", 0)
        self.open: dict[str, dict] = {}
        self.archived: dict[str, float] = st.get("archived", {})     # énergie des programmes trop anciens, par clé

    def state(self) -> dict:
        return {"runs": self.runs, "until": self.until, "dash": self.dash, "counter": self.counter, "archived": self.archived}

    @staticmethod
    def key(entity: str, name: str) -> str:
        return f"{entity}|{name}"

    def names(self, entity: str) -> list[str]:
        return next((p["names"] for p in self.plugs if p["entity"] == entity), [])

    def examples(self, entity: str) -> dict[str, list[dict]]:
        ex: dict[str, list[dict]] = {}
        for r in self.runs:
            if r["plug"] == entity and r.get("user") and r.get("name"):
                ex.setdefault(r["name"], []).append(r["f"])
        return ex

    def since(self, entity: str, now: float, backfill_s: float) -> float:
        return self.until.get(entity) or now - backfill_s

    def ingest(self, entity: str, points: list[tuple[float, float]], now: float) -> int:
        """Ajoute les programmes finis trouvés dans l'historique de la prise. Retourne combien sont nouveaux."""
        done, open_run = find_runs(points, now)
        names, ex = self.names(entity), self.examples(entity)
        seen = {(r["plug"], round(r["start"])) for r in self.runs}
        new = 0
        for r in done:
            if (entity, round(r["start"])) in seen:
                continue
            name, conf = classify(r["f"], names, ex)
            self.counter += 1
            self.runs.append({"id": f"p{self.counter}", "plug": entity, "start": r["start"], "end": r["end"], "wh": r["wh"],
                              "f": r["f"], "name": name if conf >= SURE else None, "guess": name, "conf": conf, "user": False})
            new += 1
        if open_run:
            name, conf = classify(open_run["f"], names, ex) if open_run["f"]["D"] >= 600 else (None, 0.0)
            if len(names) == 1:
                name, conf = names[0], 1.0
            self.open[entity] = {"start": open_run["start"], "power": open_run["prof"][-1] if open_run["prof"] else 0,
                                 "guess": name if conf >= 0.6 else None, "conf": conf}
            # on relira depuis le début du programme en cours la prochaine fois
            self.until[entity] = open_run["start"] - 1
        else:
            self.open.pop(entity, None)
            last = max([r["end"] for r in done] + [self.until.get(entity, 0)])
            self.until[entity] = max(last, now - GAP_S - STEP)
        self._trim(now)
        return new

    def _trim(self, now):
        keep = []
        for r in self.runs:
            if r["start"] < now - KEEP_DAYS * 86400:
                if r.get("name"):
                    k = self.key(r["plug"], r["name"])
                    self.archived[k] = self.archived.get(k, 0) + r["wh"] / 1000
                continue
            keep.append(r)
        self.runs = keep

    def label(self, rid: str, name: str | None):
        """L'utilisateur dit quel appareil a tourné (ou l'enlève). Les programmes non confirmés sont reclassés."""
        r = next((x for x in self.runs if x["id"] == rid), None)
        if r is None:
            raise KeyError(rid)
        if name is not None and name not in self.names(r["plug"]):
            raise KeyError(name)
        r["name"], r["user"] = name, name is not None
        if name:
            r["guess"], r["conf"] = name, 1.0
        self.reclassify(r["plug"])

    def reclassify(self, entity: str):
        names, ex = self.names(entity), self.examples(entity)
        for r in self.runs:
            if r["plug"] != entity or r.get("user"):
                continue
            name, conf = classify(r["f"], names, ex)
            r["guess"], r["conf"] = name, conf
            r["name"] = name if conf >= SURE else None

    def kwh(self, entity: str, name: str) -> float:
        k = self.key(entity, name)
        return round(self.archived.get(k, 0) + sum(r["wh"] for r in self.runs if r["plug"] == entity and r.get("name") == name) / 1000, 4)

    def running(self, entity: str, name: str) -> dict | None:
        o = self.open.get(entity)
        return o if o and o.get("guess") == name else None

    def appliances(self):
        for p in self.plugs:
            for n in p["names"]:
                yield p["entity"], n
