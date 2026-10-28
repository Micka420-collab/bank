# Bank — Expérience V2 : comprendre, anticiper, agir

> **État au 28 octobre 2026 :** le laboratoire « Et si… ? » est implémenté dans cette branche. Les autres chantiers ci-dessous sont **proposés, non livrés**. Ce document complète [DESIGN.md](DESIGN.md) et [ROADMAP.md](ROADMAP.md) : aucune refonte brutale de l'existant.

## La promesse produit

**En 10 secondes, comprendre ma situation. En 2 minutes, savoir quoi faire. En 5 minutes, préparer la démarche — sans jamais perdre le contrôle.**

Bank est un cockpit personnel, pas un fil d'actualité financière, une banque, un conseiller qui promet des rendements ou une IA qui clique partout. Il doit traduire les données en **décisions explicables**. Toute action sensible comporte : source datée → calcul/justification → aperçu → validation explicite → résultat → historique.

### La page « Aujourd'hui » sur téléphone

1. **Une phrase** : combien je peux dépenser par jour, jusqu'à quand et pourquoi.
2. **Un danger éventuel** : date du point bas ou du découvert, puis une action humaine concrète (pas dix alertes).
3. **Une prochaine action prioritaire** : confirmer un prélèvement, relier un compte, vérifier un document.
4. **Une courbe** : réalisé et prévu séparés. En dessous, « Et si on changeait la suite ? » pour explorer des hypothèses.
5. **Un détail dépliable** : dépenses par catégorie, échéances, contrats, historiques. Pas de défilement infini pour accéder à l'essentiel.

### Livré : laboratoire « Et si… ? »

- **Dépense ponctuelle** en euros et date comprise entre aujourd'hui et la fin du mois.
- **Réduction des dépenses variables quotidiennes**, plafonnée à leur niveau déjà inclus dans le calcul du backend. Les économies commencent **demain** ; aucune réécriture du solde bancaire observé.
- **Scénarios combinés** et préréglages ; réinitialisation en un geste.
- **Comparaison immédiatement visible** dans la courbe d'origine : projection actuelle et hypothèse de scénario. Calcul du nouveau solde de fin de mois, du point bas et de la première date sous zéro.
- **Local seulement** : aucun POST, aucune modification bancaire, aucun événement enregistré, aucune autorisation de paiement. L'utilisateur peut tester un scénario sans le sauvegarder.
- **Limites affichées** : projection conditionnelle, pas une promesse. Le moteur réutilise les événements et la dépense habituelle du backend ; il n'en prédit pas de nouveaux. Une réduction de 5 €/jour ne prétend pas correspondre à 5 € d'économies effectivement réalisables.

Calcul pour chaque date `d` de la projection officielle :
```
solde_scenario(d) =
  solde_officiel(d)
  + economie_quotidienne × jours_entiers_depuis_aujourdhui(d)
  - depense_ponctuelle × 1[d >= date_depense]
```
Le point bas et le premier découvert sont recalculés en balayant toute la série, **pas seulement le solde final**. Les dates civiles sont traitées en UTC pour éviter les décalages DST du navigateur.

## Prochains investissements — par effet utilisateur

