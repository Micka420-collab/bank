"""Démarrer avec Bank (#44) : parcours de première ouverture et liste « Bien démarrer », calculés côté serveur."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.db import utcnow
from app.models import (
    Account,
    AuditLog,
    BackupRun,
    BankConnection,
    ChannelLink,
    ConnectionStatus,
    CryptoWallet,
    EnergyConnection,
    LlmProvider,
    MailAccount,
    Passkey,
    User,
    VaultDocument,
    WorkPlan,
)
from app.services import consents, start
from app.services.settings_store import get_setting, set_setting
from tests.scenario import seed_household


def overview(client: TestClient) -> dict[str, Any]:
    r = client.get("/api/start")
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def step(data: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in data["journey"]["steps"] if s["key"] == key)


def item(data: dict[str, Any], key: str) -> dict[str, Any]:
    found: dict[str, Any] | None = next((i for i in data["checklist"]["items"] if i["key"] == key), None)
    assert found is not None, key
    return found


def has_item(data: dict[str, Any], key: str) -> bool:
    return any(i["key"] == key for i in data["checklist"]["items"])


def write(app: Any, fn: Callable[[Session], object]) -> None:
    with app.state.sessionmaker() as db:
        fn(db)
        db.commit()


# ------------------------------------------------------------------ première ouverture
def test_a_new_account_starts_on_the_bank_step_with_nothing_done(auth_client: TestClient) -> None:
    data = overview(auth_client)
    journey = data["journey"]
    assert journey["status"] == "new" and journey["current"] == "banque"
    assert [s["key"] for s in journey["steps"]] == list(start.STEP_KEYS)
    assert not any(s["done"] or s["skipped"] for s in journey["steps"])
    assert step(data, "foyer")["href"] == "#/bienvenue/foyer"
    cl = data["checklist"]
    assert cl["visible"] and not cl["collapsed"] and cl["done"] == 0 and cl["total"] == 8
    assert [i["key"] for i in cl["items"] if i["group"] == "essentiel"] == [k for k, *_ in start.ESSENTIALS]
    # Le pilote ne se propose qu'une fois banque, foyer et priorités faits ; Powens tant qu'aucune banque EB.
    assert not has_item(data, "pilote") and has_item(data, "powens")
    # Six accords publics, rien de coché d'avance (la veille est active par défaut, mais pas « décidée »).
    pcs = data["public_consents"]
    assert [p["purpose"] for p in pcs] == ["watch", "baremes", "tempo", "calendar", "home_value", "meteo"]
    assert all(p["sends"] and p["text"] and not p["decided"] for p in pcs)
    assert [p["purpose"] for p in pcs if p["granted"]] == ["watch"]
    assert {c["key"]: c["linked"] for c in data["connectors"]}["banques"] is False


def test_the_state_holds_only_dates_and_step_keys(app: Any, auth_client: TestClient) -> None:
    auth_client.post("/api/start/steps/banque", json={"action": "skip"})
    auth_client.post("/api/start/checklist", json={"action": "dismiss"})
    with app.state.sessionmaker() as db:
        state = get_setting(db, start.KEY)
    assert set(state) <= {
        "started_at",
        "current",
        "skipped",
        "finished_at",
        "tour_done_at",
        "checklist_completed_at",
        "dismissed_at",
    }
    assert state["skipped"] == ["banque"] and state["current"] == "foyer"


# ------------------------------------------------------------------ « fait » : une règle, une table
def _bank(db: Session) -> None:
    db.add(BankConnection(aspsp_name="BNP Paribas", aspsp_country="FR", status=ConnectionStatus.ACTIVE))


def _manual_account(db: Session) -> None:
    db.add(Account(name="Compte courant", balance_cents=64_000))


def _papier(db: Session) -> None:
    db.add(
        VaultDocument(
            kind="tax_notice", title="Avis", filename="avis.pdf", mime="application/pdf", size=1, sha256="a" * 64
        )
    )


def _priorites(db: Session) -> None:
    set_setting(db, "opportunity_priorities", ["reduire"])


def _channel(db: Session) -> None:
    db.add(ChannelLink(platform="telegram", user_index="u", user_ref="1", chat_ref="1", label="Micka"))


def _passkey(db: Session) -> None:
    user = db.scalars(select(User)).one()
    db.add(Passkey(user_id=user.id, credential_id="cred", public_key="pk", name="Téléphone"))


def _totp(db: Session) -> None:
    db.scalars(select(User)).one().totp_enabled = True


def _verified(db: Session) -> None:
    db.add(BackupRun(kind="verify", status="ok"))


def _accords(db: Session) -> None:
    for p in consents.PUBLIC_PURPOSES:
        consents.set_consent(db, p, p != "meteo", actor="micka")  # refuser compte aussi comme « décidé »


@pytest.mark.parametrize(
    ("key", "seed"),
    [
        ("banque", _bank),
        ("banque", _manual_account),
        ("papier", _papier),
        ("priorites", _priorites),
        ("prevenir", _channel),
        ("securite", _passkey),
        ("securite", _totp),
        ("sauvegarde", _verified),
        ("accords", _accords),
        ("planning", lambda db: db.add(WorkPlan())),
        ("assistant", lambda db: db.add(LlmProvider(api_key="sk", key_prefix="sk-or", model="m"))),
        ("linky", lambda db: db.add(EnergyConnection(prm="1", token="t"))),
        ("mail", lambda db: db.add(MailAccount(host="imap.example.org", username="u", password="p"))),
        ("crypto", lambda db: db.add(CryptoWallet(label="BTC", chain="bitcoin", address="bc1", address_index="x"))),
    ],
)
def test_each_done_rule_reads_the_real_tables(
    app: Any, auth_client: TestClient, key: str, seed: Callable[[Session], None]
) -> None:
    assert item(overview(auth_client), key)["done"] is False
    write(app, seed)
    assert item(overview(auth_client), key)["done"] is True


def test_household_needs_the_five_answers(auth_client: TestClient) -> None:
    answers = {"employment": "cdi_prive", "net_monthly_income": 215_000, "household": "seul", "dependents": 0}
    for q, v in answers.items():
        assert auth_client.put(f"/api/onboarding/{q}", json={"value": v}).status_code == 200
    assert step(overview(auth_client), "foyer")["done"] is False
    auth_client.put("/api/onboarding/housing_status", json={"value": "locataire"})
    data = overview(auth_client)
    assert step(data, "foyer")["done"] is True and data["journey"]["current"] == "banque"


def test_a_failed_verification_after_a_good_one_is_not_a_verified_backup(app: Any, auth_client: TestClient) -> None:
    write(app, _verified)
    write(app, lambda db: db.add(BackupRun(kind="verify", status="failed")))
    write(app, lambda db: db.add(BackupRun(kind="backup", status="ok")))
    assert item(overview(auth_client), "sauvegarde")["done"] is False


def test_powens_is_offered_only_without_an_enable_banking_bank(app: Any, auth_client: TestClient) -> None:
    write(app, _bank)
    assert not has_item(overview(auth_client), "powens")


def test_planning_comes_first_when_income_varies(auth_client: TestClient) -> None:
    plus = [i["key"] for i in overview(auth_client)["checklist"]["items"] if i["group"] == "plus"]
    assert plus[0] == "assistant"
    auth_client.put("/api/onboarding/income_variability", json={"value": "beaucoup"})
    plus = [i["key"] for i in overview(auth_client)["checklist"]["items"] if i["group"] == "plus"]
    assert plus[0] == "planning"


# ------------------------------------------------------------------ passer, reprendre, finir
def test_skip_resume_and_finish(auth_client: TestClient) -> None:
    r = auth_client.post("/api/start/steps/banque", json={"action": "current"})
    assert r.status_code == 200 and r.json()["journey"]["status"] == "in_progress"
    r = auth_client.post("/api/start/steps/banque", json={"action": "skip"})
    journey = r.json()["journey"]
    assert journey["current"] == "foyer" and step(r.json(), "banque")["skipped"] is True
    auth_client.post("/api/start/steps/foyer", json={"action": "skip"})
    assert overview(auth_client)["journey"]["current"] == "accords"
    # Reprendre une étape passée : elle n'est plus marquée passée et devient l'étape en cours.
    r = auth_client.post("/api/start/steps/banque", json={"action": "resume"})
    assert r.json()["journey"]["current"] == "banque" and step(r.json(), "banque")["skipped"] is False
    # Aller directement à une étape (lien profond) : elle devient l'étape en cours.
    r = auth_client.post("/api/start/steps/papier", json={"action": "current"})
    assert r.json()["journey"]["current"] == "papier"
    r = auth_client.post("/api/start/finish", json={"tour_done": True})
    data = r.json()
    assert data["journey"]["status"] == "finished" and data["journey"]["current"] is None
    assert step(data, "visite")["done"] is True
    # Inconnu : refusé.
    assert auth_client.post("/api/start/steps/nope", json={"action": "skip"}).status_code == 404
    assert auth_client.post("/api/start/steps/banque", json={"action": "jump"}).status_code == 422


def test_skipping_a_done_step_has_no_effect(app: Any, auth_client: TestClient) -> None:
    write(app, _papier)
    data = auth_client.post("/api/start/steps/papier", json={"action": "skip"}).json()
    assert step(data, "papier") == {**step(data, "papier"), "done": True, "skipped": False}


def test_finishing_without_the_tour_leaves_the_tour_undone(auth_client: TestClient) -> None:
    data = auth_client.post("/api/start/finish").json()
    assert data["journey"]["status"] == "finished" and step(data, "visite")["done"] is False


def test_an_existing_account_is_never_forced_through_the_journey(app: Any, auth_client: TestClient) -> None:
    write(app, seed_household)
    data = overview(auth_client)
    assert data["journey"]["status"] == "finished"
    assert data["checklist"]["visible"] is True and item(data, "banque")["done"] is True
    # Et c'est noté : même si les opérations disparaissaient, l'accueil ne s'imposerait pas.
    with app.state.sessionmaker() as db:
        assert get_setting(db, start.KEY)["finished_at"]


def test_state_changes_require_csrf(auth_client: TestClient) -> None:
    token = auth_client.headers.pop("x-csrf-token")
    try:
        assert auth_client.post("/api/start/finish").status_code == 403
        assert auth_client.post("/api/start/public-consents", json={"purposes": ["baremes"]}).status_code == 403
    finally:
        auth_client.headers["x-csrf-token"] = token


def test_anonymous_requests_are_refused(client: TestClient) -> None:
    assert client.get("/api/start").status_code == 401


# ------------------------------------------------------------------ « Tout activer pour les données publiques »
def test_public_consents_grant_exactly_the_six_and_are_audited(app: Any, auth_client: TestClient) -> None:
    r = auth_client.post("/api/start/public-consents", json={"purposes": list(consents.PUBLIC_PURPOSES)})
    assert r.status_code == 200
    assert all(p["granted"] and p["decided"] for p in r.json())
    listing = {c["purpose"]: c for c in auth_client.get("/api/consents").json()}
    assert {p for p, c in listing.items() if c["granted"]} == set(consents.PUBLIC_PURPOSES)
    with app.state.sessionmaker() as db:
        logged = db.scalars(select(AuditLog).where(AuditLog.action == "consent.grant")).all()
    assert len(logged) == 6
    assert step(overview(auth_client), "accords")["done"] is True


@pytest.mark.parametrize("purposes", [["baremes", "ai_memory"], ["geocoding"], ["push"], [], ["inconnu"]])
def test_public_consents_refuse_anything_else_and_grant_nothing(auth_client: TestClient, purposes: list[str]) -> None:
    r = auth_client.post("/api/start/public-consents", json={"purposes": purposes})
    assert r.status_code == 422
    listing = {c["purpose"]: c for c in auth_client.get("/api/consents").json()}
    assert not any(c["explicit"] for c in listing.values())


def test_consent_listing_says_what_each_one_sends(auth_client: TestClient) -> None:
    listing = auth_client.get("/api/consents").json()
    assert {c["purpose"] for c in listing} == set(consents.PURPOSES) == set(consents.SENDS)
    assert all(c["group"] in consents.GROUPS and c["sends"].endswith(".") for c in listing)
    groups = {c["purpose"]: c["group"] for c in listing}
    assert groups["geocoding"] == "adresse" and groups["ai_memory"] == "contenus" and groups["meteo"] == "zone"


# ------------------------------------------------------------------ cycle de vie de la liste
def _all_essentials(db: Session) -> None:
    for seed in (_bank, _papier, _priorites, _channel, _passkey, _verified, _accords):
        seed(db)
    from app.services import advisor

    for q, v in (
        ("employment", "cdi_prive"),
        ("net_monthly_income", 215_000),
        ("household", "seul"),
        ("dependents", 0),
        ("housing_status", "locataire"),
    ):
        advisor.set_answer(db, q, v, source="user:micka", unit="cents")


def test_eight_of_eight_completes_once_folds_for_a_week_then_disappears(app: Any, auth_client: TestClient) -> None:
    write(app, _all_essentials)
    data = overview(auth_client)
    cl = data["checklist"]
    assert cl["done"] == 8 and cl["completed_at"] and cl["just_completed"] is True
    assert cl["visible"] is True and cl["collapsed"] is True
    again = overview(auth_client)["checklist"]
    assert again["just_completed"] is False and again["completed_at"] == cl["completed_at"]
    with app.state.sessionmaker() as db:
        later = start.view(db, utcnow() + timedelta(days=start.COLLAPSE_DAYS, minutes=1))
    assert later["checklist"]["visible"] is False
    # Un élément qui redevient faux ne rouvre pas la liste terminée.
    write(app, lambda db: db.add(BackupRun(kind="verify", status="failed")))
    assert overview(auth_client)["checklist"]["completed_at"] == cl["completed_at"]


def test_dismissed_checklist_stays_hidden_and_can_be_restored(auth_client: TestClient) -> None:
    r = auth_client.post("/api/start/checklist", json={"action": "dismiss"})
    assert r.status_code == 200 and r.json()["checklist"]["visible"] is False
    assert overview(auth_client)["checklist"]["visible"] is False
    assert auth_client.post("/api/start/checklist", json={"action": "hide"}).status_code == 422
    r = auth_client.post("/api/start/checklist", json={"action": "restore"})
    assert r.json()["checklist"]["visible"] is True


def test_setup_initiatives_are_quiet_while_the_checklist_shows(app: Any, auth_client: TestClient) -> None:
    keys = [c["key"] for c in auth_client.get("/api/initiatives?limit=20").json()["cards"]]
    assert "start:data" not in keys and "security:totp" not in keys
    auth_client.post("/api/start/checklist", json={"action": "dismiss"})
    keys = [c["key"] for c in auth_client.get("/api/initiatives?limit=20").json()["cards"]]
    assert "start:data" in keys and "security:totp" in keys
