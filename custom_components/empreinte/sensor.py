"""Puissance estimée et énergie cumulée de chaque appareil reconnu."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy, UnitOfPower

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .entity import ApplianceEntity, slug


async def async_setup_entry(hass, entry, async_add_entities):
    coord = hass.data[DOMAIN]["coord"]
    coord.add_entities["sensor"] = async_add_entities
    coord.ensure_entities()


class PowerSensor(ApplianceEntity, SensorEntity):
    _suffix = "puissance"
    _label = "puissance"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coord, gid):
        super().__init__(coord, gid, "sensor")

    @property
    def native_value(self):
        o = self.coord.live_for(self.gid)
        return round(o["power"]) if o and not o["off_since"] else 0


class EnergySensor(ApplianceEntity, SensorEntity):
    """Ajoutée à la fin de chaque utilisation (compatible tableau de bord Énergie)."""
    _suffix = "energie"
    _label = "énergie"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coord, gid):
        super().__init__(coord, gid, "sensor")

    @property
    def native_value(self):
        g = self.appliance
        return round(g.get("energy", 0), 4) if g else None



class _Cat(SensorEntity):
    """Une catégorie (Électroménager…) : ses appareils comptés ensemble."""
    _attr_should_poll = False

    def __init__(self, coord, cid, suffix, label):
        self.coord, self.cid, self._label = coord, cid, label
        c = coord._cat(cid)
        self._attr_unique_id = f"{DOMAIN}_cat_{cid}_{suffix}"
        self.entity_id = f"sensor.empreinte_categorie_{slug(c['name'])}_{suffix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "cat:" + cid)}, name=c["name"],
                                            manufacturer="Empreinte", model="Catégorie")

    _attr_has_entity_name = True          # nom affiché = nom de la catégorie (l'appareil) + « puissance » / « énergie »

    @property
    def name(self):
        return self._label

    @property
    def available(self):
        return bool(self.coord._cat(self.cid))


class CatPower(_Cat):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coord, cid):
        super().__init__(coord, cid, "puissance", "puissance")

    @property
    def native_value(self):
        return self.coord.cat_power(self.cid)


class CatEnergy(_Cat):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coord, cid):
        super().__init__(coord, cid, "energie", "énergie")

    @property
    def native_value(self):
        return round(self.coord._cat(self.cid).get("total", 0), 4)



class _Line(SensorEntity):
    """Bilan par jour : le « non identifié » (la maison moins les lignes du tableau Énergie)
    et le coût de chaque ligne."""
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL        # peut baisser un peu (un appareil compté à la fin de son utilisation)

    def __init__(self, coord, key, name, uid=None):
        self.coord = coord
        self._attr_unique_id = f"{DOMAIN}_bilan_{uid or key}"
        self.entity_id = f"sensor.empreinte_bilan_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, "bilan")}, name="Bilan par jour",
                                            manufacturer="Empreinte", model="Bilan par jour")


class RestEnergy(_Line):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    sid = None

    def __init__(self, coord):
        super().__init__(coord, "reste_energie", "Non identifié")

    @property
    def native_value(self):
        return self.coord.lines.get("rest_kwh", 0)


class RestCost(_Line):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    sid = None

    def __init__(self, coord):
        super().__init__(coord, "reste_cout", "Non identifié coût")

    @property
    def native_value(self):
        return self.coord.lines.get("rest_eur", 0)


class LineCost(_Line):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2

    def __init__(self, coord, sid, name):
        self.sid = sid
        # sensor.empreinte_bilan_cout_pc, …_electromenager : lisible dans les tableaux de bord
        super().__init__(coord, "cout_" + slug(name), f"{name} coût", uid="cout_" + slug(sid))

    @property
    def name(self):
        # suit le nom de la ligne dans le tableau Énergie (une catégorie renommée garde le même capteur)
        return f"{self.coord.lines.get('names', {}).get(self.sid, self._attr_name[:-5])} coût"

    @property
    def native_value(self):
        return self.coord.line_cost(self.sid)



class Ranking(_Line):
    """Classement du mois : état = l'appareil ou la catégorie qui a le plus consommé ; le détail en attributs."""
    _attr_state_class = None
    _attr_icon = "mdi:podium"
    sid = None

    def __init__(self, coord):
        super().__init__(coord, "classement", "Classement du mois")

    @property
    def native_value(self):
        rows = self.coord.ranking.get("lignes") or []
        return rows[0]["nom"] if rows else None

    @property
    def extra_state_attributes(self):
        return {"periode": self.coord.ranking.get("periode"), "lignes": self.coord.ranking.get("lignes", []),
                "an_eur": self.coord.ranking.get("an_eur"), "annee_kwh": self.coord.ranking.get("annee_kwh"),
                "mois_kwh": self.coord.ranking.get("mois_kwh"), "jour_kwh": self.coord.ranking.get("jour_kwh"),
                "hier_kwh": self.coord.ranking.get("hier_kwh")}



