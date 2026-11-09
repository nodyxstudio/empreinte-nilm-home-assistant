"""Maison simulée : vérifie que l'algorithme retrouve les bons appareils dans les situations pièges.

Lancer :  python tests/test_scenarios.py
Chaque scénario simule une courbe Linky réaliste (une valeur toutes les 7 à 38 s, frigo qui tourne en
fond, petites variations), y place des appareils connus, puis compare ce que l'algorithme trouve à la vérité.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "empreinte"))
import engine as E  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

T0 = 1_800_000_000.0


def appliance(kind, start, **kw):
    """Profil (liste de (durée s, W)) d'un appareil type."""
    r = kw.get("rng") or random.Random(1)
    if kind == "bouilloire":
        return [(kw.get("dur", 180), 2000)]
    if kind == "chauffage":
        return [(kw.get("dur", 900), kw.get("p", 800))]
    if kind == "micro":
        return [(kw.get("dur", 150), 1300)]
    if kind == "micro_cycles":
        return [(30, 1300), (30, 0)] * 4
    if kind == "four":
        out = [(600, 2400)]
        for _ in range(6):
            out += [(60, 2400), (90, 0)]
        return out
    if kind == "four_longues_pauses":
        out = [(600, 2200)]
        for _ in range(5):
            out += [(80, 2200), (170, 0)]       # le thermostat coupe ~3 min : plus long que la pause tolérée
        return out
    if kind == "chauffage_ceramique":   # appel de courant : ~1,7 kW 20 s, puis ~850 W stable
        return [(20, 1700), (kw.get("dur", 900) - 20, 850)]
    if kind == "chargeur":
        return [(kw.get("dur", 3600), 230)]
    raise ValueError(kind)


def house(duration, items, seed=1, fridge=True, gaps=()):
    """items : [(kind, start_s, kwargs)]. Retourne (ts, ps, truth)."""
    rng = random.Random(seed)
    sched = []      # (début, fin, W, nom)
    truth = []
    for kind, start, kw in items:
        t = start
        segs = appliance(kind, start, **kw)
        for d, w in segs:
            if w:
                sched.append((t, t + d, w, kind))
            t += d
        if kind != "chargeur":
            truth.append({"kind": kind, "start": start, "end": t, "p": max([w for d, w in segs if d >= 60] or [w for _, w in segs])})

    def power(t):
        p = 330 + (95 if fridge and (t % 2700) < 720 else 0)
        return p + sum(w for a, b, w, _ in sched if a <= t < b)

    ts, ps, t, last = [], [], 0.0, None
    while t < duration:
        if not any(a <= t < b for a, b in gaps):
            p = power(t) + rng.gauss(0, 6)
            if last is None or abs(p - last) > 3 or rng.random() < 0.3:   # le ZLinky envoie surtout quand ça change
                ts.append(T0 + t)
                ps.append(round(p))
                last = p
        t += rng.uniform(7, 38)
    return ts, ps, truth


def detect(ts, ps):
    """Même chaîne que l'intégration : découpage, séparation, puis recollage des reprises."""
    found, _ = E.segment(ts, ps)
    evs = []
    for e in found:
        for k0, prof in E.split_session(e["prof"]):
            st = e["start"] + k0 * E.STEP
            evs.append({"start": st, "end": st + len(prof) * E.STEP, "prof": prof, "f": E.features(prof, 12)})
    evs.sort(key=lambda x: x["start"])
    kept = []
    for ev in evs:
        prev = max((k for k in kept if k["end"] <= ev["start"] + 30), key=lambda k: k["end"], default=None)
        if prev and E.should_stitch(prev["f"], ev["f"], ev["start"] - prev["end"]):
            prev["prof"] = E.stitch(prev["prof"], ev["start"] - prev["end"], ev["prof"])
            prev["end"] = prev["start"] + len(prev["prof"]) * E.STEP
            prev["f"] = E.features(prev["prof"], 12)
        else:
            kept.append(ev)
    return [{"start": k["start"] - T0, "end": k["end"] - T0, "p": k["f"]["P"], "d": k["f"]["D"]} for k in kept]


def check(name, items, expect_extra=0, **kw):
    ts, ps, truth = house(kw.pop("duration", 4 * 3600), items, **kw)
    got = detect(ts, ps)
    ok, msgs, used = True, [], set()
    for tr in truth:
        cand = [(i, g) for i, g in enumerate(got) if i not in used and g["start"] < tr["end"] and g["end"] > tr["start"]]
        cand = [c for c in cand if abs(math.log(c[1]["p"] / tr["p"])) < 0.25] or cand
        if not cand:
            ok = False
            msgs.append(f"{tr['kind']} à {tr['start'] / 60:.0f} min : PAS TROUVÉ")
            continue
        i, g = min(cand, key=lambda c: abs(c[1]["start"] - tr["start"]))
        used.add(i)
        err_p = abs(g["p"] - tr["p"]) / tr["p"]
        dur_true = tr["end"] - tr["start"]
        err_d = abs(g["d"] - dur_true) / dur_true
        bad = err_p > 0.12 or err_d > 0.35
        ok &= not bad
        msgs.append(f"{tr['kind']:13} vrai {tr['p']:5} W {dur_true / 60:5.1f} min → trouvé {g['p']:5} W {g['d'] / 60:5.1f} min"
                    f"{'  <-- ÉCART' if bad else ''}")
    extra = len(got) - len(used)
    if extra > expect_extra:
        ok = False
        msgs.append(f"{extra} utilisation(s) en trop : " + ", ".join(f"{g['p']} W {g['d'] / 60:.1f} min" for i, g in enumerate(got) if i not in used))
    print(("OK    " if ok else "ÉCHEC ") + name)
    for m in msgs:
        print("        " + m)
    return ok


