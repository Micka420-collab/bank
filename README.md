# Bank

Cockpit financier personnel, auto-hébergé sur nimbus. Il répond chaque matin à une question :
**combien je peux dépenser aujourd'hui sans me mettre dans le rouge**, et surveille le reste.

- **Comptes** : import CSV/OFX de n'importe quelle banque française, ou synchronisation DSP2 (Enable Banking).
- **Budget** : reste à dépenser par jour contraint par la trésorerie (pas de « 216 €/j » qui suppose le salaire déjà arrivé), courbe du mois avec point bas et date de découvert, enveloppes, suggestions.
- **Aides de l'État** : reconnaissance des versements CAF, France Travail, CPAM, MSA, impôts ; alertes de retard ou de changement ; simulation de tes droits par OpenFisca (le moteur officiel) et détection du non-recours.
- **Tes déclarations** : l'actualisation France Travail, la déclaration trimestrielle CAF ou MSA, le renouvellement
  de la Complémentaire santé solidaire, la déclaration de revenus — proposées d'après tes versements, rappelées à
  l'ouverture, avant l'échéance et en cas de retard (alerte urgente), constatées par le versement qui suit ; les
  chiffres de la déclaration trimestrielle sont préparés depuis tes relevés et tes bulletins. Voir
  [docs/DECLARATIONS.md](docs/DECLARATIONS.md).
- **Ce qui est lié** : Bank lit tes contrats dans le détail (bail : loyer, charges, dépôt, clause IRL, assureur
  exigé ; assurances : assureur, type, prime, échéance) et les croise avec tes relevés et tes aides — deux
  assurances habitation dont une vendue par ta banque, un loyer payé au-dessus du bail ou de l'IRL, une prime qui
  monte, un bulletin qui ne colle pas au virement, une déclaration CAF que le versement ne reflète pas ; et des
  pistes statistiques prudentes (huit mois, test par permutation). Chaque constat montre ses pièces et prépare la
  démarche. Voir [docs/CORRELATIONS.md](docs/CORRELATIONS.md).
- **L'année qui vient** : douze mois projetés depuis la fin du mois (revenus réguliers, charges fixes, dépenses
  habituelles), les pics trimestriels et annuels détectés à leur deuxième occurrence et posés à leur mois (assurance
  annuelle, impôts, eau), ceux que tu ajoutes, le point bas de l'année et la **provision mensuelle** qui lisse — que
  Bank propose de réserver avec ton épargne visée. Alerte deux mois avant un pic.
- **Patrimoine** : valeur nette, matelas de sécurité, livrets réglementés (taux au 1er août 2026), objectifs, projection.
- **Décider** : « je peux m'acheter ça ? » répond oui, oui mais, à partir de telle date, ou dans N mois d'épargne
  (matelas d'abord ou au plus vite) ; « où placer mon argent ? » donne un plan par priorité (mois d'avance, dette
  chère, matelas sur LEP / livrets, projets datés, long terme PEA / assurance vie / PER selon ta tranche d'impôt,
  ton profil de risque, ton horizon et tes lignes rouges).
- **Connecteurs** : banques en un clic (DSP2), compteur **Linky** (consommation réelle, facture annuelle estimée,
  rattrapage probable, pics), documents de l'**État** (avis d'impôt, attestations CAF et France Travail lus pour
  leurs chiffres — identifiants jetés), et les agents IA.
- **Bank prend des initiatives** : une fiche à la fois sur la page du jour — « ton loyer, c'est bien 650 € ? »,
  « NETFLIX, 13,49 € chaque mois, c'est voulu ? », « septembre : il t'est resté 540 €, on en met 260 de côté
  chaque mois ? », « ton salaire est arrivé : c'est le moment du virement », « ton accès n'a pas de double
  authentification »… Tu réponds d'un geste (ou « plus tard »), Bank apprend et ne redemande jamais la même chose.
- **Planning de travail → paie prévue** : relie ton agenda (iCal) ; Bank connaît déjà ton employeur, ton jour de
  paie et la paie habituelle depuis tes relevés, il ajoute tes astreintes, heures ou jours planifiés selon tes
  règles (« astreinte = 180 € »), **apprend** les vrais montants sur tes paies passées, et met la paie prévue dans
  la prévision du mois.
- **Ma situation + agent IA** : ta fiche (ce que Bank sait, lu dans tes relevés ou déclaré), un questionnaire
  posé une question à la fois ; un serveur MCP gouverné pour qu'HERMÈS devienne ton gestionnaire d'argent — il
  lit, calcule avec la prévision de Bank et propose, tu valides. Voir [integrations/hermes](integrations/hermes/README.md).
- **Bank dans tes messageries** : WhatsApp, Telegram, Signal, Discord, Slack ou ntfy — tu lui écris, il répond
  et te prévient (relances, gains à confirmer, risque de découvert), sans jamais rien valider à ta place.
  Voir [docs/CANAUX.md](docs/CANAUX.md).
