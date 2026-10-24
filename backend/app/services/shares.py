"""Partage d'un papier par lien : un seul téléchargement, 48 h au plus, filigrane au nom du destinataire, journal,
révocation.

Le lien ne dit rien du document : un jeton aléatoire de 256 bits, que Bank ne garde que sous forme d'empreinte. La
copie filigranée est préparée à la création (tu vois tout de suite si le filigrane a échoué), chiffrée, puis effacée
dès qu'elle est téléchargée, révoquée ou expirée. Le téléchargement est consommé par une seule mise à jour
conditionnelle : deux ouvertures simultanées du même lien ne peuvent pas réussir toutes les deux.
"""

from __future__ import annotations

import secrets
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import reading
from app.models import VaultDocument, VaultShare
from app.security import audit
from app.security.crypto import get_keyring, sha256_hex
from app.services import vault
from app.services.vault import VaultError

DURATIONS_HOURS = (1, 24, 48)
RECIPIENT_MAX = 60
TOKEN_MAX = 128
PUBLIC_ACTOR = "lien de partage"


def _payload_context(share_id: int) -> str:
    return f"vault.share:{share_id}"


def clean_recipient(raw: str) -> str:
    """Nom du destinataire tel qu'il sera imprimé : caractères de contrôle et invisibles retirés, espaces resserrés,
    seulement ce que la police du filigrane sait écrire, 60 caractères au plus."""
    text = unicodedata.normalize("NFKC", raw or "")
    kept = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
            continue
        if unicodedata.category(ch).startswith("C"):
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            continue
        kept.append(ch)
    name = " ".join("".join(kept).split())[:RECIPIENT_MAX].strip()
    if len(name) < 2:
        raise VaultError("Indique à qui tu transmets ce papier")
    return name


def watermark(recipient: str, day: date) -> str:
    return f"Transmis à {recipient} le {day:%d/%m/%Y} par Bank — usage unique"


def state(share: VaultShare, now: datetime) -> str:
    if share.used_at is not None:
        return "used"
    if share.revoked_at is not None:
        return "revoked"
    return "expired" if share.expires_at <= now else "active"


def summary(share: VaultShare, now: datetime) -> dict[str, Any]:
    return {
        "id": share.id,
        "recipient": share.recipient,
        "created_at": share.created_at.isoformat(),
        "expires_at": share.expires_at.isoformat(),
        "used_at": share.used_at.isoformat() if share.used_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "state": state(share, now),
    }


def create(
    db: Session, doc_id: int, *, recipient: str, hours: int, actor: str, now: datetime, today: date
) -> tuple[VaultShare, str]:
    """Prépare la copie filigranée et le lien ; renvoie le jeton en clair (montré une seule fois, jamais gardé)."""
    if hours not in DURATIONS_HOURS:
        raise VaultError("Durée : 1 h, 24 h ou 48 h")
    name = clean_recipient(recipient)
    doc = db.get(VaultDocument, doc_id)
    if doc is None:
        raise VaultError("Document introuvable", 404)
    if doc.status == "processing":
        raise VaultError("Lecture en cours : attends qu'elle soit finie pour le partager", 409)
    # Rien n'est écrit avant le filigrane (jusqu'à 25 s dans l'enfant confiné) : le journal d'audit prend le verrou
    # d'écriture de SQLite, qui bloquerait sinon tout le reste de Bank — l'ouverture des liens comprise.
    raw = vault.original(db, doc.id)
    try:
        stamped = reading.stamp_pdf(raw, watermark(name, today))
    except reading.ReadingError as exc:
        raise VaultError(str(exc)) from exc
    audit.record(db, actor, "vault.file_read", document=doc.id)
    if db.scalar(select(VaultDocument.id).where(VaultDocument.id == doc.id)) is None:
        raise VaultError("Document introuvable", 404)  # effacé pendant le filigrane
    token = secrets.token_urlsafe(32)
    share = VaultShare(
        document_id=doc.id,
        token_hash=sha256_hex(token),
        recipient=name,
        created_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    db.add(share)
    db.flush()
    share.payload = get_keyring().encrypt_bytes(stamped, _payload_context(share.id))
    db.flush()
    audit.record(db, actor, "vault.share_create", document=doc.id, share=share.id, hours=hours)
    return share, token


def journal(db: Session, doc_id: int, *, now: datetime) -> list[dict[str, Any]]:
    if db.get(VaultDocument, doc_id) is None:
        raise VaultError("Document introuvable", 404)
    rows = db.scalars(
        select(VaultShare).where(VaultShare.document_id == doc_id).order_by(VaultShare.id.desc()).limit(50)
    )
    return [summary(s, now) for s in rows]


def revoke(db: Session, doc_id: int, share_id: int, *, actor: str, now: datetime) -> dict[str, Any]:
    share = db.get(VaultShare, share_id)
    if share is None or share.document_id != doc_id:
        raise VaultError("Partage introuvable", 404)
    if share.used_at is None and share.revoked_at is None:
        share.revoked_at, share.payload = now, None
        db.flush()
        audit.record(db, actor, "vault.share_revoke", document=doc_id, share=share.id)
    return summary(share, now)


def open_link(db: Session, token: str, *, now: datetime, ip: str) -> bytes | None:
    """La copie filigranée, une seule fois. None pour un lien inconnu, expiré, consommé ou révoqué : l'appelant
    répond pareil dans tous les cas."""
    digest = sha256_hex(token[:TOKEN_MAX])  # même travail pour tous les jetons, y compris absurdes
    write_lock(db)
    share = db.scalar(select(VaultShare).where(VaultShare.token_hash == digest))
    if share is None:
        return None
    if share.payload is None or state(share, now) != "active":
        audit.record(db, PUBLIC_ACTOR, "vault.share_refused", document=share.document_id, share=share.id, ip=ip)
        return None
    data = get_keyring().decrypt_bytes(share.payload, _payload_context(share.id))
    consumed = db.execute(
        update(VaultShare)
        .where(
            VaultShare.id == share.id,
            VaultShare.used_at.is_(None),
            VaultShare.revoked_at.is_(None),
            VaultShare.expires_at > now,
        )
        .values(used_at=now, payload=None)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:  # type: ignore[attr-defined]
        return None
    audit.record(db, PUBLIC_ACTOR, "vault.share_download", document=share.document_id, share=share.id, ip=ip)
    return data


def housekeeping(db: Session, *, now: datetime) -> dict[str, int]:
    """Copies filigranées des liens expirés : effacées (le journal reste)."""
    purged = db.execute(
        update(VaultShare)
        .where(VaultShare.payload.is_not(None), VaultShare.expires_at <= now)
        .values(payload=None)
        .execution_options(synchronize_session=False)
    )
    return {"shares_purged": int(purged.rowcount or 0)}  # type: ignore[attr-defined]
