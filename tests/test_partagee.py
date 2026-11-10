"""Prise partagée : une prise connectée pour le lave-linge et le sèche-linge (jamais en même temps).

Courbes inventées, à partir de la façon dont ces appareils fonctionnent. Vérifie le découpage des programmes,
le choix de l'appareil, l'apprentissage des réponses et l'énergie de chacun.

Lancer :  python tests/test_partagee.py
"""
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "empreinte"))
import shared as S  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


rng = random.Random(5)


def curve(segs, t0, every=(8, 40)):
    """segs : [(durée en s, watts)] -> points (instant, watts) comme une prise les envoie (à chaque changement)."""
    pts, t = [], t0
    for d, w in segs:
        end = t + d
        while t < end:
            pts.append((t, max(0, w + (rng.uniform(-0.04, 0.04) * w if w else 0))))
            t += rng.uniform(*every)
        t = end
    pts.append((t, 1.5))                                  # veille
    return pts, t


def lave_linge(hot=True):
    s = [(240, 90)]                                       # remplissage, le tambour tourne
    if hot:
        s += [(900, 2050)]                                # chauffe de l'eau
    for _ in range(14):                                   # lavage : le moteur tourne et s'arrête
        s += [(120, 160), (60, 4)]
    s += [(300, 90), (240, 120), (300, 90), (180, 480), (240, 120), (240, 520)]   # rinçages et essorages
    return s


def seche_resistance():
    s = [(60, 250)]
    for _ in range(9):                                    # la résistance s'allume et s'éteint (thermostat)
        s += [(420, 2250), (90, 270)]
    s += [(600, 260)]                                     # refroidissement
    for _ in range(6):                                    # anti-froissage : quelques tours de tambour
        s += [(20, 150), (220, 2)]
    return s


def seche_pac():
    return [(300, 900), (6600, 640), (480, 260)]          # pompe à chaleur : ~650 W pendant 2 h


def day(prog_list, t0=1_800_000_000):
    pts, t = [], t0
    for segs in prog_list:
        p, t = curve(segs, t)
        pts += p
        t += 3 * 3600                                     # le suivant 3 h plus tard
    return pts, t


NAMES = ["Lave-linge", "Sèche-linge"]
plug = "sensor.prise_buanderie_puissance"

# 1. Découpage : un programme = un programme, malgré les pauses du lavage
pts, end = day([lave_linge(), seche_resistance(), lave_linge(False), seche_pac()])
done, open_run = S.find_runs(pts, end + 3600)
check("4 programmes trouvés, pauses de lavage comprises", len(done) == 4 and open_run is None, f"({len(done)})")
d0 = done[0]
check("Énergie du lavage à 60 °C juste", 600 < d0["wh"] < 800, f"({d0['wh']} Wh)")
check("Anti-froissage du sèche-linge pas compté dans sa durée utile", done[1]["f"]["D"] < 90 * 60, f"({done[1]['f']['D'] / 60:.0f} min)")

# 2. Sans aucune réponse : les connaissances générales suffisent pour ces deux appareils
got = [S.classify(r["f"], NAMES, {}) for r in done]
check("Lavage chaud : lave-linge", got[0][0] == "Lave-linge" and got[0][1] >= S.SURE, f"({got[0]})")
check("Sèche-linge à résistance", got[1][0] == "Sèche-linge" and got[1][1] >= S.SURE, f"({got[1]})")
check("Lavage à froid : lave-linge", got[2][0] == "Lave-linge" and got[2][1] >= S.SURE, f"({got[2]})")
check("Sèche-linge à pompe à chaleur", got[3][0] == "Sèche-linge" and got[3][1] >= S.SURE, f"({got[3]})")

# 3. Un appareil dont on ne connaît rien (« Déshumidificateur ») : pas de nom au hasard
n, c = S.classify(done[1]["f"], ["Lave-linge", "Déshumidificateur"], {})
check("Appareil inconnu à côté : on ne force pas le lave-linge sur un séchage", n != "Lave-linge" or c < S.SURE, f"({n}, {c})")

