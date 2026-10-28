# Chat interactif — sondages, formulaires, checklist et annotations

Cette évolution étend le format de réponse de Bank, **sans exécuter le HTML ni le JavaScript du modèle**.

## Expérience

L'assistant dispose déjà de graphiques provenant des données Bank, de schémas (flow/étapes), de sources web et de boutons validés. Il peut maintenant proposer via `ask_interactive` :

- **Sondage :** une question à choix simple ou multiple. Ex. « Quel est ton objectif ? ».
- **Checklist :** plusieurs étapes à cocher avec compteur de progression. Ex. « Quels documents as-tu ? ».
- **Formulaire :** au maximum six questions combinant choix, cases, texte court, nombre ou curseur. Ex. « Quel projet, quel montant, quel horizon ? ».
- **Annotations de schéma :** toucher directement une boîte du graphe (ou une étape de liste), également via le clavier, pour ouvrir l'annotation contextualisée ; la remarque devient un message à l'IA.
- **Simulateur financier en direct :** le modèle peut demander `show_budget_simulator` ; Bank produit les vrais points de sa prévision de mois et un plafond basé sur le rythme de dépenses variables. L'utilisateur ajuste une économie quotidienne et une dépense imprévue (avec sa date) ; deux courbes sont superposées et le solde final, le point bas et le premier découvert se recalculent localement.

Les réponses sont transformées en **nouveau message utilisateur explicite** dans la même conversation, après clic sur « Envoyer ». L'assistant peut ensuite proposer une réponse ciblée, un nouveau widget ou un schéma modifié. Les états intermédiaires ne sont ni enregistrés dans la base ni transmis au fournisseur de modèle. Après envoi, les réponses suivent les règles existantes d'historique, de confidentialité et de mémoire de Bank.

## Contrat de sécurité

Le modèle appelle uniquement une fonction `ask_interactive` avec un objet JSON borné. Le serveur :

1. accepte seulement `poll`, `checklist` ou `form` ;
2. valide les identifiants, labels, champs, types et options ; retire URLs et caractères de contrôle ;
3. limite à 1 interface par réponse, 6 champs et 8 options par question, texte nettoyé ;
4. élimine les blocs inconnus et **revalide aussi les anciens messages** au chargement ;
5. ne donne pas au modèle la possibilité de fournir du code, des actions arbitraires, des noms d'API, du HTML ou du JavaScript pour ces composants.

Le navigateur utilise des composants React et des contrôles natifs accessibles (boutons radio, cases à cocher, textarea, nombre, curseur). Les annotations sont du texte transmis au chat — elles **n'éditent pas** directement les données ou les documents.

**Aucune interaction ne modifie un compte, un virement, une déclaration ou un document**. Toute évolution future vers la sauvegarde d'un formulaire doit ajouter une API métier, une revue préalable et un accord explicite, côté serveur.

## Exemples d'utilisation

**Sondage :** « Je veux optimiser mes finances. » → le modèle propose trois priorités (« Rembourser une dette », « Constituer un matelas », « Comprendre mes dépenses »). Le choix devient une nouvelle question contextualisée.

**Checklist :** « Je dois préparer mon dossier CAF. » → affichage des pièces suggérées, cochage progressif, envoi à l'IA pour identifier les pièces manquantes. C'est une liste de suivi, pas une confirmation de transmission à la CAF.

**Annotation :** « Fais un schéma de ma déclaration. » → l'IA crée un diagramme. La personne choisit une étape dans « Annoter ce schéma » et écrit « ici, je dois vérifier le montant net social ». L'IA reçoit la remarque et peut proposer un schéma actualisé.

## Vérifications

- Backend : `pytest tests/test_chat_widgets.py tests/test_chat_domain.py` et lint du dépôt.
- Frontend : `npm run typecheck && npm test && npm run build`.
- Revue UX : smartphone étroit, clavier, lecteur d'écran, thème sombre, `prefers-reduced-motion`.
- Contrôler que les réponses ne partent pas avant validation et que les requêtes sensibles continuent à être refusées par `secret_kind`.

## Limites de cette version

Ce n'est **pas** le moteur de composition complet de ChatGPT : pas de rendu libre de composants, pas de cartes interactives géographiques, pas de collaboration simultanée ni de véritable éditeur graphique pour déplacer les nœuds. Le modèle peut produire des schémas et poser des questions, et recevoir des annotations via le chat. Un constructeur de diagrammes entièrement manipulable et persistant serait un chantier suivant avec sa propre politique d'autorisations.

## Pourquoi c'est vraiment interactif

Dans les schémas `flow`, chaque boîte SVG est un contrôle accessible : appui au doigt, clic, Entrée ou Espace. La boîte sélectionnée est mise en évidence et le panneau d'annotation s'ouvre sur cette étape. Plusieurs schémas dans une réponse ont leurs propres marqueurs SVG.

Le simulateur `scenario` n'accepte ni calculs ni valeurs arbitraires proposés par le modèle. Un outil backend lit `analytics.forecast_inputs(...).run()` et fabrique un bloc validé : jours consécutifs, soldes entiers bornés et horizon d'un seul mois. Le navigateur effectue uniquement le calcul hypothétique :

