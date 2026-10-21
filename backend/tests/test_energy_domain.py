"""Linky : lecture tolérante des formats Enedis, coût, écart de mensualité, pics."""

import time
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.energy import (
    _MAX_DEPTH,
    _MAX_NODES,
    MAX_WH_PER_DAY,
    PRM_RE,
    DailyReading,
    EnergyFormatError,
    Tariff,
    energy_cost_cents,
    insights,
    is_electricity_supplier,
    monthly_breakdown,
    parse_daily,
    period_cost_cents,
    typical_monthly_payment,
)

TARIFF = Tariff(2001, 19035, "Base 6 kVA")
TODAY = date(2026, 10, 21)

LEGACY = {
    "usage_point_id": "12345678901234",
    "start": "2026-10-01",
    "end": "2026-10-04",
    "quality": "BRUT",
    "reading_type": {"unit": "Wh", "measurement_kind": "energy", "aggregate": "sum"},
    "interval_reading": [
        {"value": "12873", "date": "2026-10-01"},
        {"value": "9800", "date": "2026-10-02"},
        {"value": "11000", "date": "2026-10-03"},
    ],
}
V2_GUESS_A = {
    "pointId": "12345678901234",
    "mesures": {
        "grandeur": "CONS",
        "unite": "kWh",
        "points": [
            {"horodatage": "2026-10-01T00:00:00+02:00", "valeur": 12.873},
            {"horodatage": "2026-10-02", "valeur": "9,8"},
        ],
    },
}
V2_GUESS_B = [{"d": "2026-10-01", "v": 12873, "unit": "Wh"}, {"d": "2026-10-02", "v": 9800, "unit": "Wh"}]


@pytest.mark.parametrize("payload", [LEGACY, V2_GUESS_A, V2_GUESS_B])
def test_parse_tolerates_known_and_plausible_formats(payload: object) -> None:
    rows = parse_daily(payload)
    assert rows[0] == DailyReading(date(2026, 10, 1), 12873) and rows[1].wh == 9800
    assert [r.day for r in rows] == sorted(r.day for r in rows)


def test_parse_rejects_garbage_and_negative_values() -> None:
    for bad in ({}, [], {"error": "x"}, [{"a": 1}], "texte", None):
        with pytest.raises(EnergyFormatError):
            parse_daily(bad)
    rows = parse_daily(
        {"interval_reading": [{"value": "-5", "date": "2026-10-01"}, {"value": "7", "date": "2026-10-02"}]}
    )
    assert [r.wh for r in rows] == [7]


def test_duplicate_days_keep_the_last_value() -> None:
    rows = parse_daily([{"date": "2026-10-01", "value": 1}, {"date": "2026-10-01", "value": 2}])
    assert rows == [DailyReading(date(2026, 10, 1), 2)]


def test_costs() -> None:
    assert energy_cost_cents(100_000, TARIFF) == 2001  # 100 kWh à 0,2001 €
    assert period_cost_cents(0, 365, TARIFF) == 19035  # abonnement seul sur un an
    assert period_cost_cents(300_000, 30, TARIFF) == 6003 + 1565  # 190,35 € × 30/365 = 15,65 €


def days(n: int, wh: int, end: date = TODAY - timedelta(days=1)) -> list[DailyReading]:
    return [DailyReading(end - timedelta(days=i), wh) for i in range(n)][::-1]


def test_insights_full_year_with_regularisation_and_last_year_comparison() -> None:
    rows = days(420, 10_000)
    # Septembre 2025 plus gourmand que septembre 2026 : le dernier mois complet se compare à l'an dernier.
    rows = [DailyReading(r.day, 15_000) if r.day.year == 2025 and r.day.month == 9 else r for r in rows]
    ins = insights(rows, today=TODAY, tariff=TARIFF, monthly_payment_cents=5_000)
    assert ins.days_of_data == 420 and not ins.stale and not ins.yearly_is_extrapolated
    assert ins.last_30_days_wh == 300_000 and ins.daily_average_wh == 10_000
    assert ins.same_month_last_year_wh == 450_000 and ins.change_vs_last_year_percent == -33
    assert ins.yearly_estimate_wh == 3_650_000
    assert ins.yearly_estimate_cents == 73_036 + 19_035  # 3 650 kWh × 0,2001 € = 730,365 € -> 730,36 €
    assert ins.recommended_monthly_cents == 7_673 and ins.regularisation_cents == 92_071 - 60_000
    assert ins.spike is False and ins.spike_ratio == 1.0
    assert ins.months[-1].month == "2026-10" and ins.months[-1].days == 20


def test_insights_extrapolate_short_history_and_detect_spike() -> None:
    rows = days(40, 8_000)
    rows = rows[:-7] + [DailyReading(r.day, 14_000) for r in rows[-7:]]
    ins = insights(rows, today=TODAY, tariff=TARIFF, monthly_payment_cents=None)
    assert ins.yearly_is_extrapolated and ins.regularisation_cents is None
    assert ins.spike and ins.spike_ratio == 1.75
    assert ins.change_vs_last_year_percent is None
    stale = insights(
        days(10, 8_000, end=TODAY - timedelta(days=20)), today=TODAY, tariff=TARIFF, monthly_payment_cents=None
    )
    assert stale.stale


