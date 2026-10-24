"""Valeur du logement par les ventes voisines (DVF) : lecture du fichier communal (une mutation = un logement, sinon
écartée), médiane et quartiles vérifiés à la main sur une commune de test synthétique, bornes, critères relâchés
par paliers, cache trimestriel (pas de réseau au deuxième appel, nouveau trimestre = nouvelle extraction, échec =
ancienne extraction gardée), ligne du patrimoine en face du prêt, recherche de commune, accord."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.api import deps
from app.domain import dvf
from app.models import AuditLog
from app.services import consents
from app.services import home_value as svc

FIXTURES = Path(__file__).parent / "fixtures" / "dvf"
TODAY = date(2026, 10, 24)
HOME = {"kind": "appartement", "surface": 60, "code": "45234", "commune": "Commune de test"}


def fixture_sales() -> list[dvf.Sale]:
    sales: list[dvf.Sale] = []
    for year in (2023, 2024, 2025):
        sales += dvf.parse_commune((FIXTURES / f"45234_{year}.csv").read_bytes())
    return sales


# ------------------------------------------------------------------ lecture du fichier
def test_one_sale_per_mutation_and_only_single_homes() -> None:
    sales = fixture_sales()
    days = [s.day.isoformat() for s in sales]
    assert "2024-06-01" not in days, "deux appartements dans la même mutation : prix de l'ensemble, écartée"
    assert "2024-07-01" not in days, "appartement + local commercial : écartée"
    assert "2024-08-01" not in days and "2024-08-02" not in days, "VEFA et échange : pas des ventes"
    assert "2024-10-01" not in days, "terrain seul"
    houses = [s for s in sales if s.kind == "maison"]
    assert [(h.day.isoformat(), h.surface) for h in houses] == [("2024-09-01", 100), ("2025-06-30", 90)]
    cellar = next(s for s in sales if s.day == date(2025, 5, 2))
    assert cellar.price_eur == 248_000 and cellar.surface == 62, "une cave avec l'appartement : gardée"
    assert {s.section for s in sales} == {"AB", "AC", "AD"}
    assert len(sales) == 13


def test_reader_is_defensive() -> None:
    with pytest.raises(dvf.DvfError, match="colonnes absentes"):
        dvf.parse_commune(b"id_mutation,date_mutation\n1,2025-01-01\n")
    raw = (
        b"id_mutation|date_mutation|nature_mutation|valeur_fonciere|type_local|surface_reelle_bati|id_parcelle\n"
        b"A|2025-02-03|Vente|150000,00|Appartement|50|452340000A0001\n"
        b"B|pas une date|Vente|150000|Appartement|50|\n"
        b"C|2025-02-04|Vente||Appartement|50|\n"
        b"D|2025-02-05|Vente|150000|Appartement|5|\n"
        b"E|2025-02-06|Vente|900|Appartement|50|\n"
    )
    sales = dvf.parse_commune(raw, since=date(2025, 1, 1))
    assert sales == [dvf.Sale(date(2025, 2, 3), 150_000, "appartement", 50, None, "0A")]
    assert dvf.parse_commune(raw, since=date(2025, 3, 1)) == []
    assert dvf.Sale.from_row(sales[0].to_row()) == sales[0]


def test_commune_and_section_rules() -> None:
    assert dvf.normalize_code(" 75115 ") == "75115" and dvf.normalize_code("2a004") == "2A004"
    with pytest.raises(dvf.DvfError, match=r"arrondissement.*75101 à 75120"):
        dvf.normalize_code("75056")
    with pytest.raises(dvf.DvfError, match="Marseille"):
        dvf.normalize_code("13055")
    with pytest.raises(dvf.DvfError, match="Bas-Rhin"):
        dvf.normalize_code("67482")
    with pytest.raises(dvf.DvfError, match="5 caractères"):
        dvf.normalize_code("4523")
    assert dvf.department("97411") == "974" and dvf.department("2B033") == "2B"
    assert dvf.normalize_section(" a ") == "0A" and dvf.normalize_section("ab") == "AB"
    assert dvf.normalize_section("") is None and dvf.normalize_section(None) is None
    with pytest.raises(dvf.DvfError):
        dvf.normalize_section("A-")


# ------------------------------------------------------------------ statistiques
def test_quantiles_and_tukey_fences() -> None:
    assert dvf.quantile([1, 2, 3, 4], 0.5) == 2.5 and dvf.quantile([5], 0.75) == 5
    with pytest.raises(ValueError):
        dvf.quantile([], 0.5)
    small = fixture_sales()[:3]
    assert dvf.drop_outliers(small) == (small, 0)


def test_hand_checked_estimate() -> None:
    """60 m² : neuf appartements comparables à ± 30 % (42 à 78 m²) dans les 24 mois ; 500 €/m² sous la barrière
    basse (Q1 2 800 − 1,5 × 800 = 1 600) est écartée. Restent 2 600, 2 800, 3 000, 3 200, 3 400, 3 600, 3 800,
    4 000 €/m² : médiane 3 300, Q1 2 950 (position 1,75), Q3 3 650 (position 5,25)."""
    est = dvf.estimate(fixture_sales(), kind="appartement", surface=60)
    assert est.ok and est.count == 8 and est.outliers == 1 and est.scope == "commune"
    assert (est.median_m2_cents, est.low_m2_cents, est.high_m2_cents) == (330_000, 295_000, 365_000)
    assert (est.median_cents, est.low_cents, est.high_cents) == (19_800_000, 17_700_000, 21_900_000)
    assert (est.window_start, est.window_end) == ("2023-06-30", "2025-06-30"), "fenêtre : 24 mois avant la dernière"
    assert (est.first_sale, est.last_sale) == ("2024-01-10", "2025-05-02")
    assert any("hors norme" in n for n in est.notes)


def test_section_then_whole_commune_then_wider_surface() -> None:
    sales = fixture_sales()
    ab = dvf.estimate(sales, kind="appartement", surface=60, section="AB")
    assert ab.ok and ab.scope == "section" and ab.count == 5 and ab.median_m2_cents == 320_000 and ab.notes == []
    elsewhere = dvf.estimate(sales, kind="appartement", surface=60, section="ZZ")
    assert elsewhere.ok and elsewhere.scope == "commune" and "toute la commune" in elsewhere.notes[0]
    rooms = dvf.estimate(sales, kind="appartement", surface=60, rooms=3)
    assert rooms.ok and rooms.rooms_used and rooms.count == 8
    lonely = dvf.estimate(sales, kind="appartement", surface=60, rooms=9)
    assert lonely.ok and not lonely.rooms_used and any("pièces" in n for n in lonely.notes)
    wide = dvf.estimate(sales, kind="appartement", surface=100)
    assert wide.ok and wide.tolerance_percent > 30 and any("élargie" in n for n in wide.notes)


def test_not_enough_sales_says_so_instead_of_inventing() -> None:
    est = dvf.estimate(fixture_sales(), kind="maison", surface=95)
    assert not est.ok and est.median_cents == 0 and est.count == 2
    assert est.message.startswith("Pas assez de ventes comparables : 2 ventes de maison")
    none = dvf.estimate([s for s in fixture_sales() if s.kind == "appartement"], kind="maison", surface=95)
    assert not none.ok and "aucune vente de maison" in none.message


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"kind": "chateau", "surface": 60}, "appartement ou maison"),
        ({"kind": "maison", "surface": 8}, "entre 9 et 1000"),
        ({"kind": "maison", "surface": 1_001}, "entre 9 et 1000"),
        ({"kind": "maison", "surface": 60, "rooms": 0}, "pièces"),
        ({"kind": "maison", "surface": 60, "rooms": 21}, "pièces"),
    ],
)
def test_input_bounds(kw: dict[str, object], message: str) -> None:
    with pytest.raises(dvf.DvfError, match=message):
        dvf.estimate(fixture_sales(), **kw)  # type: ignore[arg-type]


def test_quarters() -> None:
    assert svc.quarter_of(date(2026, 10, 24)) == "2026-T4" and svc.quarter_of(date(2027, 1, 1)) == "2027-T1"
    assert svc.next_quarter(date(2026, 10, 24)) == date(2027, 1, 1)
    assert svc.next_quarter(date(2026, 2, 28)) == date(2026, 4, 1)


# ------------------------------------------------------------------ réseau simulé
class Chunks(httpx.SyncByteStream):
    """Corps livré par morceaux, comme sur le réseau (netfetch lit le flux brut)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self) -> Iterator[bytes]:
        for i in range(0, len(self.data), 4_096):
            yield self.data[i : i + 4_096]


