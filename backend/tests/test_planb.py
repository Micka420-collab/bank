"""Plan B « perte de revenu » : trois scénarios en un écran, chiffres sourcés, démarches liées — sur un foyer type.
La fourchette d'ARE suit la règle officielle (jamais le centime), les prestations sont recalculées par un moteur
fiscal simulé qui réagit au revenu du scénario, l'épargne mobilisable exclut les placements et la crypto, et rien
ne part vers un organisme : les démarches sont des boutons vers le catalogue ou des liens officiels."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from starlette.testclient import TestClient

from app.domain import planb as dom
from app.fiscal.client import FiscalClient
from app.services import planb as svc
from tests.conftest import do_login, do_setup
from tests.scenario import TODAY, seed_household

# ------------------------------------------------------------------ domaine
INCOMES = dom.Incomes(salary=215_000, aids=25_829)
NEEDS = dom.Needs(fixed=90_000, variable=60_000, provision=5_000)  # 1 550 € / mois
FACTS = dom.Facts(tenant=True, credit_monthly_cents=18_000, subscriptions_monthly_cents=3_348, profile_known=True)


@pytest.fixture(autouse=True)
def _no_cache() -> None:
    svc.forget_cache()


def test_are_range_follows_the_official_rule_without_pretending_to_the_cent() -> None:
    r = dom.are_range(215_000)
    assert r is not None
    sjr = 215_000 * dom.NET_TO_GROSS / dom.MONTH_DAYS
    assert r.sjr_day_cents == round(sjr)
    assert r.low_cents == round(0.57 * sjr * dom.MONTH_DAYS) and r.high_cents == round(0.75 * sjr * dom.MONTH_DAYS)
    assert r.low_cents <= r.formula_cents <= r.high_cents
    assert r.low_cents < 215_000 < r.high_cents * 2  # une fourchette, pas un salaire
    # Petit salaire : le plancher journalier joue, mais jamais au-dessus de 75 % du SJR.
    small = dom.are_range(80_000)
    assert small is not None and small.formula_cents == small.high_cents and not small.floor_applied
    assert dom.are_range(0) is None
    d = r.to_dict()
    assert d["rule"]["url"].startswith("https://www.francetravail.fr/") and "simulateur" in d["note"].lower()


def test_scenario_incomes_stop_drop_end_of_rights() -> None:
    v = dom.scenario_incomes(INCOMES, 30)
    are = dom.are_range(215_000)
    assert are is not None
    assert v["stop"].salary == 0 and v["stop"].unemployment == are.low_cents and v["stop"].aids == 25_829
    assert v["drop"].salary == 150_500 and v["drop"].unemployment == 0
    assert v["end_of_rights"].salary == 0 and v["end_of_rights"].unemployment == 0 and v["end_of_rights"].aids == 25_829
    # Bornes : 5 % devient 10 %, 95 % devient 90 %.
    assert dom.scenario_incomes(INCOMES, 5)["drop"].salary == 193_500
    assert dom.scenario_incomes(INCOMES, 95)["drop"].salary == 21_500
    # Déjà au chômage : « ça s'arrête » = fin de droits ; la retraite baisse comme un salaire.
    on_are = dom.Incomes(unemployment=120_000, aids=30_000)
    assert dom.scenario_incomes(on_are)["stop"] == dom.scenario_incomes(on_are)["end_of_rights"]
    retired = dom.Incomes(pension=160_000)
    assert dom.scenario_incomes(retired, 50)["drop"].pension == 80_000
    # Un salaire qui s'arrête alors qu'une allocation est déjà versée : l'ARE estimée ne l'efface pas.
    both = dom.Incomes(salary=150_000, unemployment=40_000)
    low = dom.are_range(150_000)
    assert low is not None and dom.scenario_incomes(both)["stop"].unemployment == max(40_000, low.low_cents)
    small = dom.Incomes(salary=150_000, unemployment=200_000)
    assert dom.scenario_incomes(small)["stop"].unemployment == 200_000 or small.main == "unemployment"
    # Déjà au chômage ou à la retraite : deux scénarios, pas trois (« fin de droits » sans objet ou déjà couvert).
    for inc in (on_are, retired):
        keys = [
            s.key
            for s in dom.build(today=date(2026, 10, 21), incomes=inc, needs=NEEDS, cash_cents=1, facts=FACTS).scenarios
        ]
        assert keys == ["stop", "drop"], keys
    assert dom.Incomes().main == "none"


def test_build_without_engine_counts_months_of_autonomy_from_real_charges_and_says_aids_are_unchanged() -> None:
    plan = dom.build(today=date(2026, 10, 21), incomes=INCOMES, needs=NEEDS, cash_cents=544_000, facts=FACTS)
    assert [s.key for s in plan.scenarios] == ["stop", "drop", "end_of_rights"]
    stop, drop, end = plan.scenarios
    are = dom.are_range(215_000)
    assert are is not None
    assert stop.income_after_cents == are.low_cents + 25_829 and stop.are is not None
    assert stop.gap_cents == NEEDS.total - stop.income_after_cents
    assert stop.months is None if stop.gap_cents <= 0 else stop.months == round(544_000 / stop.gap_cents, 1)
    assert drop.income_after_cents == 150_500 + 25_829 and drop.months is None and drop.status == "ok"
    assert end.income_after_cents == 25_829 and end.gap_cents == 155_000 - 25_829
    assert end.months == round(544_000 / end.gap_cents, 1) == 4.2 and end.status == "tight"
    assert end.runway_month == "2027-02", "l'épargne mobilisable serait à zéro en février 2027"
    assert all("inchangées" in s.aids_note for s in plan.scenarios)
    assert all(s.aids == () for s in plan.scenarios)
    keys = [a.key for a in end.actions]
    assert keys[:2] == ["ft_register", "ass_check"], "J+1 d'abord : s'inscrire, puis vérifier l'ASS avant la fin"
    assert {"caf_declare", "subscriptions", "credit_pause", "landlord"} <= set(keys)
    assert "ppa" not in keys and "rsa" not in keys, "sans moteur, aucun droit n'est inventé"
    by_key = {a.key: a for a in end.actions}
    assert by_key["subscriptions"].template == "resilier_abonnement" and by_key["subscriptions"].amount_cents == 3_348
    assert by_key["credit_pause"].template == "libre" and by_key["ass_check"].url == dom.RULES["ass"].url
    assert by_key["ft_register"].amount_cents == are.formula_cents
    assert "landlord" not in {a.key for a in drop.actions}, "une baisse ne justifie pas d'alerter le bailleur"
    assert any("part basse" in n for n in stop.notes)
    assert plan.rules_recheck is False and plan.notes == ()
    data = dom.to_dict(plan)
    assert data["scenarios"][2]["runway_label"] == "févr. 27" and data["scenarios"][2]["lost_cents"] == 215_000
    assert {s["kind"] for s in data["sources"]} == {"operations", "accounts", "rule"}
    assert "n'envoie rien" in data["disclaimer"]
    later = dom.build(today=date(2027, 6, 1), incomes=INCOMES, needs=NEEDS, cash_cents=0, facts=FACTS)
    assert later.rules_recheck and any("revérifie" in n for n in later.notes)
    assert later.scenarios[2].months == 0.0 and later.scenarios[2].status == "risk"


def test_build_with_engine_shows_aid_deltas_and_links_the_right_procedures() -> None:
    baseline = dom.Simulation(
        estimates=(dom.AidEstimate("ppa", "Prime d'activité", 25_829), dom.AidEstimate("aide_logement", "APL", 12_000)),
        engine_version="176.1.2",
    )
    after_stop = dom.Simulation(
        estimates=(dom.AidEstimate("aide_logement", "APL", 23_000),),
        css="participation",
        lep_eligible=True,
        engine_version="176.1.2",
    )
    after_end = dom.Simulation(
        estimates=(dom.AidEstimate("rsa", "RSA", 63_571), dom.AidEstimate("aide_logement", "APL", 29_000)),
        css="gratuite",
        lep_eligible=True,
        engine_version="176.1.2",
    )
    plan = dom.build(
        today=date(2026, 10, 21),
        incomes=INCOMES,
        needs=NEEDS,
        cash_cents=544_000,
        facts=FACTS,
        baseline=baseline,
        simulations={
            "stop": after_stop,
            "drop": dom.Simulation(error="Moteur fiscal injoignable"),
            "end_of_rights": after_end,
        },
    )
    stop, drop, end = plan.scenarios
    assert [(d.variable, d.delta_cents) for d in stop.aids] == [("ppa", -25_829), ("aide_logement", 11_000)]
    assert stop.aids_total_before_cents == 37_829 and stop.aids_total_after_cents == 23_000
    assert "OpenFisca-France 176.1.2" in stop.aids_note and stop.css == "participation"
    are = dom.are_range(215_000)
    assert are is not None and stop.income_after_cents == are.low_cents + 23_000
    assert drop.aids == () and "inchangées" in drop.aids_note and drop.css == ""
    assert [(d.variable, d.after_cents) for d in end.aids][:2] == [("rsa", 63_571), ("ppa", 0)] or {
        d.variable for d in end.aids
    } == {"rsa", "ppa", "aide_logement"}
    end_keys = [a.key for a in end.actions]
    assert "rsa" in end_keys and "css" in end_keys and "lep" in end_keys and "ppa" not in end_keys
    stop_keys = [a.key for a in stop.actions]
    assert "css" in stop_keys and "rsa" not in stop_keys and "ppa" not in stop_keys
    css = next(a for a in stop.actions if a.key == "css")
    assert css.template == "demander_css" and css.rule == "openfisca" and css.kind == "procedure"
    assert len(end.actions) <= dom.MAX_ACTIONS
    serialized = dom.scenario_to_dict(end)
    assert serialized["aids"][0]["label"] == "RSA" and serialized["actions"][0]["rule"]["key"] == "are"


# ------------------------------------------------------------------ API, sur un foyer type
def _engine(req: httpx.Request) -> httpx.Response:
    """Un moteur qui réagit au revenu du mois simulé : prime d'activité avec un salaire, RSA sans rien."""
    body = json.loads(req.content)
    me = body["situation"]["individus"]["moi"]
    period = body["outputs"][0]["period"]
    salary = float(me["salaire_net"].get(period, 0))
    unemployment = float(me.get("chomage_net", {}).get(period, 0))
    results: dict[str, dict[str, float | bool]] = {
        "aide_logement": {period: 120.0 if salary > 1500 else 230.0},
        "livret_epargne_populaire_eligibilite": {period: salary < 2000},
    }
    if salary > 0:
        results["ppa"] = {period: round(max(0.0, 400 - salary * 0.12), 2)}
    elif unemployment == 0:
        results["rsa"] = {period: 635.71}
        results["css_cmu_acs_eligibilite"] = {period: True}
    else:
        results["css_participation_forfaitaire"] = {period: 8.0}
    return httpx.Response(200, json={"engine_version": "176.1.2", "results": results})


