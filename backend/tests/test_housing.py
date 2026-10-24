"""Ton logement face aux données publiques (#34) : les DPE de ton immeuble (ADEME, réponse réelle du 24 octobre 2026
pour le 14 rue Dalmatie à Toulouse, réduite), la règle F/G de Service-Public (F1311), la carte des loyers de l'ANIL
(fichiers 2025 réels, réduits, en Windows-1252). Rien ne part sans accord ; seul l'identifiant BAN de l'adresse part
vers l'ADEME ; Bank ne retient jamais un DPE seul ; chaque chiffre vient d'une donnée datée et sourcée."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.api import deps
from app.db import utcnow
from app.domain import correlations as corr
from app.domain import housing as dom
from app.domain import procedures as dp
from app.domain import territory as terr
from app.domain import vault as dv
from app.domain.recurring import add_months
from app.importers.base import ParsedTransaction
from app.models import Account, HousingSource, Procedure, VaultDocument, VaultField
from app.services import home_value
from app.services import housing as svc
from app.services.ingest import ingest
from tests.conftest import PASSWORD
from tests.scenario import seed_household

FIXTURES = Path(__file__).parent / "fixtures" / "logement"
BAN = "31555_2468_00014"
TODAY = date(2026, 10, 24)
# Ce qu'un constat logement ne dit jamais : ni jugement sur le loyer, ni invitation pressante.
FORBIDDEN = ("abusi", "excessi", "arnaque", "maintenant", "urgent", "recommand", "conseille", "fonce", "profit")


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def clean(s: str) -> str:
    return s.replace("\xa0", " ").replace(" ", " ")


def assert_wording(*texts: str) -> None:
    for t in texts:
        hits = [w for w in FORBIDDEN if re.search(rf"\b{w}", fold(t))]
        assert not hits, (t, hits)
        assert "!" not in t


# ------------------------------------------------------------------ lecture des sources
def test_geocoding_keeps_the_ban_identifier_only_to_the_house_number() -> None:
    place = terr.parse_geocode(read("geocodage_numero.json"))
    assert place is not None and (place.ban_id, place.precision, place.citycode) == (BAN, "housenumber", "31555")
    street = terr.parse_geocode((FIXTURES.parent / "calendrier" / "geocodage_adresse.json").read_bytes())
    assert street is not None and street.precision == "street" and street.ban_id is None, (
        "à la rue près : pas d'immeuble"
    )


def test_dpe_parser_reads_the_real_answer_and_refuses_the_rest() -> None:
    found, total = dom.parse_dpe(read("dpe_14_rue_dalmatie.json"), BAN)
    assert total == 4 and [d.number for d in found] == [
        "2631E0120274I",
        "2631E0066371D",
        "2531E2571077Y",
        "2231E1713168I",
    ]
    f = found[1]
    assert (f.letter, f.ges, f.established, f.valid_until) == ("F", "C", date(2026, 1, 10), date(2036, 1, 9))
    assert (f.building, f.where, f.surface_m2, f.depcode) == ("appartement", "Etage RDC; Porte 2", 65.3, "31")
    assert f.address == "14 Rue Dalmatie 31500 Toulouse"
    # Deux logements au rez-de-chaussée, 65 et 65,3 m², classés E et F : c'est bien à toi de dire lequel est le tien.
    assert {d.letter for d in found if d.floor == 0} == {"E", "F"}
    assert dom.Dpe.from_dict(f.to_dict()) == f
    assert dom.parse_dpe(read("dpe_aucun.json"), BAN) == ([], 0)
    # Un DPE remplacé par un autre de la liste est écarté.
    data = json.loads(read("dpe_14_rue_dalmatie.json"))
    data["results"][0]["numero_dpe_remplace"] = "2531E2571077Y"
    assert "2531E2571077Y" not in [d.number for d in dom.parse_dpe(json.dumps(data).encode(), BAN)[0]]
    for broken, message in (
        (b"<html>", "illisible"),
        (b'{"results": []}', "inattendue"),
        (read("dpe_14_rue_dalmatie.json").replace(b'"F"', b'"Z"'), "illisible"),
    ):
        with pytest.raises(dom.HousingError, match=message):
            dom.parse_dpe(broken, BAN)
    with pytest.raises(dom.HousingError, match="autre adresse"):
        dom.parse_dpe(read("dpe_14_rue_dalmatie.json"), "31555_2468_00016")


def test_dpe_request_sends_only_the_ban_identifier() -> None:
    url = dom.dpe_url(BAN)
    assert url.startswith("https://data.ademe.fr/data-fair/api/v1/datasets/dpe03existant/lines?")
    q = parse_qs(url.split("?", 1)[1])
    assert q["identifiant_ban_eq"] == [BAN] and "qs" not in q and q["sort"] == ["-date_etablissement_dpe"]
    with pytest.raises(dom.HousingError):
        dom.dpe_url("12 rue des Lilas")


def test_market_parser_reads_the_real_files_and_the_reliability_rule() -> None:
    body = read("loyers_appartement_extrait.csv")
    with pytest.raises(UnicodeDecodeError):
        body.decode("utf-8")  # publié en Windows-1252
    toulouse = dom.parse_market(body, "31555", "app", 2025)
    assert toulouse is not None and toulouse.commune == "Toulouse" and toulouse.level == "commune"
    assert (round(toulouse.m2, 2), round(toulouse.low, 2), round(toulouse.high, 2)) == (13.98, 11.17, 17.51)
    assert toulouse.obs == 225_512 and toulouse.reliable
    low_r2 = dom.parse_market(body, "38253", "app", 2025)
    assert low_r2 is not None and round(low_r2.r2, 3) == 0.485 and not low_r2.reliable
    village = dom.parse_market(body, "26030", "app", 2025)
    assert village is not None and village.commune == "La Bâtie-des-Fonds" and village.level == "maille"
    assert village.obs == 0 and not village.reliable, "moins de 30 annonces dans la commune"
    assert dom.parse_market(body, "75109", "app", 2025) is not None, "arrondissement de Paris, comme le géocodeur"
    assert dom.parse_market(body, "45234", "app", 2025) is None
    house = dom.parse_market(read("loyers_maison_extrait.csv"), "31555", "mai", 2025)
    assert house is not None and round(house.m2, 2) == 12.86
    with pytest.raises(dom.HousingError, match="colonnes"):
        dom.parse_market(b"a;b\n1;2\n", "31555", "app", 2025)
    with pytest.raises(dom.HousingError, match="hors bornes"):
        dom.parse_market(body.replace(b"13,9839274240567", b"313,98"), "31555", "app", 2025)
    assert dom.market_file("maison", 4) == "mai" and dom.market_file("appartement", None) == "app"
    assert dom.market_file("appartement", 2) == "app12" and dom.market_file("appartement", 3) == "app3"
    assert dom.market_file(None, 2) is None, "type inconnu : on ne devine pas"


def test_a_malformed_market_file_fails_closed() -> None:
    head = b'"INSEE_C";"LIBGEO";"loypredm2";"lwr.IPm2";"upr.IPm2";"TYPPRED";"nbobs_com";"R2_adj"\n'
    with pytest.raises(dom.HousingError, match="incomplète"):
        dom.parse_market(head + b'"31555";"Toulouse";13,9\n', "31555", "app", 2025)
    with pytest.raises(dom.HousingError, match="illisible"):
        dom.parse_market(head + b'"31555";"' + b"x" * 200_000 + b'"\n', "31555", "app", 2025)


def test_the_csv_is_picked_from_the_dataset_page_on_static_data_gouv_only() -> None:
    page = read("carte_des_loyers_2025.json")
    for key, name in (("app", "pred-app-mef"), ("app12", "pred-app12-"), ("app3", "pred-app3-"), ("mai", "pred-mai-")):
        url, published = dom.pick_resource(page, key)
        assert url.startswith("https://static.data.gouv.fr/resources/") and name in url and published == "2025-12-11"
    tampered = page.replace(b"https://static.data.gouv.fr/resources/", b"https://evil.example/resources/")
    with pytest.raises(dom.HousingError, match="introuvable"):
        dom.pick_resource(tampered, "app")


# ------------------------------------------------------------------ la règle F/G
def test_the_rule_starts_at_signature_or_at_the_sure_renewal() -> None:
    assert dom.covered_from(date(2023, 6, 1), False, "31") == (date(2023, 6, 1), True)
    # Vide, signé avant le 24/08/2022 : sans savoir qui loue, la reconduction de six ans (la plus tardive).
    assert dom.covered_from(date(2021, 3, 1), False, "31") == (date(2027, 3, 1), False)
    assert dom.covered_from(date(2021, 3, 1), None, "31") == (date(2027, 3, 1), False)
    assert dom.covered_from(date(2021, 3, 1), True, "31") == (date(2023, 3, 1), True), "meublé : un an"
    # Outre-mer : depuis le 1er juillet 2024.
    assert dom.rule_start("974") == date(2024, 7, 1) and dom.rule_start("13") == date(2022, 8, 24)
    assert dom.covered_from(date(2023, 1, 1), True, "974") == (date(2025, 1, 1), True)


D0 = date(2025, 1, 5)


def rent_series(amounts: list[int]) -> corr.Series:
    hist = tuple((add_months(D0, i), a) for i, a in enumerate(amounts))
    return corr.Series("foncia", "FONCIA LOYER", "logement", "monthly", amounts[-1], hist)


def lease(**fields: Any) -> corr.Doc:
    base: dict[str, Any] = {"loyer": 60_000, "charges": 3_000, "date_debut": "2023-06-01", "bailleur": "SCI Dalmatie"}
    base.update(fields)
    return corr.Doc(7, "lease", date(2023, 6, 1), base)


def dpe(letter: str = "F", established: date = date(2026, 1, 10)) -> dom.Dpe:
    return dom.Dpe("2631E0066371D", letter, "C", established, None, "appartement", "", "", 0, 65.3, "31")


def checks(link: corr.HousingLink, *docs: corr.Doc, series: corr.Series | None = None) -> tuple[list[Any], list[str]]:
    inputs = corr.Inputs(today=TODAY, series=[series] if series else [], docs=list(docs), housing=link)
    return corr.housing_checks(inputs)


# 63 € de janvier à mai 2026, 650 € à l'anniversaire du bail (juin), jusqu'en octobre : cinq mois à +20 €.
PAID = [63_000] * 17 + [65_000] * 5


def test_a_revision_on_a_dwelling_classed_f_is_not_due_and_the_amount_comes_from_the_payments() -> None:
    link = corr.HousingLink(dpe=dpe(), depcode="31")
    (f,), notes = checks(link, lease(revision="IRL 2e trimestre 2023"), series=rent_series(PAID))
    assert f.kind == "rent_dpe_frozen" and f.level == "danger" and f.topic == "logement"
    assert f.yearly_cents == 2_000 * 12
    body = clean(f.body)
    assert "classé F (DPE n° 2631E0066371D du 10/01/2026, base de l'ADEME)" in body
    assert "a pris effet le 01/06/2023, après le 24/08/2022" in body
    assert "la révision du 05/06/2026 (+20,00 € par mois) n'est pas due (Service-Public, fiche F1311)" in body
    assert "Payé en plus : 100,00 € sur 5 mois depuis le 05/06/2026" in body and "provision pour charges" in body
    assert any(e.ref == f"url:{dom.RULE_URL}" for e in f.evidence) and any(e.ref == "doc:7" for e in f.evidence)
    assert f.action and f.action["template"] == "revision_loyer_dpe"
    assert f.action["initial"]["montant"] == 10_000 and f.action["initial"]["loyer_avant"] == 63_000
    assert_wording(f.title, f.body)
    assert any("Carte des loyers" in n for n in notes)
    # « révisé dans la limite de l'IRL » ne rassure pas à tort à côté.
    small = rent_series([63_000] * 17 + [63_600] * 5)  # +6 € : dans la limite de l'IRL
    docs = [lease(revision="IRL 2e trimestre 2023")]
    alone = [x.kind for x in corr.analyse(corr.Inputs(today=TODAY, series=[small], docs=docs))]
    assert "rent_revised" in alone
    kinds = [x.kind for x in corr.analyse(corr.Inputs(today=TODAY, series=[small], docs=docs, housing=link))]
    assert "rent_dpe_frozen" in kinds and "rent_revised" not in kinds


def test_the_dpe_finding_never_guesses() -> None:
    paid = rent_series(PAID)
    # Classe E : la règle ne joue pas, et c'est dit.
    found, notes = checks(corr.HousingLink(dpe=dpe("E"), depcode="31"), lease(), series=paid)
    assert found == [] and any("classé E" in n for n in notes)
    # Aucune classe connue : rien, et le manque est dit.
    found, notes = checks(corr.HousingLink(depcode="31"), lease(), series=paid)
    assert found == [] and any("Classe énergie du logement inconnue" in n for n in notes)
    # Classe lue dans le bail : la règle joue aussi, sans DPE de l'ADEME.
    (f,), _ = checks(corr.HousingLink(depcode="31"), lease(classe_dpe="G"), series=paid)
    assert "classé G (classe énergie lue dans ton bail)" in clean(f.body) and f.confidence == 0.7
    # Bail sans date, loyer non reconnu, département inconnu.
    _, notes = checks(corr.HousingLink(dpe=dpe()), lease(date_debut=None), series=paid)
    assert any("date de prise d'effet" in n for n in notes)
    _, notes = checks(corr.HousingLink(dpe=dpe(), depcode="31"), lease())
    assert any("pas encore reconnu" in n for n in notes)
    no_dep = dom.Dpe("2631E0066371D", "F", None, date(2026, 1, 10), None, "appartement", "", "", 0, 65.3, None)
    _, notes = checks(corr.HousingLink(dpe=no_dep), lease(), series=paid)
    assert any("département est inconnu" in n for n in notes)
    # Une révision antérieure au DPE ne compte pas : la classe d'alors n'est pas connue.
    found, notes = checks(corr.HousingLink(dpe=dpe(established=date(2026, 9, 1)), depcode="31"), lease(), series=paid)
    assert found == [] and any("Aucune révision vue" in n for n in notes)
    # Bail ancien, vide : sous la règle au plus tard en 2027 ; d'ici là, une révision reste possible.
    found, notes = checks(corr.HousingLink(dpe=dpe(), depcode="31"), lease(date_debut="2021-06-01"), series=paid)
    assert found == [] and any("au plus tard le 01/06/2027" in n for n in notes)
    # Une hausse loin de l'anniversaire (régularisation de charges) n'est pas une révision.
    found, _ = checks(corr.HousingLink(dpe=dpe(), depcode="31"), lease(date_debut="2023-02-01"), series=paid)
    assert found == []


def test_only_revisions_less_than_three_years_old_are_counted() -> None:
    hist = [(date(2023, 6, 5), 60_000)]
    hist += [(add_months(date(2023, 7, 5), i), 61_000) for i in range(40)]
    series = corr.Series("foncia", "FONCIA LOYER", "logement", "monthly", 61_000, tuple(hist))
    old = lease(
        date_debut="2022-07-01", meuble="oui", classe_dpe="F"
    )  # meublé signé avant la règle, reconduit le 01/07/2023
    found, notes = checks(corr.HousingLink(depcode="31"), old, series=series)
    assert found == [] and any("plus de trois ans" in n for n in notes)


def test_a_charge_increase_after_the_frozen_revision_is_not_counted_as_overpaid() -> None:
    """Révision gelée de +20 € au 05/06/2026, puis provision de charges +15 € en janvier (hors anniversaire) :
    seuls les 20 € de la révision sont comptés, chaque mois."""
    hist = [(add_months(date(2025, 10, 5), i), 63_000) for i in range(8)]
    hist += [(add_months(date(2026, 6, 5), i), 65_000) for i in range(7)]
    hist += [(add_months(date(2027, 1, 5), i), 66_500) for i in range(3)]
    steps = dom.anniversary_steps(hist, date(2023, 6, 1))
    assert [(s.day, s.after - s.before) for s in steps] == [(date(2026, 6, 5), 2_000)]
    o = dom.overpaid(hist, steps, date(2023, 6, 1), date(2027, 3, 10))
    assert o is not None and (o.monthly_cents, o.months, o.total_cents) == (2_000, 10, 20_000)


def test_rent_is_placed_in_the_market_range_only_when_the_indicator_is_reliable() -> None:
    body = read("loyers_appartement_extrait.csv")
    toulouse = dom.parse_market(body, "31555", "app", 2025)
    link = corr.HousingLink(market=toulouse, surface_m2=65.0, surface_source="ta description du logement")
    (f,), _ = checks(link, lease(), series=rent_series(PAID))
    assert f.kind == "rent_market" and f.level == "info" and f.yearly_cents is None and not f.action
    body_text = clean(f.body)
    assert body_text.startswith("10,00 €/m² charges comprises (650,00 € pour 65 m²) : sous l'intervalle des annonces")
    assert "de 11,17 à 17,51 €/m² (loyer prédit 13,98 €/m², appartement (toutes tailles) loué vide" in body_text
    assert any(e.ref == f"url:{dom.MARKET_PAGE.format(year=2025)}" and dom.MARKET_CREDIT in e.value for e in f.evidence)
    assert_wording(f.title, f.body)
    (high,), _ = checks(link, lease(), series=rent_series([150_000, 150_000]))
    assert "au-dessus de l'intervalle" in high.body
    assert_wording(high.body)
    # Peu fiable, meublé, surface inconnue : pas de comparaison, et le manque est dit.
    fragile = corr.HousingLink(market=dom.parse_market(body, "38253", "app", 2025), surface_m2=65.0)
    found, notes = checks(fragile, lease(), series=rent_series(PAID))
    assert found == [] and any("R² 0,49" in n and "30 annonces" in n for n in notes)
    found, notes = checks(link, lease(meuble="oui"), series=rent_series(PAID))
    assert found == [] and any("meublé" in n for n in notes)
    found, notes = checks(corr.HousingLink(market=toulouse), lease(), series=rent_series(PAID))
    assert found == [] and any("Surface habitable inconnue" in n for n in notes)


def test_the_lease_reader_finds_the_energy_class() -> None:
    text_ = "CONTRAT DE LOCATION\nPrend effet le 01/06/2023\nLoyer mensuel hors charges : 600,00 €\nDPE : classe F\n"
    found = {f.key: f.value for f in dv._apply_rules("lease", dv.lines_of(text_))}
    assert found["classe_dpe"] == "F"
    vague = "CONTRAT DE LOCATION\nClasse énergie à définir\n"
    assert "classe_dpe" not in {f.key for f in dv._apply_rules("lease", dv.lines_of(vague))}


def test_the_letter_quotes_the_rule_and_the_amounts() -> None:
    tpl = dp.TEMPLATES["revision_loyer_dpe"]
    values = {"nom": "Camille Martin", "adresse_logement": "14 rue Dalmatie, 31500 Toulouse", "bailleur": "SCI Dalmatie",
              "classe": "F", "numero_dpe": "2631E0066371D", "date_revision": "2026-06-05", "loyer_avant": 63_000,
              "hausse": 2_000, "montant": 10_000, "periode": "du 06/2026 au 10/2026"}  # fmt: skip
    letter = clean(dp.render_letter(tpl, {k: {"value": v} for k, v in values.items()}, TODAY) or "")
    assert "révisé le 5 juin 2026, passant de 630,00 € à 650,00 €" in letter
    assert "Service-Public (fiche F1311)" in letter and "24 août 2022" in letter and "(DPE n° 2631E0066371D)" in letter
    assert "soit 100,00 € (du 06/2026 au 10/2026)" in letter
    assert all(s.url is None or s.url.startswith("https://") for s in tpl.sources)


# ------------------------------------------------------------------ bout en bout
class Chunks(httpx.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self) -> Iterator[bytes]:
        yield self.data


class FakeNet:
    """Géoplateforme, ADEME, data.gouv.fr, Service-Public, opendata.paris.fr et data.economie.gouv.fr simulés avec
    leurs vraies réponses (réduites) ; Géorisques coupe la connexion, comme depuis ce serveur le 24 octobre 2026.
    L'hôte se lit dans l'en-tête Host : netfetch épingle la connexion sur l'adresse résolue."""

    def __init__(self, geocode: str = "geocodage_numero.json") -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.geocode = geocode
        self.georisques_up = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.headers["host"], request.url.path
        if host in self.down:
            return httpx.Response(503, stream=Chunks(b""))
        if host == "www.georisques.gouv.fr" and not self.georisques_up:
            raise httpx.ConnectError("Connection reset by peer", request=request)
        if host == "www.georisques.gouv.fr":
            body = b'{"openapi": "3.0.1"}'
        elif host == "www.service-public.gouv.fr":
            body = read("f1314_extrait.html")
        elif host == "opendata.paris.fr":
            if path.endswith("/exports/json"):
                body = read("paris_loyers_reference_extrait.json")
            elif path.endswith("/exports/geojson"):
                body = read("paris_quartiers_extrait.geojson")
            else:
                body = read("paris_jeu.json")
        elif host == "data.economie.gouv.fr":
            dep = parse_qs(request.url.query.decode())["where"][0]
            body = read("fiscalite_75.json" if '"75"' in dep else "fiscalite_31_extrait.json")
        elif host == "data.geopf.fr":
            body = read(self.geocode)
        elif host == "data.ademe.fr":
            body = read("dpe_14_rue_dalmatie.json")
        elif host == "www.data.gouv.fr":
            if "-en-2025" not in path:
                return httpx.Response(404, stream=Chunks(b"{}"))
            body = read("carte_des_loyers_2025.json")
        else:
            assert host == "static.data.gouv.fr", host
            name = path.rsplit("/", 1)[-1]
            body = read(
                "loyers_maison_extrait.csv" if name.startswith("pred-mai-") else "loyers_appartement_extrait.csv"
            )
        return httpx.Response(200, stream=Chunks(body))

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]


