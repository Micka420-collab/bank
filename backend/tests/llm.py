"""Modèle de langage simulé, partagé par les tests de l'assistant et de sa mémoire."""

from __future__ import annotations

from typing import Any, ClassVar

from starlette.testclient import TestClient

from app.providers.openrouter import ChatResult, OpenRouterError, ToolCall
from tests.conftest import PASSWORD

KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
MODEL = "anthropic/claude-haiku-5.5"


class FakeLlm:
    """Modèle simulé : journalise les messages reçus, répond ce qu'on lui dit (ou suit un script : réponses,
    appels d'outils, erreurs), peut échouer."""

    calls: ClassVar[list[dict[str, Any]]] = []
    reply: ClassVar[str] = "Tu peux dépenser **75 €** par jour."
    cost: ClassVar[float] = 0.0005
    fail: ClassVar[OpenRouterError | None] = None
    keys: ClassVar[list[str | None]] = []
    script: ClassVar[list[ChatResult | OpenRouterError]] = []

    def __init__(self, api_key: str | None) -> None:
        FakeLlm.keys.append(api_key)

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.3,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        FakeLlm.calls.append(
            {
                "model": model,
                "messages": [dict(m) for m in messages],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": [t["function"]["name"] for t in tools or []],
            }
        )
        if FakeLlm.fail is not None:
            raise FakeLlm.fail
        if FakeLlm.script:
            step = FakeLlm.script.pop(0)
            if isinstance(step, OpenRouterError):
                raise step
            return step
        return ChatResult(FakeLlm.reply, model, 1500, 60, FakeLlm.cost, "stop")

    def list_models(self) -> list[Any]:
        raise OpenRouterError("pas de réseau en test")


def tool_call(name: str, arguments: str, call_id: str = "call_1") -> ChatResult:
    """Réponse simulée qui demande un outil (comme le ferait le modèle)."""
    return ChatResult("", MODEL, 900, 30, 0.0002, "tool_calls", (ToolCall(call_id, name, arguments),))


def answer(text: str, cost: float = 0.0003) -> ChatResult:
    return ChatResult(text, MODEL, 1000, 40, cost, "stop")


def configure(world: TestClient, **over: Any) -> Any:
    body = {
        "password": PASSWORD,
        "api_key": KEY,
        "model": MODEL,
        "privacy": "redacted",
        "monthly_cap_usd_cents": 300,
        "daily_calls_cap": 5,
    }
    body.update(over)
    return world.put("/api/assistant", json=body)
