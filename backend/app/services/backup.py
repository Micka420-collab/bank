"""Sauvegardes : copie chaude cohérente de la base (API de sauvegarde SQLite), chiffrée (format BANKBK1, clé dérivée
de la clé maîtresse), écrite dans le dossier de sauvegarde et, si demandé, dans un second emplacement ; manifeste à
côté (tailles, empreinte du clair, lignes par table — rien de secret). Restauration d'essai : la dernière sauvegarde
est déchiffrée dans un dossier jetable, ouverte en lecture seule, vérifiée (intégrité SQLite, empreinte, lignes par
table égales au manifeste), puis effacée. Chaque passage, réussi ou raté, est visible dans Bank et alerte quand ça
rate. La clé maîtresse ne se sauvegarde jamais ici : elle vit à part (gestionnaire de mots de passe)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.models import BackupRun
from app.security import audit, backupfile
from app.security.crypto import Keyring, get_keyring

SUFFIX = ".bank"
MANIFEST_SUFFIX = ".json"
STALE_HOURS = 48  # au-delà sans sauvegarde réussie : alerte
MAX_RUNS_KEPT = 200
COUNTED_TABLES_SKIP = {"sqlite_sequence", "audit_log"}  # le journal grossit entre la copie et la vérification
_LOCK = threading.Lock()


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Manifest:
    created_at: str
    kid: str
    plain_bytes: int
    plain_sha256: str
    tables: dict[str, int]
    cipher_bytes: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass
class BackupResult:
    file: str
    manifest: Manifest
    copies: list[str] = field(default_factory=list)
    pruned: int = 0
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


@dataclass
class VerifyResult:
    file: str
    ok: bool
    tables: dict[str, int]
    plain_bytes: int
    seconds: float
    problems: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ outils
def db_path(settings: Settings) -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise BackupError("La sauvegarde intégrée ne couvre que SQLite")
    return Path(url[len("sqlite:///") :])


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        if r[0] not in COUNTED_TABLES_SKIP and not r[0].startswith("sqlite_")
    ]
    out: dict[str, int] = {}
    for name in names:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue  # un nom de table hors du schéma attendu ne passe pas dans une requête
        query = f'SELECT COUNT(*) FROM "{name}"'  # noqa: S608 # nosec B608 - nom issu de sqlite_master, filtré
        out[name] = int(conn.execute(query).fetchone()[0])
    return out


def _integrity(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise BackupError(f"Intégrité SQLite : {row[0] if row else 'aucune réponse'}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _copy_atomic(src: Path, dst_dir: Path, name: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = dst_dir / f".{name}.part"
    shutil.copyfile(src, tmp)
    tmp.chmod(0o600)
    os.replace(tmp, dst_dir / name)
    return dst_dir / name


def _prune(folder: Path, keep: int) -> int:
    files = sorted(folder.glob(f"bank-*{SUFFIX}"))
    removed = 0
    for old in files[:-keep] if keep > 0 else files:
        old.unlink(missing_ok=True)
        Path(str(old) + MANIFEST_SUFFIX).unlink(missing_ok=True)
        removed += 1
    return removed


def latest_file(folder: Path) -> Path | None:
    files = sorted(folder.glob(f"bank-*{SUFFIX}"))
    return files[-1] if files else None


def read_manifest(path: Path) -> Manifest | None:
    side = Path(str(path) + MANIFEST_SUFFIX)
    if not side.exists():
        return None
    try:
        raw = json.loads(side.read_text("utf-8"))
        return Manifest(
            created_at=str(raw["created_at"]),
            kid=str(raw["kid"]),
            plain_bytes=int(raw["plain_bytes"]),
            plain_sha256=str(raw["plain_sha256"]),
            tables={str(k): int(v) for k, v in dict(raw.get("tables") or {}).items()},
            cipher_bytes=int(raw.get("cipher_bytes") or 0),
        )
    except (ValueError, KeyError, TypeError):
        return None


# ------------------------------------------------------------------ sauvegarde
def run_backup(settings: Settings, *, keyring: Keyring | None = None, now: datetime | None = None) -> BackupResult:
    """Copie chaude -> chiffrement -> écriture atomique -> second emplacement -> élagage. Lève BackupError."""
    started = time.monotonic()
    kr = keyring or get_keyring()
    moment = now or utcnow()
    source = db_path(settings)
    if not source.exists():
        raise BackupError("Base introuvable")
    out_dir = settings.backup_dir
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"bank-{moment:%Y%m%d-%H%M%S}{SUFFIX}"
    # Le dossier de travail vit à côté des sauvegardes (même disque : écriture atomique, pas le /tmp en mémoire de
    # 64 Mo du conteneur) et disparaît à la fin, même en cas d'erreur.
    with _LOCK, tempfile.TemporaryDirectory(prefix=".travail-", dir=out_dir) as tmp:
        plain = Path(tmp) / "plain.db"
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(plain)
        try:
            src.backup(dst)
            _integrity(dst)
            tables = table_counts(dst)
        finally:
            dst.close()
            src.close()
        plain_bytes = plain.stat().st_size
        digest = _sha256(plain)
        cipher = Path(tmp) / name
        with plain.open("rb") as fin, cipher.open("wb") as fout:
            backupfile.encrypt_stream(fin, fout, kr)
        kid, _ = kr.backup_key()
        manifest = Manifest(moment.isoformat(), kid, plain_bytes, digest, tables, cipher.stat().st_size)
        (Path(tmp) / (name + MANIFEST_SUFFIX)).write_text(manifest.to_json(), "utf-8")
        final = _copy_atomic(cipher, out_dir, name)
        _copy_atomic(Path(tmp) / (name + MANIFEST_SUFFIX), out_dir, name + MANIFEST_SUFFIX)
        result = BackupResult(str(final), manifest)
        if settings.backup_remote_dir is not None:
            try:
                copy = _copy_atomic(cipher, settings.backup_remote_dir, name)
                _copy_atomic(Path(tmp) / (name + MANIFEST_SUFFIX), settings.backup_remote_dir, name + MANIFEST_SUFFIX)
                result.copies.append(str(copy))
                result.pruned += _prune(settings.backup_remote_dir, settings.backup_keep)
            except OSError as exc:
                result.warnings.append(f"Second emplacement injoignable : {exc.strerror or exc}")
        result.pruned += _prune(out_dir, settings.backup_keep)
    result.seconds = round(time.monotonic() - started, 2)
    return result


# ------------------------------------------------------------------ restauration d'essai
def verify_file(path: Path, *, keyring: Keyring | None = None, work_dir: Path | None = None) -> VerifyResult:
    """Déchiffre dans un dossier jetable (``work_dir``, sinon à côté du fichier), ouvre en lecture seule, vérifie
    intégrité, empreinte et lignes."""
    started = time.monotonic()
    kr = keyring or get_keyring()
    problems: list[str] = []
    manifest = read_manifest(path)
    if manifest is None:
        problems.append("Manifeste absent ou illisible : impossible de comparer les lignes")
    tables: dict[str, int] = {}
    plain_bytes = 0
    work_dir = work_dir or path.parent
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".essai-", dir=work_dir) as tmp:
        plain = Path(tmp) / "restored.db"
        try:
            with path.open("rb") as fin, plain.open("wb") as fout:
                plain_bytes = backupfile.decrypt_stream(fin, fout, kr)
        except (backupfile.BackupFileError, OSError) as exc:
            return VerifyResult(str(path), False, {}, 0, round(time.monotonic() - started, 2), [str(exc)])
        digest = _sha256(plain)
        conn = sqlite3.connect(f"file:{plain}?mode=ro", uri=True)
        try:
            try:
                _integrity(conn)
            except BackupError as exc:
                problems.append(str(exc))
            try:
                tables = table_counts(conn)
            except sqlite3.DatabaseError as exc:
                problems.append(f"Lecture impossible : {exc}")
        finally:
            conn.close()
    if manifest is not None:
        if manifest.plain_sha256 != digest:
            problems.append("Empreinte différente du manifeste : le fichier restauré n'est pas celui sauvegardé")
        if manifest.plain_bytes != plain_bytes:
            problems.append(f"Taille {plain_bytes} octets, manifeste {manifest.plain_bytes}")
        for name, expected in manifest.tables.items():
            if tables.get(name) != expected:
                problems.append(f"Table {name} : {tables.get(name, 'absente')} lignes, manifeste {expected}")
    if not tables:
        problems.append("Aucune table lue")
    return VerifyResult(str(path), not problems, tables, plain_bytes, round(time.monotonic() - started, 2), problems)


def verify_latest(settings: Settings, *, keyring: Keyring | None = None) -> VerifyResult:
    latest = latest_file(settings.backup_dir)
    if latest is None and settings.backup_remote_dir is not None:
        latest = latest_file(settings.backup_remote_dir)
    if latest is None:
        return VerifyResult("", False, {}, 0, 0.0, ["Aucune sauvegarde à vérifier"])
    return verify_file(latest, keyring=keyring, work_dir=settings.backup_dir)


# ------------------------------------------------------------------ journal des passages
def _record(
    db: Session,
    kind: str,
    status: str,
    details: dict[str, Any],
    *,
    trigger: str,
    started: datetime,
    finished: datetime | None = None,
) -> BackupRun:
    row = BackupRun(
        kind=kind,
        status=status,
        started_at=started,
        finished_at=finished or utcnow(),
        details=json.dumps(details, ensure_ascii=False, default=str)[:20_000],
        trigger=trigger,
    )
    db.add(row)
    db.flush()
    old = list(db.scalars(select(BackupRun).order_by(BackupRun.id.desc()).offset(MAX_RUNS_KEPT)))
    for o in old:
        db.delete(o)
    return row


def backup_and_record(
    db: Session, settings: Settings, *, actor: str, trigger: str = "auto", now: datetime | None = None
) -> BackupRun:
    started = now or utcnow()
    try:
        result = run_backup(settings, now=now)
    except (BackupError, OSError, backupfile.BackupFileError) as exc:
        row = _record(db, "backup", "failed", {"error": str(exc)}, trigger=trigger, started=started, finished=now)
        audit.record(db, actor, "backup.failed", trigger=trigger)
        return row
    details = {
        "file": Path(result.file).name,
        "cipher_bytes": result.manifest.cipher_bytes,
        "plain_bytes": result.manifest.plain_bytes,
        "tables": result.manifest.tables,
        "copies": [str(Path(c).parent) for c in result.copies],
        "pruned": result.pruned,
        "seconds": result.seconds,
        "warnings": result.warnings,
    }
    row = _record(db, "backup", "ok", details, trigger=trigger, started=started, finished=now)
    audit.record(
        db, actor, "backup.done", trigger=trigger, bytes=result.manifest.cipher_bytes, copies=len(result.copies)
    )
    return row


def verify_and_record(
    db: Session, settings: Settings, *, actor: str, trigger: str = "auto", now: datetime | None = None
) -> BackupRun:
    started = now or utcnow()
    try:
        result = verify_latest(settings)
    except (BackupError, OSError) as exc:
        row = _record(db, "verify", "failed", {"error": str(exc)}, trigger=trigger, started=started, finished=now)
        audit.record(db, actor, "backup.verify_failed", trigger=trigger)
        return row
    details = {
        "file": Path(result.file).name if result.file else "",
        "tables": result.tables,
        "plain_bytes": result.plain_bytes,
        "seconds": result.seconds,
        "problems": result.problems,
    }
    row = _record(
        db, "verify", "ok" if result.ok else "failed", details, trigger=trigger, started=started, finished=now
    )
    audit.record(db, actor, "backup.verify_done" if result.ok else "backup.verify_failed", trigger=trigger)
    return row


# ------------------------------------------------------------------ cadence et état
def last_run(db: Session, kind: str, status: str | None = None) -> BackupRun | None:
    q = select(BackupRun).where(BackupRun.kind == kind)
    if status:
        q = q.where(BackupRun.status == status)
    return db.scalar(q.order_by(BackupRun.id.desc()).limit(1))


def due(db: Session, settings: Settings, *, local_now: datetime) -> tuple[bool, bool]:
    """(sauvegarde due, vérification due) : une sauvegarde par jour à partir de l'heure choisie ; une vérification
    par semaine le jour choisi, quand il existe au moins une sauvegarde."""
    if not settings.backup_enabled:
        return False, False
    today = local_now.date()
    tz = local_now.tzinfo or UTC
    last_ok = last_run(db, "backup", "ok")
    last_any = last_run(db, "backup")
    backup_due = local_now.hour >= settings.backup_hour and (
        last_ok is None or last_ok.started_at.astimezone(tz).date() < today
    )
    if backup_due and last_any is not None and last_any.status == "failed":
        # un échec aujourd'hui : on réessaie à l'heure suivante, pas en boucle
        backup_due = (local_now.astimezone(UTC) - last_any.started_at) >= timedelta(hours=1)
    last_verify = last_run(db, "verify")
    week = today.isocalendar()[:2]
    verify_due = (
        local_now.weekday() == settings.backup_verify_weekday
        and local_now.hour >= settings.backup_hour
        and latest_file(settings.backup_dir) is not None
        and (last_verify is None or last_verify.started_at.astimezone(tz).date().isocalendar()[:2] != week)
    )
    return backup_due, verify_due


def tick(db: Session, settings: Settings, *, now: datetime | None = None) -> dict[str, int]:
    """Appelé par l'entretien horaire : fait ce qui est dû. Les deux passages restent courts (secondes)."""
    moment = (now or utcnow()).astimezone(ZoneInfo(settings.timezone))
    backup_due, verify_due = due(db, settings, local_now=moment)
    report = {"backups": 0, "verifies": 0}
    if backup_due:
        backup_and_record(db, settings, actor="système", trigger="auto", now=now)
        report["backups"] = 1
        db.flush()
    if verify_due:
        verify_and_record(db, settings, actor="système", trigger="auto", now=now)
        report["verifies"] = 1
    return report


