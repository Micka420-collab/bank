"""Alertes et dossier : ce que l'agent doit remarquer sans qu'on le lui demande."""

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.recurring import add_months
from app.importers.base import ParsedTransaction
from app.models import Account, BankConnection, ConnectionStatus, FiscalSimulation
from app.services import advisor
from app.services.ingest import ingest
from app.services.proposals import create_proposal
from tests.scenario import TODAY, seed_household


def codes(db: Session, today=TODAY) -> set[str]:  # type: ignore[no-untyped-def]
    return {a["code"] for a in advisor.alerts(db, today)}


def test_overdraft_alert_beats_tight_budget(db: Session) -> None:
    seed_household(db, balance_cents=5_000)
    found = advisor.alerts(db, TODAY)
    first = found[0]
    assert first["code"] == "decouvert" and first["severity"] == "critique" and "Découvert prévu" in first["title"]
    assert "budget_serre" not in {a["code"] for a in found}


def test_rights_gap_lep_consent_proposals_and_backlog(db: Session) -> None:
    courant = seed_household(db)
    db.add(
        FiscalSimulation(
            period="2026-10",
            result=json.dumps(
                {
                    "period": "2026-10",
                    "estimates": [{"variable": "aide_logement", "monthly_cents": 12_000}],
                    "css": None,
                    "lep_eligible": True,
                }
            ),
        )
    )
    db.add(
        BankConnection(
            aspsp_name="Crédit Agricole",
            aspsp_country="FR",
            status=ConnectionStatus.ACTIVE,
            valid_until=utcnow() + timedelta(days=3),
        )
    )
    create_proposal(db, agent="hermes", kind="set_buffer", params={"amount_eur": 100}, rationale="r")
    ingest(
        db,
        courant,
        [ParsedTransaction(TODAY - timedelta(days=i % 20), -500 - i, f"CB BOUTIQUE INCONNUE {i}") for i in range(12)],
        provider="file",
    )
    db.commit()
    found = codes(db)
    assert {"droit:caf_logement", "lep", "propositions", "a_categoriser"} <= found
    assert any(c.startswith("consentement:") for c in found)
    # Un LEP déjà ouvert éteint l'opportunité.
    db.add(Account(name="LEP", kind="lep", provider="manual", balance_cents=100, include_in_budget=False))
    db.commit()
    assert "lep" not in codes(db)


def test_late_aid_and_stale_data(db: Session) -> None:
    seed_household(db)
    later = add_months(TODAY, 1).replace(day=20)  # la CAF du 5 n'est pas arrivée
    found = advisor.alerts(db, later)
    assert any(a["code"].startswith("aide:") for a in found)
    assert "donnees_anciennes" in {a["code"] for a in found}


def test_empty_base_still_produces_a_dossier(db: Session) -> None:
    d = advisor.build_dossier(db, TODAY)
    assert d["onboarding"]["completeness_percent"] == 0 and d["this_month"]["balance_cents"] == 0
    assert "donnees_anciennes" in {a["code"] for a in d["alerts"]}
    md = advisor.render_markdown(d)
    assert "- Rien encore" in md and "- Aucun" in md