| Priorité | Module | Ce que l'utilisateur voit | Garde-fou et critère de livraison |
|---|---|---|---|
| P1 | **Radar des charges invisibles** | « Ce prélèvement a augmenté de 12 € » / « Deux contrats semblent couvrir le même risque ». Une preuve par ligne et une démarche proposée. | Déduplication marchands, seuils statistiquement justifiés, liens vers justificatifs ; jamais « économie réalisée » avant confirmation. |
| P1 | **Assistant des démarches, de bout en bout** | Prendre une photo → extraction assistée → formulaire prérempli → comparaison source/champs → revue obligatoire → transmission volontaire. | Source/page/champ pour chaque valeur, aucun envoi sans clic explicite, suppression/rétention contrôlables, suivi des erreurs et retours. |
| P1 | **Fiabilité des données** | Pour chaque carte : « relevé mis à jour hier », « estimé », « à confirmer », « donnée manquante ». | Pas de chiffre précis sans date de fraîcheur ; appel externe identifié ; mode dégradé clair ; données périmées jamais déguisées en direct. |
| P2 | **Calendrier de trésorerie** | Une frise revenus/échéances/démarches : « loyer le 5, salaire le 7 ; voici le point bas ». | Distinguer événement déduit, confirmé, ou déclaré ; un rappel dédoublonné, avec option silence. |
| P2 | **Copilote IA transparent** | Propositions structurées avec source, montant, probabilité, coût et risque, puis « approuver / modifier / ignorer ». | Les sorties IA ne sont pas des écritures ; outils à permission minimale, validation côté serveur, journal d'audit, aucune action sensible par défaut. |
| P3 | **Parcours d'onboarding intelligent** | « Choisis ta banque » → consentement DSP2 → connexion → contrôle des comptes → première valeur immédiate. Démo locale si pas de banque. | Ne jamais collecter le mot de passe bancaire, états d'erreur récupérables, progression sauvegardée uniquement si nécessaire, accessibilité clavier/lecteur. |

## Une grammaire UX commune

- **Une action principale par écran** ; les détails restent disponibles, jamais cachés irréversiblement.
- **Aucune certitude inventée** : distinguer « observé », « estimé », « scénario », « confirmé » et « effectué ».
- **Design mobile-first** : targets tactiles confortables, une main, formulaires à clavier adapté, CTA proches de leur conséquence.
- **Animations fonctionnelles** : transitions qui indiquent cause/effet ; respect de `prefers-reduced-motion`, pas de récompense visuelle pour une simulation ou une prédiction.
- **Accessibilité** : labels liés aux champs, `aria-live` réservé aux changements importants, focus visible, contraste AA visé, erreurs non résolues explicites.
- **Respect** : pas de culpabilisation (« tu dépenses trop »), pas de fausse urgence, montants masquables, pas de jargon réglementaire sans explication.
- **Vie privée** : aucune donnée financière réelle dans la télémétrie UX ; seuls des compteurs techniques anonymisés/agrégés peuvent être envisagés avec information et consentement appropriés.

## Contrats d'ingénierie

1. Chaque calcul important possède des tests de scénario **positif, négatif, limite, chronologie et absence de données**.
2. Chaque lecture/écriture d'un compte, document ou connecteur passe par les politiques d'autorisation serveur existantes.
3. Pour toute action extérieure : prévisualisation, consentement spécifique, idempotence, suivi d'état, mécanisme de reprise.
4. Les estimations doivent citer la version/date de leurs barèmes et distinguer `source_updated_at` de `computed_at`.
5. Toute amélioration UI doit préserver les parcours et tests existants ; mesure en mobile 360 px et bureau 1280 px, clavier et mouvement réduit.

**Indicateurs à instrumenter sans données financières :** réussite du premier ajout bancaire, temps jusqu'au premier écran utile, pourcentage de scénarios compris sans aide, abandon des formulaires, taux d'erreurs récupérables, accessibilité et performance perçue. Pas de suivi individuel du montant des dépenses.

## Chemin de livraison

- **Étape 1 (cette branche)** : simulateur local et surcouche sur la courbe, tests et documentation.
- **Étape 2** : test UX avec données fictives (scénarios risque de découvert, fin de mois positive, aucune opération) et revue accessibilité réelle.
- **Étape 3** : radar des charges invisibles et provenance/fraîcheur partagée.
- **Étape 4** : chaîne documents → formulaire assisté → revue → démarches, sans permission implicite.
- **Étape 5** : notifications et copilote selon une politique explicite de consentement.

### Références utiles

- [OCDE, *Artificial intelligence and personal finance* (21 juillet 2026)](https://www.oecd.org/en/publications/artificial-intelligence-and-personal-finance_2858fdf4-en.html) — enjeux de fiabilité, transparence, confidentialité et protection du consommateur.
- [CNIL, recommandations relatives à l'évaluation de la solvabilité (7 mai 2026)](https://www.cnil.fr/fr/recommandation-octroi-de-credit) — utile si Bank évolue un jour vers des décisions de crédit ; le simulateur actuel **n'évalue pas la solvabilité**.