@pytest.fixture
def net(app, auth_client: TestClient) -> Iterator[tuple[TestClient, FakeNet]]:  # type: ignore[no-untyped-def]
    fake = FakeNet()
    for name in ("territory", "housing"):
        setattr(app.state, f"{name}_http", httpx.Client(transport=httpx.MockTransport(fake)))
        setattr(app.state, f"{name}_resolver", lambda host: ["93.184.216.34"])
    app.dependency_overrides[deps.today] = lambda: TODAY
    yield auth_client, fake
    app.dependency_overrides.clear()


def _seed_rent(app) -> None:  # type: ignore[no-untyped-def]
    """Le foyer de test (loyer de 650 € de juin à octobre 2026), avec les mois d'avant à 630 € et un bail du 01/06/2023 :
    la hausse tombe à l'anniversaire du bail."""
    with app.state.sessionmaker() as db:
        seed_household(db, today=TODAY)
        account = db.scalars(select(Account).where(Account.kind == "checking")).first()
        assert account is not None
        before = [ParsedTransaction(add_months(date(2025, 10, 3), i), -63_000, "PRLV SEPA FONCIA LOYER ECH/03")
                  for i in range(8)]  # fmt: skip
        ingest(db, account, before, provider="file")
        doc = VaultDocument(kind="lease", title="Bail", filename="bail.pdf", mime="application/pdf", size=10,
                            sha256="d" * 64, status="verified", source="upload", method="pdf_text", confidence=0.9,
                            document_date=date(2023, 6, 1))  # fmt: skip
        db.add(doc)
        db.flush()
        fields = {"loyer": 60_000, "charges": 3_000, "date_debut": "2023-06-01", "bailleur": "SCI Dalmatie",
                  "meuble": "non", "revision": "IRL 2e trimestre 2023"}  # fmt: skip
        db.add_all(
            [VaultField(document_id=doc.id, key=k, value=json.dumps(v), confidence=0.9) for k, v in fields.items()]
        )
        db.commit()


