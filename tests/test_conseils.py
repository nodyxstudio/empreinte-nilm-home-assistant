"""Les économies possibles et ce mois comparé au précédent (tableau Énergie).

Lancer :  python tests/test_conseils.py
"""
import asyncio
import os
import sys
import time
import types

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))
from ha_stub import C, make, stub  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


class _Rec:
    async def async_add_executor_job(self, f, *a):
        return f(*a)


CHANGE = {}                                  # (sid, mois) -> kWh


def stat(hass, start, end, sid, types_, units):
    key = "avant" if end is not None else ("30j" if start.timestamp() < time.time() - 20 * 86400 else "mois")
    return {"change": CHANGE.get((sid, key), 0)}


stub("homeassistant.components.recorder", get_instance=lambda h: _Rec())
DAYS = {"sensor.clim_energie_conso": [0, 0, 1.6, 1.8], "sensor.maison": [3.3] * 30, "sensor.pc": [0.5] * 30}


def stats(hass, start, end, sids, period, units, types_):
    return {s: [{"change": v} for v in DAYS.get(s, [])] for s in sids}


stub("homeassistant.components.recorder.statistics", statistic_during_period=stat, statistics_during_period=stats)

co = make()
co.lines["veille_w"] = 40
co.measured = ["sensor.pc"]
states = {"climate.salon": types.SimpleNamespace(state="heat", attributes={"temperature": 20.0})}
co.hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: states.get(e)), async_add_executor_job=_Rec().async_add_executor_job)
now = time.time()
# le PC : 4 h par jour au repos (80 W), le reste éteint, sur 7 jours
rows = []
for d in range(7):
    t0 = now - (7 - d) * 86400
    rows += [(t0 + k * 300, 400, 80 if k < 48 else 4) for k in range(288)]
co.store.add_samples(rows)
co.rows[4999] = {"start": now - 20 * 86400 - 3600, "end": now - 20 * 86400}   # la plus ancienne utilisation connue
# le chauffage de la salle de bain : 2 fois par jour, 10 min à 700 W
co.groups["gh"] = {"id": "gh", "name": "Chauffage salle de bain", "status": "valide", "letter": "H"}
for k in range(40):
    t = now - k * 43200 - 3600                   # 20 jours de mesures
    eid = 5000 + k
    co.rows[eid] = {"start": t, "end": t + 600}
    e = C.engine.Ev(eid, {"P": 700, "D": 600, "on": 1, "cyc": 0, "cv": 0, "wh": 700 / 6, "h": 7})
    e.gid = "gh"
    co.evs.append(e)
# le lave-linge : 3 fois par semaine, 0,6 kWh
co.groups["gl"] = {"id": "gl", "name": "Lave-linge", "status": "nomme", "letter": "L"}
for k in range(9):
    t = now - k * 2.3 * 86400 - 7200
    co.rows[6000 + k] = {"start": t, "end": t + 3600}
    e = C.engine.Ev(6000 + k, {"P": 2000, "D": 3600, "on": 0.3, "cyc": 4, "cv": 0, "wh": 600, "h": 10})
    e.gid = "gl"
    co.evs.append(e)
CHANGE.update({("sensor.clim_energie_conso", "30j"): 60, ("sensor.maison", "mois"): 30, ("sensor.maison", "avant"): 100,
               ("sensor.pc", "mois"): 3, ("sensor.pc", "avant"): 15})
asyncio.run(co._async_advice([("sensor.pc", "PC")], 0.2, "sensor.maison"))
tips = {t["id"]: t for t in co.advice["conseils"]}
check("Veilles : 40 W → 70 € par an, la moitié coupée = 35 €", tips.get("veilles", {}).get("eur_an") == 35, f"({tips.get('veilles', {}).get('constat')})")
check("PC : 4 h par jour au repos, mesurées par sa prise", "4,0 h" in tips.get("pc", {}).get("constat", ""), f"({tips.get('pc', {}).get('constat')})")
check("Clim : 1 °C plus bas, en partant du réglage actuel (19 au lieu de 20)", "19 °C au lieu de 20 °C" in tips.get("clim", {}).get("conseil", ""))
check("Clim : comptée sur ses seuls jours de chauffe (1,7 kWh par jour = 51 kWh par mois)", "51 kWh" in tips.get("clim", {}).get("constat", ""), f"({tips.get('clim', {}).get('constat')})")
check("Chauffage salle de bain : 5 min de moins sur 2 utilisations par jour", abs(tips.get("chauffage", {}).get("eur_an", 0) - 2 * 365 * 700 * 300 / 3600 / 1000 * 0.2) <= 1,
      f"({tips.get('chauffage', {}).get('eur_an')} €)")
