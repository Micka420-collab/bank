"""Connexion DSP2 (Enable Banking) : consentement, rattachement des comptes, synchronisation."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import utcnow
from app.models import Account, AccountKind, BankConnection, ConnectionStatus, Provider
from app.providers.enablebanking import EnableBankingClient, ProviderError, RemoteAccount
from app.security import audit
from app.security.crypto import get_keyring, sha256_hex
from app.services.ingest import ingest

log = logging.getLogger("bank.sync")
STATE_TTL = timedelta(minutes=15)
FIRST_SYNC_DAYS = 90  # sans nouvelle authentification forte, la plupart des banques limitent à 90 jours
OVERLAP_DAYS = 7  # recouvrement : rattrape les opérations comptabilisées tardivement


class BankFlowError(Exception):
    pass


def start_connection(db: Session, client: EnableBankingClient, aspsp_name: str, country: str, redirect_url: str) -> str:
    state = secrets.token_urlsafe(32)
    conn = BankConnection(
        aspsp_name=aspsp_name[:120],
        aspsp_country=country.upper()[:2],
        status=ConnectionStatus.PENDING,
        state_hash=sha256_hex(state),
        state_expires_at=utcnow() + STATE_TTL,
    )
    db.add(conn)
    db.flush()
    auth = client.start_auth(aspsp_name, country, redirect_url, state)
    audit.record(db, "user", "bank.connect.start", connection=conn.id, aspsp=aspsp_name)
    return auth.url


def complete_connection(db: Session, client: EnableBankingClient, code: str, state: str) -> BankConnection:
    conn = db.scalar(select(BankConnection).where(BankConnection.state_hash == sha256_hex(state)))
    if conn is None or conn.status != ConnectionStatus.PENDING:
        raise BankFlowError("Autorisation inconnue ou déjà utilisée")
    if conn.state_expires_at is None or conn.state_expires_at < utcnow():
        conn.status = ConnectionStatus.ERROR
        conn.last_error = "Autorisation expirée"
        raise BankFlowError("Autorisation expirée, recommence la connexion")
    info = client.create_session(code)
    conn.session_id = info.session_id
    conn.status = ConnectionStatus.ACTIVE
    conn.valid_until = info.valid_until
    conn.state_hash = None  # usage unique
    conn.state_expires_at = None
    for remote in info.accounts:
        link_account(db, conn, remote)
    audit.record(db, "user", "bank.connect.done", connection=conn.id, accounts=len(info.accounts))
    return conn


def link_account(db: Session, conn: BankConnection, remote: RemoteAccount) -> Account:
    """Rattache un compte distant. Les uid Enable Banking changent à chaque nouveau consentement,
    pas l'IBAN : on retrouve donc le compte (y compris un compte créé par import CSV) par l'index
    aveugle de l'IBAN — l'historique est conservé, rien n'est dupliqué."""
    kr = get_keyring()
    uid_idx = kr.blind_index(remote.uid, "account.provider_uid")
    iban_idx = kr.blind_index(remote.iban, "account.iban") if remote.iban else None
    acc = db.scalar(select(Account).where(Account.provider_uid_bidx == uid_idx))
    if acc is None and iban_idx:
        acc = db.scalar(select(Account).where(Account.iban_bidx == iban_idx))
    if acc is None:
        suffix = f" ••{remote.iban[-4:]}" if remote.iban else ""
        acc = Account(
            name=(remote.name or conn.aspsp_name)[:100] + suffix,
            kind=AccountKind.CHECKING,
            institution=conn.aspsp_name,
            currency=remote.currency,
        )
        db.add(acc)
    acc.provider = Provider.ENABLEBANKING
    acc.provider_uid = remote.uid
    acc.provider_uid_bidx = uid_idx
    if remote.iban:
        acc.iban, acc.iban_bidx = remote.iban, iban_idx
    acc.connection = conn
    db.flush()
    return acc


@dataclass
class SyncReport:
    accounts: int = 0
    inserted: int = 0
    duplicates: int = 0
    reconciled: int = 0
    errors: list[str] = field(default_factory=list)


def sync_connection(db: Session, client: EnableBankingClient, conn: BankConnection, today: date) -> SyncReport:
    report = SyncReport()
    if conn.status != ConnectionStatus.ACTIVE or not conn.session_id:
        return report
    if conn.valid_until and conn.valid_until < utcnow():
        conn.status = ConnectionStatus.EXPIRED
        report.errors.append("Consentement expiré : reconnecte la banque")
        return report
    since = (
        (conn.last_sync_at.date() - timedelta(days=OVERLAP_DAYS))
        if conn.last_sync_at
        else today - timedelta(days=FIRST_SYNC_DAYS)
    )
    try:
        for acc in [a for a in conn.accounts if not a.archived and a.provider_uid]:
            uid = acc.provider_uid or ""
            balance = client.get_balance(uid)
            if balance is not None:
                acc.balance_cents, acc.balance_at = balance[0], utcnow()
            booked = [t for t in client.iter_transactions(uid, since) if t.status == "booked"]
            r = ingest(db, acc, booked, provider="enablebanking")
            report.accounts += 1
            report.inserted += r.inserted
            report.duplicates += r.duplicates
            report.reconciled += r.reconciled
        conn.last_sync_at = utcnow()
        conn.last_error = None
    except ProviderError as exc:
        conn.last_error = str(exc)[:300]
        if exc.status in (401, 403):
            conn.status = ConnectionStatus.EXPIRED
        report.errors.append(conn.last_error)
    audit.record(db, "system", "bank.sync", connection=conn.id, inserted=report.inserted, errors=len(report.errors))
    return report


def sync_all(factory: sessionmaker[Session], client: EnableBankingClient, today: date) -> SyncReport:
    total = SyncReport()
    with factory() as db:
        conns = list(db.scalars(select(BankConnection).where(BankConnection.status == ConnectionStatus.ACTIVE)))
        for conn in conns:
            try:
                r = sync_connection(db, client, conn, today)
                db.commit()
            except Exception:  # une banque en panne ne bloque pas les autres
                db.rollback()
                log.exception("sync connection %s failed", conn.id)
                continue
            total.accounts += r.accounts
            total.inserted += r.inserted
            total.errors.extend(r.errors)
    return total
