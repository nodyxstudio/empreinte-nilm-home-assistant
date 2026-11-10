# Nouveautés

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
