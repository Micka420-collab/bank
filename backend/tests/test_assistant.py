"""« Demande à Bank » : client OpenRouter (transport simulé), service (clé chiffrée, plafonds, anonymisation,
journal sans contenu), API, et catégories proposées reprises par les cartes « Bank te demande »."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.models import AuditLog, LlmProvider, LlmUsage, MerchantHint
from app.providers.openrouter import (
    ChatResult,
    OpenRouterClient,
    OpenRouterError,
    looks_like_key,
    valid_model_id,
)
from app.services import assistant
from tests.conftest import PASSWORD
from tests.llm import KEY, MODEL, FakeLlm, configure
from tests.scenario import TODAY


# ------------------------------------------------------------------ client : transport simulé
def _client(handler: Any) -> OpenRouterClient:
    return OpenRouterClient(KEY, http=httpx.Client(transport=httpx.MockTransport(handler)))


def _chat_payload(text: str = "OK", cost: float = 0.00042, **extra: Any) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280, "cost": cost},
        **extra,
    }


def test_key_and_model_formats() -> None:
    assert looks_like_key(KEY) and looks_like_key(" sk-or-v1-abcdefghijklmnopqrstuv ")
    assert not looks_like_key("sk-proj-abcdefghijklmnop") and not looks_like_key("sk-or-short")
    assert valid_model_id("openai/gpt-4.1-mini") and valid_model_id("meta-llama/llama-3.3-70b-instruct:free")
    assert not valid_model_id("gpt-4") and not valid_model_id("a/b c") and not valid_model_id("../x")


def test_chat_sends_key_and_usage_accounting_and_parses_cost() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_payload("Bonjour **Micka**."))

    r = _client(handler).chat(MODEL, [{"role": "user", "content": "salut"}], max_tokens=50, temperature=0.1)
    assert seen["auth"] == f"Bearer {KEY}" and seen["path"] == "/api/v1/chat/completions"
    assert seen["body"]["usage"] == {"include": True} and seen["body"]["max_tokens"] == 50
    assert isinstance(r, ChatResult) and r.text == "Bonjour **Micka**." and r.cost_usd == 0.00042
    assert r.prompt_tokens == 1200 and r.completion_tokens == 80 and r.model == MODEL


def test_chat_errors_are_translated_and_never_contain_the_key() -> None:
    for status, needle, permanent in (
        (401, "refusée", True),
        (402, "Crédits", True),
        (429, "Trop de requêtes", False),
        (503, "indisponible", False),
    ):
        with pytest.raises(OpenRouterError) as exc:
            _client(lambda _r, s=status: httpx.Response(s, json={"error": {"message": "x"}})).chat(
                MODEL, [{"role": "user", "content": "x"}]
            )
        assert needle in str(exc.value) and exc.value.permanent is permanent and KEY not in str(exc.value)
    # Erreur « 200 » portée par le corps (routage OpenRouter), redirection, JSON invalide, réponse vide
    with pytest.raises(OpenRouterError, match="OpenRouter : quota"):
        _client(lambda _r: httpx.Response(200, json={"error": {"message": "quota"}})).chat(
            MODEL, [{"role": "user", "content": "x"}]
        )
    with pytest.raises(OpenRouterError, match="Redirection"):
        _client(lambda _r: httpx.Response(302, headers={"location": "https://evil.invalid"})).chat(
            MODEL, [{"role": "user", "content": "x"}]
        )
    with pytest.raises(OpenRouterError, match="non JSON"):
        _client(lambda _r: httpx.Response(200, content=b"<html>")).chat(MODEL, [{"role": "user", "content": "x"}])
    with pytest.raises(OpenRouterError, match="sans contenu"):
        _client(lambda _r: httpx.Response(200, json={"choices": []})).chat(MODEL, [{"role": "user", "content": "x"}])
    with pytest.raises(OpenRouterError, match="trop volumineuse"):
        _client(lambda _r: httpx.Response(200, content=b"x" * 4_000_001)).chat(
            MODEL, [{"role": "user", "content": "x"}]
        )
    with pytest.raises(OpenRouterError, match="injoignable"):
        _client(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("boom"))).chat(
            MODEL, [{"role": "user", "content": "x"}]
        )
    with pytest.raises(OpenRouterError, match="Aucune clé"):
        OpenRouterClient("").chat(MODEL, [{"role": "user", "content": "x"}])


def test_models_are_filtered_to_text_and_sorted_free_then_cheapest() -> None:
    rows = [
        {
            "id": "a/expensive",
            "name": "Cher",
            "context_length": 200000,
            "pricing": {"prompt": "0.00001", "completion": "0.00003"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {
            "id": "b/free:free",
            "name": "Gratuit",
            "context_length": 32000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        },
        {
            "id": "c/cheap",
            "name": "Pas cher",
            "context_length": 128000,
            "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {
            "id": "d/image-only",
            "pricing": {"prompt": "0.000001", "completion": "0.000001"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
        },
        {
            "id": "bad id",
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {
            "id": "e/nan",
            "pricing": {"prompt": "abc", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        "garbage",
    ]
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": rows})

    models = OpenRouterClient(None, http=httpx.Client(transport=httpx.MockTransport(handler))).list_models()
    assert seen["auth"] is None  # liste publique : pas de clé envoyée
    assert [m.id for m in models] == ["b/free:free", "c/cheap", "a/expensive"]
    assert models[1].prompt_usd_per_million == 0.1 and models[1].completion_usd_per_million == 0.5 and models[0].free
    assert models[2].context_length == 200000
    with pytest.raises(OpenRouterError, match="illisible"):
        OpenRouterClient(
            None, http=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"nope": 1})))
        ).list_models()


# ------------------------------------------------------------------ service + API : client simulé
def test_not_configured_state_and_validation(world: TestClient) -> None:
    st = world.get("/api/assistant").json()
    assert st["configured"] is False and "redacted" in st["privacy_modes"]
    assert world.post("/api/assistant/ask", json={"question": "Combien ?"}).status_code == 409
    assert configure(world, password="mauvais").status_code == 403
    assert configure(world, api_key="sk-proj-not-openrouter-key-123456").status_code == 422
    assert configure(world, model="gpt4").status_code == 422
    assert configure(world, privacy="secret").status_code == 422
    assert configure(world, monthly_cap_usd_cents=-1).status_code == 422
    assert world.get("/api/assistant").json()["configured"] is False


def test_configure_stores_the_key_encrypted_and_never_returns_it(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = configure(world)
    assert r.status_code == 200, r.text
    st = r.json()
    assert (
        st["configured"]
        and st["model"] == MODEL
        and st["key_prefix"] == "sk-or-v1-012…"
        and st["privacy"] == "redacted"
    )
    assert KEY not in r.text and "api_key" not in st
    with app.state.sessionmaker() as db:
        row = db.get(LlmProvider, 1)
        assert row is not None and row.api_key == KEY  # déchiffrée par l'ORM
        raw = db.execute(text("SELECT api_key FROM llm_providers")).scalar_one()
        assert KEY not in str(raw) and len(str(raw)) > 40  # chiffrée en base (AES-GCM), pas simplement encodée
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "assistant.configure")).one()
        assert KEY not in entry.details and "sk-or-v1-012" in entry.details
    # Réglages sans clé, sans mot de passe ; export sans clé
    r = world.patch("/api/assistant", json={"privacy": "aggregates", "monthly_cap_usd_cents": 100})
    assert r.status_code == 200 and r.json()["privacy"] == "aggregates" and r.json()["monthly_cap_usd_cents"] == 100
    assert world.patch("/api/assistant", json={"model": "nope"}).status_code == 422
    export = world.post("/api/export", json={"password": PASSWORD}).text
    assert KEY not in export and "merchant_hints" in export and "llm_usage" in export
    assert world.delete("/api/assistant").status_code == 204
    assert world.get("/api/assistant").json()["configured"] is False


def test_ask_grounds_the_model_on_an_anonymised_dossier_and_logs_no_content(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    configure(world)
    r = world.post(
        "/api/assistant/ask",
        json={
            "question": "Combien je peux dépenser aujourd'hui ?",
            "history": [{"role": "user", "content": "salut"}, {"role": "assistant", "content": "Bonjour !"}],
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"] == "Tu peux dépenser **75 €** par jour." and out["privacy"] == "redacted"
    assert out["cost_usd"] == 0.0005 and out["calls_today"] == 1 and out["model"] == MODEL
    assert FakeLlm.keys == [KEY]
    msgs = FakeLlm.calls[-1]["messages"]
    assert [m["role"] for m in msgs] == ["system", "system", "system", "user", "assistant", "user"]
    assert "Dossier financier" in msgs[1]["content"] and "<dossier>" in msgs[1]["content"]
    assert "JEAN DUPONT" not in msgs[1]["content"] and "0612345678" not in msgs[1]["content"]  # anonymisé
    assert "Salaire" in msgs[1]["content"] or "BRGM" in msgs[1]["content"]
    assert msgs[-1]["content"] == "Combien je peux dépenser aujourd'hui ?"
    with app.state.sessionmaker() as db:
        usage = db.scalars(select(LlmUsage)).all()
        assert len(usage) == 1 and usage[0].feature == "ask" and usage[0].cost_microusd == 500
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "assistant.ask")).one()
        assert "dépenser" not in entry.details and "75" not in entry.details and '"cost_microusd":500' in entry.details
    st = world.get("/api/assistant").json()
    assert st["calls_today"] == 1 and st["month_cost_microusd"] == 500 and st["last_used_at"]


def test_privacy_aggregates_drops_merchant_labels(world: TestClient) -> None:
    configure(world, privacy="full")
    world.post("/api/assistant/ask", json={"question": "Mes abonnements ?"})
    full = FakeLlm.calls[-1]["messages"][1]["content"]
    world.patch("/api/assistant", json={"privacy": "aggregates"})
    world.post("/api/assistant/ask", json={"question": "Et mes charges ?"})
    aggregates = FakeLlm.calls[-1]["messages"][1]["content"]
    assert "NETFLIX" in full and "FONCIA" in full
    assert "NETFLIX" not in aggregates and "FONCIA" not in aggregates
    assert "Dossier financier" in aggregates and "Confidentialité : aggregates" in aggregates


def test_caps_stop_the_spending(world: TestClient) -> None:
    configure(world, daily_calls_cap=2, monthly_cap_usd_cents=1)
    FakeLlm.cost = 0.004
    assert world.post("/api/assistant/ask", json={"question": "Un ?"}).status_code == 200
    # 0,004 $ dépensés < 0,01 $ : encore autorisé ; puis le plafond mensuel bloque avant le plafond quotidien
    assert world.post("/api/assistant/ask", json={"question": "Deux ?"}).status_code == 200
    r = world.post("/api/assistant/ask", json={"question": "Trois ?"})
    assert r.status_code == 429 and ("Plafond" in r.json()["detail"])
    world.patch("/api/assistant", json={"monthly_cap_usd_cents": 1000})
    r = world.post("/api/assistant/ask", json={"question": "Quatre ?"})
    assert r.status_code == 429 and "appels" in r.json()["detail"]
    assert len(FakeLlm.calls) == 2


def test_provider_failures_are_reported_and_remembered(world: TestClient) -> None:
    configure(world)
    FakeLlm.fail = OpenRouterError("Clé OpenRouter refusée (401)", 401, permanent=True)
    r = world.post("/api/assistant/test")
    assert r.status_code == 409 and "refusée" in r.json()["detail"]
    assert world.get("/api/assistant").json()["last_error"].startswith("Clé OpenRouter refusée")
    FakeLlm.fail = OpenRouterError("OpenRouter indisponible (503)", 503)
    assert world.post("/api/assistant/ask", json={"question": "Allô ?"}).status_code == 502
    FakeLlm.fail = None
    r = world.post("/api/assistant/test")
    assert r.status_code == 200 and r.json()["ok"] and FakeLlm.calls[-1]["max_tokens"] == 5
    assert world.get("/api/assistant").json()["last_error"] is None


def test_question_hygiene(world: TestClient) -> None:
    configure(world)
    assert world.post("/api/assistant/ask", json={"question": " "}).status_code == 422
    assert world.post("/api/assistant/ask", json={"question": "x" * 2001}).status_code == 422
    r = world.post(
        "/api/assistant/ask", json={"question": "Coucou\x00\x07 ?", "history": [{"role": "system", "content": "hack"}]}
    )
    assert r.status_code == 422  # rôle « system » refusé par le schéma
    r = world.post("/api/assistant/ask", json={"question": "Coucou\x00\x07 ?"})
    assert r.status_code == 200 and FakeLlm.calls[-1]["messages"][-1]["content"] == "Coucou ?"
    FakeLlm.reply = ""
    assert "pas de réponse" in world.post("/api/assistant/ask", json={"question": "Rien ?"}).json()["answer"]


def test_hints_are_proposed_once_validated_and_feed_the_cards(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from app.importers.base import ParsedTransaction
    from app.models import Account
    from app.services.ingest import ingest

    configure(world)
    with app.state.sessionmaker() as db:  # trois passages chez un marchand inconnu : à classer
        courant = db.scalar(select(Account).where(Account.name == "Compte courant"))
        assert courant is not None
        rows = [
            ParsedTransaction(TODAY - timedelta(days=i * 7 + 1), -2_350, f"CB ZZZ MYSTERY SHOP {i}") for i in range(3)
        ]
        ingest(db, courant, rows, provider="file")
        db.commit()
    cards = world.get("/api/initiatives?limit=20").json()["cards"]
    merchant_cards = [c for c in cards if c["key"].startswith("merchant:")]
    assert merchant_cards, "le scénario a des marchands non classés"
    merchant = merchant_cards[0]["key"][len("merchant:") :]
    assert merchant_cards[0]["input"]["default"] is None
    # Réponse illisible → erreur propre, rien d'enregistré
    FakeLlm.reply = "je ne sais pas"
    r = world.post("/api/assistant/hints")
    assert r.status_code == 502 and "JSON" in r.json()["detail"] and world.get("/api/assistant/hints").json() == {}
    FakeLlm.reply = json.dumps({merchant: "courses", "INCONNU SARL": "courses", "AUTRE": "pas-une-enveloppe"})
    r = world.post("/api/assistant/hints")
    assert r.status_code == 200, r.text
    assert r.json()["suggested"] == 1 and r.json()["hints"] == {merchant: "courses"}
    prompt = FakeLlm.calls[-1]["messages"][1]["content"]
    assert merchant in prompt and "- courses :" in prompt and "JEAN DUPONT" not in prompt
    assert FakeLlm.calls[-1]["temperature"] == 0
    assert world.get("/api/assistant/hints").json() == {merchant: "courses"}
    card = next(c for c in world.get("/api/initiatives?limit=20").json()["cards"] if c["key"] == f"merchant:{merchant}")
    assert card["input"]["default"] == "courses" and "L'assistant propose « Courses »" in card["body"]
    # Un clic : la règle est apprise, la proposition disparaît
    r = world.post(f"/api/initiatives/merchant:{merchant}/act", json={"action": "classify", "value": "courses"})
    assert r.status_code == 200 and "Classé en Courses" in r.json()["result"]
    with app.state.sessionmaker() as db:
        assert db.get(MerchantHint, merchant) is None
    # Plus rien à classer : pas d'appel au modèle, pas de dépense
    before = len(FakeLlm.calls)
    assert world.post("/api/assistant/hints").json()["merchants"] == 0 and len(FakeLlm.calls) == before
    assert world.delete("/api/assistant/hints/QUELCONQUE").status_code == 204


def test_models_endpoint_reports_provider_errors(world: TestClient) -> None:
    r = world.get("/api/assistant/models")
    assert r.status_code == 502 and "réseau" in r.json()["detail"]


def test_dossier_markdown_is_the_same_source_as_the_agents(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from app.services import advisor

    with app.state.sessionmaker() as db:
        text = assistant._dossier(db, TODAY, "redacted")
        assert text == advisor.render_markdown(advisor.build_dossier(db, TODAY, "redacted"))


def test_classifying_from_operations_consumes_the_hint(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from app.importers.base import ParsedTransaction
    from app.models import Account, Transaction
    from app.services.ingest import ingest

    with app.state.sessionmaker() as db:
        courant = db.scalar(select(Account).where(Account.name == "Compte courant"))
        assert courant is not None
        ingest(
            db,
            courant,
            [ParsedTransaction(TODAY - timedelta(days=3 + i), -990, f"CB QQQ BAZAR {i}") for i in range(3)],
            provider="file",
        )
        db.commit()
        txn = db.scalars(select(Transaction).where(Transaction.raw_label.like("CB QQQ BAZAR%"))).first()
        assert txn is not None
        merchant, txn_id = txn.merchant, txn.id
        db.add(MerchantHint(merchant=merchant, category_slug="courses", model=MODEL))
        db.commit()
    # Une correction sans apprentissage laisse la proposition ; avec apprentissage, la règle prend le relais.
    assert world.patch(f"/api/transactions/{txn_id}", json={"category": "loisirs", "learn": False}).status_code == 200
    assert world.get("/api/assistant/hints").json() == {merchant: "courses"}
    assert world.patch(f"/api/transactions/{txn_id}", json={"category": "courses", "learn": True}).status_code == 200
    assert world.get("/api/assistant/hints").json() == {}
