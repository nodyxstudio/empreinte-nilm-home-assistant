"""Mise en place : le capteur de puissance de la maison, puis des réglages facultatifs modifiables à tout moment."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (CONF_CLIMATE, CONF_CLIMATE_ENERGY, CONF_EXTRA_LINES, CONF_FRIDGE_KWH, CONF_MEASURED,
                    CONF_POWER_ENTITY, CONF_SHARED, DOMAIN)

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


def split_names(text: str) -> list[str]:
    """« Lave-linge, Sèche-linge » -> ["Lave-linge", "Sèche-linge"] (sans doublon, sans vide)."""
    out = []
    for n in (text or "").replace(";", ",").replace("/", ",").replace(" ou ", ",").replace(" et ", ",").split(","):
        n = n.strip()[:40]
        if n and n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out


class EmpreinteOptions(config_entries.OptionsFlow):
    """Réglages facultatifs : prises qui mesurent déjà un appareil, clim, frigo, autres compteurs estimés,
    et prises partagées entre plusieurs appareils."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["reglages", "prises"])

    async def async_step_reglages(self, user_input=None):
        if user_input is not None:
            # les prises partagées se règlent ailleurs : on les garde telles quelles
            keep = {CONF_SHARED: self.config_entry.options[CONF_SHARED]} if CONF_SHARED in self.config_entry.options else {}
            return self.async_create_entry(data={**keep, **user_input})
        coord = self.hass.data.get(DOMAIN, {}).get("coord")
        cur = dict(self.config_entry.options)
        cur.setdefault(CONF_MEASURED, [m for m in coord.measured if m not in {p["entity"] for p in coord.shared_cfg}] if coord else [])
        return self.async_show_form(step_id="reglages", data_schema=_options_schema(cur))

    async def async_step_prises(self, user_input=None):
        plugs = [dict(p) for p in self.config_entry.options.get(CONF_SHARED, [])]
        errors = {}
        if user_input is not None:
            plug, names = user_input.get("shared_plug"), split_names(user_input.get("shared_names", ""))
            gone = set(user_input.get("shared_remove") or [])
            if plug and len(names) < 1:
                errors["shared_names"] = "names_needed"
            elif names and not plug:
                errors["shared_plug"] = "plug_needed"
            elif plug and plug == self.config_entry.data.get(CONF_POWER_ENTITY):
                errors["shared_plug"] = "house_sensor"
            else:
                plugs = [p for p in plugs if p["entity"] not in gone and p["entity"] != plug]
                if plug:
                    plugs.append({"entity": plug, "names": names})
                return self.async_create_entry(data={**self.config_entry.options, CONF_SHARED: plugs})
        label = lambda p: f"{self.hass.states.get(p['entity']).name if self.hass.states.get(p['entity']) else p['entity']} : {', '.join(p['names'])}"
        schema = {
            vol.Optional("shared_plug"): _power_selector(classes=("power",)),
            vol.Optional("shared_names"): selector.TextSelector(),
        }
        if plugs:
            schema[vol.Optional("shared_remove")] = selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": p["entity"], "label": label(p)} for p in plugs], multiple=True))
        current = "\n".join(f"- {label(p)}" for p in plugs) or "—"
        return self.async_show_form(step_id="prises", data_schema=vol.Schema(schema), errors=errors,
                                    description_placeholders={"current": current})
