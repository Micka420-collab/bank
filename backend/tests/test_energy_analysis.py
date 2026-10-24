"""Analyse de l'énergie (#33, suite) : degrés-jours d'Open-Meteo (vraie réponse de l'archive) et consommation à
climat égal, courbe de charge Conso API (vraie réponse enregistrée par l'auteur de Conso API), simulation Base /
Heures creuses / Tempo sur les vraies grilles de la CRE et les vraies couleurs (api-couleur-tempo.fr), prix repère du
gaz ; rien ne part sans accord, aucun chiffre sans source, aucun ton de conseil."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.domain import energy_analysis as ana
from app.domain import energy_signals as sig
from app.domain import rates
from app.domain.correlations import climate_findings
from app.domain.energy import DailyReading
from app.importers.base import ParsedTransaction
from app.models import Account, EnergyLoadCurve, TempoDay, WeatherDay
from app.providers.conso_api import ConsoApiClient, ConsoApiError
from app.services import tempo, territory
from app.services.ingest import ingest
from tests.conftest import PASSWORD
from tests.test_energy_signals import PRM, TODAY, TOKEN, FakeConso, assert_factual, cre_latest, plain, world
from tests.test_territory import Chunks

__all__ = ["world"]  # la fixture « world » (Linky simulé, grilles de la CRE relevées) vient des tests de l'étape 1

FIX = Path(__file__).parent / "fixtures" / "energie"


def grids() -> dict[str, dict[int, sig.GridPower]]:
    latest = cre_latest()
    return {o: sig.grid_from_observations(o, latest, "CRE") for o in sig.OPTIONS}


def year_days(n: int = 365) -> list[date]:
    return [TODAY - timedelta(days=i) for i in range(n, 0, -1)]


# ------------------------------------------------------------------ lecture des sources
def test_open_meteo_reads_the_real_archive_response() -> None:
    temps = ana.parse_meteo((FIX / "openmeteo_archive.json").read_bytes())
    assert temps == {
        date(2025, 1, 1): 6.1,
        date(2025, 1, 2): 4.4,
        date(2025, 1, 3): 0.4,
        date(2025, 1, 4): -0.7,
        date(2025, 1, 5): 9.4,
    }
    assert ana.hdd(-0.7) == pytest.approx(18.7) and ana.hdd(19.0) == 0.0
    raw = json.loads((FIX / "openmeteo_archive.json").read_bytes())
    raw["daily"]["temperature_2m_mean"][1] = None  # l'archive n'a pas encore le jour : ignoré, jamais deviné
    assert date(2025, 1, 2) not in ana.parse_meteo(json.dumps(raw).encode())
    # La vraie erreur d'Open-Meteo (variable inconnue), relevée le 24/10/2026.
    error = b'{"reason":"Invalid value: Cannot initialize ForecastVariableDaily","error":true}'
    for bad in (error, b"<html>", b'{"daily": {"time": ["2025-01-01"]}}'):
        with pytest.raises(sig.SignalFormatError):
            ana.parse_meteo(bad)
    # Seules les coordonnées arrondies au dixième de degré partent, jamais l'adresse.
    cell = ana.meteo_cell(48.85661, 2.35222)
    url = ana.meteo_url(cell, date(2024, 10, 1), date(2026, 10, 23))
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://archive-api.open-meteo.com/v1/archive?")
    assert q["latitude"] == ["48.9"] and q["longitude"] == ["2.4"] and q["daily"] == ["temperature_2m_mean"]


def test_load_curve_reads_the_real_v2_response() -> None:
    days = ana.parse_load_curve(json.loads((FIX / "courbe_de_charge.json").read_bytes()))
    (slots,) = days.values()
    assert list(days) == [date(2026, 4, 1)] and ana.complete(slots)
    # 110 W pendant la demi-heure 00:00-00:30 = 55 Wh ; le point de « 2026-04-02 00:00 » est la dernière du 1er.
    assert slots[0] == 55 and slots[1] == 204 and slots[47] == 50
    for bad in (
        {"grandeur": [{"grandeurPhysique": "PA", "unite": "kW", "points": []}]},
        {
            "grandeur": [
                {
                    "grandeurPhysique": "PA",
                    "unite": "W",
                    "points": [{"v": "1", "d": "2026-04-01 00:10:00", "p": "PT10M"}],
                }
            ]
        },
        {"autre": 1},
    ):
        with pytest.raises(sig.SignalFormatError):
            ana.parse_load_curve(bad)


def test_conso_client_reads_the_load_curve_by_weeks() -> None:
    seen: list[httpx.Request] = []
    sample = (FIX / "courbe_de_charge.json").read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=sample)

    client = ConsoApiClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))
    days = client.load_curve(PRM, date(2026, 3, 30), date(2026, 4, 13))
    assert list(days) == [date(2026, 4, 1)] and len(seen) == 2
    assert seen[0].url.path == "/api/courbe_de_charge_consommation"
    assert dict(seen[0].url.params) == {"pointId": PRM, "dateDebut": "2026-03-30", "dateFin": "2026-04-06"}


def test_off_peak_hours_are_read_from_the_bill() -> None:
    assert ana.hc_slots(ana.parse_hc_ranges("22:00-06:00")) == frozenset([*range(44, 48), *range(12)])
    split = ana.hc_slots(ana.parse_hc_ranges("01:30-07:30, 12h30-14h30"))
    assert len(split) == 16 and 3 in split and 25 in split and 29 not in split
    for bad in ("23:15-07:15", "n'importe quoi", "00:00-23:30", ""):
        with pytest.raises(ValueError):
            ana.parse_hc_ranges(bad)


def test_tempo_season_history_reads_the_real_response() -> None:
    colours = sig.parse_tempo((FIX / "tempo_saison.json").read_bytes())
    assert colours[date(2025, 12, 29)] == "rouge" and colours[date(2026, 1, 14)] == "blanc"
    assert tempo.season_of(date(2025, 10, 24)) == "2025-2026" and tempo.season_of(date(2026, 8, 31)) == "2025-2026"
    assert tempo.history_url("2026-2027") == "https://www.api-couleur-tempo.fr/api/joursTempo?periode=2026-2027"


# ------------------------------------------------------------------ climat
def two_years(*, colder_now: float, extra_wh_recent: int = 0) -> tuple[list[DailyReading], dict[date, float]]:
    """Deux ans de relevés : 5 kWh par jour plus 400 Wh par degré-jour ; les trois derniers mois plus froids que
    l'an dernier de `colder_now` °C, et éventuellement un appareil de plus (`extra_wh_recent`)."""
    readings, temps = [], {}
    recent = date(2026, 7, 1)
    for i in range(1, 800):
        d = TODAY - timedelta(days=i)
        t = 12.0 + 6 * ((d.timetuple().tm_yday % 60) / 60) - (colder_now if d >= recent else 0.0)
        temps[d] = t
        readings.append(DailyReading(d, round(5_000 + 400 * ana.hdd(t) + (extra_wh_recent if d >= recent else 0))))
    return sorted(readings, key=lambda r: r.day), temps


