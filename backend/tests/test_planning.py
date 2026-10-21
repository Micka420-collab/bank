"""Planning de travail -> paie prévue : lecture iCal, règles de paie, apprentissage par régression."""

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.planning import (
    MAX_ICS_BYTES,
    PayModel,
    PayObservation,
    PayRule,
    PlanningError,
    Shift,
    learn,
    model_from_dict,
    model_to_dict,
    parse_ics,
    period_bounds,
    predict,
    validate_rules,
)


def ics(*events: str) -> bytes:
    body = "\n".join(events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//\n{body}\nEND:VCALENDAR\n".encode()


def vevent(uid: str, start: str, end: str, summary: str, extra: str = "") -> str:
    return f"BEGIN:VEVENT\nUID:{uid}\nDTSTART{start}\nDTEND{end}\nSUMMARY:{summary}\n{extra}END:VEVENT"


WINDOW = (date(2026, 10, 1), date(2026, 11, 30))


def test_parse_simple_timed_and_all_day_events() -> None:
    data = ics(
        vevent("a", ";TZID=Europe/Paris:20261005T080000", ";TZID=Europe/Paris:20261005T153000", "Travail bureau"),
        vevent("b", ";VALUE=DATE:20261012", ";VALUE=DATE:20261019", "Astreinte semaine 42"),  # 7 jours (fin exclusive)
        vevent("c", ";VALUE=DATE:20260901", ";VALUE=DATE:20260902", "Hors fenêtre"),
    )
    shifts = parse_ics(data, window_start=WINDOW[0], window_end=WINDOW[1])
    timed = [s for s in shifts if s.title == "Travail bureau"]
    assert len(timed) == 1 and timed[0].hours == 7.5 and not timed[0].all_day and timed[0].day == date(2026, 10, 5)
    oncall = [s for s in shifts if s.title.startswith("Astreinte")]
    assert [s.day.day for s in oncall] == [12, 13, 14, 15, 16, 17, 18] and all(s.all_day for s in oncall)
    assert all(s.start_day == date(2026, 10, 12) for s in oncall)  # une seule occurrence, sept journées
    assert not any(s.title == "Hors fenêtre" for s in shifts)


def test_parse_expands_weekly_rule_with_exceptions_and_overrides() -> None:
    master = vevent(
        "w",
        ";TZID=Europe/Paris:20261005T090000",
        ";TZID=Europe/Paris:20261005T170000",
        "Travail",
        "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;UNTIL=20261031T000000Z\nEXDATE;TZID=Europe/Paris:20261012T090000\n",
    )
    override = vevent(
        "w",
        ";TZID=Europe/Paris:20261013T140000",
        ";TZID=Europe/Paris:20261013T180000",
        "Travail (après-midi)",
        "RECURRENCE-ID;TZID=Europe/Paris:20261013T090000\n",
    )
    shifts = parse_ics(ics(master, override), window_start=date(2026, 10, 1), window_end=date(2026, 10, 31))
    days = sorted(s.day for s in shifts)
    assert date(2026, 10, 12) not in days  # EXDATE
    assert days.count(date(2026, 10, 13)) == 1  # l'occurrence modifiée remplace celle de la règle
    assert next(s for s in shifts if s.day == date(2026, 10, 13)).hours == 4.0
    assert len(days) == 19  # 20 jours ouvrés du 5 au 30 octobre, moins le 12
    assert all(s.hours == 8.0 for s in shifts if s.day != date(2026, 10, 13))


def test_parse_ignores_cancelled_and_free_events_and_crosses_midnight() -> None:
    data = ics(
        vevent("n", ";TZID=Europe/Paris:20261010T220000", ";TZID=Europe/Paris:20261011T060000", "Nuit"),
        vevent("x", ";VALUE=DATE:20261020", ";VALUE=DATE:20261021", "Annulé", "STATUS:CANCELLED\n"),
        vevent("t", ";VALUE=DATE:20261022", ";VALUE=DATE:20261023", "Dispo", "TRANSP:TRANSPARENT\n"),
    )
    shifts = parse_ics(data, window_start=WINDOW[0], window_end=WINDOW[1])
    assert [(s.day.day, s.hours) for s in shifts] == [(10, 2.0), (11, 6.0)]


def test_parse_rejects_garbage_and_huge_files_and_infinite_rules_stay_bounded() -> None:
    with pytest.raises(PlanningError, match="illisible"):
        parse_ics(b"not an ics", window_start=WINDOW[0], window_end=WINDOW[1])
    with pytest.raises(PlanningError, match="volumineux"):
        parse_ics(b"BEGIN:VCALENDAR" + b" " * MAX_ICS_BYTES, window_start=WINDOW[0], window_end=WINDOW[1])
    daily_forever = vevent("d", ";VALUE=DATE:20200101", ";VALUE=DATE:20200102", "Chaque jour", "RRULE:FREQ=DAILY\n")
    shifts = parse_ics(ics(daily_forever), window_start=date(2026, 10, 1), window_end=date(2026, 10, 31))
    assert len(shifts) == 31  # seule la fenêtre est développée, jamais la règle entière


# ------------------------------------------------------------------ règles et prévision
def day_shift(d: date, title: str, hours: float = 0.0, uid: str = "", start: date | None = None) -> Shift:
    return Shift(d, hours, hours == 0, title, uid or title, start or d)


def test_prediction_adds_rules_to_the_fixed_part() -> None:
    shifts = [
        *[day_shift(date(2026, 10, d), "Astreinte", uid="a1", start=date(2026, 10, 12)) for d in range(12, 19)],
        *[day_shift(date(2026, 10, d), "Astreinte", uid="a2", start=date(2026, 10, 26)) for d in range(26, 31)],
        day_shift(date(2026, 10, 3), "Heures sup", 3.0),
        day_shift(date(2026, 10, 17), "Heures sup", 2.5),
        day_shift(date(2026, 11, 2), "Astreinte", uid="a3"),  # mois suivant : hors période
    ]
    model = PayModel(
        190_000,
        (
            PayRule("astreinte", "per_event", 18_000),
            PayRule("heures sup", "per_hour", 2_200),
            PayRule("*", "per_day", 0),
        ),
    )
    p = predict(model, shifts, date(2026, 10, 28))
    assert (p.period_start, p.period_end) == (date(2026, 10, 1), date(2026, 10, 31))
    by = {e.pattern: e for e in p.extras}
    assert by["astreinte"].count == 2 and by["astreinte"].total_cents == 36_000
    assert by["heures sup"].count == 6 and by["heures sup"].total_cents == round(2_200 * 5.5)
    assert p.amount_cents == 190_000 + 36_000 + 12_100
    assert p.worked_days == 13 and p.shifts_in_period == 14  # le 17 porte deux événements
    # Paie à terme échu : la paie de novembre couvre octobre.
    late = predict(PayModel(190_000, model.rules, offset_months=1), shifts, date(2026, 11, 28))
    assert late.amount_cents == p.amount_cents and late.period_start == date(2026, 10, 1)
    assert period_bounds(date(2027, 1, 15), 1) == (date(2026, 12, 1), date(2026, 12, 31))


def test_learning_recovers_base_and_rule_amounts_from_past_pays() -> None:
    rules = (PayRule("astreinte", "per_event", 0), PayRule("nuit", "per_hour", 0))
    shifts: list[Shift] = []
    observations: list[PayObservation] = []
    plan = {5: (1, 8.0), 6: (2, 0.0), 7: (0, 16.0), 8: (1, 4.0), 9: (3, 8.0), 10: (0, 0.0)}
    for month, (oncalls, night_hours) in plan.items():
        for i in range(oncalls):
            shifts.append(day_shift(date(2026, month, 3 + i), "Astreinte", uid=f"a{month}{i}"))
        if night_hours:
            shifts.append(day_shift(date(2026, month, 20), "Nuit", night_hours))
        truth = 185_000 + 17_500 * oncalls + round(2_300 * night_hours)
        observations.append(PayObservation(date(2026, month, 28), truth + (37 if month % 2 else -41)))  # bruit
    learned = learn(rules, shifts, observations)
    assert learned is not None and learned.periods == 6
    assert abs(learned.model.base_cents - 185_000) < 150
    by = {r.pattern: r for r in learned.model.rules}
    assert abs(by["astreinte"].amount_cents - 17_500) < 150 and by["astreinte"].learned
    assert abs(by["nuit"].amount_cents - 2_300) < 60 and by["nuit"].learned
    assert learned.residual_cents < 100 and "6 paies" in learned.note


def test_learning_needs_history_and_keeps_unidentifiable_rules_declared() -> None:
    rules = (PayRule("astreinte", "per_event", 15_000),)
    obs = [PayObservation(date(2026, m, 28), 200_000) for m in (6, 7, 8)]
    assert learn(rules, [], obs) is None  # 3 paies : trop peu
    obs.append(PayObservation(date(2026, 9, 28), 200_000))
    learned = learn(rules, [], obs)  # jamais d'astreinte : la règle garde son montant déclaré
    assert learned is not None and learned.model.base_cents == 200_000
    assert learned.model.rules[0].amount_cents == 15_000 and not learned.model.rules[0].learned
    # Toujours exactement une astreinte : indiscernable de la part fixe -> base = paie - 150 € déclarés.
    shifts = [day_shift(date(2026, m, 5), "Astreinte", uid=f"a{m}") for m in (6, 7, 8, 9)]
    obs2 = [PayObservation(date(2026, m, 28), 215_000) for m in (6, 7, 8, 9)]
    learned2 = learn(rules, shifts, obs2)
    assert learned2 is not None and learned2.model.base_cents == 200_000


def test_rules_validation_and_model_round_trip() -> None:
    rules = validate_rules(
        [
            {"pattern": "Astreinte", "unit": "per_event", "amount_cents": 18000},
            {"pattern": "*", "unit": "per_day", "amount_cents": 0},
        ]
    )
    assert rules[0].matches(day_shift(date(2026, 10, 1), "ASTREINTE réseau")) and rules[1].matches(
        day_shift(date(2026, 10, 1), "x")
    )
    assert not rules[0].matches(day_shift(date(2026, 10, 1), "Réunion"))
    for bad in (
        [{"pattern": "", "unit": "per_event", "amount_cents": 1}],
        [{"pattern": "a", "unit": "per_minute", "amount_cents": 1}],
        [{"pattern": "a", "unit": "per_event", "amount_cents": "10"}],
        [
            {"pattern": "a", "unit": "per_event", "amount_cents": 10},
            {"pattern": "A", "unit": "per_day", "amount_cents": 1},
        ],
        [{"pattern": "<script>", "unit": "per_event", "amount_cents": 1}],
        [{"pattern": "a", "unit": "per_event", "amount_cents": 1}] * 9,
    ):
        with pytest.raises(PlanningError):
            validate_rules(bad)
    model = PayModel(190_000, rules, 1, base_learned=True)
    assert model_from_dict(model_to_dict(model)) == model
    assert model_from_dict(None) is None and model_from_dict({"base_cents": -1}) is None
    assert model_from_dict({"base_cents": 1, "offset_months": 7}).offset_months == 0  # type: ignore[union-attr]


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(1, 28), st.sampled_from(["Astreinte", "Nuit", "Réunion"]), st.floats(0, 12)), max_size=40
    ),
    st.integers(0, 300_000),
    st.integers(0, 50_000),
)
def test_prediction_invariants(raw: list[tuple[int, str, float]], base: int, amount: int) -> None:
    shifts = [
        Shift(date(2026, 10, d), round(h, 2), h == 0, t, f"{t}{i}", date(2026, 10, d))
        for i, (d, t, h) in enumerate(raw)
    ]
    model = PayModel(base, (PayRule("astreinte", "per_event", amount), PayRule("*", "per_hour", amount)))
    p = predict(model, shifts, date(2026, 10, 28))
    assert p.amount_cents >= base and p.shifts_in_period == len(shifts)
    assert p.amount_cents == base + sum(e.total_cents for e in p.extras)
    assert 0 <= p.worked_days <= len({s.day for s in shifts})
