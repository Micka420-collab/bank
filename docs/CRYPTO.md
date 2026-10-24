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
| Plateforme reliée par clé d'API en lecture seule (Kraken, Binance, Coinbase) | la plateforme voit ta clé et l'adresse IP de ton serveur ; Bank ne lit que les soldes | avec le même accord, à la relève, ou « Relever » sous la clé (6 fois / heure) |
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

### Plateformes reliées : une clé qui ne peut que lire

Crée la clé sur la plateforme avec les seuls droits de lecture (Kraken : « Query Funds » ; Binance : « Activer la
lecture », idéalement restreinte à l'adresse IP de ton serveur ; Coinbase : clé CDP avec la permission « View »,
dont le secret est une clé privée EC au format PEM). Colle-la dans Crypto › « Plateformes reliées » avec ton mot
de passe Bank. **Avant de garder quoi que ce soit**, Bank vérifie les droits :

- Binance renvoie les restrictions de la clé (`GET /sapi/v1/account/apiRestrictions`) et Coinbase ses permissions
  (`GET /api/v3/brokerage/key_permissions`) : tout droit de trading, de retrait ou de transfert refuse la clé.
- Kraken ne les renvoie pas : Bank demande une **validation d'ordre** (`AddOrder` avec `validate=true` et un volume
  nul, qui ne soumet rien et serait rejetée même soumise) et une **information de retrait** vers une adresse de
  retrait inexistante (`WithdrawInfo`, montant nul). Une clé de lecture répond « Permission denied » aux deux ;
  toute autre réponse prouve un droit de trop, et la clé est refusée. Bank n'appelle jamais `Withdraw` ni un
  `AddOrder` sans `validate`.

La clé refusée n'est pas gardée (le journal note le refus, sans la clé). La clé acceptée est chiffrée en base ;
l'écran n'en montre que les quatre derniers caractères ; l'API, l'export RGPD et le journal ne la contiennent pas ;
un message d'erreur de plateforme n'en cite rien. Les signatures (HMAC-SHA512 pour Kraken, HMAC-SHA256 pour
Binance, JWT ES256 pour Coinbase) se font dans Bank, par la porte sûre (https, hôte officiel vérifié, aucune
redirection suivie, réponses bornées). Les soldes relevés deviennent des lignes « plateforme » du portefeuille :
jamais modifiées à la main, remises à zéro quand un actif disparaît, gardées telles quelles (avec l'erreur sous la
clé) quand la plateforme ne répond pas. Les noms Kraken (`XXBT`, `ETH.F`, `SOL.S`) et Binance (`LDBTC`) sont ramenés
au symbole usuel ; les devises (EUR, USD…) ne sont pas des avoirs crypto. Retirer la clé supprime ses lignes ;
supprime-la aussi côté plateforme. Dix clés au plus. Un compte tenu hors de France se coche pour le 3916-bis.

## Alertes de cours

Sur l'écran Crypto, la carte **Alertes de cours** : un actif dont Bank suit déjà le cours, un type (seuil bas,
seuil haut en euros, ou variation sur 24 h en %, à la hausse comme à la baisse) et une valeur ; un repère
facultatif (60 caractères). 20 alertes au plus ; un seuil jusqu'à un milliard d'euros (huit décimales au plus),
une variation de 2 à 90 %. Un seuil déjà franchi au moment où tu le fixes est refusé (il n'y aurait pas de
franchissement à signaler).

- **Quand** : à chaque relève des cours (toutes les 30 minutes environ, ou « Relever maintenant »), sur le
  cours tout frais ; aucune autre source, aucun appel en plus. Un cours de plus de 2 h ne déclenche rien.
- **Au plus une fois par franchissement** : armée, l'alerte passe « déclenchée » (avec le cours et l'heure) par une
  mise à jour conditionnelle — deux relèves en même temps ne la déclenchent qu'une fois. Elle se réarme seule
  quand le cours revient de l'autre côté avec une marge (1 % du seuil, ou 1 point de variation), ou quand tu la
  réarmes (refusé tant que le cours, ou la variation, est encore au-delà ; de même à la création). L'état est
  en base : un redémarrage ne renvoie rien. « Au plus » : si elle tire, se réarme et tire de nouveau pendant tes
  heures calmes, seul le dernier franchissement t'est annoncé — avec le jour, s'il n'est plus aujourd'hui.
- **Où** : dans tes messageries et sur ton téléphone (notifications du navigateur), par le circuit commun des
  alertes : une fois par compte relié, jamais pendant tes heures calmes, regroupées avec les autres. En
  confidentialité « agrégats », le message ne dit que « une alerte de cours s'est déclenchée ».
- **Ce qu'elle dit** : un constat — « 📉 BTC est passé sous 50 000 € (49 812 € à 14 h 05) — seuil que tu as
  fixé. Une information, pas un conseil. » Jamais « c'est le moment », jamais une invitation à acheter ou à
  vendre : un test relit chaque modèle de message et refuse ces mots-là.
- **Qui** : toi seul. Aucun outil d'agent ne crée, ne change ni ne supprime une alerte.

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
  globale du portefeuille, qu'il peut te proposer depuis son suivi, à vérifier) et refuse une vente sans valeur
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

### Importer un export de plateforme

Plutôt que de tout noter à la main, dépose dans l'encart « Importer un export de plateforme » (écran Crypto)
l'export CSV de ta plateforme. Le format est reconnu à ses en-têtes :

