"""Centre d'opportunités (scénario E et suivants) : chaque piste est tirée de tes données, classée par gain net
attendu, expliquée (constaté, estimé, supposé, sources), et ne devient « obtenue » que sur un gain constaté que tu
confirmes — de la détection des frais au-delà des plafonds jusqu'au remboursement reçu."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import opportunities as do
from app.importers.base import ParsedTransaction
from app.models import (
    Account,
    FiscalSimulation,
    InitiativeState,
    OpportunityState,
    Procedure,
    ValueEvent,
    VaultDocument,
)
from app.services import opportunities as so
from app.services.ingest import ingest
from tests.scenario import TODAY


# ============================================================================ outils
def _add(app, rows: list[tuple[date, int, str]]) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        account = db.scalars(select(Account).where(Account.kind == "checking")).one()
        ingest(db, account, [ParsedTransaction(d, c, label) for d, c, label in rows], provider="file")
        db.commit()


def _simulate(app, **result: Any) -> None:  # type: ignore[no-untyped-def]
    base: dict[str, Any] = {
        "period": "2026-10",
        "engine_version": "test",
        "estimates": [],
        "css": "non_eligible",
        "css_participation_cents": 0,
        "lep_eligible": False,
    }
    with app.state.sessionmaker() as db:
        db.add(FiscalSimulation(period="2026-10", result=json.dumps({**base, **result})))
        db.commit()


def _center(world: TestClient) -> dict[str, Any]:
    r = world.get("/api/opportunities")
    assert r.status_code == 200, r.text
    return dict(r.json())


def _by_key(center: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["key"]: o for o in center["opportunities"]}


def _plain(text: str) -> str:
    return " ".join(text.replace(" ", " ").replace("\xa0", " ").split())


def _approve(world: TestClient, proc: dict[str, Any]) -> dict[str, Any]:
    keys = [d["key"] for d in proc["template"]["declarations"]]
    r = world.post(f"/api/procedures/{proc['id']}/approve", json={"hash": proc["hash"], "declarations": keys})
    assert r.status_code == 200, r.text
    return dict(r.json())


OCTOBER_FEES = [(date(2026, 10, d), -890, "COMMISSION D INTERVENTION") for d in range(1, 12)]


# ============================================================================ calcul pur
def test_fee_review_applies_the_regulatory_caps() -> None:
    fees = [do.Fee(date(2026, 10, d), "COMMISSION D INTERVENTION", 890) for d in range(1, 12)]
    review = do.review_fees("2026-10", [*fees, do.Fee(date(2026, 10, 20), "FRAIS REJET PRLV", 2_500)])
    # 11 × 0,90 € au-delà de 8 € par opération, 88 € plafonnés à 80 € sur le mois, 5 € de rejet au-delà de 20 €
    assert review.excess_cents == 990 + 800 + 500
    assert review.incident_total_cents == 11 * 890 + 2_500
    assert len(review.reasons) == 3
    fragile = do.review_fees("2026-10", fees[:3], fragile=True)
    assert fragile.excess_cents == 3 * 490  # 4 € par opération pour un client fragile
    calm = do.review_fees("2026-10", [do.Fee(date(2026, 10, 2), "COTISATION CARTE VISA", 4_500)])
    assert calm.incident_total_cents == 0 and calm.excess_cents == 0  # une cotisation ne se conteste pas
    assert [do.fee_kind(x) for x in ("COM INTERV 12/10", "AGIOS T3", "PRLV IMPAYE", "OFFRE GROUPEE", "DIVERS")] == [
        "intervention",
        "agios",
        "rejet",
        "cotisation",
        "autre",
    ]
    detail = do.fees_detail(review.fees[:1])
    assert detail == "01/10/2026 — COMMISSION D INTERVENTION — 8,90 €"


def _opp(key: str, amount: int, period: str = "once", probability: float = 1.0, **kw: Any) -> do.Opportunity:
    return do.Opportunity(key, "recuperer", key, "", amount, period, probability, 0, "moyenne", **kw)


def test_ranking_puts_deadlines_first_then_expected_net_gain() -> None:
    today = date(2026, 10, 21)
    big = _opp("big", 10_000, "month", 0.5)  # 1 200 € par an × 50 %
    small = _opp("small", 5_000)
    due = _opp("due", 0, deadline=today + timedelta(days=10))
    later = _opp("later", 0, deadline=today + timedelta(days=40))
    assert [o.key for o in do.rank([small, later, big, due], today)] == ["due", "big", "small", "later"]
    assert do.expected_net(big) == 60_000
    slow = do.Opportunity("slow", "reduire", "", "", 1_000, "once", 1.0, 120, "faible")
    assert do.expected_net(slow) == 1_000 - 3_000  # deux heures à 15 € : l'effort coûte plus que le gain
    assert do.expected_net(slow, time_value_cents_per_hour=0) == 1_000
    assert do.yearly(1_349, "month") == 16_188 and do.yearly(500, "year") == 500


@given(
    amounts=st.lists(st.integers(min_value=0, max_value=50_000), min_size=0, max_size=40),
    fragile=st.booleans(),
)
def test_fee_review_never_claims_more_than_was_charged(amounts: list[int], fragile: bool) -> None:
    labels = ("COMMISSION D INTERVENTION", "FRAIS REJET PRLV", "AGIOS", "COTISATION CARTE")
    fees = [do.Fee(date(2026, 10, 1 + i % 28), labels[i % 4], a) for i, a in enumerate(amounts)]
    review = do.review_fees("2026-10", fees, fragile=fragile)
    assert 0 <= review.excess_cents <= review.incident_total_cents <= sum(amounts)
    assert (review.excess_cents > 0) == bool(review.reasons)


@given(
    amount=st.integers(min_value=0, max_value=10_000_000),
    p1=st.floats(min_value=0, max_value=1),
    p2=st.floats(min_value=0, max_value=1),
    effort=st.integers(min_value=0, max_value=600),
)
def test_expected_gain_grows_with_probability(amount: int, p1: float, p2: float, effort: int) -> None:
    low, high = sorted((p1, p2))
    a = do.Opportunity("a", "epargne", "", "", amount, "year", low, effort, "moyenne")
    b = do.Opportunity("b", "epargne", "", "", amount, "year", high, effort, "moyenne")
    assert do.expected_net(a) <= do.expected_net(b)


# ============================================================================ détection
def test_a_fresh_household_gets_explained_and_sourced_leads(world: TestClient) -> None:
    center = _center(world)
    opps = _by_key(center)
    assert "aid:simulate" in opps  # profil prêt, pas encore de simulation
    sub = next(o for k, o in opps.items() if k.startswith("sub:"))
    assert "Netflix" in sub["title"] and sub["amount_cents"] == 1_349 and sub["period"] == "month"
    assert sub["action"]["template"] == "resilier_abonnement"
    assert sub["facts"] and sub["hypotheses"] and sub["sources"][0]["url"].startswith("https://www.inc-conso.fr/")
    assert sub["expected_net_cents"] == round(16_188 * 0.25) - round(10 * 1_500 / 60)
    assert not any(k.startswith("fees:") for k in opps)  # aucun frais : rien à contester
    assert "conseiller financier agréé" in center["disclaimer"]
    assert center["value"]["confirmed_yearly_cents"] == 0 and center["confirmations"] == []
    for o in center["opportunities"]:
        assert o["category"] in do.CATEGORIES and o["confidence"] in ("élevée", "moyenne", "faible")


def test_simulation_feeds_aids_health_and_savings(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _add(app, [(date(2026, m, 8), -4_500, "PRLV SEPA HARMONIE MUTUELLE") for m in (7, 8, 9, 10)])
    _simulate(
        app,
        estimates=[
            {"variable": "ppa", "label": "Prime d'activité", "monthly_cents": 25_829},
            {"variable": "aide_logement", "label": "Aide au logement", "monthly_cents": 18_000},
        ],
        css="gratuite",
        lep_eligible=True,
    )
    opps = _by_key(_center(world))
    assert "aid:simulate" not in opps and "aid:caf_ppa" not in opps  # perçue comme estimée : pas d'écart
    housing = opps["aid:caf_logement"]
    assert housing["amount_cents"] == 18_000 and housing["action"] == {"link": "#/aides", "label": "Voir mes aides"}
    assert any("Aucun versement" in f for f in housing["facts"])
    css = opps["aid:css"]
    assert css["amount_cents"] == 4_500 and css["action"]["template"] == "demander_css"
    assert "sans rien payer" in css["title"] and css["sources"][0]["url"].startswith("https://www.ameli.fr/")
    lep = opps["lep:open"]
    assert lep["amount_cents"] == 3_840  # 4 800 € du livret A × (2,5 % − 1,7 %)
    assert lep["action"] == {"template": "ouvrir_lep"} and lep["confidence"] == "élevée"


def test_aid_already_received_points_to_the_declaration_not_a_new_claim(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _simulate(app, estimates=[{"variable": "ppa", "label": "Prime d'activité", "monthly_cents": 31_000}])
    ppa = _by_key(_center(world))["aid:caf_ppa"]
    assert ppa["amount_cents"] == 31_000 - 25_829 and "déclaration" in ppa["summary"]
    assert "template" not in ppa["action"]  # déjà allocataire : pas de nouvelle demande
    _simulate(app, estimates=[])  # la nouvelle simulation ne prévoit plus rien : risque de trop-perçu
    risk = _by_key(_center(world))["aid-risk:caf_ppa"]
    assert risk["category"] == "anticiper" and "trop-perçu" in risk["summary"]


def test_subscription_answers_are_respected(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    sub = next(o for o in _center(world)["opportunities"] if o["key"].startswith("sub:"))
    series_key = sub["key"][len("sub:") :]
    with app.state.sessionmaker() as db:
        db.add(InitiativeState(key=f"series:{series_key}", status="done", note="inconnu"))
        db.commit()
    unknown = _by_key(_center(world))[sub["key"]]
    assert "ne reconnais pas" in unknown["title"] and unknown["probability"] == 0.7
    with app.state.sessionmaker() as db:
        db.get(InitiativeState, f"series:{series_key}").note = "voulu"  # type: ignore[union-attr]
        db.commit()
    assert sub["key"] not in _by_key(_center(world))  # tu as dit le garder : Bank ne redemande pas


def test_overdraft_expiring_documents_and_bank_offer(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        db.scalars(select(Account).where(Account.kind == "checking")).one().balance_cents = 2_000
        db.add(
            VaultDocument(
                kind="identity",
                title="Carte d'identité",
                filename="cni.pdf",
                mime="application/pdf",
                size=1,
                sha256="0" * 64,
                status="verified",
                expires_on=TODAY + timedelta(days=20),
            )
        )
        db.commit()
    _add(app, [(date(2026, m, 4), -2_400, "COTISATION OFFRE GROUPEE") for m in (7, 8, 9)])
    center = _center(world)
    keys = [o["key"] for o in center["opportunities"]]
    assert keys[0] == "overdraft:2026-10" or keys[1] == "overdraft:2026-10"  # urgences d'abord
    assert any(k.startswith("doc:") for k in keys[:2])
    doc = next(o for o in center["opportunities"] if o["key"].startswith("doc:"))
    assert doc["deadline"] == (TODAY + timedelta(days=20)).isoformat()
    offer = _by_key(center)["bank-offer:2026"]
    assert "72" in _plain(offer["title"]) and offer["action"]["link"] == "https://www.tarifs-bancaires.gouv.fr/"


def test_idle_cash_is_measured_after_the_month_and_the_buffer(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        db.scalars(select(Account).where(Account.kind == "checking")).one().balance_cents = 900_000
        db.commit()
    idle = _by_key(_center(world))["idle-cash"]
    assert idle["action"]["link"] == "#/patrimoine" and idle["period"] == "year"
    assert 0 < idle["amount_cents"] < 900_000 * 17 // 1000  # moins que 1,7 % du solde : une partie reste
    assert any("matelas" in e for e in idle["estimates"])


# ============================================================================ scénario E : frais → démarche → gain
def test_fees_over_the_caps_become_a_complaint_then_a_confirmed_refund(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _add(app, OCTOBER_FEES)
    center = _center(world)
    fees = _by_key(center)["fees:2026-10"]
    assert fees["amount_cents"] == 990 + 800 and fees["confidence"] == "élevée"
    assert len(fees["facts"]) == 11 and any("R. 312-4-1" in s["title"] for s in fees["sources"])
    nets = [o["expected_net_cents"] for o in center["opportunities"] if not o["deadline"]]
    assert nets == sorted(nets, reverse=True)  # classées par gain net attendu
    assert fees["expected_net_cents"] == round(1_790 * 0.8) - round(15 * 1_500 / 60)
    # Lancer : la démarche est préremplie avec la provenance « opportunité »
    r = world.post("/api/opportunities/start", json={"key": "fees:2026-10"})
    assert r.status_code == 201, r.text
    proc_id = r.json()["procedure_id"]
    again = world.post("/api/opportunities/start", json={"key": "fees:2026-10"}).json()["procedure_id"]
    assert again == proc_id  # une opportunité = une démarche
    proc = world.get(f"/api/procedures/{proc_id}").json()
    fields = {f["key"]: f for f in proc["fields"]}
    assert fields["montant"]["value"] == 1_790 and fields["motif"]["value"] == "plafond"
    assert fields["periode"]["value"] == "octobre 2026" and "8,90 €" in fields["detail"]["value"]
    started = _by_key(_center(world))["fees:2026-10"]
    assert started["status"] == "started" and started["procedure"]["id"] == proc_id
    value = _center(world)["value"]
    assert value["estimated"]["once"] == round(1_790 * 0.8) and value["confirmed_yearly_cents"] == 0
    assert (
        world.post("/api/opportunities/decide", json={"key": "fees:2026-10", "decision": "dismissed"}).status_code
        == 409
    )
    # Toi : nom, relecture, validation, envoi déclaré
    proc = world.patch(f"/api/procedures/{proc_id}/fields", json={"values": {"nom": "Micka Exemple"}}).json()
    proc = _approve(world, proc)
    assert world.post(f"/api/procedures/{proc_id}/sent", json={}).json()["stage"] == "waiting"
    # La banque rembourse ; un remboursement de soins le même jour ne doit pas être pris pour lui
    _add(
        app,
        [
            (TODAY + timedelta(days=12), 2_500, "VIR CPAM REMB SOINS"),
            (TODAY + timedelta(days=15), 1_790, "REMBOURSEMENT FRAIS COMMISSION"),
        ],
    )
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=16)
    confirmations = _center(world)["confirmations"]
    assert len(confirmations) == 1
    seen = confirmations[0]
    assert seen["amount_cents"] == 1_790 and "REMBOURSEMENT FRAIS" in seen["label"] and "CPAM" not in seen["label"]
    r = world.post(
        "/api/opportunities/confirm",
        json={
            "key": seen["key"],
            "amount_cents": seen["amount_cents"],
            "period": seen["period"],
            "label": seen["label"],
            "procedure_id": seen["procedure_id"],
        },
    )
    assert r.status_code == 204, r.text
    closed = world.get(f"/api/procedures/{proc_id}").json()
    assert closed["stage"] == "done"
    center = _center(world)
    assert "fees:2026-10" not in _by_key(center) and center["hidden"] >= 1
    assert center["value"]["confirmed"]["once"] == 1_790 and center["value"]["estimated_yearly_cents"] == 0
    assert center["value"]["history"][0]["evidence"].startswith("Constaté par Bank")
    assert center["confirmations"] == []
    with app.state.sessionmaker() as db:  # un gain confirmé une seule fois, même si la démarche est close « obtenu »
        events = db.scalars(select(ValueEvent).where(ValueEvent.kind == "confirmed")).all()
        assert len(events) == 1
        state = db.get(OpportunityState, "fees:2026-10")
        assert state is not None and state.status == "done"
    again = world.post(
        "/api/opportunities/confirm",
        json={"key": seen["key"], "amount_cents": 1_790, "period": "once", "procedure_id": proc_id},
    )
    assert again.status_code == 409


def test_a_cancelled_subscription_is_confirmed_once_debits_stop(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    sub = next(o for o in _center(world)["opportunities"] if o["key"].startswith("sub:"))
    proc_id = world.post("/api/opportunities/start", json={"key": sub["key"]}).json()["procedure_id"]
    proc = world.patch(f"/api/procedures/{proc_id}/fields", json={"values": {"nom": "Micka Exemple"}}).json()
    assert {f["key"]: f["value"] for f in proc["fields"]}["montant_mensuel"] == 1_349
    proc = _approve(world, proc)
    world.post(f"/api/procedures/{proc_id}/sent", json={})
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=20)
    assert _center(world)["confirmations"] == []  # trop tôt pour conclure
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=40)
    seen = _center(world)["confirmations"]
    assert len(seen) == 1 and seen[0]["period"] == "month" and seen[0]["amount_cents"] == 1_349
    assert "Plus aucun prélèvement" in seen[0]["label"]
    # Un nouveau prélèvement après la date d'effet : Bank ne propose plus de confirmer
    _add(app, [(TODAY + timedelta(days=25), -1_349, "PRLV SEPA NETFLIX.COM")])
    assert _center(world)["confirmations"] == []


def test_estimates_leave_the_total_when_a_procedure_fails(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _add(app, OCTOBER_FEES)
    proc_id = world.post("/api/opportunities/start", json={"key": "fees:2026-10"}).json()["procedure_id"]
    assert _center(world)["value"]["estimated_yearly_cents"] == 1_432
    world.patch(f"/api/procedures/{proc_id}/fields", json={"values": {"nom": "Micka Exemple"}})
    proc = _approve(world, world.get(f"/api/procedures/{proc_id}").json())
    world.post(f"/api/procedures/{proc['id']}/sent", json={})
    r = world.post(f"/api/procedures/{proc_id}/close", json={"outcome": "refuse"})
    assert r.status_code == 200, r.text
    value = _center(world)["value"]
    assert value["estimated_yearly_cents"] == 0 and value["confirmed_yearly_cents"] == 0 and value["pending"] == []


def test_css_procedure_is_prefilled_from_the_household(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    _add(app, [(date(2026, m, 8), -4_500, "PRLV SEPA HARMONIE MUTUELLE") for m in (7, 8, 9, 10)])
    _simulate(app, css="participation", css_participation_cents=1_000)
    css = _by_key(_center(world))["aid:css"]
    assert css["amount_cents"] == 3_500 and "sans rien payer" not in css["title"]
    proc_id = world.post("/api/opportunities/start", json={"key": "aid:css"}).json()["procedure_id"]
    proc = world.get(f"/api/procedures/{proc_id}").json()
    fields = {f["key"]: f for f in proc["fields"]}
    assert fields["situation"]["value"] == "seul" and fields["personnes"]["value"] == 1
    assert fields["allocataire"]["value"] == "oui"  # des versements de la Caf arrivent sur le compte
    assert fields["mutuelle_actuelle"]["value"] == 4_500
    assert proc["template"]["official_url"].startswith("https://www.ameli.fr/")
    assert any(p["key"] == "avis" and p["required"] for p in proc["pieces"])


# ============================================================================ décisions et réglages
def test_dismiss_snooze_and_restore(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    key = next(o["key"] for o in _center(world)["opportunities"] if o["key"].startswith("sub:"))
    assert world.post("/api/opportunities/decide", json={"key": key, "decision": "dismissed"}).status_code == 204
    center = _center(world)
    assert key not in _by_key(center) and center["hidden"] == 1
    assert world.post("/api/opportunities/decide", json={"key": key, "decision": "new"}).status_code == 204
    assert key in _by_key(_center(world))
    world.post("/api/opportunities/decide", json={"key": key, "decision": "snoozed", "days": 10})
    assert key not in _by_key(_center(world))
    app.dependency_overrides[deps.today] = lambda: TODAY + timedelta(days=10)
    assert key in _by_key(_center(world))  # le délai est passé : la piste revient
    assert world.post("/api/opportunities/decide", json={"key": key, "decision": "maybe"}).status_code == 422


def test_start_guards_and_time_value(world: TestClient) -> None:
    assert world.post("/api/opportunities/start", json={"key": "aid:simulate"}).status_code == 409  # un lien
    assert world.post("/api/opportunities/start", json={"key": "fees:1999-01"}).status_code == 404
    sub = next(o for o in _center(world)["opportunities"] if o["key"].startswith("sub:"))
    assert world.put("/api/opportunities/settings", json={"time_value_cents_per_hour": 0}).status_code == 204
    again = _by_key(_center(world))[sub["key"]]
    assert again["expected_net_cents"] == round(16_188 * 0.25)  # l'effort ne coûte plus rien
    assert world.put("/api/opportunities/settings", json={"time_value_cents_per_hour": 90_000}).status_code == 422
    top = world.get("/api/opportunities/top").json()
    assert 1 <= len(top["opportunities"]) <= 3 and "value" in top
    bad = world.post("/api/opportunities/confirm", json={"key": "x", "amount_cents": 100, "period": "week"})
    assert bad.status_code == 422


def test_confirm_checks_the_procedure_matches(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    proc = world.post("/api/procedures", json={"template": "libre", "title": "Remboursement"}).json()
    r = world.post(
        "/api/opportunities/confirm",
        json={"key": "fees:2026-09", "amount_cents": 500, "period": "once", "procedure_id": proc["id"]},
    )
    assert r.status_code == 409
    ok = world.post(
        "/api/opportunities/confirm",
        json={"key": f"procedure:{proc['id']}", "amount_cents": 500, "period": "once", "label": "  Geste  "},
    )
    assert ok.status_code == 204
    with app.state.sessionmaker() as db:
        event = db.scalars(select(ValueEvent)).one()
        assert event.label == "Geste" and event.kind == "confirmed"
        assert db.get(Procedure, proc["id"]).stage != "done"  # type: ignore[union-attr]  # pas lié : rien de clos


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("REMBOURSEMENT FRAIS COMMISSION", True),
        ("RETROCESSION COMMISSIONS INTERVENTION", True),
        ("VIR REGUL AGIOS T3", True),
        ("FRAIS REMBOURSES", True),
        ("GESTE COMMERCIAL", True),
        ("VIR CPAM REMB SOINS", False),
        ("REMB MUTUELLE COMPLEMENTAIRE SANTE", False),
        ("REMBOURSEMENT PRET", False),
        ("VIR SEPA RECU SALAIRE", False),
    ],
)
def test_fee_refund_recognition(label: str, expected: bool) -> None:
    assert bool(so._FEE_REFUND.search(label)) is expected
