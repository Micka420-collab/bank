# Feuille de route

Ce qui manque encore à Bank, par valeur pour toi. Chaque ligne est une issue GitHub (jalon
[Feuille de route — automne 2026](https://github.com/Micka420-collab/bank/milestone/1)) qui porte ses garde-fous et ses
critères d'acceptation. Effort : S = une session, M = quelques-unes, L = un chantier.

| # | Quoi | Pourquoi | Effort |
|---|---|---|---|
| [1](https://github.com/Micka420-collab/bank/issues/1) | **Calendrier des obligations** : actualisation France Travail, déclaration trimestrielle CAF, renouvellement CSS, déclaration de revenus — fenêtres, rappels, constat automatique par le versement qui suit, alerte urgente en retard | Un oubli suspend l'aide ou crée un indu | M |
| [2](https://github.com/Micka420-collab/bank/issues/2) | **Déclaration trimestrielle CAF pré-calculée** : les 3 mois par type de ressource depuis tes relevés (montant net social du bulletin s'il est lu), chaque chiffre ouvert sur ses opérations | Première source d'indus | M |
| [3](https://github.com/Micka420-collab/bank/issues/3) | **Corrélations** : lecture fine des contrats (bail, assurances, abonnements) et croisement avec tes relevés et entre eux — doublon d'assurance habitation, loyer payé ≠ bail, prime qui monte, déclaré ↔ versé, revenus ↔ aides, Linky ↔ facture, pistes statistiques prudentes | Ce qui explique un montant qui change et trouve l'argent qui dort | M |
| [4](https://github.com/Micka420-collab/bank/issues/4) | **Plan B « perte de revenu »** : mois d'autonomie, aides qui se déclenchent (OpenFisca), quoi faire la première semaine | Savoir avant d'avoir besoin | M |
| [5](https://github.com/Micka420-collab/bank/issues/5) | **Prévision à 12 mois** : charges annuelles détectées, point bas de l'année, provision mensuelle | Les pics ne surprennent plus | M |
| [6](https://github.com/Micka420-collab/bank/issues/6) | **Crédits** : amortissement, coût réel, remboursement anticipé, renégociation | Le prêt dans le patrimoine net | S |
| [7](https://github.com/Micka420-collab/bank/issues/7) | **Virement d'épargne en un geste** (DSP2, authentification forte à chaque fois, jamais automatique) | Mettre de côté sans ouvrir la banque | L |
| [8](https://github.com/Micka420-collab/bank/issues/8) | **Valeur du logement** par les ventes voisines (DVF) | Patrimoine net juste, si propriétaire | S |
| [9](https://github.com/Micka420-collab/bank/issues/9) | **Retraite** : trimestres, estimation, PER | Le long terme chiffré | M |
| [10](https://github.com/Micka420-collab/bank/issues/10) | **Crypto : import des exports de plateformes** (CSV) pour le 2086 | La fiscalité remplie seule, hors ligne | S |
| [11](https://github.com/Micka420-collab/bank/issues/11) | **Crypto : clés d'API en lecture seule** (droits vérifiés à la connexion) | Soldes des plateformes relevés seuls | M |
| [12](https://github.com/Micka420-collab/bank/issues/12) | **Crypto : alertes de cours** et suivi pilote vs consigne | Savoir, sans signal d'achat | S |
| [13](https://github.com/Micka420-collab/bank/issues/13) | **Passkeys** (WebAuthn) | Connexion simple et résistante au phishing | M |
| [14](https://github.com/Micka420-collab/bank/issues/14) | **Partage d'un papier par lien expirant** (signé, 48 h, un téléchargement, filigrane) | L'avis d'imposition au bailleur sans courriel | S |
| [15](https://github.com/Micka420-collab/bank/issues/15) | **Boîte mail en lecture seule** (IMAP) vers Papiers | Factures et attestations rangées seules | M |
| [16](https://github.com/Micka420-collab/bank/issues/16) | **Sauvegarde hors site avec restauration testée** ; Renovate et pip-audit en CI | Dormir tranquille | S |
| [17](https://github.com/Micka420-collab/bank/issues/17) | **Foyer à deux** : conjoint, comptes joints, qui voit quoi | Si c'est ta situation | L |

Ordre de développement : 1 → 2 → 3 → 5 → 13, puis le reste selon ce qui te sert d'abord. Les règles qui portent une
date (fenêtres d'actualisation, plafonds, taux) sont sourcées et datées dans le code et dans `docs/`, et Bank dit
quand les revérifier.
