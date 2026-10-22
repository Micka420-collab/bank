#!/usr/bin/env bash
# Branche HERMÈS (Hermes Agent) sur Bank : compétence « gestionnaire-argent », serveur MCP, jeton, routines.
#
#   ./install.sh                          # compétence + config + jeton + test de connexion
#   ./install.sh --routines               # … et les routines (matin, dimanche, 1er du mois)
#   ./install.sh --check                  # teste seulement la connexion
#   ./install.sh --url https://bank.nimbus.lan/mcp --deliver telegram --hermes-home ~/.hermes
#
# Idempotent : relancer ne duplique rien (compétence, serveur MCP, règle de routage dans memories/MEMORY.md). Le jeton (créé dans Bank › Réglages › Agents IA) n'est jamais
# affiché ni écrit ailleurs que dans ~/.hermes/.env (mode 600).
set -euo pipefail
umask 077  # tout ce que ce script crée (config, sauvegardes, .env) n'est lisible que par toi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
URL="https://bank.nimbus.lan/mcp"
DELIVER="telegram"
ROUTINES=0
CHECK_ONLY=0
CACERT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --deliver) DELIVER="$2"; shift 2 ;;
    --hermes-home) HERMES_HOME="$2"; shift 2 ;;
    --cacert) CACERT="$2"; shift 2 ;;
    --routines) ROUTINES=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Adresse stricte : HTTPS (le jeton ne circule jamais en clair), sauf boucle locale pour les essais.
[[ "$URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/mcp$ || "$URL" =~ ^http://(localhost|127\.0\.0\.1)(:[0-9]{1,5})?/mcp$ ]] \
  || die "Adresse refusée (attendu https://hôte[:port]/mcp) : $URL"
[[ "$DELIVER" =~ ^[A-Za-z0-9:_,@.-]+$ ]] || die "Destination de livraison invalide : $DELIVER"
[[ -d "$HERMES_HOME" ]] || die "$HERMES_HOME introuvable : installe et lance HERMÈS une première fois."
ENV_FILE="$HERMES_HOME/.env"
CONFIG="$HERMES_HOME/config.yaml"

# ------------------------------------------------------------------ jeton
read_token() {
  local line
  line="$(grep -E '^BANK_MCP_TOKEN=' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
  printf '%s' "${line#BANK_MCP_TOKEN=}"
}

store_token() {
  local token="" tmp
  echo "Colle le jeton créé dans Bank › Réglages › Agents IA (saisie masquée) :"
  read -rs token
  echo
  [[ "$token" =~ ^bnk_[A-Za-z0-9_-]{30,80}$ ]] || die "Ce n'est pas un jeton Bank (bnk_…)."
  touch "$ENV_FILE"
  tmp="$(mktemp "$HERMES_HOME/.env.XXXXXX")"
  grep -vE '^BANK_MCP_TOKEN=' "$ENV_FILE" > "$tmp" || true
  printf 'BANK_MCP_TOKEN=%s\n' "$token" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
  ok "Jeton enregistré dans $ENV_FILE (lecture réservée à toi)"
}

# ------------------------------------------------------------------ test de connexion
check_connection() {
  local token="$1" body code out
  out="$(mktemp)"
  body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hermes-install","version":"1"}}}'
  local curl_opts=(-sS --max-time 20 -o "$out" -w '%{http_code}' -X POST "$URL"
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'
    --data "$body" -K -)
  [[ -n "$CACERT" ]] && curl_opts+=(--cacert "$CACERT")
  # Le jeton passe par l'entrée standard (-K -) : jamais dans la ligne de commande visible par « ps ».
  if ! code="$(printf 'header = "Authorization: Bearer %s"\n' "$token" | curl "${curl_opts[@]}" 2>"$out.err")"; then
    warn "Connexion impossible : $(head -c 300 "$out.err")"
    warn "DNS de bank.nimbus.lan ? certificat (essaie --cacert /chemin/ca.pem) ? reverse proxy ?"
    rm -f "$out" "$out.err"; return 1
  fi
  case "$code" in
    200)
      if grep -q '"name":"bank"' "$out"; then ok "Bank répond : serveur MCP « bank » joignable et jeton accepté."
      else warn "Réponse 200 inattendue : $(head -c 200 "$out")"; fi ;;
    401) warn "401 : jeton refusé (révoqué, expiré, ou adresse IP hors de la liste autorisée du jeton)." ;;
    400) warn "400 : nom d'hôte refusé. Ajoute-le à BANK_ALLOWED_HOSTS dans le .env de Bank." ;;
    403) warn "403 : origine refusée par la protection anti-DNS-rebinding." ;;
    404) warn "404 : /mcp introuvable. Image web à jour (location /mcp) ? BANK_AGENTS_ENABLED=true ?" ;;
    429) warn "429 : trop d'appels, réessaie dans une minute." ;;
    *)   warn "HTTP $code : $(head -c 200 "$out")" ;;
  esac
  rm -f "$out" "$out.err"
  [[ "$code" == 200 ]]
}

if [[ "$CHECK_ONLY" == 1 ]]; then
  TOKEN="$(read_token)"
  [[ -n "$TOKEN" ]] || die "Aucun BANK_MCP_TOKEN dans $ENV_FILE"
  check_connection "$TOKEN"
  exit $?
fi

echo "Bank × HERMÈS — installation dans $HERMES_HOME"

