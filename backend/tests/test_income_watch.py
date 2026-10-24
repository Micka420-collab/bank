"""Revenus et prestations : une revalorisation légale qui ne se voit pas sur le versement, un taux de prélèvement
décalé des revenus de l'année (avec le solde de septembre posé dans l'Année qui vient), le chèque énergie que Bank
ne voit pas. Barèmes datés et sourcés ; le modèle d'impôt est recoupé avec le vrai OpenFisca France 176.1.2."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import baremes as dbar
from app.domain import income_watch as dom
from app.domain import rates
from app.domain.correlations import Finding
from app.importers.base import ParsedTransaction
from app.models import Account, FiscalSimulation, OfficialFact, RateObservation, VaultDocument, VaultField
from app.services import income_watch as svc
from app.services.ingest import ingest
from tests.scenario import TODAY, seed_household

FIXTURES = Path(__file__).parent / "fixtures" / "baremes"
RAW = rates.static()
REVALS, APPEAL = dom.load_revaluations(RAW)
MODEL = dom.load_tax_model(RAW)
RULE = dom.load_withholding_rule(RAW)


def caf(i: int, day: date, cents: int, kind: str = "caf_ppa", complementary: bool = False) -> dom.AidCredit:
    return dom.AidCredit(i, day, cents, kind, complementary)


# ------------------------------------------------------------------ barèmes datés et sourcés
def test_every_rule_is_dated_and_sourced() -> None:
    by_id = {r.id: r for r in REVALS}
    assert by_id["prestations-2026"].rate_percent == Decimal("0.8")
    assert by_id["prestations-2026"].effective == date(2026, 4, 1)
    assert by_id["pensions-2026"].rate_percent == Decimal("0.9") and by_id["are-2026"].rate_percent == 0
    assert "caf_logement" not in by_id["prestations-2026"].applies_to, "les aides au logement suivent l'IRL"
    for r in REVALS:
        assert r.source and r.url.startswith("https://")
    assert APPEAL is not None and APPEAL.months == 2 and "F2500" in APPEAL.url
    assert RULE is not None and RULE.decrease_gap_percent == 5 and RULE.balance_single_max_cents == 30_000
    assert MODEL is not None and MODEL.decote_single_cents == 89_700 and MODEL.qf_cap_half_part_cents == 180_700


def test_the_bmaf_read_in_openfisca_dates_a_revaluation_rates_json_does_not_know() -> None:
    body = (FIXTURES / "openfisca_bmaf.json").read_bytes()  # vraie réponse du 24/10/2026, raccourcie
    obs = dbar.parse_openfisca_values(body, "https://api.fr.openfisca.org/x", TODAY, "openfisca.bmaf")
    assert [(o.period, o.value) for o in obs][-2:] == [("2025-04-01", "474.37"), ("2026-04-01", "478.16")]
    points = [(date.fromisoformat(o.period), Decimal(o.value), o.source_url) for o in obs]
    assert dom.revaluations_from_bmaf(points, REVALS) == [], "rien de plus récent que rates.json"
    only_2025 = [r for r in REVALS if r.id != "prestations-2026"]
    derived = dom.revaluations_from_bmaf(points, only_2025)
    got = {r.effective: r for r in derived}
    assert list(got) == [date(2026, 4, 1)], "les hausses d'avant 2025 ne comptent plus"
    assert got[date(2026, 4, 1)].rate_percent == Decimal("0.8") and "478,16" in got[date(2026, 4, 1)].source
    assert got[date(2026, 4, 1)].applies_to == only_2025[0].applies_to


# ------------------------------------------------------------------ revalorisation absente
def test_an_unchanged_payment_after_the_april_revaluation_is_a_finding_never_a_debt() -> None:
    credits = [caf(1, date(2026, 4, 5), 31_245), caf(2, date(2026, 5, 5), 31_245), caf(3, date(2026, 6, 5), 31_245)]
    found = dom.missing_revaluations(REVALS, credits, date(2026, 6, 20), APPEAL)
    assert len(found) == 1
    f = found[0]
    assert f.key == "reval:prestations-2026:caf_ppa" and f.level == "warning" and f.topic == "aides"
    assert "0,8 %" in f.body and "01/04/2026" in f.body and "lis la notification de la CAF" in f.body
    assert "2 mois après la notification" in f.body and "commission de recours amiable" in f.body
    assert f.yearly_cents is None, "aucun montant dû n'est affirmé"
    refs = [e.ref for e in f.evidence]
    assert refs[:2] == ["txn:1", "txn:2"] and any(r and r.startswith("url:https://") for r in refs[2:])
    # un centime d'écart : la revalorisation a joué, ou autre chose ; Bank ne dit rien
    moved = [caf(1, date(2026, 4, 5), 31_245), caf(2, date(2026, 5, 5), 31_246)]
    assert dom.missing_revaluations(REVALS, moved, date(2026, 6, 20), APPEAL) == []


def test_what_does_not_make_a_finding() -> None:
    today = date(2026, 8, 20)
    are = [caf(1, date(2026, 7, 2), 98_000, "ft_are"), caf(2, date(2026, 8, 3), 98_000, "ft_are")]
    assert dom.missing_revaluations(REVALS, are, today, APPEAL) == [], "pas de revalorisation de l'ARE en 2026"
    rappel = [caf(1, date(2026, 4, 5), 31_245), caf(2, date(2026, 5, 5), 31_245), caf(3, date(2026, 5, 12), 4_000)]
    assert dom.missing_revaluations(REVALS, rappel, today, APPEAL) == [], "un rappel dans le mois brouille tout"
    apl = [caf(1, date(2026, 4, 5), 20_000, "caf_logement"), caf(2, date(2026, 5, 5), 20_000, "caf_logement")]
    assert dom.missing_revaluations(REVALS, apl, today, APPEAL) == []
    old = [caf(1, date(2026, 4, 5), 31_245), caf(2, date(2026, 5, 5), 31_245)]
    assert dom.missing_revaluations(REVALS, old, date(2026, 12, 1), APPEAL) == [], "plus de six mois : trop tard"
    gap = [caf(1, date(2026, 2, 5), 31_245), caf(2, date(2026, 5, 5), 31_245)]
    assert dom.missing_revaluations(REVALS, gap, today, APPEAL) == [], "deux mois qui ne se suivent pas"


def test_base_pensions_in_january_but_not_the_complementary_ones() -> None:
    today = date(2026, 3, 1)
    base = [
        caf(1, date(2026, 1, 9), 120_000, "retraite_pension"),
        caf(2, date(2026, 2, 9), 120_000, "retraite_pension"),
    ]
    found = dom.missing_revaluations(REVALS, base, today, APPEAL)
    assert [f.key for f in found] == ["reval:pensions-2026:retraite_pension"] and found[0].topic == "revenus"
    assert "retraites de base" in found[0].body and "0,9 %" in found[0].body
    agirc = [
        caf(1, date(2026, 1, 2), 40_000, "retraite_pension", True),
        caf(2, date(2026, 2, 2), 40_000, "retraite_pension", True),
    ]
    assert dom.missing_revaluations(REVALS, agirc, today, APPEAL) == []
    assert dom.COMPLEMENTARY.search(dom.fold("VIR AGIRC-ARRCO RETRAITE COMPLÉMENTAIRE"))


def test_france_travail_points_to_the_notification_for_the_appeal() -> None:
    today = date(2025, 9, 1)
    are = [caf(1, date(2025, 7, 1), 98_000, "ft_are"), caf(2, date(2025, 8, 1), 98_000, "ft_are")]
    (f,) = dom.missing_revaluations(REVALS, are, today, APPEAL)
    assert "0,5 %" in f.body and "la notification indique la voie et le délai de recours" in f.body
    assert "2 mois" not in f.body


# ------------------------------------------------------------------ modèle d'impôt
@pytest.mark.parametrize(
    ("salaries", "children", "openfisca"),
    [
        # impot_revenu_restant_a_payer, revenus 2025, calculé par openfisca-france 176.1.2 le 24/10/2026
        ([30_000], 0, 1_564),
        ([18_000], 0, 0),
        ([22_000], 0, 413),
        ([60_000], 0, 9_304),
        ([45_000, 25_000], 2, 3_023),
        ([80_000, 0], 3, 2_607),
    ],
)
def test_the_tax_model_matches_openfisca(salaries: list[int], children: int, openfisca: int) -> None:
    assert MODEL is not None
    couple = len(salaries) == 2
    parts = Decimal(2 if couple else 1) + sum(Decimal(1) if i >= 2 else Decimal("0.5") for i in range(children))
    tax = dom.estimate_tax(MODEL, [s * 100 for s in salaries], parts, couple)
    assert abs(tax - openfisca * 100) <= 100


def test_taxable_income_from_a_salary_credit() -> None:
    # 2 000 € versés à 10 % : NI = 1,03 × 2 000 / (1 − 0,103)
    assert dom.taxable_from_credit(200_000, Decimal(10)) == 229_654
    assert dom.taxable_from_credit(200_000, Decimal(0)) == 206_000


# ------------------------------------------------------------------ taux de prélèvement
def pas(rate: str, monthly: int, months: int = 6, payslips: bool = True) -> dom.PasEstimate | None:
    assert MODEL is not None
    inp = dom.PasInputs(
        year=2026,
        rate=dom.RateSeen(Decimal(rate), "bulletin de paie de 06/2026", "doc:4"),
        months=[dom.MonthIncome(f"2026-{m:02d}", monthly, payslips) for m in range(1, months + 1)],
        parts=Decimal(1),
        parts_origin="avis d'impôt sur les revenus 2025",
        couple=False,
    )
    return dom.estimate_withholding(inp, MODEL)


def test_income_down_lets_the_rate_be_lowered_with_its_monthly_effect() -> None:
    est = pas("7.4", 200_000)  # 24 000 € imposables : environ 2,8 %
    assert est is not None and est.estimated_percent < Decimal("7.4")
    (f,) = dom.withholding_findings(est, RULE)
    assert f.key == "pas_lower:2026" and f.level == "info" and f.yearly_cents == -est.balance_cents
    assert "7,4 %" in f.title and f"{dom.fr_percent(est.estimated_percent)}" in f.title
    assert "CGI art. 204 J" in f.body and "de plus resteraient chaque mois" in f.body and "Estimation Bank" in f.body
    labels = {e.label: e for e in f.evidence}
    assert labels["Taux appliqué"].ref == "doc:4" and "6 mois" in labels["Revenus vus"].value
    assert labels["Règle"].ref and "BOI-IR-PAS-20-30-20-10" in labels["Règle"].ref
    assert labels["Impôt estimé"].ref and labels["Impôt estimé"].ref.startswith("url:https://")
    assert dom.september_balance(est, RULE) == []


def test_a_small_gap_says_nothing_and_three_months_are_needed() -> None:
    est = pas("2.9", 200_000)
    assert est is not None and dom.withholding_findings(est, RULE) == []
    assert pas("7.4", 200_000, months=2) is None


def test_income_up_provisions_the_september_balance_in_four_instalments() -> None:
    est = pas("2.0", 450_000, payslips=False)
    assert est is not None and est.balance_cents > 30_000
    (f,) = dom.withholding_findings(est, RULE)
    assert f.key == "pas_balance:2026" and f.level == "warning" and "de septembre à décembre 2027" in f.title
    assert any(e.label == "Solde" and e.ref and "F35898" in e.ref for e in f.evidence)
    parts = dom.september_balance(est, RULE)
    assert [d for d, _l, _c in parts] == [date(2027, m, 25) for m in (9, 10, 11, 12)]
    assert sum(c for _d, _l, c in parts) == est.balance_cents and parts[0][1].endswith("(1/4)")
    small = pas("7.5", 300_000)
    assert small is not None and 0 < small.balance_cents <= 30_000
    assert [(d, c) for d, _l, c in dom.september_balance(small, RULE)] == [(date(2027, 9, 25), small.balance_cents)]


# ------------------------------------------------------------------ chèque énergie
def test_energy_cheque_reminder_without_any_campaign_date() -> None:
    sim = dom.EnergyCheque(2026, 4_800, date(2026, 10, 2), "176.1.2")
    (f,) = dom.energy_cheque_findings(sim, [], TODAY)
    assert "Bank ne voit pas de chèque énergie utilisé" in f.title and "48 €" in f.body
    assert not re.search(r"\b\d{1,2}/\d{2}/\d{4}\b", f.body.replace("02/10/2026", "")), "aucune date de campagne"
    assert dom.energy_cheque_findings(sim, [date(2026, 5, 3)], TODAY) == []
    assert dom.energy_cheque_findings(dom.EnergyCheque(2025, 4_800, date(2025, 10, 2), "x"), [], TODAY) == []
    assert dom.energy_cheque_findings(dom.EnergyCheque(2026, 0, date(2026, 10, 2), "x"), [], TODAY) == []
    unknown = dom.EnergyCheque(2026, 27_700, date(2026, 10, 2), "x", rfr_known=False)
    (missing,) = dom.energy_cheque_findings(unknown, [], TODAY)
    assert missing.key == "cheque_energie_rfr:2026" and "revenu fiscal de référence 2024" in missing.body
    assert "277" not in missing.body, "un montant calculé sur un revenu inconnu n'est pas montré"
    assert dom.is_energy_cheque("AVOIR EDF CHÈQUE ÉNERGIE 2026") and not dom.is_energy_cheque("PRLV EDF ENERGIE")


# ------------------------------------------------------------------ jamais un conseil
FORBIDDEN = ("recommand", "conseill", "urgent", "maintenant ou jamais", "tu as droit", "te doit", "rembourse-toi")


def _texts(found: list[Finding]) -> list[str]:
    return [t for f in found for t in (f.title, f.body, *(e.value for e in f.evidence))]


def test_texts_are_findings_not_advice() -> None:
    found = dom.missing_revaluations(REVALS, [caf(1, date(2026, 4, 5), 9), caf(2, date(2026, 5, 5), 9)], TODAY, APPEAL)
    found += dom.withholding_findings(pas("7.4", 200_000), RULE) + dom.withholding_findings(pas("2.0", 450_000), RULE)
    found += dom.energy_cheque_findings(dom.EnergyCheque(2026, 4_800, date(2026, 10, 2), "x"), [], TODAY)
    assert len(found) == 4
    for text in _texts(found):
        folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        assert not [w for w in FORBIDDEN if w in folded], text
        assert "!" not in text


# ------------------------------------------------------------------ service : relevés, Papiers, simulation
@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def _credit(app, *items: tuple[date, int, str]) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        account = s.scalars(select(Account).where(Account.kind == "checking")).first()
        assert account is not None
        ingest(s, account, [ParsedTransaction(d, c, label) for d, c, label in items], provider="file")
        s.commit()


def test_revaluation_and_energy_cheque_reach_the_correlations(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    _credit(
        app,
        (date(2026, 4, 5), 50_000, "VIR CAF DU LOIRET RSA"),
        (date(2026, 5, 5), 50_000, "VIR CAF DU LOIRET RSA"),
        (date(2026, 4, 6), 14_000, "VIR CAF DU LOIRET ALLOCATIONS FAMILIALES"),
        (date(2026, 5, 6), 14_112, "VIR CAF DU LOIRET ALLOCATIONS FAMILIALES"),
    )
    with app.state.sessionmaker() as s:
        result = {
            "period": "2026-10",
            "engine_version": "176.1.2",
            "cheque_energie_cents": 4_800,
            "inputs": {"months_of_salary_history": 30, "used_declared_taxable_income": True},
        }
        s.add(FiscalSimulation(period="2026-10", result=json.dumps(result)))
        s.commit()
    data = world.get("/api/correlations").json()
    keys = {f["key"]: f for f in data["findings"]}
    assert "reval:prestations-2026:caf_rsa" in keys and "reval:prestations-2026:caf_af" not in keys
    assert keys["cheque_energie:2026"]["topic_label"] == "Énergie"
    # l'export RGPD porte la simulation, chèque énergie estimé compris
    from tests.conftest import PASSWORD

    exported = json.loads(world.post("/api/export", json={"password": PASSWORD}).content)
    assert exported["fiscal_simulations"][-1]["result"]["cheque_energie_cents"] == 4_800
    # un chèque énergie vu dans les relevés éteint le rappel
    _credit(app, (date(2026, 6, 2), 4_800, "AVOIR EDF CHEQUE ENERGIE"))
    keys = {f["key"] for f in world.get("/api/correlations").json()["findings"]}
    assert "cheque_energie:2026" not in keys
    # écarter le constat le range, comme les autres
    r = world.post("/api/correlations/decide", json={"key": "reval:prestations-2026:caf_rsa", "decision": "dismissed"})
    assert r.status_code == 200 and "reval:prestations-2026:caf_rsa" not in {f["key"] for f in r.json()["findings"]}


def test_a_bmaf_observation_extends_the_dated_revaluations(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        for period, value in (("2026-04-01", "478.16"), ("2027-04-01", "482.00")):
            s.add(RateObservation(series="openfisca.bmaf", period=period, value=value, source_url="https://of/x"))
        s.commit()
        revals, _appeal = svc.revaluations(s)
    derived = next(r for r in revals if r.effective == date(2027, 4, 1))
    assert derived.rate_percent == Decimal("0.8") and derived.url == "https://of/x"


def _payslip(s, month: str, taxable: int, rate: str, sha: str) -> int:  # type: ignore[no-untyped-def]
    doc = VaultDocument(kind="payslip", title="Bulletin", filename="b.pdf", mime="application/pdf", size=10,
                        sha256=sha * 64, status="verified", source="upload", method="pdf_text", confidence=0.9)  # fmt: skip
    s.add(doc)
    s.flush()
    fields = {"periode": month, "net_imposable": taxable, "pas_taux": rate}
    s.add_all([VaultField(document_id=doc.id, key=k, value=json.dumps(v), confidence=0.9) for k, v in fields.items()])
    s.flush()
    return int(doc.id)


def test_payslips_and_salary_credits_feed_the_withholding_check(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        for i, m in enumerate(("2026-01", "2026-02", "2026-03")):
            last = _payslip(s, m, 150_000, "9.5", "abc"[i])
        s.add(OfficialFact(kind="avis_impot", period="2025", title="Avis", data=json.dumps({"parts": "1"})))
        s.commit()
        inp = svc.pas_inputs(s, TODAY)
    assert inp is not None and inp.rate.percent == Decimal("9.5") and inp.rate.ref == f"doc:{last}"
    assert inp.parts == 1 and inp.parts_origin == "avis d'impôt sur les revenus 2025" and not inp.couple
    by_month = {m.month: m for m in inp.months}
    assert by_month["2026-01"].from_payslip and by_month["2026-01"].taxable_cents == 150_000
    assert not by_month["2026-07"].from_payslip  # virement de salaire du scénario, net imposable estimé
    assert by_month["2026-07"].taxable_cents == dom.taxable_from_credit(215_000, Decimal("9.5"))
    data = world.get("/api/correlations").json()
    lower = next(f for f in data["findings"] if f["key"] == "pas_lower:2026")
    assert "9,5 %" in lower["title"] and any(e["ref"] == f"doc:{last}" for e in lower["evidence"])


def test_the_september_balance_lands_in_the_year_ahead(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        data = {"parts": "1", "withholding_rate_percent": "1"}
        s.add(OfficialFact(kind="avis_impot", period="2025", title="Avis", data=json.dumps(data)))
        s.commit()
    found = {f["key"]: f for f in world.get("/api/correlations").json()["findings"]}
    assert "pas_balance:2026" in found
    year = world.get("/api/forecast/year").json()
    spikes = [s for s in year["spikes"] if s["label"].startswith("Solde d'impôt 2026 estimé")]
    assert [s["day"] for s in spikes] == ["2027-09-25", "2027-10-25"], "la fenêtre s'arrête en octobre 2027"
    assert found["pas_balance:2026"]["yearly_cents"] // 4 <= spikes[0]["amount_cents"]


def test_no_network_and_nothing_stored() -> None:
    import inspect

    source = inspect.getsource(svc) + inspect.getsource(dom)
    assert "netfetch" not in source and "httpx" not in source and ".add(" not in source


def test_the_simulation_asks_the_energy_cheque_for_the_whole_year() -> None:
    import httpx

    from app.fiscal.client import FiscalClient
    from app.fiscal.situation import HouseholdProfileIn, build_situation

    seen: dict[str, object] = {}

    def engine(req: httpx.Request) -> httpx.Response:
        seen["outputs"] = json.loads(req.content)["outputs"]
        return httpx.Response(200, json={"engine_version": "176.1.2", "results": {"cheque_energie": {"2026": 48.0}}})

    http = httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(engine))
    out = FiscalClient("http://fiscal", http=http).simulate({"individus": {}}, date(2026, 10, 1))
    assert {"variable": "cheque_energie", "period": "2026"} in seen["outputs"]  # type: ignore[operator]
    assert out.cheque_energie_cents == 4_800
    profile = HouseholdProfileIn(birth_date=date(1990, 5, 1), postal_insee_code="45234")
    sit = build_situation(profile, period=date(2026, 10, 15), salary_net_by_month={})
    # le chèque énergie (annuel) lit le logement au premier mois de l'année : sans lui, OpenFisca répond 0
    assert sit["menages"]["menage"]["statut_occupation_logement"]["2026-01"] == "locataire_vide"
