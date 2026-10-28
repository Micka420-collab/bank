"""Sauvegardes : le fichier chiffré (BANKBK1) se relit avec la clé maîtresse et refuse toute altération, troncature ou
rallonge ; la sauvegarde chaude est cohérente, écrite en deux emplacements, élaguée ; la restauration d'essai
déchiffre dans un dossier jetable, vérifie l'intégrité, l'empreinte et les lignes, et dit ce qui ne va pas ; une
réussite et un échec sont visibles dans Bank, et un échec alerte les messageries."""

from __future__ import annotations

import io
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.models import BackupRun
from app.security import backupfile
from app.security.crypto import Keyring
from app.services import backup as svc
from tests.conftest import do_login, do_setup


def test_backup_file_format_roundtrip_and_refuses_tampering(keyring: Keyring) -> None:
    plain = os.urandom(backupfile.CHUNK * 2 + 12_345)  # trois morceaux, le dernier court
    out = io.BytesIO()
    assert backupfile.encrypt_stream(io.BytesIO(plain), out, keyring) == len(plain)
    blob = out.getvalue()
    assert blob.startswith(backupfile.MAGIC) and b"k1" in blob[:16]
    back = io.BytesIO()
    assert backupfile.decrypt_stream(io.BytesIO(blob), back, keyring) == len(plain) and back.getvalue() == plain
    # Vide : un seul morceau, marqué dernier.
    empty = io.BytesIO()
    backupfile.encrypt_stream(io.BytesIO(b""), empty, keyring)
    assert backupfile.decrypt_stream(io.BytesIO(empty.getvalue()), io.BytesIO(), keyring) == 0
    # Un octet retourné au milieu du chiffré, une troncature, une rallonge, une autre clé, un faux en-tête : refus.
    header_len = len(backupfile.MAGIC) + 1 + 2 + backupfile.SALT_LEN
    flipped = bytearray(blob)
    flipped[header_len + 4 + 100] ^= 0x01
    with pytest.raises(backupfile.BackupFileError, match="altérée"):
        backupfile.decrypt_stream(io.BytesIO(bytes(flipped)), io.BytesIO(), keyring)
    first_len = int.from_bytes(blob[header_len : header_len + 4], "big")
    truncated = blob[: header_len + 4 + first_len]  # premier morceau complet, puis plus rien
    with pytest.raises(backupfile.BackupFileError, match="dernier morceau absent"):
        backupfile.decrypt_stream(io.BytesIO(truncated), io.BytesIO(), keyring)
    with pytest.raises(backupfile.BackupFileError, match="tronqué"):
        backupfile.decrypt_stream(io.BytesIO(blob[:-7]), io.BytesIO(), keyring)
    with pytest.raises(backupfile.BackupFileError, match="après le dernier"):
        backupfile.decrypt_stream(io.BytesIO(blob + b"\x00"), io.BytesIO(), keyring)
    with pytest.raises(backupfile.BackupFileError, match="clé maîtresse inconnue"):
        backupfile.decrypt_stream(io.BytesIO(blob), io.BytesIO(), Keyring({"k2": os.urandom(32)}))
    with pytest.raises(backupfile.BackupFileError, match="pas une sauvegarde"):
        backupfile.decrypt_stream(io.BytesIO(b"SQLite format 3\x00" + blob), io.BytesIO(), keyring)
    # La même clé maîtresse connue sous le même identifiant relit ; une rotation garde l'ancienne clé lisible.
    master = os.urandom(32)
    old = Keyring({"k1": master})
    sealed = io.BytesIO()
    backupfile.encrypt_stream(io.BytesIO(b"hello"), sealed, old)
    rotated = Keyring({"k2": os.urandom(32), "k1": master})
    assert backupfile.decrypt_stream(io.BytesIO(sealed.getvalue()), (res := io.BytesIO()), rotated) == 5
    assert res.getvalue() == b"hello"


