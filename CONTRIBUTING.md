# Contribuer à Empreinte

Merci ! Toute aide est la bienvenue : un retour sur un appareil mal reconnu, un nouveau type de compteur testé, une traduction ou du code.

## Signaler un problème

Ouvrez une [issue](https://github.com/nodyxstudio/empreinte-nilm-home-assistant/issues/new/choose) avec la version d'Empreinte, celle de Home Assistant, votre capteur de puissance et les lignes du journal qui mentionnent « empreinte ». Ne collez jamais de jeton, de mot de passe ou l'adresse de votre Home Assistant.

## Modifier le code

L'intégration est en Python pur, sans dépendance. L'interface est un module JavaScript sans étape de compilation.

```bash
git clone https://github.com/nodyxstudio/empreinte-nilm-home-assistant.git
cd empreinte-nilm-home-assistant
```

Les tests n'ont pas besoin de Home Assistant (il est imité par `tests/ha_stub.py`) :

```bash
python tests/test_scenarios.py
```

La liste complète est dans les [notes techniques](docs/TECHNIQUE.md#vérifier-lalgorithme). **Règle d'or : aucun score ne doit baisser.** Plusieurs tests rejouent des journées simulées et comparent à des références ; une modification qui en dégrade une n'est pas acceptée, même si elle améliore un cas particulier.

Pour voir l'interface sans Home Assistant : servez le dépôt en local (`python -m http.server`) et ouvrez `dev/mock.html?demo` (maison imaginaire générée par `dev/make_demo.py`).

## Proposer une modification

1. Une branche par sujet.
2. Les tests passent (ils tournent aussi tout seuls sur GitHub).
3. Une description claire de ce qui change pour l'utilisateur.

## Traduire le panneau

Les textes du panneau sont dans `custom_components/empreinte/frontend/app.js`. Une traduction anglaise est la plus attendue : ouvrez d'abord une discussion pour s'organiser.

## Contact

Une question qui ne rentre pas dans une issue ? Écrivez à **nodyxstudio@gmail.com**.
