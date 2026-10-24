"""Barèmes tenus à jour : lecture des réponses réelles (CRE, Caisse des Dépôts, INSEE, OpenFisca, relevées le 24
octobre 2026 et réduites), différence datée avec rates.json (une source en retard ne fait jamais reculer un taux),
couche au-dessus du fichier (un fichier plus récent reprend la main), écart d'abonnement 6 et 9 kVA corrigé par ce
mécanisme, entrée dans la Veille et au journal, relève ratée qui garde les valeurs, retour au fichier, accord."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api import deps
from app.domain import baremes as dom
from app.domain import contracts, rates
from app.models import AuditLog, RateObservation, WatchSource
from app.security import netfetch
from app.services import baremes as svc
from app.services import consents, energy
from app.services import watch as watch_svc

FIXTURES = Path(__file__).parent / "fixtures" / "baremes"
TODAY = date(2026, 10, 24)
NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_overlay() -> Iterator[None]:
    rates.install({})
    yield
    rates.install({})
    contracts.install_irl({}, None)


def read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def all_observations(today: date = TODAY) -> list[dom.Observation]:
    obs: list[dom.Observation] = []
    for option in dom.CRE_FILES:
        obs += dom.parse_cre(read(dom.CRE_FILES[option]), dom.cre_url(option), today, option)
    obs += dom.parse_cdc(read("cdc_flux_et_taux.json"), dom.CDC_URL, today)
    for idbank in dom.INSEE_SERIES:
        obs += dom.parse_insee(read(f"insee_{idbank}.xml"), dom.INSEE_URL.format(idbank=idbank), today)
    obs += dom.parse_openfisca_values(read("openfisca_smic_b_horaire.json"), "u", today, "openfisca.smic_horaire")
    obs += dom.parse_openfisca_scale(read("openfisca_bareme_ir.json"), "u", today)
    return obs


# ------------------------------------------------------------------ lecture des sources
def test_parsers_read_the_real_responses() -> None:
    obs = dom.latest(all_observations())
    assert obs["cre.base.6.fixe_ttc"].value == "190.81" and obs["cre.base.6.fixe_ttc"].period == "2026-08-01"
    assert obs["cre.base.9.fixe_ttc"].value == "239.84" and obs["cre.base.6.kwh_ttc"].value == "0.2001"
    assert obs["cre.hphc.9.fixe_ttc"].value == "239.8378787" and obs["cre.hphc.6.hp_ttc"].value == "0.2142"
    assert obs["cre.tempo.6.rouge_hp_ttc"].value == "0.7295" and obs["cre.tempo.9.bleu_hc_ttc"].value == "0.1356"
    assert obs["cre.base.6.fixe_ttc"].source_url.endswith("/Option_Base.csv")
    assert (obs["cdc.livret_a"].period, obs["cdc.livret_a"].value) == ("2026-03", "1.50")
    assert obs["cdc.ldds"].value == "1.50" and obs["cdc.lep"].value == "2.50"
    assert (obs["insee.ipc"].period, obs["insee.ipc"].value) == ("2026-09", "103.07")
    assert (obs["insee.ipc_glissement"].value, obs["insee.irl"].period, obs["insee.irl"].value) == (
        "3.1",
        "2026-Q3",
        "148.06",
    )
    smic = obs["openfisca.smic_horaire"]
    assert (smic.period, smic.value) == ("2026-06-01", "12.31") and "22 mai 2026" in (smic.note or "")
    scale = obs["openfisca.bareme_ir"]
    assert scale.period == "2025-01-01" and "Loi 2026-103" in (scale.note or "")
    assert json.loads(scale.value)[1] == ["11600", "0.11"]


def test_a_grid_announced_for_later_is_not_read_yet() -> None:
    """Le 15 juillet 2026, la grille du 1er août n'est pas en vigueur : c'est celle du 1er février qui compte."""
    obs = dom.latest(dom.parse_cre(read("Option_Base.csv"), "u", date(2026, 7, 15), "base"))
    assert obs["cre.base.6.fixe_ttc"].period == "2026-02-01" and obs["cre.base.6.fixe_ttc"].value == "188.2"
    with pytest.raises(dom.BaremeError, match="aucune grille"):
        dom.parse_cre(read("Option_Base.csv"), "u", date(2025, 1, 1), "base")


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: dom.parse_cre(b"DATE_DEBUT;P_SOUSCRITE\n01/08/2026;6\n", "u", TODAY, "base"), "colonnes absentes"),
        (
            lambda: dom.parse_cre(
                b"DATE_DEBUT;DATE_FIN;P_SOUSCRITE;PART_FIXE_TTC;PART_VARIABLE_TTC\n01/08/2026;;6;nan;0,2\n",
                "u",
                TODAY,
                "base",
            ),
            "illisible",
        ),
        (lambda: dom.parse_cdc(b"<html>", "u", TODAY), "JSON attendu"),
        (lambda: dom.parse_cdc(b'{"results": [{"date": "2026-03", "tla_tldds_percent": 95}]}', "u", TODAY), "bornes"),
        (lambda: dom.parse_insee(b"pas du xml", "u", TODAY), "XML attendu"),
        (
            lambda: dom.parse_insee(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>', "u", TODAY),
            "XML attendu",
        ),
        (lambda: dom.parse_openfisca_values(b"[1, 2]", "u", TODAY, "s"), "inattendue"),
        (lambda: dom.parse_openfisca_scale(b'{"brackets": {"2025-01-01": {"0": 0, "10": 1.5}}}', "u", TODAY), "bornes"),
    ],
)
def test_parsers_are_defensive(call, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(dom.BaremeError, match=message):
        call()


def test_float_noise_impossible_dates_and_hostile_bodies_are_handled() -> None:
    """Le Livret A de janvier 2026 arrive en 1.7000000000000002 dans le JSON de la Caisse des Dépôts : c'est 1,70 %.
    Une date impossible, un JSON trop imbriqué, un champ CSV démesuré : une erreur lisible, jamais une panne."""
    from decimal import Decimal

    assert dom.decimal_of(1.7000000000000002) == Decimal("1.7")
    row = {"date": "2026-09", "tla_tldds_percent": 1.7000000000000002, "tlep_percent": 2.7}
    obs = dom.latest(dom.parse_cdc(json.dumps({"results": [row]}).encode(), "u", TODAY))
    assert obs["cdc.livret_a"].value == "1.70" and obs["cdc.lep"].value == "2.70"
    with pytest.raises(dom.BaremeError, match="Date illisible"):
        dom.parse_cdc(b'{"results": [{"date": "2026-13", "tla_tldds_percent": 1.5, "tlep_percent": 2.5}]}', "u", TODAY)
    with pytest.raises(dom.BaremeError, match="JSON attendu"):
        dom.parse_openfisca_scale(b"[" * 100_000 + b"]" * 100_000, "u", TODAY)
    with pytest.raises(dom.BaremeError, match="Date illisible"):
        dom.parse_openfisca_values(b'{"values": {"2026-02-30": 12.0}}', "u", TODAY, "s")
    huge = b"DATE_DEBUT;P_SOUSCRITE;PART_FIXE_TTC;PART_VARIABLE_TTC\n01/08/2026;6;" + b"1" * 200_000 + b";0,2\n"
    with pytest.raises(dom.BaremeError, match="CSV attendu"):
        dom.parse_cre(huge, "u", TODAY, "base")


# ------------------------------------------------------------------ différence datée
def test_proposal_fixes_the_subscription_gap_and_nothing_else() -> None:
    prop = dom.propose(rates.static(), all_observations(), TODAY)
    shown = {c.path: (c.old, c.new, c.shown_old, c.shown_new) for c in prop.changes if c.label}
    assert shown == {
        "electricity_trv/options:base_6/yearly_subscription_cents": (
            19035,
            19081,
            "190,35 € par an",
            "190,81 € par an",
        ),
        "electricity_trv/options:base_9/yearly_subscription_cents": (
            23860,
            23984,
            "238,60 € par an",
            "239,84 € par an",
        ),
        "electricity_trv/options:hphc_6/yearly_subscription_cents": (
            19035,
            19081,
            "190,35 € par an",
            "190,81 € par an",
        ),
        "electricity_trv/options:hphc_9/yearly_subscription_cents": (
            23860,
            23984,
            "238,60 € par an",
            "239,84 € par an",
        ),
    }, "les prix du kWh concordent (Heures creuses : 65 % × 21,42 + 35 % × 15,89 = 19,48 c€)"
    meta = [c.path for c in prop.changes if not c.label]
    assert meta == ["electricity_trv/source"], "même grille (01/08/2026) : seule la source change, pas la date"
    assert all(c.effective_from == "2026-08-01" for c in prop.changes)
    assert any("Caisse des Dépôts : dernier mois publié 03/2026" in n and "rien de changé" in n for n in prop.notes), (
        "le jeu de la Caisse des Dépôts a des mois de retard : 1,50 % de mars ne remplace pas 1,70 % d'août"
    )


def test_newer_sources_propose_dated_and_sourced_values() -> None:
    newer = [
        dom.Observation("cdc.livret_a", "2027-02", "2.00", dom.CDC_URL),
        dom.Observation("cdc.ldds", "2027-02", "2.00", dom.CDC_URL),
        dom.Observation("cdc.lep", "2027-02", "2.50", dom.CDC_URL),
        dom.Observation(
            "openfisca.bareme_ir",
            "2026-01-01",
            json.dumps([["0", "0"], ["11700", "0.11"], ["29800", "0.3"], ["85300", "0.41"], ["183400", "0.45"]]),
            "u",
            "Loi de finances pour 2027",
        ),
    ]
    prop = dom.propose(rates.static(), newer, date(2027, 3, 1))
    by = {c.path: c for c in prop.changes}
    assert (by["products:livret_a/rate_percent"].old, by["products:livret_a/rate_percent"].new) == ("1.70", "2.00")
    assert by["products:livret_a/rate_percent"].shown_new == "2,00 %" and "products:lep/rate_percent" not in by
    assert by["as_of"].new == "2027-02-01" and "Caisse des Dépôts" in by["source"].new
    tax = by["income_tax/brackets"]
    assert tax.new[1] == {"up_to_cents": 2980000, "rate_percent": 11} and tax.new[-1]["up_to_cents"] is None
    assert tax.shown_new.startswith("11 % au-delà de 11 700 €")
    assert (by["income_tax/incomes_year"].new, by["income_tax/year"].new) == (2026, 2027)
    assert "Loi de finances pour 2027" in by["income_tax/source"].new
    # Un taux non entier n'est pas arrondi en silence.
    odd = [dom.Observation("openfisca.bareme_ir", "2026-01-01", json.dumps([["0", "0"], ["11700", "0.105"]]), "u")]
    assert dom.propose(rates.static(), odd, date(2027, 3, 1)).changes == []


def test_a_livret_rate_is_dated_from_the_month_it_changed() -> None:
    """La Caisse des Dépôts publie janvier 1,70 %, février et mars 1,50 % : le taux vaut 1,50 % depuis février (pas
    depuis mars, le dernier mois publié) ; un nouveau mois au même taux ne déplace rien."""
    raw = rates.static()
    raw["as_of"] = "2026-01-01"
    by = {c.path: c for c in dom.propose(raw, all_observations(), TODAY).changes if c.section == "livrets"}
    assert by["products:livret_a/rate_percent"].effective_from == "2026-02-01"
    assert by["as_of"].new == "2026-02-01" and "02/2026" in by["source"].new
    assert "products:lep/rate_percent" not in by, "LEP : 2,50 % comme dans le fichier"
    for p in raw["products"]:
        p["rate_percent"] = "1.50" if p["kind"] != "lep" else "2.50"
    raw["as_of"] = "2026-02-01"
    same = [c for c in dom.propose(raw, all_observations(), TODAY).changes if c.section == "livrets"]
    assert same == [], "mars au même taux que février : ni taux, ni date, ni source ne changent"


def test_each_electricity_option_is_dated_by_its_own_grid() -> None:
    """Base est publiée avec la grille de février 2027, le fichier Heures creuses est encore celui de février 2026 :
    Base est appliquée et date la section, Heures creuses est gardée et c'est dit."""
    from dataclasses import replace

    base = [
        replace(o, period="2027-02-01", value="200" if o.series == "cre.base.6.fixe_ttc" else o.value)
        for o in dom.parse_cre(read("Option_Base.csv"), dom.cre_url("base"), TODAY, "base")
    ]
    hphc = dom.parse_cre(read("Option_HPHC.csv"), dom.cre_url("hphc"), date(2026, 7, 15), "hphc")
    prop = dom.propose(rates.static(), base + hphc, date(2027, 3, 1))
    paths = {c.path: c for c in prop.changes if c.section == "electricity"}
    assert paths["electricity_trv/options:base_6/yearly_subscription_cents"].new == 20000
    assert not any("hphc" in p for p in paths), "grille Heures creuses plus ancienne que celle en vigueur"
    assert paths["electricity_trv/as_of"].new == "2027-02-01" and "01/02/2027" in paths["electricity_trv/source"].new
    assert any("Heures pleines / creuses 6 kVA" in n and "01/02/2026" in n for n in prop.notes)


