"""Une ligne du bilan qui n'existe plus (estimation retirée, frigo plus estimé) : son capteur disparaît de HA.

Rien d'actuel n'est retiré, ni le bouton du classement, ni les capteurs des appareils.
Lancer :  python tests/test_lignes_orphelines.py
"""
import os
import sys
import types

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from ha_stub import C, make  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


class Reg:
    def __init__(self, ents):
        self.ents = {e.entity_id: e for e in ents}

    def async_remove(self, eid):
        self.ents.pop(eid)


E = lambda eid, uid: types.SimpleNamespace(entity_id=eid, unique_id=uid)  # noqa: E731
reg = Reg([E("sensor.empreinte_bilan_reste_energie", "empreinte_bilan_reste_energie"),
           E("sensor.empreinte_bilan_cout_pc", "empreinte_bilan_cout_pc"),
           E("sensor.empreinte_bilan_cout_freebox_estime", "empreinte_bilan_cout_freebox_estime"),     # retirée des réglages
           E("sensor.empreinte_bilan_frigo_energie", "empreinte_bilan_frigo_energie"),                 # frigo plus estimé
           E("button.empreinte_classement_repartir_de_zero", "empreinte_bilan_classement_zero"),
           E("sensor.empreinte_four_energie", "empreinte_g1_energie")])
C.er.async_get = lambda h: reg
C.er.async_entries_for_config_entry = lambda r, eid: list(r.ents.values())
co = make()
cur = [types.SimpleNamespace(unique_id="empreinte_bilan_reste_energie", hass=None),
       types.SimpleNamespace(unique_id="empreinte_bilan_cout_pc", hass=None)]
co.entities["lines"] = cur
co._drop_old_lines()
check("Rien n'est retiré tant que les lignes actuelles ne sont pas toutes dans HA", len(reg.ents) == 6)
for e in cur:
    e.hass = object()
co._drop_old_lines()
check("Les lignes qui n'existent plus disparaissent", "sensor.empreinte_bilan_cout_freebox_estime" not in reg.ents
      and "sensor.empreinte_bilan_frigo_energie" not in reg.ents, f"({sorted(reg.ents)})")
check("Les lignes actuelles restent", {"sensor.empreinte_bilan_reste_energie", "sensor.empreinte_bilan_cout_pc"} <= set(reg.ents))
check("Le bouton du classement et les capteurs des appareils restent",
      {"button.empreinte_classement_repartir_de_zero", "sensor.empreinte_four_energie"} <= set(reg.ents))

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