# 4. Tout le circuit : historique relu par morceaux, sans doublon, énergie par appareil
sp = S.SharedPlugs([{"entity": plug, "names": NAMES}])
mid = pts[len(pts) // 2][0]
sp.ingest(plug, [p for p in pts if p[0] <= mid], mid)
sp.ingest(plug, [p for p in pts if p[0] >= sp.until[plug] - 1], end + 3600)
check("Relu en deux fois : 4 programmes, pas de doublon", len(sp.runs) == 4, f"({len(sp.runs)})")
ll, sl = sp.kwh(plug, "Lave-linge"), sp.kwh(plug, "Sèche-linge")
check("Énergie séparée : lave-linge et sèche-linge", ll > 0.5 and sl > 3, f"({ll} / {sl} kWh)")
tot = sum(r["wh"] for r in sp.runs) / 1000
check("Toute l'énergie de la prise est attribuée", abs(ll + sl - tot) < 1e-6, f"({tot} kWh)")

# 5. Une correction : l'énergie change d'appareil, et ce qui ressemble suit
rid = sp.runs[2]["id"]
sp.label(rid, "Sèche-linge")
check("« Non, c'est le sèche-linge » : l'énergie passe de l'un à l'autre", abs(sp.kwh(plug, "Lave-linge") - (ll - sp.runs[2]["wh"] / 1000)) < 1e-6)
sp.label(rid, "Lave-linge")
check("… et revient si on se reprend", abs(sp.kwh(plug, "Lave-linge") - ll) < 1e-6)
try:
    sp.label(rid, "Four")
    check("Un nom qui n'est pas sur la prise est refusé", False)
except KeyError:
    check("Un nom qui n'est pas sur la prise est refusé", True)

# 6. Apprendre : deux appareils inconnus du programme, nommés une fois chacun par l'utilisateur
A, B = "Machine A", "Machine B"
sp2 = S.SharedPlugs([{"entity": plug, "names": [A, B]}])
pts2, end2 = day([lave_linge(), seche_pac(), lave_linge(), seche_pac(), lave_linge()])
sp2.ingest(plug, pts2, end2 + 3600)
check("Noms inconnus : rien n'est attribué au hasard", all(r["name"] is None for r in sp2.runs))
sp2.label(sp2.runs[0]["id"], A)
sp2.label(sp2.runs[1]["id"], B)
check("Après une réponse pour chacun : les autres programmes sont reconnus",
      [r["name"] for r in sp2.runs] == [A, B, A, B, A], f"({[r['name'] for r in sp2.runs]})")

# 7. En direct : le programme en cours
sp3 = S.SharedPlugs([{"entity": plug, "names": NAMES}])
p3, _ = curve(seche_resistance(), 1_900_000_000)
now = 1_900_000_000 + 40 * 60
sp3.ingest(plug, [p for p in p3 if p[0] <= now], now)
o = sp3.open.get(plug)
check("Sèche-linge en marche depuis 40 min : reconnu en direct", o and o["guess"] == "Sèche-linge", f"({o and o['guess']})")
check("… mais pas encore compté (à la fin seulement)", not sp3.runs and sp3.kwh(plug, "Sèche-linge") == 0)
now = 1_900_000_000 + 5 * 60
sp4 = S.SharedPlugs([{"entity": plug, "names": NAMES}])
sp4.ingest(plug, [p for p in p3 if p[0] <= now], now)
check("Au bout de 5 min : trop tôt pour dire lequel", sp4.open.get(plug) and sp4.open[plug]["guess"] is None)

# 8. Une seule prise, un seul appareil : tout lui revient
sp5 = S.SharedPlugs([{"entity": plug, "names": ["Lave-linge"]}])
sp5.ingest(plug, pts, end + 3600)
check("Un seul appareil sur la prise : tout lui est attribué", all(r["name"] == "Lave-linge" for r in sp5.runs))

# 9. Dans Empreinte : l'historique de la prise relu dans Home Assistant, les réponses du panneau, ce qu'il affiche
import asyncio  # noqa: E402
import types  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from ha_stub import C, FakeEntry, make, stub  # noqa: E402

HIST = {"pts": pts}
St = lambda t, p: types.SimpleNamespace(state=str(p), last_updated=C.dt_util.utc_from_timestamp(t))


class _Rec:
    async def async_add_executor_job(self, f, *a):
        return f(*a)


def _changes(hass, start, end, entity_id, include_start_time_state):
    t0 = start.timestamp()
    sts = [St(t, p) for t, p in HIST["pts"] if t >= t0]
    sts.insert(0, types.SimpleNamespace(state="unavailable", last_updated=start))    # une coupure : ignorée
    return {entity_id: sts}


stub("homeassistant.components", recorder=None)
stub("homeassistant.components.recorder", get_instance=lambda h: _Rec(), history=types.SimpleNamespace(state_changes_during_period=_changes))
FakeEntry.options = {**FakeEntry.options, "shared_plugs": [{"entity": plug, "names": NAMES}]}
co = make()
co.hass = types.SimpleNamespace(async_add_executor_job=lambda f, *a: _Rec().async_add_executor_job(f, *a))
real_time = C.time.time
C.time.time = lambda: end + 3600
try:
    asyncio.run(co.async_shared())
    asyncio.run(co.async_shared())                       # relu une deuxième fois : rien en double
finally:
    C.time.time = real_time
check("Empreinte relit la prise dans l'historique de HA : 4 programmes", len(co.shared.runs) == 4, f"({len(co.shared.runs)})")
co2 = C.Coordinator(types.SimpleNamespace(), FakeEntry())        # un redémarrage : même base, nouveau coordinateur
co2.store = co.store
co2._load()
check("Après un redémarrage, tout est retrouvé", len(co2.shared.runs) == 4 and co2.shared.kwh(plug, "Sèche-linge") == co.shared.kwh(plug, "Sèche-linge"))
rid = co.shared.runs[0]["id"]
co._action({"action": "shared_label", "rid": rid, "name": "Sèche-linge"})
check("Réponse du panneau : enregistrée", next(r for r in co.shared.runs if r["id"] == rid)["name"] == "Sèche-linge")
co._action({"action": "shared_dash", "key": co.shared.key(plug, "Lave-linge"), "on": True})
pay = co._shared_payload()[0]
check("Le panneau reçoit la prise, ses appareils et ses programmes",
      [a["name"] for a in pay["apps"]] == NAMES and len(pay["runs"]) == 4 and pay["apps"][0]["dash"], f"({[a['name'] for a in pay['apps']]})")
try:
    co._action({"action": "shared_dash", "key": "sensor.autre|Four", "on": True})
    check("Tableau Énergie : un appareil inconnu est refusé", False)
except KeyError:
    check("Tableau Énergie : un appareil inconnu est refusé", True)
check("Pas d'annulation mélangée avec les appareils de la courbe", not co.undo)

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
