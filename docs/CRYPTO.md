# Crypto et pilote automatique

Bank › Épargne › **Crypto** suit ton portefeuille en lecture seule ; Bank › Épargne › **Pilote** laisse, si tu
le veux, ton agent agir seul dans un cadre que toi seul règles. Bank ne détient aucune clé : il ne peut rien
signer ni envoyer lui-même.

## Ce que tu vois

- **La valeur du moment** et sa variation sur 24 h, la **répartition** (un ruban, une couleur par actif), la
  **courbe** (un point par jour, écrit à chaque relève et à chaque changement de tes avoirs).
- **Tes actifs** : quantité exacte (le détail complet au survol), cours en euros, variation, où ils sont
  (adresse suivie ou plateforme). Un actif sans cours compte pour 0 € et le dit (« sans cours ») : rien n'est
  inventé ; un cours de plus de 24 h est marqué « cours ancien ».
- **Garde-fous** : le poids de la crypto dans ce que tu possèdes (à partir de 10 %, puis en alerte à 25 %), et
  un rappel si ton matelas de précaution n'est pas fait.
- **L'humeur du marché** (indice « peur et avidité » d'alternative.me) : un thermomètre, jamais un signal.
- **C'est une arnaque ?** : colle un message, une offre ou une adresse ; Bank repère les signaux classiques
  (phrase de récupération demandée, rendement garanti, urgence, faux support, airdrop, « connecte ton
  portefeuille », autorité qui réclame des cryptos, frais de déblocage, arnaque à la récupération, liens
  douteux, adresse qui imite une des tiennes) et donne les recours officiels. Rien n'est gardé.
- **Impôts de l'année** (voir plus bas), et l'état du pilote automatique.

Dans Épargne, la crypto compte dans ton patrimoine sur une ligne à part (jamais dans l'argent disponible du
budget) ; « Réglages du suivi » permet de l'en retirer.

## D'où viennent les soldes

| Source | Ce qu'elle voit | Quand |
|---|---|---|
| Adresses publiques suivies (Bitcoin, Ethereum, Base, Arbitrum, Optimism, Polygon, Avalanche, BNB Chain, Solana) | l'adresse demandée et l'adresse IP de ton serveur | avec ton accord « Suivi de tes adresses crypto », toutes les 30 minutes environ, ou « Relever maintenant » (6 fois / 10 min) |
| Saisie à la main (plateforme, portefeuille papier…) | rien, ça ne sort pas | quand tu la modifies |
| Cours en euros (CoinGecko) | la liste des actifs, jamais tes montants | avec la relève |
| Humeur du marché (alternative.me) | rien de toi | avec la relève |

Nœuds publics par défaut (relevés le 21 octobre 2026) : `mempool.space` pour Bitcoin, PublicNode pour les autres
réseaux (`ethereum-rpc.publicnode.com`, `base-rpc.publicnode.com`, `arbitrum-one-rpc.publicnode.com`,
`optimism-rpc.publicnode.com`, `polygon-bor-rpc.publicnode.com`, `avalanche-c-chain-rpc.publicnode.com`,
`bsc-rpc.publicnode.com`, `solana-rpc.publicnode.com`). **Ton propre nœud** est le plus discret : « Réglages du
suivi » accepte une adresse https publique par réseau, avec ton mot de passe (revenir au nœud public n'en demande
pas). Comme elle porte souvent une clé d'accès (`…/v2/<clé>`), elle est chiffrée en base et Bank n'en montre plus
que le schéma et l'hôte, nulle part en entier (écran, export, journaux). Chaque appel passe par la porte sûre de
Bank (https, hôte public vérifié, taille et durée bornées, pas de redirection pour un appel JSON-RPC). Un nœud ou
une source de cours qui renvoie n'importe quoi (solde négatif ou hors de proportion, cours de plus d'un milliard
d'euros, variation absurde) est écarté : les dernières valeurs connues restent, et l'erreur s'affiche.

Seuls les jetons du catalogue sont lus, avec leurs contrats officiels : USDC et EURC (pages de Circle, relevées
le 21 octobre 2026), USDT, WBTC et WETH (contrats de leurs émetteurs). Un jeton inconnu arrivé sur ton adresse
— souvent un appât pour te faire « réclamer » quelque chose — n'apparaît jamais comme une valeur.

## Ce que Bank refuse

