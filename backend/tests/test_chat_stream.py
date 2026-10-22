"""La réponse en direct : le client OpenRouter recompose un flux SSE (texte, appels d'outils fragmentés, usage,
erreur) en la même réponse qu'en un bloc ; l'API diffuse statuts, morceaux de texte, « reset » après un outil, puis
la réponse complète avec ses blocs — ou une erreur lisible — et la conversation est bien enregistrée."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from app.providers.openrouter import OpenRouterClient, OpenRouterError
from tests.llm import KEY, MODEL, FakeLlm, answer, configure, tool_call
from tests.test_watch import Chunks


def _sse(*events: Any) -> bytes:
    lines = [": OPENROUTER PROCESSING\n"]
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}\n\n" if not isinstance(ev, str) else f"data: {ev}\n\n")
    return "".join(lines).encode()


def _stream_client(body: bytes, seen: dict[str, Any]) -> OpenRouterClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, stream=Chunks(body, 7), headers={"content-type": "text/event-stream"})

    return OpenRouterClient(KEY, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_reassembles_text_tool_calls_usage_and_cost_from_a_stream() -> None:
    body = _sse(
        {"id": "x", "model": MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Tu peux "}}]},
        {"choices": [{"index": 0, "delta": {"content": [{"type": "text", "text": "dépenser "}]}}]},
        {"choices": [{"index": 0, "delta": {"content": "75 €."}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "call_a", "function": {"name": "show_chart", "arguments": '{"data'}}
                        ]
                    },
                }
            ]
        },
        {
            "choices": [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'set": "balance"}'}}]}}
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 1, "id": "call_b", "function": {"name": "propose_actions", "arguments": "{}"}}
                        ]
                    },
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "cost": 0.00042}},
        "[DONE]",
    )
    seen: dict[str, Any] = {}
    pieces: list[str] = []
    r = _stream_client(body, seen).chat(MODEL, [{"role": "user", "content": "?"}], on_delta=pieces.append)
    assert seen["body"]["stream"] is True and seen["accept"] == "text/event-stream"
    assert pieces == ["Tu peux ", "dépenser ", "75 €."] and r.text == "Tu peux dépenser 75 €."
    assert [(c.id, c.name, c.arguments) for c in r.tool_calls] == [
        ("call_a", "show_chart", '{"dataset": "balance"}'),
        ("call_b", "propose_actions", "{}"),
    ]
    assert r.finish_reason == "tool_calls" and r.cost_usd == 0.00042 and r.prompt_tokens == 1200 and r.model == MODEL
    assert r.assistant_message()["tool_calls"][0]["function"]["name"] == "show_chart"


def test_client_stream_errors_and_limits() -> None:
    err = _sse({"error": {"message": "Rate limited", "code": 429}}, "[DONE]")
    with pytest.raises(OpenRouterError, match="Rate limited") as info:
        _stream_client(err, {}).chat(MODEL, [{"role": "user", "content": "?"}], on_delta=lambda _p: None)
    assert KEY not in str(info.value)
    # Sans on_delta, pas de flux demandé ; une réponse JSON classique reste lue d'un bloc.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
        )

    r = OpenRouterClient(KEY, http=httpx.Client(transport=httpx.MockTransport(handler))).chat(
        MODEL, [{"role": "user", "content": "?"}]
    )
    assert "stream" not in seen["body"] and r.text == "ok"


def _events(client: TestClient, body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    with client.stream("POST", "/api/assistant/ask/stream", json=body) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-accel-buffering"] == "no" and r.headers["cache-control"] == "no-store"
        event = ""
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                out.append((event, json.loads(line[6:])))
    return out


def test_stream_endpoint_sends_status_deltas_reset_and_the_full_answer(world: TestClient) -> None:
    configure(world)
    FakeLlm.script = [
        tool_call("show_chart", json.dumps({"dataset": "spend_by_category"})),
        answer("Surtout le logement, puis les courses."),
    ]
    events = _events(world, {"question": "Où est parti mon argent ?"})
    names = [e for e, _ in events]
    assert names[0] == "status" and events[0][1]["text"] == "lit ton dossier"
    assert ("status", {"text": "dessine le graphique", "tool": "show_chart"}) in events
    assert "reset" not in names, "le tour d'outil n'avait pas produit de texte : rien à effacer"
    deltas = "".join(d["text"] for e, d in events if e == "delta")
    assert deltas == "Surtout le logement, puis les courses."
    done = events[-1]
    assert (
        done[0] == "done"
        and done[1]["answer"] == deltas
        and [b["type"] for b in done[1]["blocks"]] == ["chart", "actions"]
    )
    assert all(c["stream"] for c in FakeLlm.calls), "le flux est demandé au modèle à chaque appel"
    # La conversation existe bien, avec ses blocs, et une deuxième question la continue.
    detail = world.get(f"/api/assistant/conversations/{done[1]['conversation_id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"] and detail["messages"][1]["blocks"]
    # Du texte avant un appel d'outil (certains modèles « pensent » tout haut) : montré, puis effacé par « reset ».
    thinking = replace(tool_call("propose_actions", json.dumps({"keys": ["go:aides"]})), text="Je regarde… ")
    FakeLlm.script = [thinking, answer("Oui.")]
    events2 = _events(world, {"question": "Et les aides ?", "conversation_id": done[1]["conversation_id"]})
    names2 = [e for e, _ in events2]
    assert names2.index("reset") > names2.index("delta") and names2[-1] == "done"
    after_reset = "".join(d["text"] for e, d in events2[names2.index("reset") :] if e == "delta")
    assert after_reset == "Oui." and events2[-1][1]["answer"] == "Oui."
    assert events2[-1][1]["conversation_id"] == done[1]["conversation_id"]


def test_stream_endpoint_reports_errors_as_an_event(world: TestClient) -> None:
    configure(world, daily_calls_cap=0)
    events = _events(world, {"question": "Salut ?"})
    assert events[-1][0] == "error" and events[-1][1]["status"] == 429 and "Plafond" in events[-1][1]["detail"]
    assert not any(e == "done" for e, _ in events)
