from datetime import date, timedelta
from itertools import pairwise

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.forecast import (
    CategoryInfo,
    UpcomingEvent,
    allowance_schedule,
    build_forecast,
    daily_variable_rate,
    month_bounds,
    suggest_budgets,
)
from app.domain.recurring import Txn, add_months, detect_recurring

CATS = {
    "salaire": CategoryInfo("salaire", "Salaire", "income"),
    "logement": CategoryInfo("logement", "Logement", "fixed"),
    "abonnements": CategoryInfo("abonnements", "Abonnements", "fixed", 3000),
    "courses": CategoryInfo("courses", "Courses", "variable", 30000),
    "restaurants": CategoryInfo("restaurants", "Restaurants", "variable", 5000),
    "epargne": CategoryInfo("epargne", "Épargne", "savings"),
    "a_categoriser": CategoryInfo("a_categoriser", "À catégoriser", "uncategorized"),
}


def monthly(merchant: str, amount: int, day: int, months: int, end: date, cat: str, start_id: int = 1) -> list[Txn]:
    out = []
    for i in range(months):
        d = add_months(end.replace(day=1), -i, day)
        out.append(Txn(start_id + i, d, amount, merchant, cat))
    return out


def test_add_months_clamps_end_of_month() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert add_months(date(2026, 3, 10), -3) == date(2025, 12, 10)
    assert add_months(date(2026, 1, 5), 1, day=31) == date(2026, 2, 28)


def test_detects_monthly_rent_and_predicts_next() -> None:
    today = date(2026, 10, 21)
    txns = monthly("FONCIA", -65000, 3, 6, date(2026, 10, 3), "logement")
    [s] = detect_recurring(txns, today)
    assert s.cadence == "monthly" and s.direction == "out" and s.amount_cents == -65000
    assert s.next_date == date(2026, 11, 3) and s.day_of_month == 3
    assert not s.variable and not s.late and s.confidence > 0.9


def test_tolerates_missed_month_and_jitter() -> None:
    today = date(2026, 10, 21)
    dates = [
        date(2026, 3, 5),
        date(2026, 4, 6),
        date(2026, 6, 5),
        date(2026, 7, 4),
        date(2026, 8, 5),
        date(2026, 9, 7),
        date(2026, 10, 5),
    ]
    txns = [Txn(i, d, 25829, "CAF LOIRET", "aides") for i, d in enumerate(dates)]
    [s] = detect_recurring(txns, today)
    assert s.cadence == "monthly" and s.next_date == date(2026, 11, 5)


def test_variable_amounts_flagged() -> None:
    today = date(2026, 10, 21)
    amounts = [-6200, -8100, -5400, -9900, -7000, -6100]
    txns = [Txn(i, add_months(date(2026, 10, 16), -i), a, "EDF", "energie") for i, a in enumerate(amounts)]
    [s] = detect_recurring(txns, today)
    assert s.variable and s.amount_min_cents == -9900 and s.amount_max_cents == -5400


def test_weekly_detection() -> None:
    today = date(2026, 10, 21)
    txns = [Txn(i, date(2026, 10, 19) - timedelta(days=7 * i), -1500, "MARCHE", "courses") for i in range(6)]
    [s] = detect_recurring(txns, today)
    assert s.cadence == "weekly" and s.next_date == date(2026, 10, 26)


def test_irregular_and_ended_series_ignored() -> None:
    today = date(2026, 10, 21)
    irregular = [
        Txn(i, date(2026, 1, 1) + timedelta(days=d), -2000, "AMAZON", "shopping")
        for i, d in enumerate([0, 3, 40, 41, 100, 180, 181, 250])
    ]
    ended = monthly("ANCIEN ABO", -999, 10, 5, date(2026, 5, 10), "abonnements", 100)
    assert detect_recurring(irregular + ended, today) == []


def test_late_flag() -> None:
    today = date(2026, 10, 12)
    txns = monthly("CAF LOIRET", 25829, 5, 5, date(2026, 9, 5), "aides")
    [s] = detect_recurring(txns, today)
    assert s.next_date == date(2026, 10, 5) and s.late


def test_transfers_and_future_ignored() -> None:
    today = date(2026, 10, 21)
    txns = [Txn(i, add_months(date(2026, 10, 1), -i), -30000, "LIVRET", "epargne", is_transfer=True) for i in range(6)]
    txns.append(Txn(99, date(2026, 12, 1), -100, "FUTUR"))
    assert detect_recurring(txns, today) == []


