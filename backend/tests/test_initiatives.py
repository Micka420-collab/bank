"""Initiatives : Bank demande une chose à la fois, au bon moment, et n'insiste pas."""

from datetime import date, timedelta

from app.domain.initiatives import (
    MAX_QUESTION_CARDS,
    ConnectionInfo,
    Context,
    EmergencyInfo,
    GoalInfo,
    MerchantGroup,
    MonthReview,
    PlanningInfo,
    SalaryInfo,
    SeriesInfo,
    State,
    build_cards,
    card_to_dict,
    eur,
)
from app.domain.onboarding import QUESTIONS, Suggestion

TODAY = date(2026, 10, 3)


def ctx(**over: object) -> Context:
    base: dict[str, object] = {
        "today": TODAY,
        "has_transactions": True,
        "months_of_data": 2.0,
        "questions": QUESTIONS,
        "answers": {},
        "suggestions": [],
        "totp_enabled": True,
    }
    base.update(over)
    return Context(**base)  # type: ignore[arg-type]


def keys(cards: list) -> list[str]:  # type: ignore[type-arg]
    return [c.key for c in cards]


def test_without_transactions_bank_asks_for_data_first() -> None:
    cards = build_cards(ctx(has_transactions=False, months_of_data=0))
    assert cards[0].key == "start:data" and cards[0].actions[0].effect == {"type": "navigate", "page": "connecteurs"}


def test_detected_facts_become_one_tap_questions_with_a_fix_input() -> None:
    sugg = [
        Suggestion("net_monthly_income", 192_000, "Médiane de tes salaires détectés sur 4 mois"),
        Suggestion("housing_cost", 65_000, "Prélèvement mensuel SCI LES LILAS"),
        Suggestion("housing_status", "locataire", "Déduit de ton profil"),
    ]
    cards = build_cards(ctx(suggestions=sugg))
    fact = next(c for c in cards if c.key == "fact:net_monthly_income")
    assert fact.title == "Ton salaire net, c'est bien 1 920 € par mois ?" and fact.evidence and fact.kind == "question"
    assert fact.actions[0].effect == {"type": "answer", "question_id": "net_monthly_income", "value": 192_000}
    assert fact.actions[1].effect == {"type": "answer", "question_id": "net_monthly_income", "from_input": True}
    assert fact.input == {"kind": "money"} and fact.why
    housing = next(c for c in cards if c.key == "fact:housing_status")
    assert housing.title == "Tu es locataire ?" and housing.input is not None and housing.input["kind"] == "choice"
    # Les questions déjà couvertes par un fait détecté ne sont pas posées en plus.
    assert "ask:net_monthly_income" not in keys(cards)


def test_questions_come_one_by_one_by_weight_with_why_and_inline_input() -> None:
    cards = build_cards(ctx())
    asks = [c for c in cards if c.key.startswith("ask:")]
    assert len(asks) == MAX_QUESTION_CARDS
    weights = [next(q.weight for q in QUESTIONS if q.id == c.key[4:]) for c in asks]
    assert weights == sorted(weights, reverse=True) and all(c.why for c in asks)
    employment = next(c for c in asks if c.key == "ask:employment")
    assert employment.input is not None and employment.input["kind"] == "choice"
    assert [a.effect["type"] for a in employment.actions] == ["answer", "snooze", "dismiss"]
    # Répondue : elle disparaît, la suivante prend sa place.
    after = build_cards(ctx(answers={"employment": "cdi_prive"}))
    assert (
        "ask:employment" not in keys(after)
        and len([c for c in after if c.key.startswith("ask:")]) == MAX_QUESTION_CARDS
    )


def test_states_hide_cards_until_they_should_come_back() -> None:
    states = {
        "ask:employment": State("snoozed", until=TODAY + timedelta(days=2)),
        "ask:net_monthly_income": State("dismissed"),
        "security:totp": State("snoozed", until=TODAY),  # échéance atteinte : revient aujourd'hui
    }
    cards = build_cards(ctx(states=states, totp_enabled=False))
    assert "ask:employment" not in keys(cards) and "ask:net_monthly_income" not in keys(cards)
    assert "security:totp" in keys(cards)
    later = build_cards(ctx(states=states, totp_enabled=False, today=TODAY + timedelta(days=2)))
    assert "ask:employment" in keys(later)


