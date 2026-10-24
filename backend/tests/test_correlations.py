"""Corrélations : un bail et une deuxième assurance habitation prélevée par la banque, un loyer au-dessus de l'IRL,
une prime qui monte, un bulletin qui ne colle pas, des ressources déclarées que le versement ne reflète pas — et des
pistes statistiques qui ne parlent qu'avec huit mois et un test par permutation."""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.api import deps
from app.domain import contracts
from app.domain import correlations as dom
from app.domain import vault as dv
from app.domain.recurring import add_months
from app.importers.base import ParsedTransaction
from app.models import Account, Procedure, VaultDocument, VaultField
from app.services import channel_alerts
from app.services import correlations as svc
from app.services.ingest import ingest
from tests.scenario import TODAY, seed_household
from tests.test_mcp import make_agent

D0 = date(2026, 6, 10)


def series(merchant: str, cents: int, *, category: str = "assurances", months: int = 6, cadence: str = "monthly",
           history: list[int] | None = None, key: str | None = None) -> dom.Series:  # fmt: skip
    amounts = history or [cents] * months
    hist = tuple((add_months(D0, i), a) for i, a in enumerate(amounts))
    return dom.Series(key or merchant.lower().replace(" ", "-"), merchant, category, cadence, cents, hist)


def lease(**fields: Any) -> dom.Doc:
    base: dict[str, Any] = {"loyer": 65_000, "date_debut": "2024-09-01"}
    base.update(fields)
    return dom.Doc(7, "lease", date(2024, 9, 1), base)


# ------------------------------------------------------------------ assurances
def test_two_home_insurers_are_a_duplicate_and_the_bank_one_is_named() -> None:
    maif = series("MAIF ASSURANCE HABITATION", 3_800)
    pacifica = series("PACIFICA ASSURANCE HABITATION", 1_200)
    found = dom.duplicate_insurance(
        [maif, pacifica, series("FREE MOBILE", 1_999, category="telecom")], [lease(assureur="MAIF")]
    )
    assert len(found) == 1
    f = found[0]
    assert f.kind == "duplicate_insurance" and f.level == "danger" and f.yearly_cents == 1_200 * 12
    assert "vendue par ta banque" in f.body and "loi Hamon" in f.body
    assert f.action == {
        "template": "resilier_abonnement",
        "label": "Préparer la résiliation",
        "merchant": "PACIFICA ASSURANCE HABITATION",
    }
    assert [e.label for e in f.evidence] == ["MAIF", "Pacifica (Crédit Agricole)", "Assureur nommé dans le bail"]
    assert f.evidence[2].ref == "doc:7"


def test_a_known_insurer_without_a_type_is_only_probable() -> None:
    maif = series("MAIF ASSURANCE HABITATION", 3_800)
    acm = series("ASSURANCES DU CREDIT MUTUEL", 1_500)  # type non écrit : probable seulement
    (f,) = dom.duplicate_insurance([maif, acm], [])
    assert f.level == "warning" and f.title.startswith("Peut-être") and f.confidence == pytest.approx(0.55)
    # une attestation habitation qui nomme l'ACM lève le doute
    doc = dom.Doc(
        3, "insurance", date(2026, 1, 1), {"assureur": "ACM (Crédit Mutuel)", "garantie": "Assurance Habitation"}
    )
    (f2,) = dom.duplicate_insurance([maif, acm], [doc])
    assert f2.level == "danger"
    # auto + habitation : deux contrats différents, pas un doublon
    assert dom.duplicate_insurance([maif, series("MACIF ASSURANCE AUTO", 4_500)], []) == []


def test_lease_naming_another_insurer_than_the_one_paid_is_a_hint() -> None:
    (f,) = dom.duplicate_insurance([series("PACIFICA ASSURANCE HABITATION", 1_200)], [lease(assureur="MAIF")])
    assert f.kind == "lease_insurer_mismatch" and f.level == "info" and "MAIF" in f.body
    assert dom.duplicate_insurance([series("MAIF ASSURANCE HABITATION", 3_800)], [lease(assureur="MAIF")]) == []