# --------------------------------------------------------------------------- prévision
def scenario(today: date) -> list[Txn]:
    txns: list[Txn] = []
    txns += monthly("BRGM", 215000, 28, 7, add_months(today.replace(day=1), -1, 28), "salaire", 1)
    txns += monthly("FONCIA", -65000, 3, 7, today.replace(day=3), "logement", 100)
    txns += monthly("NETFLIX", -1349, 25, 7, add_months(today.replace(day=1), -1, 25), "abonnements", 200)
    nid = 1000
    d = today - timedelta(days=90)
    while d < today:
        txns.append(Txn(nid, d, -2000, f"SHOP{nid % 7}", "courses"))
        nid += 1
        d += timedelta(days=1)
    txns.append(Txn(nid + 1, today - timedelta(days=10), -80000, "FRIGO", "courses"))  # exceptionnel
    return txns


def test_daily_variable_rate_excludes_exceptional() -> None:
    today = date(2026, 10, 21)
    rate = daily_variable_rate(scenario(today), today, CATS, planned_merchants=set())
    assert rate == 2000


def test_forecast_end_of_month() -> None:
    today = date(2026, 10, 21)
    f = build_forecast(
        today=today,
        txns=scenario(today),
        current_balance_cents=50000,
        series=detect_recurring(scenario(today), today),
        categories=CATS,
        buffer_cents=10000,
    )
    assert f.days_left == 11 and f.days_in_month == 31
    labels = {(e.label, e.date) for e in f.upcoming}
    assert ("NETFLIX", date(2026, 10, 25)) in labels and ("BRGM", date(2026, 10, 28)) in labels
    assert f.expected_income_cents == 215000 and f.expected_fixed_cents == -1349
    assert f.daily_variable_rate_cents == 2000
    assert f.projected_end_balance_cents == 50000 + 215000 - 1349 - 2000 * 10
    assert f.safe_to_spend_cents == 50000 + 215000 - 1349 - 10000
    # 500 € disponibles avant le salaire du 28 : la limite du jour vient de là, pas du total du mois.
    first = f.allowance[0]
    assert first.end == date(2026, 10, 27) and first.reason == "BRGM"
    assert f.safe_to_spend_per_day_cents == (50000 - 1349 - 10000) // 7
    assert f.allowance[-1].per_day_cents > first.per_day_cents
    assert len(f.projection) == 11 and f.projection[-1].balance_cents == f.projected_end_balance_cents
    assert f.status == "ok" and f.overdraft_date is None
    assert f.spent_fixed_cents == 65000  # loyer du 3


def test_forecast_detects_overdraft_before_payday() -> None:
    today = date(2026, 10, 21)
    f = build_forecast(
        today=today,
        txns=scenario(today),
        current_balance_cents=5000,
        series=detect_recurring(scenario(today), today),
        categories=CATS,
    )
    # 5000 - 2000/j : passe sous zéro le 24, avant le salaire du 28 -> le solde final positif masquait le risque
    assert f.status == "risk" and f.overdraft_date == date(2026, 10, 24)
    assert f.projected_end_balance_cents > 0
    assert f.lowest_balance_date == date(2026, 10, 27)


def test_budgets_status_and_savings_goal() -> None:
    today = date(2026, 10, 15)
    start, _ = month_bounds(today)
    txns = [
        Txn(1, start + timedelta(days=1), -20000, "RESTO", "restaurants"),
        Txn(2, start + timedelta(days=2), -5000, "SUPER", "courses"),
        Txn(3, start + timedelta(days=3), -30000, "VERS LIVRET", "virements_internes", is_transfer=True),
    ]
    f = build_forecast(
        today=today,
        txns=txns,
        current_balance_cents=100000,
        series=[],
        categories=CATS,
        savings_goal_cents=50000,
    )
    by = {b.slug: b for b in f.budgets}
    assert by["restaurants"].status == "over" and by["restaurants"].remaining_cents == -15000
    assert by["courses"].status == "ok"
    assert f.saved_cents == 30000
    assert f.safe_to_spend_cents == 100000 - 20000  # il reste 200 € à épargner sur l'objectif de 500 €


def test_suggest_budgets() -> None:
    today = date(2026, 10, 21)
    txns = [
        Txn(i, date(2026, m, 10), -a, "X", "courses") for i, (m, a) in enumerate([(7, 31000), (8, 28800), (9, 30100)])
    ]
    txns.append(Txn(10, date(2026, 9, 10), -1000, "Y", "logement"))
    assert suggest_budgets(txns, today, CATS) == {"courses": 30500}


