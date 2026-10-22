"""Passkeys : un authentificateur simulé (clé P-256, attestation « none ») s'enregistre avec le mot de passe, se
connecte sans lui, et tout le reste est refusé — fausse origine, compteur rejoué, vérification utilisateur
absente, défi réutilisé, passkey retirée."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from starlette.testclient import TestClient

from app.models import AuditLog, User
from app.security import passkeys
from tests.conftest import PASSWORD, USERNAME, do_login

RP_ID, ORIGIN = "testserver", "https://testserver"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Authenticator:
    """Ce que fait un téléphone ou une clé : une clé privée qui ne sort jamais, un compteur, et une signature sur
    le défi, l'origine et le domaine."""

    def __init__(self, origin: str = ORIGIN, rp_id: str = RP_ID) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(16)
        self.counter = 0
        self.origin, self.rp_id = origin, rp_id
        self.user_handle: bytes | None = None

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict[str, Any], *, uv: bool = True) -> dict[str, Any]:
        self.user_handle = base64.urlsafe_b64decode(options["user"]["id"] + "==")
        cdj = self._client_data("webauthn.create", options["challenge"])
        flags = 0x41 | (0x04 if uv else 0)
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + self.counter.to_bytes(4, "big")
            + bytes(16) + len(self.cred_id).to_bytes(2, "big") + self.cred_id + self._cose()
        )  # fmt: skip
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(cdj), "attestationObject": b64u(att), "transports": ["internal"]},
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }  # fmt: skip

    def get(
        self, options: dict[str, Any], *, uv: bool = True, bump: int = 1, handle: bytes | None = None
    ) -> dict[str, Any]:
        self.counter += bump
        cdj = self._client_data("webauthn.get", options["challenge"])
        flags = 0x01 | (0x04 if uv else 0)
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + self.counter.to_bytes(4, "big")
        sig = self.key.sign(auth_data + hashlib.sha256(cdj).digest(), ec.ECDSA(hashes.SHA256()))
        used = handle if handle is not None else self.user_handle
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(cdj), "authenticatorData": b64u(auth_data), "signature": b64u(sig),
                         "userHandle": b64u(used) if used else None},
            "clientExtensionResults": {},
        }  # fmt: skip


def register(client: TestClient, auth: Authenticator, name: str = "iPhone de Micka", password: str = PASSWORD) -> Any:
    opts = client.post("/api/auth/passkeys/register/options").json()
    assert (
        opts["options"]["rp"]["id"] == RP_ID
        and opts["options"]["authenticatorSelection"]["userVerification"] == "required"
    )
    assert (
        opts["options"]["attestation"] == "none"
        and opts["options"]["authenticatorSelection"]["residentKey"] == "required"
    )
    return client.post(
        "/api/auth/passkeys/register",
        json={"state": opts["state"], "credential": auth.create(opts["options"]), "name": name, "password": password},
    )


def login_with(client: TestClient, auth: Authenticator, **kw: Any) -> Any:
    opts = client.post("/api/auth/passkeys/login/options").json()
    assert opts["options"]["userVerification"] == "required" and opts["options"].get("allowCredentials", []) == []
    return client.post(
        "/api/auth/passkeys/login", json={"state": opts["state"], "credential": auth.get(opts["options"], **kw)}
    )