def test_recurring_outflows_are_acknowledged_once_and_quiet_categories_skipped() -> None:
    series = [
        SeriesInfo(
            "NETFLIX|out", "NETFLIX", -1_799, "monthly", 5, "loisirs", 0.9, date(2026, 9, 28), date(2026, 10, 28)
        ),
        SeriesInfo(
            "SCI LILAS|out", "SCI LILAS", -65_000, "monthly", 6, "logement", 0.95, date(2026, 10, 1), date(2026, 11, 1)
        ),
        SeriesInfo("CAFE|out", "CAFE", -150, "weekly", 8, "sorties", 0.9, date(2026, 10, 1), date(2026, 10, 8)),
        SeriesInfo("DOUTEUX|out", "DOUTEUX", -3_000, "monthly", 3, "divers", 0.4, date(2026, 10, 1), date(2026, 11, 1)),
    ]
    cards = build_cards(ctx(series=series))
    assert keys([c for c in cards if c.key.startswith("series:")]) == ["series:NETFLIX|out"]
    card = next(c for c in cards if c.key == "series:NETFLIX|out")
    assert card.title == "NETFLIX : 17,99 € chaque mois, c'est voulu ?" and "5 fois" in card.body
    assert [a.effect for a in card.actions] == [
        {"type": "ack", "note": "voulu"},
        {"type": "ack", "note": "terminé"},
        {"type": "ack", "note": "inconnu"},
    ]
    assert "series:NETFLIX|out" not in keys(
        build_cards(ctx(series=series, states={"series:NETFLIX|out": State("done", note="voulu")}))
    )


def test_uncategorized_merchants_get_a_category_picker() -> None:
    groups = [
        MerchantGroup("LECLERC ORLEANS", 12, -31_200, date(2026, 10, 1)),
        MerchantGroup("RARE", 2, -500, date(2026, 10, 1)),
    ]
    cards = build_cards(ctx(uncategorized=groups, categories=[("courses", "Courses"), ("loisirs", "Loisirs")]))
    card = next(c for c in cards if c.key.startswith("merchant:"))
    assert card.key == "merchant:LECLERC ORLEANS" and "12 opérations" in card.title and "-312 €" in card.title
    assert card.input == {
        "kind": "category",
        "options": [{"value": "courses", "label": "Courses"}, {"value": "loisirs", "label": "Loisirs"}],
        "default": None,
    }
    assert card.actions[0].effect == {"type": "rule", "merchant": "LECLERC ORLEANS", "from_input": True}
    assert "merchant:RARE" not in keys(cards)
    # Une enveloppe proposée par l'assistant est préselectionnée et annoncée, jamais appliquée d'office.
    hinted = [MerchantGroup("LECLERC ORLEANS", 12, -31_200, date(2026, 10, 1), "courses", "Courses")]
    card = next(
        c
        for c in build_cards(ctx(uncategorized=hinted, categories=[("courses", "Courses")]))
        if c.key.startswith("merchant:")
    )
    assert (
        card.input is not None and card.input["default"] == "courses" and "L'assistant propose « Courses »" in card.body
    )


def test_salary_change_is_noticed_only_when_meaningful() -> None:
    sal = SalaryInfo("BRGM", 198_000, 3, 28, date(2026, 10, 28), date(2026, 9, 28))
    cards = build_cards(ctx(salary=sal, answers={"net_monthly_income": 185_000}))
    card = next(c for c in cards if c.key.startswith("salary:"))
    assert "augmenté" in card.title and "1 980 €" in card.title and "1 850 €" in card.title
    assert card.actions[0].effect["value"] == 198_000
    close = build_cards(ctx(salary=sal, answers={"net_monthly_income": 196_000}))
    assert not [c for c in close if c.key.startswith("salary:")]


def test_month_review_proposes_a_savings_goal_in_the_first_week_only() -> None:
    review = MonthReview("2026-09", 30, 210_000, 150_000, 55_000)
    cards = build_cards(ctx(month_review=review))
    card = next(c for c in cards if c.key == "review:2026-09")
    assert card.kind == "question" and card.title.startswith("Septembre : il t'est resté 600 €")
    assert card.actions[0].effect == {
        "type": "setting",
        "key": "savings_goal_cents",
        "value": 27_000,
    }  # 55 000 / 2 arrondi à 10 €
    assert card.input == {"kind": "money", "placeholder": "270"}
    # Déjà une épargne au moins égale : simple bonne nouvelle.
    nice = next(
        c for c in build_cards(ctx(month_review=review, savings_goal_cents=30_000)) if c.key == "review:2026-09"
    )
    assert nice.kind == "notice" and "Beau mois" in nice.body
    # Mois déficitaire : constat, sans culpabiliser.
    neg = next(
        c
        for c in build_cards(ctx(month_review=MonthReview("2026-09", 30, 150_000, 171_000, -5_000)))
        if c.key == "review:2026-09"
    )
    assert neg.kind == "notice" and "210 € de plus que reçu" in neg.title and "Ça arrive" in neg.body
    assert "review:2026-09" not in keys(build_cards(ctx(month_review=review, today=date(2026, 10, 9))))
    assert "review:2026-09" not in keys(build_cards(ctx(month_review=MonthReview("2026-09", 12, 1, 1, 1))))


