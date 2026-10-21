"""Côté humain : jetons, onboarding « Ma situation », propositions, aperçu du dossier."""

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.api import deps
from tests.conftest import PASSWORD
from tests.scenario import TODAY, seed_household


@pytest.fixture
def seeded(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def test_endpoints_require_session(client: TestClient) -> None:
    for method, path in [
        ("get", "/api/agents/tokens"),
        ("post", "/api/agents/tokens"),
        ("get", "/api/onboarding"),
        ("put", "/api/onboarding/employment"),
        ("get", "/api/agents/proposals"),
        ("get", "/api/agents/dossier"),
        ("get", "/api/alerts"),
        ("get", "/api/agents/activity"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path


def test_token_lifecycle(auth_client: TestClient) -> None:
    cat = auth_client.get("/api/agents/catalog").json()
    assert "propose" in cat["scopes"] and cat["presets"]["observateur"] == ["read"]

    r = auth_client.post(
        "/api/agents/tokens",
        json={
            "name": "hermes",
            "preset": "conseiller",
            "privacy": "redacted",
            "allowed_ips": ["192.168.1.112"],
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    raw = body["token"]
    assert raw.startswith("bnk_") and body["prefix"] == raw[:10]
    assert body["mcp_url"] == "https://testserver/mcp"
    assert "Bearer ${BANK_MCP_TOKEN}" in body["hermes_config"] and body["hermes_env"] == f"BANK_MCP_TOKEN={raw}"
    assert body["allowed_ips"] == ["192.168.1.112/32"] and "write:categorize" not in body["scopes"]

    listed = auth_client.get("/api/agents/tokens").json()
    assert len(listed) == 1 and "token" not in listed[0] and listed[0]["state"] == "actif"
    assert raw not in auth_client.get("/api/agents/tokens").text  # le secret n'est montré qu'une fois

    dup = auth_client.post("/api/agents/tokens", json={"name": "hermes", "password": PASSWORD})
    assert dup.status_code == 409

    assert auth_client.delete(f"/api/agents/tokens/{body['id']}").status_code == 204
    assert auth_client.get("/api/agents/tokens").json()[0]["state"] == "revoque"
    assert auth_client.delete("/api/agents/tokens/999").status_code == 404
    # Après révocation, le nom est réutilisable.
    assert auth_client.post("/api/agents/tokens", json={"name": "hermes", "password": PASSWORD}).status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x"},
        {"name": "<script>"},
        {"name": "ok", "scopes": ["admin"]},
        {"name": "ok", "privacy": "tout"},
        {"name": "ok", "ttl_days": 0},
        {"name": "ok", "allowed_ips": ["pas une ip"]},
        {"name": "ok", "allowed_ips": [f"10.0.0.{i}" for i in range(11)]},
        {"name": "ok", "allowed_ips": ["192.168.1.112/24"]},  # bits d'hôte : refusé, pas élargi en silence
    ],
)
def test_token_creation_is_validated(auth_client: TestClient, payload: dict) -> None:  # type: ignore[type-arg]
    assert auth_client.post("/api/agents/tokens", json={"password": PASSWORD, **payload}).status_code == 422


def test_token_creation_requires_password(app, auth_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    assert auth_client.post("/api/agents/tokens", json={"name": "hermes"}).status_code == 422
    r = auth_client.post("/api/agents/tokens", json={"name": "hermes", "password": "pas le bon"})
    assert r.status_code == 403 and "token" not in r.text
    assert auth_client.get("/api/agents/tokens").json() == []
    from sqlalchemy import select

    from app.models import AuditLog

    with app.state.sessionmaker() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "agent.token.denied")) is not None


def test_token_creation_requires_csrf(auth_client: TestClient) -> None:
    token = auth_client.headers.pop("x-csrf-token")
    try:
        r = auth_client.post("/api/agents/tokens", json={"name": "hermes", "password": PASSWORD})
        assert r.status_code == 403
    finally:
        auth_client.headers["x-csrf-token"] = token


def test_onboarding_flow(seeded: TestClient) -> None:
    ob = seeded.get("/api/onboarding").json()
    assert ob["percent"] == 0 and {s["id"] for s in ob["sections"]} >= {"travail", "mandat"}
    debts_q = next(q for q in ob["questions"] if q["id"] == "debts")
    fields = {f["name"]: f for f in debts_q["item_fields"]}
    assert fields["remaining_cents"]["kind"] == "money" and fields["end_date"]["kind"] == "date"
    assert fields["kind"]["kind"] == "choice" and "conso" in fields["kind"]["options"]
    assert fields["rate_percent"]["kind"] == "decimal" and fields["label"]["required"]
    sugg = {s["question_id"]: s for s in ob["suggestions"]}
    assert sugg["net_monthly_income"]["value"] == 215_000 and sugg["housing_cost"]["value"] == 65_000

    # Confirmer une suggestion telle quelle, puis répondre au reste.
    assert seeded.put("/api/onboarding/net_monthly_income", json={"value": 215_000}).status_code == 200
    assert seeded.put("/api/onboarding/employment", json={"value": "public"}).status_code == 200
    assert seeded.put("/api/onboarding/autonomy", json={"value": "gestionnaire"}).status_code == 200
    r = seeded.put("/api/onboarding/red_lines", json={"value": [{"rule": "Jamais de crédit conso"}]})
    assert r.status_code == 200 and r.json()["value"] == [{"rule": "Jamais de crédit conso"}]
    assert seeded.put("/api/onboarding/employment", json={"value": "milliardaire"}).status_code == 422
    assert seeded.put("/api/onboarding/inconnue", json={"value": 1}).status_code == 404
    assert seeded.put("/api/onboarding/notes", json={"value": "x" * 30_000}).status_code == 422

    after = seeded.get("/api/onboarding").json()
    assert after["percent"] > 0 and after["answers"]["employment"]["source"] == "user:micka"
    assert "net_monthly_income" not in {s["question_id"] for s in after["suggestions"]}
    assert "employment" not in after["missing"]

    seeded.put("/api/onboarding/employment", json={"value": "cdd"})
    hist = seeded.get("/api/onboarding/employment/history").json()
    assert [h["value"] for h in hist] == ["cdd", "public"]
    assert seeded.delete("/api/onboarding/employment").status_code == 204
    assert "employment" not in seeded.get("/api/onboarding").json()["answers"]
    assert seeded.get("/api/onboarding/inconnue/history").status_code == 404
    assert seeded.delete("/api/onboarding/inconnue").status_code == 404


def test_planned_expense_enters_the_forecast(seeded: TestClient) -> None:
    before = seeded.get("/api/budget").json()["forecast"]
    r = seeded.put(
        "/api/onboarding/planned_expenses",
        json={"value": [{"label": "Pneus", "amount_cents": 32_000, "date": "2026-10-26"}]},
    )
    assert r.status_code == 200
    after = seeded.get("/api/budget").json()["forecast"]
    assert any(e["label"] == "Pneus" for e in after["upcoming"])
    assert after["lowest_balance_cents"] == before["lowest_balance_cents"] - 32_000


def test_dossier_preview_privacy_levels(seeded: TestClient) -> None:
    red = seeded.get("/api/agents/dossier").json()
    assert red["dossier"]["schema"] == "bank-dossier/v1" and red["dossier"]["privacy"] == "redacted"
    assert "DUPONT" not in red["markdown"] and "# Dossier financier" in red["markdown"]
    full = seeded.get("/api/agents/dossier", params={"privacy": "full"}).json()
    assert full["dossier"]["privacy"] == "full"
    assert seeded.get("/api/agents/dossier", params={"privacy": "tout"}).status_code == 422
    alerts = seeded.get("/api/alerts").json()
    assert any(a["code"] == "enveloppe:courses" for a in alerts)


def test_proposals_inbox(seeded: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    from app.services.proposals import create_proposal

    with app.state.sessionmaker() as db:
        p1 = create_proposal(
            db, agent="hermes", kind="set_envelope", params={"category": "courses", "monthly_eur": 650}, rationale="r"
        )
        p2 = create_proposal(db, agent="hermes", kind="set_buffer", params={"amount_eur": 150}, rationale="r")
        db.commit()
        ids = (p1.id, p2.id)
    with app.state.sessionmaker() as db:
        create_proposal(
            db,
            agent="hermes",
            kind="create_rule",
            params={"pattern": "carrefour", "category": "courses"},
            rationale="r",
        )
        db.commit()
    rule = next(p for p in seeded.get("/api/agents/proposals").json() if p["kind"] == "create_rule")
    assert rule["params"]["pattern"] == "CARREFOUR" and rule["impact"] > 10
    seeded.post(f"/api/agents/proposals/{rule['id']}/reject")
    assert seeded.get("/api/agents/summary").json() == {
        "pending_proposals": 2,
        "onboarding_percent": 0,
        "active_agents": 0,
    }
    pending = seeded.get("/api/agents/proposals").json()
    assert [p["id"] for p in pending] == [ids[1], ids[0]] and pending[0]["title"]
    ok = seeded.post(f"/api/agents/proposals/{ids[0]}/approve").json()
    assert ok["status"] == "applied"
    assert seeded.post(f"/api/agents/proposals/{ids[1]}/reject").json()["status"] == "rejected"
    assert seeded.post(f"/api/agents/proposals/{ids[1]}/approve").status_code == 409
    assert seeded.post("/api/agents/proposals/999/approve").status_code == 409
    assert seeded.get("/api/agents/proposals").json() == []
    assert len(seeded.get("/api/agents/proposals", params={"status": "all"}).json()) == 3
    cats = {c["slug"]: c for c in seeded.get("/api/categories").json()}
    assert cats["courses"]["monthly_budget_cents"] == 65_000
    activity = seeded.get("/api/agents/activity").json()
    assert activity and activity[0]["agent"] == "hermes" and activity[0]["action"] == "proposal.create"
