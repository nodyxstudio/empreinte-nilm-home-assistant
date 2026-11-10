"""Bilan par jour : le reste de la maison et le coût de chaque ligne du tableau Énergie.

Lancer :  python tests/test_bilan.py
"""
import asyncio
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))
from ha_stub import C as _C, make, stub  # noqa: E402
dt_util = _C.dt_util

PREFS = {"energy_sources": [{"type": "grid", "stat_energy_from": "sensor.maison", "number_energy_price": 0.2}],
         "device_consumption": [{"stat_consumption": "sensor.pc", "name": "PC"},
                                {"stat_consumption": "sensor.empreinte_categorie_electromenager_energie", "name": "Électroménager"}]}
stub("homeassistant.components")
stub("homeassistant.components.energy")


async def _mgr(hass):
    return types.SimpleNamespace(data=PREFS)


stub("homeassistant.components.energy.data", async_get_manager=_mgr)
vals = {"sensor.maison": 1000.0, "sensor.pc": 10.0, "sensor.empreinte_categorie_electromenager_energie": 0.0}


async def run_job(f, *a):
    return f(*a)

co = make()
co.hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: types.SimpleNamespace(state=str(vals[e])) if e in vals else None),
                                async_add_executor_job=run_job)
ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


asyncio.run(co.async_lines())                       # première lecture : point de départ
vals.update({"sensor.maison": 1003.0, "sensor.pc": 10.5})                   # la maison +3 kWh, dont PC 0,5
asyncio.run(co.async_lines())
vals["sensor.empreinte_categorie_electromenager_energie"] = 1.2             # le four compté à la fin de son utilisation
asyncio.run(co.async_lines())
L = co.lines
check("Reste de la maison = maison − PC − électroménager", abs(L["rest_kwh"] - (3 - 0.5 - 1.2)) < 1e-6, f"({L['rest_kwh']} kWh)")
check("Coût de chaque ligne au prix du tableau Énergie", abs(co.line_cost("sensor.pc") - 0.1) < 1e-6
      and abs(co.line_cost("sensor.empreinte_categorie_electromenager_energie") - 0.24) < 1e-6)
check("Coût du reste", abs(L["rest_eur"] - 1.3 * 0.2) < 1e-6, f"({L['rest_eur']} €)")
vals["sensor.maison"] = 5.0                          # compteur remplacé / remis à zéro : ignoré
asyncio.run(co.async_lines())
check("Un compteur remis à zéro ne fausse rien", abs(L["rest_kwh"] - 1.3) < 1e-6)
co._line_adjust["sensor.empreinte_categorie_electromenager_energie"] = -1.2   # « Retirer » : ce n'était pas le four
asyncio.run(co.async_lines())
check("Un retrait d'Empreinte rend ses kWh au reste de la maison", abs(L["rest_kwh"] - 2.5) < 1e-6, f"({L['rest_kwh']} kWh)")
# Classement du mois : lu dans les statistiques de HA, du plus gros au plus petit, avec le frigo estimé et le non identifié
MONTH = {"sensor.pc": 12.0, "sensor.empreinte_categorie_electromenager_energie": 30.5,
         "sensor.empreinte_bilan_frigo_energie": 19.5, "sensor.empreinte_bilan_reste_energie": 50.0,
         "sensor.maison": 120.0}


class _Rec:
    async def async_add_executor_job(self, f, *a):
        return f(*a)


stub("homeassistant.components.recorder", get_instance=lambda h: _Rec())
stub("homeassistant.components.recorder.statistics",
     statistic_during_period=lambda hass, start, end, sid, types, units: {"change": MONTH.get(sid, 0)},
     statistics_during_period=lambda hass, start, end, sids, period, units, types: {s: [{"start": __import__("time").time() - 10 * 86400, "change": MONTH.get(s, 0)}] for s in sids})
co.ranking = {}
asyncio.run(co.async_lines())
rows = co.ranking.get("lignes", [])
check("Classement du mois, du plus gros au plus petit", [r["nom"] for r in rows] == ["Non identifié", "Électroménager", "PC", "Frigo (estimé)"],
      f"({[(r['nom'], r['kwh'], r['pct']) for r in rows]})")
