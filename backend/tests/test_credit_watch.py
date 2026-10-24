"""Crédit et coûts : seuils de l'usure lus dans le vrai avis du Journal officiel (archives de la DILA, réponses du
24/10/2026 réduites), agios et crédits face au seuil, renégociation lancée seule au taux moyen du marché, frais
réels de trajet face à l'abattement de 10 %. Chaque chiffre est daté et sourcé ; la méthode est montrée."""

from __future__ import annotations

import io
import json
import tarfile
import unicodedata
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import baremes as dbar
from app.domain import credit_watch as dom
from app.domain import income_watch as iw
from app.domain import loans as dl
from app.domain import rates
from app.domain.correlations import Finding
from app.importers.base import ParsedTransaction
from app.models import Account, AccountKind, Loan, Provider, PublicSourceKey, WorkPlan, WorkShift
from app.services import baremes as bsvc
from app.services import credit_watch as svc
from app.services.ingest import ingest
from tests.scenario import TODAY, seed_household

FIXTURES = Path(__file__).parent / "fixtures" / "credit"
RAW = rates.static()
QUARTERS = dom.merge_usury(dom.load_usury(RAW), [])
SCALE = dom.load_mileage(RAW)
MODEL = iw.load_tax_model(RAW)


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ------------------------------------------------------------------ l'avis trimestriel, lu dans le vrai XML
def test_the_real_avis_gives_the_thresholds_and_the_market_averages() -> None:
    q = dom.parse_usury(read("jorf_usure_2026-Q4.xml"))  # avis du 25/09/2026, JO n° 0225 du 26/09/2026
    assert (q.quarter, q.effective) == ("2026-Q4", date(2026, 10, 1))
    assert q.thresholds["conso_3000"] == Decimal("23.6") and q.thresholds["conso_6000"] == Decimal("15.76")
    assert q.thresholds["conso_plus"] == Decimal("8.56") and q.thresholds["immo_fixe_20"] == Decimal("5.4")
    assert q.averages["immo_fixe_10_20"] == Decimal("3.51") and q.averages_quarter == "2026-Q3"
    assert "Avis du 25/09/2026" in q.source and "JO n° 0225 du 26/09/2026" in q.source
    assert q.url == "https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000054903528"
    # les tableaux des entreprises (découverts à 19,08 %) ne se mélangent pas aux particuliers
    assert Decimal("19.08") not in q.thresholds.values()
    # l'avis de juin colle ses morceaux de libellé par des <br/> : « supérieur à<br/>3 000 euros »
    q3 = dom.parse_usury(read("jorf_usure_2026-Q3.xml"))
    assert q3.quarter == "2026-Q3" and q3.thresholds["conso_6000"] == Decimal("15.67")


def test_rates_json_carries_the_four_2026_avis_dated_and_sourced() -> None:
    assert list(QUARTERS) == ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"]
    for name, qid in (("jorf_usure_2026-Q4.xml", "2026-Q4"), ("jorf_usure_2026-Q3.xml", "2026-Q3")):
        parsed = dom.parse_usury(read(name))
        assert QUARTERS[qid].thresholds == parsed.thresholds and QUARTERS[qid].averages == parsed.averages
        assert QUARTERS[qid].url == parsed.url
    for q in QUARTERS.values():
        assert q.source.startswith("Avis du") and q.url.startswith("https://www.legifrance.gouv.fr/jorf/id/")


def test_what_is_not_the_avis_is_refused() -> None:
    with pytest.raises(dom.UsuryError):
        dom.parse_usury(b"<ARTICLE><CONTEXTE/></ARTICLE>")
    with pytest.raises(dom.UsuryError):
        dom.parse_usury(b'<!DOCTYPE x [<!ENTITY a "b">]><ARTICLE>&a;</ARTICLE>')
    broken = read("jorf_usure_2026-Q4.xml").replace(b"15,76 %", b"")
    with pytest.raises(dom.UsuryError, match="incomplet"):
        dom.parse_usury(broken)
    with pytest.raises(dom.UsuryError):
        dom.usury_article(b"pas une archive")


