"""Powens (sur contrat), face à un faux Powens fidèle aux charges documentées (docs.powens.com, relu le 24/10/2026) :
hôte lu dans l'en-tête Host (netfetch épingle la connexion sur l'adresse résolue), jeton Bearer vérifié, pagination par
``_links.next``. Parcours : identifiants (mot de passe Bank exigé, secret refusé jamais gardé), webview et retour,
comptes et opérations sur deux pages, dédoublonnage, état SCARequired → carte « reconnecte », reconnexion, coupure,
retrait ; le secret et le jeton n'apparaissent ni dans une réponse, ni en clair en base, ni dans l'export ou le journal."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.models import Account, AuditLog, BankConnection, ProviderCredential, Transaction
from app.providers.powens import PowensClient, PowensError, map_transaction
from app.services import initiatives as initiatives_service
from tests.conftest import PASSWORD

DOMAIN = "bankperso-sandbox"
HOST = "bankperso-sandbox.biapi.pro"
CLIENT_ID = "48213977"
SECRET = "s3cr3t-Powens-0123456789abcdefABCDEF"
TOKEN = "jeton-permanent-utilisateur-0f9e8d7c6b5a"
CALLBACK = "https://testserver/api/banks/powens/callback"


class FakePowens:
    """{domain}.biapi.pro/2.0 simulé : une application (client_id + secret), un utilisateur, une connexion (7)."""

    def __init__(self) -> None:
        self.secret = SECRET
        self.token = TOKEN
        self.users = 0
        self.state: str | None = None
        self.deleted: list[str] = []
        self.calls: list[httpx.Request] = []
        self.next_href: str | None = None  # remplace le lien de page suivante (test d'hôte étranger)
        self.gone = False  # utilisateur effacé chez Powens : /auth/renew répond 404
        self.broken = False  # la connexion 7 ne se lit pas (403)
        self.undeletable = False  # la connexion 7 ne se supprime pas (403)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        resp = self._handle(request)
        # Corps en flux non lu, comme sur le réseau : netfetch lit le flux brut.
        return httpx.Response(resp.status_code, stream=httpx.ByteStream(resp.content), headers=resp.headers)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.headers["host"] == HOST
        assert request.extensions["sni_hostname"] == HOST
        path = request.url.path.removeprefix("/2.0")
        assert request.url.path.startswith("/2.0/")
        if path in ("/auth/init", "/auth/renew"):
            body = json.loads(request.content)
            if body.get("client_id") != CLIENT_ID or body.get("client_secret") != self.secret:
                return httpx.Response(401, json={"code": "invalidClient", "description": f"bad {body}"})
            if path == "/auth/renew":
                if self.gone:
                    return httpx.Response(404, json={"code": "userNotFound"})
                assert body["grant_type"] == "client_credentials" and body["id_user"] == 42
                return httpx.Response(200, json={"access_token": self.token, "token_type": "Bearer"})
            self.users += 1
            return httpx.Response(200, json={"auth_token": self.token, "type": "permanent", "id_user": 42})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"code": "invalidToken"})
        q = parse_qs(request.url.query.decode())
        if path == "/users/me":
            if request.method == "DELETE":
                self.deleted.append("user")
                return httpx.Response(204)
            return httpx.Response(200, json={"id": 42, "signin": "2026-10-24 10:00:00"})
        if path == "/auth/token/code":
            assert q["type"] == ["singleAccess"]
            return httpx.Response(200, json={"code": "code-temp-1", "type": "temporary", "access": "single"})
        if path == "/users/me/connections/7":
            if request.method == "DELETE" and self.undeletable:
                return httpx.Response(403, json={"code": "forbidden"})
            if request.method == "DELETE":
                self.deleted.append("connection:7")
                return httpx.Response(204)
            assert q["expand"] == ["connector"]
            if self.broken:
                return httpx.Response(403, json={"code": "forbidden"})
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "id_user": 42,
                    "state": self.state,
                    "error_message": None,
                    "connector": {"id": 59, "name": "Connecteur de test"},
                },
            )
        if path == "/users/me/connections/7/accounts":
            return httpx.Response(200, json={"accounts": ACCOUNTS, "balance": 0})
        if path == "/users/me/accounts/101/transactions":
            assert q["limit"] == ["1000"] and "min_date" in q
            if q.get("cursor") == ["page2"]:
                return httpx.Response(200, json={"transactions": PAGE2, "_links": {"next": None}})
            # Lien opaque et absolu, qui reprend les filtres de la première requête (comme dans la doc).
            query = request.url.query.decode()
            href = self.next_href or f"https://{HOST}/2.0/users/me/accounts/101/transactions?{query}&cursor=page2"
            return httpx.Response(200, json={"transactions": PAGE1, "_links": {"self": None, "next": {"href": href}}})
        if path.startswith("/users/me/accounts/") and path.endswith("/transactions"):
            return httpx.Response(200, json={"transactions": [], "_links": {"next": None}})
        if path == "/users/me/accounts/103/investments":
            return httpx.Response(200, json={"valuation": 15234.5, "investments": [{"id": 1, "label": "Fonds euros"}]})
        return httpx.Response(404, json={"code": "notFound"})


def _acc(id_: int, name: str, type_: Any, balance: Any, **kw: Any) -> dict[str, Any]:
    return {
        "id": id_,
        "id_connection": 7,
        "name": name,
        "original_name": name.upper(),
        "balance": balance,
        "coming": None,
        "currency": kw.get("currency", {"id": "EUR", "symbol": "€", "name": "Euro", "precision": 2}),
        "type": type_,
        "deleted": None,
        "disabled": None,
        "iban": kw.get("iban"),
        "loan": kw.get("loan"),
    }


ACCOUNTS = [
    _acc(101, "Compte chèque", "checking", 1234.56, iban="FR7630006000011234567890189"),
    _acc(102, "Livret A", {"name": "livret_a", "id": 4}, 8000),  # type objet : toléré
    _acc(103, "Assurance vie", "lifeinsurance", None),  # solde absent : valorisation des supports
    _acc(104, "Prêt immo", "loan", 150000, loan={"total_amount": 200000, "rate": 1.2, "duration": 240}),
    _acc(105, "Carte différée", "card", -230.4),  # ignorée : déjà dans le compte courant
    _acc(106, "Compte dollars", "checking", 50, currency={"id": "USD", "symbol": "$", "name": "US Dollar"}),
]
PAGE1 = [
    {"id": 9001, "id_account": 101, "date": "2026-10-20", "rdate": "2026-10-19", "value": -42.5,
     "original_wording": "CB CARREFOUR 19/10", "wording": "Carrefour", "coming": False, "deleted": None},
    {"id": 9002, "id_account": 101, "date": "2026-10-22", "value": 2100,
     "original_wording": "VIR SALAIRE OCTOBRE", "coming": False, "deleted": None},
]  # fmt: skip
PAGE2 = [
    {"id": 9003, "id_account": 101, "date": "2026-10-23", "value": -9.99,
     "original_wording": "PRLV NETFLIX", "coming": False, "deleted": None},
    {"id": 9004, "id_account": 101, "date": "2026-10-25", "value": -15,
     "original_wording": "CB A VENIR", "coming": True, "deleted": None},
    {"id": 9005, "id_account": 101, "date": "2026-10-10", "value": -1,
     "original_wording": "SUPPRIMEE", "coming": False, "deleted": "2026-10-11 08:00:00"},
]  # fmt: skip


@pytest.fixture
def pw(app) -> FakePowens:  # type: ignore[no-untyped-def]
    fake = FakePowens()
    app.state.powens_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.powens_resolver = lambda host: ["93.184.216.34"]
    return fake


def _put(c: TestClient, password: str = PASSWORD, secret: str = SECRET, domain: str = DOMAIN) -> Any:
    return c.put(
        "/api/banks/powens/setup",
        json={"password": password, "domain": domain, "client_id": CLIENT_ID, "client_secret": secret},
    )


def _audit(app) -> str:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        return " ".join(f"{a.action} {a.details}" for a in db.scalars(select(AuditLog)))


def _connect(c: TestClient) -> dict[str, list[str]]:
    r = c.post("/api/banks/powens/connect")
    assert r.status_code == 200, r.text
    url = urlsplit(r.json()["redirect_url"])
    assert (url.scheme, url.hostname, url.path) == ("https", "webview.powens.com", "/fr/connect")
    return parse_qs(url.query)


def _callback(c: TestClient, **params: str) -> str:
    r = c.get("/api/banks/powens/callback", params=params, follow_redirects=False)
    assert r.status_code == 303
    return str(r.headers["location"])


def _linked(c: TestClient, pw: FakePowens) -> None:
    assert _put(c).status_code == 200
    q = _connect(c)
    assert "result=connected" in _callback(c, connection_id="7", state=q["state"][0])


# ------------------------------------------------------------------ parcours
def test_setup_connect_and_sync_end_to_end(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    st = c.get("/api/banks/powens").json()
    assert st["configured"] is False and st["redirect_url"] == CALLBACK and st["connections"] == []
    assert c.post("/api/banks/powens/connect").status_code == 503

    r = _put(c, domain=f"https://{HOST}/2.0")  # l'adresse complète collée telle quelle est acceptée
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["configured"] is True and st["domain"] == DOMAIN and st["sandbox"] is True
    assert st["client_id_hint"] == "…3977" and len(st["secret_fingerprint"].replace(" ", "")) == 16
    assert st["check"]["ok"] is True and [i["code"] for i in st["check"]["issues"]] == ["sandbox"]
    assert "démonstration" in st["check"]["issues"][0]["message"]
    assert app.state.powens is not None and pw.users == 1

    # Webview : les paramètres documentés, un code temporaire, un state à usage unique.
    q = _connect(c)
    assert q["domain"] == [HOST] and q["client_id"] == [CLIENT_ID] and q["redirect_uri"] == [CALLBACK]
    assert q["code"] == ["code-temp-1"] and len(q["state"][0]) >= 40
    assert SECRET not in json.dumps(q) and TOKEN not in json.dumps(q)
    loc = _callback(c, connection_id="7", state=q["state"][0])
    assert loc == "https://testserver/#/banques?result=connected&provider=powens"
    assert "result=error" in _callback(c, connection_id="7", state=q["state"][0])  # rejouer : refusé

    st = c.get("/api/banks/powens").json()
    (conn,) = st["connections"]
    assert conn["aspsp_name"] == "Connecteur de test" and conn["status"] == "active"
    assert c.get("/api/banks").json()["connections"] == []  # pas mélangée aux connexions Enable Banking
    assert c.post(f"/api/banks/{conn['id']}/sync").status_code == 404

    r = c.post(f"/api/banks/powens/{conn['id']}/sync")
    assert r.status_code == 200 and r.json()["errors"] == []
    assert r.json()["inserted"] == 3  # deux pages ; ni l'opération à venir ni l'effacée
    with app.state.sessionmaker() as db:
        accounts = {a.name: a for a in db.scalars(select(Account))}
        assert {a.kind for a in accounts.values()} == {"checking", "livret_a", "assurance_vie", "credit"}
        assert not any("Carte" in n for n in accounts)
        cheque = next(a for n, a in accounts.items() if n.startswith("Compte chèque"))
        assert cheque.balance_cents == 123456 and cheque.provider == "powens" and cheque.include_in_budget
        assert cheque.name.endswith("••0189")
        assert accounts["Livret A"].balance_cents == 800000 and not accounts["Livret A"].include_in_budget
        assert accounts["Assurance vie"].balance_cents == 1523450
        assert accounts["Prêt immo"].balance_cents == -15000000
        assert cheque.currency == "EUR" and accounts["Compte dollars"].currency == "USD"  # le code, pas le nom
        labels = sorted(t.raw_label for t in db.scalars(select(Transaction)))
        assert len(labels) == 3 and "CB CARREFOUR 19/10" in labels
    # Deuxième synchronisation : tout est déjà là.
    r = c.post(f"/api/banks/powens/{conn['id']}/sync").json()
    assert r["inserted"] == 0 and r["duplicates"] == 3
    # Le jeton ne part que vers le domaine de l'API ; le secret seulement dans /auth/init.
    for call in pw.calls:
        assert call.headers["host"] == HOST
        if call.url.path != "/2.0/auth/init":
            assert SECRET.encode() not in call.content


def test_secret_never_leaves_bank(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    r = _put(c)
    assert r.status_code == 200
    texts = [r.text, c.get("/api/banks/powens").text, _audit(app)]
    export = c.post("/api/export", json={"password": PASSWORD})
    assert export.status_code == 200 and export.json()["powens_setup"]["client_id_hint"] == "…3977"
    texts.append(export.text)
    for out in texts:
        assert SECRET not in out and TOKEN not in out and CLIENT_ID not in out
    with app.state.sessionmaker() as db:
        raw = db.execute(text("SELECT client_id, secret, user_token FROM bank_provider_credentials")).one()
        assert CLIENT_ID not in raw[0] and SECRET not in raw[1] and TOKEN not in raw[2]
        row = db.scalar(select(ProviderCredential))
        assert row is not None and row.secret == SECRET and row.user_token == TOKEN and row.domain == DOMAIN
    assert repr(app.state.powens) == "PowensClient(domain='bankperso-sandbox')"
    # Une erreur de validation ne renvoie pas le secret saisi.
    r = c.put(
        "/api/banks/powens/setup",
        json={"password": PASSWORD, "domain": DOMAIN, "client_id": CLIENT_ID, "client_secret": SECRET + " x" * 200},
    )
    assert r.status_code == 422 and SECRET not in r.text


def test_password_required_and_refused_secret_is_never_kept(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    assert _put(c, password="pas le bon").status_code == 403
    assert "bank.setup.denied" in _audit(app)
    r = _put(c, secret="un-autre-secret-0123456789")
    assert r.status_code == 400 and "refuse ces identifiants" in r.json()["detail"]
    assert "invalidClient" not in r.text and "un-autre-secret" not in r.text
    assert "bank.setup.refused" in _audit(app)
    with app.state.sessionmaker() as db:
        assert db.scalar(select(ProviderCredential)) is None
    assert app.state.powens is None
    r = _put(c, domain="mon appli!")
    assert r.status_code == 422 and "domaine" in r.json()["detail"]
    r = _put(c, secret="court")
    assert r.status_code == 422 and "secret" in r.json()["detail"]


def test_sca_required_marks_the_connection_and_reconnect_resumes(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    _linked(c, pw)
    conn_id = c.get("/api/banks/powens").json()["connections"][0]["id"]
    pw.state = "SCARequired"
    r = c.post(f"/api/banks/powens/{conn_id}/sync").json()
    assert r["inserted"] == 0 and "authentification forte" in r["errors"][0]
    st = c.get("/api/banks/powens").json()["connections"][0]
    assert st["status"] == "expired" and "reconnecte" in st["last_error"]
    # La carte « reconnecte ta banque » apparaît sur l'accueil.
    with app.state.sessionmaker() as db:
        cards = initiatives_service.cards(db, date(2026, 10, 24))
    card = next(k for k in cards if k.key == f"consent:{conn_id}:reconnect")
    assert "Connecteur de test" in card.title and "authentification forte" in card.body

    # Reconnexion : la webview « reconnect » sur la même connexion, retour avec le seul state.
    r = c.post(f"/api/banks/powens/{conn_id}/reconnect")
    url = urlsplit(r.json()["redirect_url"])
    q = parse_qs(url.query)
    assert url.path == "/fr/reconnect" and q["connection_id"] == ["7"] and q["code"] == ["code-temp-1"]
    pw.state = None
    assert "result=connected" in _callback(c, state=q["state"][0])
    assert c.get("/api/banks/powens").json()["connections"][0]["status"] == "active"
    r = c.post(f"/api/banks/powens/{conn_id}/sync").json()
    assert r["errors"] == [] and r["inserted"] == 3


def test_passing_state_still_syncs_with_a_warning(auth_client: TestClient, pw: FakePowens) -> None:
    _linked(auth_client, pw)
    conn_id = auth_client.get("/api/banks/powens").json()["connections"][0]["id"]
    pw.state = "websiteUnavailable"
    r = auth_client.post(f"/api/banks/powens/{conn_id}/sync").json()
    assert r["inserted"] == 3 and "indisponible" in r["errors"][0]
    assert auth_client.get("/api/banks/powens").json()["connections"][0]["status"] == "active"


def test_webview_error_and_unknown_state(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    assert _put(c).status_code == 200
    q = _connect(c)
    loc = _callback(c, state=q["state"][0], error="access_denied", error_description="L'utilisateur a annulé")
    assert "result=refused" in loc
    assert "result=error" in _callback(c, state="inconnu", connection_id="7")
    assert "result=refused" in _callback(c)
    q = _connect(c)
    assert "result=error" in _callback(c, state=q["state"][0], connection_id="7/../../users")
    with app.state.sessionmaker() as db:  # tentatives sans suite : aucune ligne « Powens · erreur » ne reste
        assert list(db.scalars(select(BankConnection))) == []
    assert "bank.connect.failed" in _audit(app) and c.get("/api/banks/powens").json()["connections"] == []


def test_unreadable_connection_is_deleted_or_revocable(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    assert _put(c).status_code == 200
    pw.broken = True
    q = _connect(c)
    assert "result=error" in _callback(c, connection_id="7", state=q["state"][0])
    assert pw.deleted == ["connection:7"]  # supprimée chez Powens : rien d'invisible n'y reste
    assert c.get("/api/banks/powens").json()["connections"] == []
    # Suppression impossible : la connexion reste listée, en erreur, et se coupe.
    pw.undeletable = True
    q = _connect(c)
    assert "result=error" in _callback(c, connection_id="7", state=q["state"][0])
    (conn,) = c.get("/api/banks/powens").json()["connections"]
    assert conn["status"] == "error" and "coupe-la" in conn["last_error"]
    pw.undeletable = False
    assert c.delete(f"/api/banks/powens/{conn['id']}").status_code == 204
    assert pw.deleted == ["connection:7", "connection:7"]


def test_pagination_link_to_another_host_is_refused(auth_client: TestClient, pw: FakePowens) -> None:
    _linked(auth_client, pw)
    conn_id = auth_client.get("/api/banks/powens").json()["connections"][0]["id"]
    pw.next_href = "https://evil.example/2.0/users/me/accounts/101/transactions?cursor=page2"
    r = auth_client.post(f"/api/banks/powens/{conn_id}/sync").json()
    assert "pagination" in r["errors"][0]
    assert all(call.headers["host"] == HOST for call in pw.calls)


def test_revoke_and_remove(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    _linked(c, pw)
    conn_id = c.get("/api/banks/powens").json()["connections"][0]["id"]
    assert c.delete(f"/api/banks/{conn_id}").status_code == 404  # pas une connexion Enable Banking
    assert c.delete(f"/api/banks/powens/{conn_id}").status_code == 204
    assert pw.deleted == ["connection:7"]
    assert c.get("/api/banks/powens").json()["connections"][0]["status"] == "revoked"
    assert c.request("DELETE", "/api/banks/powens/setup", json={"password": "faux"}).status_code == 403
    r = c.request("DELETE", "/api/banks/powens/setup", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["check"] is None
    assert pw.deleted == ["connection:7", "user"] and app.state.powens is None
    assert "bank.setup.removed" in _audit(app)


def test_changing_only_the_secret_keeps_the_user(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    _linked(c, pw)
    pw.secret = "nouveau-secret-0123456789abcdef"
    pw.token = "jeton-renouvele-1122334455"
    r = _put(c, secret=pw.secret)
    assert r.status_code == 200 and r.json()["check"]["ok"] is True
    assert pw.users == 1  # /auth/renew : même utilisateur Powens, connexions gardées
    assert r.json()["connections"][0]["status"] == "active"
    # Changer de domaine avec des banques reliées : refusé tant qu'elles ne sont pas coupées.
    r = _put(c, secret=pw.secret, domain="autreappli")
    assert r.status_code == 409
    # Sans identifiant d'utilisateur connu, un nouvel utilisateur orphelinerait les banques reliées : refusé aussi.
    with app.state.sessionmaker() as db:
        row = db.scalar(select(ProviderCredential))
        assert row is not None
        row.user_ref = None
        db.commit()
    assert _put(c, secret=pw.secret).status_code == 409 and pw.users == 1


def test_user_deleted_at_powens_is_replaced_and_never_orphaned(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    assert _put(c).status_code == 200
    pw.gone = True  # effacé depuis la console : le renouvellement répond 404
    r = _put(c)
    assert r.status_code == 200 and r.json()["check"]["ok"] is True
    assert pw.users == 2 and pw.deleted == ["user"]  # l'ancien est effacé au mieux, pas laissé chez Powens
    assert "replaced_user" in _audit(app)


def test_password_checks_share_the_login_budget(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    c = auth_client
    codes = [_put(c, password="pas le bon").status_code for _ in range(10)]
    assert codes[-1] == 429 and set(codes) == {403, 429}
    # Le budget épuisé ici l'est aussi pour l'export (même limiteur que la connexion).
    assert c.post("/api/export", json={"password": PASSWORD}).status_code == 429
    r = c.request("DELETE", "/api/banks/setup", json={"password": PASSWORD})
    assert r.status_code == 429


def test_account_followed_by_enable_banking_is_not_taken_over(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    from app.security.crypto import get_keyring

    iban = "FR7630006000011234567890189"
    with app.state.sessionmaker() as db:
        eb = BankConnection(aspsp_name="Ma banque", aspsp_country="FR", status="active", session_id="s-1")
        acc = Account(name="Courant EB", kind="checking", provider="enablebanking", provider_uid="eb-uid", iban=iban)
        acc.iban_bidx = get_keyring().blind_index(iban, "account.iban")
        acc.connection = eb
        db.add_all([eb, acc])
        db.commit()
        eb_id = eb.id
    c = auth_client
    _linked(c, pw)
    (conn,) = c.get("/api/banks/powens").json()["connections"]
    assert "Enable Banking" in conn["last_error"] and "Compte chèque" in conn["last_error"]
    r = c.post(f"/api/banks/powens/{conn['id']}/sync").json()
    assert r["inserted"] == 0 and any("Enable Banking" in e for e in r["errors"])
    with app.state.sessionmaker() as db:
        kept = db.scalar(select(Account).where(Account.name == "Courant EB"))
        assert kept is not None and kept.provider == "enablebanking" and kept.connection_id == eb_id


def test_automatic_sync_uses_clients_saved_after_startup(app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    from app.main import _sync_once
    from tests.test_bank_setup import PEM, FakeEnableBanking

    fake_eb = FakeEnableBanking()
    app.state.enablebanking_http = httpx.Client(transport=httpx.MockTransport(fake_eb))
    app.state.enablebanking_resolver = lambda host: ["93.184.216.34"]
    assert app.state.enablebanking is None and app.state.powens is None  # rien au démarrage
    r = auth_client.put(
        "/api/banks/setup", json={"password": PASSWORD, "app_id": fake_eb.app["kid"], "private_key": PEM}
    )
    assert r.status_code == 200, r.text
    _linked(auth_client, pw)
    with app.state.sessionmaker() as db:
        eb = BankConnection(aspsp_name="Ma banque", aspsp_country="FR", status="active", session_id="s-1")
        acc = Account(name="Courant EB", kind="checking", provider="enablebanking", provider_uid="eb-uid")
        acc.connection = eb
        db.add_all([eb, acc])
        db.commit()
    fake_eb.calls.clear()
    pw.calls.clear()
    _sync_once(app, date(2026, 10, 24))
    assert any("/accounts/eb-uid/" in str(call.url) for call in fake_eb.calls)
    assert any(call.url.path.endswith("/transactions") for call in pw.calls)


def test_credentials_survive_a_restart(settings, app, auth_client: TestClient, pw: FakePowens) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    assert _put(auth_client).status_code == 200
    again = create_app(settings)
    with TestClient(again, base_url="https://testserver"):
        assert again.state.powens is not None and again.state.powens.domain == DOMAIN


def test_setup_is_rate_limited(auth_client: TestClient, pw: FakePowens) -> None:
    assert _put(auth_client).status_code == 200
    codes = [auth_client.post("/api/banks/powens/setup/check").status_code for _ in range(10)]
    assert codes[:9] == [200] * 9 and codes[-1] == 429


# ------------------------------------------------------------------ client seul
def test_transaction_mapping() -> None:
    t = map_transaction({"id": 1, "date": "2026-10-01", "value": "-12.30", "original_wording": "CB X", "coming": False})
    assert t is not None and t.amount_cents == -1230 and t.external_id == "1" and t.status == "booked"
    assert map_transaction({"id": 2, "date": "2026-10-01", "value": 5, "coming": True}).status == "pending"  # type: ignore[union-attr]
    assert map_transaction({"id": 3, "date": "2026-10-01", "value": 5, "deleted": "2026-10-02"}) is None
    assert map_transaction({"id": 4, "value": 5}) is None


def test_client_rejects_bad_ids_and_hides_error_bodies() -> None:
    fake = FakePowens()
    client = PowensClient(
        DOMAIN,
        CLIENT_ID,
        "mauvais-secret-0123456789",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        resolver=lambda host: ["93.184.216.34"],
    )
    with pytest.raises(PowensError) as err:
        client.init_user()
    assert err.value.status == 401 and err.value.code == "invalidClient"
    assert "mauvais-secret" not in str(err.value)
    client.user_token = TOKEN
    with pytest.raises(PowensError):
        client.list_accounts("7/../../users")
    with pytest.raises(ValueError):
        PowensClient("evil.example/", CLIENT_ID, SECRET)
