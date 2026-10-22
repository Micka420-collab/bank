"""Journal d'audit inviolable : chaîne HMAC.

Chaque entrée contient l'empreinte de la précédente ; l'empreinte est un HMAC dont la clé est dérivée
de la clé maîtresse (jamais en base). Quelqu'un qui obtient un accès en écriture à la base ne peut
ni modifier, ni supprimer, ni réordonner une entrée sans casser la chaîne — et il ne peut pas
la recalculer sans la clé. ``verify_chain`` le détecte.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow, write_lock
from app.models import AuditLog
from app.security.crypto import get_keyring

GENESIS = "0" * 64
log = logging.getLogger("bank.audit")
_FORBIDDEN_KEYS = {"password", "totp", "code", "token", "secret", "iban", "session_id"}


def _canonical(ts: datetime, actor: str, action: str, details: str, prev: str) -> bytes:
    return json.dumps(
        {"ts": ts.isoformat(), "actor": actor, "action": action, "details": details, "prev": prev},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _mac(payload: bytes) -> str:
    return hmac.new(get_keyring().audit_key, payload, hashlib.sha256).hexdigest()


def _scrub(details: dict[str, Any]) -> dict[str, Any]:
    """Garde-fou : un secret ne doit jamais finir dans le journal, même par erreur d'appelant."""
    return {k: ("[redacted]" if k.lower() in _FORBIDDEN_KEYS else v) for k, v in details.items()}


def record(db: Session, actor: str, action: str, **details: Any) -> AuditLog:
    # Lire la tête puis écrire, sous le verrou d'écriture : deux écrivains parallèles ne s'accrochent jamais au
    # même maillon (la chaîne ne « casse » pas toute seule sous la charge).
    write_lock(db)
    prev = db.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)) or GENESIS
    ts = utcnow().replace(microsecond=0)
    det = json.dumps(_scrub(details), separators=(",", ":"), sort_keys=True, default=str)
    entry = AuditLog(
        ts=ts,
        actor=actor[:64],
        action=action[:64],
        details=det,
        prev_hash=prev,
        hash=_mac(_canonical(ts, actor[:64], action[:64], det, prev)),
    )
    db.add(entry)
    db.flush()
    # Ancrage externe : la tête de chaîne part dans les logs du conteneur. Une troncature de la fin
    # du journal (seule attaque que le chaînage seul ne voit pas) devient détectable par comparaison.
    log.info("audit head id=%s action=%s hash=%s", entry.id, entry.action, entry.hash[:16])
    return entry


@dataclass(frozen=True)
class ChainStatus:
    ok: bool
    entries: int
    broken_at: int | None = None
    reason: str | None = None


def verify_chain(db: Session) -> ChainStatus:
    prev = GENESIS
    count = 0
    for entry in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        count += 1
        if entry.prev_hash != prev:
            return ChainStatus(False, count, entry.id, "chaînage rompu (entrée supprimée ou insérée)")
        expected = _mac(_canonical(entry.ts, entry.actor, entry.action, entry.details, entry.prev_hash))
        if not hmac.compare_digest(expected, entry.hash):
            return ChainStatus(False, count, entry.id, "contenu modifié")
        prev = entry.hash
    return ChainStatus(True, count)
