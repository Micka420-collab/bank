"""Ton territoire (#32) : géocodage de l'adresse (Géoplateforme), jours fériés (calendrier.api.gouv.fr), académie et
vacances scolaires (data.education.gouv.fr), lus sur les vraies réponses du 24 octobre 2026 (réduites) ; jour ouvré
suivant ; prélèvements décalés dans la prévision du mois et l'année qui vient ; seule l'adresse tapée part, avec
ton accord ; une relève ratée garde la précédente ; la commune pré-remplit le logement (DVF)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.api import deps
from app.domain import territory as dom
from app.domain.forecast import CategoryInfo, build_forecast
from app.domain.recurring import RecurringSeries
from app.domain.yearahead import build_year_ahead
from app.models import AuditLog, HouseholdPlace
from app.services import territory as svc

FIXTURES = Path(__file__).parent / "fixtures" / "calendrier"
TODAY = date(2026, 10, 24)
QUERY = "Place de l'Hôtel de Ville 80000 Amiens"


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def holidays() -> dict[date, str]:
    return {**dom.parse_holidays(read("jours_feries_metropole_2026.json"), 2026), **dom.parse_holidays(read("jours_feries_metropole_2027.json"), 2027)}  # fmt: skip


# ------------------------------------------------------------------ lecture des sources
def test_parsers_read_the_real_responses() -> None:
    place = dom.parse_geocode(read("geocodage_adresse.json"))
    assert place is not None
    assert (place.citycode, place.postcode, place.city, place.depcode) == ("80021", "80000", "Amiens", "80")
    assert (place.lat, place.lon, place.precision) == (49.893238, 2.295394, "street") and place.score > 0.9
    assert dom.parse_geocode(read("geocodage_vide.json")) is None
    days = dom.parse_holidays(read("jours_feries_metropole_2027.json"), 2027)
    assert len(days) == 11 and days[date(2027, 11, 11)] == "11 novembre" and date(2027, 3, 26) not in days
    alsace = dom.parse_holidays(read("jours_feries_alsace-moselle_2027.json"), 2027)
    assert alsace[date(2027, 3, 26)] == "Vendredi saint" and alsace[date(2027, 12, 26)] == "2ème jour de Noël"
    assert dom.parse_academy(read("annuaire_academie_080.json")) == "Amiens"
    zone, breaks = dom.parse_school(read("calendrier_scolaire_amiens.json"), "Amiens")
    assert zone == "Zone B"
    first = breaks[0]
    # « 2026-10-16T22:00:00+00:00 » → le samedi 17 à Paris ; la fin du jeu est le jour de la reprise.
    assert (first.label, first.start, first.end) == ("Vacances de la Toussaint", date(2026, 10, 17), date(2026, 11, 1))
    noel = next(b for b in breaks if b.label == "Vacances de Noël")
    assert (noel.start, noel.end) == (date(2026, 12, 19), date(2027, 1, 3))
    summer = [b for b in breaks if b.label == "Vacances d'Été"]
    assert len(summer) == 1 and summer[0].end == date(2027, 9, 1), "les élèves, pas les enseignants"


def test_zones_and_urls_send_only_what_they_must() -> None:
    assert dom.holiday_zone("80") == "metropole" and dom.holiday_zone("67") == "alsace-moselle"
    assert dom.holiday_zone("974") == "la-reunion" and dom.holiday_zone(None) == "metropole"
    assert dom.holidays_url("metropole", 2027) == "https://calendrier.api.gouv.fr/jours-feries/metropole/2027.json"
    assert 'code_departement="080"' in parse_qs(dom.academy_url("80").split("?", 1)[1])["where"][0]
    assert parse_qs(dom.geocode_url("  8  rue  X  ").split("?", 1)[1]) == {"q": ["8 rue X"], "limit": ["1"]}
    with pytest.raises(dom.TerritoryError):
        dom.geocode_url("ab")
    with pytest.raises(dom.TerritoryError):
        dom.academy_url('80" or 1=1')


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: dom.parse_geocode(b"<html>"), "JSON attendu"),
        (
            lambda: dom.parse_geocode(
                b'{"features": [{"properties": {"citycode": "x"}, "geometry": {"coordinates": [1, 2]}}]}'
            ),
            "code",
        ),
        (
            lambda: dom.parse_geocode(
                b'{"features": [{"properties": {"citycode": "80021", "depcode": "80"}, "geometry": {"coordinates": [999, 2]}}]}'
            ),
            "bornes",
        ),
        (lambda: dom.parse_holidays(read("jours_feries_metropole_2027.json"), 2026), "hors de l'année"),
        (lambda: dom.parse_holidays(b'{"2027-01-01": "1er janvier"}', 2027), "inattendue"),
        (
            lambda: dom.parse_school(b'{"results": [{"location": "Amiens", "start_date": "demain"}]}', "Amiens"),
            "illisible",
        ),
        (lambda: dom.parse_academy(b'{"results": [{"libelle_academie": "<script>"}]}'), "illisible"),
    ],
)
def test_parsers_are_defensive(call, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(dom.TerritoryError, match=message):
        call()


def test_next_business_day() -> None:
    days = holidays()
    assert dom.next_business_day(date(2027, 11, 11), days) == date(2027, 11, 12)  # jeudi férié → vendredi
    assert dom.next_business_day(date(2027, 5, 8), days) == date(2027, 5, 10)  # samedi 8 mai → lundi
    assert dom.next_business_day(date(2026, 10, 31), days) == date(2026, 11, 2)  # samedi, puis Toussaint (dimanche)
    assert dom.next_business_day(date(2026, 10, 27), days) == date(2026, 10, 27)


# ------------------------------------------------------------------ prévision et année qui vient
def _series(key: str, day: date, amount: int, cadence: str = "monthly", direction: str = "out") -> RecurringSeries:
    return RecurringSeries(
        key=key,
        merchant=key.upper(),
        direction=direction,
        cadence=cadence,
        period_days=30.4 if cadence == "monthly" else 91.3,
        amount_cents=amount,
        amount_min_cents=amount,
        amount_max_cents=amount,
        variable=False,
        occurrences=6,
        last_date=day - timedelta(days=30),
        next_date=day,
        day_of_month=day.day,
        confidence=0.9,
        category="loyer",
        late=False,
    )


CATS = {"loyer": CategoryInfo("loyer", "Loyer", "fixed")}


def test_month_forecast_moves_debits_to_the_next_business_day() -> None:
    series = [_series("credit", date(2027, 11, 11), -50_000), _series("box", date(2027, 11, 27), -3_000)]
    series.append(_series("salaire", date(2027, 11, 28), 200_000, direction="in"))
    today = date(2027, 11, 2)
    plain = build_forecast(today=today, txns=[], current_balance_cents=0, series=series, categories=CATS)
    assert [e.date for e in plain.upcoming] == [date(2027, 11, 11), date(2027, 11, 27), date(2027, 11, 28)]
    assert plain.holidays == []
    moved = build_forecast(
        today=today, txns=[], current_balance_cents=0, series=series, categories=CATS, holidays=holidays()
    )
    by_label = {e.label: e for e in moved.upcoming}
    assert by_label["CREDIT"].date == date(2027, 11, 12) and by_label["CREDIT"].shifted_from == date(2027, 11, 11)
    assert by_label["BOX"].date == date(2027, 11, 29), "samedi → lundi"
    assert by_label["SALAIRE"].date == date(2027, 11, 28) and by_label["SALAIRE"].shifted_from is None
    assert [(h.date, h.name) for h in moved.holidays] == [(date(2027, 11, 1), "Toussaint"), (date(2027, 11, 11), "11 novembre")]  # fmt: skip
    # Un prélèvement du samedi 30 octobre part le mardi 2 novembre (Toussaint le lundi) : plus dans ce mois.
    late_oct = [_series("assurance", date(2027, 10, 30), -4_000)]
    oct_ = build_forecast(
        today=date(2027, 10, 20),
        txns=[],
        current_balance_cents=0,
        series=late_oct,
        categories=CATS,
        holidays=holidays(),
    )
    assert oct_.upcoming == [] and oct_.expected_fixed_cents == 0


def test_year_ahead_shows_holidays_school_breaks_and_shifted_spikes() -> None:
    _, breaks = dom.parse_school(read("calendrier_scolaire_amiens.json"), "Amiens")
    spike = _series("taxe", date(2026, 11, 11), -90_000, cadence="yearly")
    y = build_year_ahead(
        today=TODAY,
        start_balance_cents=100_000,
        series=[spike],
        categories=CATS,
        txns=[],
        holidays=holidays(),
        school_breaks=breaks,
    )
    (s,) = y.spikes
    assert s.day == date(2026, 11, 12) and s.shifted_from == date(2026, 11, 11)
    nov, dec = y.points[0], y.points[1]
    assert nov.holidays == ("01/11 : Toussaint", "11/11 : 11 novembre")
    assert dec.school == ("Vacances de Noël du 19/12 au 03/01",) and dec.holidays == ("25/12 : Jour de Noël",)
    assert y.points[5].school == ("Vacances de Printemps du 17/04 au 02/05",), "une fois, au mois où elles commencent"
    assert y.points[6].school == ("Pont de l'Ascension le 07/05",)
    plain = build_year_ahead(today=TODAY, start_balance_cents=100_000, series=[spike], categories=CATS, txns=[])
    assert plain.spikes[0].day == date(2026, 11, 11) and plain.points[0].holidays == ()


# ------------------------------------------------------------------ bout en bout
class Chunks(httpx.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self) -> Iterator[bytes]:
        yield self.data


class FakeNet:
    """Géoplateforme, calendrier.api.gouv.fr et data.education.gouv.fr simulés avec leurs vraies réponses. L'hôte
    se lit dans l'en-tête Host : netfetch épingle la connexion sur l'adresse résolue."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        if host in self.down:
            return httpx.Response(503, stream=Chunks(b""))
        path = request.url.path
        if host == "data.geopf.fr":
            body = read("geocodage_adresse.json")
        elif host == "calendrier.api.gouv.fr":
            zone, year = path.split("/")[-2], path.rsplit("/", 1)[-1].removesuffix(".json")
            body = read(f"jours_feries_{zone}_{year}.json")
        else:
            assert host == "data.education.gouv.fr", host
            body = read("annuaire_academie_080.json" if "annuaire" in path else "calendrier_scolaire_amiens.json")
        return httpx.Response(200, stream=Chunks(body), headers={"content-type": "application/json"})

    def hosts(self) -> set[str]:
        return {r.headers["host"] for r in self.requests}