def test_climate_compares_at_equal_climate_and_flags_only_a_real_drift() -> None:
    readings, temps = two_years(colder_now=2.0)
    check = ana.climate_check(readings, temps, today=TODAY)
    assert check.status == "steady" and check.period == "de juillet à septembre 2026"
    assert check.raw_pct is not None and check.raw_pct > 5 and check.cold_pct is not None and check.cold_pct > 10
    assert check.corrected_pct == 0 and check.wh_per_hdd == 400 and check.r2 is not None and check.r2 > 0.9
    text = plain(check.message)
    assert f"+{check.raw_pct} % sur un an" in text and f"mais il a fait {check.cold_pct} % plus froid" in text
    assert "à climat égal, +0 %" in text and "degrés-jours" in text
    assert not climate_findings(check)
    assert_factual(check.message)
    drift = ana.climate_check(*two_years(colder_now=2.0, extra_wh_recent=2_000), today=TODAY)
    assert drift.status == "drift" and drift.corrected_pct is not None and drift.corrected_pct >= ana.DRIFT_PCT
    assert drift.extra_kwh is not None and drift.extra_kwh > 100 and "chauffe-eau" in drift.message
    (finding,) = climate_findings(drift)
    assert finding.kind == "energy_climate_drift" and finding.topic == "energie" and finding.level == "warning"
    assert any(e.ref == "url:https://open-meteo.com/" for e in finding.evidence)
    assert_factual(finding.title + finding.body)