def test_register_with_password_then_login_without_it(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    auth = Authenticator()
    assert auth_client.get("/api/auth/passkeys").json()["passkeys"] == []
    assert register(auth_client, auth, password="faux").status_code == 403, "ajouter une passkey exige le mot de passe"
    r = register(auth_client, auth)
    assert r.status_code == 201 and r.json()["passkey"]["name"] == "iPhone de Micka", r.text
    listing = auth_client.get("/api/auth/passkeys").json()
    assert [k["name"] for k in listing["passkeys"]] == ["iPhone de Micka"] and listing["rp_id"] == RP_ID
    assert auth_client.get("/api/auth/me").json()["passkeys"] == 1
    assert auth_client.post("/api/auth/logout").status_code == 200
    auth_client.cookies.clear()
    auth_client.headers.pop("x-csrf-token", None)
    assert auth_client.get("/api/auth/me").status_code == 401
    r = login_with(auth_client, auth)
    assert r.status_code == 200 and r.json() == {"ok": True, "username": USERNAME}, r.text
    assert auth_client.get("/api/auth/me").json()["username"] == USERNAME, "session ouverte par la passkey"
    with app.state.sessionmaker() as s:
        actions = [a for (a,) in s.execute(select(AuditLog.action))]
        assert "passkey.added" in actions and "passkey.denied" in actions
        last = s.scalar(select(AuditLog).where(AuditLog.action == "login.success").order_by(AuditLog.id.desc()))
        assert last is not None and '"passkey":' in last.details and '"mfa":true' in last.details
        from app.models import Passkey

        row = s.scalar(select(Passkey))
        assert row is not None and row.sign_count == 1 and row.last_used_at is not None


def test_phishing_replay_and_missing_verification_are_refused(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    auth = Authenticator()
    assert register(auth_client, auth).status_code == 201
    auth_client.post("/api/auth/logout")
    auth_client.cookies.clear()
    auth_client.headers.pop("x-csrf-token", None)
    # Fausse page : l'origine signée n'est pas celle de Bank.
    evil = Authenticator(origin="https://bank-nimbus.example")
    evil.key, evil.cred_id, evil.user_handle = auth.key, auth.cred_id, auth.user_handle
    assert login_with(auth_client, evil, bump=5).status_code == 401
    # Vérification utilisateur absente (pas de code ni de biométrie) : refus.
    assert login_with(auth_client, auth, uv=False, bump=5).status_code == 401
    # Un défi ne sert qu'une fois, même encore valable.
    opts = auth_client.post("/api/auth/passkeys/login/options").json()
    first = auth_client.post(
        "/api/auth/passkeys/login", json={"state": opts["state"], "credential": auth.get(opts["options"], bump=5)}
    )
    assert first.status_code == 200
    accepted = auth.counter
    auth_client.cookies.clear()
    again = auth_client.post(
        "/api/auth/passkeys/login", json={"state": opts["state"], "credential": auth.get(opts["options"], bump=5)}
    )
    assert again.status_code == 401
    # Compteur qui ne monte pas (clonage) : refus ; mauvais identifiant d'utilisateur : refus.
    auth.counter = accepted  # le dernier compteur accepté, rejoué tel quel
    assert login_with(auth_client, auth, bump=0).status_code == 401
    assert login_with(auth_client, auth, bump=5, handle=b"x" * 32).status_code == 401
    # Les échecs comptent comme des échecs de connexion.
    with app.state.sessionmaker() as s:
        user = s.scalar(select(User))
        assert user is not None and user.failed_attempts >= 2, "compteur rejoué et mauvais identifiant"
    # Une passkey inconnue ne dit rien de plus qu'un mauvais mot de passe.
    stranger = Authenticator()
    stranger.user_handle = auth.user_handle
    r = login_with(auth_client, stranger, bump=1)
    assert r.status_code == 401 and r.json()["detail"] == "Identifiant, mot de passe ou code incorrect"


def test_registration_needs_a_fresh_challenge_and_refuses_duplicates(auth_client: TestClient) -> None:
    auth = Authenticator()
    opts = auth_client.post("/api/auth/passkeys/register/options").json()
    other = auth_client.post("/api/auth/passkeys/register/options").json()
    # Réponse faite sur un autre défi que celui de l'état : refus.
    r = auth_client.post(
        "/api/auth/passkeys/register",
        json={"state": opts["state"], "credential": auth.create(other["options"]), "name": "x", "password": PASSWORD},
    )
    assert r.status_code == 400
    assert (
        auth_client.post(
            "/api/auth/passkeys/register",
            json={"state": "x" * 20, "credential": auth.create(opts["options"]), "name": "x", "password": PASSWORD},
        ).status_code
        == 400
    )
    assert register(auth_client, auth).status_code == 201
    assert register(auth_client, auth, name="encore").status_code == 409, "la même passkey deux fois"
    # Sans vérification utilisateur à l'enregistrement : refus.
    fresh = Authenticator()
    o = auth_client.post("/api/auth/passkeys/register/options").json()
    assert "id" in o["options"]["excludeCredentials"][0]
    r = auth_client.post(
        "/api/auth/passkeys/register",
        json={
            "state": o["state"],
            "credential": fresh.create(o["options"], uv=False),
            "name": "clé",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 400


def test_removing_a_passkey_needs_the_password_and_closes_the_door(auth_client: TestClient) -> None:
    auth = Authenticator()
    key_id = register(auth_client, auth).json()["passkey"]["id"]
    assert auth_client.post(f"/api/auth/passkeys/{key_id}/delete", json={"password": "faux"}).status_code == 403
    assert auth_client.post(f"/api/auth/passkeys/{key_id}/delete", json={"password": PASSWORD}).status_code == 200
    assert auth_client.post(f"/api/auth/passkeys/{key_id}/delete", json={"password": PASSWORD}).status_code == 404
    auth_client.post("/api/auth/logout")
    auth_client.cookies.clear()
    auth_client.headers.pop("x-csrf-token", None)
    assert login_with(auth_client, auth).status_code == 401
    do_login(auth_client)  # le mot de passe reste la porte de secours
    assert auth_client.get("/api/auth/me").json()["passkeys"] == 0


def test_relying_party_comes_from_the_public_url() -> None:
    assert passkeys.relying_party("https://bank.nimbus.lan") == passkeys.RelyingParty(
        "bank.nimbus.lan", "https://bank.nimbus.lan"
    )
    assert passkeys.relying_party("https://bank.example.org:8443/") == passkeys.RelyingParty(
        "bank.example.org", "https://bank.example.org:8443"
    )
    assert (
        passkeys.user_handle(1, "micka") != passkeys.user_handle(2, "micka")
        and len(passkeys.user_handle(1, "micka")) == 32
    )
