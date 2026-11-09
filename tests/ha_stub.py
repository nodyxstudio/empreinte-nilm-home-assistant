"""Imitation de Home Assistant pour faire tourner l'intégration Empreinte dans les tests (sans HA).

Les modules de HA sont remplacés par de petites imitations ; la base SQLite est un fichier temporaire.
"""
import datetime
import os
import sys
import tempfile
import types

HERE = os.path.dirname(__file__)


def stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


TZ = datetime.timezone(datetime.timedelta(hours=2))
stub("homeassistant")
stub("homeassistant.core", Event=object, HomeAssistant=object, callback=lambda f: f)
stub("homeassistant.helpers")
stub("homeassistant.helpers.device_registry", async_get=lambda h: None, async_entries_for_config_entry=lambda *a: [])
stub("homeassistant.helpers.entity_registry", async_get=lambda h: None)
stub("homeassistant.helpers.event", async_track_state_change_event=lambda *a: None, async_track_time_interval=lambda *a: None)
stub("homeassistant.util")
stub("homeassistant.util.dt", as_local=lambda d: d.astimezone(TZ),
     utc_from_timestamp=lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc),
     get_default_time_zone=lambda: TZ, now=lambda: datetime.datetime.now(TZ))

pkg = types.ModuleType("empreinte")
pkg.__path__ = [os.path.join(HERE, "..", "custom_components", "empreinte")]
sys.modules["empreinte"] = pkg
from empreinte import coordinator as C  # noqa: E402
from empreinte import engine as E  # noqa: E402
from empreinte.store import Store  # noqa: E402


class FakeEntry:
    # les réglages d'une maison type : une clim avec son compteur, un frigo de 250 kWh/an, des lampes estimées par Powercalc
    data = {"power_entity": "sensor.puissance_maison"}
    options = {"measured": [], "climate_entity": "climate.salon", "climate_energy": "sensor.clim_energie_conso",
               "fridge_kwh_year": 250, "extra_lines": ["sensor.lampes_energie"]}
    entry_id = "test"


def make(seed=3):
    co = C.Coordinator(types.SimpleNamespace(), FakeEntry())
    co.store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    co._load()
    return co
