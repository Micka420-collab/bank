"""Local read-only holdings: no guessed product sheets, duplicated asset total or stale crypto price."""

from __future__ import annotations

from datetime import date, timedelta

from starlette.testclient import TestClient

from app.db import utcnow
from app.models import Account, AccountKind, CryptoHolding, CryptoPrice, Provider
from app.services import advisor, investment_holdings

TODAY = date(2026, 10, 28)


def test_non_eur_account_is_not_implicitly_converted() -> None:
    position = investment_holdings.account_row(
        item_id=2,
        name="Compte étranger",
        kind="pea",
        currency="USD",
        balance_cents=400_000,
        balance_at=utcnow(),
        provider=Provider.MANUAL,
        today=TODAY,
        rates={},
    )
    assert position["currency"] == "USD"
    assert position["value_cents"] is None
    assert position["valuation_status"] == "devise_non_convertie"
    assert position["rate_pct"] is None
    assert not position["document_verified"]


def test_holding_inventory_uses_real_accounts_and_marks_stale_crypto_and_duplicates(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        db.add(
            Account(
                name="PEA banque test", kind=AccountKind.PEA, currency="EUR",
                provider=Provider.MANUAL, balance_cents=250_000, balance_at=utcnow(),
            )
        )
        advisor.set_answer(
            db,
            "outside_assets",
            [{"kind": "pea", "label": "PEA indiqué par ailleurs", "value_cents": 250_000}],
            source="user:web",
        )
        db.add(CryptoHolding(asset="BTC", quantity="1.25", platform="test"))
        db.add(CryptoPrice(asset="BTC", eur="50000", fetched_at=utcnow() - timedelta(days=5)))
        db.commit()
    result = world.get("/api/watch/investments/holdings")
    assert result.status_code == 200, result.text
    items = result.json()["positions"]
    tracked = next(p for p in items if p["label"] == "PEA banque test")
    assert tracked["value_cents"] == 250_000
    assert tracked["origin"] == "compte"
    assert tracked["valuation_status"] == "valeur_declaree_non_verifiee"
    assert tracked["document_verified"] is False
    assert tracked["rate_pct"] is None
    declared = next(p for p in items if p["label"] == "PEA indiqué par ailleurs")
    assert declared["potential_overlap"] is True
    assert declared["valuation_status"] == "valeur_declaree_non_verifiee"
    crypto = next(p for p in items if p["key"] == "crypto:BTC")
    assert crypto["quantity"] == "1.25"
    assert crypto["value_cents"] is None, "A stale price cannot be passed off as a current holding value"
    assert crypto["valuation_status"] == "cours_absent_ou_perime"
    assert not any("iban" in p for p in items)
    assert result.json()["counts"]["potential_overlaps"] >= 1
    assert "sans total consolidé" in result.json()["note"]


def test_holdings_api_requires_login(client: TestClient) -> None:
    response = client.get("/api/watch/investments/holdings")
    assert response.status_code in (401, 403)
