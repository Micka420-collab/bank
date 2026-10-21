"""« Puis-je acheter ça ? » / « Et si j'épargnais ? » : les chiffres viennent de la prévision, pas du modèle."""

from collections.abc import Sequence
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.decisions import evaluate_purchase, plan_purchase, what_if_savings
from app.domain.forecast import MonthForecast, UpcomingEvent, build_forecast
from app.domain.recurring import detect_recurring
from tests.test_recurring_forecast import CATS, scenario

TODAY = date(2026, 10, 21)
TXNS = scenario(TODAY)
SERIES = detect_recurring(TXNS, TODAY)


def runner(balance: int = 50_000):  # type: ignore[no-untyped-def]
    def run(extra: Sequence[UpcomingEvent] = (), savings_goal_cents: int = 0) -> MonthForecast:
        return build_forecast(
            today=TODAY,
            txns=TXNS,
            current_balance_cents=balance,
            series=SERIES,
            categories=CATS,
            buffer_cents=10_000,
            savings_goal_cents=savings_goal_cents,
            extra_events=extra,
        )

    return run


def test_small_purchase_is_ok() -> None:
    v = evaluate_purchase(runner(), today=TODAY, amount_cents=10_000, on=None, label="Casque", buffer_cents=10_000)
    assert v.verdict == "ok" and v.on == TODAY and v.best_date is None
    assert v.after.per_day_cents < v.before.per_day_cents
    assert v.before.lowest_cents - v.after.lowest_cents == 10_000
    assert "/jour avant" in v.explanation


def test_purchase_dipping_into_cushion_is_tight_with_safe_date() -> None:
    v = evaluate_purchase(runner(), today=TODAY, amount_cents=30_000, on=None, label="Vélo", buffer_cents=10_000)
    assert v.verdict == "serre"
    assert v.best_date == date(2026, 10, 28)  # le jour du salaire


def test_purchase_causing_overdraft_should_wait_for_payday() -> None:
    v = evaluate_purchase(runner(), today=TODAY, amount_cents=50_000, on=None, label="Console", buffer_cents=10_000)
    assert v.verdict == "attendre" and v.after.overdraft_date is not None
    assert v.best_date == date(2026, 10, 28)
    assert "28/10" in v.explanation


def test_unaffordable_purchase_is_discouraged() -> None:
    v = evaluate_purchase(runner(), today=TODAY, amount_cents=500_000, on=None, label="Voiture", buffer_cents=10_000)
    assert v.verdict == "deconseille" and v.best_date is None


def test_envelope_impact_and_savings_equivalent() -> None:
    v = evaluate_purchase(
        runner(),
        today=TODAY,
        amount_cents=6_000,
        on=date(2026, 10, 23),
        label="Resto",
        buffer_cents=10_000,
        category="restaurants",
        monthly_savings_cents=20_000,
    )
    assert v.envelope is not None and v.envelope.over_after and v.envelope.budget_cents == 5_000
    assert v.savings_months_equivalent == 0.3
    assert "restaurants serait dépassée" in v.explanation


def test_purchase_after_month_end_is_flagged_and_past_dates_clamped() -> None:
    later = evaluate_purchase(
        runner(), today=TODAY, amount_cents=10_000, on=date(2026, 12, 1), label="X", buffer_cents=10_000
    )
    assert "après ce mois" in later.explanation
    past = evaluate_purchase(runner(), today=TODAY, amount_cents=10_000, on=date(2026, 1, 1), label="X", buffer_cents=0)
    assert past.on == TODAY
    with pytest.raises(ValueError, match="positif"):
        evaluate_purchase(runner(), today=TODAY, amount_cents=0, on=None, label="X", buffer_cents=0)


