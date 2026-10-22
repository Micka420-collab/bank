"""« Enregistré » n'est dit que quand c'est vrai : la transaction d'une requête est validée avant que la réponse
parte. Un observateur (une autre session, comme le gardien MCP ou la requête suivante du navigateur) regarde la
base à l'instant précis où les octets de la réponse sont envoyés — la ligne doit déjà y être."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from starlette.testclient import TestClient

from app.models import Account
from tests.conftest import do_login, do_setup


class _Observer:
    """Enveloppe ASGI : au moment de « http.response.body » d'une requête choisie, exécute une sonde."""

    def __init__(self, app: Any, probe: Callable[[], Any], *, method: str, path: str) -> None:
        self.app, self.probe, self.method, self.path = app, probe, method, path
        self.seen: list[Any] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != self.method or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        async def spy(message: Any) -> None:
            if message["type"] == "http.response.body":
                self.seen.append(self.probe())
            await send(message)

        await self.app(scope, receive, spy)


def test_the_row_is_committed_before_the_response_leaves(app) -> None:  # type: ignore[no-untyped-def]
    def count_accounts() -> int:
        with app.state.sessionmaker() as s:
            return len(list(s.scalars(select(Account.id).where(Account.name == "Livret observé"))))

    observer = _Observer(app, count_accounts, method="POST", path="/api/accounts")
    with TestClient(observer, base_url="https://testserver") as client:
        do_setup(client)
        do_login(client)
        r = client.post("/api/accounts", json={"name": "Livret observé", "kind": "savings", "balance_cents": 1000})
        assert r.status_code == 201, r.text
    assert observer.seen, "la sonde doit avoir vu partir la réponse"
    assert observer.seen[0] == 1, "la ligne doit être validée en base avant l'envoi de la réponse"