def run_view(row: BackupRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        details = json.loads(row.details)
    except ValueError:
        details = {}
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "trigger": row.trigger,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "details": details,
    }


def files_view(folder: Path | None) -> list[dict[str, Any]]:
    if folder is None or not folder.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(folder.glob(f"bank-*{SUFFIX}"), reverse=True)[:30]:
        st = f.stat()
        out.append(
            {
                "name": f.name,
                "bytes": st.st_size,
                "modified_at": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                "manifest": read_manifest(f) is not None,
            }
        )
    return out


def status(db: Session, settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or utcnow()
    local = moment.astimezone(ZoneInfo(settings.timezone))
    last_ok = last_run(db, "backup", "ok")
    last_backup = last_run(db, "backup")
    last_verify = last_run(db, "verify")
    stale = last_ok is None or (moment - last_ok.started_at) > timedelta(hours=STALE_HOURS)
    level = "ok"
    if (last_backup is not None and last_backup.status == "failed") or (
        last_verify is not None and last_verify.status == "failed"
    ):
        level = "failed"
    elif stale:
        level = "stale" if last_ok is not None else "none"
    elif last_verify is None or last_verify.status != "ok":
        level = "unverified"
    runs = list(db.scalars(select(BackupRun).order_by(BackupRun.id.desc()).limit(20)))
    next_backup = local.replace(hour=settings.backup_hour, minute=0, second=0, microsecond=0)
    if next_backup <= local or (
        last_ok is not None and last_ok.started_at.astimezone(local.tzinfo).date() == local.date()
    ):
        next_backup = next_backup + timedelta(days=1)
    return {
        "enabled": settings.backup_enabled,
        "level": level,
        "directory": str(settings.backup_dir),
        "remote_directory": str(settings.backup_remote_dir) if settings.backup_remote_dir else None,
        "remote_reachable": bool(settings.backup_remote_dir and settings.backup_remote_dir.exists()),
        "hour": settings.backup_hour,
        "keep": settings.backup_keep,
        "verify_weekday": settings.backup_verify_weekday,
        "next_backup": next_backup.isoformat(),
        "last_backup": run_view(last_backup),
        "last_ok_backup": run_view(last_ok),
        "last_verify": run_view(last_verify),
        "runs": [run_view(r) for r in runs],
        "files": files_view(settings.backup_dir),
        "remote_files": files_view(settings.backup_remote_dir),
        "restore_hint": (
            "Restaurer : arrêter la pile, puis « docker compose run --rm api python -m scripts.restore "
            "/data/backups/<fichier>.bank /data/bank.db » avec la même clé maîtresse, et relancer. La clé maîtresse "
            "n'est jamais dans la sauvegarde : garde-la à part."
        ),
    }


def alerts(db: Session, *, now: datetime, public_url: str) -> list[tuple[str, str, bool]]:
    """Alertes des messageries : un échec, ou plus de 48 h sans sauvegarde réussie (quand il y en a déjà eu une)."""
    out: list[tuple[str, str, bool]] = []
    last_backup = last_run(db, "backup")
    last_ok = last_run(db, "backup", "ok")
    if last_backup is not None and last_backup.status == "failed":
        out.append(
            (
                f"backup:failed:{last_backup.id}",
                f"💾 La sauvegarde de Bank a échoué. Regarde Réglages › Sauvegardes : {public_url}/#/reglages",
                True,
            )
        )
    elif last_ok is not None and (now - last_ok.started_at) > timedelta(hours=STALE_HOURS):
        day = last_ok.started_at.date().isoformat()
        out.append(
            (
                f"backup:stale:{day}",
                f"💾 Aucune sauvegarde de Bank depuis le {day}. Regarde Réglages › Sauvegardes : "
                f"{public_url}/#/reglages",
                True,
            )
        )
    last_verify = last_run(db, "verify")
    if last_verify is not None and last_verify.status == "failed":
        out.append(
            (
                f"backup:verify_failed:{last_verify.id}",
                "💾 La restauration d'essai de la sauvegarde a échoué : la sauvegarde ne se relit pas. "
                f"{public_url}/#/reglages",
                True,
            )
        )
    return out


def today_local(settings: Settings, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(ZoneInfo(settings.timezone)).date()
