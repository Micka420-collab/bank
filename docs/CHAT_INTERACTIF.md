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