- **Crypto, sous verre** : tes adresses publiques suivies en lecture seule (sommes de contrôle vérifiées, jetons
  inconnus ignorés), tes avoirs de plateforme, la valeur, la courbe et la répartition, l'impôt de l'année
  (305 €, 2086, 3916-bis), un vérificateur d'arnaques — et aucune phrase de récupération ni clé acceptée, nulle
  part. Voir [docs/CRYPTO.md](docs/CRYPTO.md).
- **Pilote automatique** (éteint par défaut) : ton agent peut acheter ou envoyer seul, mais seulement dans ton
  cadre — un jeton précis, tes actifs, tes adresses, tes consignes, des plafonds glissants, une perte maximale,
  une durée ; Bank autorise ou refuse chaque mouvement avant qu'il parte et coupe tout à la première anomalie.
  « Tout arrêter » d'un geste, ou `/pilote stop` (voire « stop pilote ») depuis ta messagerie.
- **Passkeys** : Face ID, empreinte ou clé de sécurité pour te connecter sans mot de passe, résistant au phishing
  (Réglages › Sécurité, mot de passe exigé pour en ajouter ou en retirer ; le mot de passe reste en secours et pour
  les gestes sensibles).
- **Un chat qui agit** : l'assistant prend l'initiative (il propose de relier ce qui manque — banque, bail,
  assurances, bulletins, Linky… — avec ce que ça débloque), répond avec des boutons, des graphiques dessinés par
  Bank avec tes chiffres, des schémas, et cherche sur le web (SearXNG chez toi ou Brave, sous ton accord) en
  citant ses sources. Chaque bloc est construit et validé par le serveur, jamais par le modèle seul.
- **Pensé pour le téléphone** : réponses de l'assistant en direct (le texte arrive au fil de l'eau, avec ce qu'il
  fait — « cherche sur le web », « dessine le graphique »), chat en feuille qui reste au-dessus du clavier, bouton
  flottant qui s'efface quand tu lis, cibles tactiles larges, installation sur l'écran d'accueil, bandeau hors
  ligne — et des **notifications sur le téléphone** (Web Push, chiffrées pour ton appareil, sans messagerie ni
  compte tiers) pour le découvert qui approche, la déclaration à faire, le gain à confirmer, le pilote arrêté.
- **Simple et ludique** : cinq destinations (Aujourd'hui, Opérations, Gagner, Papiers, Épargne), des
  missions avec leur récompense, une tirelire qui se remplit avec ce que Bank t'a vraiment fait récupérer.
- **Une interface de produit fini** : design system « Encre claire » (clair et sombre, choix dans Réglages),
  première ouverture guidée (intro, accès, banque en un clic avec tuiles aux couleurs de chaque enseigne,
  synchronisation visible, confirmation des comptes — « je connecterai mes comptes plus tard » toujours
  possible), tableau de bord avec courbe interactive (7 j / 30 j / 3 mois / 1 an / tout), répartition, reçu vs
  dépensé, anomalies et dernières opérations ; tout fonctionne au téléphone, au clavier et sans animation si tu
  l'as demandé au système. Voir [docs/DESIGN.md](docs/DESIGN.md).

## Architecture

```
navigateur ──HTTPS──> ton reverse proxy (nimbus) ──> web : nginx, CSP stricte, rate-limit
HERMÈS ─────HTTPS + jeton bnk_… ──┘ (/mcp)              │ réseau « app » (interne)
                                                        ▼
                                                 api : FastAPI + SQLite (volume chiffré par champ)
                                                   │ réseau « fiscal » (interne)   │ réseau « egress »
                                                   ▼                               ▼
                                         fiscal : OpenFisca France        api.enablebanking.com (banques)
                                         (aucun accès Internet)           conso.boris.sh (Linky / Enedis)
                                                                          openrouter.ai (assistant, si activé)
                                                                          ton agenda iCal (https, hôte public)
```

| Dossier | Contenu |
|---|---|
| `backend/app/domain` | Cœur métier pur, sans I/O : montants, normalisation des libellés, catégorisation, récurrences, prévision, aides, patrimoine, décisions, planning → paie, initiatives (cartes) |
| `backend/app/importers` | CSV toutes banques (auto-détection), OFX 1.x/2.x sans parseur XML |
| `backend/app/security` | Chiffrement AES-256-GCM par champ, index aveugles, Argon2id, TOTP, sessions, audit chaîné HMAC, middlewares |
| `backend/app/providers` | Client Enable Banking (JWT RS256) |
| `backend/app/fiscal` | Construction de la situation OpenFisca à partir de tes vrais relevés |
| `backend/app/domain/{invest,energy,documents}.py` | Plan de placement (barème 2026, priorités, lignes rouges), analyse Linky (formats Enedis tolérés, coût, régularisation), lecture des documents officiels |
| `backend/app/providers/conso_api.py` | Client Conso API (Linky), fenêtres bornées, erreurs explicites |
| `backend/app/mcp_server.py` | Serveur MCP des agents IA : jeton par appel, portées, mandat, audit, débit borné |
| `backend/app/services/advisor.py` | Dossier de l'agent (provenance de chaque fait), alertes, onboarding |
| `backend/app/domain/{onboarding,decisions,redact}.py` | Questionnaire, verdicts d'achat / d'épargne, anonymisation des libellés |
| `integrations/hermes/` | Compétence HERMÈS, configuration MCP, routines, script d'installation |
| `fiscal-engine/` | Micro-service OpenFisca fermé (liste blanche de variables, jeton) |
| `frontend/` | React 19 + TypeScript, PWA, zéro dépendance d'UI, police auto-hébergée |

## Installation sur nimbus

```bash
cp .env.example .env          # renseigne BANK_PUBLIC_URL et BANK_ALLOWED_HOSTS
make secrets                  # clé maîtresse, jeton d'installation, jeton fiscal (./secrets, mode 640)
make build                    # derrière un proxy TLS d'entreprise : EXTRA_CA=/chemin/ca.crt make build
make up                       # affiche le jeton d'installation
```

Expose ensuite `127.0.0.1:8088` via ton reverse proxy en HTTPS. Exemple Caddy :

```
bank.nimbus.lan {
    reverse_proxy 127.0.0.1:8088 {
        header_up X-Forwarded-Proto https
    }
}
```

Ouvre l'adresse, crée ton accès avec le jeton d'installation, puis **active la double authentification** dans Réglages.

> Sauvegarde `secrets/master_key` hors de nimbus (gestionnaire de mots de passe). Sans elle, les IBAN, le profil
> et les sessions bancaires chiffrés sont irrécupérables, même depuis une sauvegarde.

## Connecter ta banque (DSP2, gratuit pour un usage personnel)

GoCardless Bank Account Data n'accepte plus de nouveaux comptes depuis juillet 2025. Enable Banking propose un
**mode restreint gratuit** réservé à l'usage personnel : ton application ne voit que les comptes que tu as liés toi-même.

1. Crée un compte sur le Control Panel Enable Banking et enregistre une application **Production**.
2. Redirect URL : `https://bank.nimbus.lan/api/banks/callback` (exactement ton `BANK_PUBLIC_URL` + `/api/banks/callback`).
3. Télécharge la clé privée générée et place-la dans `secrets/enablebanking.pem` (`chmod 640`, groupe 10010).
4. Clique « Activate by linking accounts » et lie tes comptes : l'application passe en mode restreint actif.
5. Dans `.env` : `BANK_ENABLEBANKING_APP_ID=<id>` et décommente `BANK_ENABLEBANKING_KEY_FILE`.
6. `docker compose up -d` puis, à la première ouverture, l'étape « Connecte ta banque » (ou plus tard :
   Connecteurs → Banques → Ajouter une banque).