| Plateforme | Export | Ce que Bank en lit |
|---|---|---|
| Kraken | export **Grand livre** (`ledgers.csv`) ou **Transactions** (`trades.csv`) | les deux jambes d'un échange (même `refid`), codes historiques ramenés (`XXBT` → BTC, `ZEUR` → EUR) ; un échange vu dans les deux fichiers ne compte qu'une fois |
| Binance | export **Historique des transactions** (`User_ID, UTC_Time, Account, Operation, Coin, Change, Remark`) | les jambes d'un même ordre, à la même seconde ; `User_ID` et `Remark` ne sont jamais lus |
| Coinbase | rapport **Historique des transactions** (CSV) | les lignes après l'en-tête (les lignes du dessus, avec ton nom, sont sautées) ; la paire de la note (`on ETH-BTC`) pour reconnaître un achat payé en crypto |

Tu vois d'abord un **aperçu** : achats en euros, ventes en euros, ventes à compléter, opérations déjà importées
ou déjà notées à la main, et lignes écartées, chacune avec sa raison. Rien n'est noté avant « Importer ».
Kraken et Binance livrent l'export dans une archive `.zip` : dézippe-la et dépose le `.csv`.

- **Acquisition** : un achat payé en euros (prix et frais en euros). **Cession** : une vente contre des euros.
- **Écartés, avec leur raison** : les échanges crypto contre crypto, stablecoins compris (pas imposables, sursis
  de l'article 150 VH bis) ; dépôts, retraits et envois (un paiement d'un bien ou d'un service avec tes cryptos
  est imposable : note-le à la main) ; récompenses de staking et intérêts ; marge et produits dérivés ; ce que
  l'export ne chiffre pas en euros (achat par carte, P2P). Une contrepartie dans une **autre monnaie** (USD…)
  est signalée : Bank ne la convertit pas avec un cours inventé.
- **Valeur globale du portefeuille** : pour chaque vente, une case pré-remplie et modifiable dans l'aperçu.
  Bank y propose le relevé de son suivi de **la veille** (date à Paris, l'export est en UTC), sinon celui du
  jour même — mais celui-là est la dernière valeur de la journée, prise **après** la vente. Vérifie-la : la
  valeur attendue est celle de **toutes** tes cryptos, sur toutes tes plateformes et tous tes portefeuilles,
  **juste avant** la vente ; le suivi ne compte que ce que Bank connaît. Une valeur trop basse sous-estime la
  plus-value. Sans relevé, ou si le relevé est plus bas que le prix de vente, la case est vide ; sans valeur,
  la vente attend — un nouvel import la reprendra.
- **Déjà notée à la main** : une opération de l'export qui a le même jour, le même type et le même montant
  qu'une saisie à la main n'est pas recréée (sinon la vente compterait deux fois au 2086) ; coche-la dans
  l'aperçu seulement si c'en est vraiment une autre.
- **Doublons écartés** : chaque ligne laisse une empreinte (SHA-256 de la plateforme et de l'identifiant de
  l'échange, ou de la ligne et de son rang quand l'export n'a pas d'identifiant) ; réimporter le même fichier,
  ou un export qui chevauche le précédent, ne crée rien. Une opération importée que tu supprimes n'est plus
  recréée par les imports suivants (seule son empreinte est gardée) ; l'aperçu la montre « supprimée » et tu
  peux la cocher pour la reprendre. Pour corriger une opération importée (des frais payés en BNB, que Bank ne
  chiffre pas), supprime-la puis ressaisis-la à la main avec ses frais.
- **Garde-fous** : lu sur ton serveur, aucun appel réseau (pas même un cours) ; 5 Mo et 20 000 lignes au plus ;
  nombres lus en décimal exact sous une forme stricte (une cellule « =FORMULE » n'est qu'un texte) ; ne sont
  gardés que la date, le type, l'actif, la quantité, les montants en euros et l'empreinte — jamais un
  identifiant de compte, de transaction, une adresse ni une note de la plateforme. Chaque import est journalisé
  (nombre d'opérations créées, doublons, déjà notées à la main, supprimées, écartées).

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

**Pilote vs consigne** (écran Pilote) : pour chaque consigne, les rendez-vous prévus sur trois mois (un par
jour, semaine ou mois civil, dans ton fuseau) face à ce que l'agent a rapporté — fait à temps, en retard (de
combien de jours), manqué, montant différent (écart en euros et en %), en cours, attendu — avec le total prévu et
le total fait, puis les mouvements faits hors consigne (mode libre, consigne retirée, deuxième mouvement sur une
même période). Une consigne peut porter un **jour prévu** (« chaque semaine, prévu le lundi », « chaque mois, prévu le 15 » ; un 31
tombe le dernier jour des mois courts) : il ne change rien à ce que Bank autorise, il sert à dire « en retard ».
Un rendez-vous n'est attendu que si le pilote était allumé avec cette consigne à partir du jour prévu : une
semaine de pause, d'arrêt ou après l'échéance s'affiche « pilote éteint », pas « manqué ». Un mouvement refusé
par Bank ne compte pas comme fait (la raison du refus s'affiche), un échec rapporté ou un mouvement jamais
rapporté non plus. Les jours se comptent en dates civiles de Paris : un passage à l'heure d'été ou d'hiver ne
décale rien (testé sur un calendrier de référence, `backend/tests/test_pilot_track.py`).

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
