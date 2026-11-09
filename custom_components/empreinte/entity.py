"""Base commune des capteurs d'un appareil reconnu."""
from __future__ import annotations

import re
import unicodedata

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


def slug(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "appareil"


class ApplianceEntity(Entity):
    _attr_should_poll = False
    _suffix = ""
    _label = ""

    def __init__(self, coord, gid: str, domain: str):
        self.coord = coord
        self.gid = gid
        g = coord.groups[gid]
        self._attr_unique_id = f"{DOMAIN}_{gid}_{self._suffix or 'running'}"
        self.entity_id = f"{domain}.empreinte_{slug(g['name'])}{'_' + self._suffix if self._suffix else ''}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, gid)}, name=g["name"] or gid,
                                            manufacturer="Empreinte", model="Appareil reconnu")

    @property
    def appliance(self):
        return self.coord.groups.get(self.gid)

    @property
    def name(self):
        g = self.appliance
        return f"{g['name'] if g else self.gid} {self._label}".strip()

    @property
    def available(self):
        return self.appliance is not None