# ------------------------------------------------------------------ compétence
DEST="$HERMES_HOME/skills/finance/gestionnaire-argent"
if [[ -d "$DEST" ]] && ! diff -rq "$DEST" "$HERE/skills/finance/gestionnaire-argent" >/dev/null 2>&1; then
  # Sauvegarde HORS de skills/ : une copie dans l'arborescence serait chargée comme une 2e compétence.
  BACKUP="$HERMES_HOME/backups/bank/gestionnaire-argent-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$(dirname "$BACKUP")"
  cp -R "$DEST" "$BACKUP"
  warn "Ancienne version de la compétence sauvegardée dans $BACKUP"
fi
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -R "$HERE/skills/finance/gestionnaire-argent" "$DEST"
ok "Compétence installée : $DEST"

# ------------------------------------------------------------------ routage (mémoire de HERMÈS)
# Comme la compétence de Minara, on inscrit une règle de routage dans la mémoire de HERMÈS : les questions
# d'argent de Micka passent par Bank, et un mouvement crypto passe par Bank AVANT l'outil qui l'exécute.
MEMORY="$HERMES_HOME/memories/MEMORY.md"
mkdir -p "$(dirname "$MEMORY")"
touch "$MEMORY"
if grep -q '^## Bank — argent de Micka' "$MEMORY"; then
  ok "Règle de routage déjà présente dans $MEMORY"
else
  cp "$MEMORY" "$MEMORY.bak-$(date +%Y%m%d%H%M%S)"
  cat >> "$MEMORY" <<'ROUTAGE'

## Bank — argent de Micka

- Toute question sur l'argent de Micka (budget, achats, aides, impôts, épargne, crypto) : compétence
  gestionnaire-argent et serveur MCP bank, avant toute recherche ou connaissance générale.
- Un mouvement crypto (acheter, vendre, échanger, envoyer) passe d'abord par Bank : get_crypto, crypto_check
  sur toute adresse ou offre reçue, autonomy_status ; puis confirmation de Micka dans un message séparé, ou
  autorisation du pilote automatique (request_autonomous_move) ; puis compte rendu.
- Jamais de phrase de récupération, de clé privée, de mot de passe ni de numéro de carte : ni demandés, ni lus,
  ni recopiés.
ROUTAGE
  ok "Règle de routage ajoutée à $MEMORY (sauvegarde .bak créée)"
fi

# ------------------------------------------------------------------ serveur MCP
touch "$CONFIG"
if grep -qE '^[[:space:]]{2}bank:[[:space:]]*$' "$CONFIG" && grep -qE '^mcp_servers:' "$CONFIG"; then
  warn "Un serveur « bank » existe déjà dans config.yaml : laissé tel quel (vérifie son url : $URL)."
else
  cp "$CONFIG" "$CONFIG.bak-$(date +%Y%m%d%H%M%S)"
  blockfile="$(mktemp)"
  # Substitution par awk/ENVIRON : l'adresse est une donnée, jamais interprétée (pas d'injection sed).
  BANK_URL="$URL" awk 'f || /^  bank:/ { f = 1; gsub(/https:\/\/bank\.nimbus\.lan\/mcp/, ENVIRON["BANK_URL"]); print }' \
    "$HERE/config.snippet.yaml" > "$blockfile"
  if grep -qE '^mcp_servers:' "$CONFIG"; then
    # Insère le bloc juste sous « mcp_servers: » sans toucher au reste (commentaires compris).
    awk -v f="$blockfile" '{ print } /^mcp_servers:[[:space:]]*$/ && !done { while ((getline l < f) > 0) print l; done=1 }' \
      "$CONFIG" > "$CONFIG.tmp" && mv "$CONFIG.tmp" "$CONFIG"
  else
    { printf '\nmcp_servers:\n'; cat "$blockfile"; } >> "$CONFIG"
  fi
  rm -f "$blockfile"
  ok "Serveur MCP « bank » ajouté à $CONFIG (sauvegarde .bak créée)"
fi

# ------------------------------------------------------------------ jeton + test
TOKEN="$(read_token)"
if [[ -z "$TOKEN" ]]; then
  store_token
  TOKEN="$(read_token)"
else
  ok "Jeton déjà présent dans $ENV_FILE"
fi
check_connection "$TOKEN" || warn "Corrige le point ci-dessus puis relance : ./install.sh --check"

# ------------------------------------------------------------------ routines
if [[ "$ROUTINES" == 1 ]]; then
  if ! command -v hermes >/dev/null 2>&1; then
    warn "Commande hermes introuvable : crée les routines à la main (voir README)."
  else
    existing="$(hermes cron list 2>/dev/null || true)"
    add_job() {  # nom, planification, consigne
      if grep -q "$1" <<<"$existing"; then ok "Routine $1 déjà en place"; return; fi
      hermes cron create "$2" "$3" --skill gestionnaire-argent --deliver "$DELIVER" --name "$1" >/dev/null
      ok "Routine $1 créée ($2)"
    }
    add_job bank-point-du-matin "30 7 * * *" \
      "Point financier du matin : suis la procédure A de la compétence gestionnaire-argent."
    add_job bank-revue-du-dimanche "0 18 * * 0" \
      "Revue financière de la semaine : suis la procédure C de la compétence gestionnaire-argent."
    add_job bank-bilan-du-mois "0 9 1 * *" \
      "Bilan financier du mois écoulé : suis la procédure D de la compétence gestionnaire-argent."
    warn "Vérifie avec « hermes tools » que le serveur MCP bank est actif pour la plateforme cron."
  fi
fi

cat <<EOF

Terminé. Dans HERMÈS :
  1. /reload-mcp              (ou redémarre la passerelle)
  2. « Fais mon onboarding financier »  → il te pose les questions et remplit Bank › Ma situation
  3. Règle toi-même son mandat dans Bank › Ma situation › Le mandat de ton agent
EOF