check("Lave-linge : à 30 °C, un tiers de moins sur sa vraie consommation", tips.get("lave_linge", {}).get("eur_an") == round(9 / 20.04 * 365 * 0.6 * 0.35 * 0.2),
      f"({tips.get('lave_linge', {}).get('constat')} → {tips.get('lave_linge', {}).get('eur_an')} €)")
co.groups["gv"] = {"id": "gv", "name": "Lave-vaisselle", "status": "nomme", "letter": "V"}
for k in range(6):                                       # 3 lavages de 2 phases chacun (à 40 min d'écart)
    t = now - (k // 2) * 3 * 86400 - (k % 2) * 2400 - 7200
    co.rows[7000 + k] = {"start": t, "end": t + 600}
    e = C.engine.Ev(7000 + k, {"P": 1700, "D": 600, "on": 1, "cyc": 0, "cv": 0, "wh": 280, "h": 20})
    e.gid = "gv"
    co.evs.append(e)
asyncio.run(co._async_advice([("sensor.pc", "PC")], 0.2, "sensor.maison"))
tips = {t["id"]: t for t in co.advice["conseils"]}
check("Lave-vaisselle : 2 phases du même programme = 1 lavage", "1,0 fois par semaine" in tips.get("lave_vaisselle", {}).get("constat", ""),
      f"({tips.get('lave_vaisselle', {}).get('constat')})")
# Un lave-vaisselle à 2 phases de chauffe, 1 h 41 d'un démarrage à l'autre (1 h 28 de pause)
co.evs = [e for e in co.evs if e.gid != "gv"]
for k in range(4):                                       # 2 programmes de 2 phases
    t = now - (k // 2) * 2 * 86400 - (k % 2) * 6060 - 7200
    co.rows[7100 + k] = {"start": t, "end": t + 770}
    e = C.engine.Ev(7100 + k, {"P": 1700, "D": 770, "on": 1, "cyc": 0, "cv": 0, "wh": 340, "h": 8})
    e.gid = "gv"
    co.evs.append(e)
check("Lave-vaisselle : 2 phases à 1 h 28 de pause = 1 seul programme", co._programmes([e for e in co.evs if e.gid == "gv"], 7200) == 2)
check("Lave-linge : 2 lessives à 2 h d'écart restent 2 lessives", co._programmes([e for e in co.evs if e.gid == "gl"][:2], 5400) == 2)
check("Pas de conseil pour un appareil jamais vu (four, bouilloire)", "four" not in tips and "bouilloire" not in tips)
check("Conseils du plus rentable au moins rentable", [t["eur_an"] for t in co.advice["conseils"]] == sorted((t["eur_an"] for t in co.advice["conseils"]), reverse=True))
comp = co.advice["comparaison"]
check("Comparaison par jour, même pour des mois pas finis", comp.get("jour", 0) > 0 and comp.get("avant", 0) > 0 and comp.get("pct") is not None, f"({comp.get('jour')} contre {comp.get('avant')} kWh/jour, {comp.get('pct')} %)")
check("… avec ce qui a le plus changé", comp.get("lignes") and comp["lignes"][0]["nom"] == "PC")
co.lines["veille_w"] = 5
co.measured = []
states.clear()
CHANGE.clear()
DAYS.clear()
co.evs = []
asyncio.run(co._async_advice([], 0.2, "sensor.maison"))
check("Sans mesure qui le montre : aucun conseil inventé", co.advice["conseils"] == [] and co.advice["total"] == 0)
print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