@settings(max_examples=40, deadline=None)
@given(st.integers(min_value=1, max_value=400_000))
def test_verdict_is_monotonic_in_amount(amount: int) -> None:
    """Plus cher ne peut jamais être plus sûr."""
    order = {"ok": 0, "serre": 1, "attendre": 2, "deconseille": 3}
    run = runner()
    a = evaluate_purchase(run, today=TODAY, amount_cents=amount, on=None, label="A", buffer_cents=10_000)
    b = evaluate_purchase(run, today=TODAY, amount_cents=amount + 5_000, on=None, label="B", buffer_cents=10_000)
    assert order[b.verdict] >= order[a.verdict]
    assert b.after.lowest_cents <= a.after.lowest_cents


def test_what_if_savings() -> None:
    run = runner()
    w = what_if_savings(
        lambda goal: run(savings_goal_cents=goal),
        monthly_cents=20_000,
        emergency_available_cents=100_000,
        emergency_target_cents=300_000,
        monthly_essentials_cents=100_000,
        goals=[("Vacances", 0, 120_000, 10_000)],
    )
    assert w.feasible_this_month
    # Le plafond du jour est fixé par la trésorerie d'ici la paie : épargner après la paie ne le change pas…
    assert w.per_day_after_cents == w.per_day_now_cents and "ne change pas" in w.explanation
    # …mais le budget libre du mois baisse exactement de l'épargne prévue.
    assert w.free_now_cents - w.free_after_cents == 20_000
    assert w.emergency_months_now == 1.0 and w.emergency_reached_in_months is not None
    assert 9 <= w.emergency_reached_in_months <= 10
    [g] = w.goals
    assert g.months_before == 12 and g.months_after == 4
    assert "Matelas atteint dans" in w.explanation

    too_much = what_if_savings(
        lambda goal: run(savings_goal_cents=goal),
        monthly_cents=60_000,
        emergency_available_cents=400_000,
        emergency_target_cents=300_000,
        monthly_essentials_cents=100_000,
        goals=[],
    )
    assert not too_much.feasible_this_month and "découvert" in too_much.explanation
    assert too_much.emergency_reached_in_months == 0 and "déjà constitué" in too_much.explanation


# ------------------------------------------------------------------ oui / non / dans combien de temps
def plan(amount: int, **kw):  # type: ignore[no-untyped-def]
    args = {
        "today": TODAY,
        "amount_cents": amount,
        "label": "Achat",
        "buffer_cents": 10_000,
        "monthly_capacity_cents": 50_000,
    }
    return plan_purchase(runner(), **{**args, **kw})


def test_plan_answers_now_when_cash_allows() -> None:
    assert plan(10_000).answer == "oui"
    tight = plan(30_000)
    assert tight.answer == "oui_mais" and "28/10" in tight.headline
    wait = plan(50_000)
    assert wait.answer == "attendre" and "28/10" in wait.headline and not wait.paths


def test_plan_uses_savings_beyond_the_emergency_fund_only() -> None:
    p = plan(500_000, liquid_savings_cents=900_000, emergency_target_cents=300_000)
    assert p.answer == "oui_mais" and p.from_savings_cents == 500_000 and "matelas" in p.headline
    # Le matelas lui-même n'est jamais compté comme disponible.
    q = plan(500_000, liquid_savings_cents=300_000, emergency_target_cents=300_000)
    assert q.answer == "plus_tard" and q.from_savings_cents == 0


def test_plan_gives_two_honest_paths_when_the_emergency_fund_is_incomplete() -> None:
    p = plan(
        500_000,
        monthly_capacity_cents=50_000,
        committed_monthly_cents=10_000,
        liquid_savings_cents=100_000,
        emergency_target_cents=300_000,
    )
    assert p.answer == "plus_tard"
    first, fast = p.paths
    assert first.label.startswith("Matelas d'abord") and first.months == 18  # (2 000 + 5 000) / 400 €
    assert fast.months == 13 and fast.affordable_on is not None
    assert first.affordable_on == date(2028, 4, 1)
    assert "avril 2028" in p.headline and "400,00" in p.headline


def test_plan_is_honest_when_out_of_reach() -> None:
    assert plan(5_000_000, monthly_capacity_cents=0).answer == "hors_de_portee"
    far = plan(10_000_000, monthly_capacity_cents=20_000)
    assert far.answer == "hors_de_portee" and far.paths[0].months is None
