"""Ton logement face aux données publiques, suite (#34) : l'encadrement des loyers (fiche F1314 de Service-Public et
loyers de référence de la Ville de Paris, réponses réelles du 24 octobre 2026, réduites), la taxe foncière prévue à
partir des taux votés (data.economie.gouv.fr, vrais taux de Toulouse et de Paris) et Géorisques, injoignable depuis ce
serveur. Rien ne part sans accord ; ni l'adresse ni les coordonnées ne partent ; aucun chiffre n'est deviné."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from starlette.testclient import TestClient

from app.api import deps
from app.db import utcnow
from app.domain import correlations as corr
from app.domain import procedures as dp
from app.domain import property_tax as pt
from app.domain import rent_control as rc
from app.domain import vault as dv
from app.domain import yearahead as ya
from app.domain.recurring import RecurringSeries
from app.importers.base import ParsedTransaction
from app.models import Account, HousingSource, Procedure, VaultDocument, VaultField
from app.services import home_value
from app.services import housing as svc
from app.services import yearahead as ya_service
from app.services.ingest import ingest
from tests.conftest import PASSWORD
from tests.scenario import seed_household
from tests.test_housing import FakeNet, assert_wording, clean, read

TODAY = date(2026, 10, 24)
LON, LAT = 2.384005, 48.850941  # 5 rue Faidherbe, Paris 11e (Géoplateforme)


# ------------------------------------------------------------------ fiche F1314
def test_the_f1314_list_is_dated_and_says_when_it_is_outdated() -> None:
    page = rc.parse_page(read("f1314_extrait.html"))
    assert page.verified == date(2026, 8, 1) and page.missing == () and not page.changed
    newer = read("f1314_extrait.html").replace(
        "Vérifié le 01 août 2026".encode(), "Vérifié le 02 janvier 2027".encode()
    )
    assert rc.parse_page(newer).changed
    gone = read("f1314_extrait.html").replace(b"Montpellier", b"")
    assert rc.parse_page(gone).missing == ("Montpellier",) and rc.parse_page(gone).changed
    with pytest.raises(rc.RentControlError, match="encadrement"):
        rc.parse_page(b"<html>Autre fiche. V\xc3\xa9rifi\xc3\xa9 le 01 ao\xc3\xbbt 2026</html>")
    # Les territoires de la fiche, par code INSEE : arrondissements compris, communes en partie encadrées signalées.
    assert rc.territory_of("75111") == (rc.BY_KEY["paris"], False)
    assert rc.territory_of("69381") == (rc.BY_KEY["lyon"], False)
    assert rc.territory_of("59350") == (rc.BY_KEY["lille"], False), "Hellemmes et Lomme ont le code de Lille"
    assert rc.territory_of("38185") == (rc.BY_KEY["grenoble"], True)
    assert rc.territory_of("31555") is None


def test_malformed_public_files_fail_closed_with_a_readable_error() -> None:
    impossible = read("f1314_extrait.html").replace(
        "Vérifié le 01 août 2026".encode(), "Vérifié le 31 février 2026".encode()
    )
    with pytest.raises(rc.RentControlError, match="impossible"):
        rc.parse_page(impossible)
    empty = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id_quartier": 1,
             "nom_quartier": "X"}, "geometry": {"type": "Polygon", "coordinates": []}}]}  # fmt: skip
    with pytest.raises(rc.RentControlError, match="contour illisible"):
        rc.parse_quartiers(json.dumps(empty).encode())
    with pytest.raises(rc.RentControlError, match="aucune année"):
        rc.dataset_years([{"annee": "1999"}])


# ------------------------------------------------------------------ Paris : quartiers et loyers de référence
def test_paris_quartier_is_found_here_and_references_are_read_without_guessing() -> None:
    quartiers = rc.parse_quartiers(read("paris_quartiers_extrait.geojson"))
    q = rc.quartier_at(LON, LAT, quartiers)
    assert q is not None and (q.id, q.name) == (44, "Sainte-Marguerite")
    assert rc.quartier_at(1.4443, 43.6045, quartiers) is None, "Toulouse : hors des quartiers"
    rows = rc.load_rows(read("paris_loyers_reference_extrait.json"))
    assert rc.dataset_years(rows) == [2019, 2020, 2021, 2022, 2023, 2024, 2025], "2026 n'est pas encore publié"
    refs = rc.parse_references(rows, 44)
    r = rc.reference_for(refs, 2025, 2, "1946-1970", furnished=False)
    assert r is not None and (r.low, r.ref, r.high) == (16.9, 24.1, 28.9)
    assert rc.reference_for(refs, 2025, 6, "Avant 1946", furnished=True) == rc.reference_for(
        refs, 2025, 4, "Avant 1946", furnished=True
    ), "4 et plus"
    assert rc.reference_for(refs, 2026, 2, "1946-1970", furnished=False) is None
    # Le jeu se met à jour le 1er juillet : un bail de mars 2026 relève encore des valeurs 2025.
    assert rc.reference_year(date(2026, 3, 1)) == 2025 and rc.reference_year(date(2026, 7, 1)) == 2026
    assert rc.parse_paris_meta(read("paris_jeu.json")).startswith("2025-06-17")
    broken = json.loads(read("paris_loyers_reference_extrait.json"))
    broken[0]["max"] = 400
    with pytest.raises(rc.RentControlError, match="hors bornes"):
        rc.parse_references(rc.load_rows(json.dumps(broken).encode()), broken[0]["id_quartier"])
    # Aucune requête ne dit où tu habites : fichiers entiers.
    for url in (rc.paris_rows_url(), rc.paris_shapes_url(2025)):
        assert "Faidherbe" not in url and "2.38" not in url and "48.85" not in url


def control(**kw: object) -> rc.ControlLink:
    base: dict[str, object] = {
        "territory": rc.BY_KEY["paris"],
        "partial": False,
        "page": rc.PageCheck(date(2026, 8, 1), ()),
        "located": True,
        "quartier": "Sainte-Marguerite",
        "years": (2024, 2025),
        "refs": tuple(rc.parse_references(rc.load_rows(read("paris_loyers_reference_extrait.json")), 44)),
        "epoque": "1946-1970",
        "rooms": 2,
        "rooms_source": "ce que tu as indiqué",
    }
    base.update(kw)
    return rc.ControlLink(**base)  # type: ignore[arg-type]


def paris_lease(**fields: object) -> corr.Doc:
    base: dict[str, object] = {"loyer": 200_000, "date_debut": "2025-09-01", "meuble": "non", "bailleur": "M. Durand"}
    base.update(fields)
    return corr.Doc(9, "lease", date(2025, 9, 1), base)


def checks(link: corr.HousingLink, *docs: corr.Doc) -> tuple[list[corr.Finding], list[str]]:
    return corr.housing_checks(corr.Inputs(today=TODAY, docs=list(docs), housing=link))


def test_a_paris_rent_above_the_cap_gives_the_yearly_overpayment_and_the_free_route() -> None:
    link = corr.HousingLink(control=control(), surface_m2=60.0, surface_source="ta description du logement")
    (f,), _notes = checks(link, paris_lease())
    # 2 000 € pour 60 m² : 33,33 €/m², plafond 28,90 €/m² × 60 = 1 734 € : 266 € par mois, 3 192 € par an.
    assert f.kind == "rent_control_above" and f.level == "warning" and f.yearly_cents == 319_200
    body = clean(f.body)
    assert "Loyer de base 33,33 €/m² hors charges (2 000,00 € pour 60 m²)" in body
    assert "loyer de référence majoré de 28,90 €/m² (Sainte-Marguerite, 2 pièces, construit de 1946 à 1970" in body
    assert "266,00 € par mois au-dessus, soit 3 192,00 € par an" in body
    assert "complément de loyer" in body and "commission départementale de conciliation" in body and "F1216" in body
    assert any(e.ref == f"url:{rc.PARIS_PAGE}" and "indicative" in e.value for e in f.evidence)
    assert any(e.ref == f"url:{rc.PAGE_URL}" for e in f.evidence)
    assert f.action and f.action["template"] == "encadrement_loyer" and f.action["initial"]["ecart_mensuel"] == 26_600
    assert_wording(f.title, f.body)
    (ok,), _ = checks(link, paris_lease(loyer=150_000))
    assert ok.kind == "rent_control_ok" and ok.yearly_cents is None and "28,90 €/m²" in ok.body


def test_rent_control_asks_for_what_is_missing_and_never_guesses() -> None:
    link = corr.HousingLink(control=control(epoque=None), surface_m2=60.0)
    found, notes = checks(link, paris_lease())
    assert found == [] and any("l'époque de construction" in n and "ne devine rien" in n for n in notes)
    found, notes = checks(corr.HousingLink(control=control(rooms=None), surface_m2=None), paris_lease(meuble=None))
    (note,) = [n for n in notes if "Encadrement" in n]
    assert "meublé ou vide" in note and "pièces principales" in note and "surface habitable" in note
    # Bail signé après le 1er juillet 2026 : valeurs 2026 absentes du jeu, l'arrêté est cité.
    found, notes = checks(corr.HousingLink(control=control(), surface_m2=60.0), paris_lease(date_debut="2026-08-01"))
    assert found == [] and any("arrêté de 2026" in n and "dernière année publiée : 2025" in n for n in notes)
    # Bail d'avant l'encadrement, ou d'après la fin écrite sur la fiche.
    _, notes = checks(corr.HousingLink(control=control(), surface_m2=60.0), paris_lease(date_debut="2018-01-01"))
    assert any("avant l'encadrement" in n and "01/07/2019" in n for n in notes)
    _, notes = checks(corr.HousingLink(control=control(), surface_m2=60.0), paris_lease(date_debut="2026-12-01"))
    assert any("24/11/2026" in n for n in notes)
    # Ailleurs qu'à Paris : pas de données ouvertes vérifiées, le simulateur officiel ; une commune en partie encadrée.
    lyon = control(territory=rc.BY_KEY["lyon"], quartier=None, refs=())
    _, notes = checks(corr.HousingLink(control=lyon, surface_m2=60.0), paris_lease(date_debut="2023-01-01"))
    assert any("Lyon et Villeurbanne" in n and "simulateur officiel" in n for n in notes)
    grenoble = control(territory=rc.BY_KEY["grenoble"], partial=True)
    _, notes = checks(corr.HousingLink(control=grenoble), paris_lease())
    assert any("une partie seulement" in n for n in notes)
    # La fiche revue depuis la liste de Bank : c'est dit.
    _, notes = checks(corr.HousingLink(control=control(page=rc.PageCheck(date(2026, 12, 1), ()))), paris_lease())
    assert any("revue le 01/12/2026" in n for n in notes)
    assert_wording(*notes)


def test_the_rent_control_letter_quotes_the_rule() -> None:
    tpl = dp.TEMPLATES["encadrement_loyer"]
    values = {"nom": "Camille Martin", "bailleur": "M. Durand", "date_bail": "2025-09-01", "loyer": 200_000,
              "surface": "60", "plafond_m2": "28,90", "categorie": "Sainte-Marguerite, 2 pièces (arrêté 2025)",
              "ecart_mensuel": 26_600}  # fmt: skip
    letter = clean(dp.render_letter(tpl, {k: {"value": v} for k, v in values.items()}, TODAY) or "")
    assert "bail prenant effet le 1er septembre 2025" in letter or "bail prenant effet le 1 septembre 2025" in letter
    assert "2 000,00 € hors charges pour 60 m²" in letter and "28,90 €/m²" in letter and "(fiche F1314)" in letter
    assert "soit 266,00 € de moins par mois" in letter and "commission départementale de conciliation" in letter
    assert all(s.url is None or s.url.startswith("https://") for s in tpl.sources)


# ------------------------------------------------------------------ taxe foncière
def test_local_tax_rates_are_read_for_the_commune_only() -> None:
    toulouse = pt.parse_rates(read("fiscalite_31_extrait.json"), "31555")
    assert sorted(toulouse) == [2021, 2022, 2023, 2024, 2025]
    assert (toulouse[2025].tfb, toulouse[2025].teom, toulouse[2024].tfb) == (49.67, 8.1, 49.65)
    paris = pt.parse_rates(read("fiscalite_75.json"), "75056")
    assert (paris[2022].tfb, paris[2023].tfb, paris[2023].teom) == (14.08, 21.15, 6.21)
    assert (
        pt.tax_commune("75111") == "75056" and pt.tax_commune("69381") == "69123" and pt.tax_commune("13201") == "13055"
    )
    assert pt.department("31555") == "31" and pt.department("2A004") == "2A" and pt.department("97411") == "974"
    url = pt.rates_url("31")
    assert parse_qs(url.split("?", 1)[1])["where"] == ['dep="31"'], "seul le département part"
    with pytest.raises(pt.PropertyTaxError, match="autre département"):
        pt.parse_rates(read("fiscalite_75.json"), "31555")
    with pytest.raises(pt.PropertyTaxError, match="hors bornes"):
        pt.parse_rates(read("fiscalite_31_extrait.json").replace(b"49.67", b"149.67"), "31555")


def test_the_forecast_applies_published_rates_and_says_what_is_unknown() -> None:
    paris = pt.parse_rates(read("fiscalite_75.json"), "75056")
    # Avis 2022 de 1 000 €, dont 120 € de TEOM ; taux 2023 publiés : 14,08 % → 21,15 %, TEOM inchangée.
    base = pt.Known(2022, 100_000, "avis", 12_000, date(2022, 10, 17), ref="doc:3")
    f = pt.forecast(base, paris, date(2023, 3, 1))
    assert f.target == 2023 and f.unknown_rates == () and f.due == date(2023, 10, 17)
    assert f.amount_cents == 144_188, "880 € × 21,15 / 14,08 + 120 €"
    link = corr.HousingLink(tax=f)
    (finding,), _ = corr.housing_checks(corr.Inputs(today=date(2023, 3, 1), housing=link))
    body = clean(finding.body)
    assert (
        finding.kind == "property_tax_forecast"
        and clean(finding.title) == "Taxe foncière 2023 : 1 441,88 € à bases inchangées (+44,2 %)"
    )
    assert "taux 2023 votés : taxe foncière 14,08 % → 21,15 %, TEOM 6,21 % → 6,21 %" in body
    assert "Bank ne la chiffre pas" in body and "dont 120,00 € de TEOM" in body
    assert_wording(finding.title, finding.body)
    # Aujourd'hui : l'avis 2026 est arrivé, les taux 2026 et 2027 ne sont pas publiés (dernier exercice : 2025).
    recent = pt.Known(2026, 150_000, "avis", None, date(2026, 10, 15))
    f = pt.forecast(recent, paris, TODAY)
    assert (f.target, f.unknown_rates, f.latest_rates, f.amount_cents) == (2027, (2027,), 2025, 150_000)
    (finding,), _ = corr.housing_checks(corr.Inputs(today=TODAY, housing=corr.HousingLink(tax=f)))
    assert "Taux 2027 pas encore publiés (dernier exercice publié : 2025) : comptés inchangés" in clean(finding.body)
    # Sans la part TEOM, deux taux qui bougent différemment : pas de chiffre inventé.
    f = pt.forecast(pt.Known(2022, 100_000, "avis"), paris, date(2023, 3, 1))
    assert f.amount_cents is None and any("TEOM" in n for n in f.notes)


def test_payments_tell_the_last_complete_year_and_monthly_plans() -> None:
    once = [(date(2025, 10, 15), 98_000), (date(2026, 10, 15), 101_000)]
    k = pt.from_payments(once, TODAY)
    assert k is not None and (k.year, k.amount_cents, k.due, k.monthly) == (2026, 101_000, date(2026, 10, 15), False)
    monthly = [(date(2026, m, 15), 10_000) for m in range(1, 11)] + [(date(2025, m, 15), 9_800) for m in range(1, 11)]
    k = pt.from_payments(monthly, TODAY)
    assert k is not None and (k.year, k.amount_cents, k.monthly) == (2025, 98_000, True), "2026 pas fini"
    # Mensualisée depuis janvier, 4 prélèvements au 10/04 : l'année n'est pas un paiement unique.
    started = [(date(2025, 10, 15), 120_000)] + [(date(2026, m, 15), 12_000) for m in range(1, 5)]
    k = pt.from_payments(started, date(2026, 4, 10))
    assert k is not None and (k.year, k.amount_cents) == (2025, 120_000)
    # Premier prélèvement de l'année quand l'an passé était mensualisé : pas un paiement unique non plus.
    first = [(date(2025, m, 15), 10_000) for m in range(1, 11)] + [(date(2026, 1, 15), 10_500)]
    k = pt.from_payments(first, date(2026, 2, 1))
    assert k is not None and k.year == 2025


def test_a_past_due_date_is_never_announced_as_the_coming_one() -> None:
    """Le 24/10/2026, seul l'avis 2025 (échéance 15/10/2025) est connu et le paiement 2026 n'a pas été vu : la
    prévision porte sur 2027, jamais sur une échéance 2026 déjà passée."""
    base = pt.Known(2025, 120_000, "avis", None, date(2025, 10, 15))
    f = pt.forecast(base, {}, TODAY)
    assert (f.target, f.due, f.unknown_rates) == (2027, date(2027, 10, 15), (2026, 2027))
    assert pt.forecast(base, {}, date(2026, 10, 1)).due == date(2026, 10, 15), "échéance encore à venir"


def test_the_property_tax_notice_is_read_in_papers() -> None:
    notice = (
        "DIRECTION GÉNÉRALE DES FINANCES PUBLIQUES\nAVIS D'IMPÔT 2026 TAXES FONCIÈRES\n"
        "Montant de votre impôt 2026 : 1 234 €\nDate limite de paiement : 15/10/2026\n"
        "Taxe foncière sur les propriétés bâties 1 102 €\nTaxe d'enlèvement des ordures ménagères 132 €\n"
    )
    assert dv.classify(notice)[0] == "property_tax"
    found = {f.key: f.value for f in dv._apply_rules("property_tax", dv.lines_of(notice))}
    assert found == {"annee": 2026, "montant": 123_400, "teom": 13_200, "date_limite": "2026-10-15"}
    assert "property_tax" in dv.KINDS and dv.KINDS["property_tax"].retention.years == 1


def test_the_forecast_replaces_the_detected_spike_in_the_year_ahead() -> None:
    series = RecurringSeries("s1", "DGFIP IMPOT TAXE", "out", "yearly", 365.0, -98_000, -98_000, -95_000, False, 2,
                             date(2026, 10, 15), date(2027, 10, 15), 15, 0.9, "impots", False)  # fmt: skip
    spike = ya.ForecastSpike("housing:taxe_fonciere:2027", "Taxe foncière 2027", 101_000, date(2027, 10, 15),
                             frozenset({"DGFIP IMPOT TAXE"}), "Taxe foncière 2027 : 1010 €")  # fmt: skip
    y = ya.build_year_ahead(today=TODAY, start_balance_cents=0, series=[series], categories={}, txns=[],
                            forecasts=[spike])  # fmt: skip
    assert [(s.label, s.source, s.amount_cents) for s in y.spikes] == [("Taxe foncière 2027", "forecast", 101_000)]
    assert "Taxe foncière 2027 : 1010 €" in y.notes


# ------------------------------------------------------------------ bout en bout
@pytest.fixture
def paris_net(app, auth_client: TestClient) -> Iterator[tuple[TestClient, FakeNet]]:  # type: ignore[no-untyped-def]
    fake = FakeNet(geocode="geocodage_paris.json")
    for name in ("territory", "housing"):
        setattr(app.state, f"{name}_http", httpx.Client(transport=httpx.MockTransport(fake)))
        setattr(app.state, f"{name}_resolver", lambda host: ["93.184.216.34"])
    app.dependency_overrides[deps.today] = lambda: TODAY
    yield auth_client, fake
    app.dependency_overrides.clear()


def _paris_home(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        home_value.save_profile(db, actor="test", kind="appartement", surface=60, code="75111", commune="Paris",
                                section=None, rooms=2, in_wealth=False)  # fmt: skip
        doc = VaultDocument(kind="lease", title="Bail", filename="bail.pdf", mime="application/pdf", size=10,
                            sha256="e" * 64, status="verified", source="upload", method="pdf_text", confidence=0.9,
                            document_date=date(2025, 9, 1))  # fmt: skip
        db.add(doc)
        db.flush()
        fields = {"loyer": 200_000, "date_debut": "2025-09-01", "meuble": "non", "bailleur": "M. Durand"}
        db.add_all(
            [VaultField(document_id=doc.id, key=k, value=json.dumps(v), confidence=0.9) for k, v in fields.items()]
        )
        db.commit()


def test_nothing_new_leaves_without_consent(paris_net: tuple[TestClient, FakeNet]) -> None:
    client, fake = paris_net
    for path in ("/api/housing/control/refresh", "/api/housing/taxes/refresh", "/api/housing/risks/refresh"):
        r = client.post(path)
        assert r.status_code == 409, (path, r.text)
    assert fake.requests == []


def test_paris_rent_control_end_to_end(app, paris_net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = paris_net
    _paris_home(app)
    for purpose in ("housing", "geocoding"):
        assert client.put(f"/api/consents/{purpose}", json={"granted": True}).status_code == 200
    assert client.post("/api/territory/geocode", json={"query": "5 rue Faidherbe Paris"}).status_code == 200
    fake.requests.clear()

    r = client.post("/api/housing/control/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["message"] == "Loyers de référence lus pour le quartier Sainte-Marguerite."
    hosts = [q.headers["host"] for q in fake.requests]
    assert hosts == ["www.service-public.gouv.fr"] + ["opendata.paris.fr"] * 3
    for q in fake.requests:
        raw = str(q.url)
        assert "Faidherbe" not in raw and "2.384" not in raw and "48.85" not in raw, "rien de ton adresse ne part"
    view = r.json()["view"]
    assert (
        view["control"]["territory"]["name"] == "Paris" and view["control"]["paris"]["quartier"] == "Sainte-Marguerite"
    )
    assert view["control"]["page"] == {"verified": "2026-08-01", "missing": [], "changed": False}
    # L'époque de construction manque : demandée, jamais devinée.
    assert not [f for f in view["findings"] if f["kind"].startswith("rent_control")]
    assert any("l'époque de construction" in n for n in view["notes"])
    assert client.post("/api/housing/details", json={"epoque": "Avant 1900"}).status_code == 422
    view = client.post("/api/housing/details", json={"epoque": "1946-1970", "rooms": 2}).json()
    above = next(f for f in view["findings"] if f["kind"] == "rent_control_above")
    assert above["yearly_cents"] == 319_200 and above["level"] == "warning"
    assert_wording(above["title"], above["body"], *view["notes"])

    # La lettre au bailleur, préparée depuis le constat.
    r = client.post("/api/correlations/start", json={"key": above["key"]})
    assert r.status_code == 201, r.text
    with app.state.sessionmaker() as db:
        proc = db.get(Procedure, r.json()["procedure_id"])
        assert proc is not None and proc.template == "encadrement_loyer"
        fields = {k: v["value"] for k, v in json.loads(proc.data)["fields"].items()}
        assert fields["ecart_mensuel"] == 26_600 and fields["plafond_m2"] == "28,90"

    # Même jeu publié : la fiche est relue, les fichiers ne sont pas retéléchargés.
    fake.requests.clear()
    assert client.post("/api/housing/control/refresh").status_code == 200
    assert [q.url.path.rsplit("/", 1)[-1] for q in fake.requests] == ["F1314", "logement-encadrement-des-loyers"]

    # Chiffré au repos ; export ; effacer l'adresse efface le quartier.
    with app.state.sessionmaker() as db:
        raw = " ".join(db.execute(text("SELECT coalesce(data, '') FROM housing_sources")).scalars())
        assert "Sainte-Marguerite" not in raw and "1946-1970" not in raw
    exported = client.post("/api/export", json={"password": PASSWORD}).json()
    keys = {h["key"] for h in exported["housing"]}
    assert {"rentctl", "paris", "details"} <= keys
    assert client.delete("/api/territory/place").status_code == 200
    with app.state.sessionmaker() as db:
        assert db.get(HousingSource, "paris") is None and db.get(HousingSource, "details") is not None


def test_property_tax_rates_forecast_and_year_ahead_end_to_end(app, paris_net: tuple[TestClient, FakeNet]) -> None:  # type: ignore[no-untyped-def]
    client, fake = paris_net
    with app.state.sessionmaker() as db:
        seed_household(db, today=TODAY)
        account = db.scalars(select(Account).where(Account.kind == "checking")).first()
        assert account is not None
        paid = [ParsedTransaction(date(y, 10, 15), -amount, "PRLV SEPA DGFIP IMPOT TAXE FONCIERE ECH")
                for y, amount in ((2024, 95_000), (2025, 98_000), (2026, 101_000))]  # fmt: skip
        ingest(db, account, paid, provider="file")
        db.commit()
    _paris_home(app)
    for purpose in ("housing", "geocoding"):
        client.put(f"/api/consents/{purpose}", json={"granted": True})
    client.post("/api/territory/geocode", json={"query": "5 rue Faidherbe Paris"})
    fake.requests.clear()
    r = client.post("/api/housing/taxes/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["message"] == "Taux 2025 lus pour Ville De Paris."
    (q,) = fake.requests
    assert q.headers["host"] == "data.economie.gouv.fr" and parse_qs(q.url.query.decode())["where"] == ['dep="75"']
    taxes = r.json()["view"]["taxes"]
    assert taxes["known"] == {"year": 2026, "amount_cents": 101_000, "teom_cents": None, "source": "prelevements",
                              "monthly": False}  # fmt: skip
    assert taxes["forecast"] == {"target": 2027, "amount_cents": 101_000, "due": "2027-10-15", "unknown_rates": [2027]}
    finding = next(f for f in r.json()["view"]["findings"] if f["kind"] == "property_tax_forecast")
    assert "Taux 2027 pas encore publiés" in clean(finding["body"])
    # L'année qui vient : la prévision remplace le pic détecté du prélèvement.
    with app.state.sessionmaker() as db:
        year = ya_service.compute(db, TODAY)
    tf = [s for s in year.spikes if "FONCI" in s.label.upper() or "TAXE" in s.label.upper()]
    assert [(s.label, s.source, s.day) for s in tf] == [("Taxe foncière 2027", "forecast", date(2027, 10, 15))]
    assert any("Taxe foncière 2027 : 1010 €" in n for n in year.notes)


def test_georisques_unreachable_is_said_and_the_official_page_is_linked(
    app: FastAPI, paris_net: tuple[TestClient, FakeNet]
) -> None:
    client, fake = paris_net
    client.put("/api/consents/housing", json={"granted": True})
    r = client.post("/api/housing/risks/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "failed" and "injoignable" in r.json()["result"]["message"]
    risks = r.json()["view"]["risks"]
    assert risks["reachable"] is False and risks["page"] == "https://www.georisques.gouv.fr/" and risks["last_error"]
    (q,) = fake.requests
    assert q.headers["host"] == "www.georisques.gouv.fr" and not q.url.query, "rien de toi ne part"
    fake.georisques_up = True
    r = client.post("/api/housing/risks/refresh")
    assert r.json()["view"]["risks"]["reachable"] is True and "n'en tire rien" in r.json()["result"]["message"]
    # La tâche de fond ressonde une source injoignable au bout d'une semaine, pas chaque jour.
    fake.georisques_up = False
    fetch = svc.default_fetch(http=app.state.housing_http, resolver=app.state.housing_resolver)
    now = utcnow() + timedelta(days=31)
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=now, today=TODAY, fetch=fetch).get("housing_risks_failed") == 1
        db.commit()
        assert "housing_risks_failed" not in svc.tick(db, now=now + timedelta(days=2), today=TODAY, fetch=fetch)
