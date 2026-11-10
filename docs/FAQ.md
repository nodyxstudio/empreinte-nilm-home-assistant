# Questions fréquentes

[← Retour au README](../README.md)

### Est-ce que mes données partent quelque part ?

Non. Empreinte n'a ni compte, ni serveur, ni statistiques d'utilisation. Tout est calculé dans votre Home Assistant et rangé dans `config/empreinte/empreinte.db`, inclus dans vos sauvegardes. Le code est ouvert : vous pouvez le vérifier.

### Faut-il un Linky ?

Non : il faut un capteur de **la puissance de toute la maison**, mis à jour souvent. Le Linky avec un module ZLinky ou TIC est le cas le plus courant en France, mais un Shelly EM, un Shelly 3EM, une pince ampèremétrique ou un compteur Modbus marchent aussi.

### Mon capteur est en VA, pas en W. C'est grave ?

Non. Le Linky donne souvent la puissance apparente (VA). Empreinte s'en sert très bien pour reconnaître les appareils ; seuls les kWh affichés sont un peu au-dessus des vrais (quelques %).

### Le capteur n'apparaît pas dans la liste à l'installation

La liste montre les capteurs de **puissance** (classe d'appareil `power`, en W, ou `apparent_power`, en VA). Si le vôtre n'en a pas, réglez sa classe dans **Paramètres → Entités → (le capteur) → ⚙️ → Afficher comme : Puissance** quand c'est proposé, ou créez un capteur modèle (*template*) en W.

### Il ne trouve rien

- Vérifiez que le capteur change bien quand vous allumez la bouilloire.
- Laissez-le tourner quelques heures : il faut des allumages pour avoir des découvertes.
- S'il ne donne une valeur que toutes les minutes ou plus, les appareils courts passent inaperçus. Un module plus rapide (ZLinky, Shelly) change tout.

### Il voit plein de petits appareils qui n'existent pas

Le plus souvent, c'est une **clim ou une pompe à chaleur** : son compresseur fait varier la courbe sans arrêt. Indiquez-la dans **Configurer → Clim / pompe à chaleur**. Faites de même pour les appareils déjà mesurés par une prise connectée.

### Il confond le four et les plaques

C'est le cas le plus difficile : même puissance, souvent en même temps. Mettez-les dans la même **catégorie** (Électroménager) : leur total reste juste. Plus vous corrigez, mieux il les sépare.

### Pourquoi « Non identifié » est si gros ?

Il contient tout ce qui ne fait pas un saut de plus de 350 W : télé, box, lampes, chargeurs, veilles, frigo… Pour le réduire : indiquez la consommation du frigo, ajoutez des compteurs estimés (Powercalc) dans les réglages, et nommez les appareils que vous reconnaissez.

### Est-ce que ça ralentit Home Assistant ?

Non. L'analyse tourne une fois par minute, en Python pur, sans bibliothèque à installer. Un Raspberry Pi 4 ou un mini PC suffit largement. La base garde 120 jours de puissance brute.

### Mon appareil n'est pas dans la liste

Les suggestions (four, sèche-linge, bouilloire…) ne sont que des raccourcis. Dans **C'est…** ou **En ce moment, c'est…**, écrivez n'importe quel nom : pompe de piscine, aquarium, spa, borne de recharge… Un nouvel appareil est créé, et Empreinte l'apprend comme les autres après 2 ou 3 réponses.

### J'ai une seule prise connectée pour deux appareils

C'est prévu : **Configurer → Prises partagées**, choisissez la prise et écrivez les noms des appareils (« Lave-linge, Sèche-linge »). Ils ne doivent jamais tourner en même temps. Empreinte découpe chaque programme sur la courbe de la prise et reconnaît lequel a tourné. Lave-linge, sèche-linge et lave-vaisselle sont reconnus dès le départ ; pour d'autres appareils, confirmez un programme de chacun dans **Appareils**. Attention à la puissance maximale de la prise (souvent 3 680 W) : deux appareils qui chauffent en même temps la dépasseraient.

### Je me suis trompé en nommant un appareil

Tout se corrige dans **Appareils** : renommer, supprimer l'appareil, le fusionner avec un autre ou le séparer en deux. Pour une seule utilisation : « Non, c'est… ». Empreinte réapprend à chaque correction, et le bouton **Annuler** revient sur la dernière action.

### Je veux tout supprimer

**Paramètres → Appareils et services → Empreinte → ⋮ → Supprimer** : Empreinte efface lui-même tout ce qu'il avait ajouté (capteurs, lignes du tableau Énergie, statistiques, base de données). Supprimez-le ensuite dans HACS (ou le dossier `config/custom_components/empreinte`), et redémarrez. Il ne reste rien.

### L'interface est en français, et en anglais ?

Les écrans de réglage sont en français et en anglais ; le panneau Empreinte est pour l'instant en français. Une traduction est la bienvenue : voir [CONTRIBUTING.md](../CONTRIBUTING.md).

### Une autre question ?

Ouvrez une [discussion](https://github.com/nodyxstudio/empreinte-nilm-home-assistant/discussions) ou écrivez à **nodyxstudio@gmail.com**.