def _resp(status: int, body: bytes = b"", content_type: str = "text/csv") -> httpx.Response:
    return httpx.Response(status, stream=Chunks(body), headers={"content-type": content_type})


class FakeDvf:
    """files.data.gouv.fr et geo.api.gouv.fr simulés. L'hôte se lit dans l'en-tête Host : netfetch épingle la
    connexion sur l'adresse résolue quand aucun proxy n'est configuré."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        if self.down:
            return _resp(503)
        if host == "geo.api.gouv.fr":
            communes = [
                {"nom": "Paris", "code": "75056", "codesPostaux": ["75015"]},
                {"nom": "Paris 15e Arrondissement", "code": "75115", "codesPostaux": ["75015"]},
                "pas une commune",
            ]
            return _resp(200, json.dumps(communes).encode(), "application/json")
        assert host == "files.data.gouv.fr"
        name = request.url.path.split("/")[-1]
        year = request.url.path.split("/")[-4]
        path = FIXTURES / f"{name.removesuffix('.csv')}_{year}.csv"
        return _resp(200, path.read_bytes()) if path.exists() else _resp(404)

    @property
    def files(self) -> list[str]:
        return [r.url.path for r in self.requests if r.headers["host"] == "files.data.gouv.fr"]


@pytest.fixture
def home(app, auth_client: TestClient) -> Iterator[tuple[TestClient, FakeDvf]]:  # type: ignore[no-untyped-def]
    fake = FakeDvf()
    app.state.dvf_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.dvf_resolver = lambda host: ["93.184.216.34"]
    app.dependency_overrides[deps.today] = lambda: TODAY
    yield auth_client, fake
    app.dependency_overrides.clear()


def _agree(client: TestClient) -> None:
    assert client.put(f"/api/consents/{svc.CONSENT}", json={"granted": True}).status_code == 200


def test_nothing_leaves_without_consent(home: tuple[TestClient, FakeDvf]) -> None:
    client, fake = home
    r = client.put("/api/home/profile", json=HOME)
    assert r.status_code == 200 and r.json()["result"] is None
    view = r.json()["view"]
    assert view["consent"] is False and view["profile"]["code"] == "45234" and view["estimate"] is None
    assert client.post("/api/home/refresh").status_code == 409
    assert client.get("/api/home/communes", params={"q": "Orléans"}).status_code == 409
    assert fake.requests == []


def test_estimate_cache_and_quarter_rollover(app, home: tuple[TestClient, FakeDvf]) -> None:  # type: ignore[no-untyped-def]
    client, fake = home
    _agree(client)
    r = client.put("/api/home/profile", json={**HOME, "section": "ab", "rooms": 3})
    assert r.status_code == 200 and r.json()["result"]["status"] == "refreshed"
    # Seuls les fichiers communaux sont demandés : pas d'adresse, pas de section, pas de surface, aucun paramètre.
    assert fake.files == [f"/geo-dvf/latest/csv/{y}/communes/45/45234.csv" for y in (2023, 2024, 2025, 2026)]
    assert all(not req.url.query for req in fake.requests)
    view = r.json()["view"]
    est = view["estimate"]
    assert est["ok"] and est["scope"] == "section" and est["median_cents"] == 19_200_000
    assert view["data"]["latest_sale"] == "2025-06-30" and view["data"]["years"] == [2023, 2024, 2025]
    assert view["data"]["fresh"] and view["data"]["fetched_at"] and view["data"]["sales"] == 12
    assert view["profile"]["section"] == "AB" and view["source"].startswith("DVF, Etalab / DGFiP")

    # Deuxième appel du même trimestre : rien ne part.
    again = client.post("/api/home/refresh").json()
    assert again["result"]["status"] == "fresh" and "01/01/2027" in again["result"]["message"]
    assert len(fake.files) == 4
    # Changer la surface ou la section se recalcule ici, sans réseau.
    r = client.put("/api/home/profile", json=HOME).json()
    assert r["result"] is None and r["view"]["estimate"]["median_cents"] == 19_800_000 and len(fake.files) == 4

    # Nouveau trimestre : nouvelle extraction.
    app.dependency_overrides[deps.today] = lambda: date(2027, 1, 5)
    assert client.get("/api/home").json()["data"]["fresh"] is False
    assert client.post("/api/home/refresh").json()["result"]["status"] == "refreshed"
    assert len(fake.files) == 8


def test_failed_download_keeps_the_previous_extraction(app, home: tuple[TestClient, FakeDvf]) -> None:  # type: ignore[no-untyped-def]
    client, fake = home
    _agree(client)
    client.put("/api/home/profile", json=HOME)
    app.dependency_overrides[deps.today] = lambda: date(2027, 1, 5)
    fake.down = True
    r = client.post("/api/home/refresh").json()
    assert r["result"]["status"] == "failed" and "garde l'extraction du" in r["result"]["message"]
    assert "503" in r["result"]["message"]
    view = r["view"]
    assert view["estimate"]["median_cents"] == 19_800_000 and view["data"]["error"] == "La source répond 503"
    assert view["data"]["fresh"] is False
    # Trois essais par jour, bouton ou tâche de fond : le quatrième attend demain.
    for _ in range(2):
        assert client.post("/api/home/refresh").json()["result"]["status"] == "failed"
    assert client.post("/api/home/refresh").status_code == 429
    calls = len(fake.requests)
    with app.state.sessionmaker() as db:
        assert (
            svc.tick(db, today=date(2027, 1, 5), now=datetime(2027, 1, 5, 9, tzinfo=UTC), fetch=svc.default_fetch())
            == {}
        )
    assert len(fake.requests) == calls
    # Le lendemain, la source revient.
    fake.down = False
    app.dependency_overrides[deps.today] = lambda: date(2027, 1, 6)
    assert client.post("/api/home/refresh").json()["result"]["status"] == "refreshed"


def test_unknown_commune_or_unpublished_files(home: tuple[TestClient, FakeDvf]) -> None:
    client, _fake = home
    _agree(client)
    r = client.put("/api/home/profile", json={**HOME, "code": "45000"}).json()
    assert r["result"]["status"] == "failed" and "Aucun fichier DVF" in r["result"]["message"]
    assert r["view"]["estimate"] is None and "garde" not in r["result"]["message"]
    assert client.put("/api/home/profile", json={**HOME, "code": "75056"}).status_code == 422
    assert client.put("/api/home/profile", json={**HOME, "surface": 2}).status_code == 422
    assert client.put("/api/home/profile", json={**HOME, "section": "@"}).status_code == 422


def test_home_line_in_net_worth_next_to_the_loan(home: tuple[TestClient, FakeDvf]) -> None:
    client, _fake = home
    before = client.get("/api/wealth").json()["net_worth"]
    _agree(client)
    client.put("/api/home/profile", json=HOME)
    loan = {
        "name": "Prêt immo",
        "kind": "immobilier",
        "principal_cents": 15_000_000,
        "annual_rate_bp": 300,
        "months": 240,
        "first_payment": "2024-01-05",
    }
    assert client.post("/api/loans", json=loan).status_code == 201
    view = client.get("/api/home").json()
    owed = view["loans_remaining_cents"]
    assert 0 < owed < 15_000_000 and view["loans"][0]["name"] == "Prêt immo"
    assert view["net_cents"] == 19_800_000 - owed and view["in_wealth"]
    wealth = client.get("/api/wealth").json()
    lines = [a for a in wealth["accounts"] if a["kind"] == svc.HOME_KIND]
    assert lines == [
        {"id": svc.HOME_LINE_ID, "name": svc.HOME_LINE, "kind": svc.HOME_KIND, "balance_cents": 19_800_000}
    ]
    nw = wealth["net_worth"]
    assert nw["by_bucket"]["immobilier"] == 19_800_000
    assert nw["net_cents"] - before["net_cents"] == 19_800_000 - owed, "le logement et le prêt, chacun une fois"

    client.put("/api/home/profile", json={**HOME, "in_wealth": False})
    assert not [a for a in client.get("/api/wealth").json()["accounts"] if a["kind"] == svc.HOME_KIND]
    assert client.delete("/api/home/profile").json()["profile"] is None
    assert client.get("/api/home").json()["estimate"] is None


def test_profile_is_encrypted_and_audit_says_what_changed_not_the_values(app, home: tuple[TestClient, FakeDvf]) -> None:  # type: ignore[no-untyped-def]
    client, _fake = home
    _agree(client)
    client.put("/api/home/profile", json={**HOME, "section": "AB"})
    client.put("/api/home/profile", json={**HOME, "section": "AB", "surface": 61})
    with app.state.sessionmaker() as db:
        raw = db.execute(text("SELECT data FROM home_profiles")).scalar_one()
        cached = db.execute(text("SELECT data FROM dvf_cache")).scalar_one()
        assert "45234" not in raw and "45234" not in cached
        entries = list(db.scalars(select(AuditLog).where(AuditLog.action.like("home.%")).order_by(AuditLog.id)))
        actions = [e.action for e in entries]
        assert actions[:3] == ["home.profile", "home.dvf_refresh", "home.profile"]
        assert json.loads(entries[2].details) == {"changed": "surface"}
        assert all("45234" not in e.details for e in entries)


def test_commune_search_sends_only_what_you_typed(home: tuple[TestClient, FakeDvf]) -> None:
    client, fake = home
    _agree(client)
    r = client.get("/api/home/communes", params={"q": "75015"})
    assert r.status_code == 200
    assert r.json() == [{"code": "75115", "name": "Paris 15e Arrondissement", "postcodes": ["75015"]}]
    sent = fake.requests[-1].url
    assert sent.params["codePostal"] == "75015" and "arrondissement-municipal" in sent.params["type"]
    client.get("/api/home/communes", params={"q": "Saint-Jean de Braye"})
    assert fake.requests[-1].url.params["nom"] == "Saint-Jean de Braye"
    assert client.get("/api/home/communes", params={"q": "12 rue de la Paix"}).status_code == 422
    fake.down = True
    assert client.get("/api/home/communes", params={"q": "Orléans"}).status_code == 502


def test_background_tick_refreshes_once_per_quarter(app, home: tuple[TestClient, FakeDvf]) -> None:  # type: ignore[no-untyped-def]
    client, fake = home
    fetch = svc.default_fetch(http=app.state.dvf_http, resolver=app.state.dvf_resolver)
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    with app.state.sessionmaker() as db:
        assert svc.tick(db, today=TODAY, now=now, fetch=fetch) == {}, "ni accord ni logement"
    client.put("/api/home/profile", json=HOME)
    with app.state.sessionmaker() as db:
        consents.set_consent(db, svc.CONSENT, True, actor="test")
        assert svc.tick(db, today=TODAY, now=now, fetch=fetch) == {"home_value_refreshed": 1}
        db.commit()
        assert svc.tick(db, today=TODAY, now=now, fetch=fetch) == {}
    assert len(fake.files) == 4


def test_agent_dossier_does_not_count_the_home_twice(app, home: tuple[TestClient, FakeDvf]) -> None:  # type: ignore[no-untyped-def]
    from app.services import advisor

    client, _fake = home
    flat = {"label": "Appartement", "kind": "immobilier", "value_cents": 25_000_000}
    pea = {"label": "PEA", "kind": "pea", "value_cents": 250_000}
    with app.state.sessionmaker() as db:
        advisor.set_answer(db, "outside_assets", [flat, pea], source="user:micka")
        db.commit()
        declared = advisor.build_dossier(db, TODAY)["wealth"]
    assert declared["outside_assets_cents"] == 25_250_000
    _agree(client)
    client.put("/api/home/profile", json=HOME)
    with app.state.sessionmaker() as db:
        w = advisor.build_dossier(db, TODAY)["wealth"]
    assert w["buckets"]["immobilier"] == 19_800_000 and w["outside_assets_cents"] == 250_000
    assert w["net_worth_cents"] == w["tracked_net_worth_cents"] + 250_000
