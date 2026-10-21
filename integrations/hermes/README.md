# Bank × HERMÈS — ton gestionnaire d'argent

HERMÈS (Hermes Agent) se branche sur Bank par le protocole MCP. Il devient ton gestionnaire d'argent : il connaît
ta situation, te fait un point le matin, répond à « je peux m'acheter ça ? » avec les vrais chiffres, revoit ta
semaine et ton mois, et te propose des réglages que tu valides dans Bank.

```
HERMÈS (192.168.1.112) ──HTTPS + jeton bnk_…──> reverse proxy ──> nginx /mcp ──> api : serveur MCP « bank »
        │                                                                          │
        └── compétence finance/gestionnaire-argent                                 ├── calculs (prévision, verdicts)
            routines : 7 h 30, dimanche 18 h, le 1er                               ├── dossier « Ma situation »
                                                                                   └── propositions → toi, dans Bank
```

## Ce que HERMÈS peut faire — et ne peut pas faire

| Il peut | Il ne peut pas |
|---|---|
| Lire ton dossier (situation, mois en cours, aides, patrimoine, énergie, documents officiels, paie prévue depuis ton planning, ce que Bank aimerait te demander, alertes) | Déplacer, virer, payer ou placer de l'argent : aucun outil ne le permet |
| Répondre « je peux l'acheter ? » (oui, oui mais, à partir du…, dans N mois) et « où placer ? » avec les calculs de Bank | Inventer un chiffre : les verdicts et le plan viennent de Bank, il les explique |
| Évaluer un achat ou une épargne avec la prévision de Bank | Changer son propre mandat (rôle, lignes rouges, seuil) : refusé côté serveur |
| Mener ton onboarding et enregistrer tes réponses | Appliquer un réglage sans toi : il **propose**, tu valides dans Bank |
| Reclasser une opération, si ton mandat est « gestionnaire » | Voir les noms des particuliers, IBAN, e-mails, téléphones (masqués par défaut) |
| Proposer enveloppes, objectifs, épargne, coussin, règles | Agir depuis une autre machine si tu limites le jeton à son adresse IP |

Chaque appel est inscrit au journal d'audit chaîné ; Bank › Ma situation montre « ce que ton agent a fait ».

## Installation

1. **Dans Bank** › Réglages › Agents IA : crée un jeton « hermes », rôle *Conseiller* (ou *Gestionnaire*),
   confidentialité *Anonymisé*, adresse autorisée `192.168.1.112`, puis confirme avec ton mot de passe. Copie le
   jeton (affiché une seule fois).
2. **Sur la machine d'HERMÈS** :

   ```bash
   cd integrations/hermes
   ./install.sh --routines            # demande le jeton (saisie masquée)
   # options : --url https://bank.nimbus.lan/mcp  --deliver telegram  --cacert /chemin/ca.pem
   ```

   Le script installe la compétence dans `~/.hermes/skills/finance/gestionnaire-argent`, ajoute le serveur `bank`
   à `~/.hermes/config.yaml` (sauvegarde faite, le reste du fichier intact), range le jeton dans `~/.hermes/.env`
   (mode 600), teste la connexion et crée trois routines. Relancer ne duplique rien.
3. **Dans HERMÈS** : `/reload-mcp`, puis « fais mon onboarding financier ».
4. **Dans Bank** › Ma situation › *Le mandat de ton agent* : règle son rôle, la fréquence des points, ton seuil
   d'alerte et tes lignes rouges. Lui ne peut pas les modifier.

Installation manuelle : copie `skills/finance/gestionnaire-argent` dans `~/.hermes/skills/finance/`, fusionne
`config.snippet.yaml` dans `~/.hermes/config.yaml`, ajoute `BANK_MCP_TOKEN=bnk_…` à `~/.hermes/.env`, puis :

```bash
hermes cron create "30 7 * * *" "Point financier du matin : suis la procédure A de la compétence gestionnaire-argent." \
  --skill gestionnaire-argent --deliver telegram --name bank-point-du-matin
hermes cron create "0 18 * * 0" "Revue financière de la semaine : suis la procédure C de la compétence gestionnaire-argent." \
  --skill gestionnaire-argent --deliver telegram --name bank-revue-du-dimanche
hermes cron create "0 9 1 * *" "Bilan financier du mois écoulé : suis la procédure D de la compétence gestionnaire-argent." \
  --skill gestionnaire-argent --deliver telegram --name bank-bilan-du-mois
```

Vérifie avec `hermes tools` que le serveur `bank` est actif pour la plateforme *cron*. Si ton mandat dit « seulement
en cas d'alerte », le point du matin répond `[SILENT]` les jours calmes : HERMÈS n'envoie alors rien.

Les points partent là où `--deliver` les envoie (Telegram par défaut) : ils contiennent des montants. Si tu préfères
qu'ils restent chez toi, utilise `--deliver local` ou une messagerie chiffrée de bout en bout.