@pytest.fixture
def world(app, client: TestClient) -> TestClient:  # type: ignore[no-untyped-def]
    do_setup(client)
    do_login(client)
    with app.state.sessionmaker() as db:
        seed_household(db)
    app.state.fiscal_client = FiscalClient(
        "http://fiscal", http=httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(_engine))
    )
    from app.api import deps

    app.dependency_overrides[deps.today] = lambda: TODAY
    return client


def test_plan_b_screen_on_a_typical_household(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    r = world.get("/api/planb")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["drop_percent"] == 30 and p["main_income"] == "salary"
    assert p["incomes"]["salary_cents"] == 215_000 and p["incomes"]["aids_cents"] == 25_829
    assert p["cash_cents"] == 64_000 + 480_000, "compte courant + Livret A ; jamais un placement ni la crypto"
    assert p["needs"]["fixed_cents"] > 80_000 and p["needs"]["total_cents"] > p["needs"]["fixed_cents"]
    assert [s["key"] for s in p["scenarios"]] == ["stop", "drop", "end_of_rights"]
    stop, drop, end = p["scenarios"]
    assert stop["title"] == "Ton salaire s'arrête" and stop["are"]["low_cents"] < stop["are"]["high_cents"]
    assert stop["are"]["simulator"]["url"] == "https://candidat.francetravail.fr/portail-simulateurs/"
    # Le moteur a recalculé : plus de prime d'activité sans salaire, APL en hausse, RSA après la fin des droits.
    assert "OpenFisca-France 176.1.2" in stop["aids_note"]
    deltas = {d["variable"]: d for d in stop["aids"]}
    assert deltas["ppa"]["after_cents"] == 0 and deltas["aide_logement"]["after_cents"] == 23_000
    end_deltas = {d["variable"]: d for d in end["aids"]}
    assert end_deltas["rsa"]["after_cents"] == 63_571 and end["css"] == "gratuite"
    assert end["income_after_cents"] == end["aids_total_after_cents"] + p["incomes"]["other_cents"]
    assert end["gap_cents"] == end["needs_cents"] - end["income_after_cents"]
    assert end["months"] == round(p["cash_cents"] / end["gap_cents"], 1) and end["runway_label"]
    assert drop["income_after_cents"] > end["income_after_cents"]
    # Démarches : des boutons vers le catalogue ou des liens officiels, jamais un envoi.
    kinds = {a["kind"] for s in p["scenarios"] for a in s["actions"]}
    assert kinds <= {"procedure", "link", "check"}
    templates = {a["template"] for s in p["scenarios"] for a in s["actions"] if a["kind"] == "procedure"}
    catalogue = {t["key"] for t in world.get("/api/procedures").json()["templates"]}
    assert templates and templates <= catalogue
    end_keys = [a["key"] for a in end["actions"]]
    assert end_keys[0] == "ft_register" and "rsa" in end_keys and "css" in end_keys and "credit_pause" in end_keys
    urls = {a["url"] for s in p["scenarios"] for a in s["actions"] if a.get("url")}
    assert all(
        u.startswith(("https://www.francetravail.fr/", "https://candidat.francetravail.fr/", "https://caf.fr/"))
        for u in urls
    )
    assert all(src.get("url") is None or str(src["url"]).startswith("https://") for src in p["sources"])
    assert sum(1 for src in p["sources"] if src.get("url")) == 3, "les trois règles datées portent leur lien"
    assert p["rules_checked"] == "2026-10-22" and "n'envoie rien" in p["disclaimer"]
    # X % réglable, borné ; la même situation ne repasse pas par le moteur (cache en mémoire).
    calls_before = len(svc._CACHE)
    half = world.get("/api/planb", params={"drop": 50}).json()
    assert half["scenarios"][1]["title"] == "Ton salaire baisse de 50 %"
    assert half["scenarios"][1]["incomes_after"]["salary_cents"] == 107_500
    assert len(svc._CACHE) == calls_before + 1, "seul le scénario « baisse » a changé : une simulation de plus"
    assert world.get("/api/planb", params={"drop": 5}).status_code == 422
    assert world.get("/api/planb", params={"drop": 95}).status_code == 422


def test_plan_b_without_profile_or_engine_stays_honest(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    def down(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    app.state.fiscal_client = FiscalClient(
        "http://fiscal", http=httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(down))
    )
    p = world.get("/api/planb").json()
    assert all(s["aids"] == [] and "inchangées" in s["aids_note"] for s in p["scenarios"])
    assert "erreur 503" in p["scenarios"][0]["aids_note"]
    keys = {a["key"] for s in p["scenarios"] for a in s["actions"]}
    assert "rsa" not in keys and "ppa" not in keys and "css" not in keys, "sans moteur : aucun droit inventé"
    assert "ft_register" in keys and "caf_declare" in keys
    with app.state.sessionmaker() as db:
        from app.models import HouseholdProfile

        db.delete(db.get(HouseholdProfile, 1))
        db.commit()
    p = world.get("/api/planb").json()
    assert "renseigne ton foyer" in p["scenarios"][0]["aids_note"]
    # Déjà au chômage : deux scénarios, pas trois (« ça s'arrête » est la fin de droits).
    agent = svc.agent_view
    with app.state.sessionmaker() as db:
        view = agent(db, TODAY, privacy="aggregates", fiscal=None)
    assert "incomes" not in view and "cash_cents" not in view and "rule" in view
    assert all("incomes_after" not in s and "cash_cents" not in s for s in view["scenarios"])
    assert all("months" in s and "actions" in s for s in view["scenarios"])


# ------------------------------------------------------------------ MCP : l'agent explique, Bank calcule
def test_agents_get_the_plan_with_their_privacy_and_a_budget_of_calls(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    from tests.test_mcp import make_agent

    observer = make_agent(app, world, preset="observateur", name="obs")
    assert "Portée" in observer.fails("get_plan_b"), "le plan B sollicite le moteur fiscal : portée « simulate »"
    agent = make_agent(app, world)
    plan = agent.ok("get_plan_b")
    assert [s["key"] for s in plan["scenarios"]] == ["stop", "drop", "end_of_rights"]
    assert plan["scenarios"][0]["are"]["low_cents"] > 0 and plan["cash_cents"] == 544_000
    assert plan["scenarios"][2]["aids"][0]["label"] and "J+1" in {a["when"] for a in plan["scenarios"][2]["actions"]}
    assert "n'invente aucun droit" in plan["rule"]
    blind = make_agent(app, world, name="agg", privacy="aggregates")
    agg = blind.ok("get_plan_b")
    assert "cash_cents" not in agg and "incomes" not in agg
    assert all("income_after_cents" not in s and "incomes_after" not in s for s in agg["scenarios"])
    for _ in range(5):
        blind.ok("get_plan_b")
    assert "Trop de plans B" in blind.fails("get_plan_b")
