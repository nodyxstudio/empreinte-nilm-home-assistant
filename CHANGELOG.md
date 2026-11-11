# Nouveautés

## 1.2.0

- **Ce que coûte chaque appareil** : le classement du mois (`sensor.empreinte_bilan_classement`) donne aussi, pour chaque appareil connu, son coût sur la dernière heure, aujourd'hui, ce mois-ci et cette année (attribut `couts`), et le prix de chaque programme du jour sur une prise partagée (attribut `utilisations`).
- Bouton **Classement : repartir de zéro** (appareil « Bilan par jour ») : après un changement (nouvelles prises, nouveaux appareils…), « Qui consomme le plus » compte à partir de ce moment-là, parts comprises. Le mois suivant, il reprend depuis le 1er.
- Prises partagées : un lave-linge s'arrête sans cesse (le tambour tourne, s'arrête, repart), un sèche-linge tourne sans arrêt. Cet indice rend la reconnaissance sûre pour tous les programmes testés : lavages chauds, froids, éco, rapides, avec prélavage ; sèche-linge à résistance ou à pompe à chaleur.
- Prises partagées : les noms anglais (« Washing machine », « Washer », « Dryer », « Dishwasher ») sont reconnus comme les noms français.
- **Veilles permanentes** : après un changement des prises mesurées, elles se remesurent sur les nuits suivantes (une seule suffit), au lieu de garder les nuits d'avant.
- Les capteurs des lignes du bilan qui n'existent plus (estimation retirée des réglages, ligne sortie du tableau Énergie) disparaissent de Home Assistant au lieu de rester « indisponibles ». Leur historique reste.
- Corrections : classement remis à zéro en cours d'heure (valeurs fausses de HA tant que l'heure n'est pas compilée) ; une année ne peut plus être plus petite que son mois.

## 1.1.1

- Correction : après un changement des prises mesurées (ou de la façon de mesurer), Empreinte refait toute l'analyse. Les utilisations déjà envoyées au tableau Énergie (catégories, appareils validés) étaient alors comptées une seconde fois. Elles gardent maintenant ce qui avait été compté.

## 1.1.0

- **Prises partagées** : une prise connectée pour plusieurs appareils jamais allumés ensemble (lave-linge et sèche-linge…). Empreinte découpe chaque programme sur la courbe de la prise et reconnaît lequel a tourné ; chacun a ses capteurs (en marche, puissance, énergie) et sa ligne possible dans le tableau Énergie. Un clic corrige, et il apprend.
- Le bouton **Configurer** ouvre un menu : Réglages, ou Prises partagées.
- Le panneau ne lit plus la base de données sur le fil principal de Home Assistant : son actualisation ne peut plus ralentir HA (surtout sur carte SD).
- Installation : chercher Empreinte directement dans HACS.

## 1.0.1

- Icône d'Empreinte dans Home Assistant (dossier `brand`), pour la liste par défaut de HACS.

## 1.0.0

Première version publique.

- Reconnaissance des gros appareils (plus de 350 W) à partir de la seule puissance de la maison : découvertes, nommage, suivi de bout en bout, modèle qui apprend des réponses.
- Accueil au premier lancement, et à tout moment avec le bouton ?.
- Appareils personnalisés : n'importe quel nom crée un nouvel appareil, en plus des suggestions (four, sèche-linge…).
- Catégories (Électroménager…) : un total juste même quand deux appareils se ressemblent.
- Capteurs de puissance, d'énergie et « en marche » pour chaque appareil validé, ajoutés au tableau Énergie d'un clic.
- Bilan par jour : « non identifié », veilles permanentes mesurées la nuit, frigo estimé, coût de chaque ligne, sans heure négative.
- Économies possibles en € par an, calculées sur la vraie consommation, et mois comparé au précédent.
- Réglages dans Home Assistant : prises déjà mesurées, clim / pompe à chaleur et son compteur, frigo, compteurs estimés.
- Tout se corrige : renommer, supprimer, fusionner, séparer, retirer du tableau Énergie, annuler.
- Désinstallation propre : supprimer l'intégration efface ses capteurs, ses lignes du tableau Énergie, leurs statistiques et sa base de données.
- 100 % local : aucun compte, aucun serveur, aucune donnée envoyée.