check("… avec le coût et la part de chacun", rows and rows[0]["eur"] == 10.0 and rows[0]["pct"] == 59 and rows[0]["an"] == 2579)   # maison 120 kWh/mois sur un an, moins le frigo de 250 kWh
check("Le frigo (estimé) est compté à part, retiré du non identifié", "fridge_kwh" in co.lines)
fr = next((r for r in rows if r["nom"] == "Frigo (estimé)"), {})
check("Coût sur un an : le frigo à sa consommation de fiche", fr.get("an") == 250 and fr.get("an_eur") == round(250 * 0.2))
# « Ce que coûte chaque appareil » : chaque ligne connue sur l'heure, le jour, le mois, l'année ; et les programmes du jour
co.shared.runs.append({"id": "p1", "plug": "sensor.prise_linge", "start": __import__("time").time() - 600, "end": __import__("time").time(),
                       "wh": 900.0, "name": "Lave-linge", "guess": "Lave-linge", "conf": 0.9})
co.ranking = {}
asyncio.run(co.async_lines())
couts = {c["nom"]: c for c in co.ranking.get("couts", [])}
check("Coût de chaque appareil connu : heure, jour, mois, année", couts.get("PC") == {"nom": "PC", "heure": 2.4, "jour": 2.4, "mois": 2.4,
      "annee": 2.4, "kwh_mois": 12.0}, f"({couts.get('PC')})")
check("… seulement les appareils connus (pas le non identifié)", "Non identifié" not in couts and len(couts) >= 2, f"({list(couts)})")
check("Le prix de chaque programme du jour (prise partagée)", co.ranking.get("utilisations", [{}])[0].get("eur") == 0.18,
      f"({co.ranking.get('utilisations')})")