- **Une phrase de récupération ou une clé privée**, partout : champ d'adresse, nom, note, messagerie,
  assistant. Bank reconnaît les listes BIP39 anglaise et française (douze mots de la liste à la suite, une
  coquille tolérée), les clés WIF, xprv, hexadécimales et Solana. Dans l'écran Crypto, la détection se fait
  aussi dans ton navigateur : le secret ne part même pas au serveur. Si c'était la vraie, elle est compromise :
  crée un nouveau portefeuille et déplaces-y tes fonds.
- **Une adresse mal recopiée** : somme de contrôle EIP-55 (Keccak-256 implémenté et vérifié contre une
  implémentation de référence), bech32 et bech32m (BIP173, BIP350), base58check, 32 octets pour Solana.
- **Une adresse qui ressemble à une des tiennes** sans être elle (mêmes premiers et derniers caractères :
  l'empoisonnement d'adresse) : il faut confirmer après avoir comparé en entier ; pour le pilote automatique,
  c'est un refus net.

Les adresses suivies sont chiffrées en base (elles relient ton identité à tout ton historique) ; l'agent IA
n'en voit jamais aucune. Une même adresse EVM peut être suivie sur plusieurs réseaux (Ethereum et Base, par
exemple) : ce sont des soldes distincts.

## Impôts (cessions de particulier, article 150 VH bis du CGI)

- **Qu'est-ce qui est imposable ?** Vendre contre des euros, ou payer un bien ou un service avec des cryptos.
  Échanger une crypto contre une autre ne l'est pas, et ne se note pas.
- **Seuil** : si le total de tes prix de vente de l'année ne dépasse pas **305 €**, les plus-values sont
  exonérées (le formulaire 2086 se joint quand même s'il y a eu des ventes).
- **Calcul** (formule du portefeuille) : plus-value = prix de cession − frais − prix total d'acquisition ×
  prix de cession ÷ valeur globale du portefeuille juste avant la vente. Le prix d'acquisition « consommé »
  par chaque vente est retiré pour les suivantes. Bank note tes achats en euros et tes ventes (avec la valeur
  globale du portefeuille, qu'il peut reprendre de son suivi le jour même) et refuse une vente sans valeur
  globale plutôt que d'en inventer une.
- **Taux** : pour les cessions de 2026, prélèvement forfaitaire de **31,4 %** (12,8 % d'impôt sur le revenu +
  18,6 % de prélèvements sociaux, la CSG étant portée à 10,6 % par l'article 12 de la loi n° 2025-1403 du
  30 décembre 2025), sauf option pour le barème. Pour les cessions de 2025, les sources divergent sur
  l'application de cette hausse : Bank n'estime pas, il te renvoie au taux de ta déclaration en ligne. Taux
  relevé le 21 octobre 2026, à revérifier chaque année.
- **Déclarer** : formulaire 2086, report en case 3AN (plus-value) ou 3BN (moins-value) de la 2042 C ; un
  **3916-bis par compte** ouvert sur une plateforme étrangère, même inactif ou clos dans l'année (oubli :
  750 € par compte, 1 500 € si la valeur à l'étranger a dépassé 50 000 € dans l'année ; notice 3916 de
  mars 2026). Coche « plateforme hors de France » sur tes avoirs saisis pour que Bank te le rappelle.
- Depuis le 1er juillet 2026, seuls les prestataires de services sur crypto-actifs **autorisés** (PSCA)
  peuvent te servir en France : vérifie le tien sur la liste blanche de l'AMF.

Bank estime pour t'aider à remplir le 2086 ; la déclaration et l'option pour le barème restent ton choix, et
Bank n'est pas un conseiller fiscal. Si tu trades comme un professionnel, d'autres règles peuvent s'appliquer.

## Pilote automatique

Éteint par défaut. L'allumer demande ton mot de passe, et ton code de double authentification si tu l'as
activée. Le cadre :

- **Qui** : un jeton d'agent précis (rôle Conseiller ou Gestionnaire). Un nouveau jeton du même nom n'hérite de
  rien ; révoquer le jeton coupe le pilote.
- **Quoi** : acheter, vendre, échanger au comptant (`crypto_spot`) et/ou envoyer vers tes adresses enregistrées
  (`transfer`) ; une liste d'actifs ; **consignes** (ex. « Acheter 20 € de BTC chaque semaine », suivies une
  fois par jour, semaine ou mois civil) ou **libre dans tes plafonds**.
