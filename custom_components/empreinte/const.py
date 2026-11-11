"""Constantes d'Empreinte."""

DOMAIN = "empreinte"
VERSION = "1.2.0"
CONF_POWER_ENTITY = "power_entity"        # la puissance de toute la maison (W) : le seul réglage obligatoire
# Réglages facultatifs (modifiables à tout moment : Paramètres → Appareils et services → Empreinte → Configurer)
CONF_MEASURED = "measured"                # prises connectées qui mesurent déjà un appareil (retirées de l'analyse)
CONF_CLIMATE = "climate_entity"           # la clim / pompe à chaleur (son thermostat)
CONF_CLIMATE_ENERGY = "climate_energy"    # … et son compteur d'énergie (kWh), s'il existe
CONF_FRIDGE_KWH = "fridge_kwh_year"       # le frigo : sa consommation annuelle (étiquette énergie), 0 = pas estimé
CONF_SHARED = "shared_plugs"             # prises partagées : [{"entity": prise, "names": [appareils branchés dessus]}]
CONF_EXTRA_LINES = "extra_lines"          # autres compteurs d'énergie estimés (Powercalc…) à retirer du « non identifié »

DATA_DIR = "empreinte"            # dans /config, donc inclus dans les sauvegardes HA
DB_FILE = "empreinte.db"
STATIC_URL = "/empreinte_static"
PANEL_URL = "empreinte"

BACKFILL_DAYS = 10                # historique HA relu au premier démarrage
SAMPLES_KEEP_DAYS = 120           # puissance brute gardée dans notre base
EVENTS_SENT_DAYS = 30             # démarrages envoyés à l'interface
PROCESS_EVERY_S = 60
# Le frigo est invisible sur la courbe (vagues de 20 à 30 W) : s'il est réglé, il est estimé à partir de son étiquette
# énergie (kWh par an). Il apparaît dans le bilan, retiré du « non identifié » ; jamais dans le tableau Énergie officiel,
# qui ne contient que du mesuré ou du reconnu.

# Quand une clim tourne, son compresseur fait monter et descendre la courbe toutes les quelques minutes : ce n'est
# pas un appareil à reconnaître (elle a sa propre ligne). En dessous de cette puissance, on ne l'enregistre pas.
CLIM_MAX_W = 450
CLIM_LAG_KWH = 0.5   # retard maximal de la clim dans le bilan (elle attend la place laissée par la maison)
# Phases de chauffe d'un même programme : une phase qui démarre moins de tant de secondes après la fin de la précédente
PROGRAMME_GAP_S = {"Lave-vaisselle": 7200}

# Ce qui reste allumé jour et nuit, mesuré au plus bas des nuits : retiré du « non identifié », qui ne garde que
# ce qui varie (télé, chargeurs, parties d'appareils trop faibles pour être vues).
VEILLE_NAME = "Veilles permanentes"

# Le compteur de la clim avance souvent par paliers (0,1 kWh) : sa puissance se lit sur le temps entre deux paliers.

# Conseils par appareil reconnu : (nom de l'appareil, id, icône, part économisée, le geste, l'hypothèse du calcul).
# Parts prudentes, d'après les ordres de grandeur de l'ADEME.
APPLIANCE_TIPS = [
    ("Lave-linge", "lave_linge", "mdi:washing-machine", 0.35,
     "Laver à 30 °C au lieu de 40 °C (le linge du quotidien sort aussi propre) : chauffer l'eau, c'est l'essentiel de sa consommation.",
     "un tiers de moins en lavant à 30 °C"),
    ("Lave-vaisselle", "lave_vaisselle", "mdi:dishwasher", 0.3,
     "Le programme Éco : plus long, mais il chauffe moins l'eau. Et toujours bien plein.", "30 % de moins en programme Éco"),
    ("Four", "four", "mdi:stove", 0.1,
     "L'éteindre 5 à 10 min avant la fin (il reste chaud), et éviter d'ouvrir la porte pendant la cuisson.", "10 % de moins par cuisson"),
    ("Bouilloire", "bouilloire", "mdi:kettle", 0.25,
     "Ne faire chauffer que l'eau dont tu as besoin (une tasse = 25 cl).", "un quart d'eau chauffée en moins"),
]
TIP_IDS = ["veilles", "pc", "clim", "chauffage"] + [t[1] for t in APPLIANCE_TIPS]

# Les autres lignes estimées (réglage « extra_lines », par exemple des compteurs Powercalc : lampes, box, alarme) sont
# montrées dans le bilan et le classement, retirées du « non identifié », mais jamais ajoutées au tableau Énergie officiel.

AUTO_NAME_SCORE = 0.85            # au-dessus, le programme nomme le groupe tout seul
AUTO_NAME_MARGIN = 0.25           # ... s'il n'hésite pas avec un autre appareil
AUTO_NAME_MIN = 8                 # ... s'il l'a vu au moins 8 fois
AUTO_NAME_DAYS = 3                # ... sur au moins 3 jours différents
AUTO_NAME_COHERENCE = 0.8         # ... et si les utilisations du groupe se ressemblent vraiment
HYP_MIN_N = 5                     # une hypothèse n'est proposée qu'après 5 utilisations…
HYP_MIN_DAYS = 2                  # … sur au moins 2 jours…
HYP_MIN_SCORE = 0.6               # … et si elle est nette (60 %)
