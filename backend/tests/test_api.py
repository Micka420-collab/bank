"""Tests d'intégration HTTP : sécurité de bout en bout + parcours métier complets."""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pyotp
import pytest
from sqlalchemy import select, text, update
from starlette.testclient import TestClient

from app.db import utcnow
from app.fiscal.client import FiscalClient
from app.models import Account, AuthSession, Transaction
from app.security.crypto import sha256_hex
from tests.conftest import PASSWORD, SETUP_TOKEN, USERNAME, do_login, do_setup, fixture_bytes
from tests.test_enablebanking import FakeBank, make_client

VALID_IBAN = "FR7630004000031234567890143"


# =============================================================================== sécurité
class TestSetupAndLogin:
    def test_setup_requires_token_and_strong_password(self, client: TestClient) -> None:
        assert client.get("/api/setup/status").json() == {"needs_setup": True}
        bad = client.post(
            "/api/setup", json={"setup_token": "wrong-token-xx", "username": USERNAME, "password": PASSWORD}
        )
        assert bad.status_code == 403
        weak = client.post("/api/setup", json={"setup_token": SETUP_TOKEN, "username": USERNAME, "password": "court"})
        assert weak.status_code == 422 and "12 caractères" in weak.json()["detail"]
        do_setup(client)
        again = client.post("/api/setup", json={"setup_token": SETUP_TOKEN, "username": "autre", "password": PASSWORD})
        assert again.status_code == 409
        assert client.get("/api/setup/status").json() == {"needs_setup": False}

    def test_login_cookies_are_hardened(self, client: TestClient) -> None:
        do_setup(client)
        r = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        cookies = r.headers.get_list("set-cookie")
        session = next(c for c in cookies if c.startswith("__Host-bank_session="))
        assert "HttpOnly" in session and "Secure" in session and "SameSite=strict" in session and "Path=/" in session
        assert "Domain" not in session
        csrf = next(c for c in cookies if c.startswith("bank_csrf="))
        assert "HttpOnly" not in csrf and "SameSite=strict" in csrf

    def test_generic_errors_and_lockout(self, client: TestClient) -> None:
        do_setup(client)
        unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
        wrong = client.post("/api/auth/login", json={"username": USERNAME, "password": "x"})
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()  # pas d'énumération d'identifiants
        for _ in range(4):
            client.post("/api/auth/login", json={"username": USERNAME, "password": "mauvais"})
        locked = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        assert locked.status_code == 401  # verrouillé : même le bon mot de passe est refusé

    def test_rate_limit(self, client: TestClient) -> None:
        do_setup(client)
        codes = [client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code for _ in range(12)]
        assert 429 in codes

    def test_validation_errors_never_echo_input(self, client: TestClient) -> None:
        r = client.post("/api/auth/login", json={"username": "", "password": "s3cr3t-value"})
        assert r.status_code == 422 and "s3cr3t-value" not in r.text


