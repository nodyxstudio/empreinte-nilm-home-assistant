"""Suivi d'un appareil désigné, au milieu des autres : vérifie le début, la fin et le temps allumé.

Lancer :  python tests/test_tracking.py
"""
import io
import contextlib
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "empreinte"))
import engine as E  # noqa: E402
import test_scenarios as S  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


REF_OK = 377                      # suivis réussis sur 390 (référence)

def segs(kind, rng):
    if kind == "four":
        out = [(600, 2200)]
        for _ in range(6):
            out += [(rng.uniform(60, 100), 2200), (rng.uniform(90, 200), 0)]
        return out
    if kind == "lave_vaisselle":
        return [(600, 90), (900, 2000), (2400, 90), (600, 2000), (1500, 25)]
    if kind == "lv_long":          # programme de 3 h 30 : chauffe, 2 h de lavage tiède, chauffe, séchage
        return [(900, 2000), (7200, 90), (900, 2000), (3600, 25)]
    if kind == "chauffage_ceramique":
        return [(20, 1700), (900, 850)]
    if kind == "cafe":
        return [(90, 1300)]
    if kind == "cafe_fort":           # café mesuré plus haut (avec autre chose) : 1,65 kW
        return [(70, 1650)]
    if kind == "bouilloire":
        return [(180, 2000)]
    if kind == "plaques":
        return [(rng.uniform(60, 200), rng.choice([700, 1100, 1500, 1800])) for _ in range(8)]
    if kind == "plaque_fixe":         # une plaque réglée à ~1 kW, qui ne bouge pas
        return [(1500, 1000)]
    if kind == "micro":               # micro-ondes 1,32 kW, un peu plus d'une minute
        return [(70, 1320)]
    if kind == "chargeur":
        return [(3000, 230)]
    raise ValueError(kind)


def build(items, seed):
    rng = random.Random(seed)
    sched, truth = [], {}
    for name, kind, start in items:
        t = start
        for d, w in segs(kind, rng):
            if w > 300:
                sched.append((t, t + d, w))
                truth.setdefault(name, []).append((t, t + d))
            elif w:
                sched.append((t, t + d, w))
            t += d
    noise = random.Random(seed + 1000)

    def power(t):
        return 330 + (95 if (t % 2700) < 720 else 0) + sum(w for a, b, w in sched if a <= t < b)

    ts, ps, t, last = [], [], 0.0, None
    while t < 6 * 3600:
        p = power(t) + noise.gauss(0, 6)
        if last is None or abs(p - last) > 3 or noise.random() < 0.3:
            ts.append(S.T0 + t)
            ps.append(round(p))
            last = p
        t += noise.uniform(7, 38)
    return ts, ps, truth


def run(name, items, target, power, pause_max, seed=1, known=None):
    ts, ps, truth = build(items, seed)
    ivs = truth[target]
    t0 = S.T0 + ivs[0][0]
    true_end = S.T0 + ivs[-1][1]
    res = E.track(ts, ps, t0, power, ts[-1], pause_max, known=known)
    on_true = sum(b - a for a, b in ivs)
    on_found = sum(b - a for a, b in res["intervals"])
    err_end = (res["end"] - true_end) if res["done"] else None
    err_on = abs(on_found - on_true) / on_true
    ok = res["done"] and abs(err_end) <= 180 and err_on <= 0.15
    return ok, f"fin {err_end:+.0f} s · temps allumé {on_found / 60:.1f} min (vrai {on_true / 60:.1f}, écart {err_on:.0%})" if res["done"] else "fin PAS détectée"


CASES = [
    ("Four seul (longues pauses de thermostat)", [("four", "four", 3600)], "four", 2200, 600),
    ("Four + bouilloire pendant qu'il chauffe", [("four", "four", 3600), ("b", "bouilloire", 3700)], "four", 2200, 600),
    ("Four + plaques qui changent de puissance", [("four", "four", 3600), ("p", "plaques", 3650)], "four", 2200, 600),
    ("Four + chargeur", [("four", "four", 3600), ("c", "chargeur", 3000)], "four", 2200, 600),
    ("Lave-vaisselle seul (2 chauffes)", [("lv", "lave_vaisselle", 3600)], "lv", 2000, 4500),
    ("Lave-vaisselle + bouilloire pendant une chauffe", [("lv", "lave_vaisselle", 3600), ("b", "bouilloire", 4400)], "lv", 2000, 4500),
    ("Chauffage à céramique (pic au démarrage) suivi", [("ch", "chauffage_ceramique", 3600)], "ch", 850, 600),
    ("Lave-vaisselle en pause + chauffage à céramique (pic de 1,7 kW)", [("lv", "lave_vaisselle", 3600), ("ch", "chauffage_ceramique", 5700)], "lv", 2000, 4500),
    ("Four + café (1,3 kW) pendant les pauses du thermostat", [("four", "four", 3600), ("c1", "cafe", 4300), ("c2", "cafe", 4700), ("c3", "cafe", 5100)], "four", 2200, 600, [1300]),
    ("Four + café mesuré à 1,65 kW pendant les pauses", [("four", "four", 3600), ("c1", "cafe_fort", 4300), ("c2", "cafe_fort", 4700), ("c3", "cafe_fort", 5100)], "four", 2200, 600),
    ("Lave-vaisselle + bouilloire entre les 2 chauffes", [("lv", "lave_vaisselle", 3600), ("b", "bouilloire", 6000)], "lv", 2000, 4500),
    ("Chauffage désigné pendant son pic de démarrage (1,7 kW au lieu de 850 W)", [("ch", "chauffage_ceramique", 3600)], "ch", 1700, 600),
    ("… pareil pendant que le lave-vaisselle chauffe", [("lv", "lave_vaisselle", 3000), ("ch", "chauffage_ceramique", 3700)], "ch", 1700, 600),
]

if __name__ == "__main__":
    total = ok_n = 0
    for name, items, target, power, pause, *kn in CASES:
        res = []
        for seed in range(1, 31):
            ok, msg = run(name, items, target, power, pause, seed, kn[0] if kn else None)
            res.append((ok, msg))
        n_ok = sum(o for o, _ in res)
        total += len(res)
        ok_n += n_ok
        print(f"{'OK    ' if n_ok >= 27 else 'ÉCHEC '}{name} : {n_ok}/30   ex. {res[0][1]}")
        for o, m in res:
            if not o:
                print("        échec :", m)
                break
    print(f"\n{ok_n}/{total} suivis réussis")
    sys.exit(0 if ok_n >= REF_OK else 1)          # à ne jamais dégrader
