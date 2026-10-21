"""Plan de placement : l'ordre des priorités, la part d'actions, la fiscalité et les lignes rouges."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.invest import (
    DebtLine,
    PlanInputs,
    ProjectLine,
    TaxEstimate,
    build_plan,
    estimate_tax,
    excluded_by_red_lines,
    household_parts,
    income_tax,
    load_references,
    marginal_rate,
)
from app.domain.wealth import ProductStatus

REFS = load_references()
TODAY = date(2026, 10, 21)


def product(kind: str, name: str, rate: str, ceiling: int, balance: int) -> ProductStatus:
    return ProductStatus(kind, name, Decimal(rate), ceiling, balance, max(0, ceiling - balance), 0, balance >= ceiling)


LIVRETS = [product("livret_a", "Livret A", "1.70", 2_295_000, 480_000), product("ldds", "LDDS", "1.70", 1_200_000, 0)]


def inputs(**over):  # type: ignore[no-untyped-def]
    base = {
        "today": TODAY,
        "monthly_capacity_cents": 50_000,
        "liquid_savings_cents": 480_000,
        "emergency_target_cents": 400_000,
        "monthly_essentials_cents": 100_000,
        "debts": [],
        "projects": [],
        "regulated": LIVRETS,
        "lep_eligible": False,
        "risk_label": "equilibre",
        "risk_horizon": "plus_10",
        "red_lines": [],
        "tax": TaxEstimate(11, "declared", None, None, ""),
        "age": 35,
    }
    return PlanInputs(**{**base, **over})


# ------------------------------------------------------------------ impôt
def test_tax_brackets_2026() -> None:
    assert marginal_rate(1_000_000, Decimal(1), REFS) == 0
    assert marginal_rate(2_500_000, Decimal(1), REFS) == 11
    assert marginal_rate(3_000_000, Decimal(1), REFS) == 30
    assert marginal_rate(9_000_000, Decimal(1), REFS) == 41
    assert marginal_rate(20_000_000, Decimal(1), REFS) == 45
    assert marginal_rate(3_000_000, Decimal(2), REFS) == 11  # couple : le quotient divise par deux
    # 30 000 € pour une part : 11 % de (29 579 − 11 600) + 30 % de (30 000 − 29 579) = 1 977,69 + 126,30
    assert income_tax(3_000_000, Decimal(1), REFS) == 210_399


def test_household_parts() -> None:
    assert household_parts(couple=False, children=0) == Decimal(1)
    assert household_parts(couple=True, children=2) == Decimal(3)
    assert household_parts(couple=True, children=3) == Decimal(4)
    assert household_parts(couple=False, children=1, single_parent=True) == Decimal(2)


def test_estimate_tax_priorities() -> None:
    assert (
        estimate_tax(
            declared_bracket="30",
            document_taxable_cents=1,
            document_parts=None,
            net_monthly_income_cents=1,
            yearly_extras_cents=0,
            couple=False,
            children=0,
            single_parent=False,
            refs=REFS,
        ).source
        == "declared"
    )
    doc = estimate_tax(
        declared_bracket=None,
        document_taxable_cents=3_500_000,
        document_parts=Decimal("1.5"),
        net_monthly_income_cents=None,
        yearly_extras_cents=0,
        couple=False,
        children=1,
        single_parent=False,
        refs=REFS,
    )
    assert doc.source == "document" and doc.marginal_rate_percent == 11
    est = estimate_tax(
        declared_bracket=None,
        document_taxable_cents=None,
        document_parts=None,
        net_monthly_income_cents=215_000,
        yearly_extras_cents=90_000,
        couple=False,
        children=0,
        single_parent=False,
        refs=REFS,
    )
    assert est.source == "estimated" and est.marginal_rate_percent == 11 and est.taxable_income_cents == 2_750_100
    assert (
        estimate_tax(
            declared_bracket=None,
            document_taxable_cents=None,
            document_parts=None,
            net_monthly_income_cents=None,
            yearly_extras_cents=None,
            couple=False,
            children=0,
            single_parent=False,
            refs=REFS,
        ).source
        == "unknown"
    )


# ------------------------------------------------------------------ lignes rouges
@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        (["Jamais de crypto"], {"crypto"}),
        (["Pas de bourse", "pas d'assurance-vie"], {"actions", "assurance_vie"}),
        (["Aucun PER"], {"per"}),
        (["Pas de crédit conso"], {"credit"}),
        (["Je veux garder 500 € de marge"], set()),
    ],
)
def test_red_lines(rules: list[str], expected: set[str]) -> None:
    assert excluded_by_red_lines(rules) == expected


# ------------------------------------------------------------------ le plan
def test_long_term_when_bases_are_in_place() -> None:
    plan = build_plan(inputs(), REFS)
    kinds = [s.kind for s in plan.steps]
    assert kinds == ["long_terme"] and plan.long_term_monthly_cents == 50_000 and plan.equity_share_percent == 50
    vehicles = {a.vehicle: a for a in plan.long_term}
    assert set(vehicles) == {"pea", "assurance_vie"} and vehicles["pea"].monthly_cents == 25_000
    assert "pas un conseil" in plan.cautions[-1]
    assert "long terme" in plan.summary


def test_emergency_fund_first_then_wait() -> None:
    plan = build_plan(inputs(liquid_savings_cents=100_000), REFS)
    assert [s.kind for s in plan.steps] == ["matelas", "attente"]
    first = plan.steps[0]
    assert first.monthly_cents == 50_000 and first.months == 6 and "Livret A" in first.where
    assert plan.long_term == [] and plan.long_term_monthly_cents == 0


def test_one_month_buffer_is_absolute_priority_and_lep_route() -> None:
    plan = build_plan(inputs(liquid_savings_cents=0, lep_eligible=True), REFS)
    first = plan.steps[0]
    assert first.title.startswith("Un premier mois") and first.target_cents == 100_000 and first.months == 2
    assert "LEP à ouvrir" in first.where
    assert any("éligible au LEP" in c for c in plan.cautions)


def test_expensive_debt_before_long_term_with_caution() -> None:
    debt = DebtLine("Crédit conso", 300_000, 15_000, Decimal("7.9"))
    plan = build_plan(inputs(debts=[debt]), REFS)
    assert [s.kind for s in plan.steps] == ["dette", "attente"]
    step = plan.steps[0]
    assert step.monthly_cents == 50_000 and step.months == 5 and "7.9" in step.title
    assert any("indemnité" in c for c in plan.cautions)
    cheap = DebtLine("Prêt immo", 15_000_000, 80_000, Decimal("1.2"))
    assert [s.kind for s in build_plan(inputs(debts=[cheap]), REFS).steps] == ["long_terme"]


def test_debt_and_gap_split_capacity() -> None:
    debt = DebtLine("Crédit", 100_000, 5_000, Decimal("9"))
    plan = build_plan(inputs(debts=[debt], liquid_savings_cents=200_000), REFS)
    assert [s.kind for s in plan.steps] == ["dette", "matelas", "attente"]
    assert plan.steps[0].monthly_cents == 35_000 and plan.steps[1].monthly_cents == 15_000


def test_dated_projects_are_safe_and_prioritised() -> None:
    projects = [
        ProjectLine("Voyage", 240_000, date(2027, 10, 1), 2),
        ProjectLine("Apport appartement", 1_500_000, date(2029, 10, 1), 1, saved_cents=300_000),
    ]
    plan = build_plan(inputs(projects=projects, monthly_capacity_cents=60_000), REFS)
    kinds = [s.kind for s in plan.steps]
    assert kinds == ["projet", "projet", "long_terme"]
    apport, voyage = plan.steps[0], plan.steps[1]
    assert apport.title.endswith("Apport appartement") and apport.monthly_cents == 33_334  # 12 000 € / 36 mois
    assert "fonds euros" in apport.where and voyage.monthly_cents == 20_000 and "Livrets" in voyage.where
    assert plan.long_term_monthly_cents == 60_000 - 33_334 - 20_000
    tight = build_plan(inputs(projects=projects, monthly_capacity_cents=40_000), REFS)
    assert any("Voyage" in c and "par mois" in c for c in tight.cautions)


def test_equity_share_depends_on_profile_horizon_age_and_red_lines() -> None:
    assert build_plan(inputs(risk_label="dynamique"), REFS).equity_share_percent == 75
    assert build_plan(inputs(risk_label="prudent"), REFS).equity_share_percent == 20
    assert build_plan(inputs(risk_horizon="2_5"), REFS).equity_share_percent == 0
    assert build_plan(inputs(risk_horizon="5_10", risk_label="dynamique"), REFS).equity_share_percent == 50
    assert build_plan(inputs(age=63, risk_label="dynamique"), REFS).equity_share_percent == 40
    no_stocks = build_plan(inputs(red_lines=["pas de bourse"]), REFS)
    assert no_stocks.equity_share_percent == 0 and {a.vehicle for a in no_stocks.long_term} == {"assurance_vie"}
    no_av = build_plan(inputs(red_lines=["pas de bourse ni d'assurance vie"]), REFS)
    assert {a.vehicle for a in no_av.long_term} == {"livret"}
    unknown = build_plan(inputs(risk_label="inconnu"), REFS)
    assert unknown.equity_share_percent == 30 and any("Profil de risque incomplet" in c for c in unknown.cautions)


def test_per_only_when_marginal_rate_justifies_it() -> None:
    low = build_plan(inputs(tax=TaxEstimate(11, "declared", None, None, "")), REFS)
    assert "per" not in {a.vehicle for a in low.long_term}
    high = build_plan(inputs(tax=TaxEstimate(30, "declared", None, None, "")), REFS)
    per = next(a for a in high.long_term if a.vehicle == "per")
    assert per.share_percent == 25 and per.monthly_cents == 12_500
    assert "per" not in {a.vehicle for a in build_plan(inputs(tax=high.tax, red_lines=["pas de PER"]), REFS).long_term}
    assert "per" not in {a.vehicle for a in build_plan(inputs(tax=high.tax, age=60), REFS).long_term}


def test_overdraft_and_no_capacity() -> None:
    plan = build_plan(inputs(overdraft=True, liquid_savings_cents=100_000), REFS)
    assert plan.steps[0].kind == "urgence" and plan.steps[0].monthly_cents == 0
    assert plan.long_term_monthly_cents == 0
    empty = build_plan(inputs(monthly_capacity_cents=0), REFS)
    assert empty.steps == [] and "Aucune marge" in empty.summary


@settings(max_examples=150, deadline=None)
@given(
    capacity=st.integers(min_value=0, max_value=500_000),
    savings=st.integers(min_value=0, max_value=3_000_000),
    target=st.integers(min_value=100_000, max_value=1_200_000),
    debt_rate=st.decimals(min_value=0, max_value=20, places=1),
    debt=st.integers(min_value=0, max_value=2_000_000),
)
def test_plan_never_allocates_more_than_the_capacity(
    capacity: int, savings: int, target: int, debt_rate: Decimal, debt: int
) -> None:
    debts = [DebtLine("D", debt, 10_000, debt_rate)] if debt else []
    plan = build_plan(
        inputs(
            monthly_capacity_cents=capacity, liquid_savings_cents=savings, emergency_target_cents=target, debts=debts
        ),
        REFS,
    )
    assert 0 <= plan.allocated_cents <= capacity
    assert sum(a.monthly_cents for a in plan.long_term) == plan.long_term_monthly_cents
    assert all(s.monthly_cents >= 0 for s in plan.steps)
    orders = [s.order for s in plan.steps]
    assert orders == sorted(orders)
    if debts and debt_rate >= 5 and capacity > 0:
        assert "long_terme" not in {s.kind for s in plan.steps}