class FridgeEnergy(_Line):
    """Le frigo, estimé à partir de sa fiche (invisible sur la courbe du compteur)."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    sid = None
    fridge = True

    def __init__(self, coord):
        super().__init__(coord, "frigo_energie", "Frigo (estimé)")

    @property
    def native_value(self):
        return round(self.coord.lines.get("fridge_kwh", 0), 4)


class FridgeCost(_Line):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    sid = None
    fridge = True

    def __init__(self, coord):
        super().__init__(coord, "frigo_cout", "Frigo (estimé) coût")

    @property
    def native_value(self):
        return round(self.coord.lines.get("fridge_eur", 0), 4)


class VeilleEnergy(_Line):
    """Les veilles permanentes : le plus bas du non identifié, mesuré la nuit."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    sid = None
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "veille_energie", "Veilles permanentes")

    @property
    def native_value(self):
        return round(self.coord.lines.get("veille_kwh", 0), 4)

    @property
    def extra_state_attributes(self):
        return {"puissance_w": self.coord.lines.get("veille_w")}


class VeilleCost(_Line):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "EUR"
    _attr_suggested_display_precision = 2
    sid = None
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "veille_cout", "Veilles permanentes coût")

    @property
    def native_value(self):
        return round(self.coord.lines.get("veille_eur", 0), 4)


class ClimPower(_Line):
    """La clim en ce moment, en moyenne (son compteur n'avance que par paliers de 0,1 kWh), et ce qu'elle a consommé."""
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    sid = None
    clim = True
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "clim_puissance", "Climatisation puissance moyenne")

    @property
    def native_value(self):
        ce = self.coord.climate_entity
        st = self.coord.hass.states.get(ce) if ce else None
        return 0 if ce and (st is None or st.state == "off") else self.coord.lines.get("clim_w", 0)

    @property
    def extra_state_attributes(self):
        r = self.coord.ranking
        return {"jour_kwh": r.get("clim_jour_kwh"), "mois_kwh": r.get("clim_mois_kwh"), "prix": r.get("prix")}


class ClimEnergy(_Line):
    """La clim pour les graphiques : son compteur lissé (il n'avance que par paliers de 0,1 kWh, ce qui ferait des
    barres en dents de scie et des heures négatives pour le non identifié). Elle ne prend jamais plus que ce que la
    maison a laissé : au plus 0,1 kWh devant le vrai compteur, au plus 0,5 kWh derrière."""
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    sid = None
    clim = True
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "clim_energie", "Climatisation")

    @property
    def native_value(self):
        v = self.coord.lines.get("clim_s")
        return None if v is None else round(v, 4)


class Advice(_Line):
    """Les économies possibles (état : le total en € par an) et ce mois comparé au précédent (attributs)."""
    _attr_state_class = None
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:piggy-bank-outline"
    sid = None
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "conseils", "Économies possibles par an")

    @property
    def native_value(self):
        return self.coord.advice.get("total")

    @property
    def extra_state_attributes(self):
        a = self.coord.advice
        return {"conseils": a.get("conseils", []), "comparaison": a.get("comparaison", {})}


class AdviceTip(_Line):
    """Le n-ième conseil, du plus rentable au moins rentable : état = ce qu'il ferait gagner par an (vide s'il n'y en a
    pas autant), le détail en attributs."""
    _attr_state_class = None
    _attr_native_unit_of_measurement = "EUR"
    _attr_icon = "mdi:lightbulb-on-outline"
    sid = None
    veille = True

    def __init__(self, coord, rank):
        self.rank = rank
        super().__init__(coord, f"conseil_{rank}", f"Conseil n°{rank}")

    def _tip(self):
        tips = self.coord.advice.get("conseils", [])
        return tips[self.rank - 1] if len(tips) >= self.rank else None

    @property
    def native_value(self):
        t = self._tip()
        return t["eur_an"] if t else None

    @property
    def icon(self):
        t = self._tip()
        return t["icone"] if t else self._attr_icon

    @property
    def extra_state_attributes(self):
        t = self._tip() or {}
        return {k: t.get(k) for k in ("titre", "constat", "conseil", "base", "sur")}


class Evolution(_Line):
    """Ce mois-ci comparé au précédent, en % par jour (pour la jauge)."""
    _attr_state_class = None
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:chart-line"
    sid = None
    veille = True

    def __init__(self, coord):
        super().__init__(coord, "evolution", "Ce mois-ci comparé au précédent")

    @property
    def native_value(self):
        return (self.coord.advice.get("comparaison") or {}).get("pct")
