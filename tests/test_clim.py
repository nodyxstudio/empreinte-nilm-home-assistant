"""La clim en chauffage, et un faux « chauffage salle de bain » à éviter.

Lancer :  python tests/test_clim.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))
from ha_stub import C, make  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


HEATER = {"g": {"P": 602}}
# Une cafetière (courbe inventée) : une valeur reçue en pleine montée ressemble au chauffage
CAFE = [(7, 335), (14, 332), (28, 340), (35, 339), (49, 339), (56, 336), (63, 335), (70, 334), (84, 345), (91, 333), (98, 332), (105, 334),
        (112, 338), (119, 339), (126, 905), (133, 1605), (140, 1604), (147, 1614), (161, 690), (168, 1635), (175, 349), (182, 339), (189, 346), (196, 348),
        (203, 343), (210, 339), (224, 338), (238, 344), (245, 346), (252, 339), (259, 344), (266, 340), (273, 349), (280, 345), (287, 351), (301, 348),
        (308, 342), (315, 348), (322, 348), (329, 440), (336, 1652), (343, 1648), (350, 1657), (357, 1654), (371, 1656), (378, 349), (392, 348), (399, 344)]
live = C.Live()
on = 0
for t, p in CAFE:
    live.feed(t, p, HEATER)
    on += "g" in live.running
check("Cafetière : le chauffage ne s'allume jamais", on == 0, f"({on} valeurs)")

# Un chauffage : une valeur en pleine montée, son pic de démarrage, puis son régime
live = C.Live()
for t in range(0, 300, 10):
    live.feed(t, 480, HEATER)
for t, p in ((300, 1150), (308, 1890), (323, 1280), (338, 1240), (353, 1235), (368, 1240), (383, 1238), (398, 1240)):
    live.feed(t, p, HEATER)
check("Un chauffage, avec son pic de démarrage : il s'allume bien", "g" in live.running and not live.running["g"]["off_since"])

live = C.Live()
for t in range(0, 300, 10):
    live.feed(t, 340, HEATER)
live.feed(300, 960, HEATER)
check("… pas tout de suite : il attend d'être sûr", "g" not in live.running)
for t in range(310, 700, 10):
    live.feed(t, 960, HEATER)
check("Le chauffage s'allume bien", "g" in live.running and not live.running["g"]["off_since"])
live.feed(700, 340, HEATER)
for t in range(710, 900, 10):
    live.feed(t, 340, HEATER)
check("… et s'éteint", "g" not in live.running)

live = C.Live()
for t in range(0, 300, 10):
    live.feed(t, 340, HEATER)
live.feed(300, 960, HEATER)
for t in range(310, 700, 10):
    live.feed(t, 960, HEATER)
live.feed(700, 700, HEATER)      # descente en deux fois : aucun saut ne ressemble à son arrêt
live.feed(710, 450, HEATER)
for t in range(720, 900, 10):
    live.feed(t, 340, HEATER)
check("Arrêt en deux fois : il ne reste pas allumé 6 heures", "g" not in live.running)

live = C.Live()
for t in range(0, 300, 10):
    live.feed(t, 340, HEATER)
live.feed(300, 2340, HEATER)     # le four tourne déjà
for t in range(310, 600, 10):
    live.feed(t, 2340, HEATER)
live.feed(600, 2960, HEATER)     # le chauffage s'y ajoute
for t in range(610, 900, 10):
    live.feed(t, 2960, HEATER)
live.feed(900, 960, HEATER)      # le four s'arrête, le chauffage continue
for t in range(910, 1200, 10):
    live.feed(t, 960, HEATER)
check("Le four s'arrête, le chauffage continue : il reste allumé", "g" in live.running and not live.running["g"]["off_since"])


# Une soirée (courbe inventée) : deux appareils, plus un saut de compresseur vers le bas au milieu
SOIR = [683, 685, 686, 687, 695, 684, 694, 694, 696, 698, 780, 680, 432, 424, 434, 435, 425, 434, 432, 436, 428, 436, 434, 429,
        425, 665, 574, 578, 570, 579, 565, 573, 581, 570, 581, 568, 585, 574, 585, 581, 584, 571, 569, 576, 570, 582, 581, 1835,
        1730, 1350, 1130, 1105, 1124, 1115, 1120, 1105, 1196, 1228, 1221, 1212, 1189, 1212, 1254, 1192, 1192, 1341, 1333, 1320, 1342, 1335, 1342, 1322,
        1322, 1339, 1333, 1335, 1323, 1326, 1334, 1337, 1331]
parts = C.engine.split_session(SOIR, C.CLIM_MAX_W)
check("Deux appareils pendant que la clim tourne : ils sont séparés", len(parts) == 2, f"({[(k, max(p)) for k, p in parts]})")
check("… sans clim, la règle stricte d'avant ne change pas", len(C.engine.split_session(SOIR)) == 1)
# 700 W sans pic = les plaques, la même puissance avec un pic de démarrage = le chauffage
F = lambda P, D, s: {"P": P, "D": D, "on": 1.0, "cyc": 0, "cv": 0.0, "wh": P * D / 3600, "h": 19, "spike": s}
EX = {"chauffage": [F(692, 900, 2.0), F(701, 810, 2.3), F(764, 855, 2.0)], "plaques": [F(780, 900, 1.0), F(700, 1200, None), F(800, 1755, 1.0)]}
CATS = {"chauffage": "h", "plaques": "e"}
check("Même puissance, sans pic de démarrage : les plaques", C.engine.probable(F(700, 1215, 1.0), EX, set(), CATS) == "plaques")
check("Même puissance, avec un pic : le chauffage", C.engine.probable(F(700, 900, 2.4), EX, set(), CATS) == "chauffage")
check("Pic calculé sur la courbe d'origine d'un morceau recoupé", C.engine.start_spike(SOIR[47:51], 543, SOIR[46]) == round((SOIR[47] - SOIR[46]) / 543, 2), f"({C.engine.start_spike(SOIR[47:51], 543, SOIR[46])})")


def replay(clim, coffee=True):
    now = C.time.time()
    t0, rows = now - 4 * 3600, []
    for k in range(int(3.5 * 3600 / 10)):
        t = t0 + k * 10
        p = 320 + (370 if (k // 60) % 2 else 0)        # le compresseur : +370 VA, 10 min sur 20
        if coffee and 7000 <= k * 10 < 7200:
            p += 1300
        rows.append((t, p, 0))
    co = make()
    co.processed_until = t0
    co.store.add_samples(rows)
    co._clim_until = now + 600 if clim else 0.0
    co._process()
    return co


co = replay(True)
ps = sorted(e.f["P"] for e in co.evs)
check("Clim allumée : ses montées ne sont plus des utilisations", not [p for p in ps if p <= C.CLIM_MAX_W], f"({ps})")
check("… mais la cafetière par-dessus est bien vue", any(p >= 1000 for p in ps), f"({ps})")
co = replay(False, coffee=False)
check("Clim éteinte : la même courbe reste enregistrée (rien n'est caché)", len(co.evs) > 0, f"({len(co.evs)})")
print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
