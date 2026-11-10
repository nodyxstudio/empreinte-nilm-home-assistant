# Tutoriel : de l'installation au tableau Énergie

Ce guide vous amène pas à pas d'un Home Assistant avec un compteur Linky à un tableau Énergie qui montre la consommation du four, de la bouilloire ou du lave-vaisselle. Comptez 10 minutes de mise en place, puis quelques jours où Empreinte apprend.

[← Retour au README](../README.md)

---

## 1. Avoir la puissance de la maison dans Home Assistant

Empreinte n'a besoin que d'**un capteur** : la puissance de toute la maison, en watts, mise à jour toutes les quelques secondes.

| Votre installation | Le capteur à utiliser |
|---|---|
| Linky + **ZLinky** (Zigbee, via ZHA ou Zigbee2MQTT) | « Puissance apparente » / `apparent_power` (VA) |
| Linky + module **TIC** Wi-Fi (Lixee, Tasmota, ESPHome…) | `PAPP` ou `SINSTS` |
| **Shelly EM / 3EM** sur l'arrivée générale | la puissance totale (W) |
| Pince ampèremétrique Zigbee ou Wi-Fi | sa puissance (W) |

Pour vérifier : **Outils de développement → États**, cherchez le capteur. Sa valeur doit changer quand vous allumez la bouilloire (+2 000 W environ).

> 💡 Plus le capteur est rapide, mieux c'est : une valeur toutes les 1 à 10 secondes est idéale. Au-delà de 30 secondes, les appareils courts (micro-ondes, bouilloire) seront souvent manqués.

## 2. Installer Empreinte

**Avec HACS** :

1. Ouvrez HACS et cherchez **Empreinte**, puis **Télécharger**.
2. **Redémarrez Home Assistant** (Paramètres → Système → ⏻ → Redémarrer).

S'il n'apparaît pas encore dans la recherche : ⋮ en haut à droite → **Dépôts personnalisés**, collez `https://github.com/nodyxstudio/empreinte-nilm-home-assistant`, choisissez **Intégration**, puis **Ajouter**, et recommencez l'étape 1.

