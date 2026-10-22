# Tes déclarations : ce qu'il faut faire pour garder tes aides

Bank › Gagner › Aides › **Tes déclarations**. Les aides se perdent plus souvent par un oubli que par un refus :
une actualisation manquée radie, une déclaration trimestrielle non validée suspend, un renouvellement oublié
laisse sans complémentaire. Bank suit ces obligations, te prévient au bon moment, prépare les chiffres et
constate que c'est fait. **Il ne déclare jamais à ta place** : rien n'est envoyé à un organisme depuis Bank.

## Ce que Bank suit

| Obligation | Quand | Si tu oublies | Comment Bank sait que c'est fait |
|---|---|---|---|
| **Actualisation France Travail** | chaque mois, du 28 (26 en février court) au 15 du mois suivant à minuit | radiation de la liste et allocation du mois non versée ; réinscription nécessaire | le virement France Travail qui suit (3 à 5 jours ouvrés après l'actualisation) ; rien au 22 → alerte urgente |
| **Déclaration trimestrielle de ressources** (CAF ou MSA, pour le RSA et la prime d'activité) | tous les trois mois, dans le mois de déclaration que tu indiques (visible dans Mon Compte), sur les ressources des 4ᵉ, 3ᵉ et 2ᵉ mois précédents | prestation suspendue tant que ce n'est pas validé ; pour le RSA, les mois non déclarés ne sont pas rattrapés | le versement du mois suivant ; rien au 15 → alerte urgente |
| **Renouvellement de la Complémentaire santé solidaire** | de quatre à deux mois avant la date de fin de droits (sur l'attestation) ; l'instruction prend un à deux mois | plus de complémentaire à la date de fin : soins à ta charge jusqu'à la nouvelle attribution | tu le notes ; automatique si tu touches le RSA ou l'ASPA (Bank te le rappelle alors comme une vérification) |
| **Déclaration de revenus** | de l'ouverture (9 avril en 2026) à la date limite de ton département : 21 mai (01 à 19), 28 mai (20 à 54, Corse comprise), 4 juin (55 à 976) en 2026 ; papier le 19 mai | majoration de 10 % dès le premier jour de retard (20 % après mise en demeure), et des aides calculées sur des revenus inconnus | tu le notes |
| **Une obligation à toi** | la date que tu donnes, à la cadence que tu veux (attestation d'assurance au bailleur, document à renouveler…) | ce que tu as noté | tu le notes |

Les dates de la déclaration de revenus des années suivantes sont **estimées** jusqu'à la publication du
calendrier officiel (mars-avril) : Bank le dit et prend la date la plus proche quand ta commune n'est pas
renseignée. Toutes ces règles sont datées (relevées le 22 octobre 2026) et sourcées en bas de page ; si
l'organisme te dit autre chose, c'est lui qui a raison.

## Comment ça marche

1. **Bank propose** : un versement France Travail reconnu dans tes relevés → actualisation ; une prime
   d'activité ou un RSA → déclaration trimestrielle ; la déclaration de revenus pour tout le monde. Une fiche
   « Bank te demande » sur la page du jour, tu acceptes ou tu écartes d'un geste. Tu peux aussi tout ajouter à la
   main dans Aides.
2. **Bank calcule tes fenêtres** sur un an, dans le calendrier civil. S'il lui manque une date (mois de ta
   déclaration CAF, fin de tes droits CSS), il te la demande.
3. **Bank te prévient** : carte sur la page du jour à l'ouverture, rappel trois jours avant l'échéance, alerte
   dans tes messageries (`/declarations` donne l'état à tout moment). En retard, ou quand le versement qui devait
   suivre ne vient pas, l'alerte est **urgente** : elle passe même pendant tes heures calmes.
4. **Bank constate** : le virement qui suit la fenêtre vaut preuve (« constaté par le virement du 3 »). Tu peux
   aussi dire « c'est fait » toi-même, et revenir dessus.
5. **Ton agent** (HERMÈS) lit le même calendrier (`get_obligations`) : il rappelle et explique, les dates
   viennent de Bank, et il ne déclare jamais.

## La déclaration trimestrielle : tes chiffres, prêts

« Mes chiffres » donne, pour les trois mois de référence et par type de ressource (salaires et revenus d'activité,
allocations chômage, indemnités journalières, pensions, autres), ce que tes relevés disent — chaque montant
s'ouvre sur ses opérations. Depuis 2025 (« solidarité à la source »), la CAF préremplit salaires, chômage,
indemnités journalières et pensions avec le **montant net social** : pour les salaires, c'est la case du bulletin
de paie, pas le net versé sur ton compte (souvent plus bas). Si tu as déposé tes bulletins dans Papiers, Bank
reprend leur montant net social et montre le net versé à côté ; sinon il te le rappelle. Les prestations CAF, les
remboursements et les virements entre tes comptes sont mis à part (« non déclarables »), les entrées dont la
nature est à préciser sont listées « à regarder » (pension alimentaire ? vente ? cadeau ?). Tu vérifies, tu
recopies dans Mon Compte, tu valides là-bas. « J'ai déclaré ces montants » garde ce que tu as déclaré, chiffré,
pour le comparer au versement qui suit.

À ajouter toi-même si tu en as : pension alimentaire reçue, revenus non salariés, revenus de placements, loyers
perçus.

## Ce qui est protégé

Les libellés, réglages et montants déclarés sont chiffrés en base (ils disent de quelles aides tu vis). L'agent
IA ne voit les montants préparés qu'en mode complet ou anonymisé, jamais en mode agrégats. Détails et tests :
[SECURITY.md](../SECURITY.md).

## Sources

- [Calendrier des paiements et de l'actualisation 2026 — francetravail.fr](https://www.francetravail.fr/candidat/vos-droits-et-demarches/vos-demarches-aupres-de-pole-emp/le-calendrier-des-paiements.html)
- [RSA, prime d'activité : vérifiez, validez, c'est déclaré (solidarité à la source) — caf.fr](https://www.caf.fr/allocataires/caf-de-touraine/actualites-departementales/rsa-prime-d-activite-verifiez-validez-c-est-declare)
- [Un an de solidarité à la source — communiqué Cnaf, mars 2026](https://www.caf.fr/sites/default/files/medias/cnaf/Nous_connaitre/Presse/2026/260318%20CP%201%20an%20de%20solidarit%C3%A9%20%C3%A0%20la%20source_2.pdf)
- [Déclaration trimestrielle RSA et prime d'activité — aide-sociale.fr](https://www.aide-sociale.fr/declaration-rsa-caf/)
- [Renouvellement de la Complémentaire santé solidaire — aide-sociale.fr](https://www.aide-sociale.fr/renouvellement-css/) ·
  [Complémentaire santé solidaire — ameli.fr](https://www.ameli.fr/)
- [Déclaration de revenus 2026 : ouverture le 9 avril — economie.gouv.fr](https://www.economie.gouv.fr/node/34365) ·
  [dates limites par département — neofa.com](https://neofa.com/fr/fiscalite/declaration-impots/date-limite-declaration-impots/)
