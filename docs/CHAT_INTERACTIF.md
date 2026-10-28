# Chat interactif — sondages, formulaires, checklist et annotations

Cette évolution étend le format de réponse de Bank, **sans exécuter le HTML ni le JavaScript du modèle**.

## Expérience

L'assistant dispose déjà de graphiques provenant des données Bank, de schémas (flow/étapes), de sources web et de boutons validés. Il peut maintenant proposer via `ask_interactive` :

- **Sondage :** une question à choix simple ou multiple. Ex. « Quel est ton objectif ? ».
- **Checklist :** plusieurs étapes à cocher avec compteur de progression. Ex. « Quels documents as-tu ? ».
- **Formulaire :** au maximum six questions combinant choix, cases, texte court, nombre ou curseur. Ex. « Quel projet, quel montant, quel horizon ? ».
- **Annotations de schéma :** sélectionner une boîte ou une étape et envoyer une remarque ou correction à l'IA.

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