# ------------------------------------------------------------------ logement
def rent(paid: int, **extra: Any) -> dom.Series:
    return series("FONCIA LOYER", paid, category="logement", **extra)


def test_rent_paid_is_compared_with_the_lease_and_the_irl() -> None:
    today = date(2026, 10, 22)
    doc = lease(charges=5_000, revision="IRL 3e trimestre 2024")
    assert dom.rent_findings([rent(70_000)], [doc], today) == [], "650 + 50 : rien à dire"
    (over,) = dom.rent_findings([rent(72_000)], [doc], today)
    allowed = round(65_000 * contracts.IRL[(2026, 3)] / contracts.IRL[(2024, 3)])
    assert over.kind == "rent_over_irl" and over.level == "danger"
    assert over.yearly_cents == (72_000 - (allowed + 5_000)) * 12 and "IRL" in over.evidence[-1].label
    (ok,) = dom.rent_findings([rent(71_200)], [doc], today)
    assert ok.kind == "rent_revised" and ok.level == "info"
    (norev,) = dom.rent_findings([rent(72_000)], [lease(charges=5_000)], today)
    assert norev.kind == "rent_mismatch" and norev.level == "warning" and "17-1" in norev.body
    (low,) = dom.rent_findings([rent(69_000)], [doc], today)
    assert low.level == "info" and low.title == "Tu paies moins que le bail"
    assert dom.rent_findings([rent(72_000)], [], today) == [], "sans bail, rien à comparer"


def test_a_revision_is_checked_against_the_published_irl_without_guessing() -> None:
    """« Ta révision applique un IRL de X ; l'indice publié pour ce trimestre est Y : Z €/mois de trop » : seulement
    quand le trimestre de référence est écrit dans la clause et que l'ancien loyer (celui du bail) a été vu payé."""
    contracts.install_irl({(2025, 2): 146.68, (2026, 2): 148.37}, "24/10/2026")
    try:
        today = date(2026, 11, 15)
        doc = lease(loyer=65_000, date_debut="2025-09-01", revision="IRL 2e trimestre 2025")
        revised = rent(66_000, history=[65_000, 65_000, 65_000, 66_000, 66_000, 66_000])
        (f,) = dom.rent_findings([revised], [doc], today)
        assert f.kind == "rent_irl_misapplied" and f.level == "danger" and f.yearly_cents == 251 * 12
        assert f.body.replace("\xa0", " ").startswith(
            "Ta révision applique un IRL de 148,94 ; l'indice publié pour ce trimestre (2e trimestre 2026) est "
            "148,37 : 2,51 €/mois de trop."
        )
        irl = f.evidence[-1]
        assert irl.label == "IRL" and "INSEE, série 001515333 (IRL), relevée par Bank le 24/10/2026" in irl.value
        assert irl.ref == f"url:{contracts.IRL_SERIES_URL}"
        # Une révision dans la limite : rien de plus que le constat habituel.
        (ok,) = dom.rent_findings([rent(65_700, history=[65_000] * 3 + [65_700] * 3)], [doc], today)
        assert ok.kind == "rent_revised"
        # Ne jamais deviner : trimestre absent de la clause, ou ancien loyer qui n'est pas celui du bail.
        vague = lease(loyer=65_000, date_debut="2025-09-01", revision="IRL")
        assert dom.rent_findings([revised], [vague], today)[0].kind != "rent_irl_misapplied"
        other = rent(66_000, history=[64_000, 64_000, 64_000, 66_000, 66_000, 66_000])
        assert dom.rent_findings([other], [doc], today)[0].kind != "rent_irl_misapplied"
        # Une hausse loin de l'anniversaire du bail (régularisation des charges…) n'est pas lue comme la révision.
        off = lease(loyer=65_000, date_debut="2025-05-01", revision="IRL 2e trimestre 2025")
        assert dom.rent_findings([revised], [off], today)[0].kind != "rent_irl_misapplied"
    finally:
        contracts.install_irl({}, None)
    assert contracts.irl_source((2026, 2)) == contracts.IRL_SOURCE, "sans relève, la table livrée et sa date"


