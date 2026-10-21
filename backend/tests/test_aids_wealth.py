from datetime import date
from decimal import Decimal

import pytest

from app.domain.aids import AidPayment, classify_aid, compute_gaps, income_category_for, summarize_aids
from app.domain.recurring import add_months
from app.domain.transfers import Movement, looks_like_transfer, match_internal_transfers
from app.domain.wealth import (
    AccountBalance,
    emergency_fund,
    load_regulated_products,
    monthly_needed,
    months_to_goal,
    net_worth,
    project_savings,
    regulated_status,
)


@pytest.mark.parametrize(
    ("label", "amount", "kind"),
    [
        ("VIR CAF DU LOIRET PRIME ACTIVITE 092026", 25829, "caf_ppa"),
        ("VIR SEPA RECU /DE C.A.F DU LOIRET /MOTIF PPA 09/2026", 25829, "caf_ppa"),
        ("VIR CAF DU LOIRET APL 092026", 3188, "caf_logement"),
        ("VIREMENT CAISSE D ALLOCATIONS FAMILIALES RSA", 57349, "caf_rsa"),
        ("VIR CAF LOIRET ALLOCATIONS FAMILIALES", 14800, "caf_af"),
        ("VIR CAF LOIRET 102026", 10000, "caf_autre"),
        ("VIR FRANCE TRAVAIL ARE 09/2026", 116900, "ft_are"),
        ("VIR POLE EMPLOI", 20000, "ft_autre"),
        ("VIR CPAM DU LOIRET REMBOURSEMENT SOINS", 1890, "cpam_rembt"),
        ("VIR DGFIP REMBOURSEMENT IMPOT", 31200, "dgfip_rembt"),
        ("VIR CARSAT PENSION", 120000, "retraite_pension"),
        ("VIR CAF DU LOIRET", -100, None),  # un débit n'est pas une aide
        ("CB CAFE DE LA GARE", 380, None),
        ("PRLV DGFIP IMPOT", -5000, None),
        # « REMBOURSEMENT » contient « BOURSE » : un remboursement de frais n'est pas une bourse d'études
        ("REMBOURSEMENT FRAIS COMMISSION", 1790, None),
        ("VIR BOURSE DIRECT RETRAIT", 50000, None),
        ("VIR CROUS PARIS BOURSE", 45000, "crous_bourse"),
        ("VIR BOURSE SUR CRITERES SOCIAUX", 45000, "crous_bourse"),
    ],
)
def test_classify_aid(label: str, amount: int, kind: str | None) -> None:
    assert classify_aid(label, amount) == kind


def test_income_category() -> None:
    assert income_category_for("caf_ppa") == "aides"
    assert income_category_for("cpam_rembt") == "remboursements"


def ppa_stream(today: date, months: int, amount: int = 25829, skip_last: bool = False) -> list[AidPayment]:
    out = []
    for i in range(1 if skip_last else 0, months):
        out.append(AidPayment(add_months(today.replace(day=1), -i, 5), amount, "caf_ppa"))
    return out


def test_summary_ok_and_next_expected() -> None:
    today = date(2026, 10, 21)
    s = summarize_aids(ppa_stream(today, 6), today)
    [stream] = s.streams
    assert stream.status == "ok" and stream.next_expected == date(2026, 11, 5)
    assert stream.average_3m_cents == 25829 and s.total_last_12m_cents == 6 * 25829
    assert stream.portal.startswith("https://")


def test_summary_late_payment_alert() -> None:
    today = date(2026, 10, 12)
    s = summarize_aids(ppa_stream(today, 5, skip_last=True), today)
    [stream] = s.streams
    assert stream.status == "late" and "non reçu" in (stream.message or "")
    assert s.alerts


def test_summary_amount_change_and_ended() -> None:
    today = date(2026, 10, 21)
    pays = [*ppa_stream(today, 4)[1:], AidPayment(date(2026, 10, 5), 15000, "caf_ppa")]
    [stream] = summarize_aids(pays, today).streams
    assert stream.status == "changed" and "baisse" in (stream.message or "")
    old = [
        AidPayment(date(2026, 5, 5), 3000, "caf_logement"),
        AidPayment(date(2026, 6, 5), 3000, "caf_logement"),
    ]
    [ended] = summarize_aids(old, today).streams
    assert ended.status == "ended"


def test_gaps_non_recours_and_indu() -> None:
    today = date(2026, 10, 21)
    streams = summarize_aids(
        [
            *ppa_stream(today, 6),
            AidPayment(date(2026, 10, 5), 3188, "caf_logement"),
            AidPayment(date(2026, 9, 5), 3188, "caf_logement"),
            AidPayment(date(2026, 8, 5), 3188, "caf_logement"),
        ],
        today,
    ).streams
    gaps = compute_gaps({"ppa": 25829, "aide_logement": 0, "rsa": 0, "af": 14800}, streams)
    kinds = {g.kind: g for g in gaps}
    assert "caf_ppa" not in kinds  # perçu = estimé
    assert kinds["caf_af"].gap_cents == 14800 and "Droit estimé" in kinds["caf_af"].message
    assert kinds["caf_logement"].gap_cents == -3188 and "indu" in kinds["caf_logement"].message
    over = compute_gaps({"ppa": 1569}, streams)
    assert over[0].kind == "caf_ppa" and over[0].gap_cents == 1569 - 25829 and "rappel" in over[0].message