def test_payday_tip_needs_a_recent_salary_and_a_goal() -> None:
    sal = SalaryInfo("BRGM", 190_000, 3, 28, date(2026, 10, 28), date(2026, 10, 2))
    assert "payday:2026-10" in keys(build_cards(ctx(salary=sal, paid_recently=True, savings_goal_cents=15_000)))
    assert "payday:2026-10" not in keys(build_cards(ctx(salary=sal, paid_recently=True, savings_goal_cents=0)))
    assert "payday:2026-10" not in keys(build_cards(ctx(salary=sal, paid_recently=False, savings_goal_cents=15_000)))


def test_celebrations_and_hygiene() -> None:
    cards = build_cards(
        ctx(
            months_of_data=3.2,
            emergency=EmergencyInfo(4.0, 3, 420_000),
            goals=[GoalInfo(7, "Vélo", 1.0), GoalInfo(8, "Japon", 0.4)],
            totp_enabled=False,
            connections=[
                ConnectionInfo(1, "BoursoBank", TODAY + timedelta(days=5)),
                ConnectionInfo(2, "CA", TODAY + timedelta(days=60)),
            ],
            energy_connected=True,
            energy_days_since_data=12,
            salary=SalaryInfo("BRGM", 190_000, 4, 28, date(2026, 10, 28), date(2026, 9, 28)),
            latest_tax_period=2024,
        )
    )
    k = keys(cards)
    assert k[:3] == ["celebrate:3mois", "celebrate:goal:7", "celebrate:matelas"]  # les bonnes nouvelles d'abord
    assert "celebrate:goal:8" not in k
    assert "consent:1:20261008" in k and not any(x.startswith("consent:2") for x in k)
    assert "energy:stale" in k and "security:totp" in k and "doc:avis:2025" in k
    consent = next(c for c in cards if c.key.startswith("consent:1"))
    assert consent.priority < next(c for c in cards if c.key == "security:totp").priority
    first_doc = build_cards(
        ctx(salary=SalaryInfo("BRGM", 190_000, 4, 28, date(2026, 10, 28), date(2026, 9, 28)), latest_tax_period=None)
    )
    assert "doc:avis:first" in keys(first_doc)


def test_planning_cards_invite_then_ask_rules_then_announce_the_pay() -> None:
    sal = SalaryInfo("BRGM", 190_000, 4, 28, date(2026, 10, 28), date(2026, 9, 28))
    invite = next(c for c in build_cards(ctx(salary=sal, planning=None)) if c.key == "planning:connect")
    assert "BRGM" in invite.body and "28" in invite.body and "1 900 €" in invite.body
    rules = build_cards(ctx(salary=sal, planning=PlanningInfo(True, False)))
    assert "planning:rules" in keys(rules) and "planning:connect" not in keys(rules)
    pay = build_cards(
        ctx(
            salary=sal,
            planning=PlanningInfo(True, True, date(2026, 10, 28), 226_000, 190_000, (("astreinte", 2, 36_000),)),
        )
    )
    card = next(c for c in pay if c.key.startswith("planning:pay:"))
    assert (
        card.title == "Paie du 28 octobre : 2 260 € prévus (+360 €)"
        and "2 × astreinte" in card.body
        and card.priority < 10
    )
    same = build_cards(ctx(salary=sal, planning=PlanningInfo(True, True, date(2026, 10, 28), 191_000, 190_000)))
    assert not [c for c in same if c.key.startswith("planning:pay:")]


def test_cards_serialize_for_the_screen_and_money_formats() -> None:
    card = build_cards(ctx(totp_enabled=False))[-1]
    d = card_to_dict(card)
    assert set(d) == {"key", "kind", "priority", "title", "body", "why", "evidence", "topic", "input", "actions"}
    assert d["actions"][0]["effect"]["type"] == "navigate"
    assert eur(123_456) == "1 234,56 €" and eur(-5_000) == "-50 €" and eur(0) == "0 €"