def test_deposit_above_the_legal_cap_is_flagged() -> None:
    (f,) = dom.rent_findings([rent(65_000)], [lease(depot_garantie=130_000)], date(2026, 10, 22))
    assert f.kind == "deposit_over_cap" and f.yearly_cents == 65_000 and "un mois" in f.body
    assert dom.rent_findings([rent(65_000)], [lease(depot_garantie=130_000, meuble="oui")], date(2026, 10, 22)) == []


def test_allowed_rent_follows_the_lease_quarter_and_stops_at_the_last_published_index() -> None:
    assert contracts.allowed_rent_cents(65_000, date(2026, 3, 1), date(2026, 10, 22)) == (65_000, (2026, 1), (2026, 1))
    got = contracts.allowed_rent_cents(65_000, date(2024, 9, 1), date(2026, 10, 22), reference=(2024, 2))
    assert got is not None and got[1:] == ((2024, 2), (2026, 2))
    assert contracts.allowed_rent_cents(65_000, date(2024, 12, 1), date(2026, 12, 15)) is not None
    assert contracts.allowed_rent_cents(65_000, date(2010, 1, 1), date(2026, 10, 22)) is None, "indice de 2010 inconnu"
    assert contracts.quarter_in("REVISION ANNUELLE SELON L'IRL DU 2E TRIMESTRE 2025") == (2025, 2)


# ------------------------------------------------------------------ primes, bulletins, quittances
def test_a_premium_that_rises_is_reported_with_the_right_to_leave() -> None:
    bump = series("MAIF ASSURANCE HABITATION", 3_800, history=[3_800, 3_800, 3_800, 3_800, 4_100])
    (f,) = dom.price_drift([bump])
    assert f.kind == "price_drift" and f.level == "info" and f.yearly_cents == 300 * 12 and "Hamon" in f.body
    steady = series("MAIF ASSURANCE HABITATION", 3_800, history=[3_800, 3_800, 3_800, 4_100, 4_100])
    assert dom.price_drift([steady])[0].level == "warning"
    (tel,) = dom.price_drift(
        [series("FREE MOBILE", 1_999, category="telecom", history=[1_999, 1_999, 1_999, 1_999, 2_499])]
    )
    assert "L224-33" in tel.body
    assert (
        dom.price_drift([series("MAIF ASSURANCE HABITATION", 3_800, history=[3_800, 3_800, 3_800, 3_800, 3_900])]) == []
    )
    assert dom.price_drift([series("NETFLIX", 1_349, category="abonnements", history=[1_349, 1_349, 1_499])]) == [], (
        "trop court"
    )


def test_payslip_and_receipt_are_checked_against_the_statements() -> None:
    slip = dom.Doc(5, "payslip", date(2026, 9, 30), {"periode": "2026-09", "net_paye": 215_000})
    credits = [dom.Credit(1, date(2026, 9, 28), 210_000), dom.Credit(2, date(2026, 8, 28), 215_000)]
    (f,) = dom.salary_vs_payslips([slip], credits)
    assert f.kind == "salary_mismatch" and f.level == "info" and f.evidence[1].ref == "txn:1"
    assert dom.salary_vs_payslips([slip], [dom.Credit(1, date(2026, 9, 28), 215_000)]) == []
    receipt = dom.Doc(6, "rent_receipt", date(2026, 9, 5), {"periode": "2026-09", "montant": 66_000})
    paid = series("FONCIA LOYER", 65_000, category="logement", history=[65_000, 65_000, 65_000, 65_000])
    months = [f"{d.year}-{d.month:02d}" for d, _ in paid.history]
    receipt_month = dom.Doc(6, "rent_receipt", date(2026, 9, 5), {"periode": months[-1], "montant": 66_000})
    (r,) = dom.receipts_vs_rent([receipt_month], [paid])
    assert r.kind == "receipt_mismatch" and "quittance corrigée" in r.body
    assert dom.receipts_vs_rent([receipt], [paid]) == [] or receipt.fields["periode"] in months


