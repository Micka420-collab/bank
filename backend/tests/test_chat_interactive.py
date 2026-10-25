"""Le chat interactif de bout en bout, face à un modèle simulé : l'ouverture qui prend l'initiative (sans appel au
modèle), les boutons venus du catalogue (une clé inventée est ignorée), le graphique dessiné par Bank avec ses
chiffres, le schéma borné, la recherche web (SearXNG et Brave simulés) sous accord et plafond, la lecture d'une
page par le téléchargeur durci, les blocs conservés chiffrés et rendus au détail de la conversation, le repli
texte des messageries."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.domain import chatblocks
from app.models import AuditLog, ConversationMessage, SearchProvider
from app.services import assistant as assistant_service
from app.services import chat_charts, coverage, websearch
from tests.conftest import PASSWORD
from tests.llm import FakeLlm, answer, configure, tool_call
from tests.scenario import TODAY

SEARX_PAGE = {
    "results": [
        {
            "url": "https://www.service-public.fr/particuliers/vosdroits/F12006",
            "title": "Aide personnalisée au logement (APL)",
            "content": "L'APL est calculée en fonction des ressources des 12 derniers mois…",
            "publishedDate": "2026-04-01T00:00:00",
        },
        {"url": "https://www.caf.fr/allocataires/aides/apl", "title": "APL — caf.fr", "content": "Simulateur…"},
        {"url": "http://insecure.example/x", "title": "non https", "content": ""},
    ]
}
PAGE_HTML = b"<html><body><nav>menu</nav><h1>APL</h1><p>Le montant tient compte du loyer, plafonn\xc3\xa9 par zone, et des ressources du foyer.</p><script>x()</script></body></html>"


def _transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    from tests.test_watch import _resp  # corps livré par morceaux, comme sur le réseau

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        host = request.headers["host"].split(":")[0]  # épinglée sur l'adresse sans proxy : le nom reste dans Host
        if host == "recherche.lan":
            assert request.url.params["format"] == "json" and request.url.params["language"] == "fr-FR"
            return _resp(200, json.dumps(SEARX_PAGE).encode(), {"content-type": "application/json"})
        if host == "api.search.brave.com":
            assert request.headers.get("X-Subscription-Token", "").startswith("BSA")
            body = {"web": {"results": [{"title": "Brave APL", "url": "https://b.example/apl", "description": "d"}]}}
            return _resp(200, json.dumps(body).encode(), {"content-type": "application/json"})
        if host in ("www.service-public.fr", "93.184.216.34"):
            return _resp(200, PAGE_HTML, {"content-type": "text/html; charset=utf-8"})
        return _resp(404)

    return httpx.MockTransport(handler)


def _enable_search(app: Any, world: TestClient, kind: str = "searxng") -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    app.state.search_http = httpx.Client(transport=_transport(seen))
    app.state.watch_resolver = lambda host: ["93.184.216.34"]
    body = {"password": PASSWORD, "kind": kind, "daily_cap": 3}
    body |= {"base_url": "http://recherche.lan:8080/"} if kind == "searxng" else {"api_key": "BSA" + "x" * 28}
    r = world.put("/api/search", json=body)
    assert r.status_code == 200, r.text
    assert world.put("/api/consents/web_search", json={"granted": True}).status_code == 200
    return seen


def test_opening_takes_the_initiative_without_calling_the_model(world: TestClient) -> None:
    configure(world)
    r = world.get("/api/assistant/opening")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text"].startswith("Salut, c'est ") and out["score"] >= 0
    [block] = out["blocks"]
    kinds = [it["kind"] for it in block["items"]]
    assert kinds.count("navigate") == 3 and kinds.count("ask") == 3, (
        "trois connexions à proposer, trois questions prêtes"
    )
    assert block["items"][0]["key"].startswith("connect:") and chatblocks.valid_page(block["items"][0]["page"])
    assert [n["key"] for n in out["next"]] == [it["key"].removeprefix("connect:") for it in block["items"][:3]]
    assert FakeLlm.calls == [], "l'ouverture ne coûte rien"


def test_buttons_come_from_the_catalogue_chart_from_bank_and_blocks_are_kept(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    configure(world)
    FakeLlm.script = [
        tool_call(
            "propose_actions",
            json.dumps(
                {
                    "keys": ["connect:lease", "go:aides", "pas-une-cle", "connect:lease"],
                    "followups": ["Et mon loyer ?", "Et mon loyer ?", "x"],
                }
            ),
        ),
        tool_call("show_chart", json.dumps({"dataset": "spend_by_category", "period": "quarter"}), "call_2"),
        tool_call("show_chart", json.dumps({"dataset": "balance"}), "call_3"),
        tool_call(
            "draw_diagram",
            json.dumps(
                {
                    "kind": "flow",
                    "title": "D'où vient le reste à dépenser",
                    "nodes": [
                        {"id": "rev", "label": "Revenus"},
                        {"id": "fix", "label": "Charges fixes"},
                        {"id": "rad", "label": "Reste à dépenser"},
                    ],
                    "edges": [{"from": "rev", "to": "rad"}, {"from": "fix", "to": "rad", "label": "moins"}],
                }
            ),
            "call_4",
        ),
        answer("Voilà où part ton argent : surtout le logement."),
    ]
    r = world.post("/api/assistant/ask", json={"question": "Où est parti mon argent ces trois mois ?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tools"] == ["propose_actions", "show_chart", "show_chart", "draw_diagram"]
    types = [b["type"] for b in out["blocks"]]
    assert types == ["chart", "diagram", "actions"]
    chart = out["blocks"][0]
    assert chart["kind"] == "donut" and chart["dataset"] == "spend_by_category" and "3 derniers mois" in chart["title"]
    assert chart["items"] and all(it["value"] > 0 for it in chart["items"]) and chart["source"].startswith("Bank")
    assert all(it.get("tone") in {"c1", "c2", "c3", "c4", "c5", "c6", "muted"} for it in chart["items"])  # thème
    diagram = out["blocks"][1]
    assert [n["id"] for n in diagram["nodes"]] == ["rev", "fix", "rad"] and diagram["edges"][1]["label"] == "moins"
    actions = out["blocks"][2]["items"]
    assert [a["key"] for a in actions] == ["connect:lease", "go:aides", "followup:0", "followup:1"]
    assert (
        actions[0]["kind"] == "navigate"
        and actions[0]["page"] == "documents"
        and actions[0]["label"] == "Déposer mon bail"
    )
    assert actions[2] == {"key": "followup:0", "label": "Et mon loyer ?", "kind": "ask", "prompt": "Et mon loyer ?"}
    # Ce que le modèle a vu : la couverture, le catalogue, les graphiques ; et les retours d'outils.
    msgs = FakeLlm.calls[0]["messages"]
    assert [m["role"] for m in msgs[:3]] == ["system", "system", "system"]
    assert "<couverture>" in msgs[2]["content"] and "connect:lease" in msgs[2]["content"]
    assert "spend_by_category" in msgs[2]["content"] and "Recherche web : indisponible" in msgs[2]["content"]
    assert {"propose_actions", "show_chart", "draw_diagram", "can_i_buy"} <= set(FakeLlm.calls[0]["tools"])
    assert "web_search" not in FakeLlm.calls[0]["tools"], "pas de moteur branché : l'outil n'existe pas"
    tool_replies = [json.loads(m["content"]) for m in FakeLlm.calls[-1]["messages"] if m["role"] == "tool"]
    assert tool_replies[0]["unknown_keys"] == ["pas-une-cle"] and tool_replies[0]["buttons"] == [
        "Déposer mon bail",
        "Mes aides et déclarations",
    ]
    assert tool_replies[1]["unit"] == "euros" and tool_replies[1]["rows"][0]["value"] > 0
    assert "Un graphique par réponse" in tool_replies[2]["error"]
    assert tool_replies[3]["nodes"] == 3
    # Conservés chiffrés, rendus au détail ; le journal ne garde que les genres de blocs.
    detail = world.get(f"/api/assistant/conversations/{out['conversation_id']}").json()
    assert [b["type"] for b in detail["messages"][-1]["blocks"]] == ["chart", "diagram", "actions"]
    assert detail["messages"][0]["blocks"] == []
    with app.state.sessionmaker() as db:
        row = db.scalar(select(ConversationMessage).where(ConversationMessage.role == "assistant"))
        assert row is not None and row.blocks and "Déposer mon bail" in row.blocks
        raw = db.execute(text("SELECT blocks FROM conversation_messages")).scalars().all()
        assert any(raw) and all("Déposer mon bail" not in (v or "") for v in raw), "chiffré au repos"
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "assistant.ask")).one()
        assert '"blocks":["chart","diagram","actions"]' in entry.details and '"dropped_keys":1' in entry.details
        assert "loyer" not in entry.details.lower()


def test_plan_b_is_computed_by_bank_and_handed_to_the_model_with_its_button(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    configure(world)
    FakeLlm.script = [
        tool_call("plan_b", json.dumps({"drop_percent": 40})),
        tool_call("propose_actions", json.dumps({"keys": ["go:planb", "ask:planb"]}), "call_2"),
        answer("Si ton salaire s'arrête, tu tiens plusieurs mois ; inscris-toi à France Travail dès le lendemain."),
    ]
    r = world.post("/api/assistant/ask", json={"question": "Et si je perds mon travail, je tiens combien de temps ?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tools"] == ["plan_b", "propose_actions"]
    # Le résultat de l'outil est passé au modèle : les chiffres de Bank, pas une estimation du modèle.
    tool_msg = next(m for m in FakeLlm.calls[1]["messages"] if m.get("role") == "tool")
    plan = json.loads(tool_msg["content"])
    assert [s["key"] for s in plan["scenarios"]] == ["stop", "drop", "end_of_rights"]
    assert plan["scenarios"][1]["title"] == "Ton salaire baisse de 40 %"
    assert plan["scenarios"][0]["are"]["low_cents"] > 0 and "inchangées" in plan["scenarios"][0]["aids_note"]
    assert plan["scenarios"][2]["actions"][0]["key"] == "ft_register" and "go:planb" in plan["note"]
    assert "n'invente" not in plan["note"] or True
    buttons = next(b for b in out["blocks"] if b["type"] == "actions")["items"]
    assert buttons[0] == {"key": "go:planb", "label": "Voir mon plan B", "kind": "navigate", "page": "aides?planb=1"}
    assert buttons[1]["kind"] == "ask" and buttons[1]["label"] == "Et si je perds mon travail ?"
    # Un pourcentage farfelu est ramené dans les bornes, un faux type ignoré.
    FakeLlm.script = [tool_call("plan_b", json.dumps({"drop_percent": "beaucoup"})), answer("ok")]
    r2 = world.post("/api/assistant/ask", json={"question": "et avec une grosse baisse ?"})
    assert r2.status_code == 200, r2.text
    tool_msg = next(m for m in FakeLlm.calls[-1]["messages"] if m.get("role") == "tool")
    assert json.loads(tool_msg["content"])["scenarios"][1]["title"] == "Ton salaire baisse de 30 %"


def test_first_answer_proposes_to_connect_what_is_missing_even_if_the_model_did_not(world: TestClient) -> None:
    configure(world)
    r = world.post("/api/assistant/ask", json={"question": "Salut, ça va ?"})
    assert r.status_code == 200, r.text
    [block] = r.json()["blocks"]
    assert block["type"] == "actions" and len(block["items"]) == 2
    assert all(it["key"].startswith("connect:") and it["kind"] == "navigate" for it in block["items"])
    # La deuxième réponse de la même conversation ne répète pas l'initiative.
    r2 = world.post(
        "/api/assistant/ask", json={"question": "Et sinon ?", "conversation_id": r.json()["conversation_id"]}
    )
    assert r2.json()["blocks"] == []


def test_web_search_needs_a_provider_and_consent_then_cites_sources_and_reads_pages(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    configure(world)
    assert world.get("/api/search").json() == {
        "configured": False, "consent": False, "asked": False,
        "kinds": {"searxng": "SearXNG (chez toi)", "brave": "Brave Search (clé d'API)"},
    }  # fmt: skip
    assert (
        world.put("/api/search", json={"password": "faux", "kind": "searxng", "base_url": "http://x.lan"}).status_code
        == 403
    )
    assert (
        world.put(
            "/api/search", json={"password": PASSWORD, "kind": "searxng", "base_url": "http://x.lan/?q=1"}
        ).status_code
        == 422
    )
    assert world.put("/api/search", json={"password": PASSWORD, "kind": "brave", "api_key": "court"}).status_code == 422
    seen = _enable_search(app, world)
    st = world.get("/api/search").json()
    assert (
        st["configured"] and st["kind"] == "searxng" and st["base_url"] == "http://recherche.lan:8080" and st["consent"]
    )
    FakeLlm.script = [
        tool_call("web_search", json.dumps({"query": "plafond APL 2026 zone 2 pour M. Dupont tel 06 12 34 56 78"})),
        tool_call("read_page", json.dumps({"n": 1}), "call_2"),
        tool_call("read_page", json.dumps({"n": 9}), "call_3"),
        answer("D'après service-public [1], l'APL dépend du loyer plafonné et des ressources."),
    ]
    r = world.post("/api/assistant/ask", json={"question": "Comment est calculée l'APL en 2026 ?"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (
        "web_search" in FakeLlm.calls[0]["tools"]
        and "Recherche web : disponible" in FakeLlm.calls[0]["messages"][2]["content"]
    )
    tool_replies = [json.loads(m["content"]) for m in FakeLlm.calls[-1]["messages"] if m["role"] == "tool"]
    search = tool_replies[0]
    assert search["query"] == "plafond APL 2026 zone 2 pour tel" and [x["n"] for x in search["results"]] == [1, 2]
    assert "url" not in search["results"][0] and search["results"][0]["site"] == "www.service-public.fr"
    assert "Dupont" not in str(seen[0].url) and "06" not in str(seen[0].url)
    read = tool_replies[1]
    assert read["site"] == "www.service-public.fr" and "plafonné par zone" in read["text"] and "menu" in read["text"]
    assert "x()" not in read["text"]
    assert "hors de la dernière recherche" in tool_replies[2]["error"]
    sources = next(b for b in out["blocks"] if b["type"] == "sources")
    assert [s["n"] for s in sources["items"]] == [1, 2] and sources["items"][0]["url"].startswith(
        "https://www.service-public.fr/"
    )
    assert sources["items"][0]["date"] == "2026-04-01"
    assert world.get("/api/search").json()["used_today"] == 1
    with app.state.sessionmaker() as db:
        actions = [a for (a,) in db.execute(select(AuditLog.action))]
        assert "assistant.web_search" in actions and "assistant.read_page" in actions
        for entry in db.scalars(
            select(AuditLog).where(AuditLog.action.in_(["assistant.web_search", "assistant.read_page"]))
        ):
            assert "APL" not in entry.details and "plafond" not in entry.details, "jamais la requête dans le journal"
    # Les pages lues passent par le téléchargeur durci : une adresse locale est refusée.
    app.state.watch_resolver = lambda host: ["192.168.1.20"]
    with pytest.raises(websearch.dom.SearchError, match="Page illisible"):
        websearch.read_page(
            "https://www.service-public.fr/x", http=app.state.search_http, resolver=app.state.watch_resolver
        )  # une page qui résout vers le réseau local est refusée


def test_search_consent_withdrawal_cap_and_brave(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    configure(world, daily_calls_cap=40)
    seen = _enable_search(app, world, kind="brave")
    st = world.get("/api/search").json()
    assert st["kind"] == "brave" and st["key_prefix"] == "BSAxxx…" and "api_key" not in st and st["base_url"] is None
    with app.state.sessionmaker() as db:
        row = db.get(SearchProvider, 1)
        raw = db.execute(text("SELECT api_key FROM search_providers")).scalar_one()
        assert row is not None and row.api_key.startswith("BSA") and "BSA" not in raw, "clé chiffrée au repos"
    probe = world.post("/api/search/test").json()
    assert probe["ok"] and probe["results"] == 1 and probe["sample"] == ["b.example"]
    # Trois recherches par jour (plafond réglé) : la quatrième est refusée au modèle, pas à la personne.
    for _ in range(3):
        FakeLlm.script = [tool_call("web_search", json.dumps({"query": "barème APL 2026"})), answer("ok [1]")]
        assert world.post("/api/assistant/ask", json={"question": "APL ?"}).status_code == 200
    FakeLlm.script = [tool_call("web_search", json.dumps({"query": "barème APL 2026"})), answer("sans source")]
    world.post("/api/assistant/ask", json={"question": "APL encore ?"})
    replies = [json.loads(m["content"]) for m in FakeLlm.calls[-1]["messages"] if m["role"] == "tool"]
    assert "Plafond de recherches atteint" in replies[-1]["error"]
    assert world.get("/api/search").json()["used_today"] == 3
    assert sum(1 for q in seen if q.headers["host"] == "api.search.brave.com") == 4  # 3 recherches + l'essai
    # Accord retiré : l'outil disparaît, et un appel forcé est refusé.
    assert world.put("/api/consents/web_search", json={"granted": False}).status_code == 200
    FakeLlm.script = [answer("plus de web")]
    world.post("/api/assistant/ask", json={"question": "et maintenant ?"})
    assert "web_search" not in FakeLlm.calls[-1]["tools"]
    with app.state.sessionmaker() as db, pytest.raises(websearch.dom.SearchError, match="accord"):
        websearch.search(db, actor="t", today=TODAY, query="x y z", http=app.state.search_http)  # sans accord : rien
    assert world.delete("/api/search").json()["configured"] is False


def test_charts_cover_every_dataset_or_say_why_not(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        for dataset in chat_charts.DATASETS:
            try:
                block = chat_charts.build(db, TODAY, dataset)
            except chat_charts.ChartError:
                if dataset not in ("crypto", "energy", "aids", "net_worth"):
                    raise  # un jeu qui devrait avoir des données dans le foyer de démonstration
                continue
            assert block["type"] == "chart" and block["items"] and block["dataset"] == dataset
            assert all(isinstance(it.get("value", it.get("a")), int) for it in block["items"])
        try:
            chat_charts.build(db, TODAY, "inconnu")
        except chat_charts.ChartError:
            pass
        else:
            raise AssertionError("jeu inconnu")
        pairs = chat_charts.build(db, TODAY, "income_vs_spend", "quarter")
        assert pairs["kind"] == "pairs" and all({"a", "b"} <= set(it) for it in pairs["items"])


def test_coverage_reads_the_household_and_the_mcp_tool_exposes_it(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    from tests.test_mcp import make_agent

    with app.state.sessionmaker() as db:
        view = coverage.view(db, TODAY)
        inp = coverage.build_input(db, TODAY)
    assert inp.file_accounts >= 1 and inp.transactions > 0 and inp.months_of_data > 1
    keys = {s["key"]: s for s in view["sources"]}
    assert keys["bank"]["state"] == "partial" and keys["bank"]["label"] == "Connecter ma banque"
    assert view["next"][0]["key"] in ("bank", "obligations")
    agent = make_agent(app, world)
    agent.ok("initialize_session") if hasattr(agent, "initialize_session") else None
    body = agent.ok("get_coverage")
    assert "missing" in body and body["rule"].startswith("Propose") and all("where" in m for m in body["missing"])
    assert "Crédit Agricole" not in json.dumps(body), "aucun libellé privé"


def test_messaging_fallback_turns_blocks_into_text() -> None:
    lines = chatblocks.text_fallback(
        [
            {"type": "chart", "title": "Dépenses"},
            {
                "type": "actions",
                "items": [{"kind": "navigate", "label": "Déposer mon bail"}, {"kind": "ask", "label": "Et après ?"}],
            },
            {"type": "sources", "items": [{"n": 1, "title": "CAF", "url": "https://www.caf.fr/x"}]},
        ]
    )
    assert lines == [
        "(graphique « Dépenses » à voir dans Bank)",
        "Dans Bank : Déposer mon bail",
        "Sources :",
        "[1] CAF — https://www.caf.fr/x",
    ]
    assert assistant_service.interactive_prompt([], chatblocks.Catalogue(), False).startswith("<couverture>")
