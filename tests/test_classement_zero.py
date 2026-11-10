"""« Classement : repartir de zéro » : le classement du mois compte à partir de ce moment-là, et s'en souvient.

Lancer :  python tests/test_classement_zero.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from ha_stub import C, FakeEntry  # noqa: E402
import ha_stub  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


co = ha_stub.make()
co.ranking = {"t": 1.0, "lignes": [{"nom": "PC", "kwh": 12.0}]}
real = C.time.time
C.time.time = lambda: 1_800_000_000.0
co._action({"action": "ranking_reset"})
C.time.time = real
check("Le classement repart de maintenant", co.lines.get("ranking_since") == 1_800_000_000.0)
check("… et sera recalculé tout de suite", not co.ranking)
co2 = C.Coordinator(co.hass, FakeEntry())
co2.store = co.store
co2._load()
check("Après un redémarrage, il s'en souvient", co2.lines.get("ranking_since") == 1_800_000_000.0)
check("Rien d'autre n'a bougé (pas dans la pile d'annulation)", not co.undo)

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
