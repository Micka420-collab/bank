from typing import Any

import pytest

from app.domain.onboarding import (
    BY_ID,
    QUESTIONS,
    SECTIONS,
    AnswerError,
    DetectedFacts,
    completeness,
    recommended_emergency_months,
    risk_profile,
    suggestions,
    validate_answer,
)


def test_catalogue_is_coherent() -> None:
    sections = {s for s, _ in SECTIONS}
    assert len(BY_ID) == len(QUESTIONS)
    for q in QUESTIONS:
        assert q.section in sections and q.why and q.label.endswith(("?", "…"))
        if q.kind in ("choice", "multi"):
            assert len(q.options) >= 2
    # Le mandat existe et ne pèse pas sur la décision de l'agent (il est réglé par l'humain).
    assert {q.id for q in QUESTIONS if q.section == "mandat"} >= {"autonomy", "red_lines", "alert_threshold"}


@pytest.mark.parametrize(
    ("qid", "value", "unit", "expected"),
    [
        ("employment", "cdi_prive", "cents", "cdi_prive"),
        ("net_monthly_income", 215_000, "cents", 215_000),
        ("net_monthly_income", "2 150,50 €", "euros", 215_050),
        ("net_monthly_income", 2150.5, "euros", 215_050),
        ("priorities", ["securite", "projets", "securite"], "cents", ["securite", "projets"]),
        ("money_stress", 4, "cents", 4),
        ("notes", "  je  change de   poste en janvier ", "cents", "je change de poste en janvier"),
    ],
)
def test_validate_answer_normalises(qid: str, value: Any, unit: Any, expected: Any) -> None:
    assert validate_answer(qid, value, unit) == expected


@pytest.mark.parametrize(
    ("qid", "value", "unit"),
    [
        ("inconnue", 1, "cents"),
        ("employment", "milliardaire", "cents"),
        ("employment", 3, "cents"),
        ("net_monthly_income", 2150.5, "cents"),  # côté API humaine : centimes entiers uniquement
        ("net_monthly_income", True, "euros"),
        ("net_monthly_income", "beaucoup", "euros"),
        ("net_monthly_income", -5, "cents"),
        ("priorities", [], "cents"),
        ("priorities", ["securite", "liberte", "projets", "plaisir"], "cents"),
        ("priorities", ["inconnu"], "cents"),
        ("money_stress", 9, "cents"),
        ("money_stress", True, "cents"),
        ("notes", "x" * 2001, "cents"),
        ("debts", [{"label": "Auto"}], "cents"),
        ("debts", "pas une liste", "cents"),
    ],
)
def test_validate_answer_rejects(qid: str, value: Any, unit: Any) -> None:
    with pytest.raises(AnswerError):
        validate_answer(qid, value, unit)


def test_list_items_in_euros_for_agents() -> None:
    debts = validate_answer(
        "debts",
        [{"label": "Prêt auto", "kind": "auto", "remaining_eur": "8 400", "monthly_eur": 180, "rate_percent": "4.9"}],
        "euros",
    )
    assert debts == [
        {
            "label": "Prêt auto",
            "kind": "auto",
            "remaining_cents": 840_000,
            "monthly_cents": 18_000,
            "rate_percent": "4.9",
            "end_date": None,
        }
    ]
    planned = validate_answer(
        "planned_expenses", [{"label": "Pneus", "amount_eur": 320, "date": "2026-10-29"}], "euros"
    )
    assert planned[0]["amount_cents"] == 32_000 and planned[0]["date"] == "2026-10-29"
    with pytest.raises(AnswerError, match="Élément 1"):
        validate_answer("debts", [{"label": "X", "remaining_eur": "abc", "monthly_eur": 1}], "euros")


def test_red_lines_are_trimmed_and_bounded() -> None:
    assert validate_answer("red_lines", [{"rule": "  jamais   de crypto "}]) == [{"rule": "jamais de crypto"}]
    with pytest.raises(AnswerError):
        validate_answer("red_lines", [{"rule": "x"} for _ in range(11)])


def test_completeness_is_weighted_and_orders_missing_by_importance() -> None:
    empty = completeness({})
    assert empty.percent == 0 and empty.answered == 0
    assert empty.missing[0].weight == max(q.weight for q in QUESTIONS)
    heavy = completeness({"employment": "cdi_prive", "net_monthly_income": 1})
    light = completeness({"tone": "direct", "report_frequency": "quotidien"})
    assert heavy.percent > light.percent > 0
    full = completeness({q.id: 1 for q in QUESTIONS})
    assert full.percent == 100 and not full.missing
    # « notes » a un poids nul : elle n'entre pas dans le calcul.
    assert "notes" not in {q.id for q in empty.missing}


def test_risk_profile_capacity_beats_appetite() -> None:
    bold = {"risk_horizon": "plus_10", "risk_drop": "renforces", "risk_priority": "plutot_rendement"}
    p = risk_profile(bold, emergency_months=6, expensive_debt=False)
    assert p.label == "dynamique" and not p.capacity_limited
    limited = risk_profile(bold, emergency_months=1, expensive_debt=False)
    assert limited.label == "dynamique" and limited.capacity_limited and "matelas" in limited.reason
    debt = risk_profile(bold, emergency_months=6, expensive_debt=True)
    assert debt.capacity_limited and "crédit" in debt.reason
    shy = risk_profile({"risk_horizon": "moins_2", "risk_drop": "vends_tout"}, 6, False)
    assert shy.label == "prudent"
    assert risk_profile({"risk_horizon": "2_5"}, 6, False).label == "inconnu"


def test_recommended_emergency_months() -> None:
    assert recommended_emergency_months({"employment": "public"}) == 3
    assert recommended_emergency_months({"employment": "independant", "income_variability": "beaucoup"}) == 8
    assert recommended_emergency_months({"employment": "cdd", "dependents": 5}) == 8
    assert recommended_emergency_months({}) == 4


def test_suggestions_prefill_from_statements_without_overwriting() -> None:
    facts = DetectedFacts(
        salary_monthly_cents=215_000,
        salary_months=4,
        housing_cost_cents=65_000,
        housing_label="FONCIA",
        credit_lines=[("COFIDIS", 18_000)],
        savings_capacity_cents=40_000,
        household_couple=False,
        children=0,
    )
    by = {s.question_id: s for s in suggestions(facts, {})}
    assert by["net_monthly_income"].value == 215_000 and "4 mois" in by["net_monthly_income"].evidence
    assert by["housing_cost"].value == 65_000 and by["housing_status"].value == "locataire"
    assert by["debts"].value[0]["monthly_cents"] == 18_000
    assert by["household"].value == "seul" and by["dependents"].value == 0
    # Une réponse déclarée n'est jamais écrasée par une détection.
    again = {s.question_id for s in suggestions(facts, {"net_monthly_income": 200_000})}
    assert "net_monthly_income" not in again
    # Toute suggestion est une réponse valide (l'interface peut la confirmer telle quelle).
    for s in by.values():
        validate_answer(s.question_id, s.value)