@settings(max_examples=60, deadline=None)
@given(
    balance=st.integers(min_value=-500_000, max_value=5_000_000),
    day=st.integers(min_value=1, max_value=28),
    spend=st.lists(st.integers(min_value=1, max_value=50_000), min_size=0, max_size=60),
)
def test_forecast_invariants(balance: int, day: int, spend: list[int]) -> None:
    today = date(2026, 10, day)
    txns = [Txn(i, today - timedelta(days=1 + i % 60), -a, f"M{i % 5}", "courses") for i, a in enumerate(spend)]
    f = build_forecast(today=today, txns=txns, current_balance_cents=balance, series=[], categories=CATS)
    assert len(f.projection) == f.days_left
    assert f.lowest_balance_cents == min(p.balance_cents for p in f.projection)
    assert f.safe_to_spend_per_day_cents >= 0
    # Sans échéance attendue, le solde ne peut que baisser ou stagner.
    assert all(a.balance_cents >= b.balance_cents for a, b in zip(f.projection, f.projection[1:], strict=False))
    assert (f.overdraft_date is not None) == (f.lowest_balance_cents < 0)


def test_history_reconstructed_backwards() -> None:
    today = date(2026, 10, 5)
    txns = [
        Txn(1, date(2026, 10, 2), -1000, "A", "courses"),
        Txn(2, date(2026, 10, 5), 5000, "B", "salaire"),
        Txn(3, date(2026, 10, 1), -700, "C", "courses"),
    ]
    f = build_forecast(today=today, txns=txns, current_balance_cents=10000, series=[], categories=CATS)
    by_day = {p.date: p.balance_cents for p in f.history}
    assert [p.date.day for p in f.history] == [1, 2, 3, 4]
    assert by_day[date(2026, 10, 4)] == 5000  # avant le salaire du 5
    assert by_day[date(2026, 10, 1)] == 6000  # avant la dépense du 2
    first_of_month = date(2026, 10, 1)
    assert f.history[0].date == first_of_month


def _ev(d: date, cents: int, label: str = "X") -> UpcomingEvent:
    return UpcomingEvent(d, label, cents, "c", "income" if cents > 0 else "fixed", 1.0, late=False)


def test_allowance_waits_for_payday() -> None:
    today, end = date(2026, 10, 21), date(2026, 10, 31)
    segs = allowance_schedule(
        today=today,
        end=end,
        balance_cents=64000,
        buffer_cents=10000,
        events=[_ev(date(2026, 10, 25), -1349), _ev(date(2026, 10, 28), 215000, "BRGM")],
    )
    assert [(s.start.day, s.end.day) for s in segs] == [(21, 27), (28, 31)]
    assert segs[0].per_day_cents == (64000 - 10000 - 1349) // 7 and segs[0].reason == "BRGM"
    assert segs[1].per_day_cents > 50000


def test_allowance_zero_when_underwater() -> None:
    segs = allowance_schedule(
        today=date(2026, 10, 30), end=date(2026, 10, 31), balance_cents=-5000, buffer_cents=0, events=[]
    )
    assert all(s.per_day_cents == 0 for s in segs)


@settings(max_examples=150, deadline=None)
@given(
    balance=st.integers(min_value=0, max_value=1_000_000),
    buffer=st.integers(min_value=0, max_value=50_000),
    reserve=st.integers(min_value=0, max_value=50_000),
    raw_events=st.lists(st.tuples(st.integers(0, 15), st.integers(-200_000, 300_000)), max_size=10),
)
def test_allowance_never_breaches_cushion(
    balance: int, buffer: int, reserve: int, raw_events: list[tuple[int, int]]
) -> None:
    """Propriété clé : si l'on dépense exactement le palier chaque jour, le solde reste >= coussin
    tous les jours (et >= coussin + réserve le dernier jour) — dès que c'était atteignable sans rien dépenser."""
    today = date(2026, 10, 16)
    end = date(2026, 10, 31)
    events = [_ev(today + timedelta(days=o), c) for o, c in raw_events if c != 0]
    segs = allowance_schedule(
        today=today, end=end, balance_cents=balance, events=events, buffer_cents=buffer, reserve_at_end_cents=reserve
    )
    assert segs[0].start == today and segs[-1].end == end
    assert all(a.end + timedelta(days=1) == b.start for a, b in pairwise(segs))
    # Paliers non décroissants (sauf quand on est déjà sous l'eau, tout est à 0)
    flows: dict[date, int] = {}
    for e in events:
        flows[e.date] = flows.get(e.date, 0) + e.amount_cents
    per_day = {}
    for s in segs:
        d = s.start
        while d <= s.end:
            per_day[d] = s.per_day_cents
            d += timedelta(days=1)
    bal, zero_spend_bal, feasible = balance, balance, True
    d = today
    while d <= end:
        zero_spend_bal += flows.get(d, 0)
        floor = buffer + (reserve if d == end else 0)
        feasible = feasible and zero_spend_bal >= floor
        bal += flows.get(d, 0) - per_day[d]
        if feasible:
            assert bal >= floor, (d, bal, floor)
        d += timedelta(days=1)
