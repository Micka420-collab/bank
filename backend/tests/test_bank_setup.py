"""Assistant de connexion Enable Banking : l'identifiant d'application et la clé privée se saisissent dans Bank, face à
un faux Enable Banking fidèle (JWT RS256 vérifié avec la clé publique de l'application, hôte lu dans l'en-tête Host :
netfetch épingle la connexion sur l'adresse résolue). Mot de passe Bank exigé, clé refusée jamais gardée, application
inactive ou adresse de retour absente expliquées, client reconstruit à chaud, la clé n'apparaît jamais dans une
réponse, la base en clair, l'export ni le journal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import AuditLog, ProviderCredential
from app.services import bank_setup as svc
from tests.conftest import PASSWORD

APP_ID = "4f1c2b3a-9d8e-4c7b-a6f5-0e1d2c3b4a59"
CALLBACK = "https://testserver/api/banks/callback"


def _pem(key: Any) -> str:
    pem: bytes = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem.decode()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _pem(KEY)
OTHER_PEM = _pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))


class FakeEnableBanking:
    """api.enablebanking.com simulé : une application enregistrée (kid = APP_ID) et sa clé publique."""

    def __init__(self) -> None:
        self.app: dict[str, Any] = {
            "name": "Bank (perso)",
            "kid": APP_ID,
            "environment": "PRODUCTION",
            "redirect_urls": [CALLBACK],
            "active": True,
        }
        self.down = False
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # Corps en flux non lu, comme sur le réseau : netfetch lit le flux brut.
        resp = self._handle(request)
        return httpx.Response(resp.status_code, stream=httpx.ByteStream(resp.content), headers=resp.headers)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["host"] == "api.enablebanking.com"
        assert request.extensions["sni_hostname"] == "api.enablebanking.com"
        if self.down:
            return httpx.Response(503)
        token = request.headers["authorization"].removeprefix("Bearer ")
        if jwt.get_unverified_header(token).get("kid") != APP_ID:
            return httpx.Response(401, json={"message": "Unknown application"})
        try:
            jwt.decode(
                token,
                KEY.public_key(),
                algorithms=["RS256"],
                audience="api.enablebanking.com",
                issuer="enablebanking.com",
            )
        except jwt.InvalidTokenError:
            return httpx.Response(401, json={"message": "Invalid JWT signature"})
        if request.url.path == "/application":
            return httpx.Response(200, json=self.app)
        if request.url.path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [{"name": "Crédit Agricole", "country": "FR"}]})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def eb(app) -> FakeEnableBanking:  # type: ignore[no-untyped-def]
    fake = FakeEnableBanking()
    app.state.enablebanking_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.enablebanking_resolver = lambda host: ["93.184.216.34"]
    return fake


def _put(c: TestClient, password: str = PASSWORD, pem: str = PEM, app_id: str = APP_ID) -> Any:
    return c.put("/api/banks/setup", json={"password": password, "app_id": app_id, "private_key": pem})


def _audit(app) -> str:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        return " ".join(f"{a.action} {a.details}" for a in db.scalars(select(AuditLog)))


# ------------------------------------------------------------------ parcours
def test_wizard_saves_checks_and_serves_the_new_client_live(app, auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    st = c.get("/api/banks/setup").json()
    assert st["configured"] is False and st["source"] is None and st["redirect_url"] == CALLBACK
    assert c.get("/api/banks").json()["enabled"] is False
    assert c.get("/api/banks/aspsps").status_code == 503

    r = _put(c, app_id=f"{APP_ID}.pem")  # le nom du fichier collé tel quel est accepté
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["configured"] is True and st["source"] == "app"
    assert st["app_id_hint"] == "…3b4a59" and len(st["key_fingerprint"].replace(" ", "")) == 16
    assert st["check"]["ok"] is True and st["check"]["issues"] == [] and st["check"]["app_name"] == "Bank (perso)"
    # À chaud : pas de redémarrage, le choix de la banque passe tout de suite par le nouveau client (et netfetch).
    assert app.state.enablebanking is not None
    assert c.get("/api/banks").json()["enabled"] is True
    assert c.get("/api/banks/aspsps").json()[0]["name"] == "Crédit Agricole"
    assert [r.url.path for r in eb.calls] == ["/application", "/aspsps"]

    # La clé ne ressort jamais : ni la réponse, ni l'état, ni l'export, ni le journal ; chiffrée en base.
    body = "-----BEGIN PRIVATE KEY-----"
    secret_line = PEM.splitlines()[1]
    for out in (r.text, c.get("/api/banks/setup").text, _audit(app)):
        assert body not in out and secret_line not in out and APP_ID not in out
    export = c.post("/api/export", json={"password": PASSWORD})
    assert export.status_code == 200
    assert secret_line not in export.text and APP_ID not in export.text
    assert export.json()["bank_setup"]["app_id_hint"] == "…3b4a59"
    with app.state.sessionmaker() as db:
        raw = db.execute(text("SELECT client_id, secret FROM bank_provider_credentials")).one()
        assert APP_ID not in raw[0] and "PRIVATE KEY" not in raw[1]
        row = db.scalar(select(ProviderCredential))
        assert row is not None and row.client_id == APP_ID and "BEGIN PRIVATE KEY" in row.secret
    assert "bank.setup.saved" in _audit(app)


def test_password_is_required_and_a_refused_key_is_never_kept(app, auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    r = _put(c, password="pas le bon")
    assert r.status_code == 403 and "bank.setup.denied" in _audit(app)
    # Une autre clé que celle de l'application : Enable Banking refuse la signature, Bank ne garde rien.
    r = _put(c, pem=OTHER_PEM)
    assert r.status_code == 400 and "ne reconnaît pas cette clé" in r.json()["detail"]
    assert "PRIVATE KEY" not in r.text and "bank.setup.refused" in _audit(app)
    r = _put(c, app_id="0000aaaa-1111-2222-3333-444455556666")  # identifiant inconnu : même refus
    assert r.status_code == 400
    with app.state.sessionmaker() as db:
        assert db.scalar(select(ProviderCredential)) is None
    assert app.state.enablebanking is None and c.get("/api/banks/setup").json()["configured"] is False


@pytest.mark.parametrize(
    ("pem", "expected"),
    [
        ("bonjour", "taille"),
        (_pem(rsa.generate_private_key(65537, 2048)).replace("PRIVATE", "PUBLIC"), "clé publique"),
        ("-----BEGIN PRIVATE KEY-----\n" + "A" * 400 + "\n-----END PRIVATE KEY-----", "illisible"),
        (_pem(ec.generate_private_key(ec.SECP256R1())), "RSA"),
        (_pem(rsa.generate_private_key(65537, 1024)), "1024 bits"),  # noqa: S505 - la clé trop courte refusée
        (
            rsa.generate_private_key(65537, 2048)
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"phrase"),
            )
            .decode(),
            "phrase de passe",
        ),
    ],
)
def test_only_a_usable_rsa_private_key_is_accepted(auth_client: TestClient, eb, pem: str, expected: str) -> None:  # type: ignore[no-untyped-def]
    r = _put(auth_client, pem=pem)
    assert r.status_code == 422 and expected in r.json()["detail"]
    assert eb.calls == []  # rien n'est parti vers Enable Banking


def test_bad_app_id_is_explained(auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    r = _put(auth_client, app_id="mon app")
    assert r.status_code == 422 and "identifiant" in r.json()["detail"]


def test_inactive_app_is_saved_and_explained_until_accounts_are_linked(app, auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    eb.app["active"] = False
    st = _put(auth_client).json()
    assert st["configured"] is True and st["check"]["ok"] is False
    assert [i["code"] for i in st["check"]["issues"]] == ["inactive"]
    assert "Activate by linking accounts" in st["check"]["issues"][0]["message"]
    # Tu lies tes comptes dans le Control Panel, puis « Vérifier à nouveau ».
    eb.app["active"] = True
    st = auth_client.post("/api/banks/setup/check").json()
    assert st["check"]["ok"] is True and st["check"]["issues"] == []
    assert "bank.setup.check" in _audit(app)


def test_missing_redirect_url_and_sandbox_are_spelled_out(auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    eb.app["redirect_urls"] = ["https://ailleurs.example/callback"]
    eb.app["environment"] = "SANDBOX"
    st = _put(auth_client).json()
    codes = [i["code"] for i in st["check"]["issues"]]
    assert codes == ["redirect", "sandbox"] and st["check"]["ok"] is False
    assert CALLBACK in st["check"]["issues"][0]["message"]
    eb.app["redirect_urls"] = [CALLBACK + "/"]  # une barre finale ne fait pas une autre adresse
    st = auth_client.post("/api/banks/setup/check").json()
    assert st["check"]["ok"] is True and [i["code"] for i in st["check"]["issues"]] == ["sandbox"]


def test_unreachable_api_is_not_a_bad_key(settings: Settings) -> None:
    fake = FakeEnableBanking()
    fake.down = True
    client = svc.make_client(
        settings,
        APP_ID,
        PEM.encode(),
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        resolver=lambda host: ["93.184.216.34"],
    )
    client._sleep = lambda _s: None
    result = svc.check(client, settings, datetime(2026, 10, 24, tzinfo=UTC))
    assert result["ok"] is False and [i["code"] for i in result["issues"]] == ["unreachable"]
    assert len(fake.calls) == 3  # trois tentatives, puis une explication


def test_remove_needs_password_and_stops_the_client(app, auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    assert _put(c).status_code == 200
    assert c.request("DELETE", "/api/banks/setup", json={"password": "faux"}).status_code == 403
    assert app.state.enablebanking is not None
    r = c.request("DELETE", "/api/banks/setup", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["check"] is None
    assert app.state.enablebanking is None and c.get("/api/banks").json()["enabled"] is False
    assert c.post("/api/banks/setup/check").status_code == 409
    assert c.request("DELETE", "/api/banks/setup", json={"password": PASSWORD}).status_code == 404
    assert "bank.setup.removed" in _audit(app)


def test_saved_credentials_survive_a_restart(settings: Settings, app, auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    assert _put(auth_client).status_code == 200
    again = create_app(settings)
    with TestClient(again, base_url="https://testserver"):
        assert again.state.enablebanking is not None and again.state.enablebanking.app_id == APP_ID


def test_server_configuration_wins_and_is_not_editable(tmp_path: Path, settings: Settings) -> None:
    key_file = tmp_path / "eb.pem"
    key_file.write_text(PEM)
    server = settings.model_copy(update={"enablebanking_app_id": APP_ID, "enablebanking_key_file": key_file})
    app = create_app(server)
    from tests.conftest import do_login, do_setup

    with TestClient(app, base_url="https://testserver") as c:
        do_setup(c)
        do_login(c)
        st = c.get("/api/banks/setup").json()
        assert st["configured"] is True and st["source"] == "server" and st["app_id_hint"] == "…3b4a59"
        assert st["key_fingerprint"] == svc.short_fingerprint(svc.fingerprint(PEM.encode()))
        assert _put(c).status_code == 409
        assert c.request("DELETE", "/api/banks/setup", json={"password": PASSWORD}).status_code == 409


def test_setup_calls_are_rate_limited(auth_client: TestClient, eb) -> None:  # type: ignore[no-untyped-def]
    assert _put(auth_client).status_code == 200
    codes = [auth_client.post("/api/banks/setup/check").status_code for _ in range(10)]
    assert codes[:9] == [200] * 9 and codes[-1] == 429


def test_validation_errors_never_echo_the_key(auth_client: TestClient) -> None:
    r = auth_client.put("/api/banks/setup", json={"password": PASSWORD, "app_id": APP_ID, "private_key": PEM * 10})
    assert r.status_code == 422 and PEM.splitlines()[1] not in r.text
    assert json.dumps(r.json()).count("BEGIN") == 0
