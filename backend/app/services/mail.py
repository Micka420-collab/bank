"""Boîte mail en lecture seule (IMAP) : Bank ouvre UN dossier en lecture seule (EXAMINE), ne lit que les messages
nouveaux depuis la dernière fois (UID), en borne le nombre et la taille, en extrait les PDF joints et les propose
dans Papiers avec le genre probable ; l'import se fait à ta demande (le message est relu, la pièce vérifiée par son
empreinte, puis déposée au coffre par le circuit habituel). BODY.PEEK : rien n'est marqué lu ; jamais de STORE, de
COPY ni d'EXPUNGE. Identifiants chiffrés, jamais renvoyés, jamais dans une erreur. TLS exigé (SSL ou STARTTLS)."""

from __future__ import annotations

import contextlib
import imaplib
import json
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import mail as dom
from app.domain import vault as vault_domain
from app.models import MailAccount, MailProposal, VaultDocument
from app.security import audit
from app.services import consents, vault

MAX_MESSAGES_PER_SCAN = 40
MAX_PROPOSALS = 200
SCAN_EVERY = timedelta(minutes=55)
TIMEOUT = 20.0
SECURITIES = ("ssl", "starttls")
HEADER_FIELDS = "(FROM SUBJECT DATE MESSAGE-ID CONTENT-TYPE)"

Client = Any  # imaplib.IMAP4 ou IMAP4_SSL ; en test, un client vers le faux serveur
ClientFactory = Callable[[str, int, str], Client]


class MailError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def default_client(host: str, port: int, security: str) -> Client:
    """TLS obligatoire : IMAPS (993) ou STARTTLS, certificats vérifiés avec le magasin du système."""
    ctx = ssl.create_default_context()
    if security == "ssl":
        return imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=TIMEOUT)
    client = imaplib.IMAP4(host, port, timeout=TIMEOUT)
    client.starttls(ctx)
    return client


# ------------------------------------------------------------------ configuration
def account(db: Session) -> MailAccount | None:
    return db.get(MailAccount, 1)


def _hint(username: str) -> str:
    local, _, domain = username.partition("@")
    return f"{local[:2]}…@{domain}" if domain else f"{username[:2]}…"


def _senders(row: MailAccount) -> tuple[str, ...]:
    try:
        raw = json.loads(row.senders or "[]")
    except ValueError:
        return ()
    return tuple(str(s) for s in raw if isinstance(s, str))[:50]


def configure(
    db: Session,
    *,
    actor: str,
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    folder: str,
    senders: list[str],
    factory: ClientFactory = default_client,
) -> MailAccount:
    """Enregistre la boîte après un essai réussi (connexion, identification, ouverture du dossier en lecture seule).
    Un essai raté n'enregistre rien et ne garde pas le mot de passe."""
    try:
        clean_host = dom.valid_host(host)
        clean_folder = dom.valid_folder(folder)
    except ValueError as exc:
        raise MailError(str(exc), 422) from exc
    if not 1 <= port <= 65535:
        raise MailError("Port invalide", 422)
    if security not in SECURITIES:
        raise MailError("Sécurité : ssl ou starttls", 422)
    user = username.strip()
    if not user or len(user) > 200 or not password or len(password) > 500:
        raise MailError("Identifiant ou mot de passe d'application manquant", 422)
    clean_senders = [s.strip().lower() for s in senders if s.strip()][:50]
    if any(len(s) > 120 or any(c in s for c in ' "\\\r\n') for s in clean_senders):
        raise MailError("Liste d'expéditeurs : adresses ou domaines, un par ligne", 422)
    count = _probe(clean_host, port, security, user, password, clean_folder, factory)
    row = account(db) or MailAccount(id=1)
    row.host, row.port, row.security = clean_host, port, security
    row.username, row.username_hint, row.password = user, _hint(user), password
    row.folder, row.senders = clean_folder, json.dumps(clean_senders)
    row.status, row.last_error, row.last_uid, row.uid_validity = "active", None, 0, None
    db.add(row)
    db.flush()
    audit.record(db, actor, "mail.configure", host=clean_host, folder=clean_folder, messages=count)
    return row


def remove(db: Session, *, actor: str) -> None:
    row = account(db)
    if row is None:
        raise MailError("Aucune boîte mail reliée", 404)
    db.delete(row)
    for p in db.scalars(select(MailProposal).where(MailProposal.status == "proposed")):
        db.delete(p)
    db.flush()
    audit.record(db, actor, "mail.remove")


