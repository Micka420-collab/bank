# Bank

Cockpit financier personnel, auto-hébergé sur nimbus. Il répond chaque matin à une question :
**combien je peux dépenser aujourd'hui sans me mettre dans le rouge**, et surveille le reste.

- **Comptes** : import CSV/OFX de n'importe quelle banque française, ou synchronisation DSP2 (Enable Banking).
- **Budget** : reste à dépenser par jour contraint par la trésorerie (pas de « 216 €/j » qui suppose le salaire déjà arrivé), courbe du mois avec point bas et date de découvert, enveloppes, suggestions.
- **Aides de l'État** : reconnaissance des versements CAF, France Travail, CPAM, MSA, impôts ; alertes de retard ou de changement ; simulation de tes droits par OpenFisca (le moteur officiel) et détection du non-recours.
- **Patrimoine** : valeur nette, matelas de sécurité, livrets réglementés (taux au 1er août 2026), objectifs, projection.

## Architecture

```
navigateur ──HTTPS──> ton reverse proxy (nimbus) ──> web : nginx, CSP stricte, rate-limit
                                                        │ réseau « app » (interne)
                                                        ▼
                                                 api : FastAPI + SQLite (volume chiffré par champ)
                                                   │ réseau « fiscal » (interne)   │ réseau « egress »
                                                   ▼                               ▼
                                         fiscal : OpenFisca France        api.enablebanking.com
                                         (aucun accès Internet)
```

| Dossier | Contenu |
|---|---|
| `backend/app/domain` | Cœur métier pur, sans I/O : montants, normalisation des libellés, catégorisation, récurrences, prévision, aides, patrimoine |
| `backend/app/importers` | CSV toutes banques (auto-détection), OFX 1.x/2.x sans parseur XML |
| `backend/app/security` | Chiffrement AES-256-GCM par champ, index aveugles, Argon2id, TOTP, sessions, audit chaîné HMAC, middlewares |
| `backend/app/providers` | Client Enable Banking (JWT RS256) |
| `backend/app/fiscal` | Construction de la situation OpenFisca à partir de tes vrais relevés |
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
6. `docker compose up -d` puis Réglages → Connecter une banque.

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

Qualité au moment de la livraison : 220 tests backend (unitaires, propriétés Hypothesis, intégration HTTP,
intégration OpenFisca réelle), couverture 94 %, mypy strict sans erreur, ruff, bandit et pip-audit propres,
`npm audit` sans vulnérabilité, aucune violation CSP sur toutes les pages.

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
