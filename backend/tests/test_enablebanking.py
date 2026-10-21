from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.providers.enablebanking import EnableBankingClient, ProviderError, map_transaction

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class FakeBank:
    """Faux serveur Enable Banking : vérifie chaque JWT avec la clé publique, comme le vrai."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.fail_next: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        token = request.headers["authorization"].removeprefix("Bearer ")
        header = jwt.get_unverified_header(token)
        assert header["kid"] == "app-123" and header["alg"] == "RS256" and header["typ"] == "JWT"
        # Horloge simulée : on vérifie signature/audience/émetteur, et la durée de vie à la main.
        claims = jwt.decode(
            token,
            KEY.public_key(),
            algorithms=["RS256"],
            audience="api.enablebanking.com",
            issuer="enablebanking.com",
            options={"verify_iat": False, "verify_exp": False},
        )
        assert 0 < claims["exp"] - claims["iat"] <= 86400
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"retry-after": "0"})
        path, params = request.url.path, dict(request.url.params)
        if path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [{"name": "Crédit Agricole", "country": "FR"}]})
        if path == "/auth":
            body = json.loads(request.content)
            assert body["psu_type"] == "personal" and body["aspsp"]["country"] == "FR"
            return httpx.Response(
                200, json={"url": "https://tilisy.enablebanking.com/auth?x=1", "authorization_id": "a1"}
            )
        if path == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "sess-1",
                    "access": {"valid_until": "2027-01-19T10:00:00+00:00"},
                    "accounts": [
                        {
                            "uid": "uid-1",
                            "account_id": {"iban": "FR7630004000031234567890143"},
                            "name": "Compte courant",
                            "currency": "EUR",
                        }
                    ],
                },
            )
        if path.endswith("/balances"):
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {"balance_type": "CLBD", "balance_amount": {"amount": "999.99", "currency": "EUR"}},
                        {"balance_type": "ITAV", "balance_amount": {"amount": "1234.56", "currency": "EUR"}},
                    ]
                },
            )
        if path.endswith("/transactions"):
            if params.get("continuation_key") == "p2":
                return httpx.Response(200, json={"transactions": [_raw("e3", "12.00", "DBIT", "2026-10-03")]})
            return httpx.Response(
                200,
                json={
                    "continuation_key": "p2",
                    "transactions": [
                        _raw("e1", "650.00", "DBIT", "2026-10-01", ["PRLV SEPA FONCIA"]),
                        _raw("e2", "258.29", "CRDT", "2026-10-05", ["CAF LOIRET PRIME ACTIVITE"]),
                        {**_raw("e4", "5.00", "DBIT", "2026-10-06"), "status": "PDNG"},
                    ],
                },
            )
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "OK"})
        return httpx.Response(404, json={"message": "not found"})


def _raw(ref: str, amount: str, ind: str, day: str, remittance: list[str] | None = None) -> dict[str, Any]:
    return {
        "entry_reference": ref,
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "credit_debit_indicator": ind,
        "status": "BOOK",
        "booking_date": day,
        "value_date": day,
        "remittance_information": remittance or [],
        "creditor": {"name": "MARCHAND"},
        "debtor": {"name": "EMETTEUR"},
    }


def make_client(bank: FakeBank, clock: list[float] | None = None) -> EnableBankingClient:
    t = clock if clock is not None else [1_800_000_000.0]
    http = httpx.Client(base_url="https://api.enablebanking.com", transport=httpx.MockTransport(bank.handler))
    return EnableBankingClient("app-123", PEM, http=http, clock=lambda: t[0], sleep=lambda _s: None)


def test_full_flow() -> None:
    bank = FakeBank()
    c = make_client(bank)
    assert c.list_aspsps("FR")[0]["name"] == "Crédit Agricole"
    auth = c.start_auth("Crédit Agricole", "FR", "https://bank.lan/api/banks/callback", "state-xyz")
    assert auth.url.startswith("https://") and auth.authorization_id == "a1"
    sess = c.create_session("code-1")
    assert sess.session_id == "sess-1" and sess.accounts[0].iban == "FR7630004000031234567890143"
    assert sess.valid_until is not None and sess.valid_until.year == 2027
    assert c.get_balance("uid-1") == (123456, "EUR")  # ITAV préféré à CLBD
    txns = list(c.iter_transactions("uid-1", date(2026, 7, 1)))
    assert [(t.amount_cents, t.external_id, t.status) for t in txns] == [
        (-65000, "e1", "booked"),
        (25829, "e2", "booked"),
        (-500, "e4", "pending"),
        (-1200, "e3", "booked"),
    ]
    assert txns[0].label == "PRLV SEPA FONCIA"
    assert txns[3].label == "MARCHAND"  # pas de remittance : nom du créancier pour un débit
    c.delete_session("sess-1")


def test_jwt_is_cached_then_refreshed() -> None:
    bank = FakeBank()
    clock = [1_800_000_000.0]
    c = make_client(bank, clock)
    c.list_aspsps()
    c.list_aspsps()
    tokens = {r.headers["authorization"] for r in bank.calls}
    assert len(tokens) == 1
    clock[0] += 3600  # proche de l'expiration -> nouveau jeton
    c.list_aspsps()
    assert len({r.headers["authorization"] for r in bank.calls}) == 2


def test_retries_on_429_and_5xx() -> None:
    bank = FakeBank()
    bank.fail_next = [429, 503]
    c = make_client(bank)
    assert c.list_aspsps()
    assert len(bank.calls) == 3


def test_gives_up_after_three_failures() -> None:
    bank = FakeBank()
    bank.fail_next = [500, 500, 500]
    with pytest.raises(ProviderError):
        make_client(bank).list_aspsps()


def test_client_error_not_retried() -> None:
    bank = FakeBank()
    c = make_client(bank)
    with pytest.raises(ProviderError) as exc:
        c._request("GET", "/unknown")
    assert exc.value.status == 404 and len(bank.calls) == 1


def test_invalid_auth_url_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": "javascript:alert(1)"})

    http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    c = EnableBankingClient("app-123", PEM, http=http)
    with pytest.raises(ProviderError):
        c.start_auth("B", "FR", "https://cb", "s")


def test_transport_errors_retried() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("boom")

    http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    c = EnableBankingClient("app-123", PEM, http=http, sleep=lambda _s: None)
    with pytest.raises(ProviderError):
        c.list_aspsps()
    assert attempts["n"] == 3


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"transaction_amount": {"amount": "abc"}}, None),
        ({"transaction_amount": {"amount": "1.00"}, "credit_debit_indicator": "CRDT"}, None),  # aucune date
        (
            {
                "transaction_amount": {"amount": "1.00"},
                "credit_debit_indicator": "CRDT",
                "value_date": "2026-10-01",
                "debtor": {"name": "CAF"},
            },
            (100, "CAF"),
        ),
        (
            {
                "transaction_amount": {"amount": "-3.50"},
                "credit_debit_indicator": "DBIT",
                "transaction_date": "2026-10-01",
            },
            (-350, "(sans libellé)"),
        ),
    ],
)
def test_map_transaction_edge_cases(raw: dict[str, Any], expected: tuple[int, str] | None) -> None:
    t = map_transaction(raw)
    assert (None if t is None else (t.amount_cents, t.label)) == expected
