from __future__ import annotations

import json
import os
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from app.fiscal.client import FiscalClient, FiscalEngineError, interpret
from app.fiscal.situation import NET_TO_TAXABLE, HouseholdProfileIn, build_situation, months_since_n2

PROFILE = HouseholdProfileIn(
    birth_date=date(1990, 5, 1),
    postal_insee_code="45234",
    housing="locataire_vide",
    rent_cents=60000,
    activity="actif",
)


def test_months_since_n2() -> None:
    months = months_since_n2(date(2026, 10, 21))
    assert months[0] == "2024-01" and months[-1] == "2026-09" and len(months) == 33


def test_situation_single_person() -> None:
    s = build_situation(PROFILE, period=date(2026, 10, 21), salary_net_by_month={"2026-08": 130000, "2026-09": 130000})
    me = s["individus"]["moi"]
    assert me["salaire_net"]["2024-01"] == 1300.0  # extrapolé depuis l'historique bancaire
    assert me["salaire_imposable"]["2025-06"] == round(1300 * NET_TO_TAXABLE, 2)
    assert me["livret_epargne_populaire_eligibilite"] == {"2026-10": None}
    fam = s["familles"]["famille"]
    assert fam["parents"] == ["moi"] and fam["rsa"] == {"2026-10": None} and fam["ppa"] == {"2026-10": None}
    assert s["menages"]["menage"]["loyer"] == {"2026-10": 600.0}
    assert s["menages"]["menage"]["depcom"] == {"2026-10": "45234"}


def test_situation_couple_children_and_declared_income() -> None:
    profile = PROFILE.model_copy(
        update={
            "couple": True,
            "partner_birth_date": date(1992, 1, 1),
            "partner_monthly_net_cents": 150000,
            "children_birth_dates": [date(2020, 3, 1), date(2018, 6, 1)],
            "annual_taxable_income_n2_cents": 2_400_000,
        }
    )
    s = build_situation(
        profile, period=date(2026, 10, 1), salary_net_by_month={}, unemployment_by_month={"2026-09": 50000}
    )
    assert set(s["individus"]) == {"moi", "conjoint", "enfant_1", "enfant_2"}
    assert s["individus"]["enfant_1"]["date_naissance"]["ETERNITY"] == "2018-06-01"  # trié
    assert s["foyers_fiscaux"]["foyer"]["declarants"] == ["moi", "conjoint"]
    assert s["foyers_fiscaux"]["foyer"]["personnes_a_charge"] == ["enfant_1", "enfant_2"]
    me = s["individus"]["moi"]
    assert me["salaire_imposable"]["2024-03"] == 2000.0  # revenu déclaré N-2 prioritaire
    assert me["chomage_net"]["2026-09"] == 500.0
    assert s["menages"]["menage"]["conjoint"] == ["conjoint"]


def test_profile_validation() -> None:
    with pytest.raises(ValidationError):
        HouseholdProfileIn(birth_date=date(1990, 1, 1), postal_insee_code="ABCDE")
    with pytest.raises(ValidationError):
        HouseholdProfileIn(birth_date=date(1800, 1, 1), postal_insee_code="45234")
    assert HouseholdProfileIn(birth_date=date(1990, 1, 1), postal_insee_code="2A004").postal_insee_code == "2A004"


def test_interpret() -> None:
    out = interpret(
        "2026-10",
        "176.1.2",
        {
            "rsa": 0.0,
            "ppa": 258.29,
            "aide_logement": 31.88,
            "af": None,
            "css_participation_forfaitaire": 14.0,
            "css_cmu_acs_eligibilite": True,
            "livret_epargne_populaire_eligibilite": True,
        },
    )
    assert [(e.variable, e.monthly_cents) for e in out.estimates] == [("ppa", 25829), ("aide_logement", 3188)]
    assert out.css == "participation" and out.css_participation_cents == 1400 and out.lep_eligible
    assert interpret("2026-10", "x", {"css_cmu_acs_eligibilite": True}).css == "gratuite"
    assert interpret("2026-10", "x", {}).css == "non_eligible"


def test_fiscal_client_roundtrip() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["token"] = req.headers.get("x-fiscal-token")
        body = json.loads(req.content)
        seen["outputs"] = {o["variable"] for o in body["outputs"]}
        return httpx.Response(
            200,
            json={"engine_version": "176.1.2", "results": {"ppa": {"2026-10": 100.5}, "rsa": {"2026-10": 0}}},
        )

    http = httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(handler))
    out = FiscalClient("http://fiscal", token="tok", http=http).simulate({"individus": {}}, date(2026, 10, 1))
    assert seen["token"] == "tok" and "livret_epargne_populaire_eligibilite" in seen["outputs"]  # type: ignore[operator]
    assert out.estimates[0].monthly_cents == 10050 and out.engine_version == "176.1.2"


def test_fiscal_client_errors() -> None:
    http = httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(FiscalEngineError):
        FiscalClient("http://fiscal", http=http).simulate({}, date(2026, 10, 1))

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    http2 = httpx.Client(base_url="http://fiscal", transport=httpx.MockTransport(boom))
    with pytest.raises(FiscalEngineError):
        FiscalClient("http://fiscal", http=http2).simulate({}, date(2026, 10, 1))


@pytest.mark.openfisca
@pytest.mark.skipif(not os.environ.get("FISCAL_ENGINE_URL"), reason="moteur fiscal réel non démarré")
def test_real_openfisca_engine() -> None:
    """Bout en bout contre le vrai OpenFisca France (lancé par `make fiscal-test` ou la CI)."""
    client = FiscalClient(os.environ["FISCAL_ENGINE_URL"], token=os.environ.get("FISCAL_TOKEN"))
    period = date(2026, 10, 15)
    low = build_situation(PROFILE, period=period, salary_net_by_month={"2026-09": 90000})
    out_low = client.simulate(low, period)
    by_var = {e.variable: e.monthly_cents for e in out_low.estimates}
    assert by_var.get("ppa", 0) > 20000  # un SMIC partiel ouvre droit à la prime d'activité
    assert by_var.get("aide_logement", 0) > 0
    high = build_situation(PROFILE, period=period, salary_net_by_month={"2026-09": 400000})
    out_high = client.simulate(high, period)
    assert not out_high.estimates and not out_high.lep_eligible
