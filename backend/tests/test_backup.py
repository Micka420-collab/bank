import sqlite3
from pathlib import Path

from scripts.backup import backup


def test_backup_is_consistent_rotated_and_private(tmp_path: Path) -> None:
    db = tmp_path / "bank.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x int)")
    con.executemany("insert into t values (?)", [(i,) for i in range(100)])
    con.commit()
    con.close()
    out = tmp_path / "backups"
    for i in range(3):
        (out / f"bank-2020010{i}-000000.db").parent.mkdir(exist_ok=True)
        sqlite3.connect(out / f"bank-2020010{i}-000000.db").close()
    target = backup(db, out, keep=2)
    assert sqlite3.connect(target).execute("select count(*) from t").fetchone() == (100,)
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert len(list(out.glob("bank-*.db"))) == 2 and target.exists()