def test_empty_history() -> None:
    ins = insights([], today=TODAY, tariff=TARIFF, monthly_payment_cents=4_000)
    assert ins.days_of_data == 0 and ins.stale and ins.yearly_estimate_cents == 0 and ins.months == []


def test_monthly_breakdown_and_suppliers() -> None:
    rows = [
        DailyReading(date(2026, 9, 29), 1_000),
        DailyReading(date(2026, 9, 30), 1_000),
        DailyReading(date(2026, 10, 1), 2_000),
    ]
    months = monthly_breakdown(rows, TARIFF)
    assert [(m.month, m.wh, m.days) for m in months] == [("2026-09", 2_000, 2), ("2026-10", 2_000, 1)]
    assert is_electricity_supplier("EDF CLIENTS PARTICULIERS") and is_electricity_supplier("TOTALENERGIES ELEC")
    assert not is_electricity_supplier("CARREFOUR MARKET") and not is_electricity_supplier("VEOLIA EAU")
    assert typical_monthly_payment([-6_200, -6_100, -7_000, 3_000]) == 6_200
    assert typical_monthly_payment([3_000]) is None


@settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=100_000), min_size=1, max_size=400))
def test_insights_invariants(values: list[int]) -> None:
    rows = [DailyReading(TODAY - timedelta(days=len(values) - i), v) for i, v in enumerate(values)]
    ins = insights(rows, today=TODAY, tariff=TARIFF, monthly_payment_cents=5_000)
    assert ins.days_of_data == len(values)
    assert ins.last_30_days_wh == sum(values[-30:])
    assert ins.yearly_estimate_cents >= TARIFF.yearly_subscription_cents
    assert sum(m.wh for m in monthly_breakdown(rows, TARIFF)) == sum(values)
    assert ins.recommended_monthly_cents * 12 >= ins.yearly_estimate_cents


# ------------------------------------------------------------------ réponses hostiles (revue de sécurité)
def test_deeply_nested_response_is_rejected_in_bounded_time() -> None:
    """Avant : « data » dans « data » visité deux fois par niveau -> 2^40 appels. Maintenant : un seul passage borné."""
    nested: dict[str, object] = {}
    for _ in range(40):
        nested = {"data": nested}
    started = time.perf_counter()
    with pytest.raises(EnergyFormatError):
        parse_daily(nested)
    assert time.perf_counter() - started < 1.0
    # Un format valide reste reconnu jusqu'à la profondeur explorée, pas au-delà.
    series = [{"date": "2026-10-01", "value": 1}]
    shallow: object = series
    for _ in range(_MAX_DEPTH - 1):
        shallow = {"wrapper": shallow}
    assert parse_daily(shallow)[0].wh == 1
    too_deep: object = series
    for _ in range(_MAX_DEPTH + 2):
        too_deep = {"wrapper": too_deep}
    with pytest.raises(EnergyFormatError):
        parse_daily(too_deep)


def test_huge_response_is_cut_by_node_budget() -> None:
    started = time.perf_counter()
    with pytest.raises(EnergyFormatError, match="volumineuse"):
        parse_daily({"noise": [{"x": i} for i in range(_MAX_NODES + 10)], "data": [{"date": "2026-10-01", "value": 1}]})
    assert time.perf_counter() - started < 3.0
    # Un vrai relevé d'un an (365 points + métadonnées) reste très loin du budget.
    year = [{"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "value": str(1000 + i)} for i in range(365)]
    assert len(parse_daily({"reading_type": {"unit": "Wh"}, "interval_reading": year})) == 365


def test_values_must_be_finite_and_plausible() -> None:
    rows = parse_daily(
        [
            {"date": "2026-10-01", "value": float("inf")},
            {"date": "2026-10-02", "value": float("nan")},
            {"date": "2026-10-03", "value": "1e400"},
            {"date": "2026-10-04", "value": MAX_WH_PER_DAY + 1},
            {"date": "2026-10-05", "value": MAX_WH_PER_DAY},
            {"date": "2026-10-06", "value": 0},
        ]
    )
    assert [(r.day.day, r.wh) for r in rows] == [(5, MAX_WH_PER_DAY), (6, 0)]


def test_insights_ignore_readings_dated_in_the_future() -> None:
    """Un relevé daté 9999-12-31 faisait déborder la fenêtre glissante (OverflowError sur toute l'API)."""
    rows = [*days(10, 8_000), DailyReading(date(9999, 12, 31), 5), DailyReading(TODAY + timedelta(days=1), 5)]
    ins = insights(rows, today=TODAY, tariff=TARIFF, monthly_payment_cents=None)
    assert ins.days_of_data == 10 and ins.data_through == TODAY - timedelta(days=1)


def test_prm_pattern_is_strict_ascii() -> None:
    assert PRM_RE.match("12345678901234")
    for bad in ("12345678901234\n", "١٢٣٤٥٦٧٨٩٠١٢٣٤", "1234567890123", "123456789012345", " 12345678901234"):
        assert PRM_RE.match(bad) is None, repr(bad)
