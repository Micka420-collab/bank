"""Relit une sauvegarde chiffrée de Bank (format BANKBK1) avec la clé maîtresse de l'environnement.

    docker compose run --rm api python -m scripts.restore /data/backups/bank-20261024-030000.bank /data/restored.db
    python -m scripts.restore --check /data/backups/bank-20261024-030000.bank      # vérifie sans rien écrire

Ensuite : arrêter la pile, remplacer /data/bank.db par le fichier restauré, relancer avec la MÊME clé maîtresse
(BANK_MASTER_KEY ou BANK_MASTER_KEY_FILE). Le fichier restauré contient les champs chiffrés par cette clé : il ne se
lit pas sans elle. Vérifie toujours le résultat (intégrité, lignes) avant de remplacer la base.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from app.config import Settings
from app.security.crypto import Keyring, install_keyring
from app.services import backup as backup_service


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args or (not check_only and len(args) != 2):
        print(__doc__)
        return 2
    settings = Settings()
    keyring = Keyring(settings.master_keys)
    install_keyring(keyring)
    src = Path(args[0])
    if check_only:
        result = backup_service.verify_file(src, keyring=keyring)
        print("OK" if result.ok else "ÉCHEC", result.plain_bytes, "octets", sum(result.tables.values()), "lignes")
        for p in result.problems:
            print(" -", p)
        return 0 if result.ok else 1
    dst = Path(args[1])
    if dst.exists():
        print(f"{dst} existe déjà : choisis un autre nom, puis remplace la base toi-même.")
        return 2
    from app.security import backupfile

    with src.open("rb") as fin, dst.open("wb") as fout:
        written = backupfile.decrypt_stream(fin, fout, keyring)
    dst.chmod(0o600)
    conn = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        counts = backup_service.table_counts(conn)
    finally:
        conn.close()
    manifest = backup_service.read_manifest(src)
    ok = bool(integrity and integrity[0] == "ok") and (manifest is None or manifest.tables == counts)
    verdict = "OK" if ok else "ÉCHEC"
    print(f"{verdict} : {written} octets, {sum(counts.values())} lignes dans {len(counts)} tables -> {dst}")
    if manifest is not None and manifest.tables != counts:
        print(" - les lignes ne correspondent pas au manifeste")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
