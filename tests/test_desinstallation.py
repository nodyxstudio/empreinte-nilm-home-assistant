"""Désinstaller Empreinte ne laisse rien : ses lignes du tableau Énergie, ses statistiques et sa base de données
disparaissent ; tout le reste de Home Assistant est intact.

Lancer :  python tests/test_desinstallation.py
"""
import asyncio
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(__file__))
from ha_stub import stub  # noqa: E402  (imite Home Assistant)

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


ident = lambda *a, **k: (lambda f: f)                       # décorateurs de HA : sans effet ici
vol = stub("voluptuous", Required=lambda *a, **k: a[0], Optional=lambda *a, **k: a[0], In=lambda x: x, Any=lambda *a: a,
           Coerce=lambda t: t, All=lambda *a: a, Range=lambda **k: k, Schema=lambda s: s)
ws = types.SimpleNamespace(websocket_command=ident, require_admin=lambda f: f, async_response=lambda f: f,
                           async_register_command=lambda *a: None)
stub("homeassistant.components", frontend=types.SimpleNamespace(), panel_custom=types.SimpleNamespace(), websocket_api=ws)
stub("homeassistant.components.http", StaticPathConfig=object)
stub("homeassistant.config_entries", ConfigEntry=object)

PREFS = {"device_consumption": [{"stat_consumption": "sensor.prise_pc_energie"},
                                {"stat_consumption": "sensor.empreinte_four_energie"},
                                {"stat_consumption": "sensor.empreinte_categorie_electromenager_energie"},
                                {"stat_consumption": "sensor.clim_energie"}],
         "energy_sources": [{"type": "grid", "stat_energy_from": "sensor.linky"}]}


class Mgr:
    data = PREFS

    async def async_update(self, upd):
        PREFS.update(upd)


async def _mgr(hass):
    return Mgr()


CLEARED = []


class Rec:
    async def async_add_executor_job(self, f, *a):
        return f(*a)

    def async_clear_statistics(self, ids):
        CLEARED.extend(ids)


stub("homeassistant.components.energy")
stub("homeassistant.components.energy.data", async_get_manager=_mgr)
stub("homeassistant.components.recorder", get_instance=lambda h: Rec())
STATS = ["sensor.linky", "sensor.prise_pc_energie", "sensor.empreinte_four_energie", "sensor.empreinte_bilan_reste_energie",
         "sensor.empreinte_bilan_reste_cout"]
stub("homeassistant.components.recorder.statistics", list_statistic_ids=lambda hass: [{"statistic_id": s} for s in STATS])

import importlib.util  # noqa: E402

# le fichier __init__.py de l'intégration, chargé dans le paquet imité par ha_stub
_spec = importlib.util.spec_from_file_location(
    "empreinte._init", os.path.join(os.path.dirname(__file__), "..", "custom_components", "empreinte", "__init__.py"))
empreinte = importlib.util.module_from_spec(_spec)
empreinte.__package__ = "empreinte"
_spec.loader.exec_module(empreinte)

cfg = tempfile.mkdtemp()
os.makedirs(os.path.join(cfg, "empreinte"))
open(os.path.join(cfg, "empreinte", "empreinte.db"), "w").write("x")
open(os.path.join(cfg, "configuration.yaml"), "w").write("x")


async def _job(f, *a):
    return f(*a)

hass = types.SimpleNamespace(config=types.SimpleNamespace(path=lambda *p: os.path.join(cfg, *p)), async_add_executor_job=_job)
asyncio.run(empreinte.async_remove_entry(hass, None))
left = [d["stat_consumption"] for d in PREFS["device_consumption"]]
check("Tableau Énergie : les lignes d'Empreinte sont retirées", not any(s.startswith("sensor.empreinte_") for s in left), f"({left})")
check("… et seulement elles (la prise du PC et la clim restent)", left == ["sensor.prise_pc_energie", "sensor.clim_energie"])
check("Statistiques : celles d'Empreinte sont effacées, pas les autres",
      sorted(CLEARED) == sorted(s for s in STATS if s.startswith("sensor.empreinte_")), f"({CLEARED})")
check("Sa base de données est supprimée", not os.path.exists(os.path.join(cfg, "empreinte")))
check("Le reste de la configuration est intact", os.path.exists(os.path.join(cfg, "configuration.yaml")))
asyncio.run(empreinte.async_remove_entry(hass, None))
check("Désinstaller deux fois ne plante pas", True)
print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