def test_climate_says_what_is_missing() -> None:
    readings, temps = two_years(colder_now=0.0)
    assert ana.climate_check(readings, {}, today=TODAY).status == "no_weather"
    few = ana.climate_check(readings[-60:], temps, today=TODAY)
    assert few.status == "too_few" and "il en faut 90" in few.message
    no_last_year = [r for r in readings if r.day >= TODAY - timedelta(days=300)]
    short = ana.climate_check(no_last_year, temps, today=TODAY)
    assert short.status == "too_few" and "l'an dernier" in short.message
    # Une consommation qui ne suit pas le froid n'est pas corrigée : la comparaison brute vaut à climat égal.
    flat = [DailyReading(r.day, 8_000) for r in readings]
    check = ana.climate_check(flat, temps, today=TODAY)
    assert check.status == "steady" and check.wh_per_hdd == 0 and "à climat égal, l'écart reste" in check.message


# ------------------------------------------------------------------ simulation
def test_simulation_estimates_from_daily_readings_with_its_assumptions() -> None:
    readings = [DailyReading(d, 10_000) for d in year_days()]
    reds = set(year_days()[100:122])  # 22 jours rouges
    colours = {d: ("rouge" if d in reds else "bleu") for d in [*year_days(366)]}
    g = grids()
    sim = ana.simulate(readings, {}, colours, today=TODAY, kva=9, grids=g, hc_ranges=None, tempo_colors_known=True)
    assert sim.status == "ok" and sim.mode == "estimation" and sim.kwh == 3_650 and sim.red_days == 22
    by = {o.option: o for o in sim.options}
    # Base 9 kVA au 01/08/2026 : 239,84 € + 3 650 kWh × 0,1985 € = 964,365 € (arrondi au centime pair).
    assert by["base"].total_cents == 96_436 and by["base"].subscription_cents == 23_984
    b9 = g["hphc"][9]
    expected = b9.fixe + 3650 * (Decimal("0.35") * b9.kwh["hc_ttc"] + Decimal("0.65") * b9.kwh["hp_ttc"])
    assert by["hphc"].total_cents == int((expected * 100).quantize(Decimal(1)))
    t9 = g["tempo"][9]
    blue = Decimal("0.35") * t9.kwh["bleu_hc_ttc"] + Decimal("0.65") * t9.kwh["bleu_hp_ttc"]
    red = Decimal("0.35") * t9.kwh["rouge_hc_ttc"] + Decimal("0.65") * t9.kwh["rouge_hp_ttc"]
    assert by["tempo"].total_cents == int(((t9.fixe + 10 * (343 * blue + 22 * red)) * 100).quantize(Decimal(1)))
    assert any("35 %" in a for a in sim.assumptions) and any("minuit à minuit" in a for a in sim.assumptions)
    assert any("habitudes inchangées" in a for a in sim.assumptions)
    assert sim.hc_breakeven_pct is not None and 0 <= sim.hc_breakeven_pct <= 100
    text = plain(sim.message)
    assert "grille de la CRE du 01/08/2026 en 9 kVA" in text and "(estimation)" in text and "22 jours rouges" in text
    assert "964 €" in text
    assert_factual(sim.message + " ".join(sim.assumptions))
    # Sans les couleurs de l'année, Tempo n'est pas chiffrée : Bank le dit.
    blind = ana.simulate(readings, {}, {}, today=TODAY, kva=9, grids=g, hc_ranges=None, tempo_colors_known=False)
    t = next(o for o in blind.options if o.option == "tempo")
    assert t.total_cents is None and "Couleur Tempo" in (t.missing or "") and blind.red_days is None