# ------------------------------------------------------------------ aides
def test_declared_resources_and_caf_payments_must_move_together() -> None:
    q1 = dom.Declared("2026-04", date(2026, 4, 30), {"activite": 600_000})
    q2 = dom.Declared("2026-07", date(2026, 7, 31), {"activite": 720_000})
    pays = [dom.Credit(1, date(2026, 5, 5), 25_000, "caf_ppa"), dom.Credit(2, date(2026, 8, 5), 25_000, "caf_ppa")]
    (f,) = dom.declared_vs_paid([q1, q2], pays)
    assert f.key == "declared_flat:2026-07" and f.level == "warning" and "+20 %" in f.body
    drop = [dom.Credit(1, date(2026, 5, 5), 25_000, "caf_ppa"), dom.Credit(2, date(2026, 8, 5), 15_000, "caf_ppa")]
    (g,) = dom.declared_vs_paid([q1, dom.Declared("2026-07", date(2026, 7, 31), {"activite": 600_000})], drop)
    assert g.key == "paid_drop:2026-07" and "40 %" in g.body
    coherent = [dom.Credit(1, date(2026, 5, 5), 25_000, "caf_ppa"), dom.Credit(2, date(2026, 8, 5), 21_000, "caf_ppa")]
    assert dom.declared_vs_paid([q1, q2], coherent) == []


def test_energy_mismatch_comes_from_the_linky_analysis() -> None:
    (f,) = dom.energy_findings(dom.EnergyLink("EDF", 6_000, 9_000, 36_000))
    assert f.level == "warning" and "rattrapage" in f.title and f.yearly_cents == 36_000
    (g,) = dom.energy_findings(dom.EnergyLink("EDF", 12_000, 9_000, -36_000))
    assert g.level == "info" and "remboursement" in g.title
    assert dom.energy_findings(dom.EnergyLink("EDF", 9_000, 9_000, 2_000)) == [] and dom.energy_findings(None) == []


# ------------------------------------------------------------------ habitudes
def test_statistical_links_need_eight_months_and_survive_the_permutation_test() -> None:
    months = [f"2026-{m:02d}" for m in range(1, 13)]
    income = {m: 200_000 + i * 10_000 for i, m in enumerate(months)}
    loisirs = {m: 20_000 + i * 3_000 for i, m in enumerate(months)}  # suit le revenu
    rng = random.Random(7)
    courses = {m: 30_000 + rng.randint(-8_000, 8_000) for m in months}  # indépendant
    found = dom.statistical_links(
        {"loisirs": loisirs, "courses": courses}, income, {"loisirs": "Loisirs", "courses": "Courses"}
    )
    assert [f.key for f in found] == ["stat:revenu:loisirs"]
    assert (
        found[0].statistical
        and found[0].level == "info"
        and "Piste" in found[0].title
        and "pas une cause" in found[0].body
    )
    short = dict(list(income.items())[:6])
    assert dom.statistical_links({"loisirs": loisirs}, short, {}) == [], "six mois : trop court"
    assert dom.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert dom.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert dom._ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_payday_concentration_only_speaks_with_six_paydays_and_a_real_pattern() -> None:
    paydays = [date(2026, m, 28) for m in range(1, 9)]
    daily: dict[date, int] = {}
    for p in paydays:
        daily[p + timedelta(days=1)] = 30_000  # la grosse sortie, juste après la paie
        daily[p + timedelta(days=15)] = 10_000
    (f,) = dom.payday_concentration(daily, paydays, {})
    assert f.kind == "payday_concentration" and "75 %" in f.title and f.statistical
    flat = {p + timedelta(days=k): 5_000 for p in paydays for k in (1, 8, 15, 22)}
    assert dom.payday_concentration(flat, paydays, {}) == []
    assert dom.payday_concentration(daily, paydays[:4], {}) == []


def test_analyse_orders_by_seriousness_and_dedups() -> None:
    inputs = dom.Inputs(
        today=date(2026, 10, 22),
        series=[
            series("MAIF ASSURANCE HABITATION", 3_800),
            series("PACIFICA ASSURANCE HABITATION", 1_200),
            rent(72_000),
        ],
        docs=[lease(charges=5_000)],
        energy=dom.EnergyLink("EDF", 6_000, 9_000, 36_000),
    )
    out = dom.analyse(inputs)
    assert [f.level for f in out] == ["danger", "warning", "warning"]
    assert out[0].kind == "duplicate_insurance"
    as_dict = dom.finding_to_dict(out[0])
    assert as_dict["topic_label"] == "Assurances" and as_dict["action"]["template"] == "resilier_abonnement"