def test_dossier_reflects_declared_profile(db: Session) -> None:
    seed_household(db)
    advisor.set_answer(db, "employment", "independant", source="user:micka")
    advisor.set_answer(db, "income_variability", "beaucoup", source="user:micka")
    advisor.set_answer(db, "notes", "Je change de travail en janvier", source="user:micka")
    advisor.set_answer(
        db,
        "outside_assets",
        [{"label": "PEA", "kind": "pea", "value_cents": 250_000, "monthly_contribution_cents": 5_000}],
        source="user:micka",
    )
    advisor.set_answer(
        db,
        "planned_expenses",
        [{"label": "Mariage", "amount_cents": 300_000, "date": "2027-06-15"}],
        source="user:micka",
    )
    advisor.set_answer(db, "red_lines", [{"rule": "Pas de crypto"}], source="user:micka")
    advisor.set_answer(db, "risk_horizon", "plus_10", source="user:micka")
    advisor.set_answer(db, "risk_drop", "attends", source="user:micka")
    db.commit()

    d = advisor.build_dossier(db, TODAY)
    assert d["profile"]["employment"]["source"] == "declared"
    # Les notes sont écrites pour l'agent : lues en mode anonymisé, jamais en mode agrégats.
    assert d["profile"]["notes"]["value"].startswith("Je change")
    assert d["profile"]["employment"]["recorded_by"] == "user"  # l'identifiant de connexion ne sort pas
    agg = advisor.build_dossier(db, TODAY, "aggregates")
    assert "notes" not in agg["profile"]
    assert advisor.build_dossier(db, TODAY, "full")["profile"]["employment"]["recorded_by"] == "user:micka"
    assert all(c["label"] != "COFIDIS ECHEANCE PRET" for c in agg["commitments"])
    assert all(a["reason"] in (None, "prochaine échéance") for a in agg["this_month"]["allowance"])
    assert all(s["evidence"] == "Déduit de tes relevés" for s in agg["detected_not_confirmed"])
    assert d["wealth"]["outside_assets_cents"] == 250_000
    assert d["wealth"]["net_worth_cents"] == d["wealth"]["tracked_net_worth_cents"] + 250_000
    # Indépendant + revenu très variable : matelas recommandé de 8 mois.
    assert d["wealth"]["emergency_fund"]["target_months"] == 8
    assert d["wealth"]["emergency_fund"]["target_source"] == "computed"
    [mariage] = d["goals"]["planned_expenses"]
    assert mariage["monthly_provision_cents"] == 37_500  # 3 000 € sur 8 mois
    assert d["risk_profile"]["label"] == "dynamique" and d["risk_profile"]["capacity_limited"]
    assert d["mandate"]["red_lines"] == [{"rule": "Pas de crypto"}]
    md = advisor.render_markdown(d)
    assert "Ligne rouge : Pas de crypto" in md and "Mariage" in md and "Indépendant" in md


def test_mandate_cannot_be_written_by_an_agent_even_internally(db: Session) -> None:
    import pytest

    from app.domain.onboarding import AnswerError

    with pytest.raises(AnswerError, match="mandat"):
        advisor.set_answer(db, "autonomy", "gestionnaire", source="agent:hermes", unit="euros")
    advisor.set_answer(db, "autonomy", "conseiller", source="user:micka")
    assert advisor.plain_answers(db)["autonomy"] == "conseiller"
    advisor.clear_answer(db, "autonomy", source="user:micka")
    advisor.clear_answer(db, "autonomy", source="user:micka")  # idempotent
    assert "autonomy" not in advisor.plain_answers(db)
    assert [h["value"] for h in advisor.history(db, "autonomy")] == [None, "conseiller"]


def test_person_merchants_are_masked_in_commitments(db: Session) -> None:
    courant = seed_household(db)
    rent = [
        ParsedTransaction(add_months(TODAY.replace(day=1), -i, 2), -50_000, "VIR PERMANENT A MME SOPHIE BERNARD LOYER")
        for i in range(4, -1, -1)
    ]
    ingest(db, courant, rent, provider="file")
    db.commit()
    text = json.dumps(advisor.build_dossier(db, TODAY), default=str)
    assert "BERNARD" not in text and "SOPHIE" not in text
    assert "BERNARD" in json.dumps(advisor.build_dossier(db, TODAY, "full"), default=str)
    assert db.scalar(select(Account).where(Account.name == "Compte courant")) is not None


def test_income_from_a_person_never_leaks_in_redacted_dossier(db: Session) -> None:
    """Une pension versée par un particulier contraint le reste à dépenser : son nom ne doit sortir nulle part."""
    courant = seed_household(db, balance_cents=20_000)
    pension = [
        ParsedTransaction(add_months(TODAY.replace(day=1), -i, 24), 30_000, "VIR SEPA DE MME CLAIRE MARTIN PENSION")
        for i in range(4, 0, -1)
    ]
    ingest(db, courant, pension, provider="file")
    db.commit()
    d = advisor.build_dossier(db, TODAY)
    blob = json.dumps(d, default=str) + advisor.render_markdown(d)
    assert "MARTIN" not in blob and "CLAIRE" not in blob
    assert "MARTIN" in json.dumps(advisor.build_dossier(db, TODAY, "full"), default=str)
