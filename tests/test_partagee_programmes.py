"""Prise partagée lave-linge + sèche-linge : chaque sorte de programme est reconnue, et sûre (sans demander).

Courbes inventées à partir de la façon dont ces appareils fonctionnent : un lave-linge tourne d'abord sans chauffer,
chauffe l'eau (ou pas, à froid) puis alterne moteur et pauses ; un sèche-linge chauffe dès le début presque tout le
temps (résistance) ou tire ~650 W stables (pompe à chaleur), et son tambour ne s'arrête pas.

Lancer :  python tests/test_partagee_programmes.py
"""
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "empreinte"))
import shared as S  # noqa: E402

rng = random.Random(1)
def curve(segs, t0):
    pts, t = [], t0
    for d, w in segs:
        end = t + d
        while t < end:
            pts.append((t, max(0, w * (1 + rng.uniform(-0.05, 0.05)) if w else 0))); t += rng.uniform(5, 40)
        t = end
    pts.append((t, 1.5)); return pts
def wash(pre=240, heat=900, heat_w=2050, wash_cycles=14, spin=(480, 520), prewash_heat=False):
    s = [(pre, 90)]
    for _ in range(3): s += [(120, 160), (60, 4)]          # tourne sans chauffer
    if prewash_heat: s += [(300, 2000), (600, 150)]
    if heat: s += [(heat, heat_w)]
    for _ in range(wash_cycles): s += [(120, 160), (60, 4)]
    s += [(300, 90), (240, 120), (300, 90), (180, spin[0]), (240, 120), (240, spin[1])]
    return s
def wash_late_heat():   # chauffe vers le milieu (programmes éco)
    s=[(240,90)]+[(120,160),(60,4)]*15+[(1200,1900)]+[(120,160),(60,4)]*10+[(300,90),(240,500)]
    return s
def dry(cycles=9, on=420, off=90, w=2250, cool=600):
    s = [(60, 250)] + [(on, w), (off, 270)] * cycles + [(cool, 260)] + [(20, 150), (220, 2)] * 6
    return s
def dry_short(): return dry(cycles=4)
def dry_long(): return dry(cycles=16, on=360)
def dry_lowpower(): return dry(w=1400, cycles=12)
def dry_reverse():    # le tambour change de sens toutes les 2 min (courte pause)
    return [(60, 250)] + [(110, 2200), (10, 0)] * 36 + [(600, 260)]
def wash_no_pause():  # moteur à variateur : pas d'arrêt franc, lavage à froid
    return [(240, 90), (3600, 140), (300, 90), (240, 500), (300, 120), (240, 550)]
def dry_pac(): return [(300, 900), (6600, 640), (480, 260)]
def dry_pac_short(): return [(200, 850), (3000, 600), (300, 250)]
cases = {"Lave-linge": {"chaud 40°": wash(), "60° long": wash(heat=1500), "froid (sans chauffe)": wash(heat=0),
                        "chauffe tard (éco)": wash_late_heat(), "prélavage chauffé": wash(prewash_heat=True),
                        "rapide 30 min": [(120,90),(600,2000)]+[(120,160),(60,4)]*4+[(240,500)],
                        "long démarrage moteur": [(900,120)]+wash(pre=60), "moteur continu, froid": wash_no_pause()},
         "Sèche-linge": {"résistance": dry(), "court": dry_short(), "long": dry_long(), "faible puissance": dry_lowpower(),
                         "pompe à chaleur": dry_pac(), "PAC court": dry_pac_short(), "tambour qui change de sens": dry_reverse()}}
names = ["Lave-linge", "Sèche-linge"]; bad = 0
for truth, progs in cases.items():
    for label, segs in progs.items():
        runs, _ = S.find_runs(curve(segs, 1_800_000_000), 1_800_000_000 + 30 * 3600)
        if len(runs) != 1: print(f"ÉCHEC {truth:12s} {label:22s} découpé en {len(runs)}"); bad += 1; continue
        f = runs[0]["f"]; n, p = S.classify(f, names, {})
        ok = n == truth and p >= S.SURE
        bad += not ok
        print(f"{'OK   ' if ok else 'ÉCHEC'} {truth:12s} {label:22s} -> {n} {p:.2f}  (D {f['D']//60} min, chauffe {f['heat']}, pos {f['heat_pos']}, mid {f['mid']}, blocs {f['blocks']})")
print("\nTout est bon" if not bad else "\nIl y a des échecs")
sys.exit(1 if bad else 0)