# ------------------------------------------------------------------ lecture du bail et de l'attestation
def test_the_vault_reads_the_small_print_of_a_lease_and_an_insurance_notice() -> None:
    text = (
        "CONTRAT DE LOCATION - BAIL D'HABITATION\nLogement vide\nLe bailleur : SCI LES LILAS\n"
        "Prend effet le 01/09/2024\nLoyer mensuel hors charges : 650,00 €\nProvision pour charges : 50,00 €\n"
        "Dépôt de garantie : 650,00 €\nRévision annuelle selon l'indice de référence des loyers (IRL) du 2e trimestre 2024\n"
        "Le locataire doit justifier d'une assurance habitation (attestation MAIF fournie)\nPréavis : trois mois\n"
    )
    found = {f.key: f.value for f in dv._apply_rules("lease", dv.lines_of(text))}
    assert found == {
        "date_debut": "2024-09-01", "loyer": 65_000, "charges": 5_000, "depot_garantie": 65_000,
        "revision": "IRL 2e trimestre 2024", "preavis": 3, "meuble": "non", "assurance_exigee": "oui",
        "assureur": "MAIF", "bailleur": "SCI LES LILAS",
    }  # fmt: skip
    notice = "AVIS D'ECHEANCE\nPacifica - Assurance Habitation\nCotisation annuelle TTC : 144,00 €\nValable jusqu'au 31/12/2026\nPrime par mois : 12,00 €\n"
    got = {f.key: f.value for f in dv._apply_rules("insurance", dv.lines_of(notice))}
    assert (
        got["type"] == "habitation"
        and got["assureur"] == "Pacifica (Crédit Agricole)"
        and got["valable_jusqu"] == "2026-12-31"
    )
    assert got["cotisation"] == 14_400 and got["periodicite"] in ("annuelle", "mensuelle")


# ------------------------------------------------------------------ service, API, cartes, agent
@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
    yield auth_client
    app.dependency_overrides.clear()


def _monthly(db: Session, label: str, cents: int, day: int, months: int = 4) -> None:
    account = db.scalars(select(Account).where(Account.kind == "checking")).first()
    assert account is not None
    txns = [ParsedTransaction(add_months(TODAY.replace(day=1), -i, day), cents, label) for i in range(months, -1, -1)]
    ingest(db, account, [t for t in txns if t.booking_date <= TODAY], provider="file")


def _doc(db: Session, kind: str, fields: dict[str, Any], sha: str) -> int:
    doc = VaultDocument(kind=kind, title=kind, filename=f"{kind}.pdf", mime="application/pdf", size=10, sha256=sha * 64,
                        status="verified", source="upload", method="pdf_text", confidence=0.9,
                        document_date=date.fromisoformat(fields["date_debut"]) if "date_debut" in fields else None)  # fmt: skip
    db.add(doc)
    db.flush()
    db.add_all([VaultField(document_id=doc.id, key=k, value=json.dumps(v), confidence=0.9) for k, v in fields.items()])
    db.flush()
    return doc.id


