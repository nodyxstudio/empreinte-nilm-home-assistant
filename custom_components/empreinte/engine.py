"""Algorithme d'Empreinte : détection des démarrages et regroupement.

Pur Python, sans dépendance à Home Assistant, pour pouvoir le tester à part.

1. segment()   coupe la courbe de puissance en « événements » : périodes où la
               maison consomme nettement plus que son fond permanent.
2. features()  résume chaque événement : puissance, durée, forme, heure.
3. assign() et dbscan() rangent les événements qui se ressemblent dans des
               groupes, en respectant les corrections faites par l'utilisateur.
4. guess()     propose une hypothèse d'appareil pour chaque groupe.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

STEP = 15            # s, pas des profils enregistrés
ON_W = 350           # écart au fond qui ouvre un événement
OFF_W = 200          # sous cet écart, l'appareil est considéré éteint
OFF_FRAC = 0.35      # … ou sous 35 % du pic de l'utilisation
GAP_S = 150          # un creux plus court ne coupe pas l'événement (thermostat, micro-ondes)
PRE_S = 90           # fond d'une utilisation = médiane des 90 s juste avant son saut
BASE_WIN_S = 900     # le fond = bas de la courbe sur les 15 dernières minutes calmes
MAX_EVENT_S = 6 * 3600
CUT_S = 600          # plus de 10 min sans aucune valeur = coupure (courant ou HA éteint) : on repart de zéro
REARM_S = 180        # après une coupure, tout ce qui se rallume (frigo, box, PC) devient le nouveau fond
EPS = 0.9            # distance max entre voisins pour former un groupe (strict : mieux vaut attendre que mélanger)
MIN_PTS = 3          # il faut au moins 3 démarrages semblables pour créer un groupe
ASSIGN_MAX = 1.2     # distance max pour rattacher une utilisation à un groupe existant


def engine_median(vals):
    s = sorted(vals)
    return s[len(s) // 2]


def quantile(vals, q):
    s = sorted(vals)
    if not s:
        return 0.0
    return s[min(len(s) - 1, int(q * len(s)))]


def segment(ts: list[float], ps: list[float]):
    """Découpe la courbe en événements.

    Retourne (events, open_start) : la liste des événements terminés
    (start, end, base, prof) et le début d'un événement encore en cours, ou None.
    """
    events = []
    quiet: deque = deque()
    base = None
    i_start = None
    last_above = None
    base_ev = 0.0
    peak_ev = 0.0
    prev_t = None
    rearm = None                 # après une coupure : on n'ouvre rien avant cet instant

    def close(i_next):
        t0 = ts[i_start]
        t1 = ts[i_next] if i_next is not None else ts[last_above] + STEP
        events.append({"start": t0, "end": t1, "base": base_ev,
                       "prof": profile(ts, ps, i_start, t0, t1, base_ev)})

    for i, (t, p) in enumerate(zip(ts, ps)):
        if prev_t is not None and t - prev_t > CUT_S:
            # Coupure : l'utilisation en cours s'arrête à la dernière valeur reçue, rien n'est compté pendant
            # le trou, et le retour du courant (tout se rallume d'un coup) n'est pas pris pour un appareil.
            if i_start is not None:
                close(None)
                i_start = None
            quiet, base, rearm = deque(), None, t + REARM_S
        prev_t = t
        if rearm is not None and t < rearm:
            quiet.append((t, p))
            base = quantile([q[1] for q in quiet], 0.2)
            continue
        if i_start is None:
            if base is None:
                base = p
            if p - base > ON_W:
                # Le fond de l'utilisation = le niveau juste avant le saut (et pas la moyenne des 15 dernières
                # minutes, qui retarde) : un chargeur allumé peu avant n'est pas compté dans l'appareil.
                # Le Linky n'envoie une valeur que quand elle change : sans valeur récente, la dernière reçue est encore vraie.
                just_before = [q[1] for q in quiet if q[0] >= t - PRE_S] or ([quiet[-1][1]] if quiet else [])
                base_ev = max(base, engine_median(just_before)) if just_before else base
                if p - base_ev <= ON_W:
                    quiet.append((t, p))
                    continue
                i_start, last_above, peak_ev = i, i, p - base_ev
                continue
            quiet.append((t, p))
            while quiet and quiet[0][0] < t - BASE_WIN_S:
                quiet.popleft()
            base = quantile([q[1] for q in quiet], 0.2)
        else:
            # L'appareil est éteint quand la courbe redescend franchement : sous OFF_W, ou sous un tiers
            # de son pic (un petit appareil allumé entre-temps ne doit pas prolonger l'utilisation).
            peak_ev = max(peak_ev, p - base_ev)
            if p - base_ev > max(OFF_W, OFF_FRAC * peak_ev):
                if t - ts[i_start] > MAX_EVENT_S:
                    close(i)
                    i_start, last_above = i, i
                else:
                    last_above = i
            elif t - ts[last_above] > GAP_S:
                close(last_above + 1)
                i_start = None
                quiet = deque(q for q in quiet if q[0] >= t - BASE_WIN_S)
                quiet.append((t, p))
                base = quantile([q[1] for q in quiet], 0.2)
    open_start = ts[i_start] if i_start is not None else None
    return events, open_start


def cuts(ts: list[float]) -> list[tuple[float, float]]:
    """Les coupures : (dernière valeur avant, première valeur après) pour chaque silence de plus de CUT_S."""
    return [(ts[i - 1], ts[i]) for i in range(1, len(ts)) if ts[i] - ts[i - 1] > CUT_S]


def profile(ts, ps, i0, t_start, t_end, base):
    """Puissance au-dessus du fond, sur une grille de STEP secondes."""
    n = max(1, round((t_end - t_start) / STEP))
    out, j = [], i0
    for k in range(n):
        tk = t_start + k * STEP
        while j + 1 < len(ts) and ts[j + 1] <= tk:
            j += 1
        out.append(max(0, round(ps[j] - base)))
    return out


# ---------- Séparer deux appareils allumés en même temps
# Idée : chaque appareil fait monter la courbe d'un « saut » puis la fait redescendre d'un saut de même taille.
# On retrouve les paires montée/descente de même hauteur ; chaque hauteur correspond à un appareil.
EDGE_W = 300          # plus petit saut pris en compte
EDGE_MERGE = 2        # sauts dans le même sens à moins de 2 points (30 s) = un seul saut
SPLIT_EXPLAINED = 0.7  # les appareils trouvés doivent expliquer au moins 70 % de l'énergie
SPLIT_MAX = 3


def _edges(prof, closed=True, min_edge=EDGE_W):
    """Sauts d'au moins min_edge dans un profil. closed=False : l'utilisation est encore en cours (pas d'arrêt final)."""
    pr = [0] + list(prof) + ([0] if closed else [])
    raw = [(k, pr[k] - pr[k - 1]) for k in range(1, len(pr)) if abs(pr[k] - pr[k - 1]) >= 100]
    out = []
    for k, d in raw:
        if out and (out[-1][1] > 0) == (d > 0) and k - out[-1][2] <= EDGE_MERGE:
            out[-1] = (out[-1][0], out[-1][1] + d, k)
        else:
            out.append((k, d, k))
    return [(k_last if d < 0 else k0, d) for k0, d, k_last in _settle(out, SETTLE_S / STEP) if abs(d) >= min_edge]


SETTLE_S = 60         # le Linky envoie une valeur toutes les 7 à 38 s : la fin du pic peut arriver 1 min après


def _settle(edges, window):
    """Appel de courant au démarrage : un pic suivi de près (window) d'une descente plus petite = un seul
    démarrage, compté à sa puissance stable (chauffage soufflant à céramique, moteur…).
    edges : [(début, Δ, fin)] ; window dans la même unité que les temps."""
    out, i = [], 0
    while i < len(edges):
        e = edges[i]
        if i + 1 < len(edges):
            n = edges[i + 1]
            if e[1] > 0 and n[1] < 0 and n[0] - e[2] <= window and -n[1] < 0.8 * e[1]:
                net = e[1] + n[1]
                if net >= 100:
                    out.append((e[0], net, n[2]))
                i += 2
                continue
        out.append(e)
        i += 1
    return out


def _close(h, m):
    return abs(m - h) <= max(200, 0.25 * h)


def split_session(prof: list[int], min_edge: int = EDGE_W) -> list[tuple[int, list[int]]]:
    """Découpe une utilisation en plusieurs appareils si la courbe le montre clairement.

    Retourne [(décalage en points, profil)], ou [(0, prof)] si on ne sépare pas.
    Un appareil est « net » quand on voit à la fois son propre démarrage et son propre arrêt.
    On ne sépare que si tous les appareils trouvés sont nets, sauf un au plus dont on voit
    au moins le démarrage ou l'arrêt.
    """
    n = len(prof)
    spans = _pair(prof, min_edge=min_edge)
    if not spans:
        return [(0, prof)]
    comps = _components(spans)
    if not 2 <= len(comps) <= SPLIT_MAX:
        return [(0, prof)]
    net = [any(sp[3] and sp[4] for sp in c["spans"]) for c in comps]
    half = [any(sp[3] or sp[4] for sp in c["spans"]) for c in comps]
    if sum(net) < len(comps) - 1 or not all(half):
        return [(0, prof)]
    total = sum(prof) or 1
    explained = sum(c["h"] * (sp[1] - sp[0]) for c in comps for sp in c["spans"])
    if not SPLIT_EXPLAINED <= explained / total <= 1.3:
        return [(0, prof)]
    out = []
    for c in sorted(comps, key=lambda c: min(sp[0] for sp in c["spans"])):
        k0 = max(0, min(sp[0] for sp in c["spans"]))
        k1 = min(n, max(sp[1] for sp in c["spans"]))
        p = [0] * (k1 - k0)
        for a, b, *_ in c["spans"]:
            for k in range(max(a, k0), min(b, k1)):
                p[k - k0] = round(c["h"])
        if len(p) >= 2 and max(p) >= ON_W and sum(p) >= 0.1 * total:
            out.append((k0, p))
    return out if len(out) >= 2 else [(0, prof)]


def _pair(prof, closed=True, min_edge=EDGE_W):
    """Associe chaque montée à la descente de même taille : [(k0, k1, h, démarrage vu, arrêt vu)]."""
    n = len(prof)
    opened, spans = [], []          # opened : (k, h, démarrage vu) ; spans : (k0, k1, h, démarrage vu, arrêt vu)
    for k, d in _edges(prof, closed, min_edge):
        if d > 0:
            opened.append((k - 1, d, True))
            continue
        m = -d
        best = min((o for o in opened if _close(o[1], m)), key=lambda o: abs(o[1] - m), default=None)
        if best:
            opened.remove(best)
            spans.append((best[0], k - 1, (best[1] + m) / 2, best[2], True))
            continue
        pair = None
        for a in range(len(opened)):
            for b in range(a + 1, len(opened)):
                if _close(opened[a][1] + opened[b][1], m):
                    pair = (opened[a], opened[b])
        if pair:   # deux appareils qui s'arrêtent en même temps
            for o in pair:
                opened.remove(o)
                spans.append((o[0], k - 1, o[1], o[2], True))
            continue
        big = max((o for o in opened if o[1] - m >= EDGE_W), key=lambda o: o[1], default=None)
        if big:    # deux appareils démarrés en même temps : un seul s'arrête
            opened[opened.index(big)] = (big[0], big[1] - m, False)
            spans.append((big[0], k - 1, m, False, False))
    for o in opened:
        spans.append((o[0], n, o[1], o[2], False))
    return spans


def _components(spans):
    """Regroupe les intervalles de même hauteur : un appareil qui s'allume et s'éteint (thermostat) reste un seul appareil."""
    comps = []
    for s in sorted(spans, key=lambda s: s[2]):
        for c in comps:
            if abs(math.log(s[2] / c["h"])) < 0.12:
                c["spans"].append(s)
                c["h"] = sum(x[2] for x in c["spans"]) / len(c["spans"])
                break
        else:
            comps.append({"h": s[2], "spans": [s]})
    return comps


def extract_component(prof: list[int], k_at: int, power: float):
    """Découpe guidée : sort d'une utilisation mélangée l'appareil désigné par l'utilisateur
    (son saut de démarrage à k_at, sa puissance environ power).

    Retourne (k0, profil de l'appareil, profil restant), ou None si on ne le retrouve pas clairement.
    """
    n = len(prof)
    best, bs = None, None
    for c in _components(_pair(prof)):
        k0 = min(sp[0] for sp in c["spans"])
        rel = abs(c["h"] - power) / max(c["h"], power)
        if rel > 0.35 or abs(k0 - k_at) > 8:
            continue
        score = abs(k0 - k_at) / 4 + rel
        if bs is None or score < bs:
            best, bs = c, score
    if not best:
        return None
    k0 = max(0, min(sp[0] for sp in best["spans"]))
    k1 = min(n, max(sp[1] for sp in best["spans"]))
    part = [0] * (k1 - k0)
    rest = list(prof)
    h = round(best["h"])
    for a, b, *_ in best["spans"]:
        for k in range(max(a, 0), min(b, n)):
            part[k - k0] = h
            rest[k] = max(0, rest[k] - h)
    if max(part, default=0) < ON_W:
        return None
    return k0, part, rest


def live_parts(prof: list[int]) -> list[dict]:
    """Ce qui tourne dans une utilisation encore en cours, appareil par appareil (comme on le lirait à la main).

    Chaque montée est associée à la descente de même taille ; les marche/arrêt de même hauteur
    (thermostat) restent un seul appareil. Retourne [{k0, k1, h, n, running}].
    """
    n = len(prof)
    out = []
    for c in _components(_pair(prof, closed=False)):
        if c["h"] < EDGE_W:
            continue
        k0 = min(sp[0] for sp in c["spans"])
        k1 = max(sp[1] for sp in c["spans"])
        running = any(sp[1] >= n and not sp[4] for sp in c["spans"])
        if not running and k1 - k0 < 2:
            continue
        out.append({"k0": max(0, k0), "k1": min(n, k1), "h": round(c["h"]), "n": len(c["spans"]), "running": running})
    return sorted(out, key=lambda c: c["k0"])


STITCH_S = 360       # un appareil qui reprend dans les 6 min avec la même puissance = la même utilisation


def should_stitch(prev: dict, nxt: dict, gap_s: float) -> bool:
    """Deux utilisations à la suite sont-elles le même appareil qui reprend (thermostat d'un four, d'un radiateur…) ?"""
    if not 0 <= gap_s <= STITCH_S:
        return False
    rel = abs(prev["P"] - nxt["P"]) / max(prev["P"], nxt["P"])
    if "cycles" in (prev["shape"], nxt["shape"]):
        return rel <= 0.2
    # une courte reprise (moins de 6 min) à la même puissance : le thermostat a juste coupé un peu plus longtemps
    return rel <= 0.12 and nxt["D"] <= 360


def stitch(prev_prof: list[int], gap_s: float, prof: list[int]) -> list[int]:
    return list(prev_prof) + [0] * max(0, round(gap_s / STEP)) + list(prof)


# ---------- Suivi d'un appareil désigné, de son démarrage à son arrêt
TRACK_MAX_S = 6 * 3600


def _close_to(power, m):
    return abs(m - power) <= max(250, 0.25 * power)


def track(ts, ps, t0: float, power: float, now: float, pause_max: float, max_s: float = TRACK_MAX_S,
          known: list | None = None) -> dict:
    """Suit UN appareil (démarré à t0, saut d'environ `power`) au milieu des autres.

    Les sauts de sa taille l'allument ou l'éteignent ; les autres sauts appartiennent à d'autres appareils.
    Quand un saut de même taille pourrait venir de lui ou d'un autre appareil allumé entre-temps, on donne
    le saut à celui dont la taille est la plus proche. Il est considéré éteint pour de bon quand il ne
    reprend pas pendant `pause_max` secondes.
    Sa tolérance se resserre sur la taille réelle de ses sauts dès qu'il en a fait quelques-uns, et un saut
    plus proche d'un autre appareil connu (`known` : puissances des autres appareils nommés) n'est pas à lui.
    Retourne {intervals: [(début, fin)], on, last_change, done, end}.
    """
    edges = []                                   # (t, Δ) en regroupant une montée étalée sur 2 points
    for i in range(1, len(ts)):
        if ts[i] < t0 - STEP - 1:      # on garde son propre démarrage (t0 est arrondi aux 15 s) pour lisser son pic
            continue
        d = ps[i] - ps[i - 1]
        if abs(d) < 100:
            continue
        if edges and (edges[-1][1] > 0) == (d > 0) and ts[i] - edges[-1][2] <= EDGE_MERGE * STEP:
            edges[-1] = (edges[-1][0], edges[-1][1] + d, ts[i])
        else:
            edges.append((ts[i], d, ts[i]))
    edges = _settle(edges, SETTLE_S)
    on, since, intervals, others = True, t0, [], []
    own = []                                     # tailles de ses propres sauts déjà vus

    def ref():
        return sum(own) / len(own) if len(own) >= 3 else power

    def mine(x):
        """Ce saut a-t-il la taille des siens ?"""
        if len(own) >= 3:
            mu = ref()
            sd = (sum((o - mu) ** 2 for o in own) / len(own)) ** 0.5
            ok = abs(x - mu) <= max(150, 3 * sd, 0.08 * mu)
        else:
            ok = _close_to(power, x)
        # plus proche d'un autre appareil connu : c'est sans doute lui
        return ok and not any(abs(x - k) < abs(x - ref()) and _close_to(k, x) for k in (known or []))

    start_seen = False
    resumed = False                              # l'intervalle en cours vient-il d'une reprise ?
    for t, d, _ in edges:
        if abs(d) < EDGE_W:
            continue
        if d > 0 and not start_seen and -STEP - 1 <= t - t0 <= 90 and (_close_to(power, d) or 0.3 * power <= d < power):
            # c'est son propre démarrage, déjà connu ; plus petit qu'annoncé : on l'a désigné pendant son pic de
            # démarrage (chauffage à céramique), sa vraie puissance est celle d'après le pic
            start_seen = True
            power = power if _close_to(power, d) else d
            own.append(d)
            continue
        if d > 0:
            if not on and mine(d) and (not others or abs(d - ref()) <= min(abs(d - o) for o in others) + 1):
                off_before = since
                on, since, resumed = True, t, True   # il reprend (thermostat)
                own.append(d)
            elif not on and others and any(abs(d - (ref() - o)) <= 200 for o in others):
                # il reprend pile quand un autre s'arrête : un seul saut, plus petit
                k = min(range(len(others)), key=lambda k: abs(d - (ref() - others[k])))
                others.pop(k)
                off_before = since
                on, since, resumed = True, t, True
            elif not on and any(abs(d - (ref() + x)) <= 200 for x in (known or [])):
                # il reprend en même temps qu'un autre appareil connu démarre
                x = min(known, key=lambda x: abs(d - (ref() + x)))
                others.append(x)
                off_before = since
                on, since, resumed = True, t, True
            else:
                others.append(d)             # un autre appareil démarre
            continue
        m = -d
        cand = [("moi", ref())] if on else []
        cand += [(k, o) for k, o in enumerate(others)]
        # à taille égale (à 8 % près), c'est le dernier appareil allumé qui s'arrête en premier
        best = min(cand, key=lambda c: (round(abs(c[1] - m) / (0.08 * max(power, m))), -1 if c[0] == "moi" else c[0]), default=None)
        if best is not None and best[0] == "moi" and others:
            newest = len(others) - 1
            if abs(others[newest] - m) <= max(0.08 * power, abs(power - m)):
                best = (newest, others[newest])
        if best and (mine(m) if best[0] == "moi" else _close_to(best[1], m)):
            if best[0] == "moi":
                # une « reprise » après une longue pause (10 min et plus), bien plus courte que ses chauffes
                # habituelles (8 min et plus) : c'était un autre appareil de même puissance (bouilloire pendant la
                # pause d'un lave-vaisselle), on ne la compte pas. Le thermostat d'un four, lui, reprend vite.
                lens = sorted(b - a for a, b in intervals)
                typical = lens[len(lens) // 2] if lens else 0
                if resumed and typical >= 480 and t - since < 0.4 * typical and since - off_before >= 600:
                    on, since, resumed = False, off_before, False   # il était toujours à l'arrêt
                    continue
                intervals.append((since, t))
                own.append(m)
                on, since, resumed = False, t, False
            else:
                others.pop(best[0])
        elif on and others and any(_close_to(ref() + o, m) for o in others):
            # lui et un autre s'arrêtent ensemble
            k = min(range(len(others)), key=lambda k: abs(ref() + others[k] - m))
            others.pop(k)
            intervals.append((since, t))
            on, since = False, t
        elif on and any(abs(m - (ref() - x)) <= 200 for x in (known or []) + others if ref() - x >= EDGE_W):
            # il s'arrête pile quand un autre appareil connu démarre : un seul saut, plus petit
            x = min([x for x in (known or []) + others if ref() - x >= EDGE_W], key=lambda x: abs(m - (ref() - x)))
            others.append(x)
            intervals.append((since, t))
            on, since = False, t
    # Contrôle physique : quand la maison consomme moins que (niveau d'avant + la moitié de sa puissance)
    # pendant plus d'une minute, il ne peut pas être allumé, quoi qu'aient dit les sauts.
    pre = [p for t, p in zip(ts, ps) if t0 - 90 <= t < t0] or [ps[0]]
    floor = engine_median(pre) + 0.5 * ref()
    ivs = intervals + ([(since, now)] if on else [])
    cleaned = []
    for a, b in ivs:
        cur, low_since = a, None
        for t, p in zip(ts, ps):
            if t < a or t >= b:
                continue
            if p < floor:
                low_since = low_since if low_since is not None else t
            elif low_since is not None:
                if t - low_since >= 60:
                    if low_since > cur:
                        cleaned.append((cur, low_since))
                    cur = t
                low_since = None
        if low_since is not None and b - low_since >= 60:
            if low_since > cur:
                cleaned.append((cur, low_since))
            if on and b == now:
                on, since = False, low_since       # il est en fait arrêté depuis low_since
            cur = None
        if cur is not None and b > cur:
            cleaned.append((cur, b))
    if on:
        cleaned = [iv for iv in cleaned if iv[1] != now] if cleaned and cleaned[-1][1] == now else cleaned
    intervals = [iv for iv in cleaned if iv[1] - iv[0] >= 15 and not (on and iv[1] == now)]
    done, end = False, None
    if not on and now - since > pause_max:
        done, end = True, since
    if now - t0 > max_s:
        if on:
            intervals.append((since, now))
        done, end = True, (now if on else since)
    return {"intervals": intervals + ([(since, now)] if on and not done else []), "on": on and not done,
            "last_change": since, "done": done, "end": end, "p": round(power)}


def clean_rest(rest: list[int], min_len: int = 3) -> list[int]:
    """Après avoir retiré un appareil, efface les miettes (moins de 45 s) laissées à ses démarrages et arrêts."""
    out = list(rest)
    k = 0
    while k < len(out):
        if out[k] >= OFF_W:
            j = k
            while j < len(out) and out[j] >= OFF_W:
                j += 1
            if j - k < min_len:
                for q in range(k, j):
                    out[q] = 0
            k = j
        else:
            out[k] = 0            # sous OFF_W : résidu, pas un appareil
            k += 1
    return out


def track_profile(t0: float, intervals, power: float) -> list[int]:
    """Profil (pas de STEP s) d'un appareil suivi : sa puissance pendant ses moments allumés."""
    if not intervals:
        return []
    n = max(1, round((max(b for _, b in intervals) - t0) / STEP))
    prof = [0] * n
    for a, b in intervals:
        for k in range(max(0, round((a - t0) / STEP)), min(n, round((b - t0) / STEP))):
            prof[k] = round(power)
    return prof


def shape_of(f) -> str:
    if f["cyc"] >= 6 and f["on"] < 0.85:
        return "cycles"
    if f["cv"] > 0.25:
        return "paliers"
    return "stable"


def start_level(prof: list[int]) -> float | None:
    return _start(prof)[0]


SPIKE_RATIO = 1.7    # un vrai pic de démarrage (chauffage à céramique) monte au moins à 1,7 fois sa puissance


def _start(prof: list[int], spike: bool = True) -> tuple[float | None, int, bool]:
    """Puissance propre d'un appareil : la hauteur de sa marche de départ, une fois son pic de démarrage retombé
    (chauffage à céramique : 1,6 kW quelques secondes puis 700 W), et avant que quoi que ce soit d'autre démarre
    (micro-ondes lancé 1 min après les plaques). Une seule règle, utilisée partout. None si on ne peut pas la lire."""
    n = len(prof)
    if n < 2:
        return None, 0, False
    settle = round(SETTLE_S / STEP)
    k, last_fall, other = 1, 0, False
    while k < n:
        d = prof[k] - prof[k - 1]
        if prof[k] < OFF_W:                   # l'appareil s'arrête
            break
        if d >= EDGE_W and k > 2:             # un autre appareil démarre (une montée en 2 temps au début est la sienne)
            other = k < 10
            break
        if spike and d < -max(50, 0.05 * prof[k - 1]) and k <= settle + 2:
            last_fall = k                     # son pic retombe, parfois en escalier (1 414 → 942 → 789 → 692 W)
        elif d <= -EDGE_W:                    # plus tard, une grosse baisse : autre chose s'est arrêté
            break
        k += 1
    win = prof[last_fall:k]
    # le Linky envoie parfois une valeur pile pendant la montée : on ignore le premier point s'il y en a d'autres
    if last_fall == 0 and len(win) > 2:
        win = win[1:]
    # la première marche seulement : 2 minutes au plus, jusqu'au premier vrai changement (un chargeur de 230 W
    # qui s'allume ou s'éteint n'est pas à lui)
    if win:
        ref = engine_median(win[:3])
        cut = next((i for i, v in enumerate(win) if abs(v - ref) > max(150, 0.15 * ref)), len(win))
        win = win[:min(cut, 8)] or win[:1]
    level = float(engine_median(win)) if win and engine_median(win) >= OFF_W else None
    # une baisse au début qui n'est pas un vrai pic (plaques qu'on règle, un autre appareil qui s'arrête) :
    # ce n'est pas un pic, on relit sans
    if last_fall and level and max(prof[:last_fall]) < SPIKE_RATIO * level:
        return _start(prof, spike=False)
    # si la puissance remonte aussitôt après, ce n'était pas un pic mais un appareil par à-coups (soupière, thermostat)
    if last_fall and max(prof[last_fall:last_fall + 8]) >= 0.8 * max(prof[:last_fall]):
        return _start(prof, spike=False)
    # perturbé : un pic de démarrage, ou un autre appareil qui démarre dans les 2 premières minutes
    return level, last_fall, bool(last_fall) or other


def features(prof: list[int], hour: float) -> dict:
    hi = [v for v in prof if v >= OFF_W] or [max(prof) or 1]
    p = max(1.0, quantile(hi, 0.9))
    # Puissance propre de l'appareil = son palier de démarrage (2 premières minutes). Une hausse plus tardive
    # de moins de ON_W vient d'un petit appareil allumé entre-temps (chargeur, frigo) : on ne la lui attribue pas.
    head = prof[2:10] if len(prof) > 4 else (prof[1:9] or prof[:1])
    first = [v for v in head if v >= 0.75 * max(head)] or head
    p_first = engine_median(first)
    if p_first > 0 and 0 < p - p_first < ON_W:
        p = float(p_first)
    # Début perturbé (pic d'appel de courant, ou micro-ondes lancé juste après les plaques) : on lit plutôt
    # sa marche de départ, une fois le pic retombé et avant l'autre appareil (voir start_level).
    level, k0, troubled = _start(prof)
    if troubled and level:
        p = level
    else:
        k0 = 0
    # la forme (stable, paliers, marche/arrêt) se lit après son pic de démarrage, pas dessus
    body = prof[k0:] if len(prof) - k0 >= 2 else prof
    on = [v for v in body if v >= 0.5 * p] or [p]
    mu = sum(on) / len(on)
    cv = math.sqrt(sum((v - mu) ** 2 for v in on) / len(on)) / mu
    half = 0.5 * p
    cyc = sum(1 for k in range(1, len(body)) if (body[k] >= half) != (body[k - 1] >= half))
    f = {
        "P": round(p),
        "D": len(prof) * STEP,
        "on": round(len(on) / len(body), 3),
        "cyc": cyc,
        "cv": round(cv, 3),
        "h": round(hour, 2),
        "wh": round(sum(prof) * STEP / 3600, 1),
    }
    f["shape"] = shape_of(f)
    return f


def _hdiff(a, b):
    d = abs(a - b) % 24
    return min(d, 24 - d)


def power_scale(a: dict, b: dict) -> float:
    """Écart de puissance « normal » : 8 % (jamais moins de ±120 W : le frigo ou une veille la décalent un peu),
    ou plus pour un appareil dont on a vu qu'il varie (chauffage entre 650 et 860 W) : sa propre dispersion."""
    return max(0.08 * max(a["P"], b["P"]), 120, 2 * a.get("sP", 0), 2 * b.get("sP", 0))


def dist(a: dict, b: dict) -> float:
    """Écart entre deux démarrages (ou un démarrage et un groupe). 1 ≈ « nettement différent ».
    Un groupe apprend sa propre tolérance (sP, sD) : un appareil régulier reste strict, un appareil variable non."""
    s = (abs(a["P"] - b["P"]) / power_scale(a, b)) ** 2
    s += (math.log(a["D"] / b["D"]) / max(1.2, 2 * a.get("sD", 0), 2 * b.get("sD", 0))) ** 2
    s += ((a["on"] - b["on"]) / 0.4) ** 2
    s += ((math.log1p(a["cyc"]) - math.log1p(b["cyc"])) / 1.4) ** 2
    s += 0.5 * ((a["cv"] - b["cv"]) / 0.25) ** 2
    # l'heure n'est qu'un indice : un appareil peut servir à toute heure (chauffage le matin au lieu du soir)
    s += 0.2 * (min(_hdiff(a["h"], b["h"]), 6) / 4) ** 2
    return math.sqrt(s)


def similarity(d: float) -> float:
    return math.exp(-0.5 * (d / EPS) ** 2)


def centroid(fs: list[dict], spread_from: list[dict] | None = None) -> dict | None:
    """Centre d'un groupe. Sa tolérance (sP, sD) se lit sur spread_from (ce que l'utilisateur a confirmé) :
    jamais sur des rangements automatiques, qui la feraient dériver vers un autre appareil."""
    if not fs:
        return None
    med = lambda k: sorted(f[k] for f in fs)[len(fs) // 2]
    ang = [f["h"] / 24 * 2 * math.pi for f in fs]
    h = math.atan2(sum(map(math.sin, ang)), sum(map(math.cos, ang))) / (2 * math.pi) * 24 % 24
    c = {k: med(k) for k in ("P", "D", "on", "cyc", "cv", "wh")}
    c["h"] = round(h, 2)
    c["shape"] = shape_of(c)
    # dispersion vue sur ses utilisations confirmées (écart médian), à partir de 2 : sa tolérance à lui
    sf = fs if spread_from is None else spread_from
    if len(sf) >= 2:
        c["sP"] = round(1.4826 * engine_median([abs(f["P"] - c["P"]) for f in sf]))
        lD = [math.log(max(f["D"], 1)) for f in sf]
        mD = engine_median(lD)
        c["sD"] = round(1.4826 * engine_median([abs(x - mD) for x in lD]), 3)
    return c


@dataclass
class Ev:
    id: int
    f: dict
    gid: str | None = None
    pinned: bool = False
    excluded: set = field(default_factory=set)
    probable: bool = False      # rangé par ressemblance à des exemples, sans certitude (voir probable)


PROB_WH = 15          # en dessous, ça ne pèse rien : on ne devine pas
PROB_MIN = 2          # il faut au moins 2 exemples confirmés pour reconnaître un appareil
PROB_SURE = 0.6       # probabilité à atteindre pour nommer (sinon : pas de nom plutôt qu'un faux nom)
PROB_UNKNOWN = 0.02   # le poids de « c'est un appareil que je ne connais pas »
# À quel point deux utilisations d'un même appareil peuvent différer
BW_P, BW_D, BW_ON, BW_CYC, BW_CV, BW_H = 0.15, 0.5, 0.25, 0.8, 0.2, 8
SPIKE_SEEN, SPIKE_PENALTY = 1.3, 6.0


def start_spike(prof: list[int], level: float | None = None, before: float = 0) -> float | None:
    """Le pic de démarrage : le haut de la première minute divisé par le régime de l'appareil (chauffage à céramique : 2 fois).
    None quand on ne peut pas le lire (profil recoupé, à plat : son début réel n'est plus là)."""
    if len(prof) < 4 or (level is None and len(set(prof)) <= 2):
        return None
    on = [v for v in prof if v > 150] or list(prof)
    return round((max(prof[:4]) - before) / max(level or engine_median(on), 1), 2)


def _kernel(a: dict, b: dict) -> float:
    """Ressemblance entre deux utilisations, de 0 à 1 (puissance, durée, part allumée, cycles, régularité, heure)."""
    z = (math.log(a["P"] / b["P"]) / BW_P) ** 2 + (math.log(a["D"] / b["D"]) / BW_D) ** 2 + ((a["on"] - b["on"]) / BW_ON) ** 2
    z += ((math.log1p(a["cyc"]) - math.log1p(b["cyc"])) / BW_CYC) ** 2 + ((a["cv"] - b["cv"]) / BW_CV) ** 2
    z += (_hdiff(a["h"], b["h"]) / BW_H) ** 2
    # un pic de démarrage net, ou son absence, quand on sait le lire des deux côtés : un moteur ou une céramique ne se cachent pas
    sa, sb = a.get("spike"), b.get("spike")
    if sa is not None and sb is not None and (sa >= SPIKE_SEEN) != (sb >= SPIKE_SEEN):
        z += SPIKE_PENALTY
    return math.exp(-0.5 * z)


def probabilities(f: dict, examples: dict[str, list[dict]], excluded=()) -> dict[str, float]:
    """La probabilité de chaque appareil pour cette utilisation, d'après toutes les utilisations confirmées : chaque
    exemple vote selon sa ressemblance. Plus il y a d'exemples, mieux le modèle connaît les habitudes de la maison."""
    sc = {gid: sum(_kernel(f, x) for x in ex) for gid, ex in examples.items() if gid not in excluded and len(ex) >= PROB_MIN}
    tot = sum(sc.values()) + PROB_UNKNOWN
    return {gid: v / tot for gid, v in sc.items()}


def probable(f: dict, examples: dict[str, list[dict]], excluded, cats: dict[str, str]) -> str | None:
    """L'appareil le plus probable, s'il l'est assez. Hésiter entre deux appareils de la même catégorie n'empêche pas
    de choisir : la case du bilan reste la bonne."""
    pr = probabilities(f, examples, excluded)
    if not pr:
        return None
    best = max(pr, key=pr.get)
    if pr[best] >= PROB_SURE:
        return best
    same = sum(v for g, v in pr.items() if cats.get(g, g) == cats.get(best, best))
    return best if same >= PROB_SURE else None


def assign(events: list[Ev], cents: dict[str, dict], examples: dict[str, list[dict]] | None = None,
           cats: dict[str, str] | None = None) -> int:
    """Range les démarrages non verrouillés dans le groupe le plus proche. Retourne le nombre de changements."""
    changed = 0
    for e in events:
        # confirmé par l'utilisateur, ou suivi de bout en bout : on ne le défait pas tout seul
        if e.pinned or (e.f.get("tracked") and e.gid):
            e.probable = False
            continue
        best, bd = None, None
        cand = []
        for gid, c in cents.items():
            if gid in e.excluded:
                continue
            d = dist(e.f, c)
            # « même puissance » au sens strict (sans la tolérance apprise : des plaques qui varient de ±500 W
            # ne sont pas pour autant de la même puissance que la cafetière)
            cand.append((d, gid, abs(e.f["P"] - c["P"]) / max(0.08 * max(e.f["P"], c["P"]), 120), c.get("named", True)))
            if d <= c.get("amax", ASSIGN_MAX) and (bd is None or d < bd):
                best, bd = gid, d
        # deux appareils de même puissance (micro-ondes et cafetière vers 1,3 kW) : on ne choisit que si l'un
        # ressemble nettement plus (2 fois) ; sinon, pas de nom plutôt qu'un faux nom
        if best is not None and cents[best].get("named", True) and                 any(g != best and named and pd <= 1.0 and d < 2 * bd for d, g, pd, named in cand):
            best = None
        # le modèle appris sur les réponses de l'utilisateur a le dernier mot quand il est assez sûr de lui
        e.probable = False
        if examples and e.f["wh"] >= PROB_WH:
            p = probable(e.f, examples, e.excluded, cats or {})
            if p is not None:
                if not (best == p and cents[best].get("named", True)):
                    e.probable = True
                best = p
        if best != e.gid:
            e.gid = best
            changed += 1
    return changed


def dbscan(events: list[Ev]) -> list[list[Ev]]:
    """Regroupe les démarrages encore libres (DBSCAN simple, sans dépendance)."""
    n = len(events)
    nb = [[j for j in range(n) if j != i and dist(events[i].f, events[j].f) <= EPS] for i in range(n)]
    label = [None] * n
    clusters = []
    for i in range(n):
        if label[i] is not None or len(nb[i]) + 1 < MIN_PTS:
            continue
        cid = len(clusters)
        clusters.append([])
        stack = [i]
        label[i] = cid
        while stack:
            k = stack.pop()
            clusters[cid].append(events[k])
            if len(nb[k]) + 1 < MIN_PTS:
                continue
            for j in nb[k]:
                if label[j] is None:
                    label[j] = cid
                    stack.append(j)
    return [c for c in clusters if len(c) >= MIN_PTS]


# ---------- Hypothèses
# Pour chaque appareil courant : puissance typique (W) et sa tolérance, durée typique (s) et sa tolérance,
# formes plausibles, heures habituelles et fréquence dans une maison. Ce sont des indices, pas des règles.
MEALS = [(7, 9.5), (11.5, 14), (18.5, 21.5)]
KINDS = {
    "bouilloire":     ("Bouilloire",              2100, .12, 150, .45, {"stable": 1, "paliers": .3, "cycles": .1}, None, 1.2),
    "micro_ondes":    ("Micro-ondes",             1250, .15, 150, .8,  {"stable": 1, "cycles": .9, "paliers": .3}, MEALS, 1.2),
    "four":           ("Four",                    2400, .18, 2700, .6, {"cycles": 1, "paliers": .6, "stable": .25}, [(11, 14), (17.5, 21.5)], 1),
    "chauffage_sdb":  ("Chauffage salle de bain", 1900, .2, 900, .7,   {"stable": 1, "cycles": .4, "paliers": .4}, [(5.5, 10), (18, 24)], 1),
    "lave_linge":     ("Lave-linge",              2000, .1, 900, .5,   {"paliers": 1, "stable": .6, "cycles": .4}, None, 1),
    "seche_linge":    ("Sèche-linge",             2400, .15, 4500, .5, {"cycles": 1, "paliers": .6, "stable": .3}, None, 1),
    "lave_vaisselle": ("Lave-vaisselle",          2000, .1, 1200, .5,  {"paliers": 1, "stable": .8, "cycles": .3}, [(12, 15), (19, 24)], .9),
    "plaques":        ("Plaques de cuisson",      1500, .35, 1200, .7, {"paliers": 1, "cycles": .8, "stable": .3}, MEALS, 1),
    "aspirateur":     ("Aspirateur",              1200, .2, 900, .7,   {"stable": 1, "paliers": .6, "cycles": .2}, [(8, 20)], .7),
    "fer":            ("Fer à repasser",          1800, .25, 2700, .6, {"cycles": 1, "paliers": .4, "stable": .2}, None, .5),
    "grille_pain":    ("Grille-pain",              900, .15, 180, .5,  {"stable": 1, "paliers": .3, "cycles": .3}, [(6, 10)], .7),
    "seche_cheveux":  ("Sèche-cheveux",           1600, .2, 300, .7,   {"stable": 1, "paliers": .6, "cycles": .2}, [(6, 10), (19, 24)], .8),
}
VOTE_WEIGHT = 2.5    # poids d'un groupe dont toutes les utilisations ont reçu le même « peut-être »
OTHER = 0.05   # part laissée à « un autre appareil », pour ne jamais être sûr à 100 % sans raison


def guess(c: dict, hours_hist: list[int], learned: dict | None = None, votes: dict | None = None) -> list[tuple[str, float]]:
    """Probabilité de chaque type d'appareil pour un groupe (la somme vaut moins de 1).

    `learned` : appareils que l'utilisateur a déjà nommés chez lui ({clé: centre du groupe}).
    Ils pèsent plus lourd que les connaissances générales, car ils viennent de la maison elle-même.
    """
    peak = max(range(24), key=lambda h: hours_hist[h]) if any(hours_hist) else c["h"]
    raw = {}
    for kind, (_, pc, sp, dc, sd, shapes, hrs, prior) in KINDS.items():
        s = math.exp(-0.5 * (math.log(c["P"] / pc) / sp) ** 2) * math.exp(-0.5 * (math.log(c["D"] / dc) / sd) ** 2)
        s *= shapes.get(c["shape"], .3) * prior
        s *= 0.8 if hrs is None else (1 if any(a <= peak + 0.5 <= b + 1 for a, b in hrs) else 0.5)
        raw[kind] = s
    for key, proto in (learned or {}).items():
        raw[key] = 3 * math.exp(-0.5 * (dist(c, proto) / 1.0) ** 2)
    # « Peut-être » de l'utilisateur : chaque indice pèse selon la part des utilisations du groupe qui l'ont reçu.
    for name, share in (votes or {}).items():
        raw[f"vote:{name}"] = raw.get(f"vote:{name}", 0) + VOTE_WEIGHT * share
    total = sum(raw.values()) + OTHER
    out = sorted(((k, round(v / total, 3)) for k, v in raw.items()), key=lambda x: -x[1])
    return out