def test_a_new_income_year_with_the_same_scale_has_its_own_entry() -> None:
    scale = dom.latest(all_observations())["openfisca.bareme_ir"]
    newer = replace_obs(scale, period="2026-01-01")
    by = {c.path: c for c in dom.propose(rates.static(), [newer], date(2027, 3, 1)).changes}
    assert "income_tax/brackets" not in by
    year = by["income_tax/incomes_year"]
    assert year.label and (year.shown_old, year.shown_new) == ("revenus 2025", "revenus 2026")


def replace_obs(o: dom.Observation, **kw: Any) -> dom.Observation:
    from dataclasses import replace

    return replace(o, **kw)


def test_overlay_never_beats_a_newer_file_and_ignores_unknown_paths() -> None:
    layer = {
        "electricity_trv/options:base_6/yearly_subscription_cents": (19081, "2026-08-01"),
        "electricity_trv/options:base_99/yearly_subscription_cents": (1, "2026-08-01"),
        "products:livret_a/rate_percent": ("1.50", "2026-03-01"),
    }
    raw = rates.static()
    out = rates.apply(raw, layer)
    assert rates.get(out, "electricity_trv/options:base_6/yearly_subscription_cents") == 19081
    assert rates.get(out, "products:livret_a/rate_percent") == "1.70", "relevé plus ancien que le fichier : ignoré"
    assert raw == rates.static(), "le fichier lui-même n'est jamais modifié"
    raw["electricity_trv"]["as_of"] = "2027-02-01"  # une nouvelle version de Bank livre une grille plus récente
    assert rates.get(rates.apply(raw, layer), "electricity_trv/options:base_6/yearly_subscription_cents") == 19035