## Les outils (`mcp_bank_*`)

| Outil | Portée | Rôle |
|---|---|---|
| `get_dossier` (`output: json\|markdown`) | read | Toute la situation, avec la provenance de chaque fait — à lire en premier. Contient `work` (employeur, jour de paie, paie prévue d'après le planning) et `bank_asks` (les cartes que Bank te montre) |
| `get_month`, `get_alerts`, `get_aids`, `get_wealth` | read | Vues ciblées |
| `spending_summary` (`months`) | read | Moyennes par catégorie, identifiants de catégories |
| `list_transactions` | read:transactions | Opérations (libellés anonymisés ; refusé en mode agrégats) |
| `can_i_buy` (`amount_eur`, `label`, `category`) | read | « Oui / oui mais / à partir du… / dans N mois » avec les deux chemins d'épargne (matelas d'abord, au plus vite) |
| `evaluate_purchase` (`amount_eur`, `label`, `on_date`, `category`) | read | Verdict ok / serre / attendre / deconseille à une date précise, enveloppe |
| `investment_plan` | read | Où placer, par priorité : mois d'avance, dette chère, matelas (LEP / livrets), projets datés, long terme (PEA / AV / PER selon tranche, risque, horizon, lignes rouges) |
| `get_energy` | read | Linky : kWh et coût sur 30 jours et par mois, facture annuelle estimée, écart avec la mensualité, pic |
| `what_if_savings` (`monthly_eur`) | read | Effet d'une épargne mensuelle sur le budget, le matelas, les objectifs |
| `onboarding_status`, `record_answer` | read, write:onboarding | Questionnaire « Ma situation » : ni le mandat, ni une réponse que tu as donnée toi-même, ni les dépenses prévues (il les propose) |
| `simulate_aids` | simulate | Simulation OpenFisca de tes droits |
| `categorize_transaction` | write:categorize | Une opération à la fois, si ton mandat est « gestionnaire » ; jamais une opération que tu as classée |
| `propose_action`, `list_proposals` | propose, read | Enveloppe, épargne visée, coussin, objectif, règle de classement (avec le nombre d'opérations touchées), réponse « Ma situation » |

Ressource `bank://questions` (catalogue de l'onboarding) ; prompts `onboarding_interview`, `daily_brief`,
`weekly_review`.

## Routines : ce qu'elles regardent

| Routine | Outils | Ce qu'HERMÈS en fait |
|---|---|---|
| Point du matin (7 h 30) | `get_dossier` | Reste à dépenser et paliers, échéances à 3 jours, alertes (dont énergie et fin de droits), une action |
| Revue du dimanche (18 h) | `get_dossier`, `spending_summary`, `get_alerts`, `list_proposals` | Écarts d'enveloppes, matelas, 3 recommandations chiffrées → propositions |
| Bilan du mois (le 1er, 9 h) | `spending_summary`, `get_wealth`, `get_aids`, `get_energy`, `simulate_aids` | Le mois raconté, enveloppes du mois suivant, épargne, aides, électricité (rattrapage probable) |
| À la demande | `can_i_buy`, `investment_plan` | « Je peux l'acheter ? » et « où placer ? » avec les calculs de Bank |

## Confidentialité

- *Anonymisé* (par défaut) : noms de particuliers, IBAN, e-mails, téléphones et longs numéros remplacés
  (`[PARTICULIER]`, `[IBAN]`…), y compris dans les preuves des valeurs détectées. La contrepartie d'un virement
  est traitée comme un particulier sauf indice contraire (organisme, forme juridique, salaire…). Les montants,
  dates, marchands et catégories restent : c'est ce qui sert à décider. Tes notes libres (écrites pour lui) aussi.
- *Agrégats* : aucune opération individuelle, aucun libellé, ni tes notes : catégories et totaux seulement.
- *Complet* : libellés bruts et notes — à réserver à un modèle qui tourne chez toi.

Le dossier exact que voit un agent est consultable dans Bank › Ma situation › *Ce que ton agent voit*.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| `401` | Jeton révoqué, expiré, mal copié — ou l'IP vue par Bank n'est pas celle autorisée : ton reverse proxy doit transmettre `X-Forwarded-For` (Caddy le fait par défaut) |
| `400 Invalid host header` | Le nom utilisé n'est pas dans `BANK_ALLOWED_HOSTS` |
| `404` sur `/mcp` | Image web ancienne (sans `location = /mcp`) ou `BANK_AGENTS_ENABLED=false` |
| `429` | Plus de 5 requêtes/s (nginx) ou de 120 appels/min par jeton (API) |
| Erreur TLS | Certificat interne : `./install.sh --check --cacert /chemin/ca.pem`, et donne la même CA à HERMÈS |
| « Portée … non accordée » | Le rôle du jeton ne couvre pas l'outil : recrée un jeton avec le bon rôle |

`./install.sh --check` reteste la connexion à tout moment.
