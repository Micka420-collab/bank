"""Crédits : amortissement exact au centime sur un cas de référence (200 000 € à 3,5 % sur 20 ans : 1 159,92 € par
mois), capital restant dû à une date, remboursement anticipé avec les indemnités plafonnées par la loi (immobilier :
6 mois d'intérêts / 3 % ; consommation : rien sous 10 000 €, puis 1 % ou 0,5 %), renégociation ; le prêt entre dans
le patrimoine net et sa mensualité est reconnue dans les relevés."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.domain import loans as dom
from app.models import AuditLog
from tests.conftest import do_login, do_setup
from tests.scenario import TODAY, seed_household

HOUSE = dom.Loan(
    20_000_000, 350, 240, date(2026, 11, 5), insurance_monthly_cents=5_000, kind="immobilier", name="Maison"
)


def test_reference_amortization_is_exact_to_the_cent() -> None:
    s = dom.schedule(HOUSE)
    assert s.payment_cents == 115_992, (
        "1 159,92 € : le chiffre des simulateurs publics pour 200 000 € à 3,5 % sur 20 ans"
    )
    assert len(s.rows) == 240 and s.rows[-1].remaining_cents == 0
    assert sum(r.principal_cents for r in s.rows) == 20_000_000, "le capital est soldé exactement"
    assert s.total_interest_cents == 7_838_066 and s.total_insurance_cents == 240 * 5_000
    assert s.rows[0].interest_cents == 58_333 and s.rows[0].principal_cents == 57_659  # 200 000 × 3,5 % / 12
    assert s.rows[-1].payment_cents == 115_978, "la dernière échéance absorbe l'arrondi (quelques centimes)"
    assert all(r.payment_cents == 115_992 for r in s.rows[:-1])
    assert all(a.remaining_cents > b.remaining_cents for a, b in zip(s.rows, s.rows[1:], strict=False))
    assert s.rows[12].day == date(2027, 11, 5) and s.rows[-1].day == date(2046, 10, 5)
    # Sans intérêts : des douzièmes, pas un centime de plus. À 5 % sur un an : 85,61 € pour 1 000 €.
    assert dom.payment_cents(1_000_000, 0, 10) == 100_000 and dom.payment_cents(1_000_000, 500, 12) == 85_607
    zero = dom.schedule(dom.Loan(1_000_000, 0, 10, date(2026, 1, 1)))
    assert zero.total_interest_cents == 0 and zero.rows[-1].remaining_cents == 0
    y = dom.yearly(s)
    assert y[0]["year"] == 2026 and y[0]["months"] == 2 and sum(v["principal_cents"] for v in y) == 20_000_000
    assert sum(v["interest_cents"] for v in y) == s.total_interest_cents


def test_position_early_repayment_and_legal_indemnities() -> None:
    s = dom.schedule(HOUSE)
    pos = dom.position(s, date(2031, 11, 5))
    assert pos.paid_rows == 61 and pos.months_left == 179 and pos.remaining_cents == 16_156_644
    assert pos.next_payment == date(2031, 12, 5) and pos.next_payment_cents == 115_992 + 5_000
    assert 0.19 < pos.progress < 0.20
    # Immobilier : 30 000 € remboursés -> 6 mois d'intérêts (525 €) < 3 % du restant dû (4 847 €).
    e = dom.early_repayment(HOUSE, date(2031, 11, 5), 3_000_000, "shorter")
    assert e.indemnity.cents == 52_500 and e.indemnity.rule == "ira_immobilier"
    assert e.new_payment_cents == 115_992 and e.new_months_left == 138 < 179 and e.new_end == date(2043, 5, 5)
    assert e.interest_saved_cents == 1_968_541 and e.net_gain_cents == e.interest_saved_cents - 52_500
    lower = dom.early_repayment(HOUSE, date(2031, 11, 5), 3_000_000, "lower")
    assert lower.new_months_left == 179 and lower.new_payment_cents == 94_454 < 115_992
    assert 0 < lower.interest_saved_cents < e.interest_saved_cents, "garder la durée économise moins que raccourcir"
    full = dom.early_repayment(HOUSE, date(2031, 11, 5), 0, "full")
    assert full.amount_cents == 16_156_644 and full.remaining_after_cents == 0 and full.new_end is None
    assert full.indemnity.cents == min(round(16_156_644 * 0.035 / 12 * 6), round(16_156_644 * 0.03))
    # Consommation : rien sous 10 000 € sur douze mois ; au-delà 1 % (plus d'un an restant), 0,5 % sinon, jamais
    # plus que les intérêts restants.
    conso = dom.Loan(1_200_000, 650, 48, date(2026, 1, 15), kind="consommation", name="Auto")
    assert dom.schedule(conso).payment_cents == 28_458
    free = dom.early_repayment(conso, date(2026, 10, 15), 500_000)
    assert free.indemnity.cents == 0 and "10 000 €" in free.indemnity.explanation
    paid = dom.early_repayment(conso, date(2026, 10, 15), 500_000, repaid_last_12m_cents=600_000)
    assert paid.indemnity.cents == 5_000
    last_year = dom.early_repayment(conso, date(2029, 2, 15), 100_000, repaid_last_12m_cents=1_000_000)
    assert last_year.indemnity.cents <= 500 and "0,5 %" in last_year.indemnity.explanation
    capped = dom.indemnity(conso, repaid_cents=1_000_000, remaining_before_cents=1_000_000, months_left=2, interest_left_cents=300, repaid_last_12m_cents=1_000_000)  # fmt: skip
    assert capped.cents == 300, "jamais plus que les intérêts qu'il restait à payer"
    # Renégociation : même durée, nouveau taux ; le rachat par une autre banque paie l'indemnité chez l'ancienne.
    r = dom.renegotiate(HOUSE, date(2031, 11, 5), 250, fees_cents=100_000, buyout=True)
    assert r.new_payment_cents == 108_227 and r.indemnity_cents == 282_741 and r.worth_it
    assert r.net_gain_cents == r.old_interest_left_cents - r.new_interest_cents - 100_000 - 282_741
    same_bank = dom.renegotiate(HOUSE, date(2031, 11, 5), 250, fees_cents=100_000)
    assert same_bank.indemnity_cents == 0 and same_bank.net_gain_cents > r.net_gain_cents
    useless = dom.renegotiate(HOUSE, date(2031, 11, 5), 345, fees_cents=500_000)
    assert not useless.worth_it
    for bad in (
        lambda: dom.Loan(0, 350, 240, date(2026, 1, 1)),
        lambda: dom.Loan(1_000, 3_500, 240, date(2026, 1, 1)),
        lambda: dom.Loan(1_000, 350, 481, date(2026, 1, 1)),
        lambda: dom.Loan(1_000, 350, 12, date(2026, 1, 1), kind="revolving"),  # type: ignore[arg-type]
        lambda: dom.early_repayment(HOUSE, date(2031, 11, 5), 0),
        lambda: dom.early_repayment(HOUSE, date(2050, 1, 1), 100),
        lambda: dom.renegotiate(HOUSE, date(2031, 11, 5), 5_000),
    ):
        with pytest.raises(dom.LoanError):
            bad()
    ov = dom.overview(HOUSE, date(2031, 11, 5))
    assert ov.rules_recheck is True, "plus d'un an après la vérification des règles : Bank le dit"
    assert dom.overview(HOUSE, date(2026, 12, 1)).rules_recheck is False


@pytest.fixture
def world(app, client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    with app.state.sessionmaker() as db:
        seed_household(db)
    from app.api import deps

    app.dependency_overrides[deps.today] = lambda: TODAY
    return client


def test_loan_api_recognizes_the_installment_and_counts_in_net_worth(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    before = world.get("/api/wealth").json()["net_worth"]["net_cents"]
    assert world.get("/api/loans").json() == {
        **world.get("/api/loans").json(),
        "loans": [],
        "total_remaining_cents": 0,
    }
    body = {
        "name": "Prêt auto Cofidis",
        "lender": "Cofidis",
        "kind": "consommation",
        "principal_cents": 900_000,
        "annual_rate_bp": 750,
        "months": 60,
        "first_payment": "2025-11-15",
    }
    r = world.post("/api/loans", json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    loan = out["loans"][0]
    assert loan["id"] == out["created_id"] and loan["kind_label"] == "Crédit à la consommation"
    assert loan["payment_cents"] == 18_034 and loan["paid_rows"] == 12 and loan["months_left"] == 48
    assert loan["matched"] and loan["matched"]["merchant"].startswith("COFIDIS"), (
        "la mensualité des relevés est reconnue"
    )
    assert loan["matched"]["amount_cents"] == 18_000 and loan["next_payment"] == "2026-11-15"
    assert loan["remaining_cents"] < 900_000 and loan["progress"] > 0.1
    after = world.get("/api/wealth").json()
    assert after["net_worth"]["net_cents"] == before - loan["remaining_cents"], (
        "le restant dû est une dette du patrimoine"
    )
    debt_line = next(a for a in after["accounts"] if a["kind"] == "credit")
    assert debt_line["name"] == "Prêt auto Cofidis" and debt_line["balance_cents"] == -loan["remaining_cents"]
    sched = world.get(f"/api/loans/{loan['id']}/schedule").json()
    assert len(sched["rows"]) == 60 and sum(x["principal_cents"] for x in sched["rows"]) == 900_000
    assert sched["yearly"][0]["year"] == 2025 and sched["yearly"][0]["months"] == 2
    early = world.post(f"/api/loans/{loan['id']}/early", json={"amount_cents": 300_000, "mode": "shorter"}).json()
    assert early["indemnity_cents"] == 0 and early["new_months_left"] < 48 and early["net_gain_cents"] > 0
    assert early["indemnity_rule"]["url"].startswith("https://www.service-public.fr/")
    reneg = world.post(f"/api/loans/{loan['id']}/renegotiate", json={"new_rate_bp": 400, "fees_cents": 5_000}).json()
    assert reneg["new_payment_cents"] < reneg["old_payment_cents"] and reneg["worth_it"] is True
    assert world.post(f"/api/loans/{loan['id']}/renegotiate", json={"new_rate_bp": 9_999}).status_code == 422
    assert world.post(f"/api/loans/{loan['id']}/early", json={"amount_cents": 0}).status_code == 422
    # Validation : un compte relié doit être un compte « crédit » ; un taux absurde est refusé.
    courant = world.get("/api/accounts").json()[0]["id"]
    assert world.post("/api/loans", json={**body, "account_id": courant}).status_code == 422
    assert world.post("/api/loans", json={**body, "annual_rate_bp": 4_000}).status_code == 422
    # Le dossier de l'agent reprend le prêt suivi (tableau) plutôt que la dette déclarée, sans compter deux fois.
    dossier = world.get("/api/agents/dossier", params={"privacy": "full"}).json()["dossier"]
    assert dossier["debts"]["source"] == "amortization"
    assert dossier["debts"]["total_remaining_cents"] == loan["remaining_cents"]
    assert (
        dossier["debts"]["items"][0]["label"] == "Prêt auto Cofidis"
        and dossier["debts"]["items"][0]["months_left"] == 48
    )
    assert dossier["wealth"]["net_worth_cents"] == after["net_worth"]["net_cents"], "jamais compté deux fois"
    redacted = world.get("/api/agents/dossier").json()["dossier"]
    assert redacted["debts"]["items"][0]["label"] == "Crédit à la consommation", "le nom du prêt reste en mode complet"
    assert world.delete(f"/api/loans/{loan['id']}").json()["loans"] == []
    assert world.delete(f"/api/loans/{loan['id']}").status_code == 404
    assert world.get("/api/wealth").json()["net_worth"]["net_cents"] == before
    with app.state.sessionmaker() as db:
        actions = [a for (a,) in db.execute(select(AuditLog.action))]
        assert {"loan.create", "loan.delete"} <= set(actions)
        for entry in db.scalars(select(AuditLog).where(AuditLog.action.like("loan.%"))):
            assert "Cofidis" not in entry.details, "ni le nom ni le prêteur dans le journal"