class TestSessionsAndCsrf:
    def test_csrf_and_origin_enforced(self, auth_client: TestClient) -> None:
        token = auth_client.headers.pop("x-csrf-token")
        assert auth_client.post("/api/accounts", json={"name": "A"}).status_code == 403
        auth_client.headers["x-csrf-token"] = "forged"
        assert auth_client.post("/api/accounts", json={"name": "A"}).status_code == 403
        auth_client.headers["x-csrf-token"] = token
        evil = auth_client.post("/api/accounts", json={"name": "A"}, headers={"origin": "https://evil.example"})
        assert evil.status_code == 403
        ok = auth_client.post("/api/accounts", json={"name": "A"}, headers={"origin": "https://testserver"})
        assert ok.status_code == 201

    def test_unauthenticated_access_denied(self, client: TestClient) -> None:
        for path in (
            "/api/accounts",
            "/api/transactions",
            "/api/budget",
            "/api/aids",
            "/api/wealth",
            "/api/audit",
        ):
            assert client.get(path).status_code == 401, path

    def test_session_token_stored_hashed_and_idle_expiry(self, auth_client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
        raw = auth_client.cookies["__Host-bank_session"]
        assert db.get(AuthSession, raw) is None  # jamais en clair
        sess = db.get(AuthSession, sha256_hex(raw))
        assert sess is not None
        db.execute(update(AuthSession).values(last_seen_at=utcnow() - timedelta(hours=2)))
        db.commit()
        assert auth_client.get("/api/auth/me").status_code == 401

    def test_logout(self, auth_client: TestClient) -> None:
        assert auth_client.post("/api/auth/logout").status_code == 200
        assert auth_client.get("/api/auth/me").status_code == 401

    def test_password_change_revokes_other_sessions(self, auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
        with TestClient(app, base_url="https://testserver") as other:
            do_login(other)
            assert other.get("/api/auth/me").status_code == 200
            weak = auth_client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "x"})
            assert weak.status_code == 422
            bad = auth_client.post("/api/auth/password", json={"current_password": "nope", "new_password": "x" * 20})
            assert bad.status_code == 403
            new_pw = "une toute nouvelle phrase secrete"
            r = auth_client.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": new_pw})
            assert r.status_code == 200
            assert other.get("/api/auth/me").status_code == 401  # l'autre appareil est déconnecté
        auth_client.headers["x-csrf-token"] = auth_client.cookies["bank_csrf"]
        assert auth_client.get("/api/auth/me").status_code == 200


class TestTotp:
    def test_full_mfa_flow_with_replay_protection_and_recovery(self, auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
        setup = auth_client.post("/api/auth/totp/setup").json()
        assert setup["qr_svg"].startswith("<?xml") or "<svg" in setup["qr_svg"]
        totp = pyotp.TOTP(setup["secret"])
        assert auth_client.post("/api/auth/totp/enable", json={"code": "000000"}).status_code == 400
        used = totp.now()  # rejoué tel quel : si la fenêtre de 30 s tourne entre-temps, totp.now() serait un code neuf
        enabled = auth_client.post("/api/auth/totp/enable", json={"code": used})
        codes = enabled.json()["recovery_codes"]
        assert len(codes) == 10
        assert auth_client.get("/api/auth/me").json()["totp_enabled"] is True

        with TestClient(app, base_url="https://testserver") as c:
            step1 = c.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD}).json()
            assert step1["mfa_required"] is True and "__Host-bank_session" not in c.cookies
            # Le code déjà utilisé pour l'activation est refusé (anti-rejeu)
            replay = c.post("/api/auth/mfa", json={"mfa_token": step1["mfa_token"], "code": used})
            assert replay.status_code == 401
            ok = c.post("/api/auth/mfa", json={"mfa_token": step1["mfa_token"], "code": codes[0]})
            assert ok.status_code == 200 and c.get("/api/auth/me").json()["recovery_codes_left"] == 9
            tampered = c.post("/api/auth/mfa", json={"mfa_token": step1["mfa_token"] + "x", "code": codes[1]})
            assert tampered.status_code == 401


