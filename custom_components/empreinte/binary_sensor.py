"""« En marche » pour chaque appareil reconnu."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .const import DOMAIN
from .entity import ApplianceEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coord = hass.data[DOMAIN]["coord"]
    coord.add_entities["binary_sensor"] = async_add_entities
    coord.ensure_entities()


class RunningSensor(ApplianceEntity, BinarySensorEntity):
    _label = "en marche"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coord, gid):
        super().__init__(coord, gid, "binary_sensor")

    @property
    def is_on(self):
        return self.coord.live_for(self.gid) is not None