def test_the_lease_and_the_bank_insurance_meet_in_a_duplicate_everywhere(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        _monthly(s, "PRLV SEPA PACIFICA ASSURANCE HABITATION", -1_200, 14)
        doc_id = _doc(
            s,
            "lease",
            {"loyer": 65_000, "date_debut": "2024-09-01", "assureur": "MAIF", "revision": "IRL 3e trimestre 2024"},
            "b",
        )
        s.commit()
    data = world.get("/api/correlations").json()
    dup = next(f for f in data["findings"] if f["kind"] == "duplicate_insurance")
    assert dup["level"] == "danger" and dup["yearly_cents"] == 14_400 and dup["status"] == "new"
    assert (
        any(e["ref"] == f"doc:{doc_id}" for e in dup["evidence"])
        and "MAIF" in dup["body"]
        and "Pacifica" in dup["body"]
    )
    assert not [f for f in data["findings"] if f["kind"].startswith("rent")], "loyer payé = loyer du bail : rien"
    assert "INSEE" in data["irl_source"]
    # La carte du jour, et la démarche de résiliation préparée en un geste.
    cards = {c["key"]: c for c in world.get("/api/initiatives?limit=20").json()["cards"]}
    card = cards[f"correlation:{dup['key']}"]
    assert "144 €" in card["title"] and card["actions"][0]["label"] == "Préparer la démarche"
    r = world.post(f"/api/initiatives/correlation:{dup['key']}/act", json={"action": "corr_start"})
    assert r.status_code == 200 and "Démarche préparée" in r.json()["result"]
    with app.state.sessionmaker() as s:
        proc = s.scalar(select(Procedure).where(Procedure.template == "resilier_abonnement"))
        assert proc is not None and proc.title.startswith("Résilier Pacifica")
        fields = json.loads(proc.data)["fields"]
        assert fields["fournisseur"]["value"].startswith("Pacifica") and fields["montant_mensuel"]["value"] == 1_200
    data = world.get("/api/correlations").json()
    dup = next(f for f in data["findings"] if f["kind"] == "duplicate_insurance")
    assert dup["procedure_id"] == proc.id
    # Démarche préparée une seule fois ; l'agent lit le constat ; l'alerte de messagerie le porte une fois.
    assert world.post("/api/correlations/start", json={"key": dup["key"]}).json()["procedure_id"] == proc.id
    agent = make_agent(app, world)
    out = agent.ok("get_correlations")
    assert out["findings"][0]["kind"] == "duplicate_insurance" and "evidence" in out["findings"][0]
    discreet = make_agent(app, world, preset="observateur", privacy="aggregates", name="discret")
    assert "body" not in discreet.ok("get_correlations")["findings"][0]
    with app.state.sessionmaker() as s:
        from datetime import UTC, datetime

        notices = channel_alerts.collect(
            s,
            today=TODAY,
            local=datetime(2026, 10, 21, 12, tzinfo=UTC),
            privacy="redacted",
            public_url="https://b.example",
        )
        assert any(n.key == f"corr:{dup['key']}" and "assurances habitation" in n.text for n in notices)
    # Écarter le constat le sort de la liste et de la page du jour.
    r = world.post("/api/correlations/decide", json={"key": dup["key"], "decision": "dismissed"})
    assert (
        r.status_code == 200
        and r.json()["hidden"] == 1
        and not [f for f in r.json()["findings"] if f["kind"] == "duplicate_insurance"]
    )
    assert f"correlation:{dup['key']}" not in {c["key"] for c in world.get("/api/initiatives?limit=20").json()["cards"]}
    r = world.post("/api/correlations/decide", json={"key": dup["key"], "decision": "new"})
    assert r.json()["hidden"] == 0


def test_rent_above_the_irl_is_found_from_the_lease_and_the_statements(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        _doc(s, "lease", {"loyer": 60_000, "date_debut": "2024-09-01", "revision": "IRL 3e trimestre 2024"}, "c")
        s.commit()
    data = world.get("/api/correlations").json()
    over = next(f for f in data["findings"] if f["kind"] == "rent_over_irl")
    allowed = round(60_000 * contracts.IRL[(2026, 3)] / contracts.IRL[(2024, 3)])
    assert over["level"] == "danger" and over["yearly_cents"] == (65_000 - allowed) * 12
    assert any(e["label"] == "IRL" for e in over["evidence"])
    assert world.post("/api/correlations/start", json={"key": over["key"]}).status_code == 409, (
        "rien à préparer : une réclamation"
    )


def test_nothing_is_stored_and_nothing_leaves(world: TestClient) -> None:
    import inspect

    source = inspect.getsource(svc) + inspect.getsource(dom)
    assert "netfetch" not in source and "httpx" not in source
    assert world.get("/api/correlations").json()["disclaimer"].startswith("Chaque constat renvoie à ses pièces")
