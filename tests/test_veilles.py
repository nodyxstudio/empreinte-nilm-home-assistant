"""« Veilles permanentes » : ce qui reste allumé la nuit, mesuré sur les nuits qui comptent.

Après un changement des prises mesurées (ou « Classement : repartir de zéro »), les nuits d'avant ne disent plus rien
de ce qui reste allumé (ce que mesurent les nouvelles prises y était encore compté) : seules les nuits suivantes
comptent, et une seule suffit.
Lancer :  python tests/test_veilles.py
"""
import asyncio
import datetime
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from ha_stub import C, TZ, make  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


now = datetime.datetime(2026, 3, 12, 10, 0, tzinfo=TZ)
change = datetime.datetime(2026, 3, 11, 20, 0, tzinfo=TZ).timestamp()      # nouvelles prises la veille au soir


def night_rows(frm):
    """7 nuits : 90 Wh par heure avant le changement (le frigo y était encore), 30 Wh après."""
    rows = []
    for d in range(7, 0, -1):
        for h in range(1, 5):
            t = (now - datetime.timedelta(days=d - 1)).replace(hour=h).timestamp()
            if t >= frm.timestamp():
                rows.append({"start": t, "change": 0.030 if t > change else 0.090})
    return {"sensor.empreinte_bilan_reste_energie": rows}


def stats(hass, frm, to, ids, period, units, types):
    return night_rows(frm)


class Rec:
    async def async_add_executor_job(self, f, *a):
        return f(*a)


co = make()
w = asyncio.run(co._veille_w(Rec(), stats, now))
check("Sans changement : la nuit la plus calme des 7 dernières", w == 90.0, f"({w} W)")
co._sub_since = change
w = asyncio.run(co._veille_w(Rec(), stats, now))
check("Après de nouvelles prises : seulement les nuits suivantes, une seule suffit", w == 30.0, f"({w} W)")
co._sub_since, co.lines["ranking_since"] = 0, change
w = asyncio.run(co._veille_w(Rec(), stats, now))
check("Pareil après « Classement : repartir de zéro »", w == 30.0, f"({w} W)")
co.lines["ranking_since"] = now.timestamp() - 3600
w = asyncio.run(co._veille_w(Rec(), stats, now))
check("Pas encore une nuit entière depuis : on garde la valeur d'avant", w is None, f"({w})")

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
