"""Bouton « Classement : repartir de zéro » : « Qui consomme le plus » compte à partir de maintenant."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RankingReset(hass.data[DOMAIN]["coord"])])


class RankingReset(ButtonEntity):
    """Après un changement (nouvelles prises, nouveaux appareils…), le classement du mois repart de zéro.
    Le mois suivant, il reprend normalement depuis le 1er."""
    _attr_has_entity_name = True
    _attr_name = "Classement : repartir de zéro"
    _attr_icon = "mdi:restart"

    def __init__(self, coord):
        self.coord = coord
        self._attr_unique_id = f"{DOMAIN}_bilan_classement_zero"
        self.entity_id = "button.empreinte_classement_repartir_de_zero"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "bilan")}, name="Bilan par jour",
                                            manufacturer="Empreinte", model="Bilan par jour")

    async def async_press(self) -> None:
        await self.coord.async_action({"action": "ranking_reset"})
