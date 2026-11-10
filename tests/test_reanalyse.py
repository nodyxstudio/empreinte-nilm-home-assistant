"""Une analyse refaite (par exemple après l'ajout d'une prise mesurée) ne compte rien deux fois.

Toutes les utilisations sont alors réenregistrées sous de nouveaux numéros. Ce qui avait déjà été envoyé au tableau
Énergie (catégories, appareils validés, journal) doit suivre chaque utilisation sous son nouveau numéro : sinon
tout l'historique serait ajouté une seconde fois aux compteurs.

Lancer :  python tests/test_reanalyse.py
"""
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from ha_stub import C, FakeEntry, make  # noqa: E402
import test_tracking as TT  # noqa: E402
import test_scenarios as S  # noqa: E402

ok_all = True
real_time = C.time.time


def dishwasher():
    """Le scénario de test_coordinator : un lave-vaisselle suivi, un thé et un café pendant ses pauses."""
    ts3, ps3, _ = TT.build([("lv", "lv_long", 3600), ("the", "bouilloire", 5400), ("cafe", "cafe", 7200),
                            ("the2", "bouilloire", 12000)], 7)
    co3 = make()
    cut = S.T0 + 3600 + 240
    C.time.time = lambda: cut
    co3.store.add_samples([(t, p, 0) for t, p in zip(ts3, ps3) if t <= cut])
    co3.processed_until = ts3[0]
    co3._process()
    part = next(x for x in co3.running_parts if abs(x["p"] - 2000) < 400)
    co3._action({"action": "label_now", "name": "Lave-vaisselle", "at": part["start"], "p": part["p"]})
    for minute in range(5, 300):
        now = S.T0 + 3600 + minute * 60
        C.time.time = lambda now=now: now
        co3.store.add_samples([(t, p, 0) for t, p in zip(ts3, ps3) if now - 60 < t <= now])
        co3._process()
    return co3


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


co = dishwasher()
end = C.time.time()
co.hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None))
co._action({"action": "cat_dash", "cid": "menager", "on": True})
men = co._cat("menager")
men["since"] = 1.0                          # tout l'historique compte
co._cats_update()
total = men["total"]
gid = next(g for g, x in co.groups.items() if x.get("name") == "Lave-vaisselle")
co._action({"action": "validate", "gid": gid})       # « Valider » : l'appareil a aussi son propre compteur
co._cats_update()
check("Avant : l'électroménager a compté le lave-vaisselle", total > 0.5, f"({round(total, 3)} kWh)")
old_ids = {e.id for e in co.evs}
log_before = len([x for x in co.energy_log if not x["removed"]])

# Une prise mesurée est ajoutée : tout est ré-analysé au redémarrage d'Empreinte
co.store.set_meta(C.RESEGMENT_KEY, False)
co2 = C.Coordinator(co.hass, FakeEntry())
co2.hass = co.hass
co2.store = co.store
co2._load()
for _ in range(20):
    co2._process()
co2._cats_update()
men2 = co2._cat("menager")
new_ids = {e.id for e in co2.evs}
check("L'analyse a bien été refaite (nouveaux numéros)", new_ids and not (new_ids & old_ids), f"({len(new_ids)} utilisations)")
check("Le lave-vaisselle est retrouvé", any(co2.groups.get(e.gid, {}).get("name") == "Lave-vaisselle" for e in co2.evs))
check("L'électroménager n'a rien compté une seconde fois", abs(men2["total"] - total) < 1e-3,
      f"({round(total, 3)} → {round(men2['total'], 3)} kWh)")
counted = set(men2.get("counted", {}))
check("… ce qui était compté suit les nouveaux numéros", counted <= {str(i) for i in new_ids | old_ids}
      and any(k in counted for k in map(str, new_ids)))
log_after = [x for x in co2.energy_log if not x["removed"]]
check("Le journal du tableau Énergie n'a pas grossi", len(log_after) == log_before, f"({log_before} → {len(log_after)})")
check("… et il pointe vers les nouvelles utilisations", all(x["eid"] in new_ids or x.get("checked") for x in log_after))
check("Plus rien en attente de renumérotation", not co2._renum and not co2.store.get_meta("renumber", []))

# Plus tard (plus de 10 min) : les corrections automatiques ne retirent rien de ce qui était juste
C.time.time = lambda: end + 900
co2._auto_corrections()
check("Aucune correction automatique déclenchée à tort", not any(x.get("auto") for x in co2.energy_log))
C.time.time = real_time

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
