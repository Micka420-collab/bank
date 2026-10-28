# Bank — Radar prospectif, actualités et transparence CRS

## Pourquoi ce développement

L'objectif est de croiser les échéances déjà connues, la trésorerie prévisionnelle et des sources institutionnelles à jour **sans confondre une corrélation avec une prédiction certaine**. Aucun titre financier, politique ou géopolitique ne suffit à déduire qu'un portefeuille perdra une somme dans un mois.

## Ce qui fonctionne dans cette première version

- **Radar des 45 prochains jours** (`GET /api/watch/radar`, authentifié) : rapproche les dépenses prévues et les récurrences détectées avec le solde estimé **du mois correspondant**, dont le calcul est effectué par `yearahead.compute`. Un mois se terminant sous le matelas de sécurité déclenche une mention « Marge à surveiller », sinon « Dépense à prévoir ».
- **Données vérifiables** : date, montant, base du rapprochement, nature « saisi » ou « estimé », solde mensuel prévu. Exclut les impôts estimés sans date certaine, les événements trop éloignés et les petits montants. Sans compte courant budgétaire, pas de fausse prévision.
- **Informations du monde** : flux RSS officiels de la BCE, de la Réserve fédérale américaine (décisions monétaires), du Conseil de l'UE, de la Commission européenne, du ministère français de l'Économie et de Service Public. Les flux passent par le fetcher SSRF-protected déjà présent, avec horodatage, détection de versions et consentement de veille. Le radar n'affiche que les articles officiels pertinents pour les thèmes marchés, géopolitique ou transparence, publiés dans les 14 derniers jours.
- **Information et non influence** : un titre ne devient ni une prévision de cours, ni une promesse de rendement. Pas d'inférence « cet article va faire baisser ton ETF » sans positions identifiées, exposition mesurée et données de prix validées.
- **Messageries existantes** : les dépenses **saisies explicitement** et susceptibles de faire passer le mois sous la marge de sécurité peuvent alimenter les alertes du service `channel_alerts`. Le mécanisme existant exige un canal configuré, l'autorisation de recevoir ses alertes, respecte les heures calmes et évite les envois multiples avec une clé stable. Les pics déjà envoyés par `yearahead.alerts` ne sont pas répétés par le nouveau radar.

### Investissements : première sensibilité documentée

Bank peut maintenant récupérer **les avoirs crypto déjà connus** et les **dernières cotations en euros** dans son propre stockage. Le radar calcule l'impact arithmétique de **chocs hypothétiques de -10 % et -25 %** sur les actifs avec un cours âgé de 48 heures au plus. Les actifs sans prix fiable ou trop ancien sont écartés du montant utilisé et signalés explicitement. Ces deux chocs ne sont ni des prévisions, ni des probabilités, ni une recommandation de vente.

Cette version ne sait pas encore relier chaque ETF, action, obligation ou placement aux entreprises, devises et pays sous-jacents. Le tableau des titres publics reste volontairement séparé des projections de perte afin d'éviter une causalité fabriquée. Le suivi des actifs reste en lecture seule.

### WhatsApp : limite de livraison importante

L'API officielle Meta autorise une réponse libre pendant les **24 heures** suivant le dernier message entrant. Pour envoyer spontanément une alerte plus tard, Bank doit disposer de **modèles WhatsApp approuvés** et du consentement correspondant ; à défaut, Meta peut refuser l'envoi. Le connecteur existant ne contourne pas cette politique. Avant un système de notifications WhatsApp vraiment hors session, il faut intégrer la configuration et le contrôle du modèle approuvé. Telegram, Signal ou les notifications push peuvent aussi être configurés.

## Banques : CRS, non-CRS, FATCA, DAC2, DAC8

La section **CRS / non-CRS des banques** de l'écran « Veille » propose une sélection de **résidence fiscale** et de **juridiction de l'établissement financier**. Le résultat est une explication guidée et un lien vers la source OCDE officielle des relations bilatérales activées.

Il ne faut pas étiqueter une banque « non-CRS » à partir de son IBAN ou de son nom, car :
- le statut dépend des règles nationales, de l'entité bancaire, du type de compte, de la résidence fiscale et de la **relation bilatérale** et de son sens ;
- les États-Unis disposent notamment du dispositif **FATCA**, distinct du CRS ;
- l'absence d'une relation CRS ne supprime pas les obligations locales. En France, le formulaire **3916–3916 bis** peut être exigé pour des comptes ou actifs numériques détenus à l'étranger ;
- **DAC8** s'applique depuis le 1er janvier 2026 à la collecte d'informations sur les cryptoactifs relevant de son champ et prévoit les premiers échanges d'informations en 2027.

### Sources officielles