# =============================================================================== comptes & imports
def make_account(c: TestClient, **kw: object) -> int:
    r = c.post("/api/accounts", json={"name": "Courant", "kind": "checking", **kw})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def upload(c: TestClient, account_id: int, name: str, dry_run: bool = False, data: bytes | None = None) -> dict:  # type: ignore[type-arg]
    r = c.post(
        "/api/imports",
        data={"account_id": str(account_id), "dry_run": str(dry_run).lower()},
        files={"file": (name, data if data is not None else fixture_bytes(name), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


class TestAccounts:
    def test_iban_validated_encrypted_masked_and_unique(self, auth_client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
        bad = auth_client.post("/api/accounts", json={"name": "X", "iban": "FR7630004000031234567890144"})
        assert bad.status_code == 422
        aid = make_account(auth_client, iban="fr76 3000 4000 0312 3456 7890 143")
        out = next(a for a in auth_client.get("/api/accounts").json() if a["id"] == aid)
        assert out["iban_masked"] == "FR76 •••• •••• 0143" and VALID_IBAN not in json.dumps(out)
        stored = db.execute(text("select iban from accounts where id = :i"), {"i": aid}).scalar()
        assert stored.startswith("enc:") and VALID_IBAN not in stored
        assert auth_client.post("/api/accounts", json={"name": "Y", "iban": VALID_IBAN}).status_code == 409

    def test_update_account(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        r = auth_client.patch(f"/api/accounts/{aid}", json={"name": "Perso", "balance_cents": 12345, "archived": False})
        assert r.json()["name"] == "Perso" and r.json()["balance_cents"] == 12345
        assert auth_client.patch("/api/accounts/9999", json={"name": "x"}).status_code == 404


class TestImports:
    def test_dry_run_then_import_then_idempotent(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        preview = upload(auth_client, aid, "ca_export.csv", dry_run=True)
        assert preview["dry_run"] and preview["parsed"] == 9 and preview["inserted"] == 0
        assert auth_client.get("/api/transactions").json()["total"] == 0
        first = upload(auth_client, aid, "ca_export.csv")
        assert first["inserted"] == 9 and first["aids_detected"] == 2
        again = upload(auth_client, aid, "ca_export.csv")
        assert again["already_imported"] and again["inserted"] == 0 and again["duplicates"] == 9

    def test_categories_and_aids_assigned(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        items = {t["label"]: t for t in auth_client.get("/api/transactions?limit=50").json()["items"]}
        carrefour = next(t for k, t in items.items() if "CARREFOUR" in k)
        assert carrefour["category"] == "courses" and carrefour["merchant"] == "CARREFOUR MARKET ORLEANS"
        ppa = next(t for k, t in items.items() if "PRIME ACTIVITE" in k)
        assert ppa["aid_kind"] == "caf_ppa" and ppa["category"] == "aides"
        assert next(t for k, t in items.items() if "SALAIRE" in k)["category"] == "salaire"

    def test_ofx_import_updates_balance(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        r = upload(auth_client, aid, "sample_v1.ofx")
        assert r["format"] == "ofx" and r["inserted"] == 3
        acc = next(a for a in auth_client.get("/api/accounts").json() if a["id"] == aid)
        assert acc["balance_cents"] == 123456 and acc["provider"] == "file"

    def test_import_errors(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        bad = auth_client.post(
            "/api/imports",
            data={"account_id": str(aid), "dry_run": "false"},
            files={"file": ("x.csv", b"rien;ici\n", "text/csv")},
        )
        assert bad.status_code == 422
        missing = auth_client.post(
            "/api/imports",
            data={"account_id": "999", "dry_run": "true"},
            files={"file": ("x.csv", b"a", "text/csv")},
        )
        assert missing.status_code == 404

    def test_upload_size_limit(self, auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
        aid = make_account(auth_client)
        app.state.settings.max_upload_bytes = 10_000
        r = auth_client.post(
            "/api/imports",
            data={"account_id": str(aid), "dry_run": "true"},
            files={"file": ("big.csv", b"x" * 20_000, "text/csv")},
        )
        assert r.status_code == 413

    def test_internal_transfer_detected_across_accounts(self, auth_client: TestClient) -> None:
        courant = make_account(auth_client)
        livret = make_account(auth_client, name="Livret A", kind="livret_a")
        upload(
            auth_client,
            courant,
            "x.csv",
            data="Date;Libellé;Montant\n30/09/2026;VIR VERS LIVRET A;-300,00\n".encode(),
        )
        r = upload(auth_client, livret, "y.csv", data="Date;Libellé;Montant\n01/10/2026;VERSEMENT;300,00\n".encode())
        assert r["transfers_detected"] == 1
        items = auth_client.get("/api/transactions").json()["items"]
        assert all(t["is_transfer"] and t["category"] == "virements_internes" for t in items)


class TestTransactions:
    def test_search_filters_and_like_escaping(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        assert auth_client.get("/api/transactions?q=carrefour").json()["total"] == 2
        assert auth_client.get("/api/transactions?q=%25").json()["total"] == 0  # % littéral, pas joker
        assert auth_client.get("/api/transactions?category=telecom").json()["total"] == 1
        page = auth_client.get("/api/transactions?from=2026-09-10&to=2026-09-30").json()
        assert all("2026-09-10" <= t["booking_date"] <= "2026-09-30" for t in page["items"])

    def test_recategorize_learns_and_propagates(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        items = auth_client.get("/api/transactions?q=carrefour").json()["items"]
        r = auth_client.patch(f"/api/transactions/{items[0]['id']}", json={"category": "enfants", "note": "goûter"})
        assert r.json() == {"updated": 1, "propagated": 1}
        after = auth_client.get("/api/transactions?q=carrefour").json()["items"]
        assert {t["category"] for t in after} == {"enfants"}
        assert {t["category_source"] for t in after} == {"manual", "learned"}
        # Un nouvel import du même marchand applique la règle apprise
        upload(
            auth_client,
            aid,
            "z.csv",
            data="Date;Libellé;Montant\n10/10/2026;CB CARREFOUR MARKET 09/10 ORLEANS;-9,99\n".encode(),
        )
        latest = auth_client.get("/api/transactions?q=carrefour&from=2026-10-10").json()["items"][0]
        assert latest["category"] == "enfants" and latest["category_source"] == "learned"
        assert auth_client.patch(f"/api/transactions/{items[0]['id']}", json={"category": "nope"}).status_code == 422

    def test_user_rule_applies_to_history(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        r = auth_client.post("/api/rules", json={"pattern": "pharmacie du centre", "category": "enfants"})
        assert r.status_code == 201 and r.json()["updated"] == 1
        assert auth_client.get("/api/transactions?q=pharmacie").json()["items"][0]["category"] == "enfants"

    def test_category_budget(self, auth_client: TestClient) -> None:
        r = auth_client.patch("/api/categories/courses", json={"monthly_budget_cents": 40000})
        assert r.json()["monthly_budget_cents"] == 40000
        assert auth_client.patch("/api/categories/nope", json={"name": "x"}).status_code == 404


# =============================================================================== budget / aides / patrimoine
class TestInsights:
    def test_budget_endpoint(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client, balance_cents=100000)
        upload(auth_client, aid, "ca_export.csv")
        auth_client.put("/api/budget/settings", json={"buffer_cents": 5000, "savings_goal_cents": 20000})
        body = auth_client.get("/api/budget").json()
        f = body["forecast"]
        assert f["current_balance_cents"] == 100000 and len(f["projection"]) == f["days_left"]
        assert body["settings"] == {"buffer_cents": 5000, "savings_goal_cents": 20000}
        assert auth_client.get("/api/recurring").status_code == 200

    def test_aids_profile_and_simulation(self, auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        assert auth_client.post("/api/aids/simulate").status_code == 409  # pas de profil
        profile = {
            "birth_date": "1990-05-01",
            "postal_insee_code": "45234",
            "housing": "locataire_vide",
            "rent_cents": 65000,
            "activity": "actif",
        }
        assert auth_client.put("/api/profile", json=profile).status_code == 200
        stored = db.execute(text("select data from household_profile")).scalar()
        assert stored.startswith("enc:") and "45234" not in stored
        assert auth_client.get("/api/profile").json()["postal_insee_code"] == "45234"

        def engine(req: httpx.Request) -> httpx.Response:
            sit = json.loads(req.content)["situation"]
            assert sit["individus"]["moi"]["salaire_net"]["2026-09"] == 2150.0  # salaire réel des relevés
            return httpx.Response(
                200,
                json={
                    "engine_version": "176.1.2",
                    "results": {
                        "ppa": {"2026-10": 258.29},
                        "aide_logement": {"2026-10": 120.0},
                        "af": {"2026-10": 0},
                        "livret_epargne_populaire_eligibilite": {"2026-10": True},
                    },
                },
            )

        app.state.fiscal_client = FiscalClient(
            "http://fiscal",
            http=httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(engine)),
        )
        app.dependency_overrides.clear()
        from app.api import deps

        app.dependency_overrides[deps.today] = lambda: date(2026, 10, 21)
        sim = auth_client.post("/api/aids/simulate")
        assert sim.status_code == 200, sim.text
        assert sim.json()["lep_eligible"] is True
        aids = auth_client.get("/api/aids").json()
        gaps = {g["kind"]: g for g in aids["gaps"]}
        assert gaps["caf_logement"]["estimated_cents"] == 12000  # APL estimée > APL perçue -> écart signalé
        assert {s["kind"] for s in aids["summary"]["streams"]} >= {"caf_ppa", "caf_logement"}
        app.dependency_overrides.clear()

    def test_simulation_engine_down(self, auth_client: TestClient) -> None:
        auth_client.put("/api/profile", json={"birth_date": "1990-05-01", "postal_insee_code": "45234"})
        assert auth_client.post("/api/aids/simulate").status_code == 503

    def test_wealth_projection_and_goals(self, auth_client: TestClient) -> None:
        make_account(auth_client, balance_cents=150000)
        livret = make_account(auth_client, name="Livret A", kind="livret_a", balance_cents=500000)
        make_account(auth_client, name="Crédit", kind="credit", balance_cents=-200000)
        g = auth_client.post(
            "/api/goals",
            json={
                "name": "Matelas",
                "target_cents": 1_000_000,
                "account_id": livret,
                "monthly_contribution_cents": 50000,
                "target_date": "2027-12-01",
            },
        )
        assert g.status_code == 201
        w = auth_client.get("/api/wealth").json()
        assert w["net_worth"]["net_cents"] == 450000
        goal = w["goals"][0]
        assert goal["progress"] == 0.5 and goal["months_to_goal"] is not None and goal["monthly_needed_cents"] > 0
        assert w["history"][-1]["net_cents"] == 450000
        proj = auth_client.post(
            "/api/wealth/projection",
            json={
                "initial_cents": 0,
                "monthly_cents": 10000,
                "rate_percent": "0",
                "years": 2,
                "inflation_percent": "0",
            },
        ).json()
        assert proj[-1]["nominal_cents"] == 240000
        assert (
            auth_client.post("/api/goals", json={"name": "X", "target_cents": 1, "account_id": 999}).status_code == 422
        )
        assert auth_client.delete(f"/api/goals/{g.json()['id']}").status_code == 204
        assert auth_client.delete("/api/goals/999").status_code == 404


# =============================================================================== banque DSP2
class TestBanks:
    def test_disabled_without_config(self, auth_client: TestClient) -> None:
        assert auth_client.get("/api/banks").json()["enabled"] is False
        assert auth_client.post("/api/banks/connect", json={"aspsp_name": "X"}).status_code == 503

    def test_connect_callback_sync_reconcile_revoke(self, auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
        bank = FakeBank()
        app.state.enablebanking = make_client(bank)
        # Compte créé avant, par import CSV, avec son IBAN : la connexion doit le retrouver (index aveugle).
        existing = make_account(auth_client, iban=VALID_IBAN)
        upload(
            auth_client,
            existing,
            "x.csv",
            data="Date;Libellé;Montant\n01/10/2026;PRLV FONCIA LOYER OCT;-650,00\n".encode(),
        )

        assert auth_client.get("/api/banks/aspsps").json()[0]["name"] == "Crédit Agricole"
        redirect = auth_client.post("/api/banks/connect", json={"aspsp_name": "Crédit Agricole"}).json()["redirect_url"]
        assert redirect.startswith("https://")
        state = json.loads(next(r for r in bank.calls if r.url.path == "/auth").content)["state"]

        anon = TestClient(app, base_url="https://testserver")  # la banque redirige sans cookie de session
        cb = anon.get(f"/api/banks/callback?code=abc&state={state}", follow_redirects=False)
        assert cb.status_code == 303 and "result=connected" in cb.headers["location"]
        reuse = anon.get(f"/api/banks/callback?code=abc&state={state}", follow_redirects=False)
        assert "result=error" in reuse.headers["location"]  # state à usage unique
        assert (
            "result=refused" in anon.get("/api/banks/callback?error=denied", follow_redirects=False).headers["location"]
        )

        accounts = auth_client.get("/api/accounts").json()
        assert len(accounts) == 1 and accounts[0]["provider"] == "enablebanking"  # fusionné, pas dupliqué
        conn_id = auth_client.get("/api/banks").json()["connections"][0]["id"]
        r = auth_client.post(f"/api/banks/{conn_id}/sync").json()
        # e1 (loyer) existait déjà via CSV -> rapproché ; e2, e3 nouveaux ; e4 en attente ignoré
        assert r["reconciled"] == 1 and r["inserted"] == 2 and not r["errors"]
        acc = auth_client.get("/api/accounts").json()[0]
        assert acc["balance_cents"] == 123456
        stored_session = db.execute(text("select session_id from bank_connections")).scalar()
        assert stored_session.startswith("enc:")
        again = auth_client.post(f"/api/banks/{conn_id}/sync").json()
        assert again["inserted"] == 0  # idempotent

        assert auth_client.delete(f"/api/banks/{conn_id}").status_code == 204
        assert auth_client.get("/api/banks").json()["connections"][0]["status"] == "revoked"
        assert any(r.method == "DELETE" for r in bank.calls)

    def test_expired_state_rejected(self, auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
        bank = FakeBank()
        app.state.enablebanking = make_client(bank)
        auth_client.post("/api/banks/connect", json={"aspsp_name": "Crédit Agricole"})
        state = json.loads(next(r for r in bank.calls if r.url.path == "/auth").content)["state"]
        db.execute(
            text("update bank_connections set state_expires_at = :t"),
            {"t": (utcnow() - timedelta(minutes=1)).replace(tzinfo=None).isoformat(sep=" ")},
        )
        db.commit()
        cb = auth_client.get(f"/api/banks/callback?code=abc&state={state}", follow_redirects=False)
        assert "result=error" in cb.headers["location"]


# =============================================================================== système
class TestSystem:
    def test_security_headers(self, client: TestClient) -> None:
        h = client.get("/api/health").headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
        assert "default-src 'none'" in h["content-security-policy"] and h["cache-control"] == "no-store"
        assert "max-age" in h["strict-transport-security"] and "server" not in h

    def test_untrusted_host_rejected(self, app) -> None:  # type: ignore[no-untyped-def]
        with TestClient(app, base_url="https://evil.example") as c:
            assert c.get("/api/health").status_code == 400

    def test_docs_disabled_outside_dev(self, client: TestClient) -> None:
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404

    def test_body_limit(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/rules", content=b"x" * 300_000, headers={"content-type": "application/json"})
        assert r.status_code == 413

    def test_audit_trail_and_export(self, auth_client: TestClient) -> None:
        aid = make_account(auth_client)
        upload(auth_client, aid, "ca_export.csv")
        log = auth_client.get("/api/audit").json()
        assert log["chain"]["ok"] and {"setup.done", "login.success", "import.file"} <= {
            e["action"] for e in log["entries"]
        }
        assert auth_client.post("/api/export", json={"password": "faux"}).status_code == 403
        exp = auth_client.post("/api/export", json={"password": PASSWORD})
        assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]
        data = exp.json()
        assert data["format"] == "bank-export/v1" and len(data["transactions"]) == 9
        assert data["crypto"]["price_alerts"] == []  # le contenu est vérifié dans test_price_alerts


def test_transactions_belong_to_account(auth_client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    a1 = make_account(auth_client)
    a2 = make_account(auth_client, name="Autre")
    upload(auth_client, a1, "ca_export.csv")
    assert auth_client.get(f"/api/transactions?account_id={a2}").json()["total"] == 0
    assert db.scalar(select(Transaction).where(Transaction.account_id == a1)) is not None
    assert db.get(Account, a2) is not None


@pytest.mark.parametrize("path", ["/api/transactions?limit=0", "/api/transactions?limit=501", "/api/audit?limit=0"])
def test_query_bounds(auth_client: TestClient, path: str) -> None:
    assert auth_client.get(path).status_code == 422


def test_callback_without_state_redirects(client: TestClient) -> None:
    r = client.get("/api/banks/callback", follow_redirects=False)
    assert r.status_code == 303
    assert parse_qs(urlsplit(r.headers["location"]).fragment.split("?", 1)[1])["result"] == ["refused"]