- **Combien** : par opération (1 000 € au plus), sur 24 h et sur 30 jours **glissants** (pas de remise à zéro
  à minuit), nombre d'opérations par 24 h, perte maximale (0 € : la première perte arrête tout), durée (90 jours
  au plus, renouvelable).
- **Mode essai** (conseillé pour commencer) : l'agent demande comme s'il allait agir, Bank note ce qu'il aurait
  fait (et applique les vrais plafonds), rien n'est exécuté.

Chaque mouvement : l'agent demande (`request_autonomous_move`), Bank autorise ou refuse avec la raison, l'agent
exécute exactement ce qui est autorisé (ticket unique, 10 minutes ; pour un achat, une quantité maximale calculée
sur le dernier cours de Bank, pour qu'un outil qui confond 20 € et 20 ETH soit arrêté), puis rend compte
(`report_autonomous_move`, avec l'identifiant de la transaction ou de l'ordre : sans preuve, pas d'« exécuté »).
Un échec rapporté reste compté dans les plafonds. Tu es prévenu dans tes messageries ; un arrêt pour anomalie
passe même pendant tes heures calmes. Les décisions sont sérialisées (verrou d'écriture de la base) : deux
demandes simultanées ne passent pas ensemble sous un plafond.

Ce qui ne se règle pas : jamais de levier, de contrat à terme (perps), d'ordre à cours limité, de pilote de
trading tiers ni d'achat payé par ta carte ou ta banque ; jamais d'envoi vers une adresse que tu n'as pas
enregistrée ; jamais un actif hors de ta liste ni contre tes lignes rouges (« pas de crypto » coupe tout ;
« pas d'Ethereum », « pas de stablecoins » ou « pas d'altcoins » bloque ces actifs-là).
Arrêt automatique si un montant exécuté dépasse l'autorisation (au-delà d'1 € de frais), si un mouvement
autorisé n'est pas rapporté dans les 10 minutes (il est alors compté comme dépensé ; Bank le constate seul,
chaque minute), si la perte maximale est atteinte, si l'agent essuie 5 refus en une heure ou rapporte 3 échecs en
24 h, à l'échéance et si le jeton n'est plus valable. Les demandes d'un autre agent sont refusées sans remplir
ton journal (elles restent dans l'audit) et ne peuvent pas arrêter ton pilote à sa place.

**Tout arrêter** : le bouton de l'écran Pilote (sans mot de passe), ou depuis une messagerie reliée
`/pilote stop`, `/stop`, ou simplement « stop pilote », « arrête le pilote ». `/pilote` seul donne l'état.
Conseil : restreins le jeton de ton agent à l'adresse IP de sa machine (Connecteurs › Agents IA) ; l'écran Pilote
te le rappelle tant que ce n'est pas fait.

Les scénarios adverses du banc d'essai de Minara (bait-and-switch, montant absurde ou trop précis, adresse
empoisonnée, mauvais réseau, levier, essai suivi d'une exécution, insistance après refus…) sont rejoués contre
cette barrière dans `backend/tests/test_autonomy.py`, et contre la compétence HERMÈS avec
`integrations/hermes/bench`.

## Sources

- [Comment déclarer les plus ou moins-values sur cessions d'actifs numériques — impots.gouv.fr](https://www.impots.gouv.fr/particulier/questions/comment-declarer-les-plus-ou-moins-values-sur-cessions-dactifs-numeriques)
- [Formulaire 2086, campagne 2026 — impots.gouv.fr](https://www.impots.gouv.fr/sites/default/files/formulaires/2086/2026/2086_5515.pdf)
- [Notice 3916 / 3916-bis (mars 2026) — impots.gouv.fr](https://static.impots.gouv.fr/tdir/static/2026-03-30T16:36:08Z/aide/notices/notices2020/3916/notice3916.pdf)
- [L'AMF rappelle la fin de la période transitoire des PSAN au 1er juillet 2026 — AMF](https://www.amf-france.org/fr/actualites-publications/actualites/lamf-rappelle-que-la-periode-transitoire-pour-les-psan-pour-continuer-de-fournir-des-services-sur)
- [Listes blanches de l'AMF (PSCA)](https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/listes-blanches)
- [Adresses des contrats USDC — Circle](https://developers.circle.com/stablecoins/usdc-contract-addresses) ·
  [EURC — Circle](https://developers.circle.com/stablecoins/eurc-contract-addresses)
- [PublicNode](https://www.publicnode.com/)