# --------------------------------------------------------------------------- virements internes
def test_internal_transfers() -> None:
    d = date(2026, 10, 1)
    moves = [
        Movement(1, 1, d, -30000),
        Movement(2, 2, date(2026, 10, 2), 30000),  # courant -> livret
        Movement(3, 1, d, -3000, looks_like_transfer=False),
        Movement(4, 2, d, 3000, looks_like_transfer=False),
        Movement(5, 1, d, -1000),
        Movement(6, 1, d, 1000),  # même compte
        Movement(7, 1, d, -500),
        Movement(8, 2, date(2026, 10, 9), 500),  # trop éloignés
    ]
    assert match_internal_transfers(moves) == [(1, 2)]
    assert looks_like_transfer("VIR VERS LIVRET A") and not looks_like_transfer("CB CARREFOUR")


# --------------------------------------------------------------------------- patrimoine
ACCOUNTS = [
    AccountBalance(1, "Courant", "checking", -12000),
    AccountBalance(2, "Livret A", "livret_a", 2_400_000),
    AccountBalance(3, "LEP", "lep", 500_000),
    AccountBalance(4, "PEA", "pea", 1_000_000),
    AccountBalance(5, "Prêt auto", "credit", -800_000),
]


def test_net_worth() -> None:
    nw = net_worth(ACCOUNTS)
    assert nw.assets_cents == 3_900_000 and nw.liabilities_cents == 812_000
    assert nw.net_cents == 3_088_000
    assert nw.by_bucket["epargne_disponible"] == 2_900_000 and nw.by_bucket["dettes"] == -812_000


def test_emergency_fund_levels() -> None:
    assert emergency_fund(ACCOUNTS, 150_000).level == "confortable"
    ef = emergency_fund(ACCOUNTS, 1_000_000)
    assert ef.level == "insuffisant" and ef.months_covered == 2.9
    assert emergency_fund([], 0).months_covered == 0.0


def test_regulated_products() -> None:
    products = load_regulated_products()
    assert {p.kind for p in products} == {"livret_a", "ldds", "lep"}
    status = {s.kind: s for s in regulated_status(ACCOUNTS, products)}
    assert status["livret_a"].at_ceiling and status["livret_a"].room_cents == 0
    assert status["livret_a"].yearly_interest_cents == round(2_295_000 * 0.017)
    assert status["lep"].room_cents == 500_000 and not status["lep"].at_ceiling


def test_projection_exact_values() -> None:
    flat = project_savings(0, 10_000, Decimal(0), 2, inflation_percent=Decimal(0))
    assert [p.nominal_cents for p in flat] == [120_000, 240_000]
    assert flat[-1].interest_cents == 0
    compound = project_savings(100_000, 0, Decimal(12), 1, inflation_percent=Decimal(0))
    assert compound[0].nominal_cents == round(100_000 * 1.01**12)
    real = project_savings(100_000, 0, Decimal(0), 1, inflation_percent=Decimal(2))
    assert real[0].real_cents == round(100_000 / 1.02)
    with pytest.raises(ValueError, match="horizon"):
        project_savings(0, 0, Decimal(1), 0)


def test_goal_helpers() -> None:
    assert months_to_goal(100, 50, 0, Decimal(0)) == 0
    assert months_to_goal(0, 100_000, 10_000, Decimal(0)) == 10
    assert months_to_goal(0, 100, 0, Decimal(0)) is None
    assert monthly_needed(0, 120_000, date(2027, 10, 1), date(2026, 10, 21)) == 10_000
    assert monthly_needed(0, 100, date(2026, 1, 1), date(2026, 10, 21)) == 100


def test_refunds_once_taken_for_scholarships_are_repaired(app, world) -> None:  # type: ignore[no-untyped-def]
    """Une ancienne règle prenait « REMBOURSEMENT » pour une bourse : au démarrage, ces opérations sont relues."""
    from datetime import date as day

    from sqlalchemy import select

    from app.models import Account, Category, CategorySource, Transaction
    from app.services.ingest import repair_aid_kinds

    with app.state.sessionmaker() as db:
        account = db.scalars(select(Account).where(Account.kind == "checking")).one()
        aides = db.scalars(select(Category).where(Category.slug == "aides")).one()
        wrong = Transaction(account_id=account.id, booking_date=day(2026, 10, 20), amount_cents=1_790,
                            raw_label="REMBOURSEMENT FRAIS COMMISSION", merchant="REMBOURSEMENT FRAIS COMMISSION",
                            fingerprint="x" * 64, aid_kind="crous_bourse", category_id=aides.id,
                            category_source=CategorySource.BUILTIN)  # fmt: skip
        mine = Transaction(account_id=account.id, booking_date=day(2026, 10, 20), amount_cents=2_000,
                           raw_label="REMBOURSEMENT AMI", merchant="REMBOURSEMENT AMI", fingerprint="y" * 64,
                           aid_kind="crous_bourse", category_id=aides.id, category_source=CategorySource.MANUAL)  # fmt: skip
        real = Transaction(account_id=account.id, booking_date=day(2026, 10, 5), amount_cents=45_000,
                           raw_label="VIR CROUS PARIS BOURSE", merchant="CROUS PARIS", fingerprint="z" * 64,
                           aid_kind="crous_bourse", category_id=aides.id, category_source=CategorySource.BUILTIN)  # fmt: skip
        db.add_all([wrong, mine, real])
        db.flush()
        assert repair_aid_kinds(db) == 2
        assert wrong.aid_kind is None and wrong.category_id != aides.id
        assert mine.aid_kind is None and mine.category_id == aides.id  # ton choix reste le tien
        assert real.aid_kind == "crous_bourse"
        assert repair_aid_kinds(db) == 0  # idempotent