**Sans HACS** : téléchargez `empreinte.zip` dans la [dernière version](https://github.com/nodyxstudio/empreinte-nilm-home-assistant/releases/latest), dézippez-le dans `config/custom_components/empreinte/` (avec le module *File editor* ou *Samba*), puis redémarrez.

## 3. Le mettre en route

1. **Paramètres → Appareils et services → + Ajouter une intégration**, cherchez **Empreinte**.
2. Choisissez le capteur de puissance de la maison. Empreinte propose celui qui y ressemble le plus.
3. Validez. **Empreinte** apparaît dans le menu de gauche.

Au premier démarrage, il relit les **10 derniers jours** de l'historique de Home Assistant. Si votre capteur était déjà là, il a souvent des découvertes à montrer tout de suite.

## 4. Les réglages qui améliorent tout (facultatif)

**Paramètres → Appareils et services → Empreinte → Configurer → Réglages** :

- **Prises qui mesurent déjà un appareil** : si le PC ou la télé ont une prise connectée, ajoutez-la. Leur puissance est retirée avant l'analyse.
- **Clim / pompe à chaleur** : indispensable si vous en avez une. Son compresseur fait varier la courbe toute la journée ; sans ce réglage, Empreinte verrait des appareils qui n'existent pas.
- **Son compteur d'énergie** : si votre clim donne ses kWh (MELCloud, Daikin, Airzone…), elle a sa ligne dans le bilan.
- **Consommation du frigo** : le chiffre en kWh/an de son étiquette énergie (par exemple 250). Il est estimé et retiré du « non identifié ».
- **Autres compteurs estimés** : des compteurs Powercalc ou autres pour les lampes, la box, l'alarme…

Et dans **Configurer → Prises partagées** : une prise connectée qui alimente plusieurs appareils jamais allumés ensemble (lave-linge et sèche-linge, par exemple). Écrivez leurs noms séparés par une virgule. Empreinte reconnaît lequel a tourné à chaque programme, et chacun a ses propres capteurs. Les programmes apparaissent dans la page **Appareils**, avec un bouton par appareil pour corriger.

## 5. Les premiers jours : nommer ce qu'il découvre

Ouvrez **Empreinte** dans le menu. La première fois, un court accueil résume tout (le bouton **?** le rouvre). Il y a six pages :

| Page | Ce qu'on y fait |
|---|---|
| **Aperçu** | La courbe du jour, ce qui tourne en ce moment, le journal |
| **Découvertes** | Les groupes trouvés tout seuls, à nommer |
| **Habitudes** | À quelle heure et quel jour tourne chaque appareil |
| **Carte** | Toutes les utilisations par puissance et durée |
| **Appareils** | Les appareils nommés, leur certitude, les catégories |
| **Rapport** | La semaine, avec le coût |

**La méthode la plus rapide** : quand vous allumez un appareil, ouvrez l'Aperçu et cliquez sur **« En ce moment, c'est… »**. Choisissez le nom : Empreinte le suit jusqu'à son arrêt, même s'il y en a d'autres en même temps. Faites-le **2 ou 3 fois par appareil** (bouilloire, four, plaques, lave-vaisselle, lave-linge…). Votre appareil n'est pas dans les suggestions ? Écrivez son nom : ça crée un nouvel appareil.

Vous pouvez aussi nommer après coup : dans **Découvertes** ou dans le journal, cliquez sur une utilisation → **C'est…**

> 💡 Une erreur ? « Non, c'est… » corrige et apprend. Dans **Appareils**, un appareil se renomme, se supprime, se fusionne ou se sépare, et **Annuler** revient sur la dernière action. Empreinte refait ses calculs sur le passé à chaque réponse.

## 6. Les catégories : un total juste même quand il hésite

Le four et les plaques, ou le lave-linge et le lave-vaisselle, se ressemblent beaucoup. Dans **Appareils → Catégories**, la catégorie **Électroménager** les regroupe : même s'il les confond entre eux, leur total reste juste. Vous pouvez créer les vôtres (« Petite cuisine » pour la bouilloire, la cafetière et le micro-ondes, par exemple).

## 7. Le tableau Énergie

Quand un appareil est assez sûr, sa carte dans **Appareils** propose **Valider**. Empreinte crée alors :

- `sensor.empreinte_<nom>_puissance` (W) et `sensor.empreinte_<nom>_energie` (kWh) ;
- `binary_sensor.empreinte_<nom>`, allumé quand l'appareil tourne (pratique pour une automatisation : « le lave-linge a fini »).

et l'ajoute lui-même au tableau Énergie (**Consommation individuelle**). Pour une catégorie, le bouton est **Ajouter au tableau Énergie**. Rien n'y est jamais ajouté sans votre clic, et **Retirer du tableau Énergie** l'enlève.

## 8. Le bilan par jour et les économies

Si le tableau Énergie connaît la consommation du réseau et le prix du kWh, Empreinte publie aussi :

| Capteur | Contenu |
|---|---|
| `sensor.empreinte_bilan_reste_energie` | Le « non identifié » : la maison moins toutes les lignes connues |
| `sensor.empreinte_bilan_cout_<ligne>` | Le coût de chaque ligne du tableau Énergie |
| `sensor.empreinte_bilan_veille_energie` | Les veilles permanentes, mesurées au plus bas des nuits |
| `sensor.empreinte_bilan_frigo_energie` | Le frigo estimé (si réglé) |
| `sensor.empreinte_bilan_classement` | Le classement du mois, ligne par ligne (en attributs) |
| `sensor.empreinte_bilan_conseils` | Les économies possibles en € par an, avec le détail en attributs |
| `sensor.empreinte_bilan_conseil_1` à `_8` | Chaque conseil, du plus rentable au moins rentable |
| `sensor.empreinte_bilan_evolution` | Ce mois-ci comparé au précédent, en % par jour |

Un exemple de tableau de bord prêt à coller : [exemples/tableau_energie.yaml](exemples/tableau_energie.yaml).

## 9. Et ensuite ?

Laissez-le tourner. Plus vous répondez au début, plus vite il devient autonome ; au bout d'une semaine ou deux, la plupart des gros appareils sont reconnus seuls.

Un souci ? Regardez la [FAQ](FAQ.md), puis ouvrez une [issue](https://github.com/nodyxstudio/empreinte-nilm-home-assistant/issues), ou écrivez à **nodyxstudio@gmail.com**.