SCENARIOS = [
    ("Bouilloire seule", [("bouilloire", 3600, {})]),
    ("Chauffage seul (800 W)", [("chauffage", 3600, {})]),
    ("Four avec thermostat", [("four", 3600, {})]),
    ("Four avec de longues pauses de thermostat (3 min)", [("four_longues_pauses", 3600, {})]),
    ("Bouilloire puis bouilloire 20 min plus tard (2 utilisations)", [("bouilloire", 3600, {}), ("bouilloire", 4980, {})]),
    ("Micro-ondes par à-coups", [("micro_cycles", 3600, {})]),
    ("Chargeur allumé 4 min avant le chauffage", [("chargeur", 3360, {}), ("chauffage", 3600, {})]),
    ("Chargeur qui s'arrête pendant le chauffage", [("chargeur", 0, {"dur": 4000}), ("chauffage", 3600, {})]),
    ("Chargeur qui démarre pendant le chauffage", [("chauffage", 3600, {}), ("chargeur", 3900, {})]),
    ("Four + bouilloire en même temps", [("four", 3600, {}), ("bouilloire", 4000, {})]),
    ("Chauffage + micro-ondes en même temps", [("chauffage", 3600, {}), ("micro", 3900, {})]),
    ("Bouilloire puis micro-ondes 1 min après", [("bouilloire", 3600, {}), ("micro", 3840, {})]),
    ("Chauffage de 800 W et de 1 900 W, jours différents", [("chauffage", 3600, {}), ("chauffage", 9000, {"p": 1900})]),
    ("Frigo qui démarre pendant le chauffage", [("chauffage", 2700 * 2 - 300, {})]),
    ("Chauffage qui démarre pendant que le frigo tourne", [("chauffage", 2700 * 2 + 100, {})]),
    ("Chauffage à céramique avec pic au démarrage", [("chauffage_ceramique", 3600, {})]),
    ("Rien que le frigo (aucune utilisation attendue)", []),
]


def same_group_checks():
    """Le même appareil, avec ou sans chargeur / frigo, doit rester « proche » (même groupe)."""
    def feats(items):
        ts, ps, _ = house(4 * 3600, items)
        found, _ = E.segment(ts, ps)
        return [E.features(p, 12) for e in found for _, p in E.split_session(e["prof"])]
    base = feats([("chauffage", 3600, {})])[0]
    ok = True
    for name, items in [("avec chargeur allumé avant", [("chargeur", 3360, {}), ("chauffage", 3600, {})]),
                        ("chargeur qui démarre pendant", [("chauffage", 3600, {}), ("chargeur", 3900, {})]),
                        ("frigo qui démarre pendant", [("chauffage", 2700 * 2 - 300, {})])]:
        f = max(feats(items), key=lambda x: x["D"])
        d = E.dist(base, f)
        good = d <= E.EPS
        ok &= good
        print(f"{'OK    ' if good else 'ÉCHEC '}Même groupe que le chauffage seul, {name} : écart {d:.2f} (max {E.EPS})")
    other = feats([("micro", 3600, {})])[0]
    d = E.dist(base, other)
    good = d > E.EPS * 1.5
    ok &= good
    print(f"{'OK    ' if good else 'ÉCHEC '}Le micro-ondes reste bien distinct du chauffage : écart {d:.2f}")
    return ok


def cut_checks():
    """Coupure de courant : rien n'est compté pendant le trou, et le retour du courant n'est pas un appareil."""
    ok = True
    # le chauffage tourne avant la coupure et encore après (il est resté sur marche)
    ts, ps, _ = house(4 * 3600, [("chauffage", 3600, {"dur": 1800})], gaps=[(4000, 4720)])
    got = detect(ts, ps)
    good = len(got) == 1 and abs(got[0]["start"] - 3600) <= 60 and abs(got[0]["end"] - 4000) <= 60
    ok &= good
    seen = ", ".join("%d W %.1f min" % (g["p"], g["d"] / 60) for g in got)
    print(f"{'OK    ' if good else 'ÉCHEC '}Coupure de 12 min pendant le chauffage : compté jusqu'à la coupure seulement ({seen})")
    # au retour du courant, tout se rallume d'un coup (appel de 1,3 kW pendant 2 min, puis le chauffage reste allumé)
    ts, ps, _ = house(4 * 3600, [("micro", 4720, {}), ("chauffage", 4720, {"dur": 1200})], gaps=[(3000, 4720)])
    got = detect(ts, ps)
    good = not got
    ok &= good
    seen = ", ".join("%d W" % g["p"] for g in got) or "rien"
    print(f"{'OK    ' if good else 'ÉCHEC '}Retour du courant après 29 min de coupure : rien n'est pris pour un appareil ({seen})")
    # et ce qui démarre vraiment 10 min après le retour est bien vu
    ts, ps, truth = house(4 * 3600, [("bouilloire", 5320, {})], gaps=[(3000, 4720)])
    got = detect(ts, ps)
    good = len(got) == 1 and abs(got[0]["start"] - 5320) <= 60
    ok &= good
    print(f"{'OK    ' if good else 'ÉCHEC '}Bouilloire 10 min après le retour du courant : bien vue")
    return ok


if __name__ == "__main__":
    results = []
    for sc in SCENARIOS:
        name, items, *rest = sc
        results.append(check(name, items, **(rest[0] if rest else {})))
    results.append(same_group_checks())
    results.append(cut_checks())
    print(f"\n{sum(results)}/{len(results)} vérifications réussies")
    sys.exit(0 if all(results) else 1)
