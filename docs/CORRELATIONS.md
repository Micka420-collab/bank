# Ce qui est lié : corrélations entre tes contrats, tes relevés et tes aides

Bank › Plus › Ma situation › **Ce qui est lié** (et une carte sur la page du jour quand ça coûte). Chaque pièce
de ton dossier dit une chose ; ensemble, elles en disent une autre : le bail nomme un assureur, ta banque te
prélève une assurance habitation, et personne ne t'avait dit que tu payais deux fois.

## Lire les contrats dans le détail

Dans Papiers, un **bail** est lu pour sa prise d'effet, son loyer hors charges, sa provision pour charges, son
dépôt de garantie, sa clause de révision (IRL et trimestre de référence), le préavis, meublé ou vide, l'assurance
exigée et l'assureur nommé, le bailleur. Une **attestation ou un avis d'échéance d'assurance** est lu pour
l'assureur (y compris ceux des banques : Pacifica, ACM, Sogessur, Cardif, BPCE Assurances, La Banque Postale
Assurances…), le type (habitation, auto, emprunteur, santé…), la cotisation, sa périodicité, la date d'échéance.
Un **bulletin de paie** pour son net payé et son montant net social, une **quittance** pour son montant et son
mois. Lecture déterministe (motifs sur le texte), chaque valeur avec la ligne qui la prouve, corrigeable d'un
clic ; la lecture assistée par le modèle de langage reste une proposition à valider, jamais une vérité.

## Les mécanismes connus

| Constat | Comment Bank le voit | Ce qu'il te dit |
|---|---|---|
| **Deux assurances habitation** | deux prélèvements réguliers d'assureurs différents avec « habitation » dans le libellé (ou nommés sur une attestation habitation) ; un assureur connu sans type écrit n'est que « probable » | le second contrat coûte X € par an pour rien ; après un an, résiliation à tout moment (loi Hamon) ; garde celui que le bail exige ; « Préparer la démarche » ouvre la lettre de résiliation préremplie |
| **Le bail nomme un autre assureur** que celui que tu paies | bail + un seul prélèvement d'assurance habitation | attestation à mettre à jour, ou doublon à vérifier |
| **Loyer payé ≠ bail** | le prélèvement de loyer face au loyer et aux charges du bail | hausse sans clause de révision : non due (loi du 6 juillet 1989, art. 17-1) ; avec clause : loyer maximal calculé sur l'IRL, dépassement chiffré par an ; loyer plus bas que le bail : à vérifier |
| **Dépôt de garantie au-dessus du plafond** | dépôt du bail face au loyer hors charges | un mois (vide), deux mois (meublé) ; l'excédent se réclame |
| **Prime ou abonnement qui monte** | dernier prélèvement de plus de 5 % au-dessus de la médiane des précédents (assurances, box et mobile, abonnements, énergie) | assurance : loi Hamon après un an, loi Chatel à l'échéance ; téléphonie : résiliation sans frais sous quatre mois après une hausse (code de la consommation, art. L224-33) |
| **Bulletin ≠ virement** | net payé du bulletin face au virement de salaire du même mois | acompte, saisie, retenue ou erreur : demander le détail |
| **Quittance ≠ loyer payé** | quittance face au prélèvement du mois | quittance corrigée à demander (elle sert pour l'APL) |
| **Déclaré ≠ versé** | ressources déclarées à la CAF un trimestre (gardées dans Tes déclarations) et le versement qui suit, comparés au trimestre d'avant | ressources en nette variation mais versement inchangé → déclaration prise en compte ? ; versement en forte baisse sans hausse de ressources → notification de droits à lire, contestation sous deux mois |
| **Mensualité d'énergie ≠ consommation** | l'analyse Linky (consommation × tarif) face à la mensualité | rattrapage probable (relever la mensualité ou provisionner) ou remboursement probable (baisser la mensualité) |

## Les pistes statistiques, avec prudence

Sur au moins **huit mois**, Bank calcule la corrélation de rang (Spearman) entre ton revenu du mois et chaque
catégorie variable, puis entre catégories, et ne garde que les liens nets (|ρ| ≥ 0,6) **et** peu probables par
hasard (test par permutation, 400 tirages, p ≤ 0,05), deux au plus. Il repère aussi la part de tes dépenses
variables qui part dans la semaine qui suit la paie (sur au moins six paies). Ces pistes sont rangées à part,
formulées « Bank a remarqué », et le texte dit chaque fois que ce n'est pas une cause.

## Ce que tu en fais

- **Préparer la démarche** : pour un doublon, la lettre de résiliation est préremplie (fournisseur, mensualité) ;
  tu relis, tu valides, tu envoies (Bank n'envoie rien).
- **Pas un problème** : le constat est écarté ; il réapparaît dans Ma situation si tu changes d'avis.
- Ton agent (HERMÈS) lit les mêmes constats (`get_correlations`), avec leurs pièces en mode complet, sans le détail
  en mode agrégats.
- Les constats sérieux (doublon sûr, loyer au-dessus de l'IRL) sont annoncés une fois dans tes messageries.

## Ce que Bank ne fait pas

Il ne recalcule pas un droit CAF (c'est OpenFisca, dans Aides), ne résilie ni ne réclame à ta place, et
n'affirme jamais une cause à partir d'une corrélation. Les règles citées sont datées ; si l'organisme, l'assureur
ou le bailleur te dit autre chose, c'est le contrat et la loi qui priment.

## Sources

- [Indice de référence des loyers, 3e trimestre 2026 — INSEE](https://www.insee.fr/fr/statistiques/9081853)
  (relevé le 22 octobre 2026 ; valeurs 2023 déduites des variations annuelles publiées pour 2024)
- [Loi n° 89-462 du 6 juillet 1989, art. 17-1 (révision du loyer) et art. 22 (dépôt de garantie) — Légifrance](https://www.legifrance.gouv.fr/loda/id/JORFTEXT000000509310/)
- [Résiliation d'une assurance (loi Hamon, loi Chatel) — service-public.fr](https://www.service-public.gouv.fr/particuliers/vosdroits/F19083)
- [Code de la consommation, art. L224-33 (hausse de prix d'un opérateur) — Légifrance](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000032227297/)
- [RSA, prime d'activité : vérifiez, validez, c'est déclaré — caf.fr](https://www.caf.fr/allocataires/caf-de-touraine/actualites-departementales/rsa-prime-d-activite-verifiez-validez-c-est-declare)
