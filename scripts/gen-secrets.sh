#!/usr/bin/env bash
# Génère les secrets de Bank dans ./secrets (droits 600). N'écrase jamais un secret existant :
# perdre la clé maîtresse = perdre l'accès aux données chiffrées (IBAN, profil, sessions bancaires).
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
mkdir -p secrets

gen() {
  local file="secrets/$1"
  if [[ -s "$file" ]]; then
    echo "  = $file existe déjà (conservé)"
  else
    head -c "$2" /dev/urandom | base64 | tr -d '\n' > "$file"
    echo "  + $file"
  fi
}

echo "Secrets Bank :"
gen master_key 32
gen setup_token 24
gen fiscal_token 24
[[ -f secrets/enablebanking.pem ]] || : > secrets/enablebanking.pem
# Les conteneurs tournent sans privilèges (uid 10001/10002) : ils lisent les secrets via le groupe 10010.
chmod 640 secrets/*
if ! chgrp 10010 secrets/* 2>/dev/null; then
  echo "  ! lance : sudo chgrp 10010 secrets/*   (sinon les conteneurs ne pourront pas lire les secrets)"
fi
echo
echo "Jeton d'installation (à saisir au premier lancement) : $(cat secrets/setup_token)"
echo "SAUVEGARDE secrets/master_key HORS de nimbus (gestionnaire de mots de passe) : sans elle, pas de restauration."
