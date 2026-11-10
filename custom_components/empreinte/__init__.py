"""Empreinte : reconnaître les gros appareils à partir de la puissance de la maison."""
from __future__ import annotations

import logging
import os
import shutil

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_DIR, DOMAIN, PANEL_URL, STATIC_URL, VERSION
from .coordinator import Coordinator

PLATFORMS = ["binary_sensor", "sensor"]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coord = Coordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})["coord"] = coord
    await coord.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))

    if not hass.data[DOMAIN].get("http"):
        hass.data[DOMAIN]["http"] = True
        root = os.path.join(os.path.dirname(__file__), "frontend")
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, root, False)])
        for cmd in (ws_state, ws_day, ws_action):
            websocket_api.async_register_command(hass, cmd)

    # Le numéro de version change dès qu'un fichier de l'interface change : le navigateur recharge tout.
    front = os.path.join(os.path.dirname(__file__), "frontend")
    stamp = int(await hass.async_add_executor_job(
        lambda: max(os.path.getmtime(os.path.join(front, f)) for f in os.listdir(front))))
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_URL, webcomponent_name="empreinte-panel",
        sidebar_title="Empreinte", sidebar_icon="mdi:fingerprint",
        module_url=f"{STATIC_URL}/empreinte-panel.js?v={VERSION}-{stamp}",
        embed_iframe=True, require_admin=True)
    return True


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coord: Coordinator = hass.data[DOMAIN].pop("coord", None)
    if coord:
        await coord.async_stop()
    frontend.async_remove_panel(hass, PANEL_URL)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Désinstallation : il ne reste rien de ce qu'Empreinte a ajouté. Ses capteurs (retirés par Home Assistant avec
    l'intégration), leurs lignes dans le tableau Énergie, leurs statistiques et sa base de données."""
    ours = lambda sid: isinstance(sid, str) and sid.startswith(("sensor.empreinte_", "binary_sensor.empreinte_"))
    try:
        from homeassistant.components.energy.data import async_get_manager
        mgr = await async_get_manager(hass)
        cur = list((mgr.data or {}).get("device_consumption", []))
        keep = [d for d in cur if not ours(d.get("stat_consumption"))]
        if len(keep) != len(cur):
            await mgr.async_update({"device_consumption": keep})
    except Exception:  # le tableau Énergie n'est pas utilisé : rien à retirer
        _LOGGER.debug("Empreinte : tableau Énergie non modifié", exc_info=True)
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import list_statistic_ids
        rec = get_instance(hass)
        ids = await rec.async_add_executor_job(list_statistic_ids, hass)
        mine = [x["statistic_id"] for x in ids if ours(x.get("statistic_id"))]
        if mine:
            rec.async_clear_statistics(mine)
    except Exception:
        _LOGGER.debug("Empreinte : statistiques non effacées", exc_info=True)
    await hass.async_add_executor_job(shutil.rmtree, hass.config.path(DATA_DIR), True)


def _coord(hass) -> Coordinator:
    return hass.data[DOMAIN]["coord"]


@websocket_api.websocket_command({vol.Required("type"): "empreinte/state"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_state(hass, connection, msg):
    connection.send_result(msg["id"], await _coord(hass).async_state())


@websocket_api.websocket_command({vol.Required("type"): "empreinte/day", vol.Required("day"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_day(hass, connection, msg):
    connection.send_result(msg["id"], await _coord(hass).async_day(msg["day"]))


@websocket_api.websocket_command({
    vol.Required("type"): "empreinte/action",
    vol.Required("action"): vol.In(["rename", "move", "confirm", "label", "maybe", "unmaybe", "doubt", "cancel_pending", "track_stop", "track_duration", "track_cancel", "label_now", "label_at", "energy_add", "energy_remove", "energy_undo", "energy_redo", "cat_create", "cat_rename", "cat_delete", "cat_set", "cat_dash", "merge", "split", "validate", "delete", "undo", "shared_label", "shared_dash"]),
    vol.Optional("gid"): str, vol.Optional("name"): str, vol.Optional("ids"): [str],
    vol.Optional("target"): vol.Any(str, None), vol.Optional("src"): str, vol.Optional("dst"): str,
    vol.Optional("thr"): vol.Coerce(float), vol.Optional("maybe"): bool, vol.Optional("on"): bool, vol.Optional("cid"): vol.Any(str, None), vol.Optional("lid"): str,
    vol.Optional("at"): vol.Any(None, vol.Coerce(float)), vol.Optional("t"): vol.Coerce(float), vol.Optional("tid"): str, vol.Optional("rid"): str, vol.Optional("key"): str, vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 60)), vol.Optional("p"): vol.Any(None, vol.Coerce(float)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_action(hass, connection, msg):
    coord = _coord(hass)
    try:
        await coord.async_action(msg)
    except KeyError as err:
        connection.send_error(msg["id"], "not_found", f"Introuvable : {err.args[0] if err.args else err}")
        return
    except ValueError:
        connection.send_error(msg["id"], "too_soon", "Ajouté il y a moins de 10 minutes : Home Assistant ne l'a pas encore enregistré. Réessaie dans quelques minutes.")
        return
    connection.send_result(msg["id"], await coord.async_state())