@pytest.fixture
def net(app, auth_client: TestClient) -> Iterator[tuple[TestClient, FakeNet]]:  # type: ignore[no-untyped-def]
    fake = FakeNet()
    app.state.territory_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.territory_resolver = lambda host: ["93.184.216.34"]
    app.dependency_overrides[deps.today] = lambda: TODAY
    yield auth_client, fake
    app.dependency_overrides.clear()


def test_nothing_leaves_without_consent(net: tuple[TestClient, FakeNet]) -> None:
    client, fake = net
    view = client.get("/api/territory").json()
    assert view["consents"] == {"geocoding": False, "calendar": False} and view["place"] is None
    assert view["holidays"] == [] and view["school"] is None
    r = client.post("/api/territory/geocode", json={"query": QUERY})
    assert r.status_code == 409 and "Ton adresse située sur la carte" in r.json()["detail"]
    r = client.post("/api/territory/calendar/refresh")
    assert r.status_code == 409 and "Jours fériés et vacances scolaires" in r.json()["detail"]
    assert fake.requests == []


def test_geocoding_sends_only_the_typed_address_and_keeps_it_encrypted(app, net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    assert client.put("/api/consents/geocoding", json={"granted": True}).status_code == 200
    r = client.post("/api/territory/geocode", json={"query": QUERY})
    assert r.status_code == 200, r.text
    place = r.json()["place"]
    assert (place["citycode"], place["city"], place["depcode"], place["postcode"]) == ("80021", "Amiens", "80", "80000")
    assert place["lat"] == 49.893238 and place["source"].startswith("Géoplateforme") and place["geocoded_at"]
    (req,) = fake.requests
    assert req.headers["host"] == "data.geopf.fr" and parse_qs(req.url.query.decode()) == {"q": [QUERY], "limit": ["1"]}
    with app.state.sessionmaker() as db:
        raw = db.execute(text("SELECT data FROM household_place")).scalar_one()
        assert "Amiens" not in raw and "80021" not in raw, "chiffré au repos"
        actions = {a.action: a for a in db.scalars(select(AuditLog).where(AuditLog.action.like("place.%")))}
        assert "place.geocode" in actions and "Amiens" not in actions["place.geocode"].details
    # Le logement (DVF) pas encore décrit : sa commune est pré-remplie.
    assert client.get("/api/home").json()["suggested"] == {"code": "80021", "commune": "Amiens"}
    # Une adresse inconnue ne change rien ; un géocodeur en panne non plus.
    fake.down.add("data.geopf.fr")
    r = client.post("/api/territory/geocode", json={"query": "1 rue de Nulle Part"})
    assert r.status_code == 502 and "rien n'a changé" in r.json()["detail"]
    assert client.get("/api/territory").json()["place"]["citycode"] == "80021"
    assert client.delete("/api/territory/place").json()["place"] is None
    with app.state.sessionmaker() as db:
        assert db.get(HouseholdPlace, 1) is None


def test_calendar_feeds_the_forecast_and_a_failure_keeps_the_previous(app, net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    for purpose in ("geocoding", "calendar"):
        assert client.put(f"/api/consents/{purpose}", json={"granted": True}).status_code == 200
    # Sans adresse : seulement les jours fériés de métropole, aucune académie demandée.
    r = client.post("/api/territory/calendar/refresh")
    assert r.status_code == 200, r.text
    assert fake.hosts() == {"calendrier.api.gouv.fr"} and r.json()["view"]["school"] is None
    view = r.json()["view"]
    assert view["holidays"][0] == {"day": "2026-11-01", "name": "Toussaint"} and view["zone_label"] == "métropole"
    # Avec l'adresse : le département (et lui seul) part vers l'annuaire, puis l'académie vers le calendrier.
    client.post("/api/territory/geocode", json={"query": QUERY})
    fake.requests.clear()
    view = client.post("/api/territory/calendar/refresh").json()["view"]
    edu = [rq for rq in fake.requests if rq.headers["host"] == "data.education.gouv.fr"]
    assert len(edu) == 2 and parse_qs(edu[0].url.query.decode())["where"] == ['code_departement="080"']
    sent = " ".join(unquote_plus(rq.url.query.decode()) + rq.url.path for rq in fake.requests)
    assert "Hôtel" not in sent and "80021" not in sent and "49.89" not in sent, "ni l'adresse, ni la commune"
    assert view["school"]["academy"] == "Amiens" and view["school"]["zone"] == "Zone B"
    assert view["school"]["breaks"][1] == {"label": "Vacances de Noël", "start": "2026-12-19", "end": "2027-01-03"}
    assert view["place"]["academy"] == "Amiens" and {s["name"] for s in view["sources"]} == {
        "Jours fériés 2026",
        "Jours fériés 2027",
        "Vacances scolaires",
    }
    # La prévision du mois dit ses jours fériés ; l'année qui vient, ses vacances.
    month = client.get("/api/budget").json()["forecast"]
    assert {"date": "2026-11-01", "name": "Toussaint"} not in month["holidays"]  # octobre : aucun férié
    year = client.get("/api/forecast/year").json()
    assert year["points"][0]["holidays"] == ["01/11 : Toussaint", "11/11 : 11 novembre"]
    assert year["points"][1]["school"] == ["Vacances de Noël du 19/12 au 03/01"]
    assert year["calendar"]["academy"] == "Amiens"
    # Relève ratée : les données d'avant restent, l'erreur est dite.
    fake.down.update({"calendrier.api.gouv.fr", "data.education.gouv.fr"})
    result = client.post("/api/territory/calendar/refresh").json()
    assert len(result["result"]["errors"]) == 3 and all("restent" in e for e in result["result"]["errors"])
    assert result["view"]["holidays"][0]["name"] == "Toussaint" and result["view"]["school"]["academy"] == "Amiens"
    assert all(s["last_error"] for s in result["view"]["sources"])
    # Accord retiré : la prévision ne décale plus rien, rien n'est montré.
    client.put("/api/consents/calendar", json={"granted": False})
    assert client.get("/api/forecast/year").json()["calendar"] is None


def test_background_tick_fetches_only_what_is_due(app, net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    fetch = svc.default_fetch(http=app.state.territory_http, resolver=app.state.territory_resolver)
    from app.db import utcnow

    now = utcnow()
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now, today=TODAY, fetch=fetch) == {}, "sans accord, rien"
    client.put("/api/consents/calendar", json={"granted": True})
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now, today=TODAY, fetch=fetch) == {"calendar_checked": 2, "calendar_errors": 0}
        db.commit()
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now + timedelta(hours=1), today=TODAY, fetch=fetch) == {}, "frais : rien à relire"
    assert len(fake.requests) == 2