def _probe(host: str, port: int, security: str, user: str, password: str, folder: str, factory: ClientFactory) -> int:
    try:
        client = factory(host, port, security)
    except (OSError, ssl.SSLError, imaplib.IMAP4.error) as exc:
        raise MailError(f"Serveur injoignable : {dom.scrub(str(exc), user, password)}", 502) from exc
    try:
        try:
            client.login(user, password)
        except imaplib.IMAP4.error as exc:
            raise MailError(f"Identification refusée : {dom.scrub(str(exc), user, password)}", 401) from exc
        typ, data = client.select(folder, readonly=True)
        if typ != "OK":
            raise MailError(f"Dossier « {folder} » introuvable", 422)
        try:
            return int(data[0] or 0)
        except (TypeError, ValueError):
            return 0
    finally:
        _quit(client)


def _quit(client: Client) -> None:
    if client is None:
        return
    with contextlib.suppress(OSError, imaplib.IMAP4.error):
        client.logout()


def probe(db: Session, *, factory: ClientFactory = default_client) -> dict[str, Any]:
    row = account(db)
    if row is None:
        raise MailError("Aucune boîte mail reliée", 404)
    count = _probe(row.host, row.port, row.security, row.username, row.password, row.folder, factory)
    row.status, row.last_error = "active", None
    return {"ok": True, "messages": count}


# ------------------------------------------------------------------ lecture
@dataclass
class ScanReport:
    scanned: int = 0
    proposed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


def _uids(data: list[bytes | None]) -> list[int]:
    raw = data[0] if data else b""
    text = raw.decode("ascii", "replace") if isinstance(raw, bytes) else str(raw or "")
    return [int(x) for x in text.split() if x.isdigit()]


def _fetch_blocks(data: list[Any]) -> list[tuple[bytes, bytes]]:
    """imaplib rend un FETCH comme une liste de (en-tête, corps) et de b')' : on garde les paires."""
    return [(item[0], item[1]) for item in data if isinstance(item, tuple) and len(item) == 2]


def _uid_in(header: bytes) -> int | None:
    m = re.search(rb"UID (\d+)", header)
    return int(m.group(1)) if m else None


def _size_in(header: bytes) -> int | None:
    m = re.search(rb"RFC822\.SIZE (\d+)", header)
    return int(m.group(1)) if m else None


def _known_fingerprints(db: Session) -> set[str]:
    known = {s for (s,) in db.execute(select(VaultDocument.sha256))}
    known |= {s for (s,) in db.execute(select(MailProposal.part_sha256))}
    return known


def scan(db: Session, *, now: datetime | None = None, factory: ClientFactory = default_client) -> ScanReport:
    """Lit les messages nouveaux (au plus 40), propose les PDF joints inconnus. Toute erreur est consignée sur la
    boîte (sans identifiant) et laisse le dernier UID où il était : la prochaine lecture reprend."""
    row = account(db)
    report = ScanReport()
    if row is None:
        return report
    if not consents.granted(db, "mail"):
        report.notes.append("accord « mail » absent : rien lu")
        return report
    moment = now or utcnow()
    allow = _senders(row)
    user, password = row.username, row.password
    try:
        client = factory(row.host, row.port, row.security)
    except (OSError, ssl.SSLError, imaplib.IMAP4.error) as exc:
        return _fail(db, row, f"Serveur injoignable : {dom.scrub(str(exc), user, password)}", report)
    try:
        try:
            client.login(user, password)
            typ, data = client.select(row.folder, readonly=True)
            if typ != "OK":
                return _fail(db, row, f"Dossier « {row.folder} » introuvable", report)
            validity = _uid_validity(client)
            if validity is not None and row.uid_validity not in (None, validity):
                row.last_uid = 0  # la boîte a été recréée : on relit depuis le début
            row.uid_validity = validity
            typ, data = client.uid("SEARCH", None, f"UID {row.last_uid + 1}:*")
            uids = [u for u in _uids(data) if u > row.last_uid]
            uids.sort()
            batch = uids[:MAX_MESSAGES_PER_SCAN]
            if len(uids) > MAX_MESSAGES_PER_SCAN:
                report.notes.append(f"{len(uids) - MAX_MESSAGES_PER_SCAN} messages restent à lire la prochaine fois")
            known = _known_fingerprints(db)
            for uid in batch:
                report.scanned += 1
                row.last_uid = max(row.last_uid, uid)
                try:
                    report.proposed += _scan_one(db, client, uid, allow, known, moment, report)
                except (imaplib.IMAP4.error, OSError, ValueError) as exc:
                    report.notes.append(f"message {uid} illisible : {dom.scrub(str(exc), user, password)}")
                    report.skipped += 1
        except imaplib.IMAP4.error as exc:
            return _fail(db, row, f"Identification refusée : {dom.scrub(str(exc), user, password)}", report)
        except (TimeoutError, OSError) as exc:
            return _fail(db, row, f"Connexion perdue : {dom.scrub(str(exc), user, password)}", report)
    finally:
        _quit(client)
    row.status, row.last_error, row.last_sync_at = "active", None, moment
    _prune(db)
    if report.proposed:
        audit.record(db, "système", "mail.scan", proposed=report.proposed, scanned=report.scanned)
    return report


