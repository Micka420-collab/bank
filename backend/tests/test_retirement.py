"""Retraite : règles datées (générations, taux, décote, surcote, Agirc-Arrco, PER) vérifiées sur le cas de référence de
service-public (35 000 € de salaire annuel moyen, 169 trimestres → 1 458,33 € par mois), puis l'écran et l'outil MCP
sur le foyer type : ce que Bank sait (naissance du profil, salaire des relevés ou bulletin du coffre), ce que tu saisis
(relevé de carrière, chiffré), des fourchettes, jamais un droit inventé, rien n'est envoyé à une caisse."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.domain import retirement as dom
from app.models import HouseholdProfile, VaultDocument, VaultField
from app.services import retirement as svc
from tests.conftest import do_login, do_setup
from tests.scenario import TODAY, seed_household

BIRTH_1962 = date(1962, 6, 15)  # génération 1962 : 62 ans et 6 mois, 169 trimestres
LEGAL_1962 = date(2024, 12, 15)
SAM_35K = 3_500_000


def _career(quarters: int, *, working: bool = False, **kw: Any) -> dom.Career:
    base: dict[str, Any] = {
        "birth": BIRTH_1962,
        "quarters_acquired": quarters,
        "as_of": LEGAL_1962,
        "gross_annual_cents": SAM_35K,
        "best_years_average_cents": SAM_35K,
        "agirc_arrco_points": Decimal(0),
        "working": working,
    }
    return dom.Career(**{**base, **kw})


# ------------------------------------------------------------------ règles datées
def test_generations_follow_the_2026_suspension_of_the_2023_calendar() -> None:
    assert dom.generation(date(1960, 5, 1)).legal_age == (62, 0)
    assert dom.generation(date(1961, 9, 1)) == dom.Generation(date(1961, 9, 1), date(1961, 12, 31), (62, 3), 169)
    assert dom.generation(BIRTH_1962).legal_age == (62, 6) and dom.generation(BIRTH_1962).quarters_required == 169
    # LFSS 2026 (loi 2025-1403, art. 105) : 62 ans 9 mois et 170 trimestres jusqu'aux générations nées avant avril 1965.
    assert (
        dom.generation(date(1963, 1, 1)).legal_age == (62, 9)
        and dom.generation(date(1965, 3, 31)).quarters_required == 170
    )
    assert (
        dom.generation(date(1965, 4, 1)).legal_age == (63, 0)
        and dom.generation(date(1965, 4, 1)).quarters_required == 171
    )
    assert (
        dom.generation(date(1969, 1, 1)).legal_age == (64, 0)
        and dom.generation(date(1991, 4, 12)).quarters_required == 172
    )
    with pytest.raises(dom.RetirementError):
        dom.generation(date(1899, 12, 31))
    assert dom.legal_age_date(BIRTH_1962) == LEGAL_1962 and dom.full_rate_age_date(BIRTH_1962) == date(2029, 6, 15)
    assert dom.quarters_between(date(2026, 1, 1), date(2027, 1, 1)) == 4 and dom.quarters_between(TODAY, TODAY) == 0
    assert dom.age_at(date(1991, 4, 12), TODAY) == (35, 6)
    assert dom.RULES_CHECKED == "2026-10-24" and all(r["checked"] == dom.RULES_CHECKED for r in dom.RULES.values())
    assert all(str(r["url"]).startswith("https://") for r in dom.RULES.values())


def test_reference_case_of_service_public_gives_1458_33_a_month_at_full_rate() -> None:
    d = dom.estimate(_career(169), LEGAL_1962)
    assert d.status == "full" and d.rate_percent == Decimal("50.000") and d.missing_quarters == 0
    assert d.base_monthly_low_cents == d.base_monthly_high_cents == 145_833  # 35 000 × 50 % ÷ 12
    assert d.complementary_monthly_low_cents == 0 and d.total_high_cents == 145_833
    assert d.age == (62, 6) and any("relevé de carrière" in n for n in d.notes)
    as_dict = dom.departure_to_dict(d)
    assert (
        as_dict["at"] == "2024-12-15" and as_dict["rate_percent"] == "50.000" and as_dict["total_low_cents"] == 145_833
    )


def test_missing_quarters_cost_a_decote_and_a_prorata_but_67_restores_the_full_rate() -> None:
    early = dom.estimate(_career(160), LEGAL_1962)
    assert early.status == "decote" and early.missing_quarters == 9
    assert early.rate_percent == Decimal("44.375")  # 50 − 9 × 0,625
    assert early.base_monthly_high_cents == 122_535  # 35 000 × 44,375 % × 160/169 ÷ 12 = 1 225,35 €
    at_67 = dom.estimate(_career(160), date(2029, 6, 15))
    assert at_67.status == "full" and at_67.rate_percent == Decimal("50.000") and at_67.missing_quarters == 9
    assert at_67.base_monthly_high_cents == 138_067  # taux plein à 67 ans, mais toujours 160/169
    # La décote est plafonnée à 20 trimestres (taux plancher 37,5 %).
    far = dom.estimate(_career(120), LEGAL_1962)
    assert far.rate_percent == Decimal("37.500") and far.missing_quarters == 49
    # Avant l'âge légal : pas de départ (hors carrière longue), on le dit.
    before = dom.estimate(_career(169), date(2024, 1, 1))
    assert before.status == "before_legal" and any("62 ans 6 mois" in n for n in before.notes)


def test_working_longer_adds_quarters_then_a_surcote_and_agirc_arrco_points() -> None:
    later = dom.estimate(_career(169, working=True), date(2025, 12, 15))
    assert later.quarters_total == 173 and later.extra_quarters == 4 and later.status == "surcote"
    assert later.rate_percent == Decimal("52.500") and later.base_monthly_high_cents == 153_125  # +1,25 % × 4
    # Points : 6,20 % de la tranche 1 au prix d'achat du point ; la fourchette vient des points déjà acquis inconnus.
    assert dom.points_per_year(3_500_000) == (Decimal(3_500_000) * Decimal("0.062") / Decimal("2018.77")).quantize(
        Decimal("0.01")
    )
    assert dom.points_per_year(0) == 0 and dom.points_per_year(100_000_000) > dom.points_per_year(4_806_000)
    unknown = dom.estimate(_career(169, agirc_arrco_points=None), LEGAL_1962)
    assert 0 < unknown.complementary_monthly_low_cents < unknown.complementary_monthly_high_cents
    assert any("Points Agirc-Arrco inconnus" in n for n in unknown.notes)
    # Salaire moyen inconnu : fourchette 75 % à 100 % du brut actuel plafonné au PASS.
    rich = dom.estimate(_career(169, best_years_average_cents=None, gross_annual_cents=9_000_000), LEGAL_1962)
    assert rich.base_monthly_high_cents == 200_250  # PASS 48 060 € × 50 % ÷ 12
    assert rich.base_monthly_low_cents == 150_188 and rich.replacement_percent is not None
    with pytest.raises(dom.RetirementError):
        dom.Career(birth=BIRTH_1962, quarters_acquired=201, as_of=LEGAL_1962, gross_annual_cents=0)


def test_milestones_and_per_rules() -> None:
    marks = dom.milestones(_career(160, working=True))
    assert marks["legal"] == LEGAL_1962 and marks["age_67"] == date(2029, 6, 15)
    assert marks["full_rate"] == date(2027, 3, 15), "9 trimestres manquants à 4 par an : 27 mois après le relevé"
    assert dom.milestones(_career(160))["full_rate"] == date(2029, 6, 15), "sans cotiser, le taux plein attend 67 ans"
    # PER : 10 % des revenus, entre 10 % du PASS et 10 % de 8 PASS.
    assert dom.per_ceiling(3_302_400) == 471_000 and dom.per_ceiling(6_000_000) == 600_000
    assert dom.per_ceiling(50_000_000) == 3_768_000
    small = dom.per_effect(200_000, professional_income_cents=3_302_400, marginal_rate_percent=11)
    assert (small.deductible_cents, small.tax_saved_cents, small.effort_cents) == (200_000, 22_000, 178_000)
    assert any("rapporte peu" in n for n in small.notes)
    over = dom.per_effect(600_000, professional_income_cents=3_302_400, marginal_rate_percent=30)
    assert over.deductible_cents == 471_000 and over.tax_saved_cents == 141_300
    assert any("Au-dessus du plafond de l'année (4 710 €)" in n for n in over.notes)
    assert all("imposée" in n for n in over.notes[:1])
    with pytest.raises(dom.RetirementError):
        dom.per_effect(0, professional_income_cents=1, marginal_rate_percent=30)


# ------------------------------------------------------------------ API, sur le foyer type
@pytest.fixture
def world(app, client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    with app.state.sessionmaker() as db:
        seed_household(db)
    from app.api import deps

    app.dependency_overrides[deps.today] = lambda: TODAY
    return client


def _payslip(db: Session, brut_cents: int) -> None:
    doc = VaultDocument(kind="payslip", title="Bulletin septembre", filename="paie.pdf", mime="application/pdf",
                        size=10, sha256="c" * 64, status="verified", source="upload", method="pdf_text",
                        confidence=0.9, document_date=date(2026, 9, 30))  # fmt: skip
    db.add(doc)
    db.flush()
    db.add(VaultField(document_id=doc.id, key="brut", value=json.dumps(brut_cents), confidence=0.9))
    db.commit()


def test_retirement_screen_from_what_bank_already_knows(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    r = world.get("/api/retirement")
    assert r.status_code == 200, r.text
    v = r.json()
    career = v["career"]
    # Né en 1991 (profil du foyer) : 64 ans, 172 trimestres ; salaire net des relevés converti en brut.
    assert (career["legal_age_years"], career["legal_age_months"], career["quarters_required"]) == (64, 0, 172)
    assert career["age_years"] == 35 and career["gross_annual_cents"] == dom.gross_from_net_monthly(215_000)
    assert "converti en brut" in career["salary_source"]
    assert career["quarters_acquired"] == 60, "sans relevé : 4 par an depuis l'année des 20 ans"
    assert any("info-retraite.fr" in n for n in v["notes"]) and v["profile"] == {}
    legal, full, at_67 = v["departures"]["legal"], v["departures"]["full_rate"], v["departures"]["age_67"]
    assert legal["at"] == "2055-04-12" and legal["age_years"] == 64 and at_67["at"] == "2058-04-12"
    assert legal["status"] == "full" and legal["quarters_total"] == 173 and legal["missing_quarters"] == 0
    assert legal["extra_quarters"] == 0, "un trimestre de trop avant l'âge légal ne fait pas de surcote"
    assert full["at"] == legal["at"], "au rythme de 4 trimestres par an, le taux plein tombe à l'âge légal"
    assert at_67["status"] == "surcote" and at_67["extra_quarters"] == 12 and at_67["rate_percent"] == "57.500"
    assert 0 < legal["total_low_cents"] < legal["total_high_cents"] < 215_000
    assert 0 < legal["replacement_percent"] < 100
    assert v["per"]["ceiling_cents"] == 471_000 and v["per"]["payment_cents"] == 165_120  # 5 % du brut par défaut
    assert v["per"]["tax_saved_cents"] == round(165_120 * v["per"]["marginal_rate_percent"] / 100)
    assert v["rules_checked"] == "2026-10-24" and v["rules_recheck"] is False and "info-retraite.fr" in v["disclaimer"]
    # Un âge au choix et un versement PER au choix.
    custom = world.get("/api/retirement", params={"age_years": 62, "age_months": 0, "per_cents": 600_000}).json()
    d = custom["departures"]["custom"]
    assert (
        d["status"] == "before_legal" and d["at"] == "2053-04-12" and any("Avant l'âge légal" in n for n in d["notes"])
    )
    assert custom["per"]["deductible_cents"] == 471_000 and any(
        "Au-dessus du plafond" in n for n in custom["per"]["notes"]
    )
    assert world.get("/api/retirement", params={"age_years": 50}).status_code == 422
    assert world.get("/api/retirement", params={"age_years": 62, "age_months": 12}).status_code == 422


def test_your_career_statement_is_saved_encrypted_and_changes_the_estimate(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    body = {"quarters_acquired": 48, "as_of": "2026-06-30", "best_years_average_cents": 2_800_000,
            "agirc_arrco_points": "1 250,5", "career_start_year": 2013, "working": True}  # fmt: skip
    r = world.put("/api/retirement/profile", json=body)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["profile"]["quarters_acquired"] == 48 and v["profile"]["agirc_arrco_points"] == "1250.50"
    career = v["career"]
    assert (
        career["quarters_acquired"] == 48
        and career["as_of"] == "2026-06-30"
        and career["best_years_average_cents"] == 2_800_000
    )
    assert not any("info-retraite.fr" in n for n in v["notes"]), "le relevé est là : plus de rappel"
    legal = v["departures"]["legal"]
    # 48 trimestres au 30 juin 2026 + 4 par an jusqu'au 12 avril 2055 (115 trimestres) = 163 < 172 : décote.
    assert legal["quarters_total"] == 163 and legal["missing_quarters"] == 9 and legal["status"] == "decote"
    assert legal["rate_percent"] == "44.375"
    assert (
        legal["base_monthly_low_cents"] == legal["base_monthly_high_cents"] == 98_124
    )  # 28 000 × 44,375 % × 163/172 ÷ 12 = 981,24 €
    assert v["departures"]["full_rate"]["at"] == "2057-06-30" and v["departures"]["full_rate"]["status"] == "full"
    assert any("coefficient d'anticipation" in n for n in legal["notes"])
    # Chiffré en base : ni les trimestres ni les points ne se lisent en clair.
    with app.state.sessionmaker() as db:
        raw = db.execute(text("SELECT data FROM retirement_profiles")).scalar_one()
    assert "1250" not in str(raw) and "quarters_acquired" not in str(raw)
    # Validation : trimestres, points, dates.
    assert world.put("/api/retirement/profile", json={**body, "quarters_acquired": 201}).status_code == 422
    assert world.put("/api/retirement/profile", json={**body, "agirc_arrco_points": "beaucoup"}).status_code == 422
    assert world.put("/api/retirement/profile", json={**body, "as_of": "2027-01-01"}).status_code == 422
    assert world.put("/api/retirement/profile", json={**body, "birth_date": "2030-01-01"}).status_code == 422
    # Un bulletin vérifié au coffre l'emporte sur le net des relevés.
    with app.state.sessionmaker() as db:
        _payslip(db, 290_000)
    v = world.get("/api/retirement").json()
    assert (
        v["career"]["gross_annual_cents"] == 3_480_000 and "bulletin de paie du coffre" in v["career"]["salary_source"]
    )
    # L'export RGPD emporte ton relevé (et les prêts suivis) ; l'estimation, elle, se recalcule.
    from tests.conftest import PASSWORD

    exported = world.post("/api/export", json={"password": PASSWORD}).json()
    assert exported["retirement_profile"]["quarters_acquired"] == 48 and exported["loans"] == []
    # Oublier le relevé : retour à l'estimation.
    v = world.delete("/api/retirement/profile").json()
    assert v["profile"] == {} and v["career"]["quarters_acquired"] == 60
    actions = [e["action"] for e in world.get("/api/audit").json()["entries"]]
    assert "retirement.profile" in actions and "retirement.profile_removed" in actions


def test_without_a_birth_date_bank_asks_instead_of_guessing(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        row = db.get(HouseholdProfile, 1)
        assert row is not None
        db.delete(row)
        db.commit()
    v = world.get("/api/retirement").json()
    assert v["career"] is None and v["departures"] == {} and v["per"] is None
    assert any("date de naissance" in n for n in v["notes"])
    # La date de naissance peut être donnée ici, avec le relevé.
    v = world.put(
        "/api/retirement/profile", json={"quarters_acquired": 80, "working": True, "birth_date": "1980-02-01"}
    ).json()
    assert v["career"]["birth_date"] == "1980-02-01" and v["career"]["legal_age_years"] == 64
    assert v["career"]["gross_annual_cents"] == dom.gross_from_net_monthly(215_000)
    with app.state.sessionmaker() as db:
        assert svc.summary_for_chat(db, TODAY)["career"]["quarters_acquired"] == 80


# ------------------------------------------------------------------ MCP et assistant : l'agent explique, Bank calcule
def test_agents_get_the_retirement_view_with_their_privacy(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    from tests.test_mcp import make_agent

    full = make_agent(app, world, name="local", privacy="full").ok("get_retirement")
    assert full["career"]["gross_annual_cents"] > 0 and full["departures"]["legal"]["total_high_cents"] > 0
    assert "n'invente aucun droit" in full["rule"] and full["rules"]["simulator"]["url"].startswith("https://")
    blind = make_agent(app, world, name="agg", privacy="aggregates").ok("get_retirement")
    assert "profile" not in blind and "gross_annual_cents" not in blind["career"]
    assert "agirc_arrco_points" not in blind["career"] and blind["career"]["quarters_required"] == 172
    assert blind["departures"]["legal"]["status"] == "full"
    with app.state.sessionmaker() as db:
        chat = svc.summary_for_chat(db, TODAY, age_years=66)
    assert set(chat["departures"]) == {"legal", "full_rate", "age_67", "custom"}
    assert chat["departures"]["custom"]["age_years"] == 66 and chat["per"]["ceiling_cents"] == 471_000
    assert chat["simulator"] == dom.RULES["simulator"]["url"] and "rules_checked" in chat