def test_simulation_is_exact_with_a_complete_load_curve() -> None:
    days = year_days(366)
    readings = [DailyReading(d, 4_800) for d in days[1:]]
    curve: dict[date, list[int | None]] = {d: [100] * 48 for d in days}  # 4,8 kWh par jour, à plat
    colours = dict.fromkeys(days, "blanc")
    g = grids()
    sim = ana.simulate(
        readings,
        curve,
        colours,
        today=TODAY,
        kva=6,
        grids=g,
        hc_ranges=ana.parse_hc_ranges("22:00-06:00"),
        tempo_colors_known=True,
    )
    assert sim.mode == "exacte" and sim.exact_days == 365 and sim.hc_breakeven_pct is None
    assert not any("35 %" in a for a in sim.assumptions), "aucune hypothèse de répartition avec la courbe"
    h6 = g["hphc"][6]
    third = Decimal(16) / 48
    expected = h6.fixe + Decimal("4.8") * 365 * (third * h6.kwh["hc_ttc"] + (1 - third) * h6.kwh["hp_ttc"])
    by = {o.option: o for o in sim.options}
    assert by["hphc"].total_cents == int((expected * 100).quantize(Decimal(1)))
    # Courbe incomplète un jour sur deux : en partie exacte, l'hypothèse revient pour les autres jours.
    holes = {d: (s if i % 2 else [*s[:47], None]) for i, (d, s) in enumerate(curve.items())}
    part = ana.simulate(
        readings, holes, colours, today=TODAY, kva=6, grids=g,
        hc_ranges=ana.parse_hc_ranges("22:00-06:00"), tempo_colors_known=True,
    )  # fmt: skip
    assert part.mode == "en partie exacte" and any("35 %" in a for a in part.assumptions)


def test_simulation_says_what_is_missing() -> None:
    g = grids()
    readings = [DailyReading(d, 10_000) for d in year_days(200)]
    kw = {"today": TODAY, "grids": g, "hc_ranges": None, "tempo_colors_known": True}
    assert ana.simulate(readings, {}, {}, kva=None, **kw).status == "no_contract"  # type: ignore[arg-type]
    few = ana.simulate(readings, {}, {}, kva=9, **kw)  # type: ignore[arg-type]
    assert few.status == "too_few" and "200 jours" in few.message
    full = [DailyReading(d, 10_000) for d in year_days()]
    none = ana.simulate(full, {}, {}, kva=9, **{**kw, "grids": {}})  # type: ignore[arg-type]
    assert none.status == "no_grid" and "Barèmes tenus à jour" in none.message
    three = ana.simulate(full, {}, {}, kva=3, **kw)  # type: ignore[arg-type]
    assert next(o for o in three.options if o.option == "tempo").missing == (
        "grille de la CRE de l'option Tempo en 3 kVA non relevée"
    )


# ------------------------------------------------------------------ gaz
def test_gas_reference_is_dated_and_sourced() -> None:
    raw = rates.current()
    oct_ = ana.gas_reference(raw, date(2026, 10, 24))
    assert oct_ is not None and oct_.eur_per_mwh == Decimal("182.88") and oct_.since == date(2026, 10, 1)
    assert oct_.next_price == Decimal("194.90") and oct_.next_since == date(2026, 11, 1)
    assert oct_.source_url == "https://www.service-public.gouv.fr/particuliers/actualites/A17956"
    nov = ana.gas_reference(raw, date(2026, 11, 2))
    assert nov is not None and nov.eur_per_mwh == Decimal("194.90") and nov.next_price is None
    assert ana.gas_reference(raw, date(2026, 9, 30)) is None, "aucun prix repère connu avant octobre 2026"


# ------------------------------------------------------------------ service et API
class CurveConso(FakeConso):
    curve_fail: ClassVar[ConsoApiError | None] = None
    curve_calls: ClassVar[list[tuple[date, date]]] = []

    def load_curve(self, prm: str, start: date, end_exclusive: date) -> dict[date, list[int | None]]:
        CurveConso.curve_calls.append((start, end_exclusive))
        if CurveConso.curve_fail is not None:
            raise CurveConso.curve_fail
        first = TODAY - timedelta(days=30)  # enregistrement activé il y a 30 jours
        days = [start + timedelta(days=i) for i in range((end_exclusive - start).days)]
        return {d: [200] * 48 for d in days if d >= first}


class MeteoNet:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["host"] == "archive-api.open-meteo.com"
        q = parse_qs(request.url.query.decode())
        start, end = date.fromisoformat(q["start_date"][0]), date.fromisoformat(q["end_date"][0])
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        body = json.loads((FIX / "openmeteo_archive.json").read_bytes())  # la vraie forme, étendue aux jours demandés
        body["daily"] = {"time": [d.isoformat() for d in days], "temperature_2m_mean": [8.0 for _ in days]}
        return httpx.Response(200, stream=Chunks(json.dumps(body).encode()))