def test_nothing_leaves_without_consent(net: tuple[TestClient, FakeNet]) -> None:
    client, fake = net
    view = client.get("/api/housing").json()
    assert view["consent"] is False and view["findings"] == [] and view["dpe"]["candidates"] == []
    for path in ("/api/housing/dpe/refresh", "/api/housing/market/refresh"):
        r = client.post(path)
        assert r.status_code == 409 and "Ton logement face aux données publiques" in r.json()["detail"]
    assert fake.requests == []


def test_dpe_market_findings_and_letter_end_to_end(app, net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    _seed_rent(app)
    assert client.put("/api/consents/housing", json={"granted": True}).status_code == 200
    # Sans adresse : ce qui manque est dit, rien ne part.
    r = client.post("/api/housing/dpe/refresh")
    assert r.status_code == 409 and "Situe d'abord ton adresse" in r.json()["detail"] and fake.requests == []
    assert client.put("/api/consents/geocoding", json={"granted": True}).status_code == 200
    assert client.post("/api/territory/geocode", json={"query": "14 rue Dalmatie Toulouse"}).status_code == 200
    fake.requests.clear()

    r = client.post("/api/housing/dpe/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["message"] == "4 DPE à ton adresse : indique lequel est ton logement."
    (req,) = fake.requests
    q = parse_qs(req.url.query.decode())
    assert req.headers["host"] == "data.ademe.fr" and q["identifiant_ban_eq"] == [BAN]
    assert set(q) == {"identifiant_ban_eq", "size", "sort", "select"}, "seul l'identifiant de l'adresse part"
    view = r.json()["view"]
    assert len(view["dpe"]["candidates"]) == 4 and view["dpe"]["chosen"] is None
    # Plusieurs logements dans l'immeuble : rien n'est retenu seul, le constat attend ton choix.
    assert not [f for f in view["findings"] if f["kind"] == "rent_dpe_frozen"]
    assert any("Classe énergie du logement inconnue" in n for n in view["notes"])
    assert client.post("/api/housing/dpe/choose", json={"number": "2631E9999999X"}).status_code == 422

    view = client.post("/api/housing/dpe/choose", json={"number": "2631E0066371D"}).json()
    assert view["dpe"]["chosen"]["letter"] == "F" and view["dpe"]["chosen_at"]
    frozen = next(f for f in view["findings"] if f["kind"] == "rent_dpe_frozen")
    assert frozen["level"] == "danger" and frozen["yearly_cents"] == 24_000
    assert "Payé en plus : 100,00 € sur 5 mois depuis le 03/06/2026" in clean(frozen["body"])
    corr_view = client.get("/api/correlations").json()
    kinds = [f["kind"] for f in corr_view["findings"]]
    assert "rent_dpe_frozen" in kinds and "rent_revised" not in kinds

    # La lettre au bailleur, préparée depuis le constat.
    r = client.post("/api/correlations/start", json={"key": frozen["key"]})
    assert r.status_code == 201, r.text
    with app.state.sessionmaker() as db:
        proc = db.get(Procedure, r.json()["procedure_id"])
        assert (
            proc is not None
            and proc.template == "revision_loyer_dpe"
            and proc.title == "Révision de loyer non due (classe F)"
        )
        fields = {k: v["value"] for k, v in json.loads(proc.data)["fields"].items()}
        assert fields["montant"] == 10_000 and fields["classe"] == "F" and fields["bailleur"] == "SCI Dalmatie"
        assert fields["date_revision"] == "2026-06-03" and fields["numero_dpe"] == "2631E0066371D"

    # La carte des loyers : la fiche 2026 n'existe pas encore, celle de 2025 donne le fichier.
    home_value_profile(app)
    fake.requests.clear()
    r = client.post("/api/housing/market/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["message"] == "Carte des loyers 2025 lue pour Toulouse."
    assert fake.hosts() == ["www.data.gouv.fr", "www.data.gouv.fr", "static.data.gouv.fr"]
    assert all(not r.url.query or b"31555" not in r.url.query for r in fake.requests), "rien de toi ne part"
    view = r.json()["view"]
    market = next(f for f in view["findings"] if f["kind"] == "rent_market")
    assert "sous l'intervalle des annonces de Toulouse" in market["body"] and view["market"]["reliable"] is True
    assert_wording(market["body"], frozen["body"], *view["notes"])
    # Même fichier publié : la fiche est relue, le CSV n'est pas retéléchargé.
    fake.requests.clear()
    assert client.post("/api/housing/market/refresh").status_code == 200
    assert "static.data.gouv.fr" not in fake.hosts()

    # Chiffré au repos ; le journal ne dit pas l'adresse ; l'export emporte tout.
    with app.state.sessionmaker() as db:
        raw = " ".join(db.execute(text("SELECT data || coalesce(source_url, '') FROM housing_sources")).scalars())
        assert "Dalmatie" not in raw and BAN not in raw and "Toulouse" not in raw
    exported = client.post("/api/export", json={"password": PASSWORD})
    assert exported.status_code == 200
    housing = {h["key"]: h for h in exported.json()["housing"]}
    assert housing["dpe"]["data"]["chosen"]["number"] == "2631E0066371D"
    assert housing["market"]["data"]["code"] == "31555"

    # Une source en panne garde la relève précédente et le dit.
    fake.down.add("data.ademe.fr")
    r = client.post("/api/housing/dpe/refresh")
    assert r.json()["result"]["status"] == "failed" and "reste" in r.json()["result"]["message"]
    assert r.json()["view"]["dpe"]["chosen"]["number"] == "2631E0066371D"

    # Effacer l'adresse efface les DPE de l'immeuble.
    assert client.delete("/api/territory/place").status_code == 200
    with app.state.sessionmaker() as db:
        assert db.get(HousingSource, "dpe") is None and db.get(HousingSource, "market") is not None


def home_value_profile(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        home_value.save_profile(db, actor="test", kind="appartement", surface=65, code="31555", commune="Toulouse",
                                section=None, rooms=None, in_wealth=False)  # fmt: skip
        db.commit()


def test_background_tick_reads_only_what_is_due(app, net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    fetch = svc.default_fetch(http=app.state.housing_http, resolver=app.state.housing_resolver)
    now = utcnow()
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now, today=TODAY, fetch=fetch) == {}, "sans accord, rien"
    client.put("/api/consents/housing", json={"granted": True})
    client.put("/api/consents/geocoding", json={"granted": True})
    client.post("/api/territory/geocode", json={"query": "14 rue Dalmatie Toulouse"})
    home_value_profile(app)
    fake.requests.clear()
    with app.state.sessionmaker() as db:
        report = svc.tick(db, now=now, today=TODAY, fetch=fetch)
        db.commit()
    # Toulouse n'est pas encadrée et aucune taxe foncière n'est connue : ni F1314 ni taux ; Géorisques est sondé.
    assert report == {"housing_dpe_refreshed": 1, "housing_market_refreshed": 1, "housing_risks_failed": 1}
    fake.requests.clear()
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now + timedelta(days=1), today=TODAY, fetch=fetch) == {}, "Géorisques : une semaine"
        later = svc.tick(db, now=now + timedelta(days=95), today=TODAY, fetch=fetch)
        db.commit()
    assert later == {"housing_dpe_refreshed": 1, "housing_market_refreshed": 1, "housing_risks_failed": 1}
    assert "static.data.gouv.fr" not in fake.hosts(), "même fichier publié : pas de nouveau téléchargement"


def test_one_failing_source_neither_blocks_the_others_nor_retries_every_hour(  # type: ignore[no-untyped-def]
    app, net: tuple[TestClient, FakeNet], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _fake = net
    client.put("/api/consents/housing", json={"granted": True})
    client.put("/api/consents/geocoding", json={"granted": True})
    client.post("/api/territory/geocode", json={"query": "14 rue Dalmatie Toulouse"})
    home_value_profile(app)
    base = svc.default_fetch(http=app.state.housing_http, resolver=app.state.housing_resolver)

    def fetch(url: str, max_bytes: int) -> bytes:
        if url.startswith(dom.DPE_URL):
            raise RuntimeError("réponse imprévue")  # rien de ce que les contrôles attendent
        return base(url, max_bytes)

    now = utcnow()
    with app.state.sessionmaker() as db:
        report = svc.tick(db, now=now, today=TODAY, fetch=fetch)
        db.commit()
    assert report["housing_dpe_failed"] == 1 and report["housing_market_refreshed"] == 1
    with app.state.sessionmaker() as db:
        row = db.get(HousingSource, "dpe")
        assert row is not None and row.last_error == "ADEME : réponse inattendue"
        assert "housing_dpe_failed" not in svc.tick(db, now=now + timedelta(hours=1), today=TODAY, fetch=fetch)
    # Une erreur que rien n'attrape dans une source : les suivantes passent quand même.
    monkeypatch.setattr(svc, "_tick_dpe", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with app.state.sessionmaker() as db:
        later = svc.tick(db, now=now + timedelta(days=40), today=TODAY, fetch=base)
    assert later["housing_dpe_error"] == 1 and later["housing_market_refreshed"] == 1
