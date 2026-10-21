"""Sauvegarde à chaud cohérente de la base (API sqlite3 backup : pas d'arrêt, pas de fichier à moitié copié).

    docker compose exec api python -m scripts.backup            # -> /data/backups/bank-AAAAMMJJ-HHMMSS.db
    docker compose cp api:/data/backups ./backups               # puis chiffre avant de sortir de nimbus :
    age -r <ta-clé-publique> -o bank.db.age backups/bank-*.db

Les champs sensibles (IBAN, profil, sessions bancaires, secret TOTP) sont déjà chiffrés par la clé
maîtresse ; les libellés d'opérations ne le sont pas -> une sauvegarde se chiffre toujours avant
d'être stockée ailleurs. Et la clé maîtresse se sauvegarde À PART (gestionnaire de mots de passe).
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

KEEP = 14


def backup(db_path: Path, out_dir: Path, keep: int = KEEP) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = out_dir / f"bank-{datetime.now(UTC):%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError(f"Sauvegarde corrompue : {check}")
    finally:
        dst.close()
        src.close()
    target.chmod(0o600)
    for old in sorted(out_dir.glob("bank-*.db"))[:-keep]:
        old.unlink()
    return target


if __name__ == "__main__":
    db = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/bank.db")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/data/backups")
    print(backup(db, out))