- [OCDE : relations CRS et accords d'échange](https://www.oecd.org/en/topics/sub-issues/international-standards-on-tax-transparency/automatic-exchange-of-information-exchange-relationships.html)
- [Commission européenne : DAC2](https://taxation-customs.ec.europa.eu/taxation/tax-transparency-cooperation/administrative-co-operation-and-mutual-assistance/directive-administrative-cooperation-dac/dac2_en)
- [Commission européenne : DAC8](https://taxation-customs.ec.europa.eu/taxation/tax-transparency-cooperation/administrative-co-operation-and-mutual-assistance/directive-administrative-cooperation-dac/dac8_en)
- [impots.gouv.fr : déclaration des comptes étrangers, formulaire 3916](https://www.impots.gouv.fr/formulaire/3916/declaration-par-un-resident-dun-compte-letranger-ou-dun-contrat-de-capitalisation-o)
- [Meta : politique de messagerie WhatsApp Business](https://whatsappbusiness.com/policy/)

## Pouvoir d'achat du salaire et rentabilité réelle des placements

L'écran Veille inclut désormais l'observatoire **Salaire, pouvoir d'achat et placements** :

- Source d'inflation : dernier IPC **définitif** documenté à la création du module, **septembre 2026 : +3,1 % sur douze mois**, publié le 15 octobre 2026 par l'Insee ([source](https://www.insee.fr/fr/statistiques/9080209)). Cette version **n'extrait pas automatiquement** les prochaines valeurs : date et lien sont visibles, et le résultat est signalé comme ancien après 60 jours. Ce taux est un repère national, pas l'inflation personnelle exacte.
- Le moteur prend les relevés des **comptes courants budgétaires** déjà connectés, ignore les virements internes et les dépenses d'épargne, et compare les **trois derniers mois civils terminés** aux **mêmes trois mois un an plus tôt**. Il exige six mois de salaires classés « salaire » : sinon il n'invente ni baisse ni hausse de revenus. Il affiche revenus observés, hausse nominale, variation de salaire réel (formule multiplicative), salaire nécessaire pour suivre le repère IPC et écart mensuel indicatif.
- Le **poids des dépenses** par rapport au salaire, les dépenses par mois et les catégories qui ont réellement augmenté sont affichés séparément : une hausse des courses n'est **pas automatiquement** une hausse des prix, car quantité, composition du panier et achats exceptionnels peuvent changer.
- Une alerte proactive (canaux déjà approuvés, heures calmes, clés anti-doublons) est éligible seulement lorsque les données des salaires sont complètes, que le repère n'est pas vieux et que le recul réel annuel dépasse 2 % **et** environ 50 €/mois. L'alerte n'inclut aucun salaire en euros en modes confidentialité restreints.
- Le calculateur **rendement réel** permet de saisir un rendement annuel *supposé*, des frais *supposés*, une fiscalité simplifiée sur gains et une inflation de comparaison. Le serveur calcule un rendement nominal net puis un rendement réel composé. Aucune hypothèse n'est envoyée automatiquement : le calcul ne part qu'après clic. Une fiscalité réelle varie avec les produits et enveloppes.
- Pour **choisir** une solution de placement, le plan existant `GET /api/decisions/investment-plan` reste prioritaire : coussin de sécurité, dette chère, projet daté, horizon et profil de risque. La simulation ne recommande ni ticker ni produit vendu et n'exécute aucune transaction.

Références pédagogiques : [AMF — objectifs et horizon](https://www.amf-france.org/fr/espace-epargnants/savoir-bien-investir/cadrer-son-projet/definir-son-objectif), [AMF — rendement, risque et inflation](https://www.amf-france.org/fr/espace-epargnants/savoir-bien-investir/cadrer-son-projet/risques-et-rendements-des-placements).

### Prochain niveau

Pour un modèle de pouvoir d'achat personnel plus précis, connecter l'IPC **par catégorie** avec version/source horodatée et pondérer les postes selon le panier réellement consommé, sans confondre dépenses et prix. Étendre ensuite la comparaison d'investissements à des **fiches de produits vérifiées** (DIC, frais, liquidité, fiscalité, risque, suivi des taux, simulations de pertes), sans promesse de rendement ni achat automatique.

## Points à développer après validation de cette première version

1. Relier **les positions d'investissement réellement identifiées** (types d'actifs, devise, exposition, horizon) à des facteurs de risque documentés. Ne pas traiter une actualité « intéressante » comme une preuve de corrélation sur le portefeuille.
2. Lire les cours depuis un fournisseur de marché autorisé et daté, afficher leur fraîcheur, identifier les périodes sans données, effectuer des analyses rétrospectives et des scénarios bornés. Pas de conseil personnalisé d'arbitrage sans cadre adéquat.
3. Intégrer l'envoi de **templates WhatsApp approuvés** hors de la fenêtre de 24 heures, avec consentement granulaire, révocation, audit des livraisons et résumé sans informations sensibles.
4. Proposer une vérification CRS assistée par banque et pays **seulement lorsqu'une source officielle par juridiction et un statut daté sont établis**. Jamais de fausse liste de banques « invisibles ».

Les décisions financières restent à l'utilisateur ; aucune transaction, déclaration ou message n'est émis sans les contrôles et autorisations du produit.