Synchronisation automatique toutes les 8 h (la DSP2 limite à 4 accès non sollicités par jour). Le consentement
dure 90 jours ; Bank t'indique quand le renouveler. Un compte déjà importé en CSV est retrouvé par son IBAN
(index aveugle) : l'historique est fusionné et les doublons rapprochés, pas recréés.

## Importer des relevés

Opérations → Importer un relevé. Bank détecte seul l'encodage, le séparateur, les lignes d'en-tête parasites,
les colonnes (montant unique ou débit/crédit) et les formats de date. Un aperçu est toujours montré avant l'import.
Réimporter un fichier, ou un fichier qui chevauche le précédent, n'ajoute aucun doublon.

## Simulation des aides

Le profil du foyer (date de naissance, commune, logement, loyer, enfants…) est chiffré en base. Les revenus sont
repris de tes relevés ; renseigne ton revenu net imposable N-2 (avis d'impôt) pour fiabiliser l'APL et le LEP, qui
reposent sur les revenus fiscaux d'il y a deux ans. Les résultats sont indicatifs : seul l'organisme payeur confirme
tes droits.

OpenFisca France est sous licence AGPL-3.0 : rien à faire tant que le service reste à ton seul usage.

## L'année qui vient

Sur la page du jour, sous la courbe du mois : une phrase (« année sans découvert, point bas 1 240 € en mars »), et en
dépliant, douze barres de solde de fin de mois, les pics posés à leur mois avec leur source (détecté à sa deuxième
occurrence, ajouté par toi, dépense prévue à l'accueil), la provision mensuelle qui les lisse. « Écarter » retire un
pic détecté à tort ; « Ajouter » pose une dépense annuelle ou trimestrielle que les relevés ne montrent pas encore.
Quand la provision n'est pas couverte par ton épargne visée, Bank te propose de la réserver d'un geste (elle sort
alors du reste à dépenser du jour, et le pic ne surprend plus). Ton agent lit la même projection (`get_year_ahead`).

## Ce qui est lié (corrélations)

Bank › Plus › Ma situation › **Ce qui est lié**. Un bail déposé dans Papiers est lu pour ses petites lignes
(loyer, charges, dépôt, clause de révision IRL, assurance exigée et assureur nommé, préavis) ; une attestation
d'assurance pour son assureur — y compris Pacifica, ACM, Sogessur, Cardif et les autres assureurs des banques —,
son type et sa prime. Bank croise ensuite avec tes prélèvements : deux assurances habitation → doublon chiffré par
an et lettre de résiliation préremplie ; loyer payé au-dessus du bail → hausse non due sans clause, ou plafond IRL
calculé (indice INSEE embarqué et daté) ; prime, box ou abonnement qui monte → le droit de partir qui va avec ;
bulletin ≠ virement, quittance ≠ loyer, ressources déclarées à la CAF ≠ versement qui suit, mensualité d'énergie ≠
consommation Linky. Les pistes statistiques (revenu ↔ catégorie, dépenses de la semaine de paie) ne parlent
qu'avec huit mois et un test par permutation, et jamais comme des causes. Détails :
[docs/CORRELATIONS.md](docs/CORRELATIONS.md).

## Déclarations à ne pas oublier

Bank › Gagner › Aides › **Tes déclarations**. Un versement France Travail reconnu dans tes relevés fait proposer
l'actualisation mensuelle ; une prime d'activité ou un RSA, la déclaration trimestrielle de ressources ; la
déclaration de revenus concerne tout le monde. Tu acceptes d'un geste sur la page du jour, Bank calcule les
fenêtres (du 28 au 15 du mois suivant pour France Travail ; le mois de déclaration que tu lui indiques, tous les
trois mois, pour la CAF ; la date limite de ton département pour les impôts), te prévient dans l'application et
tes messageries, et **constate** que c'est fait quand le virement qui suit arrive — sans virement, alerte urgente.
Pour la déclaration trimestrielle, « Mes chiffres » donne les trois mois de référence par type de ressource, chaque
montant ouvert sur ses opérations, le montant net social quand un bulletin a été lu dans Papiers, et ce qui ne se
déclare pas. Rien n'est envoyé : tu déclares sur le site de l'organisme. Règles datées et sourcées dans
[docs/DECLARATIONS.md](docs/DECLARATIONS.md).

## Connecteurs

- **Banques** : Connecteurs › Banques, tuiles des enseignes courantes (une caisse à choisir pour Crédit Agricole,
  Caisse d'Épargne, Banque Populaire, Crédit Mutuel), ou recherche parmi tous les établissements.
- **Linky** : donne ton consentement chez Enedis via [conso.boris.sh](https://conso.boris.sh) (passerelle gratuite et
  open source), colle le jeton reçu et ton PRM (14 chiffres, sur la facture). Bank récupère 400 jours, puis se met à
  jour chaque nuit ; le jeton et le PRM sont chiffrés en base ; révocable ici ou chez Enedis. Enedis a changé son
  API fin 2026 : Bank lit l'ancien format et toute structure (date, valeur, unité) du nouveau ; en cas de format
  inconnu il le dit au lieu d'inventer.
- **Planning de travail** : colle l'adresse privée iCal de ton agenda (Google Agenda › « Adresse secrète au
  format iCal », Outlook › « Publier ») ou dépose un .ics. Bank garde les journées et les titres (chiffrés),
  relit l'adresse chaque nuit, et calcule la prochaine paie : part fixe (la paie habituelle lue dans tes relevés)
  + règles par mot-clé (par événement, par jour ou par heure). Dès quatre paies connues, il apprend les montants
  réels par régression et les utilise s'ils collent à la réalité (écart médian < 5 %). La paie prévue remplace
  la médiane dans la prévision du mois ; HERMÈS la voit (`work`), jamais l'agenda.
- **État** : pas d'API ouverte aux particuliers (FranceConnect est réservé aux services agréés). Télécharge l'avis
  d'impôt (impots.gouv), l'attestation CAF ou France Travail, dépose le PDF : Bank montre les chiffres lus (revenu
  fiscal de référence, parts, taux de prélèvement ; droits CAF ; allocation et fin de droits), tu corriges, tu
  valides. Numéro fiscal, NIR, numéro d'allocataire, adresse : vus et jetés, jamais enregistrés. La tranche d'impôt
  et le revenu N-2 de la simulation des aides en découlent ; une fin de droits proche déclenche une alerte. Le PDF
  est lu dans un processus séparé et confiné (CPU, mémoire, délai, sans secret ni réseau) : un fichier piégé ne
  peut rien atteindre.

## Bank te demande (initiatives)

Bank ne t'impose pas un formulaire : il remarque, demande une chose à la fois en disant pourquoi, et se tait
quand tu dis « plus tard ». Les cartes viennent de ce qu'il lit : faits détectés à confirmer (salaire, loyer,
crédits, foyer), prélèvements réguliers à reconnaître une fois (abonnements), marchands non classés (un geste
classe tout et apprend la règle), changement de salaire, bilan du mois dans la première semaine (avec une épargne
mensuelle proposée), « se payer en premier » le lendemain de la paie, bonnes nouvelles (trois mois d'historique,
matelas constitué, objectif atteint), hygiène (double authentification, consentement bancaire qui expire, Linky
muet, avis d'impôt de l'année disponible), et le planning (relier l'agenda, régler les règles, paie prévue).
Chaque réponse est journalisée ; l'état (faite, remise à plus tard, écartée) est conservé par carte.

Première ouverture : trois chemins — connecter sa banque, importer un relevé (le compte se crée sur place), ou
« juste trois chiffres » (solde, revenu, loyer) pour un premier budget en trente secondes.

## Assistant « Demande à Bank » (OpenRouter)

Un modèle de langage, choisi parmi ceux d'[OpenRouter](https://openrouter.ai) et payé avec **tes** crédits, répond
à tes questions à partir de ton dossier (bouton « Demande à Bank » dans la barre latérale, bouton flottant sur
mobile) et propose des enveloppes pour les marchands non classés (Opérations, ou directement dans les cartes du jour).

1. Crée une clé sur openrouter.ai › Keys et ajoute quelques dollars de crédits.
2. Bank › Connecteurs › **Assistant** : colle la clé (on te redemande ton mot de passe), choisis le modèle, ce qu'il
   voit et tes plafonds.
3. « Tester » vérifie que la clé répond (un appel de 5 jetons).

Ce que le modèle voit : par défaut le dossier **anonymisé** (marchands et montants, jamais un nom de particulier,
un IBAN ou un numéro), ou **chiffres seuls** (aucun libellé), ou complet si tu le choisis. Ce que Bank garde : la
clé chiffrée (jamais réaffichée, seul son début « sk-or-v1-ab12… » sert à la reconnaître), la conversation en cours
(chiffrée), et pour chaque appel le modèle, les jetons et le coût — le journal d'audit ne contient jamais le texte
échangé. Deux plafonds (crédits par mois, appels par jour) l'arrêtent avant ta limite, relecture comprise. Il
explique et propose ; il ne classe ni ne modifie rien sans ton clic.

### Un chat qui agit : boutons, graphiques, schémas, recherche web

L'assistant ne se contente pas de répondre ; il **prend l'initiative** et rend sa réponse actionnable :

- **Il propose de relier ce qui manque.** Bank calcule ce qu'il sait et ce qu'il ne sait pas encore (banque, relevés,
  bail, assurances, bulletins, avis d'impôt, déclarations suivies, Linky, crypto, situation, messageries, sécurité) et
  ce que chaque connexion débloquerait. La conversation s'ouvre là-dessus — sans appel au modèle, donc gratuit et
  instantané — avec des boutons « Connecter ma banque », « Déposer mon bail »… ; et quand une question touche
  quelque chose d'absent (ton loyer sans bail, par exemple), l'assistant le dit et propose le bouton. Le même
  modèle de couverture est exposé aux agents (`get_coverage`).
- **Des boutons sous les réponses**, comme dans un chat moderne : ouvrir l'écran utile, agir sur une carte « Bank
  te demande » (suivre une déclaration, par exemple — par l'API des initiatives, avec ses contrôles), ou poser la
  question suivante d'un clic. Le modèle ne choisit que dans un **catalogue** calculé par Bank à chaque question :
  une clé inventée est ignorée, un bouton mène toujours à un écran qui existe.
- **Des graphiques dessinés par Bank, avec ses chiffres** : répartition des dépenses, reçu/dépensé par mois, solde,
  aides par organisme, année qui vient, patrimoine, crypto, électricité. Le modèle demande un jeu de données et
  une période ; Bank dessine et lui rend les chiffres pour qu'il les commente — jamais l'inverse.
- **Des schémas** quand il faut expliquer un mécanisme (comment une aide se calcule, les étapes d'une démarche,
  d'où vient le reste à dépenser) : boîtes et flèches ou suite d'étapes, bornés (douze boîtes, pas de lien), et
  présentés pour ce qu'ils sont : proposés par le modèle, à lire avec tes chiffres.
- **La recherche web**, pour une règle, un barème ou une actualité : avec le moteur que tu branches dans
  Connecteurs — **SearXNG** chez toi (format JSON activé) ou **Brave Search** (clé chiffrée) — et sous ton accord
  « Recherche web par l'assistant ». La requête, rédigée par le modèle, est nettoyée avant de partir (jamais
  d'IBAN, de numéro, d'e-mail, de téléphone ni de nom ; un secret l'arrête net) ; les pages sont lues depuis ton
  serveur par le téléchargeur durci (https, adresses publiques seulement), et le modèle ne choisit jamais une
  adresse, seulement un numéro de résultat. Les sources s'affichent sous la réponse, citées [1], [2]. Trois
  recherches par question, un plafond par jour, et un journal qui compte sans jamais garder la requête.

Tout bloc (boutons, graphique, schéma, sources) est construit et validé par le serveur, conservé chiffré avec la
conversation, et redevient du texte dans une messagerie (les boutons en indications, les sources en liens).

La réponse arrive **en direct** : le texte au fil de l'eau (flux SSE, `POST /api/assistant/ask/stream`), des
statuts qui disent ce que l'assistant fait (« lit ton dossier », « cherche sur le web », « dessine le graphique »),
un « reset » quand ce qui a été montré précédait un appel d'outil, puis la réponse complète avec ses blocs. Un
navigateur ou un serveur qui ne sait pas diffuser retombe sur la réponse en un bloc.

### Notifications sur ton téléphone (Web Push)

Réglages › **Notifications sur ce téléphone** › Activer : Bank te prévient par les notifications du navigateur —
découvert qui approche, déclaration à faire, gain à confirmer, pilote arrêté, point du dimanche — sans messagerie
ni compte tiers. Chaque message est chiffré pour ton appareil (RFC 8291, vérifié au bit près contre le vecteur de
la norme) et signé par ton serveur (VAPID, RFC 8292) : le service de notification de l'éditeur du navigateur
(Google, Apple, Mozilla) ne voit qu'un contenu illisible et l'adresse de ton serveur. Les appareils abonnés suivent
le circuit des messageries (heures calmes, une seule fois, pause après trop d'échecs) ; un abonnement mort retire
l'appareil ; huit appareils au plus ; accord « Notifications sur ce téléphone » révocable à tout moment. Sur
iPhone, il faut d'abord ajouter Bank à l'écran d'accueil (Safari › Partager › Sur l'écran d'accueil).

### Un assistant qui se souvient de toi (à la manière d'Hermes Agent)

Avec ton accord (demandé une fois, révocable dans Réglages › Confidentialité), l'assistant devient un compagnon
qui apprend — tu peux même lui donner un prénom :

- **Deux carnets courts** : « qui tu es » (situation, projets, préférences, façon de parler d'argent) et « ton
  environnement financier » (habitudes, contraintes, ce qui marche). Ils sont relus au début de chaque conversation.
- **Pendant la conversation**, il note ce qui compte (« fais plus court », « je pars au Japon en 2027 ») : chaque
  écriture s'affiche sous sa réponse, avec **Annuler**. Il peut aussi retrouver une conversation passée et faire
  calculer un achat par Bank (« je peux m'acheter… ? »).
- **Relecture** toutes les 8 questions et à la fin d'une conversation : il retient ce qui mérite de l'être, en
  tenant compte de tes 👍 / 👎. Elle ne fait qu'**ajouter** ; corriger ou effacer t'attend dans « À valider ».
- **Tout est visible** dans « Ce que Bank retient » (Plus › Mémoire, ou l'icône du panneau) : corriger, épingler
  (il ne pourra plus y toucher), effacer, relire ou supprimer une conversation, choisir combien de temps les garder,
  tout oublier (mot de passe demandé).
- **Jamais** d'IBAN, de numéro de carte, de sécurité sociale, de téléphone, d'e-mail, de code ni de consigne cachée :
  filtrés à l'écriture **et** à la relecture. En mode « chiffres seuls », pas de mémoire du tout.

## Coffre-fort de documents

Bank › **Documents** : prends en photo un bulletin de paie, une attestation, un RIB — ou dépose des PDF, jusqu'à 10
d'un coup. Chaque fichier est chiffré dès réception puis lu **chez toi** : texte des PDF, et pour les photos et les
scans, reconnaissance de texte (Tesseract, en français), le tout dans des processus confinés.

- **13 types reconnus** : bulletin de paie, avis d'impôt, attestations CAF et France Travail, facture, quittance de
  loyer, bail ou état des lieux, relevé de compte, RIB, assurance, pension de retraite, pièce d'identité et document
  de santé (ces deux derniers sont « sensibles » : gardés seulement avec ton accord, jamais envoyés à un modèle).
- **Chaque valeur avec sa preuve** : la ligne exacte où elle a été lue s'affiche à côté. Ce qui manque reste
  « non trouvé » — rien n'est deviné. Tu corriges d'un clic (la valeur lue reste visible), puis « C'est bon ».
  Un bulletin dont le net payé ne colle pas avec le net avant impôt moins le prélèvement est signalé.
- **Utiliser ces chiffres dans Bank** : un avis d'impôt, une attestation CAF ou France Travail alimentent ta
  tranche d'impôt, tes aides et la simulation de tes droits.
- **Lecture assistée** (facultative) : quand la lecture locale ne trouve pas tout, ton modèle OpenRouter peut
  compléter — avec ton accord, document par document, identifiants masqués, et chaque valeur doit citer mot pour
  mot le passage du document, sinon elle est écartée.
- **Combien de temps garder quoi** : la durée conseillée par Service-Public.fr s'affiche pour chaque document ;
  les dates d'expiration (pièce d'identité, assurance, fin de droits France Travail) sont surveillées.
- **Photos sans traces** : position GPS, appareil et date de prise de vue sont retirés ; l'original n'est pas gardé.
- **Portabilité** : export de tout le coffre en .zip (fichiers + valeurs + preuves), protégé par ton mot de passe.

## Opportunités : ce que tu pourrais gagner, chiffré honnêtement

Bank › **Opportunités** liste ce que tu pourrais récupérer, payer en moins, gagner, mieux placer ou anticiper,
tiré de tes comptes, de tes documents et de règles datées : écart entre tes droits estimés et tes aides perçues,
Complémentaire santé solidaire face à ta mutuelle, frais bancaires au-delà des plafonds légaux, abonnements,
livret d'épargne populaire, épargne qui dort, découvert prévu, papiers qui expirent.

- **Classées par gain net attendu** : ce que ça peut rapporter sur un an × la chance que ça marche − le temps que
  ça te prend (une heure de ton temps compte pour 15 €, réglable).
- **Expliquées** : pour chaque piste, ce qui est *constaté* dans tes données, ce qui est *estimé* par Bank, ce qui
  est *supposé*, et les sources avec leur date de vérification.
- **Un clic pour préparer la démarche** (préremplie), « Plus tard » ou « Pas pour moi ».
- **Ce que Bank t'a fait gagner** : l'*obtenu* (constaté sur tes comptes, confirmé par toi) et l'*en cours*
  (estimé) côte à côte, jamais additionnés. Quand un remboursement arrive ou qu'un prélèvement s'arrête, Bank te
  le propose ; c'est toi qui confirmes.

Bank n'est pas un conseiller financier agréé : ce sont des repères et des calculs, jamais des promesses.

## Démarches : Bank prépare, tu transmets

Bank › **Démarches** : prime d'activité (caf.fr), Complémentaire santé solidaire (ameli), réclamation de frais
bancaires, résiliation d'un abonnement, ouverture d'un LEP, ou une démarche libre à suivre.

1. **Préparer** : les valeurs sont préremplies depuis ton profil, tes comptes et ton coffre (tes trois derniers
   bulletins : montant net social), chacune avec sa provenance et la ligne du document qui la prouve.
2. **Vérifier** : une valeur qui contredit son justificatif bloque le dossier jusqu'à ce que les deux concordent ;
   les pièces viennent du coffre (une pièce non vérifiée bloque aussi).
3. **Relire et valider** : un écran récapitule exactement ce qui partira ; chaque conséquence et chaque engagement
   se coche un par un ; ta validation porte sur cette version précise — un changement l'annule.
4. **Transmettre** : c'est toi, sur le site officiel (étapes guidées, sources datées) ou par lettre (copie,
   téléchargement), puis « Je l'ai transmis » — compté une seule fois.
5. **Suivre** : prochain point daté, relance, médiateur bancaire après deux mois sans réponse, résultat.

Bank ne transmet jamais rien à ta place, ne te demande jamais un mot de passe ni ton numéro de sécurité sociale
(tu les saisis sur le site officiel).

## Veille : ce qui change, pour toi

Bank › **Veille** relève les actualités du ministère de l'Économie et les versions du moteur de calcul des aides
(OpenFisca-France, comparé à la version qui tourne chez toi), plus les flux RSS que tu ajoutes. Chaque élément
garde sa source, sa date de publication, sa dernière vérification et ses versions ; ceux qui touchent à ta
situation passent en premier (le rapprochement se fait sur ton serveur, rien ne part). Si une règle des aides
change après ta simulation, une piste te propose de la relancer. Les taux, barèmes et sources que Bank utilise
affichent leur date de prochaine vérification et passent « à revérifier » quand elle est dépassée. La veille se
coupe dans Réglages › Confidentialité.

## Messageries : Bank dans WhatsApp, Telegram, Signal, Discord, Slack et ntfy

Bank › **Messageries** : écris à Bank depuis ta messagerie (« je peux m'offrir un resto ce soir ? », `/budget`,
`/missions`, `/demarches`) et reçois-y ses alertes — démarche à relancer, gain à confirmer, risque de découvert,
nouveauté officielle pour toi, point du dimanche soir — regroupées, une seule fois chacune, hors de tes heures calmes.

- **Telegram, Discord, Slack, Signal** se connectent vers l'extérieur (long polling, websocket, Socket Mode,
  signal-cli) : rien à ouvrir sur internet. **WhatsApp** passe par l'API officielle de Meta : un seul chemin public,
  signature vérifiée avant toute lecture. **ntfy** : juste des notifications sur ton téléphone.
- Un inconnu n'obtient **aucune** réponse : tu relies tes comptes par un code à usage unique, généré après ton mot
  de passe. Jetons chiffrés, jamais réaffichés ; identifiants de compte (numéro de téléphone) chiffrés.
- Rien ne se valide depuis une messagerie, et Bank ne demande jamais un mot de passe : un secret collé par erreur
  est refusé avant d'aller où que ce soit, et les identifiants (IBAN, carte, n° fiscal…) sont retirés de chaque
  message sortant. Confidentialité réglable par messagerie (Signal : chiffré de bout en bout).

Guide pas à pas par messagerie, exposition du webhook WhatsApp et service signal-cli : [docs/CANAUX.md](docs/CANAUX.md).

## Crypto et pilote automatique

Bank › Épargne › **Crypto** : ajoute une adresse publique (celle qu'on te donne pour *recevoir*) ou saisis ce que
tu as sur une plateforme. Avec ton accord « Suivi de tes adresses crypto », Bank relève les soldes auprès de
nœuds publics (ou du tien) et les cours chez CoinGecko ; sans, tout marche à la main. La crypto compte dans ton
patrimoine sur une ligne à part, jamais dans l'argent disponible du budget.

Bank › Épargne › **Pilote** : le cadre dans lequel ton agent peut agir sans te redemander (mot de passe et code
de double authentification pour le régler), les jauges de ce qui est déjà engagé, et le journal de chaque
demande, refus compris. Mode essai conseillé pour commencer. Détails, sources fiscales et garanties :
[docs/CRYPTO.md](docs/CRYPTO.md).

## Brancher HERMÈS (ton agent IA)

1. Bank › **Ma situation** : confirme ce que Bank a lu dans tes relevés, réponds aux questions les plus utiles.
2. Bank › Réglages › **Agents IA** : crée un jeton (rôle *Conseiller*, confidentialité *Anonymisé*, IP d'HERMÈS).
3. Sur la machine d'HERMÈS : `integrations/hermes/install.sh --routines`, puis `/reload-mcp`.
4. Demande-lui « fais mon onboarding financier », puis règle son **mandat** dans Bank (lui ne peut pas).

Il ne peut ni changer son mandat, ni appliquer un réglage sans ton clic, ni bouger d'argent sans ta confirmation —
sauf, si tu l'as allumé, dans le cadre du pilote automatique. Détails, outils et dépannage :
[integrations/hermes/README.md](integrations/hermes/README.md).

## Ce qui vient ensuite

La feuille de route est dans [docs/ROADMAP.md](docs/ROADMAP.md) et dans les
[issues](https://github.com/Micka420-collab/bank/issues) du dépôt : calendrier des obligations (actualisation France
Travail, déclaration trimestrielle CAF), déclaration pré-calculée, corrélations entre tes contrats et tes relevés,
prévision à 12 mois, passkeys… chacune avec ses garde-fous.

## Développement

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "backend[dev]"
(cd frontend && npm ci)
make secrets
make demo        # base fictive : micka / demo demo demo 42
make dev-api     # http://127.0.0.1:8000  (docs : /api/docs en mode dev)
make dev-web     # http://localhost:5173
make check       # lint + mypy strict + bandit + tests + build front
make fiscal-test # contre le vrai OpenFisca
```

Qualité au moment de la livraison : 902 tests backend (unitaires, propriétés Hypothesis, intégration HTTP,
JSON-RPC MCP de bout en bout, OCR réel, scénarios du copilote A à F, messageries face à de faux serveurs fidèles
aux protocoles — passerelle Discord et Socket Mode Slack sur de vrais websockets locaux —, scénarios adverses du
banc d'essai crypto de Minara rejoués contre le pilote automatique, demandes simultanées qui ne passent jamais
ensemble sous un plafond, Keccak-256 vérifié contre une implémentation de
référence, intégration OpenFisca réelle, passkeys face à un authentificateur simulé, transaction validée avant
l'envoi de chaque réponse, chat interactif face à un modèle simulé et à de faux moteurs de recherche, réponse en
direct recomposée fragment par fragment, Web Push vérifié au bit près contre le vecteur de la RFC 8291 et déchiffré
par un faux navigateur), couverture 92 %, 123 tests front (parcours de première ouverture, coffre, démarches,
opportunités, veille, assistant, chat interactif et réponse en direct, notifications sur l'appareil, crypto,
pilote, déclarations, corrélations, année qui vient, passkeys, erreurs qui disent quoi faire),
mypy strict sans erreur,
ruff et bandit propres, revue de sécurité indépendante du pilote et de la crypto (ses constats corrigés et
testés), `pip-audit` et `npm audit` sans vulnérabilité connue, aucune erreur de console ni débordement
horizontal à 390 px (vérifié par Playwright), et un parcours complet dans Chromium à travers les conteneurs durcis,
derrière la CSP de production et un frontal TLS : accueil, seize écrans, démarche ouverte, phrase de récupération
arrêtée dans le navigateur, vérificateur d'arnaques, thème sombre — aucune violation CSP, cookies
`Secure`/`HttpOnly`/`Strict`. La relève crypto a aussi été essayée contre les vrais nœuds publics (mempool.space,
PublicNode, CoinGecko, alternative.me).

## Sauvegarde

```bash
make backup                                        # copie cohérente à chaud dans le volume
docker compose cp api:/data/backups ./backups
age -r <ta-clé-publique> -o bank.db.age backups/bank-*.db   # toujours chiffrer avant de sortir de nimbus
```

Restaurer : arrêter la pile, remplacer `/data/bank.db` dans le volume par la sauvegarde, relancer avec la **même**
clé maîtresse.

## Rotation de la clé maîtresse

`secrets/master_key` accepte plusieurs clés : `k2:<nouvelle>,k1:<ancienne>`. La première chiffre, toutes déchiffrent.
Les index aveugles (IBAN) dépendent de la clé active : après rotation, réenregistre les IBAN depuis Réglages.

Voir aussi [SECURITY.md](SECURITY.md).