@pytest.fixture
def home(app, world: TestClient) -> tuple[TestClient, MeteoNet]:  # type: ignore[no-untyped-def]
    app.state.energy_client_factory = CurveConso
    CurveConso.curve_fail, CurveConso.curve_calls = None, []
    fake = MeteoNet()
    app.state.weather_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.weather_resolver = lambda host: ["93.184.216.34"]
    assert world.post("/api/energy/linky", json={"prm": PRM, "token": TOKEN}).status_code == 201
    return world, fake


def test_simulation_on_the_energy_card_and_the_load_curve_consent(app, home: tuple[TestClient, MeteoNet]) -> None:  # type: ignore[no-untyped-def]
    client, _ = home
    st = client.get("/api/energy").json()
    assert st["simulation"]["status"] == "no_contract" and CurveConso.curve_calls == [], "pas de courbe sans accord"
    st = client.put("/api/energy/contract", json={"option": "tempo", "kva": 9, "hc": "22h00-06h00"}).json()
    assert st["contract"] == {"option": "tempo", "kva": 9, "hc": "22h00-06h00"}
    assert client.put("/api/energy/contract", json={"option": "hphc", "kva": 9, "hc": "22:15-6:15"}).status_code == 422
    sim = st["simulation"]
    assert sim["status"] == "ok" and sim["mode"] == "estimation" and sim["current_option"] == "tempo"
    assert next(o for o in sim["options"] if o["option"] == "tempo")["total_cents"] is None
    assert st["curve"]["consent"] is False
    client.put("/api/consents/linky_curve", json={"granted": True})
    st = client.post("/api/energy/sync").json()
    assert st["curve"]["days"] == 7 and st["curve"]["last_error"] is None, "d'abord la dernière semaine"
    assert CurveConso.curve_calls[0] == (TODAY - timedelta(days=7), TODAY)
    with app.state.sessionmaker() as db:
        assert db.scalar(select(EnergyLoadCurve).limit(1)) is not None
    # Puis l'historique à reculons ; le rattrapage s'arrête quand Enedis n'a plus rien avant le premier jour gardé.
    assert client.post("/api/energy/sync").json()["curve"]["days"] == 30
    client.post("/api/energy/sync")
    calls = len(CurveConso.curve_calls)
    client.post("/api/energy/sync")
    assert all(start >= TODAY - timedelta(days=2) for start, _ in CurveConso.curve_calls[calls:]), "plus d'historique"
    data = client.post("/api/export", json={"password": PASSWORD}).json()
    assert len(data["energy_load_curve"]) == 30 and data["energy_load_curve"][0]["wh_per_half_hour"][0] == 200
    # Accord retiré : la courbe gardée ne sert plus, aussitôt.
    client.put("/api/consents/linky_curve", json={"granted": False})
    assert client.get("/api/energy").json()["curve"]["days"] == 0


def test_load_curve_failure_is_reported_apart(app, home: tuple[TestClient, MeteoNet]) -> None:  # type: ignore[no-untyped-def]
    client, _ = home
    client.put("/api/consents/linky_curve", json={"granted": True})
    CurveConso.curve_fail = ConsoApiError("Requête refusée (400)", 400, permanent=True)
    st = client.post("/api/energy/sync").json()
    assert st["connection"]["status"] == "active" and "espace client Enedis" in st["curve"]["last_error"]
    calls = len(CurveConso.curve_calls)
    client.post("/api/energy/sync")
    assert len(CurveConso.curve_calls) == calls, "pas de courbe chez Enedis : nouvel essai dans une semaine"