def _uid_validity(client: Client) -> int | None:
    raw = client.response("UIDVALIDITY")[1]
    for item in raw or []:
        if isinstance(item, bytes) and item.isdigit():
            return int(item)
    return None


def _scan_one(
    db: Session,
    client: Client,
    uid: int,
    allow: tuple[str, ...],
    known: set[str],
    moment: datetime,
    report: ScanReport,
) -> int:
    typ, data = client.uid("FETCH", str(uid), f"(RFC822.SIZE BODY.PEEK[HEADER.FIELDS {HEADER_FIELDS}])")
    blocks = _fetch_blocks(data)
    if typ != "OK" or not blocks:
        return 0
    header_meta, header_bytes = blocks[0]
    headers = dom.parse_headers(header_bytes)
    size = _size_in(header_meta) or 0
    if not dom.sender_allowed(headers, allow):
        report.skipped += 1
        return 0
    if not headers.content_type.startswith("multipart/"):
        return 0  # pas de pièce jointe possible : on ne télécharge pas le message
    if size > dom.MAX_MESSAGE_BYTES:
        report.notes.append(f"message {uid} trop lourd ({size // 1_000_000} Mo) : ignoré")
        report.skipped += 1
        return 0
    typ, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
    blocks = _fetch_blocks(data)
    if typ != "OK" or not blocks:
        return 0
    raw = blocks[0][1]
    attachments, skipped = dom.attachments(raw)
    report.notes.extend(f"message {uid} : {s}" for s in skipped)
    created = 0
    for att in attachments:
        if att.sha256 in known:
            report.skipped += 1
            continue
        kind, confidence = dom.guess_kind(headers, att.filename)
        db.add(
            MailProposal(
                uid=uid,
                part_sha256=att.sha256,
                sender=f"{headers.sender_name} <{headers.sender}>" if headers.sender_name else headers.sender,
                sender_domain=headers.domain,
                subject=headers.subject or "(sans objet)",
                filename=att.filename,
                size=att.size,
                kind_guess=kind,
                confidence=confidence,
                received_at=headers.received_at or moment,
            )
        )
        known.add(att.sha256)
        created += 1
    db.flush()
    return created


def _fail(db: Session, row: MailAccount, message: str, report: ScanReport) -> ScanReport:
    row.status, row.last_error = "error", message[:300]
    report.notes.append(message)
    db.flush()
    return report


def _prune(db: Session) -> None:
    rows = list(db.scalars(select(MailProposal).order_by(MailProposal.id.desc()).offset(MAX_PROPOSALS)))
    for r in rows:
        db.delete(r)


def due(row: MailAccount | None, now: datetime) -> bool:
    if row is None:
        return False
    if row.last_sync_at is None:
        return True
    return now - row.last_sync_at >= SCAN_EVERY


def tick(db: Session, *, now: datetime | None = None, factory: ClientFactory = default_client) -> dict[str, int]:
    moment = now or utcnow()
    row = account(db)
    if not due(row, moment):
        return {"mail_scanned": 0, "mail_proposed": 0}
    report = scan(db, now=moment, factory=factory)
    return {"mail_scanned": report.scanned, "mail_proposed": report.proposed}