# ------------------------------------------------------------------ archives quotidiennes de la DILA
def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def test_the_archives_are_searched_newest_first_in_the_quarter_window() -> None:
    listing = read("dila_jorf_listing.html")
    names = dom.archives_for(listing, "2026-Q4", date(2026, 10, 24))
    assert names[0] == "JORF_20260930-215413.tar.gz" and "JORF_20260926-210736.tar.gz" in names[:9]
    assert all("202609" in n for n in names[:9]) and len(names) == dom.MAX_ARCHIVES
    assert dom.archives_for(listing, "2026-Q4", date(2026, 9, 20))[0].startswith("JORF_20260920")
    assert dom.wanted_quarters(date(2026, 10, 24), {"2026-Q4"}) == []
    assert dom.wanted_quarters(date(2026, 12, 20), {"2026-Q4"}) == ["2027-Q1"]
    # l'archive réduite (vraie, deux fichiers) : l'avis est trouvé, un autre article ne l'est pas
    article = dom.usury_article(read("JORF_20260926-210736.tar.gz"))
    assert article is not None and dom.parse_usury(article).quarter == "2026-Q4"
    assert dom.usury_article(_tar({"x/jorf/global/article/JORF/ARTI/a.xml": b"<ARTICLE/>"})) is None


def test_read_jorf_fetches_only_what_is_missing() -> None:
    seen: list[str] = []
    empty = _tar({"vide.txt": b""})

    def fetch(url: str, accept: str) -> bytes:
        seen.append(url.rsplit("/", 1)[-1])
        if url == dom.DILA_JORF_URL:
            assert accept == dom.HTML_ACCEPT
            return read("dila_jorf_listing.html")
        assert accept == dom.GZIP_ACCEPT
        return read("JORF_20260926-210736.tar.gz") if url.endswith("20260926-210736.tar.gz") else empty

    today = date(2026, 10, 24)
    assert bsvc.read_jorf(fetch, today, frozenset({"2026-Q4"})) == [] and seen == [], "rien à chercher : aucun appel"
    (obs,) = bsvc.read_jorf(fetch, today, frozenset())
    assert seen[0] == "" and seen[-1] == "JORF_20260926-210736.tar.gz" and len(seen) == 9
    assert (obs.series, obs.period) == ("jorf.usure", "2026-Q4") and obs.source_url.endswith("JORFTEXT000054903528")
    stored = dom.usury_from_observation(obs.period, obs.value, obs.note, obs.source_url)
    assert stored is not None and stored.thresholds == QUARTERS["2026-Q4"].thresholds

    def nothing(url: str, accept: str) -> bytes:
        return read("dila_jorf_listing.html") if url == dom.DILA_JORF_URL else empty

    # fenêtre close, avis absent : les archives parcourues sont rendues, « introuvable » vient quand il n'en reste
    # plus à ouvrir (le passage suivant ne retélécharge que la liste)
    seen.clear()
    (scanned,) = bsvc.read_jorf(nothing, today, frozenset())
    assert scanned.series == bsvc.SCANNED_SERIES and scanned.period == "2026-Q4"
    names = json.loads(scanned.value)
    assert len(names) == dom.MAX_ARCHIVES
    done = frozenset(f"{bsvc.SCANNED_PREFIX}{n}" for n in names)
    with pytest.raises(dbar.BaremeError, match="introuvable"):
        bsvc.read_jorf(nothing, today, done)
    pending = bsvc.read_jorf(nothing, date(2026, 9, 28), frozenset({"2026-Q3"}))
    assert [o.series for o in pending] == [bsvc.SCANNED_SERIES], "avis pas encore paru : rien que des archives vues"