# ------------------------------------------------------------------ réseau simulé
class Chunks(httpx.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self) -> Iterator[bytes]:
        for i in range(0, len(self.data), 4_096):
            yield self.data[i : i + 4_096]


OPENFISCA_FILES = {
    "marche_travail.salaire_minimum.smic.smic_b_horaire": "openfisca_smic_b_horaire.json",
    "marche_travail.salaire_minimum.smic.smic_b_mensuel": "openfisca_smic_b_mensuel.json",
    "impot_revenu.bareme_ir_depuis_1945.bareme": "openfisca_bareme_ir.json",
    "prestations_sociales.prestations_familiales.bmaf.bmaf": "openfisca_bmaf.json",
}


class FakeSources:
    """CRE, Caisse des Dépôts, INSEE et OpenFisca simulés avec leurs vraies réponses. L'hôte se lit dans l'en-tête
    Host : netfetch épingle la connexion sur l'adresse résolue."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.gone: set[str] = set()  # fichiers qui répondent 404

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        if host in self.down or "*" in self.down:
            return httpx.Response(503, stream=Chunks(b""))
        name = request.url.path.rsplit("/", 1)[-1]
        if name in self.gone:
            return httpx.Response(404, stream=Chunks(b""))
        if host == "www.cre.fr":
            body = read(name)
        elif host == "opendata.caissedesdepots.fr":
            body = read("cdc_flux_et_taux.json")
        elif host == "api.insee.fr":
            body = read(f"insee_{name}.xml")
        else:
            assert host == "api.fr.openfisca.org", host
            body = read(OPENFISCA_FILES[name])
        return httpx.Response(200, stream=Chunks(body), headers={"content-type": "text/plain"})

    def hosts(self) -> set[str]:
        return {r.headers["host"] for r in self.requests}


@pytest.fixture
def net(app, auth_client: TestClient) -> Iterator[tuple[TestClient, FakeSources]]:  # type: ignore[no-untyped-def]
    fake = FakeSources()
    app.state.baremes_http = httpx.Client(transport=httpx.MockTransport(fake))
    app.state.baremes_resolver = lambda host: ["93.184.216.34"]
    app.dependency_overrides[deps.today] = lambda: TODAY
    yield auth_client, fake
    app.dependency_overrides.clear()


def _agree(client: TestClient) -> None:
    assert client.put(f"/api/consents/{svc.CONSENT}", json={"granted": True}).status_code == 200


def _options(client: TestClient) -> dict[str, dict[str, object]]:
    return {o["id"]: o for o in client.get("/api/energy").json()["tariff"]["options"]}


# ------------------------------------------------------------------ bout en bout
def test_nothing_leaves_without_consent(net: tuple[TestClient, FakeSources]) -> None:
    client, fake = net
    view = client.get("/api/baremes").json()
    assert view["consent"] is False and view["updates"] == [] and view["indices"] == []
    assert [s["slug"] for s in view["sources"]] == ["bareme-cre", "bareme-cdc", "bareme-insee", "bareme-openfisca"]
    r = client.post("/api/baremes/refresh")
    assert r.status_code == 409 and "Barèmes tenus à jour" in r.json()["detail"]
    assert fake.requests == []


def test_monthly_check_fixes_the_6_and_9_kva_subscriptions(app, net: tuple[TestClient, FakeSources]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    before = _options(client)
    assert (
        before["base_6"]["yearly_subscription_cents"] == 19035
        and before["base_9"]["yearly_subscription_cents"] == 23860
    )
    _agree(client)
    r = client.post("/api/baremes/refresh")
    assert r.status_code == 200, r.text
    result, view = r.json()["result"], r.json()["view"]
    assert result["status"] == "checked" and result["checked"] == 4 and result["errors"] == 0
    assert len(result["changes"]) == 4 and result["message"].startswith("4 valeurs mises à jour")
    assert fake.hosts() == {"www.cre.fr", "opendata.caissedesdepots.fr", "api.insee.fr", "api.fr.openfisca.org"}
    assert all(not r.url.query or b"idbank" not in r.url.query for r in fake.requests), "rien te concernant ne part"

    after = _options(client)
    assert (
        after["base_6"]["yearly_subscription_cents"] == 19081 and after["hphc_6"]["yearly_subscription_cents"] == 19081
    )
    assert (
        after["base_9"]["yearly_subscription_cents"] == 23984 and after["hphc_9"]["yearly_subscription_cents"] == 23984
    )
    assert after["base_6"]["cents_per_100kwh"] == 2001 and after["hphc_9"]["cents_per_100kwh"] == 1948
    assert (
        str(after["base_6"]["source"]).startswith("CRE, tarifs réglementés")
        and after["base_6"]["as_of"] == "2026-08-01"
    )
    with app.state.sessionmaker() as db:
        assert energy.current_tariff(db).yearly_subscription_cents == 19081, "le tarif par défaut suit la grille"
        energy.set_tariff(db, cents_per_100kwh=1900, subscription_cents_per_year=20000, label="Mon contrat")
        assert energy.current_tariff(db).yearly_subscription_cents == 20000, "ton contrat à toi ne bouge pas"
        stored = db.scalar(select(RateObservation).where(RateObservation.series == "cre.base.9.fixe_ttc"))
        assert stored is not None and stored.value == "239.84" and stored.source_url.endswith("Option_Base.csv")
        assert stored.fetched_at is not None and stored.period == "2026-08-01"
        assert [a.action for a in db.scalars(select(AuditLog).where(AuditLog.action.like("rates.%")))] == [
            "rates.update"
        ]

    # Rien ne change en silence : la Veille le dit, avec l'ancienne et la nouvelle valeur, et la source.
    items = [i for i in client.get("/api/watch").json()["items"] if i["title"].startswith("Barèmes mis à jour")]
    assert len(items) == 1 and items[0]["title"] == "Barèmes mis à jour : tarif réglementé de l'électricité"
    assert "Abonnement Base 6 kVA : 190,35 € par an → 190,81 € par an" in items[0]["summary"]
    assert "Source : CRE" in items[0]["summary"] and items[0]["url"].endswith("Option_Base.csv")

    # Réglages › Barèmes : ce qui est en vigueur, d'où ça vient, ce qui a été changé, les indices relevés.
    groups = {g["key"]: g for g in view["in_effect"]}
    assert groups["electricity"]["updated"] is True and groups["livrets"]["updated"] is False
    assert {"label": "Livret A", "value": "1,70 %"} in groups["livrets"]["items"]
    assert groups["income_tax"]["items"][0]["value"].startswith("11 % au-delà de 11 600 €")
    assert [u["state"] for u in view["updates"]] == ["active"] * 4
    indices = {i["key"]: i for i in view["indices"]}
    assert indices["insee.ipc"]["value"] == "103,07" and indices["insee.ipc"]["period_label"] == "09/2026"
    assert indices["insee.irl"]["value"] == "148,06" and indices["insee.irl"]["period_label"] == "3e trimestre 2026"
    assert (
        indices["insee.ipc_glissement"]["value"] == "3,1 %" and indices["openfisca.smic_horaire"]["value"] == "12,31 €"
    )
    assert indices["openfisca.smic_mensuel"]["value"] == "1 867,02 €"
    assert view["irl"] == {"checked": 3, "gaps": [], "missing": [], "applied": True}, "concorde avec l'INSEE"
    assert contracts.irl_source((2026, 3)).startswith("INSEE, série 001515333"), "ton loyer : l'IRL relu (#32)"
    assert any("Caisse des Dépôts" in n["text"] for n in view["notes"])

    # Une seconde vérification ne refait rien.
    again = client.post("/api/baremes/refresh").json()["result"]
    assert again["changes"] == [] and again["message"] == "Tout est à jour : aucune valeur n'a changé."
    assert len([i for i in client.get("/api/watch").json()["items"] if i["title"].startswith("Barèmes")]) == 1


def test_failed_fetch_keeps_the_previous_values_and_says_so(net: tuple[TestClient, FakeSources]) -> None:
    client, fake = net
    _agree(client)
    client.post("/api/baremes/refresh")
    fake.down = {"*"}
    r = client.post("/api/baremes/refresh").json()
    assert r["result"]["status"] == "failed" and r["result"]["errors"] == 4
    assert r["result"]["message"] == "Relève en échec : les valeurs en vigueur restent celles d'avant."
    assert _options(client)["base_6"]["yearly_subscription_cents"] == 19081
    cre = next(s for s in r["view"]["sources"] if s["slug"] == "bareme-cre")
    assert cre["last_error"].startswith("option Base : La source répond 503") and cre["last_ok_at"] is not None
    assert any("les valeurs précédentes restent en vigueur" in n["text"] for n in r["view"]["notes"])
    assert r["view"]["indices"], "les indices déjà relevés restent affichés"


def test_one_failing_file_keeps_the_others_of_its_source(net: tuple[TestClient, FakeSources]) -> None:
    """Le fichier Tempo de la CRE disparaît : Base et Heures creuses sont tout de même appliquées, l'échec est dit et
    la source est relue le lendemain."""
    client, fake = net
    _agree(client)
    fake.gone = {"Option_Tempo.csv"}
    r = client.post("/api/baremes/refresh").json()
    assert r["result"]["status"] == "checked" and r["result"]["errors"] == 1
    assert _options(client)["base_6"]["yearly_subscription_cents"] == 19081
    cre = next(s for s in r["view"]["sources"] if s["slug"] == "bareme-cre")
    assert cre["last_error"].startswith("option Tempo :") and cre["last_ok_at"] is None
    assert any("option Tempo" in n["text"] and "le reste de la source est lu" in n["text"] for n in r["view"]["notes"])


def test_revert_returns_to_the_file_and_is_not_reapplied(net: tuple[TestClient, FakeSources]) -> None:
    client, _fake = net
    _agree(client)
    client.post("/api/baremes/refresh")
    path = "electricity_trv/options:base_6/yearly_subscription_cents"
    view = client.post("/api/baremes/revert", json={"path": path}).json()
    assert {u["path"]: u["state"] for u in view["updates"]}[path] == "reverted"
    opts = _options(client)
    assert opts["base_6"]["yearly_subscription_cents"] == 19035 and opts["base_9"]["yearly_subscription_cents"] == 23984
    again = client.post("/api/baremes/refresh").json()
    assert again["result"]["changes"] == [] and _options(client)["base_6"]["yearly_subscription_cents"] == 19035
    assert any("que tu as écarté" in n["text"] for n in again["view"]["notes"])
    assert client.post("/api/baremes/revert", json={"path": path}).status_code == 404
    # Le dernier chiffre écarté : la section reprend aussi la date et la source du fichier.
    for p in ("base_9", "hphc_6", "hphc_9"):
        client.post("/api/baremes/revert", json={"path": f"electricity_trv/options:{p}/yearly_subscription_cents"})
    assert _options(client)["base_9"]["source"] == rates.static()["electricity_trv"]["source"]


def test_background_tick_runs_monthly_and_retries_a_failure_the_next_day(
    app: Any, net: tuple[TestClient, FakeSources]
) -> None:
    _client, fake = net
    fetch = svc.default_fetch(http=app.state.baremes_http, resolver=app.state.baremes_resolver)
    with app.state.sessionmaker() as db:
        assert svc.tick(db, now=NOW, today=TODAY, fetch=fetch) == {}, "sans accord, rien"
        consents.set_consent(db, svc.CONSENT, True, actor="test")
        db.commit()
        fake.down = {"api.insee.fr"}
        report = svc.tick(db, now=NOW, today=TODAY, fetch=fetch)
        assert report == {"baremes_checked": 4, "baremes_changes": 4, "baremes_errors": 1}
        db.commit()
        n = len(fake.requests)
        assert svc.tick(db, now=NOW + timedelta(hours=2), today=TODAY, fetch=fetch) == {}
        assert len(fake.requests) == n, "l'INSEE en échec n'est pas réinterrogé dans l'heure"
        fake.down = set()
        report = svc.tick(db, now=NOW + timedelta(hours=21), today=TODAY, fetch=fetch)
        assert report["baremes_checked"] == 1 and {r.headers["host"] for r in fake.requests[n:]} == {"api.insee.fr"}
        db.commit()
        assert svc.tick(db, now=NOW + timedelta(days=29), today=TODAY, fetch=fetch) == {}
        late = svc.tick(db, now=NOW + timedelta(days=30, hours=12), today=TODAY, fetch=fetch)
        assert late["baremes_checked"] == 3, "l'INSEE, relu le lendemain, attend son propre mois"


def test_paused_source_is_not_read_and_the_news_watch_skips_them(app, net: tuple[TestClient, FakeSources]) -> None:  # type: ignore[no-untyped-def]
    client, fake = net
    _agree(client)
    client.get("/api/baremes")
    cre = next(s for s in client.get("/api/watch").json()["sources"] if s["name"].startswith("CRE"))
    assert client.patch(f"/api/watch/sources/{cre['id']}", json={"enabled": False}).status_code == 204
    client.post("/api/baremes/refresh")
    assert "www.cre.fr" not in fake.hosts() and _options(client)["base_6"]["yearly_subscription_cents"] == 19035
    seen: list[str] = []

    def fetcher(url: str, etag: str | None, last_modified: str | None) -> object:
        seen.append(url)
        raise netfetch.FetchError("hors ligne")

    with app.state.sessionmaker() as db:
        watch_svc.refresh(db, now=NOW, fetcher=fetcher, force=True)  # type: ignore[arg-type]
        kinds = {s.slug: s.kind for s in db.scalars(select(WatchSource))}
    assert kinds["bareme-insee"] == "bareme" and not any("insee" in u or "cre.fr" in u for u in seen)