# ------------------------------------------------------------------ import à la demande
def import_proposal(
    db: Session,
    proposal_id: int,
    *,
    actor: str,
    file_limit: int,
    vault_limit: int,
    factory: ClientFactory = default_client,
) -> list[int]:
    """Relit le message, retrouve la pièce par son empreinte, la dépose au coffre (source « mail »). Renvoie les
    identifiants des documents créés (à lire en tâche de fond)."""
    prop = db.get(MailProposal, proposal_id)
    if prop is None or prop.status != "proposed":
        raise MailError("Proposition introuvable", 404)
    row = account(db)
    if row is None:
        raise MailError("Aucune boîte mail reliée", 404)
    if not consents.granted(db, "mail"):
        raise MailError(consents.PURPOSES["mail"]["title"] + " : accord requis", 403)
    user, password = row.username, row.password
    client: Client = None
    try:
        client = factory(row.host, row.port, row.security)
        client.login(user, password)
        typ, _ = client.select(row.folder, readonly=True)
        if typ != "OK":
            raise MailError("Dossier introuvable", 422)
        typ, data = client.uid("FETCH", str(prop.uid), "(BODY.PEEK[])")
    except imaplib.IMAP4.error as exc:
        raise MailError(f"Boîte mail : {dom.scrub(str(exc), user, password)}", 502) from exc
    except (OSError, ssl.SSLError) as exc:
        raise MailError(f"Serveur injoignable : {dom.scrub(str(exc), user, password)}", 502) from exc
    finally:
        _quit(client)
    blocks = _fetch_blocks(data) if typ == "OK" else []
    if not blocks:
        raise MailError("Le message n'est plus dans la boîte", 410)
    attachments, _ = dom.attachments(blocks[0][1])
    match = next((a for a in attachments if a.sha256 == prop.part_sha256), None)
    if match is None:
        raise MailError("La pièce jointe a changé ou a disparu", 410)
    created, skipped = vault.ingest(
        db,
        [vault.Incoming(match.filename, match.data)],
        source="mail",
        actor=actor,
        file_limit=file_limit,
        vault_limit=vault_limit,
    )
    if not created:
        reason = skipped[0]["reason"] if skipped else "refusé"
        if skipped and skipped[0].get("document_id"):
            prop.status, prop.document_id, prop.decided_at = "imported", int(skipped[0]["document_id"]), utcnow()
            db.flush()
            return []
        raise MailError(f"Import refusé : {reason}", 422)
    doc = created[0]
    if prop.kind_guess != "other" and prop.confidence >= 0.7:
        doc.kind = prop.kind_guess  # le lecteur confirmera ou corrigera à la lecture
    prop.status, prop.document_id, prop.decided_at = "imported", doc.id, utcnow()
    db.flush()
    audit.record(db, actor, "mail.import", proposal_id=prop.id, document_id=doc.id, kind=prop.kind_guess)
    return [doc.id]


def dismiss(db: Session, proposal_id: int, *, actor: str) -> None:
    prop = db.get(MailProposal, proposal_id)
    if prop is None or prop.status != "proposed":
        raise MailError("Proposition introuvable", 404)
    prop.status, prop.decided_at = "dismissed", utcnow()
    db.flush()
    audit.record(db, actor, "mail.dismiss", proposal_id=prop.id)


# ------------------------------------------------------------------ vues
def _kind_label(kind: str) -> str:
    spec = vault_domain.KINDS.get(kind)
    return spec.label if spec else "Autre document"


def proposal_view(p: MailProposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "sender": p.sender,
        "sender_domain": p.sender_domain,
        "subject": p.subject,
        "filename": p.filename,
        "size": p.size,
        "kind_guess": p.kind_guess,
        "kind_label": _kind_label(p.kind_guess),
        "confidence": p.confidence,
        "received_at": p.received_at.isoformat() if p.received_at else None,
        "status": p.status,
        "document_id": p.document_id,
    }


def status(db: Session) -> dict[str, Any]:
    row = account(db)
    proposals = list(
        db.scalars(
            select(MailProposal).where(MailProposal.status == "proposed").order_by(MailProposal.received_at.desc())
        )
    )
    recent = list(
        db.scalars(
            select(MailProposal)
            .where(MailProposal.status != "proposed")
            .order_by(MailProposal.decided_at.desc())
            .limit(10)
        )
    )
    return {
        "configured": row is not None,
        "consent": consents.granted(db, "mail"),
        "asked": consents.decided(db, "mail"),
        "account": None
        if row is None
        else {
            "host": row.host,
            "port": row.port,
            "security": row.security,
            "username_hint": row.username_hint,
            "folder": row.folder,
            "senders": list(_senders(row)),
            "status": row.status,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
            "last_error": row.last_error,
            "last_uid": row.last_uid,
        },
        "proposals": [proposal_view(p) for p in proposals],
        "recent": [proposal_view(p) for p in recent],
        "limits": {
            "messages_per_scan": MAX_MESSAGES_PER_SCAN,
            "attachment_bytes": dom.MAX_ATTACHMENT_BYTES,
            "every_minutes": int(SCAN_EVERY.total_seconds() // 60),
        },
    }
