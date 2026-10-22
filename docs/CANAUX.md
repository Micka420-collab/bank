# Messageries : Bank dans WhatsApp, Telegram, Signal, Discord, Slack et ntfy

Bank › **Messageries** te permet d'écrire à Bank depuis la messagerie que tu utilises déjà (« je peux m'offrir un
resto ce soir ? », `/budget`, `/missions`…) et d'y recevoir ses alertes : une démarche à relancer, un gain à
confirmer, un risque de découvert, une nouveauté officielle qui te concerne, le point du dimanche soir.

La conception reprend la passerelle d'[Hermes Agent](https://github.com/NousResearch/hermes-agent) (un adaptateur
par plateforme, refus par défaut des inconnus, appairage par code, dédoublonnage des réessais, signature vérifiée
avant toute lecture, découpe des messages qui respecte les blocs de code). Elle est réduite à ce qu'exige une
application financière : messages privés seulement, aucune décision depuis une messagerie.

## Ce que Bank fait dans une messagerie, et ce qu'il ne fait jamais

| Il fait | Il ne fait jamais |
|---|---|
| Répondre à tes questions, avec les chiffres de Bank et la mémoire de l'assistant si tu l'as activée | Transmettre une démarche, confirmer un gain, changer un réglage, allumer le pilote automatique : tout se valide dans l'application |
| `/budget`, `/missions`, `/demarches`, `/declarations`, `/pilote` sans même avoir besoin de l'assistant ; **arrêter** le pilote automatique (`/pilote stop`) | Te demander un mot de passe, un code, un numéro de carte, une phrase de récupération ou une clé privée. S'il en repère un dans ton message, il le refuse : le message ne va ni au modèle ni dans l'historique |
| Te prévenir une seule fois par événement, en un seul message qui regroupe tout, hors de tes heures calmes | Répondre à un inconnu : un compte non relié n'obtient **aucune** réponse, il ne sait même pas que ce robot est relié à Bank |
| Écrire IBAN, numéro de carte, de sécurité sociale, d'allocataire ou fiscal ? Non : ils sont retirés de tout message sortant | Lire un groupe ou un salon : seulement les messages privés, et jamais ceux d'un autre robot |

## Quelle messagerie choisir ?

| | Qui peut lire ce que Bank t'écrit | Adresse publique à ouvrir | Mise en place |
|---|---|---|---|
| **Signal** | Toi seul (chiffré de bout en bout) | Non | Un service signal-cli et un numéro réservé à Bank |
| **Telegram** | Toi et les serveurs de Telegram | Non (long polling) | 2 minutes avec @BotFather |
| **Discord** | Toi et les serveurs de Discord | Non (websocket sortant) | 5 minutes |
| **Slack** | Toi, les serveurs de Slack et les administrateurs de l'espace | Non (Socket Mode) | 10 minutes |
| **WhatsApp** | Toi et les serveurs de Meta | **Oui**, un seul chemin | 20 minutes ; alertes seulement dans les 24 h qui suivent ton dernier message (règle de Meta) |
| **ntfy** | Toi et le serveur ntfy (le tien ou ntfy.sh) | Non | 1 minute, alertes seulement |

Pour des montants qui ne regardent que toi : **Signal**, ou réglage « Chiffres seuls » sur les autres.

## Mise en route (la même pour toutes)