def test_tempo_history_feeds_the_tempo_simulation(app, home: tuple[TestClient, MeteoNet]) -> None:  # type: ignore[no-untyped-def]
    client, _ = home
    season = json.loads((FIX / "tempo_saison.json").read_bytes())
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["host"] == "www.api-couleur-tempo.fr"
        seen.append(req.url.query.decode())
        if "periode=" in seen[-1]:  # la vraie forme d'une saison, étendue à tous ses jours (rouge les 22 premiers)
            first = int(seen[-1].split("periode=")[1][:4])
            days = [date(first, 9, 1) + timedelta(days=i) for i in range(365)]
            rows = [season[0] | {"dateJour": d.isoformat(), "codeJour": 3 if i < 22 else 1} for i, d in enumerate(days)]
            rows = [r for r in rows if r["dateJour"] <= (TODAY + timedelta(days=1)).isoformat()]
            return httpx.Response(200, stream=Chunks(json.dumps(rows).encode()))
        return httpx.Response(200, stream=Chunks((FIX / "tempo_jours_inconnu.json").read_bytes()))

    app.state.tempo_http = httpx.Client(transport=httpx.MockTransport(handler))
    app.state.tempo_resolver = lambda host: ["93.184.216.34"]
    client.put("/api/energy/contract", json={"option": "base", "kva": 9})
    client.put("/api/consents/tempo", json={"granted": True})
    st = client.post("/api/energy/tempo/refresh").json()
    assert sum("periode=2025-2026" in q or "periode=2026-2027" in q for q in seen) == 2
    assert st["tempo"]["history_missing_days"] == 0
    sim = st["simulation"]
    tempo_cost = next(o for o in sim["options"] if o["option"] == "tempo")
    assert tempo_cost["total_cents"] is not None and sim["red_days"] == 22
    with app.state.sessionmaker() as db:
        assert db.scalar(select(TempoDay).where(TempoDay.day == TODAY - timedelta(days=300))) is not None


def test_weather_needs_consent_and_place_then_is_forgotten_with_it(app, home: tuple[TestClient, MeteoNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = home
    st = client.get("/api/energy").json()
    assert st["weather"]["consent"] is False and st["climate"]["status"] == "no_weather"
    r = client.post("/api/energy/weather/refresh")
    assert r.status_code == 409 and "Météo de ta commune" in r.json()["detail"] and fake.requests == []
    client.put("/api/consents/meteo", json={"granted": True})
    r = client.post("/api/energy/weather/refresh")
    assert r.status_code == 409 and "Ton adresse" in r.json()["detail"]
    with app.state.sessionmaker() as db:
        territory._save_place(
            db, {"label": "8 Rue X 75004 Paris", "citycode": "75104", "lat": 48.85661, "lon": 2.35222}
        )
        db.commit()
    st = client.post("/api/energy/weather/refresh").json()
    assert st["weather_error"] is None and st["weather"]["last_success_at"]
    (req,) = fake.requests
    q = parse_qs(req.url.query.decode())
    assert q["latitude"] == ["48.9"] and q["longitude"] == ["2.4"] and "Rue" not in str(req.url)
    assert q["start_date"] == ["2024-10-01"] and q["end_date"] == [(TODAY - timedelta(days=1)).isoformat()]
    assert st["climate"]["status"] != "no_weather"
    data = client.post("/api/export", json={"password": PASSWORD}).json()
    assert len(data["weather_days"]) > 700
    # Une nouvelle relève ne relit que les derniers jours.
    client.post("/api/energy/weather/refresh")
    assert parse_qs(fake.requests[-1].url.query.decode())["start_date"] == [(TODAY - timedelta(days=6)).isoformat()]
    assert client.delete("/api/territory/place").status_code in (200, 204)
    with app.state.sessionmaker() as db:
        assert list(db.scalars(select(WeatherDay))) == [], "les températures disent où tu habites"


def test_gas_line_only_when_gas_spending_is_seen(app, home: tuple[TestClient, MeteoNet]) -> None:  # type: ignore[no-untyped-def]
    client, _ = home
    assert client.get("/api/energy").json()["gas"] is None
    with app.state.sessionmaker() as db:
        acc = Account(name="Compte courant")
        db.add(acc)
        db.flush()
        ingest(
            db,
            acc,
            [
                ParsedTransaction(TODAY - timedelta(days=30 * i + 3), -9_000, "PRLV SEPA GAZ DE BORDEAUX")
                for i in range(4)
            ],
            provider="file",
        )
        db.commit()
    gas = client.get("/api/energy").json()["gas"]
    assert gas is not None and gas["spent_cents"] == 36_000 and "182,88 €/MWh" in plain(gas["message"])
    assert "194,90 €/MWh" in plain(gas["message"]) and "facture de gaz" in gas["message"]
    assert gas["source_url"].endswith("A17956")
    assert_factual(gas["message"])
    st = client.get("/api/energy").json()
    assert st["insights"]["monthly_payment_cents"] is None, (
        "un prélèvement de gaz n'est pas une mensualité d'électricité"
    )
