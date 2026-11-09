"""Mise en place : le capteur de puissance de la maison, puis des réglages facultatifs modifiables à tout moment."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_CLIMATE, CONF_CLIMATE_ENERGY, CONF_EXTRA_LINES, CONF_FRIDGE_KWH, CONF_MEASURED,
                    CONF_POWER_ENTITY, DOMAIN)

# Mots qui trahissent le capteur de toute la maison (compteur Linky, Shelly EM, Lixee, Tic…)
_HOUSE_WORDS = ("linky", "zlinky", "lixee", "papp", "sinsts", "reseau", "réseau", "grid", "compteur", "maison", "house",
                "home", "total", "teleinfo", "shelly")


def _guess_power(hass) -> str | None:
    """Le capteur de puissance qui ressemble le plus à celui de toute la maison (pour pré-remplir le formulaire)."""
    best, score = None, 0
    for st in hass.states.async_all("sensor"):
        if st.attributes.get("device_class") not in _POWER_CLASSES or st.entity_id.startswith("sensor.empreinte_"):
            continue
        name = f"{st.entity_id} {st.name}".lower()
        s = 1 + sum(w in name for w in _HOUSE_WORDS)
        if s > score:
            best, score = st.entity_id, s
    return best


# W (puissance active) ou VA (puissance apparente, celle que donnent souvent le Linky et le ZLinky)
_POWER_CLASSES = ("power", "apparent_power")


def _power_selector(multiple=False, classes=_POWER_CLASSES):
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class=list(classes), multiple=multiple))


def _options_schema(cur: dict) -> vol.Schema:
    def opt(key, default):
        return vol.Optional(key, description={"suggested_value": cur.get(key, default)})
    return vol.Schema({
        opt(CONF_MEASURED, []): _power_selector(multiple=True, classes=("power",)),
        opt(CONF_CLIMATE, None): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
        opt(CONF_CLIMATE_ENERGY, None): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="energy")),
        opt(CONF_FRIDGE_KWH, 0): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=1000, step=1, unit_of_measurement="kWh/an",
                                          mode=selector.NumberSelectorMode.BOX)),
        opt(CONF_EXTRA_LINES, []): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)),
    })


class EmpreinteFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            if self.hass.states.get(user_input[CONF_POWER_ENTITY]) is None:
                errors["base"] = "not_found"
            else:
                return self.async_create_entry(title="Empreinte", data=user_input)
        guess = _guess_power(self.hass)
        schema = vol.Schema({
            vol.Required(CONF_POWER_ENTITY, description={"suggested_value": guess} if guess else None): _power_selector(),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return EmpreinteOptions()


class EmpreinteOptions(config_entries.OptionsFlow):
    """Réglages facultatifs : prises qui mesurent déjà un appareil, clim, frigo, autres compteurs estimés."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        coord = self.hass.data.get(DOMAIN, {}).get("coord")
        cur = dict(self.config_entry.options)
        cur.setdefault(CONF_MEASURED, coord.measured if coord else [])
        return self.async_show_form(step_id="init", data_schema=_options_schema(cur))