1. Bank › Messageries : donne ton accord « Bank dans tes messageries » (révocable : l'effet est immédiat, tout
   s'arrête en quelques secondes).
2. Choisis la messagerie, suis les étapes affichées, colle les jetons demandés, **Enregistrer et activer**. Les
   jetons sont chiffrés en base et ne sont jamais réaffichés : un préfixe (`123456…`) sert à les reconnaître.
3. **Relier ton compte** : confirme ton mot de passe, Bank affiche un code à usage unique (8 caractères, valable
   15 minutes, 5 essais) ; envoie-le au robot depuis ton compte. Sur Telegram et WhatsApp, un bouton ouvre la
   conversation avec le code déjà prêt. L'écran voit arriver ton compte tout seul, et tes autres comptes déjà
   reliés reçoivent « Nouveau compte relié… » : si ce n'est pas toi, délie-le.
4. **Envoyer un essai** vérifie que les alertes arrivent.

Ce que Bank peut écrire, réglable par messagerie (et qui ne peut que resserrer le réglage de l'assistant) :
**Anonymisé** (conseillé : marchands et montants, jamais un nom de personne), **Chiffres seuls** (totaux, aucune
opération ni titre de démarche), **Complet** (à réserver à Signal).

## Telegram

1. Dans Telegram, ouvre **@BotFather**, envoie `/newbot`, choisis un nom puis un identifiant finissant par `bot`.
2. Colle le jeton (`123456789:AAH…`) dans Bank, enregistre, puis relie ton compte (bouton « Ouvrir Telegram »).

Bank écoute par *long polling* : aucune adresse à ouvrir. Il supprime un éventuel webhook laissé sur ce robot (un
robot = une seule application qui l'écoute). Le jeton fait partie des adresses de l'API Telegram : Bank ne
journalise jamais ces requêtes.

## Discord

1. [discord.com/developers/applications](https://discord.com/developers/applications) › **New Application** ›
   **Bot** › **Reset Token** : copie le jeton.
2. Colle-le dans Bank et enregistre : un bouton **Inviter le robot** apparaît. Invite-le sur un serveur à toi (même
   vide) : Discord n'autorise un message privé qu'entre membres d'un même serveur.
3. Écris le code au robot en message privé.

Bank ne demande que l'intention « messages privés » : aucune intention privilégiée, aucun salon lu.

## Slack

1. [api.slack.com/apps](https://api.slack.com/apps) › **Create New App** › **Socket Mode** : active-le et crée un
   jeton d'application (`xapp-…`, portée `connections:write`).
2. **OAuth & Permissions** : ajoute `chat:write` et `im:history`, installe l'app, copie le jeton du robot (`xoxb-…`).
3. **Event Subscriptions** : abonne le robot à `message.im` ; **App Home** : coche « Allow users to send messages ».
4. Dans Slack, ouvre l'app › **Messages** et envoie le code.

## WhatsApp (API officielle de Meta)

Bank utilise la **WhatsApp Cloud API** officielle, pas une passerelle « WhatsApp Web » non officielle : pas de
risque de bannissement, pas de processus de plus à surveiller.

1. [developers.facebook.com](https://developers.facebook.com/apps) : crée une application de type *Business*,
   ajoute le produit **WhatsApp**.
2. **WhatsApp › Configuration de l'API** : note l'*identifiant du numéro de téléphone* ; crée un **jeton d'accès
   permanent** (Business Settings › utilisateurs système), avec la permission `whatsapp_business_messaging`.
3. **Paramètres de l'application › Général** : copie la **clé secrète**.
4. Enregistre dans Bank, puis dans **WhatsApp › Configuration** : colle l'**URL de rappel** et le **jeton de
   vérification** affichés par Bank, et abonne-toi au champ `messages`.

Meta doit pouvoir joindre l'URL de rappel depuis internet. N'ouvre **que ce chemin**. Exemple avec Caddy, sur un nom
public distinct de celui de Bank :

```caddy
bank-webhook.exemple.fr {
    @wa path /api/channels/whatsapp/webhook
    handle @wa {
        reverse_proxy 127.0.0.1:8088 {
            header_up Host bank.nimbus.lan
            header_up X-Forwarded-Proto https
        }
    }
    respond 404
}
```

et dans `.env` : `BANK_CHANNELS_PUBLIC_URL=https://bank-webhook.exemple.fr` (c'est l'adresse que l'écran te donne
à coller chez Meta). Un tunnel (Cloudflare Tunnel, Tailscale Funnel) restreint au même chemin convient aussi.

Chaque appel est vérifié **avant** d'être lu : corps borné (256 Ko), signature HMAC-SHA256 de Meta
(`X-Hub-Signature-256`, clé secrète de l'application) comparée en temps constant, numéro destinataire qui doit être
le tien. Meta réessaie pendant des jours un appel qui n'a pas eu de réponse 200 : Bank répond 200 dès que l'appel
est authentique et ne traite qu'une fois chaque message.

**Règle des 24 h** : Meta n'accepte un message libre que dans les 24 h qui suivent ton dernier message au robot.
Au-delà, l'alerte est refusée (code 131047) et l'écran le dit : écris n'importe quoi au robot pour rouvrir la
fenêtre, ou choisis une autre messagerie pour les alertes.

## Signal

Bank parle à [signal-cli](https://github.com/AsamK/signal-cli) en mode service HTTP (flux d'événements + JSON-RPC),
comme Hermes Agent. Utilise **un numéro réservé à Bank** (SIM secondaire, numéro virtuel) : tu lui écris depuis ton
propre Signal. Lier signal-cli à ton propre compte ne marche pas ici : Bank n'écoute que les messages que d'autres
envoient à son numéro.

Sur nimbus (l'hôte), une fois :

```bash
signal-cli -a +33XXXXXXXXX register        # Signal peut demander un captcha : voir la doc de signal-cli
signal-cli -a +33XXXXXXXXX verify 123456
```

puis en service (systemd), en n'écoutant que l'interface Docker de l'hôte, jamais le réseau local — quiconque
joint ce service peut écrire au nom du numéro de Bank :

```bash
signal-cli -a +33XXXXXXXXX daemon --http 172.17.0.1:8080
```

Donne à l'API l'accès à l'hôte (`docker-compose.override.yml`, fusionné automatiquement par Docker Compose) :

```yaml
services:
  api:
    extra_hosts: ["host.docker.internal:host-gateway"]
```

Dans Bank : adresse `http://host.docker.internal:8080`, numéro `+33XXXXXXXXX`. Envoie ensuite le code au numéro de
Bank depuis ton Signal.

## ntfy

1. Installe l'application ntfy sur ton téléphone.
2. Dans Bank, active ntfy (serveur `https://ntfy.sh` par défaut, ou le tien avec un jeton `tk_…`) : Bank choisit
   un sujet impossible à deviner et affiche l'adresse d'abonnement.
3. Abonne-toi à cette adresse dans l'application. Garde-la pour toi : sur un serveur public, qui connaît le sujet
   peut lire les alertes.

ntfy n'affiche que des alertes : pas de question, pas de commande.

## Ce que tu peux écrire

| | |
|---|---|
| Une question en français | L'assistant répond (s'il est branché : Connecteurs › OpenRouter), en 3 à 8 lignes |
| `/budget` | Ce que tu peux dépenser par jour, le point bas du mois |
| `/missions` | Tes meilleures pistes et ta tirelire |
| `/demarches` | Où en sont tes démarches |
| `/declarations` | Ce que tu dois déclarer et avant quand (actualisation France Travail, déclaration CAF, impôts), et les chiffres prêts |
| `/nouveau` | Une conversation vierge (ce que l'assistant a appris de toi reste) |
| `/pause`, `/reprendre` | Couper ou rallumer les alertes sur ce compte |
| `/pilote` | L'état du pilote automatique : en marche, en essai ou arrêté, ce qui reste sur 24 h, le dernier mouvement |
| `/pilote stop` | **Tout arrêter tout de suite** (aussi `/pilote off`, `/pilote arrêt`, ou simplement « stop pilote », « arrête le pilote ») — l'allumer, jamais d'ici |
| `/stop` | Pilote en marche : il s'arrête (tes alertes restent, pour que tu saches) ; sinon, les alertes se mettent en pause |
| `/delier` | Ne plus parler à Bank depuis ce compte |

Sans barre oblique, un mot seul marche aussi (`budget`, `missions`, `aide`…) : pratique sur WhatsApp.

## Alertes

| Alerte | Quand | Une seule fois par |
|---|---|---|
| ⚠️ Risque de découvert | La prévision du mois passe sous zéro | mois et date |
| 📌 Démarche à relancer | Une démarche envoyée est sans réponse à la date de relance | démarche et date |
| 🪙 Gain à confirmer | Bank voit un remboursement ou un prélèvement arrêté | gain |
| 📰 Nouveauté officielle | La veille trouve une information qui touche ta situation | information et version |
| ☀️ Le point de la semaine | Le dimanche à partir de 18 h (désactivable) | semaine |
| 🤖 Mouvement du pilote automatique | Ton agent a rapporté un achat, une vente, un échange ou un envoi (🧪 en mode essai) | mouvement et statut |
| 🛑 Pilote arrêté | Arrêt pour anomalie (montant dépassé, perte, insistance, jeton invalide) ou échéance | arrêt |
| ⚠️ Mouvement non rapporté | Un mouvement autorisé n'a pas eu de compte rendu dans les 10 minutes | mouvement |
| ⏳ Pilote bientôt échu | Moins de 24 h avant la fin de la durée choisie | échéance |

Un passage toutes les 15 minutes — tout de suite quand le pilote automatique vient d'autoriser ou de rapporter un
mouvement ; rien pendant tes heures calmes (21 h – 8 h par défaut, fuseau `BANK_TIMEZONE`), **sauf** l'arrêt du
pilote pour anomalie et le mouvement non rapporté, qui ne peuvent pas attendre le matin.
Un compte qui refuse trois fois de suite nos messages (robot bloqué, fenêtre WhatsApp fermée) est mis en pause :
l'écran dit pourquoi, et l'interrupteur « Alertes » le relance.

## Sécurité, en bref

- **Refus par défaut** : seuls les comptes reliés par un code généré dans Bank (après ton mot de passe) sont
  écoutés ; l'identifiant d'un compte (un numéro de téléphone pour WhatsApp et Signal) est chiffré, retrouvé par un
  index aveugle ; le code n'est gardé que sous forme d'empreinte salée, à usage unique.
- **Jetons** : chiffrés (AES-256-GCM), jamais renvoyés par l'API, ni exportés, ni journalisés ; le journal d'audit
  dit quels champs ont changé, jamais leur valeur. Changer de robot ou de numéro délie les comptes de cette
  messagerie : on se relie par un nouveau code, donc avec ton mot de passe (un jeton WhatsApp renouvelé ne délie rien).
- **Entrée** : dédoublonnage des réessais, 20 messages par 5 minutes et par compte, secrets collés refusés avant
  tout traitement (y compris phrase de récupération et clé privée crypto, en anglais comme en français), groupes et robots ignorés, textes traités comme des données (jamais comme des consignes).
- **Sortie** : identifiants retirés de tout message, mentions de masse désactivées (Discord), aperçus de liens
  coupés, confidentialité la plus stricte entre celle de l'assistant et celle de la messagerie.
- **Connexions** : Telegram, Discord, Slack et Signal se connectent vers l'extérieur, rien à ouvrir ; websockets en
  `wss://` seulement ; WhatsApp : signature vérifiée avant lecture, un seul chemin public.
- **Arrêt d'urgence** : retirer l'accord, mettre en pause une messagerie, délier un compte, ou
  `BANK_CHANNELS_ENABLED=false` ; tout prend effet en quelques secondes.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| « Jeton Telegram refusé » | Jeton mal copié ou régénéré chez @BotFather |
| Telegram : « Écoute impossible (409) » | Une autre application écoute déjà ce robot : un robot par application |
| Le robot ne répond pas au code | Code expiré (15 min) ou déjà utilisé : génère-en un autre ; vérifie l'état « Connecté » |
| Le robot ne répond à rien après l'appairage | Accord retiré, messagerie en pause, ou compte délié (`/delier`) |
| WhatsApp : rien n'arrive | URL de rappel injoignable depuis internet, nom absent de `BANK_ALLOWED_HOSTS` (ou Host non réécrit), abonnement au champ `messages` oublié |
| WhatsApp : « fenêtre de 24 h fermée » | Écris au robot pour la rouvrir |
| Discord : impossible d'écrire au robot | Invite-le d'abord sur un serveur à toi |
| Signal : « signal-cli injoignable » | Le service ne tourne pas, ou n'écoute pas l'interface Docker (`172.17.0.1`) |

## Et HERMÈS ?

Si tu fais déjà tourner [HERMÈS](../integrations/hermes/README.md), sa propre passerelle peut aussi le mettre sur ces
messageries, avec les outils de Bank par MCP. Les deux se complètent : les messageries de Bank pour l'essentiel sans
rien installer d'autre, HERMÈS pour un agent qui agit sur bien plus que tes finances. N'utilise pas le même robot
(jeton) pour les deux : une messagerie, une application qui l'écoute.
