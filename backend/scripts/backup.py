"""Sauvegarde à chaud, cohérente et chiffrée (format BANKBK1, clé dérivée de la clé maîtresse), vers le dossier de
sauvegarde (BANK_BACKUP_DIR, /data/backups par défaut) et le second emplacement s'il est configuré
(BANK_BACKUP_REMOTE_DIR) — la même chose que ce que Bank fait seul chaque jour.

    docker compose exec api python -m scripts.backup            # -> /data/backups/bank-AAAAMMJJ-HHMMSS.bank (+ .json)
    docker compose cp api:/data/backups ./backups               # déjà chiffré : peut sortir de nimbus tel quel

Les champs sensibles sont chiffrés par la clé maîtresse, le fichier entier l'est aussi ; la clé maîtresse se
sauvegarde À PART (gestionnaire de mots de passe). Relire : ``python -m scripts.restore``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import Settings
from app.security.crypto import Keyring, install_keyring
from app.services import backup as backup_service


def main(argv: list[str]) -> int:
    settings = Settings()
    if len(argv) > 1:
        settings = settings.model_copy(update={"backup_dir": Path(argv[1])})
    if len(argv) > 0 and argv[0] != "-":
        settings = settings.model_copy(update={"database_url": f"sqlite:///{Path(argv[0])}"})
    keyring = Keyring(settings.master_keys)
    install_keyring(keyring)
    try:
        result = backup_service.run_backup(settings, keyring=keyring)
    except backup_service.BackupError as exc:
        print(f"ÉCHEC : {exc}")
        return 1
    print(result.file)
    for copy in result.copies:
        print(copy)
    for warning in result.warnings:
        print(f"attention : {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