co.shared.runs.clear()
# « Classement : repartir de zéro » en cours d'heure : HA donne n'importe quoi pour un départ pas encore compilé,
# le classement additionne donc lui-même les créneaux (5 min puis heures) depuis la remise à zéro.
_S = sys.modules["homeassistant.components.recorder.statistics"]
_old = (_S.statistic_during_period, _S.statistics_during_period)
T_RESET = (int(__import__("time").time() // 3600) - 3) * 3600 + 1000        # 3 h moins 16 min 40 avant l'heure en cours
H0 = (T_RESET // 3600 + 1) * 3600


def _rows(hass, start, end, sids, period, units, types):
    t0 = start.timestamp()
    if period == "5minute":                       # de la remise à zéro à maintenant, 0,1 kWh par créneau de 5 min
        ts = [t for t in range(int(t0), int(__import__("time").time()), 300)]
        return {s: [{"start": t, "change": 0.1 if s == "sensor.pc" else 0.0} for t in ts] for s in sids}
    if t0 == H0:                                  # heures pleines compilées : 2 h, 1,2 kWh chacune
        return {s: [{"start": H0 + k * 3600, "change": 1.2 if s == "sensor.pc" else 0.0} for k in range(2)] for s in sids}
    return _old[1](hass, start, end, sids, period, units, types)


_S.statistic_during_period = lambda hass, start, end, sid, types, units: {"change": 999.0 if start.timestamp() == T_RESET else MONTH.get(sid, 0)}
_S.statistics_during_period = _rows
co.lines["ranking_since"], co.ranking = T_RESET, {}
asyncio.run(co.async_lines())
pc = next((r for r in co.ranking.get("lignes", []) if r["nom"] == "PC"), {})
n5_before = len(range(int((T_RESET + 299) // 300 * 300), int(H0), 300))
n5_after = len(range(int(H0 + 7200), int(__import__("time").time()), 300))
want = round(0.1 * (n5_before + n5_after) + 2.4, 2)
check("Repartir de zéro : le classement compte depuis ce moment-là", "depuis le" in co.ranking.get("periode", ""), f"({co.ranking.get('periode')})")
check("… avec les vrais créneaux, pas la valeur fausse de HA", pc.get("kwh") == want, f"({pc.get('kwh')} kWh, attendu {want})")
check("… et les parts lues sur ce qui a été mesuré depuis (pas sur l'ancien rythme d'un an)", pc.get("pct") == 100,
      f"({[(r['nom'], r['pct']) for r in co.ranking.get('lignes', [])]})")
_S.statistic_during_period, _S.statistics_during_period = _old
co.lines.pop("ranking_since")
co.ranking = {}
asyncio.run(co.async_lines())
# Une ligne estimée (Powercalc) : retirée du non identifié, sans être dans le tableau Énergie.
vals["sensor.lampes_energie"] = 2.0
asyncio.run(co.async_lines())                         # première lecture de la ligne estimée
before = co.lines["rest_kwh"]
vals["sensor.lampes_energie"] = 2.3   # +0,3 kWh d'éclairage et d'électronique
vals["sensor.maison"] += 0.3
asyncio.run(co.async_lines())
check("Ligne estimée (éclairage et électronique) : ses kWh sortent du non identifié", abs(co.lines["rest_kwh"] - before) < 0.02,
      f"({before} → {co.lines['rest_kwh']})")
check("… et elle a son coût et son nom pour les graphiques", "sensor.lampes_energie" in co.lines["names"])
cost = co.line_cost("sensor.lampes_energie")
hue = vals.pop("sensor.lampes_energie")           # au redémarrage de HA, Powercalc arrive après nous
asyncio.run(co.async_lines())
check("Capteur absent un moment (redémarrage) : la ligne garde son coût", co.line_cost("sensor.lampes_energie") == cost and cost > 0)
vals["sensor.lampes_energie"] = hue
before = co.lines["rest_kwh"]
_lines, co.extra_ids = co.extra_ids, []    # la ligne s'en va pour de bon : pas de bond du non identifié
asyncio.run(co.async_lines())
check("Une ligne qui s'en va ne fait pas bondir le non identifié", abs(co.lines["rest_kwh"] - before) < 1e-6
      and "sensor.lampes_energie" not in co.lines["names"])
co.extra_ids = _lines
# Une utilisation d'hier rangée aujourd'hui : ses kWh retournent à hier, sans barre négative aujourd'hui
import time  # noqa: E402
ADJ = []
_Rec.async_adjust_statistics = lambda self, sid, t, a, unit: ADJ.append((sid, t.timestamp(), a, unit))
co.hass.loop = types.SimpleNamespace(call_soon_threadsafe=lambda f, *a: f(*a))


class RestEnergy:
    hass = None
    entity_id = "sensor.empreinte_bilan_reste_energie"


class RestCost:
    hass = None
    entity_id = "sensor.empreinte_bilan_reste_cout"


ELEC = "sensor.empreinte_categorie_electromenager_energie"
co.entities = {"lines": [RestEnergy(), RestCost(), types.SimpleNamespace(sid=ELEC, entity_id="sensor.empreinte_bilan_cout_electromenager", hass=None)]}
co._energy_entity = lambda target: ELEC
yday = time.time() - 86400
co.rows = {999: {"start": yday, "end": yday + 1800}}
co._log_energy(("cat", "menager"), "Électroménager", 999, 0.5)
vals[ELEC] += 0.5
asyncio.run(co.async_lines())
op = co.lines["ops"][-1]
check("Comptée en retard : le bilan note l'instant où le non identifié l'absorbe", op["t_rest"] is not None)
op["t_rest"] -= 700
op["t_line"] -= 700
asyncio.run(co.async_lines())


def moved(sid, since):
    return sum(a for s, t, a, _ in ADJ if s == sid and t >= since)


check("… 10 min plus tard, le non identifié la rend aujourd'hui et la retire hier",
      abs(moved(RestEnergy.entity_id, time.time() - 3600) - 0.5) < 1e-4 and abs(moved(RestEnergy.entity_id, 0)) < 1e-4)
check("… et la ligne Électroménager et son coût passent à hier", abs(moved(ELEC, 0)) < 1e-4
      and abs(moved(ELEC, time.time() - 3600) + 0.5) < 1e-4 and abs(moved("sensor.empreinte_bilan_cout_electromenager", time.time() - 3600) + 0.1) < 1e-4)
# Un retrait : le non identifié reprend ses kWh à leur heure, pas maintenant
ADJ.clear()
co.energy_log[-1]["t"] -= 700
co.lines["since"][ELEC] = 0
co._undo_energy(co.energy_log[-1]["id"])
asyncio.run(co.async_lines())
co.lines["ops"][-1]["t_rest"] -= 700
asyncio.run(co.async_lines())
# (le non identifié monte de 0,5 maintenant : les statistiques l'annulent ici et le mettent hier)
check("Un retrait rend ses kWh au non identifié à leurs vraies heures", abs(moved(RestEnergy.entity_id, time.time() - 3600) + 0.5) < 1e-4
      and abs(moved(RestEnergy.entity_id, 0)) < 1e-4)
# Un retrait d'un ajout plus ancien que le bilan de cette ligne : le non identifié ne l'avait jamais perdu
co._log_energy(("cat", "menager"), "Électroménager", 999, 0.2)
co.energy_log[-1]["t"] -= 5000
co.lines["since"][ELEC] = time.time() - 2000
co._line_adjust = {}
co._undo_energy(co.energy_log[-1]["id"])
check("Retrait d'un ajout d'avant le bilan : le non identifié ne bouge pas", not co._line_adjust)
# Une utilisation déjà comptée qui rétrécit (une partie rendue à un autre appareil) : l'écart est retiré, à ses heures
ADJ.clear()
co.lines["since"][ELEC] = 0
co._shrink_energy(("cat", "menager"), "Électroménager", 999, 0.1)
check("Utilisation raccourcie : l'écart est retiré de la ligne à ses heures et rendu au non identifié",
      abs(moved(ELEC, 0) + 0.1) < 1e-4 and abs(moved(ELEC, time.time() - 3600)) < 1e-4
      and abs(co._line_adjust.get(ELEC, 0) + 0.1) < 1e-6 and co.lines["ops"][-1]["k"] == -0.1)
co._log_energy(("cat", "menager"), "Électroménager", 999, 0.3)
co._shrink_energy(("cat", "menager"), "Électroménager", 999, 0.1)
check("… et un retrait plus tard ne reprend que ce qui reste compté", abs(co.energy_log[-2]["kwh"] - 0.2) < 1e-6)
# Juste après un démarrage, le capteur n'existe pas encore : l'utilisation est quand même remise à sa vraie heure
ADJ.clear()
_real_entity, co._energy_entity = co._energy_entity, lambda target: None
co._log_energy(("cat", "menager"), "Électroménager", 999, 0.4)
co._energy_entity = _real_entity
vals[ELEC] += 0.4
asyncio.run(co.async_lines())
op = next(o for o in co.lines["ops"] if o.get("k") == 0.4)
check("Capteur pas encore prêt au démarrage : le bilan le retrouve à son passage", op["sid"] == ELEC and op["t_rest"] is not None)
op["t_rest"] -= 700
op["t_line"] -= 700
asyncio.run(co.async_lines())
check("… et ses kWh partent bien à leur vraie heure", abs(moved(RestEnergy.entity_id, time.time() - 3600) - 0.4) < 1e-4)
# Les veilles permanentes : le plus bas du non identifié de nuit, compté à part
now = C_time = time.time()
night = lambda h, wh: {"start": dt_util.now().replace(hour=h, minute=0, second=0, microsecond=0).timestamp(), "change": wh / 1000}
rows_n = []
for d in range(3):
    for h, wh in ((1, 40), (2, 34), (3, 34), (4, 36)):
        x = night(h, wh)
        rows_n.append({**x, "start": x["start"] - d * 86400})
w = asyncio.run(co._veille_w(_Rec(), lambda hass, a, b, ids, per, u, ty: {"sensor.empreinte_bilan_reste_energie": rows_n}, dt_util.now()))
check("Veilles permanentes : le plus bas des nuits", w is not None and 33 <= w <= 36, f"({w} W)")
w = asyncio.run(co._veille_w(_Rec(), lambda hass, a, b, ids, per, u, ty: {"sensor.empreinte_bilan_reste_energie": rows_n[:4]}, dt_util.now()))
check("… pas de chiffre avant 2 nuits de mesure", w is None)
before = co.lines["rest_kwh"]
co.lines["veille_w"], co.lines["t_veille"] = 36, time.time() - 3600
co.lines["t_fridge"] = time.time()
vals["sensor.maison"] += 0.1                          # la maison a consommé 0,1 kWh pendant cette heure
asyncio.run(co.async_lines())
check("… 36 W pendant 1 h : 0,036 kWh sortent du non identifié", abs(co.lines["veille_kwh"] - 0.036) < 1e-3,
      f"({co.lines['veille_kwh']:.3f})")
# Une heure où la maison a consommé moins que les estimations : elles attendent, le non identifié ne passe pas sous zéro
v0, r0 = co.lines["veille_kwh"], co.lines["rest_kwh"]
co.lines["t_veille"], co.lines["t_fridge"] = time.time() - 3600, time.time()
vals["sensor.maison"] += 0.01
asyncio.run(co.async_lines())
check("Heure trop calme pour l'estimation : le non identifié ne baisse pas", co.lines["rest_kwh"] >= r0 - 1e-6 and abs(co.lines["veille_debt"] - 0.026) < 1e-3,
      f"({co.lines['rest_kwh'] - r0:+.3f} kWh, dette {co.lines['veille_debt']:.3f})")
co.lines["t_veille"] = time.time()
vals["sensor.maison"] += 0.2
asyncio.run(co.async_lines())
check("… puis l'estimation se rattrape : le total reste juste", abs(co.lines["veille_kwh"] - v0 - 0.036) < 1e-3 and co.lines["veille_debt"] < 1e-6)
# La clim : sa puissance moyenne se lit entre deux paliers de 0,1 kWh de son compteur
L = {}
vals["sensor.clim_energie_conso"] = 1000.0
co._clim_power(L, 0)
vals["sensor.clim_energie_conso"] = 1000.1
co._clim_power(L, 1800)
check("Clim : 0,1 kWh en 30 min = 200 W", L["clim_w"] == 200, f"({L['clim_w']} W)")
co._clim_power(L, 1800 + 7200)
check("… 2 h sans palier : au plus 50 W", L["clim_w"] == 50, f"({L['clim_w']} W)")
# Clim lissée : elle avance régulièrement, sans jamais quitter le palier du vrai compteur
vals["climate.salon"] = "heat"
L = {}
vals["sensor.clim_energie_conso"] = 1000.0
co._clim_power(L, 0)
steps, out = [], False
for t in range(600, 4 * 3600 + 1, 600):                     # 150 W : un palier de 0,1 kWh toutes les 40 min
    vals["sensor.clim_energie_conso"] = 1000.0 + 0.1 * (t // 2400)
    a = L["clim_goal"]
    co._clim_power(L, t)
    if t > 2400:
        steps.append(L["clim_goal"] - a)
    out |= not (vals["sensor.clim_energie_conso"] <= L["clim_goal"] <= vals["sensor.clim_energie_conso"] + 0.1)
check("Clim lissée : chaque tranche de 10 min compte à peu près la même chose (pas de paliers)", max(steps) - min(steps) < 0.012,
      f"({[round(x * 1000) for x in steps]} Wh)")
check("… et ne sort jamais du palier du compteur", not out)
L, steps = {}, []                                          # relevé toutes les 45 min : +0,2 kWh d'un coup (266 W)
vals["sensor.clim_energie_conso"] = 2000.0
co._clim_power(L, 0)
for t in range(300, 6 * 3600 + 1, 300):
    vals["sensor.clim_energie_conso"] = 2000.0 + 0.2 * (t // 2700)
    a = L["clim_goal"]
    co._clim_power(L, t)
    if t > 2700:
        steps.append(L["clim_goal"] - a)
check("Clim relevée par paliers de 0,2 kWh : toujours régulière", max(steps) - min(steps) < 0.012, f"(de {round(min(steps) * 1000)} à {round(max(steps) * 1000)} Wh par 5 min)")
# La clim dans le bilan : elle ne prend jamais plus que ce que la maison a laissé ; son retard se rattrape ensuite
PREFS["device_consumption"].append({"stat_consumption": "sensor.clim_energie_conso", "name": "Climatisation"})
vals["climate.salon"] = "heat"
vals["sensor.clim_energie_conso"] = 3000.0
co.lines.pop("clim_v", None)
asyncio.run(co.async_lines())                                # point de départ
asyncio.run(co.async_lines())
c0, r0 = co.lines["kwh"].get("sensor.clim_energie_conso", 0), co.lines["rest_kwh"]
co.lines["clim_goal"] += 0.1                                 # le lissage voudrait 0,1 kWh de clim…
co.lines["clim_ts"] = time.time()
vals["sensor.maison"] += 0.04                                # … mais la maison n'a consommé que 0,04 kWh
asyncio.run(co.async_lines())
c1 = co.lines["kwh"]["sensor.clim_energie_conso"]
check("Clim : pas plus que ce que la maison a laissé (le non identifié ne passe pas sous zéro)",
      abs(c1 - c0 - 0.04) < 1e-4 and co.lines["rest_kwh"] >= r0 - 1e-6, f"({c1 - c0:.3f} kWh, reste {co.lines['rest_kwh'] - r0:+.3f})")
co.lines["clim_ts"] = time.time()
vals["sensor.maison"] += 0.3
asyncio.run(co.async_lines())
c2 = co.lines["kwh"]["sensor.clim_energie_conso"]
check("… puis son retard se rattrape : la clim garde tout son total", abs(c2 - c0 - 0.1) < 1e-3, f"({c2 - c0:.3f} kWh)")
co.lines["clim_goal"] += 2.0                                 # un très gros retard (maison sans mesure) : rattrapé de force
co.lines["clim_ts"] = time.time()
asyncio.run(co.async_lines())
check("… sans jamais prendre plus de 0,5 kWh de retard", co.lines["clim_goal"] - co.lines["clim_s"] <= 0.5 + 1e-6)
PREFS["device_consumption"].pop()
# Un long programme compté après coup : réparti selon sa vraie courbe (un sèche-linge chauffe surtout au début)
prof = [2500] * 240 + [200] * 480                           # 1 h à 2,5 kW, puis 2 h à 200 W (un point toutes les 15 s)
w = co._spread(36000, 36000 + 3 * 3600, 3.0, prof)
first_h = sum(a for t0, a in w if t0 < 36000 + 3600)
check("Long programme : chaque heure reçoit ce qu'il y a vraiment tiré", abs(first_h - 3.0 * 2500 / (2500 + 2 * 200)) < 0.02 and abs(sum(a for _, a in w) - 3.0) < 1e-9,
      f"(1re heure {first_h:.2f} kWh sur 3)")
check("… et sans courbe, au temps passé comme avant", abs(sum(a for t0, a in co._spread(0, 3600, 1.0) if t0 < 1800) - 0.5) < 1e-9)
# Un appareil validé : une utilisation rangée plus tard dans son groupe est comptée, une seule fois
G_ID = "gh"
co.groups[G_ID] = {"id": G_ID, "name": "Chauffage", "status": "valide", "energy_dash": True, "energy": 0.0, "letter": "H"}
HEAT = "sensor.heat"
co.lines["names"][HEAT] = "Chauffage"
co.lines["since"][HEAT] = 0
_ent = co._energy_entity
co._energy_entity = lambda target: HEAT if target == ("g", G_ID) or list(target) == ["g", G_ID] else _ent(target)
co.rows[1001] = {"start": yday, "end": yday + 600}
from ha_stub import E  # noqa: E402
heat_ev = E.Ev(1001, {"P": 700, "D": 600, "on": 1, "cyc": 0, "cv": 0, "wh": 120, "h": 12, "shape": "stable"})
heat_ev.gid = G_ID
co.evs.append(heat_ev)
co._groups_update()
first = co.groups[G_ID]["energy"]
co._groups_update()
check("Appareil validé : une utilisation rangée après coup est comptée, une seule fois", abs(first - 0.12) < 1e-6 and co.groups[G_ID]["energy"] == first, f"({first})")
co.evs.remove(heat_ev)
print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
