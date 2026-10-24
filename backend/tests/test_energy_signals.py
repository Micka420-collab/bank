"""Signaux d'énergie (#33) : puissance maximale Linky (réponse réelle de `puissance_conso_max_quotidienne`) face aux
kVA souscrits, économie chiffrée sur la grille de la CRE (vrais CSV du 24/10/2026), et couleur Tempo relue chez
api-couleur-tempo.fr (vraies réponses) avec l'alerte la veille d'un jour rouge ; rien ne part sans accord, aucun
chiffre sans source, aucun ton de conseil."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import baremes as bdom
from app.domain import energy_signals as sig
from app.domain.correlations import power_findings
from app.domain.energy import DailyReading
from app.models import EnergyMaxPower, RateObservation, TempoDay
from app.providers.conso_api import ConsoApiClient, ConsoApiError
from app.services import channel_alerts, tempo
from tests.conftest import PASSWORD
from tests.test_territory import Chunks

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 24)
TOKEN = "a" * 40 + "." + "b" * 40 + "." + "c" * 40
PRM = "12345678901234"
# Ce qu'un constat ne dit jamais (même liste que les alertes de cours) : pas d'invitation pressante, pas de conseil.
FORBIDDEN = (
    "achet", "vend", "profit", "maintenant", "opportunit", "occasion", "fonce", "urgent", "depeche", "recommand",
    "conseille", "investi", "rate pas", "dernier moment",
)  # fmt: skip


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def plain(text: str) -> str:
    return text.replace("\xa0", " ")  # format_eur sépare les euros par une espace insécable


def assert_factual(text: str) -> None:
    hits = [w for w in FORBIDDEN if re.search(rf"\b{w}", fold(text))]
    assert not hits, (text, hits)
    assert "!" not in text


def cre_latest(options: tuple[str, ...] = ("base", "hphc", "tempo")) -> dict[str, tuple[str, str, str]]:
    out: dict[str, tuple[str, str, str]] = {}
    for o in options:
        body = (FIX / "baremes" / bdom.CRE_FILES[o]).read_bytes()
        for ob in bdom.parse_cre(body, bdom.cre_url(o), TODAY, o):
            out[ob.series] = (ob.period, ob.value, ob.source_url)
    return out


def year_of(va: int, *, days: int = 365, peak: tuple[date, int] | None = None) -> list[sig.MaxPower]:
    rows = [sig.MaxPower(TODAY - timedelta(days=i), va, "19:02:00") for i in range(1, days + 1)]
    if peak:
        rows = [sig.MaxPower(r.day, peak[1], "07:45:10") if r.day == peak[0] else r for r in rows]
    return sorted(rows, key=lambda r: r.day)


# ------------------------------------------------------------------ lecture des sources
def test_max_power_reads_the_real_v2_response() -> None:
    rows = sig.parse_max_power(json.loads((FIX / "energie" / "puissance_max_quotidienne.json").read_bytes()))
    assert [(r.day, r.va, r.at) for r in rows] == [
        (date(2026, 4, 1), 3533, "04:18:59"),
        (date(2026, 4, 2), 3092, "04:13:22"),
        (date(2026, 4, 3), 3494, "04:12:39"),
    ]
    kva = {"grandeur": [{"grandeurPhysique": "PMA", "unite": "kVA", "points": [{"v": "4,2", "d": "2026-04-01"}]}]}
    assert sig.parse_max_power(kva)[0].va == 4200
    holes = {
        "grandeur": [
            {
                "grandeurPhysique": "PMA",
                "unite": "VA",
                "points": [{"v": None, "d": "2026-04-01"}, {"v": "-5", "d": "2026-04-02"}, {"v": "9e9", "d": "x"}],
            }
        ]
    }
    assert sig.parse_max_power(holes) == []
    bads: list[object] = [{"grandeur": [{"unite": "W", "points": []}]}, {"autre": 1}, []]
    for bad in bads:
        with pytest.raises(sig.SignalFormatError):
            sig.parse_max_power(bad)


def test_tempo_reads_the_real_responses() -> None:
    red = sig.parse_tempo((FIX / "energie" / "tempo_jours_rouge.json").read_bytes())
    assert red == {date(2025, 12, 28): "bleu", date(2025, 12, 29): "rouge", date(2025, 12, 30): "blanc"}
    unknown = sig.parse_tempo((FIX / "energie" / "tempo_jours_inconnu.json").read_bytes())
    assert unknown[date(2026, 10, 26)] is None, "codeJour 0 : pas encore publiée, jamais devinée"
    assert sig.parse_tempo((FIX / "energie" / "tempo_jour_blanc.json").read_bytes()) == {date(2026, 1, 14): "blanc"}
    for bad in (b"<html>", b'[{"dateJour": "2026-10-24", "codeJour": 7}]', b'[{"dateJour": "hier", "codeJour": 1}]'):
        with pytest.raises(sig.SignalFormatError):
            sig.parse_tempo(bad)
    url = sig.tempo_url([date(2026, 10, 24), date(2026, 10, 25)])
    assert url == "https://www.api-couleur-tempo.fr/api/joursTempo?dateJour%5B%5D=2026-10-24&dateJour%5B%5D=2026-10-25"


def test_conso_client_asks_for_daily_pma_by_safe_chunks() -> None:
    seen: list[httpx.Request] = []
    sample = (FIX / "energie" / "puissance_max_quotidienne.json").read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=sample)

    client = ConsoApiClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = client.daily_max_power(PRM, date(2026, 1, 1), date(2026, 12, 1))
    assert [r.day for r in rows] == [date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]
    assert len(seen) == 2
    first = seen[0]
    assert first.url.path == "/api/puissance_conso_max_quotidienne"
    assert dict(first.url.params) == {
        "pointId": PRM,
        "dateDebut": "2026-01-01",
        "dateFin": "2026-06-30",
        "mesuresPas": "P1D",
        "grandeurPhysique": "PMA",
    }
    broken = ConsoApiClient(
        TOKEN, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"grandeur": 3})))
    )
    with pytest.raises(ConsoApiError, match="puissance maximale non reconnu"):
        broken.daily_max_power(PRM, date(2026, 4, 1), date(2026, 4, 4))


# ------------------------------------------------------------------ le constat
def test_power_too_high_counts_subscription_and_kwh_on_the_cre_grid() -> None:
    grid = sig.grid_from_observations("base", cre_latest(("base",)), "CRE")
    peak_day = TODAY - timedelta(days=280)
    check = sig.power_check(
        year_of(3_100, peak=(peak_day, 4_300)),
        today=TODAY,
        subscribed_kva=9,
        option="base",
        grid=grid,
        yearly_wh=3_650_000,
    )
    assert check.status == "too_high" and check.target_kva == 6 and check.max_va == 4_300
    # Abonnement : 239,84 € − 190,81 € = 49,03 € ; kWh : 3 650 kWh × (0,2001 − 0,1985) € = 5,84 € de plus en 6 kVA.
    assert check.subscription_saving_cents == 4_903 and check.kwh_effect_cents == 584
    assert check.yearly_saving_cents == 4_319 and check.grid_as_of == "2026-08-01"
    assert "4,3 kVA" in plain(check.message) and f"{peak_day:%d/%m/%Y}" in plain(check.message)
    assert "environ 43,19 €" in plain(check.message) and "grille de la CRE du 01/08/2026" in plain(check.message)
    assert "fournisseur" in plain(check.message) and "Enedis peut facturer" in plain(check.message)
    assert "non vérifié" in plain(check.message), "les frais Enedis ne sont jamais inventés"
    assert_factual(check.message)
    (finding,) = power_findings(check)
    assert finding.kind == "power_too_high" and finding.yearly_cents == 4_319 and finding.level == "warning"
    assert any(e.ref == f"url:{bdom.cre_url('base')}" for e in finding.evidence)
    assert_factual(finding.title + finding.body)


def test_power_margin_tempo_and_what_is_missing_is_said() -> None:
    grid = sig.grid_from_observations("base", cre_latest(("base",)), "CRE")
    kw = {"today": TODAY, "subscribed_kva": 9, "option": "base", "grid": grid, "yearly_wh": 3_650_000}
    fits = sig.power_check(year_of(5_200), **kw)  # type: ignore[arg-type]
    assert fits.status == "fits" and "moins de 1 kVA de marge" in fits.message and not power_findings(fits)
    short = sig.power_check(year_of(3_000, days=200), **kw)  # type: ignore[arg-type]
    assert short.status == "too_few_months" and short.months_available == 6 and "il en faut 12" in short.message
    assert not power_findings(short)
    holes = [r for r in year_of(3_000) if r.day.month not in (1, 2)]
    assert sig.power_check(holes, **kw).status == "too_few_months"  # type: ignore[arg-type]
    none = sig.power_check(year_of(3_000), **{**kw, "subscribed_kva": None})  # type: ignore[arg-type]
    assert none.status == "no_contract" and "puissance souscrite" in none.message
    # Base sans consommation connue : le kWh change avec la puissance, l'économie totale n'est pas chiffrée.
    no_wh = sig.power_check(year_of(3_000), **{**kw, "yearly_wh": None})  # type: ignore[arg-type]
    assert no_wh.status == "too_high" and no_wh.yearly_saving_cents is None and "ne chiffre pas" in no_wh.message
    assert power_findings(no_wh)[0].yearly_cents is None
    # Heures creuses : mêmes prix du kWh en 6 et 9 kVA au 01/08/2026 → 239,84 − 190,81 = 49,02 € (au centime près).
    hphc = sig.grid_from_observations("hphc", cre_latest(("hphc",)), "CRE")
    h = sig.power_check(year_of(3_000), **{**kw, "option": "hphc", "grid": hphc})  # type: ignore[arg-type]
    assert h.status == "too_high" and h.yearly_saving_cents == 4_902 and "environ" in plain(h.message)
    # Sans grille relevée pour l'option : Bank le dit au lieu d'inventer.
    nogrid = sig.power_check(year_of(3_000), **{**kw, "option": "hphc", "grid": {}})  # type: ignore[arg-type]
    assert nogrid.status == "no_grid" and "Barèmes tenus à jour" in nogrid.message and not power_findings(nogrid)
    # Tempo : mêmes prix du kWh en 6 et 9 kVA → seule la différence d'abonnement (237,34 − 189,98 = 47,36 €).
    tgrid = sig.grid_from_observations("tempo", cre_latest(("tempo",)), "CRE")
    t = sig.power_check(year_of(3_000), **{**kw, "option": "tempo", "grid": tgrid})  # type: ignore[arg-type]
    assert t.status == "too_high" and t.target_kva == 6 and t.yearly_saving_cents == 4_736 and t.kwh_effect_cents == 0
    # 3 kVA n'est jamais visé : la grille Tempo commence à 6 kVA.
    assert 3 not in tgrid


# ------------------------------------------------------------------ service et API
class FakeConso:
    rows: ClassVar[list[sig.MaxPower]] = []
    fail: ClassVar[ConsoApiError | None] = None
    calls: ClassVar[list[tuple[date, date]]] = []

    def __init__(self, token: str) -> None:
        assert token == TOKEN

    def daily_consumption(self, prm: str, start: date, end_exclusive: date) -> list[DailyReading]:
        return [DailyReading(start + timedelta(days=i), 10_000) for i in range((end_exclusive - start).days)]

    def daily_max_power(self, prm: str, start: date, end_exclusive: date) -> list[sig.MaxPower]:
        FakeConso.calls.append((start, end_exclusive))
        if FakeConso.fail is not None:
            raise FakeConso.fail
        return [r for r in FakeConso.rows if start <= r.day < end_exclusive] + [sig.MaxPower(TODAY, 1)]


@pytest.fixture
def world(app, auth_client: TestClient) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    app.dependency_overrides[deps.today] = lambda: TODAY
    app.state.energy_client_factory = FakeConso
    FakeConso.rows, FakeConso.fail, FakeConso.calls = year_of(3_100, peak=(TODAY - timedelta(days=40), 4_300)), None, []
    with app.state.sessionmaker() as db:
        now = datetime(2026, 10, 1, tzinfo=UTC)
        for series, (period, value, url) in cre_latest().items():
            db.add(RateObservation(series=series, period=period, value=value, source_url=url, fetched_at=now))
        db.commit()
    yield auth_client
    app.dependency_overrides.clear()


def test_linky_brings_max_power_and_the_finding(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    r = world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN})
    assert r.status_code == 201, r.text
    power = r.json()["power"]
    assert power["status"] == "no_contract" and power["max_kva"] == 4.3 and power["days"] == 365
    with app.state.sessionmaker() as db:
        days = list(db.scalars(select(EnergyMaxPower.day)))
        assert TODAY not in days and len(days) == 365, "un jour non terminé n'est pas gardé"
    assert world.put("/api/energy/contract", json={"option": "base", "kva": 7}).status_code == 422
    st = world.put("/api/energy/contract", json={"option": "base", "kva": 9}).json()
    assert st["contract"] == {"option": "base", "kva": 9}
    power = st["power"]
    assert power["status"] == "too_high" and power["target_kva"] == 6 and power["grid_as_of"] == "2026-08-01"
    assert power["yearly_saving_cents"] == 4_903 - 584 and power["grid_url"] == bdom.cre_url("base")
    assert power["grid_source"].startswith("CRE") and "relus le 01/10/2026" in power["grid_source"]
    findings = world.get("/api/correlations").json()["findings"]
    f = next(x for x in findings if x["kind"] == "power_too_high")
    assert f["yearly_cents"] == 4_319 and f["topic"] == "energie"
    # Choisir une ligne de la grille comme tarif règle aussi le contrat.
    world.put(
        "/api/energy/tariff",
        json={"cents_per_100kwh": 2001, "subscription_cents_per_year": 19081, "label": "Base 6 kVA"},
    )
    assert world.get("/api/energy").json()["contract"] == {"option": "base", "kva": 6}
    data = world.post("/api/export", json={"password": PASSWORD}).json()
    assert len(data["energy_max_power"]) == 365 and data["energy_contract"] == {"option": "base", "kva": 6}
    assert world.delete("/api/energy/linky").status_code == 204
    with app.state.sessionmaker() as db:
        assert list(db.scalars(select(EnergyMaxPower))) == []


def test_max_power_failure_is_reported_apart(app, world: TestClient) -> None:  # type: ignore[no-untyped-def]
    FakeConso.fail = ConsoApiError("Enedis / Conso API indisponible (503)", 503)
    st = world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN}).json()
    assert st["connection"]["status"] == "active", "la consommation, elle, est arrivée"
    assert "indisponible" in st["power"]["last_error"] and st["power"]["status"] == "no_data"
    FakeConso.fail = None
    st = world.post("/api/energy/sync").json()
    assert st["power"]["last_error"] is None and st["power"]["days"] == 365
    start, _ = FakeConso.calls[-1]
    assert start == TODAY - timedelta(days=400), "rien n'était gardé : rattrapage complet"


# ------------------------------------------------------------------ Tempo
class TempoNet:
    """api-couleur-tempo.fr simulé avec ses vraies réponses ; l'hôte se lit dans l'en-tête Host (netfetch épingle
    la connexion sur l'adresse résolue)."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["host"] == "www.api-couleur-tempo.fr"
        if self.down:
            return httpx.Response(503, stream=Chunks(b""))
        return httpx.Response(200, stream=Chunks(self.body), headers={"content-type": "application/json"})


def red_tomorrow() -> bytes:
    # La vraie réponse du 28-30/12/2025, décalée sur hier, aujourd'hui, demain.
    rows = json.loads((FIX / "energie" / "tempo_jours_rouge.json").read_bytes())
    for i, row in enumerate(rows):
        row["dateJour"] = (TODAY + timedelta(days=i - 1)).isoformat()
    return json.dumps(rows).encode()


@pytest.fixture
def tnet(app, world: TestClient) -> tuple[TestClient, TempoNet]:  # type: ignore[no-untyped-def]
    fake = TempoNet(red_tomorrow())
    app.state.tempo_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.tempo_resolver = lambda host: ["93.184.216.34"]
    return world, fake


def test_tempo_needs_consent_then_stores_known_colours(app, tnet: tuple[TestClient, TempoNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = tnet
    view = client.get("/api/energy").json()["tempo"]
    assert view["consent"] is False and view["today"] is None and "RTE" in view["source"]
    r = client.post("/api/energy/tempo/refresh")
    assert r.status_code == 409 and "Couleur Tempo" in r.json()["detail"] and fake.requests == []
    assert client.put("/api/consents/tempo", json={"granted": True}).status_code == 200
    st = client.post("/api/energy/tempo/refresh").json()
    assert st["tempo_error"] is None
    assert st["tempo"]["today"]["color"] == "rouge" and st["tempo"]["tomorrow"]["label"] == "Blanc"
    (req,) = fake.requests
    assert req.url.path == "/api/joursTempo" and req.url.query.decode().count("dateJour%5B%5D=") == 3
    # « Pas encore publiée » ne s'écrit pas ; une panne garde les couleurs connues.
    fake.body = (FIX / "energie" / "tempo_jours_inconnu.json").read_bytes()
    fake.down = True
    st = client.post("/api/energy/tempo/refresh").json()
    assert "les couleurs connues restent" in st["tempo_error"] and st["tempo"]["today"]["color"] == "rouge"
    with app.state.sessionmaker() as db:
        assert {r.day: r.color for r in db.scalars(select(TempoDay))}[TODAY + timedelta(days=1)] == "blanc"


def test_tick_is_quiet_when_today_and_tomorrow_are_known(app, tnet: tuple[TestClient, TempoNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = tnet
    fetch = tempo.default_fetch(http=app.state.tempo_http, resolver=app.state.tempo_resolver)
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    with app.state.sessionmaker() as db:
        assert tempo.tick(db, now=now, today=TODAY, fetch=fetch) == {}, "sans accord, rien"
    client.put("/api/consents/tempo", json={"granted": True})
    fake.body = (FIX / "energie" / "tempo_jours_inconnu.json").read_bytes()  # demain pas encore publié
    with app.state.sessionmaker() as db:
        rows = json.loads(fake.body)
        for i, row in enumerate(rows):
            row["dateJour"] = (TODAY + timedelta(days=i - 1)).isoformat()
        fake.body = json.dumps(rows).encode()
        assert tempo.tick(db, now=now, today=TODAY, fetch=fetch)["tempo_stored"] == 2
        db.commit()
        assert tempo.tick(db, now=now + timedelta(minutes=30), today=TODAY, fetch=fetch) == {}, "2 h entre deux essais"
        fake.body = red_tomorrow()
        assert tempo.tick(db, now=now + timedelta(hours=3), today=TODAY, fetch=fetch)["tempo_stored"] == 2
        db.commit()
        assert tempo.tick(db, now=now + timedelta(hours=6), today=TODAY, fetch=fetch) == {}
    assert len(fake.requests) == 2


def test_red_day_alert_the_evening_before_for_a_tempo_contract(app, tnet: tuple[TestClient, TempoNet]) -> None:  # type: ignore[no-untyped-def]
    client, _ = tnet
    client.put("/api/consents/tempo", json={"granted": True})
    client.post("/api/energy/tempo/refresh")
    with app.state.sessionmaker() as db:
        db.merge(TempoDay(day=TODAY + timedelta(days=1), color="rouge", source_url=sig.TEMPO_URL))
        db.commit()
    evening = datetime(2026, 10, 24, 18, 30, tzinfo=UTC)
    with app.state.sessionmaker() as db:
        assert tempo.alerts(db, today=TODAY, local=evening, public_url="https://bank") == [], "contrat Base"
    client.put("/api/energy/contract", json={"option": "tempo", "kva": 9})
    with app.state.sessionmaker() as db:
        assert tempo.alerts(db, today=TODAY, local=evening.replace(hour=15), public_url="https://bank") == []
        ((key, text, urgent),) = tempo.alerts(db, today=TODAY, local=evening, public_url="https://bank")
        assert key == "tempo_red:2026-10-25" and urgent is False, "les heures calmes la retiennent"
        assert "dimanche 25/10" in text and "0,7295 €" in text and "0,1615 €" in text and "0,1654 €" in text
        assert "grille de la CRE du 01/08/2026" in text and "RTE" in text
        assert_factual(text)
        notices = channel_alerts.collect(db, today=TODAY, local=evening, privacy="full", public_url="https://bank")
        assert any(n.key == "tempo_red:2026-10-25" for n in notices)
    client.put("/api/consents/tempo", json={"granted": False})
    with app.state.sessionmaker() as db:
        assert tempo.alerts(db, today=TODAY, local=evening, public_url="https://bank") == []