def test_read_jorf_skips_what_is_not_the_avis_and_keeps_to_its_budget() -> None:
    fetched: list[str] = []
    other = _tar({"x/jorf/global/article/JORF/ARTI/r.xml": b"<ARTICLE>concernant l'usure<BLOC_TEXTUEL>x</ARTICLE>"})
    broken = b"pas une archive"

    def fetch(url: str, accept: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        fetched.append(name)
        if url == dom.DILA_JORF_URL:
            return read("dila_jorf_listing.html")
        if name.endswith("20260930-215413.tar.gz"):
            return broken  # archive abîmée : on passe à la suivante
        if name.startswith("JORF_202609") and name > "JORF_20260927":
            return other  # un autre texte sur l'usure (rectificatif) : on passe aussi
        return read("JORF_20260926-210736.tar.gz") if name.endswith("20260926-210736.tar.gz") else _tar({})

    (obs,) = bsvc.read_jorf(fetch, date(2026, 10, 24), frozenset())
    assert obs.series == "jorf.usure" and obs.period == "2026-Q4"
    # deux trimestres cherchés (fin décembre) : douze archives au plus pour la relève entière
    fetched.clear()
    late = bsvc.read_jorf(lambda u, a: _tar({}) if u.endswith(".gz") else fetch(u, a), date(2026, 12, 30), frozenset())
    opened = [n for o in late for n in json.loads(o.value)]
    assert {o.period for o in late} <= {"2026-Q4", "2027-Q1"} and len(opened) == bsvc.JORF_RUN_ARCHIVES
    # le temps aussi : passé le budget, la suite attend le passage suivant
    ticks = iter([0.0, 0.0, 1_000.0])
    fetched.clear()
    out = bsvc.read_jorf(fetch, date(2026, 10, 24), frozenset(), clock=lambda: next(ticks, 1_000.0))
    assert [o.series for o in out] == [bsvc.SCANNED_SERIES] and len(json.loads(out[0].value)) == 1


def test_an_avis_not_yet_found_is_looked_for_again_the_next_day(app, auth_client: TestClient) -> None:  # type: ignore[no-untyped-def]
    from app.services import consents

    listing = b"".join(f'<a href="JORF_202612{d}-002728.tar.gz">'.encode() for d in (18, 19))

    def only_jorf(url: str, accept: str) -> bytes:
        if url == dom.DILA_JORF_URL:
            return listing
        if url.startswith(dom.DILA_JORF_URL):
            return _tar({})
        raise AssertionError(url)  # les autres sources sont en pause dans ce test

    day = date(2026, 12, 20)  # avis du 1er trimestre 2027 attendu, pas encore paru
    now = datetime(2026, 12, 20, 8, tzinfo=UTC)
    with app.state.sessionmaker() as s:
        consents.set_consent(s, bsvc.CONSENT, True, actor="t")
        for slug, src in bsvc.ensure_sources(s).items():
            src.enabled = slug == "bareme-jorf"
        s.commit()
        bsvc.refresh(s, now=now, today=day, fetch=only_jorf, actor="t")
        src = bsvc.ensure_sources(s)["bareme-jorf"]
        assert src.last_ok_at is None and src.last_error is None, "pas compté relu : relu demain, pas dans un mois"
        known = bsvc._known_periods(s)
        assert any(k.startswith(bsvc.SCANNED_PREFIX) for k in known), "archives vues gardées, pas retéléchargées"
        assert not bsvc._due(src, now + timedelta(hours=2), bsvc._pending("bareme-jorf", day, known))
        assert bsvc._due(src, now + timedelta(hours=21), bsvc._pending("bareme-jorf", day, known))


# ------------------------------------------------------------------ découvert
def _history(agios_cents: int, balance_on: date = date(2026, 10, 21)) -> dom.AccountHistory:
    moves = [
        dom.Movement(1, date(2026, 9, 5), -150_000, "PRLV SEPA FONCIA LOYER"),
        dom.Movement(2, date(2026, 10, 5), -agios_cents, "AGIOS TRIMESTRIELS"),
    ]
    return dom.AccountHistory(7, "Compte courant", -100_000 - agios_cents, balance_on, moves)


def test_agios_above_the_usury_threshold_with_the_method_shown() -> None:
    (cost,) = dom.overdraft_costs(_history(2_500), date(2026, 10, 21))
    # du 05/07 au 04/10 : 30 jours à −1 000 € (du 05/09 au 04/10), le reste créditeur
    assert (cost.start, cost.end, cost.debit_days, cost.average_debit_cents) == (
        date(2026, 7, 5),
        date(2026, 10, 4),
        30,
        100_000,
    )
    assert cost.rate.quantize(Decimal("0.01")) == Decimal("30.42")  # 25 × 365 ÷ (1 000 × 30)
    (f,) = dom.overdraft_findings([cost], QUARTERS)
    assert f.key == "usure_decouvert:7:2026-10-05" and f.topic == "credits" and f.level == "warning"
    assert "30,4 %" in f.title and "23,6 %" in f.title, "le seuil le plus haut des trimestres couverts"
    assert "Estimation Bank" in f.body and "Méthode" in f.body and "ticket d'agios" in f.body
    assert "19,40\xa0€" in f.body, "au seuil : 1 000 € × 30 jours × 23,6 % ÷ 365"
    assert f.evidence[0].ref == "txn:2" and f.evidence[-1].ref.startswith("url:https://www.legifrance")  # type: ignore[union-attr]
    # 15 € pour la même période : environ 18 %, sous le seuil
    assert dom.overdraft_findings(dom.overdraft_costs(_history(1_500), date(2026, 10, 21)), QUARTERS) == []
    # un trimestre dont Bank n'a pas l'avis : rien n'est dit
    only_q4 = {k: v for k, v in QUARTERS.items() if k == "2026-Q4"}
    assert dom.overdraft_findings([cost], only_q4) == []


def test_a_rate_just_above_the_threshold_is_not_enough() -> None:
    (cost,) = dom.overdraft_costs(_history(1_960), date(2026, 10, 21))  # environ 23,8 % : moins d'un point au-dessus
    assert Decimal("23.6") < cost.rate < Decimal("24.6")
    assert dom.overdraft_findings([cost], QUARTERS) == []


# ------------------------------------------------------------------ crédits
def _loan(rate_bp: int, first: date, principal: int = 200_000, months: int = 24, kind: str = "consommation") -> dl.Loan:
    return dl.Loan(principal, rate_bp, months, first, 0, kind, "Prêt")  # type: ignore[arg-type]


def test_actuarial_rate_and_the_loan_usury_check() -> None:
    assert dom.actuarial_rate(_loan(600, date(2026, 1, 5))).quantize(Decimal("0.01")) == Decimal("6.17")
    assert dom.actuarial_rate(_loan(0, date(2026, 1, 5))) == 0
    high = dom.TrackedLoan(3, "Crédit renouvelable", _loan(2_200, date(2026, 11, 15)))
    (f,) = dom.loan_usury_findings([high], QUARTERS)
    assert f.key == "usure_pret:3" and "24,36 %" in f.title and "23,6 %" in f.title
    assert "TAEG de ton offre est au moins aussi haut" in f.body
    assert dom.loan_usury_findings([dom.TrackedLoan(4, "Auto", _loan(2_000, date(2026, 11, 15)))], QUARTERS) == []
    old = dom.TrackedLoan(5, "Ancien", _loan(2_200, date(2024, 3, 15)))
    assert dom.loan_usury_findings([old], QUARTERS) == [], "seuils de 2024 inconnus de Bank : rien n'est dit"


def test_renegotiation_runs_the_simulator_at_the_market_average() -> None:
    home = dl.Loan(30_000_000, 440, 300, date(2023, 1, 5), 0, "immobilier", "Maison")
    market = dom.latest_averages(QUARTERS, TODAY)
    assert market is not None and market.quarter == "2026-Q4"
    (f,) = dom.renegotiation_findings([dom.TrackedLoan(9, "Maison", home)], market, TODAY)
    sim = dl.renegotiate(home, TODAY, 405, fees_cents=0, buyout=True)  # 20 ans et plus : 4,05 % au 3e trimestre
    assert sim.months_left >= 240 and sim.net_gain_cents >= dom.MIN_RENEGOTIATION_GAIN_CENTS
    assert f.key == "renegociation:9:2026-Q4" and "4,05 %" in f.title
    assert dom.eur0(sim.net_gain_cents) in f.title and dom.eur0(sim.indemnity_cents) in f.body
    assert "Un taux moyen n'est pas une offre" in f.body and "assurance et frais compris" in f.body
    assert any(e.ref == "loan:9:405" for e in f.evidence), "le lien ouvre le simulateur avec ce taux"
    cheap = dl.Loan(30_000_000, 350, 300, date(2023, 1, 5), 0, "immobilier", "Maison")
    assert dom.renegotiation_findings([dom.TrackedLoan(9, "Maison", cheap)], market, TODAY) == []
    assert dom.latest_averages(QUARTERS, date(2027, 9, 1)) is None, "un taux moyen trop ancien ne sert plus"


# ------------------------------------------------------------------ frais réels
def test_the_mileage_scale_and_its_rules() -> None:
    assert SCALE is not None and SCALE.incomes_year == 2025 and "27 mars 2023" in SCALE.source
    assert SCALE.cost_cents(10_000, 6, False) == 519_700  # 10 000 × 0,374 + 1 457
    assert SCALE.cost_cents(4_000, 4, True) == 290_880  # 4 000 × 0,606, +20 % en électrique
    assert SCALE.cost_cents(25_000, 9, False) == 1_175_000, "plafonné à 7 CV : 25 000 × 0,470"
    assert MODEL is not None
    flat = iw.estimate_tax(MODEL, [3_000_000], Decimal(1), False)
    assert iw.estimate_tax(MODEL, [3_000_000], Decimal(1), False, deductions_cents=[611_854]) < flat


def test_real_expenses_against_the_flat_deduction() -> None:
    days = dom.WorkDays(2026, 150, date(2026, 2, 1), date(2026, 12, 31))
    assert days.projected == 164  # 150 jours sur 334, rapportés à 365
    salary = dom.SalaryBasis(3_000_000, 0, Decimal(1), False, "test")
    commute = dom.Commute(Decimal(38), 6, False)
    (f,) = dom.real_expenses_findings(days, commute, salary, SCALE, MODEL, TODAY)
    assert f.key == "frais_reels:2026" and f.topic == "impots"
    assert "6 119 €" in f.title and "3 000 €" in f.title  # 12 464 km × 0,374 + 1 457 ; 10 % de 30 000 €
    assert "12 464 km" in f.body and "revenus 2025" in f.body and "revenus 2026 n'est pas encore publié" in f.body
    assert MODEL is not None
    saved = iw.estimate_tax(MODEL, [3_000_000], Decimal(1), False) - iw.estimate_tax(
        MODEL, [3_000_000], Decimal(1), False, deductions_cents=[611_854]
    )
    assert f.yearly_cents == saved and dom.eur0(saved) in f.body
    far = dom.real_expenses_findings(days, dom.Commute(Decimal(70), 6, False), salary, SCALE, MODEL, TODAY)
    assert "40 km par trajet au plus" in far[0].body and "12 464 km" not in far[0].body
    short = dom.real_expenses_findings(days, dom.Commute(Decimal(5), 4, False), salary, SCALE, MODEL, TODAY)
    assert short == [], "l'abattement de 10 % reste plus favorable : rien à dire"
    (ask,) = dom.real_expenses_findings(days, None, salary, SCALE, MODEL, TODAY)
    assert ask.kind == "real_expenses_ask" and "150 jours" in ask.body
    (missing,) = dom.real_expenses_findings(days, commute, None, SCALE, MODEL, TODAY)
    assert missing.key == "frais_reels_missing:2026" and "bulletins de paie" in missing.body


# ------------------------------------------------------------------ jamais un conseil
FORBIDDEN = ("recommand", "conseill", "urgent", "maintenant ou jamais", "tu as droit", "te doit", "rembourse-toi")


def test_texts_are_findings_not_advice() -> None:
    found: list[Finding] = dom.overdraft_findings(dom.overdraft_costs(_history(2_500), TODAY), QUARTERS)
    found += dom.loan_usury_findings([dom.TrackedLoan(3, "R", _loan(2_200, date(2026, 11, 15)))], QUARTERS)
    home = dl.Loan(30_000_000, 440, 300, date(2023, 1, 5), 0, "immobilier", "Maison")
    found += dom.renegotiation_findings([dom.TrackedLoan(9, "M", home)], dom.latest_averages(QUARTERS, TODAY), TODAY)
    days = dom.WorkDays(2026, 150, date(2026, 2, 1), date(2026, 12, 31))
    salary = dom.SalaryBasis(3_000_000, 0, Decimal(1), False, "test")
    found += dom.real_expenses_findings(days, dom.Commute(Decimal(38), 6, True), salary, SCALE, MODEL, TODAY)
    found += dom.real_expenses_findings(days, None, salary, SCALE, MODEL, TODAY)
    assert len(found) == 5
    for f in found:
        for text in (f.title, f.body, *(e.value for e in f.evidence)):
            folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
            assert not [w for w in FORBIDDEN if w in folded], text
            assert "!" not in text and "réclamable" not in text


# ------------------------------------------------------------------ service : relevés, prêts, agenda, trajet
@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    with app.state.sessionmaker() as db:
        seed_household(db)
        db.commit()
    yield auth_client
    app.dependency_overrides.clear()


def _keys(client: TestClient) -> dict[str, dict[str, object]]:
    return {f["key"]: f for f in client.get("/api/correlations").json()["findings"]}


def test_overdraft_and_loans_reach_the_correlations(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        acc = Account(name="Compte joint", kind=AccountKind.CHECKING, provider=Provider.FILE)
        s.add(acc)
        s.flush()
        ingest(s, acc, [ParsedTransaction(date(2026, 9, 5), -150_000, "PRLV SEPA FONCIA LOYER"),
                        ParsedTransaction(date(2026, 10, 5), -2_500, "AGIOS TRIMESTRIELS")], provider="file")  # fmt: skip
        acc.balance_cents, acc.balance_at = -102_500, datetime(2026, 10, 21, 12, tzinfo=UTC)
        s.add(Loan(name="Renouvelable", kind="consommation", principal_cents=200_000, annual_rate_bp=2_200,
                   months=24, first_payment=date(2026, 11, 15), insurance_monthly_cents=0))  # fmt: skip
        s.add(Loan(name="Maison", kind="immobilier", principal_cents=30_000_000, annual_rate_bp=440, months=300,
                   first_payment=date(2023, 1, 5), insurance_monthly_cents=0))  # fmt: skip
        s.commit()
        acc_id = acc.id
    found = _keys(world)
    assert f"usure_decouvert:{acc_id}:2026-10-05" in found
    assert found[f"usure_decouvert:{acc_id}:2026-10-05"]["topic_label"] == "Crédits"
    assert any(k.startswith("usure_pret:") for k in found) and any(k.startswith("renegociation:") for k in found)
    # un solde sans date ne permet pas de remonter le temps
    with app.state.sessionmaker() as s:
        row = s.get(Account, acc_id)
        assert row is not None
        row.balance_at = None
        s.commit()
    assert f"usure_decouvert:{acc_id}:2026-10-05" not in _keys(world)


def test_the_commute_is_asked_kept_encrypted_and_exported(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        plan = WorkPlan(source="file", label="Planning")
        s.add(plan)
        s.flush()
        d = date(2026, 2, 2)
        while d < date(2026, 10, 20):
            if d.weekday() < 5:
                s.add(WorkShift(plan_id=plan.id, day=d, start_day=d, hours=0.0, all_day=True, title="Poste"))
            d += timedelta(days=1)
        s.commit()
    found = _keys(world)
    assert "frais_reels_ask:2026" in found and found["frais_reels_ask:2026"]["topic_label"] == "Impôts"
    r = world.put("/api/correlations/commute", json={"one_way_km": "38", "fiscal_hp": 6, "electric": False})
    assert r.status_code == 200 and r.json()["commute"] == {"one_way_km": "38.0", "fiscal_hp": 6, "electric": False}
    keys = {f["key"] for f in r.json()["findings"]}
    # pas de taux de prélèvement connu : Bank dit ce qui manque au lieu d'estimer les salaires
    assert "frais_reels_ask:2026" not in keys and "frais_reels_missing:2026" in keys
    assert world.put("/api/correlations/commute", json={"one_way_km": "0", "fiscal_hp": 6}).status_code == 422
    from tests.conftest import PASSWORD

    exported = json.loads(world.post("/api/export", json={"password": PASSWORD}).content)
    assert exported["commute"]["fiscal_hp"] == 6
    with app.state.engine.connect() as conn:
        raw = conn.exec_driver_sql("SELECT data FROM commute_profile").scalar_one()
    assert "38" not in raw, "chiffré au repos"
    assert world.delete("/api/correlations/commute").json()["commute"] is None


def test_webstat_is_optional_and_its_key_never_comes_back(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    status = world.get("/api/baremes/webstat").json()
    assert status["configured"] is False and "Journal officiel" in status["message"]
    usury = next(g for g in world.get("/api/baremes").json()["in_effect"] if g["key"] == "usury")
    assert usury["title"] == "Seuils de l'usure (4e trimestre 2026)" and usury["as_of"] == "2026-10-01"
    assert any(i["value"] == "23,60 %" for i in usury["items"])
    saved = world.put("/api/baremes/webstat", json={"api_key": "abcd1234efgh5678"}).json()
    assert saved["configured"] is True and "abcd1234" not in json.dumps(saved) and "5678" in saved["message"]
    assert "pas encore" in saved["message"]
    with app.state.sessionmaker() as s:
        assert s.scalar(select(PublicSourceKey.api_key)) == "abcd1234efgh5678"
    with app.state.engine.connect() as conn:
        assert "efgh" not in conn.exec_driver_sql("SELECT api_key FROM public_source_keys").scalar_one()
    assert world.delete("/api/baremes/webstat").json()["configured"] is False


def test_no_network_in_the_findings() -> None:
    import inspect

    source = inspect.getsource(svc) + inspect.getsource(dom)
    assert "netfetch" not in source and "httpx" not in source