def _settings(tmp_path: Path, **over: object) -> Settings:
    db = tmp_path / "live.db"
    con = sqlite3.connect(db)
    con.execute("create table t (x int)")
    con.executemany("insert into t values (?)", [(i,) for i in range(250)])
    con.execute("create table audit_log (x int)")
    con.commit()
    con.close()
    base = {
        "env": "test",
        "database_url": f"sqlite:///{db}",
        "master_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "setup_token": "x" * 24,
        "backup_dir": tmp_path / "backups",
        "backup_remote_dir": tmp_path / "usb" / "bank",
        "backup_keep": 2,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_hot_backup_is_encrypted_copied_pruned_and_verifiable(tmp_path: Path, keyring: Keyring) -> None:
    settings = _settings(tmp_path)
    runs = [
        svc.run_backup(settings, keyring=keyring, now=datetime(2026, 10, 24, 3, 0, i, tzinfo=UTC)) for i in range(3)
    ]
    files = sorted(settings.backup_dir.glob("bank-*.bank"))
    assert len(files) == 2, "élagage : deux fichiers gardés"
    assert (
        sorted(settings.backup_remote_dir.glob("bank-*.bank"))
        and len(list(settings.backup_remote_dir.glob("*.bank"))) == 2
    )  # type: ignore[union-attr]
    last = runs[-1]
    assert Path(last.file).name == "bank-20261024-030002.bank" and last.copies and last.pruned >= 1
    assert last.manifest.tables == {"t": 250}, (
        "le journal d'audit n'est pas compté (il bouge entre copie et vérification)"
    )
    assert last.manifest.plain_bytes > 0 and len(last.manifest.plain_sha256) == 64 and last.manifest.kid == "k1"
    raw = Path(last.file).read_bytes()
    assert raw.startswith(backupfile.MAGIC) and b"SQLite format 3" not in raw, "jamais la base en clair sur le disque"
    assert oct(Path(last.file).stat().st_mode & 0o777) == "0o600"
    side = json.loads(Path(last.file + ".json").read_text())
    assert side["tables"] == {"t": 250} and "sha256" in json.dumps(side) and "master" not in json.dumps(side).lower()
    # Restauration d'essai : lecture seule dans un dossier jetable, tout concorde.
    ok = svc.verify_latest(settings, keyring=keyring)
    assert ok.ok and ok.tables == {"t": 250} and ok.problems == [] and ok.file == last.file
    assert not list(Path(tmp_path).glob("**/restored.db")), "le clair déchiffré ne survit pas à la vérification"
    # Un fichier altéré, un manifeste qui ne colle plus, une sauvegarde absente : dit clairement.
    data = bytearray(raw)
    data[-20] ^= 0x01
    Path(last.file).write_bytes(bytes(data))
    bad = svc.verify_latest(settings, keyring=keyring)
    assert not bad.ok and any("altérée" in p for p in bad.problems)
    Path(last.file).write_bytes(raw)
    side["tables"] = {"t": 251}
    Path(last.file + ".json").write_text(json.dumps(side))
    mismatch = svc.verify_latest(settings, keyring=keyring)
    assert not mismatch.ok and any("Table t : 250 lignes, manifeste 251" in p for p in mismatch.problems)
    for f in settings.backup_dir.glob("bank-*"):
        f.unlink()
    for f in settings.backup_remote_dir.glob("bank-*"):  # type: ignore[union-attr]
        f.unlink()
    none = svc.verify_latest(settings, keyring=keyring)
    assert not none.ok and none.problems == ["Aucune sauvegarde à vérifier"]
    # Second emplacement injoignable : la sauvegarde locale réussit et prévient.
    unreachable = settings.model_copy(update={"backup_remote_dir": Path("/proc/bank-nowhere/usb")})
    res = svc.run_backup(unreachable, keyring=keyring)
    assert res.copies == [] and res.warnings and "Second emplacement" in res.warnings[0]
    with pytest.raises(svc.BackupError, match="SQLite"):
        svc.db_path(settings.model_copy(update={"database_url": "postgresql://x"}))


@pytest.fixture
def world(app, client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    return client


def test_backups_are_visible_in_bank_scheduled_and_alert_on_failure(world: TestClient, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    st = world.get("/api/system/backups").json()
    assert st["level"] == "none" and st["last_backup"] is None and st["files"] == [] and st["hour"] == 3
    assert "clé maîtresse" in st["restore_hint"]
    r = world.post("/api/system/backups/run")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["run"]["status"] == "ok" and st["run"]["trigger"] == "manual" and st["level"] == "unverified"
    assert st["last_ok_backup"]["details"]["tables"]["users"] == 1 and st["files"][0]["name"].endswith(".bank")
    assert "audit_log" not in st["last_ok_backup"]["details"]["tables"]
    v = world.post("/api/system/backups/verify").json()
    assert v["run"]["kind"] == "verify" and v["run"]["status"] == "ok" and v["level"] == "ok", v["run"]
    assert v["last_verify"]["details"]["tables"]["users"] == 1
    # Cadence : une sauvegarde par jour après l'heure choisie, une vérification par semaine le jour choisi.
    with app.state.sessionmaker() as db:
        tz_now = datetime(2026, 10, 24, 2, 30, tzinfo=UTC)  # 4 h 30 à Paris, samedi
        assert svc.due(db, settings, local_now=tz_now.astimezone(svc.ZoneInfo("Europe/Paris"))) == (False, False)
        tomorrow = datetime(2026, 11, 8, 2, 30, tzinfo=UTC)  # dimanche 3 h 30, semaine ISO suivante : les deux sont dus
        local = tomorrow.astimezone(svc.ZoneInfo("Europe/Paris"))
        assert svc.due(db, settings, local_now=local) == (True, True)
        report = svc.tick(db, settings, now=tomorrow)
        db.commit()
        assert report == {"backups": 1, "verifies": 1}
        assert svc.due(db, settings, local_now=local) == (False, False), "une fois par jour, une fois par semaine"
        assert svc.alerts(db, now=tomorrow, public_url="https://bank.lan") == []
        off = settings.model_copy(update={"backup_enabled": False})
        assert svc.due(db, off, local_now=local) == (False, False)
    st = world.get("/api/system/backups").json()
    assert len(st["runs"]) == 4 and {r["trigger"] for r in st["runs"]} == {"manual", "auto"}
    # Un fichier altéré : la restauration d'essai échoue, Bank le montre, et les messageries sont prévenues.
    latest = svc.latest_file(settings.backup_dir)
    assert latest is not None
    data = bytearray(latest.read_bytes())
    data[-5] ^= 0x01
    latest.write_bytes(bytes(data))
    bad = world.post("/api/system/backups/verify").json()
    assert (
        bad["run"]["status"] == "failed"
        and "altérée" in json.dumps(bad["run"]["details"], ensure_ascii=False)
        and bad["level"] == "failed"
    )
    with app.state.sessionmaker() as db:
        notices = svc.alerts(db, now=datetime(2026, 11, 9, 4, 0, tzinfo=UTC), public_url="https://bank.lan")
        assert len(notices) == 1 and notices[0][2] is True and "ne se relit pas" in notices[0][1]
        # Plus de 48 h sans sauvegarde réussie : alerte aussi.
        stale = svc.alerts(db, now=datetime(2026, 11, 4, 4, 0, tzinfo=UTC), public_url="https://bank.lan")
        assert any(k.startswith("backup:stale:") for k, _, _ in stale)
        # Une sauvegarde impossible (base déplacée) : échec enregistré, pas de trace, et réessai une heure plus tard.
        broken = settings.model_copy(update={"database_url": f"sqlite:///{settings.backup_dir}/absente.db"})
        row = svc.backup_and_record(db, broken, actor="test", trigger="auto")
        assert row.status == "failed" and "introuvable" in row.details
        db.commit()
        later = datetime(2026, 11, 2, 2, 30, tzinfo=UTC).astimezone(svc.ZoneInfo("Europe/Paris"))
        assert svc.due(db, settings, local_now=later)[0] is True
        assert db.query(BackupRun).count() == 6
    st = world.get("/api/system/backups").json()
    assert st["level"] == "failed" and st["last_backup"]["status"] == "failed"
    for _ in range(6):
        world.post("/api/system/backups/verify")
    assert world.post("/api/system/backups/verify").status_code == 429, "six gestes par heure"
