"""Synchronisation DSP2 : cas limites (consentement expiré, banque en panne, plusieurs connexions)."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
from sqlalchemy import select

from app.db import utcnow
from app.models import Account, AuditLog, BankConnection, ConnectionStatus, Transaction
from app.providers.enablebanking import RemoteAccount
from app.services.bank_sync import link_account, sync_all, sync_connection
from tests.test_enablebanking import PEM, FakeBank, make_client

TODAY = date(2026, 10, 21)


def _connection(db, name: str = "Banque A", uid: str = "uid-1") -> BankConnection:  # type: ignore[no-untyped-def]
    conn = BankConnection(aspsp_name=name, aspsp_country="FR", status=ConnectionStatus.ACTIVE, session_id="sess")
    db.add(conn)
    db.flush()
    link_account(db, conn, RemoteAccount(uid=uid, iban=None, name=f"Compte {name}", currency="EUR"))
    db.commit()
    return conn


def test_expired_consent_marks_connection(db) -> None:  # type: ignore[no-untyped-def]
    conn = _connection(db)
    conn.valid_until = utcnow() - timedelta(days=1)
    report = sync_connection(db, make_client(FakeBank()), conn, TODAY)
    assert conn.status == ConnectionStatus.EXPIRED and report.errors


def test_inactive_connection_is_skipped(db) -> None:  # type: ignore[no-untyped-def]
    conn = _connection(db)
    conn.status = ConnectionStatus.REVOKED
    assert sync_connection(db, make_client(FakeBank()), conn, TODAY).accounts == 0


def test_provider_auth_error_expires_connection(db) -> None:  # type: ignore[no-untyped-def]
    conn = _connection(db)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "session expired"})

    from app.providers.enablebanking import EnableBankingClient

    client = EnableBankingClient(
        "app-123", PEM, http=httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    )
    report = sync_connection(db, client, conn, TODAY)
    assert conn.status == ConnectionStatus.EXPIRED and "401" in (conn.last_error or "")
    assert report.errors
    assert db.scalar(select(AuditLog).where(AuditLog.action == "bank.sync")) is not None


def test_incremental_window_uses_overlap(db) -> None:  # type: ignore[no-untyped-def]
    bank = FakeBank()
    conn = _connection(db)
    conn.last_sync_at = utcnow() - timedelta(days=1)
    sync_connection(db, make_client(bank), conn, TODAY)
    tx_call = next(r for r in bank.calls if r.url.path.endswith("/transactions"))
    since = date.fromisoformat(tx_call.url.params["date_from"])
    assert (utcnow().date() - since).days == 8  # 1 jour + 7 jours de recouvrement


def test_sync_all_isolates_failures(app, db) -> None:  # type: ignore[no-untyped-def]
    _connection(db, "Banque A", "uid-1")
    _connection(db, "Banque B", "uid-2")
    bank = FakeBank()
    calls = {"n": 0}
    original = bank.handler

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/accounts/uid-2"):
            calls["n"] += 1
            raise RuntimeError("bug inattendu côté parsing")
        return original(request)

    bank.handler = flaky  # type: ignore[method-assign]
    report = sync_all(app.state.sessionmaker, make_client(bank), TODAY)
    assert report.accounts == 1 and report.inserted == 3  # la banque A est passée malgré la panne de B
    assert calls["n"] >= 1
    with app.state.sessionmaker() as fresh:
        assert fresh.scalar(select(Transaction).limit(1)) is not None
        assert len(list(fresh.scalars(select(Account)))) == 2