- `solde_simulé_j = solde_Bank_j + économie_quotidienne × jours_depuis_aujourd'hui − dépense_imprévue_si_passée` ;
- le solde courant est immuable dans la simulation ;
- la réduction quotidienne est plafonnée par les dépenses variables estimées par Bank ;
- une dépense exceptionnelle n'est appliquée qu'à partir de sa date et reste soumise à un plafond de simulation ;
- les courbes, le point bas et la date du découvert se recalculent instantanément et restent distincts de la prévision initiale.

Le bouton « En discuter avec Bank » envoie **seulement après un clic** les paramètres hypothétiques et les résultats au chat pour obtenir une explication. Il ne crée aucune économie confirmée, aucun mouvement financier ni aucune instruction de virement. Durant la génération de l'IA, les composants sont désactivés pour éviter des envois simultanés involontaires.

## Vérification des nouvelles interactions

- Déplacer les curseurs sans produire d'appel réseau ; cliquer pour discuter du scénario déclenche seulement un message.
- Cliquer une boîte de schéma l'ouvre en annotation, et la sélection est utilisable au clavier.
- Tester une dépense imprévue avant/après une autre échéance, le passage sous zéro, les montants négatifs ou illisibles.
- Vérifier l'absence de compte courant : le simulateur demande d'abord de relier un compte au lieu d'inventer une projection.


## V3 — composants liés dans une seule réponse

Le modèle dispose maintenant de l'outil `show_decision_board` : aucun argument chiffré. Il permet de produire un bloc `decision_board` strictement validé à partir de la prévision existante de Bank. Ce bloc n'est pas une interface Web libre : c'est un composant React connu, avec une seule source de vérité.

En une réponse de l'assistant, il présente trois modules synchronisés :

1. **Sondage** — « Observer », « Progressif · 25 % », « Intensif · 50 % » : trois hypothèses transparentes de baisse des dépenses variables quotidiennes, jamais une promesse de résultat.
2. **Graphique** — reprend les soldes quotidiens issus de Bank et superpose la trajectoire recalculée localement pour le profil sélectionné. Le solde de départ ne bouge jamais ; l'écart de fin de mois et la réduction quotidienne se mettent à jour sans requête IA.
3. **Checklist adaptative** — trois actions générales, contextualisées au rythme sélectionné, avec état des cases et progression. Changer de profil remet ses cases à zéro pour ne pas confondre les démarches.

L'utilisateur peut ajouter une remarque (200 caractères maximum). **Rien n'est transmis tant qu'il ne clique pas** sur « En discuter avec Bank » ; l'envoi est ensuite un simple message explicatif dans le chat, pas un ordre d'action. Les choix restent locaux et ne sont pas enregistrés dans les transactions, documents, comptes ou réglages. Les contrôles sont inactifs pendant que l'IA répond.

La sortie structurée est allowlistée (`decision_board`, points consécutifs d'un seul mois, soldes entiers bornés, plafond fondé sur les dépenses variables de Bank) et revalidée avant affichage. L'IA ne fournit jamais les valeurs du graphique, les actions exécutables ou un programme JavaScript.

### Scénario de démonstration

Demander au chat : « Compare trois façons d'améliorer mon budget avec un sondage qui met à jour ton graphique et mon plan. » L'IA doit appeler `show_decision_board`. Cliquer sur « Progressif · 25 % » actualise immédiatement la courbe et la checklist ; cocher une tâche actualise son compteur ; sélectionner « Intensif · 50 % » met à jour les deux vues et réinitialise les cases ; enfin seulement, cliquer sur « En discuter avec Bank » transmet le récapitulatif.


## Coach d'achat — oui, attente ou raisonner sans culpabiliser

Quand la personne demande « Est-ce que je peux acheter ce vélo à 350 € ? », l'assistant web dispose de `show_purchase_coach`, basé sur **le même moteur `decisions.purchase` que `can_i_buy`**. Il reçoit `amount_eur` et un libellé, mais aucun verdict libre ni chiffre proposé par le modèle.

Le bloc `purchase_coach` récapitule le verdict de Bank (`oui`, `oui_mais`, `attendre`, `plus_tard`, `hors_de_portee`), le point bas de trésorerie *avant et après l'achat*, le budget quotidien restant, une date plus favorable si elle a été calculée et jusqu'à deux chemins d'épargne. Les données sont strictement validées côté serveur et revalidées au chargement.

Pour aider à décider plutôt que moraliser, un questionnaire **local** demande :
- « C'est nécessaire / utile / pour me faire plaisir / sur un coup de tête ? »
- « Je le veux maintenant / je peux prendre mon temps / je ne sais pas » et « Je pourrais attendre 48 heures ».
- Éventuellement, un nouveau prix (pour regarder une alternative moins chère).

Une courte réflexion change selon les réponses mais **jamais le verdict financier**. Si le prix change, le verdict précédent reste identifié comme celui de l'ancien montant : il faut cliquer sur « Recalculer avec Bank », puis l'assistant exécute de nouveau `show_purchase_coach`. Aucun calcul basé sur un nouveau prix n'est fabriqué dans le navigateur.

Cette carte ne lance aucune commande ni paiement. En l'absence de compte courant relié ou importé, Bank ne prononce pas de verdict fiable et propose de renseigner les données. Son approche est à la fois pragmatique et respectueuse : un achat plaisir n'est pas interdit, et un achat nécessaire ne doit pas être minimisé.

**Exemple :** « Je veux acheter des écouteurs à 240 € ; dis-moi si je peux les prendre ou raisonne-moi si c'est une mauvaise idée. »
