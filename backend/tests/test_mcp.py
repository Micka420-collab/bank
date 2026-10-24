"""Côté agent : le serveur MCP tel que HERMÈS le voit, en JSON-RPC sur HTTP, avec toutes les barrières."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.models import AgentProposal, AuditLog, Transaction
from app.security import audit
from app.security.agents import PRESETS, issue_agent_token
from tests.conftest import PASSWORD
from tests.scenario import INJECTION, TODAY, seed_household

PROTOCOL = "2025-06-18"


class Agent:
    """Client MCP minimal (streamable HTTP, réponses JSON) : ce que fait Hermes, sans magie."""

    def __init__(self, client: TestClient, token: str | None) -> None:
        self.client = client
        self.token = token
        self._id = 0

    def headers(self, **extra: str) -> dict[str, str]:
        h = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": PROTOCOL,
        }
        if self.token:
            h["authorization"] = f"Bearer {self.token}"
        return {**h, **extra}

    def post(self, method: str, params: dict[str, Any] | None = None, **headers: str):  # type: ignore[no-untyped-def]
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        return self.client.post("/mcp", json=body, headers=self.headers(**headers))

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.post(method, params)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert "error" not in payload, payload
        result: dict[str, Any] = payload["result"]
        return result

    def call(self, tool: str, **arguments: Any) -> tuple[bool, Any]:
        res = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if res.get("isError"):
            return True, res["content"][0]["text"]
        data = res.get("structuredContent")
        if data is None:
            data = json.loads(res["content"][0]["text"])
        return False, data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data

    def ok(self, tool: str, **arguments: Any) -> Any:
        err, data = self.call(tool, **arguments)
        assert not err, data
        return data

    def fails(self, tool: str, **arguments: Any) -> str:
        err, data = self.call(tool, **arguments)
        assert err, data
        return str(data)


@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def make_agent(app, client: TestClient, preset: str = "conseiller", **kw: Any) -> Agent:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        _, raw = issue_agent_token(db, name=kw.pop("name", "hermes"), scopes=PRESETS[preset], **kw)
        db.commit()
    return Agent(client, raw)


# ------------------------------------------------------------------ porte d'entrée
def test_requests_without_valid_token_never_reach_the_protocol(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    for token in (None, "bnk_inconnu", "Bearer ?"):
        r = Agent(world, token).post("tools/list")
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
        assert "tools" not in r.text
    # Le cookie de session humain n'ouvre pas le serveur MCP.
    assert Agent(world, None).post("tools/list").status_code == 401
    # Même réponse sur GET (flux SSE) : rien ne fuit.
    assert world.get("/mcp").status_code == 401


def test_security_middlewares_apply_to_mcp(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    r = agent.post("tools/list")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    # DNS rebinding : un Host étranger est refusé avant tout.
    assert agent.post("tools/list", host="evil.example").status_code == 400
    # Une page web malveillante ne peut pas piloter l'agent depuis le navigateur.
    assert agent.post("tools/list", origin="https://evil.example").status_code in (400, 403)
    # Corps trop gros.
    big = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"x": "a" * 400_000}}
    assert world.post("/mcp", json=big, headers=agent.headers()).status_code == 413


def test_initialize_and_catalogue(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    init = agent.rpc(
        "initialize",
        {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "hermes", "version": "1"}},
    )
    assert init["serverInfo"]["name"] == "bank" and "get_dossier" in init["instructions"]
    tools = {t["name"]: t for t in agent.rpc("tools/list")["tools"]}
    assert set(tools) == {
        "get_dossier", "get_alerts", "get_month", "spending_summary", "list_transactions", "get_aids",
        "simulate_aids", "get_wealth", "evaluate_purchase", "what_if_savings", "onboarding_status",
        "record_answer", "categorize_transaction", "propose_action", "list_proposals",
        "can_i_buy", "investment_plan", "get_energy", "get_opportunities", "get_procedures",
        "autonomy_status", "request_autonomous_move", "report_autonomous_move", "get_crypto", "crypto_check",
        "get_obligations", "get_correlations", "get_year_ahead", "get_coverage", "get_plan_b", "get_retirement",
    }  # fmt: skip
    assert tools["get_dossier"]["annotations"]["readOnlyHint"] is True
    # Aucun outil ne déplace d'argent : il n'en existe pas. Le pilote automatique AUTORISE ou refuse, c'est tout.
    assert not [n for n in tools if any(w in n for w in ("transfer", "pay", "virement", "send", "withdraw"))]
    prompts = {p["name"] for p in agent.rpc("prompts/list")["prompts"]}
    assert prompts == {"onboarding_interview", "daily_brief", "weekly_review"}
    text = agent.rpc("prompts/get", {"name": "onboarding_interview"})["messages"][0]["content"]["text"]
    assert "record_answer" in text and "mot de passe" in text
    res = agent.rpc("resources/read", {"uri": "bank://questions"})
    catalogue = json.loads(res["contents"][0]["text"])
    assert {q["id"] for q in catalogue["questions"]} >= {"employment", "autonomy"}


# ------------------------------------------------------------------ lecture
def test_dossier_uses_bank_clock_and_redacts(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    d = agent.ok("get_dossier")
    assert d["today"] == TODAY.isoformat() and d["privacy"] == "redacted"
    assert d["this_month"]["status"] in ("ok", "tight", "risk")
    assert "DUPONT" not in json.dumps(d) and "MARIE" not in json.dumps(d)
    md = agent.ok("get_dossier", output="markdown")["markdown"]
    assert md.startswith("# Dossier financier") and "Détecté dans les relevés" in md
    month = agent.ok("get_month")
    assert set(month) == {"this_month", "commitments"}
    summary = agent.ok("spending_summary", months=3)
    assert summary["income_avg_cents"] > 0 and any(c["id"] == "courses" for c in summary["categories"])
    assert agent.call("spending_summary", months=40)[0]  # borné par le schéma
    alerts = agent.ok("get_alerts")
    assert any(a["code"] == "enveloppe:courses" for a in alerts)
    aids = agent.ok("get_aids")
    assert aids["received_last_12_months_cents"] > 0
    wealth = agent.ok("get_wealth")
    assert wealth["wealth"]["emergency_fund"]["available_cents"] > 0


def test_transactions_respect_privacy_mode(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    redacted = make_agent(app, world)
    rows = redacted.ok("list_transactions", limit=200)
    labels = " ".join(r["label"] + r["merchant"] for r in rows)
    assert "DUPONT" not in labels and "MARIE" not in labels and "0612345678" not in labels
    assert "[PARTICULIER]" in labels and "[IBAN]" in labels and all("note" not in r for r in rows)
    # Le libellé piégé arrive comme une donnée inerte, dans un champ, jamais comme une consigne.
    trap = next(r for r in rows if "CONSIGNES" in r["label"])
    assert trap["amount_cents"] == -1234
    only_courses = redacted.ok("list_transactions", category="courses", date_from="2026-10-01", limit=5)
    assert only_courses and all(r["category"] == "courses" and r["date"] >= "2026-10-01" for r in only_courses)
    assert redacted.ok("list_transactions", query="pizzeria", limit=3)
    assert "AAAA-MM-JJ" in redacted.fails("list_transactions", date_from="hier")

    full = make_agent(app, world, name="local", privacy="full")
    assert "DUPONT" in " ".join(r["label"] for r in full.ok("list_transactions", limit=200))

    blind = make_agent(app, world, name="agg", privacy="aggregates")
    assert "agrégats" in blind.fails("list_transactions")
    assert "DUPONT" not in json.dumps(blind.ok("get_dossier"))


def test_scopes_are_enforced_per_tool(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    observer = make_agent(app, world, preset="observateur", name="obs")
    assert observer.ok("get_dossier")["schema"] == "bank-dossier/v1"
    for tool, args in [
        ("list_transactions", {}),
        ("simulate_aids", {}),
        ("record_answer", {"question_id": "employment", "value": "public"}),
        ("propose_action", {"kind": "set_buffer", "params": {"amount_eur": 10}, "rationale": "r"}),
        ("categorize_transaction", {"transaction_id": 1, "category": "courses"}),
    ]:
        assert "Portée" in observer.fails(tool, **args), tool
    with app.state.sessionmaker() as db:
        denied = list(db.scalars(select(AuditLog).where(AuditLog.action == "mcp.denied")))
        assert len(denied) == 5 and denied[0].actor == "agent:obs"


# ------------------------------------------------------------------ décision
def test_purchase_and_savings_simulations(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    small = agent.ok("evaluate_purchase", amount_eur=40, label="Livre", category="loisirs")
    assert small["verdict"] == "ok" and small["amount_cents"] == 4_000
    big = agent.ok("evaluate_purchase", amount_eur=900, label="Ordinateur")
    assert big["verdict"] in ("attendre", "deconseille", "serre") and big["after"]["lowest_cents"] < 0
    assert big["best_date"] in (None, "2026-10-28")
    assert agent.call("evaluate_purchase", amount_eur=-5)[0]
    assert "Catégorie inconnue" in agent.fails("evaluate_purchase", amount_eur=10, category="nimporte")
    assert agent.ok("evaluate_purchase", amount_eur=0.1 + 0.2)["amount_cents"] == 30  # flottant arrondi au centime
    assert "AAAA-MM-JJ" in agent.fails("evaluate_purchase", amount_eur=10, on_date="pas une date")
    world.put("/api/onboarding/alert_threshold", json={"value": 20_000})
    assert agent.ok("evaluate_purchase", amount_eur=250)["above_alert_threshold"] is True
    w = agent.ok("what_if_savings", monthly_eur=150)
    assert w["monthly_cents"] == 15_000 and w["free_now_cents"] - w["free_after_cents"] == 15_000


def test_simulate_aids_reports_engine_failure_cleanly(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    msg = agent.fails("simulate_aids")
    assert "Moteur fiscal indisponible" in msg and "Traceback" not in msg


# ------------------------------------------------------------------ onboarding par l'agent
def test_agent_records_answers_but_never_its_mandate(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    status = agent.ok("onboarding_status")
    assert status["percent"] == 0 and all(q["section"] != "mandat" for q in status["next_questions"])
    assert any(s["question_id"] == "net_monthly_income" for s in status["suggestions"])

    saved = agent.ok("record_answer", question_id="net_monthly_income", value="2 150")
    assert saved["stored_value"] == 215_000
    debts = agent.ok(
        "record_answer",
        question_id="debts",
        value=[{"label": "Cofidis", "kind": "conso", "remaining_eur": 3200, "monthly_eur": 180, "rate_percent": 7.9}],
    )
    assert debts["stored_value"][0]["remaining_cents"] == 320_000

    for qid, value in [("autonomy", "gestionnaire"), ("red_lines", []), ("alert_threshold", 1_000_000)]:
        assert "mandat" in agent.fails("record_answer", question_id=qid, value=value)
    assert "inconnue" in agent.fails("record_answer", question_id="salaire", value=1)
    assert "Choix attendu" in agent.fails("record_answer", question_id="employment", value="riche")

    ob = world.get("/api/onboarding").json()
    assert ob["answers"]["net_monthly_income"]["source"] == "agent:hermes"
    assert "autonomy" not in ob["answers"]
    d = agent.ok("get_dossier")
    assert d["profile"]["net_monthly_income"]["source"] == "declared_to_agent"
    assert d["debts"]["has_expensive_debt"] is True and d["risk_profile"]["capacity_limited"] is True


def test_categorize_requires_scope_and_mandate(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    manager = make_agent(app, world, preset="gestionnaire", name="gest")
    with app.state.sessionmaker() as db:
        txn = db.scalar(select(Transaction).where(Transaction.raw_label == INJECTION))
        assert txn is not None
        txn_id = txn.id
    assert "mandat" in manager.fails("categorize_transaction", transaction_id=txn_id, category="loisirs")
    world.put("/api/onboarding/autonomy", json={"value": "gestionnaire"})
    done = manager.ok("categorize_transaction", transaction_id=txn_id, category="loisirs")
    assert done["updated"] == 1
    assert "introuvable" in manager.fails("categorize_transaction", transaction_id=999_999, category="loisirs")
    assert "introuvable" in manager.fails("categorize_transaction", transaction_id=txn_id, category="nimporte")
    with app.state.sessionmaker() as db:
        t = db.get(Transaction, txn_id)
        assert t is not None and t.category_id is not None


def test_proposals_round_trip(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    p = agent.ok(
        "propose_action",
        kind="set_envelope",
        params={"category": "courses", "monthly_eur": 640},
        rationale="Tes courses tournent autour de 620-830 € : une enveloppe de 420 € est irréaliste.",
    )
    assert p["status"] == "pending"
    assert "Type inconnu" in agent.fails("propose_action", kind="virement", params={}, rationale="r")
    assert agent.ok("list_proposals")[0]["id"] == p["proposal_id"]
    world.post(f"/api/agents/proposals/{p['proposal_id']}/approve")
    assert agent.ok("list_proposals", status="applied")[0]["status"] == "applied"
    with app.state.sessionmaker() as db:
        prop = db.get(AgentProposal, p["proposal_id"])
        assert prop is not None and prop.agent_name == "hermes"


# ------------------------------------------------------------------ révocation, débit, audit
def test_revocation_is_immediate(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    created = world.post(
        "/api/agents/tokens", json={"name": "hermes", "preset": "observateur", "password": PASSWORD}
    ).json()
    agent = Agent(world, created["token"])
    assert agent.ok("get_alerts") is not None
    world.delete(f"/api/agents/tokens/{created['id']}")
    assert agent.post("tools/call", {"name": "get_alerts", "arguments": {}}).status_code == 401
    tokens = world.get("/api/agents/tokens").json()
    assert tokens[0]["calls"] == 1 and tokens[0]["last_used_at"]


def test_ip_allowlist(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world, allowed_ips=["192.168.1.112"])
    with TestClient(app, base_url="https://testserver", client=("192.168.1.112", 40000)) as hermes_host:
        assert Agent(hermes_host, agent.token).post("tools/list").status_code == 200
    with TestClient(app, base_url="https://testserver", client=("192.168.1.66", 40000)) as other_host:
        assert Agent(other_host, agent.token).post("tools/list").status_code == 401
    # Les démarrages imbriqués n'ont pas démonté le serveur MCP du client principal.
    assert make_agent(app, world, name="apres").post("tools/list").status_code == 200


def test_rate_limit_per_token(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    other = make_agent(app, world, name="autre")
    limiter = app.state.limiters["agent"]
    limiter.max_events = 3
    try:
        codes = [agent.post("tools/list").status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert other.post("tools/list").status_code == 200  # chaque jeton a son propre compteur
    finally:
        limiter.max_events = 120
        limiter.reset()


def test_every_tool_call_is_audited_and_chain_holds(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    agent.ok("get_dossier")
    agent.ok("evaluate_purchase", amount_eur=12)
    with app.state.sessionmaker() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.actor == "agent:hermes")))
        assert [r.action for r in rows] == ["mcp.get_dossier", "mcp.evaluate_purchase"]
        assert audit.verify_chain(db).ok
    agent.ok("record_answer", question_id="money_stress", value=2)
    activity = world.get("/api/agents/activity").json()
    # L'appel d'outil et son effet métier sont fusionnés en une seule ligne lisible.
    assert activity[0]["action"] == "mcp.record_answer" and activity[0]["details"]["question"] == "money_stress"
    assert [a["action"] for a in activity[1:]] == ["mcp.evaluate_purchase", "mcp.get_dossier"]


def test_mcp_can_be_disabled(settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    off = settings.model_copy(update={"agents_enabled": False})
    with TestClient(create_app(off), base_url="https://testserver") as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 404


# ------------------------------------------------------------------ correctifs de la revue de sécurité
def test_agent_cannot_overwrite_user_answers_nor_forecast_inputs(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    world.put("/api/onboarding/net_monthly_income", json={"value": 215_000})
    assert "propose" in agent.fails("record_answer", question_id="net_monthly_income", value=9999)
    expense = [{"label": "Pneus", "amount_eur": 9_999_999, "date": TODAY.isoformat()}]
    assert "prévision" in agent.fails("record_answer", question_id="planned_expenses", value=expense)
    before = world.get("/api/budget").json()["forecast"]["lowest_balance_cents"]
    # La voie prévue : une proposition, que l'humain voit et valide.
    prop = agent.ok(
        "propose_action",
        kind="set_answer",
        params={
            "question_id": "planned_expenses",
            "value": [{"label": "Pneus", "amount_eur": 320, "date": "2026-10-29"}],
        },
        rationale="Tu m'as parlé de pneus à changer avant la fin du mois.",
    )
    assert world.get("/api/budget").json()["forecast"]["lowest_balance_cents"] == before  # rien sans validation
    pending = world.get("/api/agents/proposals").json()[0]
    assert (
        pending["question"].startswith("Grosses dépenses") and pending["params"]["value"][0]["amount_cents"] == 32_000
    )
    assert world.post(f"/api/agents/proposals/{prop['proposal_id']}/approve").json()["status"] == "applied"
    stored = world.get("/api/onboarding").json()["answers"]["planned_expenses"]
    assert stored["source"] == "user:micka" and stored["value"][0]["amount_cents"] == 32_000
    assert "mandat" in agent.fails(
        "propose_action", kind="set_answer", params={"question_id": "autonomy", "value": "gestionnaire"}, rationale="r"
    )
    with app.state.sessionmaker() as db:
        refused = list(db.scalars(select(AuditLog).where(AuditLog.action == "mcp.refused")))
        assert len(refused) == 3 and all(r.actor == "agent:hermes" for r in refused)


def test_rule_patterns_are_validated_after_normalisation(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    for pattern in ("  ", "E ", "\ta\n", "ab"):
        assert "3 caractères" in agent.fails(
            "propose_action", kind="create_rule", params={"pattern": pattern, "category": "courses"}, rationale="r"
        )


def test_duplicate_authorization_headers_and_failed_auth_are_handled(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    agent = make_agent(app, world)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = [(k, v) for k, v in agent.headers().items()] + [("authorization", "Bearer bnk_autre")]
    r = world.post("/mcp", json=body, headers=httpx_headers(headers))
    assert r.status_code == 401
    for _ in range(3):
        Agent(world, "bnk_inconnu_mais_bien_forme_0123456789").post("tools/list")
    with app.state.sessionmaker() as db:
        failures = list(db.scalars(select(AuditLog).where(AuditLog.action == "mcp.auth_failed")))
        assert len(failures) == 1  # journalisé, mais pas d'inondation du journal
        assert audit.verify_chain(db).ok


def httpx_headers(pairs: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    import httpx

    return httpx.Headers(pairs)


def test_simulation_is_reused_and_profile_errors_never_echo_values(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    import json as _json

    from app.models import FiscalSimulation, HouseholdProfile

    agent = make_agent(app, world)
    with app.state.sessionmaker() as db:
        row = db.get(HouseholdProfile, 1)
        assert row is not None
        bad = _json.loads(row.data)
        bad["birth_date"] = "1891-13-45"
        row.data = _json.dumps(bad)
        db.commit()
    msg = agent.fails("simulate_aids")
    assert "Profil du foyer incomplet" in msg and "1891" not in msg
    with app.state.sessionmaker() as db:
        db.add(FiscalSimulation(period="2026-10", result=_json.dumps({"period": "2026-10", "estimates": []})))
        db.commit()
    reused = agent.ok("simulate_aids")
    assert reused["period"] == "2026-10" and "reused_from" in reused


def test_agent_categorization_is_marked_and_never_overrides_the_user(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from app.models import CategorySource

    manager = make_agent(app, world, preset="gestionnaire", name="gest")
    world.put("/api/onboarding/autonomy", json={"value": "gestionnaire"})
    with app.state.sessionmaker() as db:
        txn = db.scalar(select(Transaction).where(Transaction.raw_label == INJECTION))
        assert txn is not None
        txn_id = txn.id
    manager.ok("categorize_transaction", transaction_id=txn_id, category="loisirs")
    with app.state.sessionmaker() as db:
        t = db.get(Transaction, txn_id)
        assert t is not None and t.category_source == CategorySource.AGENT
    world.patch(f"/api/transactions/{txn_id}", json={"category": "divers"})
    assert "lui-même" in manager.fails("categorize_transaction", transaction_id=txn_id, category="loisirs")


def test_onboarding_status_respects_privacy(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    world.put("/api/onboarding/notes", json={"value": "Divorce en cours avec Marie D."})
    world.put("/api/onboarding/employment", json={"value": "public"})
    redacted = make_agent(app, world).ok("onboarding_status")
    assert redacted["answers"]["notes"]["value"].startswith("Divorce")  # écrit pour l'agent
    assert redacted["answers"]["employment"]["source"] == "user"  # pas l'identifiant de connexion
    blind = make_agent(app, world, name="agg", privacy="aggregates").ok("onboarding_status")
    assert "notes" not in blind["answers"]
    assert "Marie" not in json.dumps(make_agent(app, world, name="agg2", privacy="aggregates").ok("get_dossier"))


def test_opportunities_and_procedures_are_readable_never_actionable(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    """L'agent voit les pistes et l'avancement des démarches pour en parler ; il ne lance, n'écarte, ne valide ni ne
    transmet rien (aucun outil pour cela), et le mode « chiffres seuls » ne laisse passer aucun libellé."""
    from datetime import date

    from app.importers.base import ParsedTransaction
    from app.models import Account
    from app.services.ingest import ingest

    with app.state.sessionmaker() as db:
        account = db.scalars(select(Account).where(Account.kind == "checking")).one()
        rows = [ParsedTransaction(date(2026, 10, d), -890, "COMMISSION D INTERVENTION") for d in range(1, 12)]
        ingest(db, account, rows, provider="file")
        db.commit()
    proc_id = world.post("/api/opportunities/start", json={"key": "fees:2026-10"}).json()["procedure_id"]
    world.patch(f"/api/procedures/{proc_id}", json={"title": "Dossier de Jean Dupont"})
    agent = make_agent(app, world)
    data = agent.ok("get_opportunities")
    fees = next(o for o in data["opportunities"] if o.get("title", "").startswith("Frais bancaires"))
    assert fees["procedure_stage"] == "À vérifier" and fees["amount_cents"] == 1_790 and fees["sources"]
    assert data["value"]["estimated_yearly_cents"] == 1_432 and "conseiller financier agréé" in data["disclaimer"]
    procs = agent.ok("get_procedures")["procedures"]
    assert procs[0]["title"] == "Contester des frais bancaires" and procs[0]["missing_values"] == 1  # le nom
    assert "Dupont" not in json.dumps(procs) and "Micka" not in json.dumps(procs)
    numbers_only = make_agent(app, world, name="chiffres", privacy="aggregates")
    blind = numbers_only.ok("get_opportunities")
    assert all("title" not in o and "facts" not in o for o in blind["opportunities"])
    assert "COMMISSION" not in json.dumps(blind)
    tools = {t["name"] for t in agent.rpc("tools/list")["tools"]}
    assert not {"start_opportunity", "approve_procedure", "mark_sent"} & tools
