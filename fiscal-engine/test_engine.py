"""Tests du moteur fiscal (nécessitent openfisca-france : lancés dans l'image ou la CI dédiée)."""

from __future__ import annotations

import importlib
import os

import pytest
from starlette.testclient import TestClient

P = "2026-10"


def situation(net: float) -> dict[str, object]:
    months = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13) if (y, m) <= (2026, 10)]
    return {
        "individus": {
            "moi": {
                "date_naissance": {"ETERNITY": "1990-05-01"},
                "activite": {P: "actif"},
                "salaire_net": dict.fromkeys(months, net),
                "salaire_imposable": {m: round(net * 1.035, 2) for m in months},
            }
        },
        "familles": {"f": {"parents": ["moi"], "enfants": []}},
        "foyers_fiscaux": {"ff": {"declarants": ["moi"], "personnes_a_charge": []}},
        "menages": {
            "m": {
                "personne_de_reference": ["moi"],
                "loyer": {P: 600},
                "statut_occupation_logement": {P: "locataire_vide"},
                "depcom": {P: "45234"},
            }
        },
    }


@pytest.fixture(scope="module")
def client():  # type: ignore[no-untyped-def]
    os.environ["FISCAL_TOKEN"] = "secret-test"
    import engine

    importlib.reload(engine)
    with TestClient(engine.app) as c:
        yield c


def outputs(*names: str) -> list[dict[str, str]]:
    return [{"variable": n, "period": P} for n in names]


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["engine_version"]


def test_token_required(client: TestClient) -> None:
    r = client.post("/simulate", json={"situation": situation(1000), "outputs": outputs("ppa")})
    assert r.status_code == 401


def test_prime_activite_decreases_with_income(client: TestClient) -> None:
    h = {"x-fiscal-token": "secret-test"}
    low = client.post("/simulate", headers=h, json={"situation": situation(900), "outputs": outputs("ppa", "rsa")})
    high = client.post("/simulate", headers=h, json={"situation": situation(1800), "outputs": outputs("ppa", "rsa")})
    ppa_low, ppa_high = low.json()["results"]["ppa"][P], high.json()["results"]["ppa"][P]
    assert ppa_low > ppa_high > 0


def test_whitelist_and_validation(client: TestClient) -> None:
    h = {"x-fiscal-token": "secret-test"}
    forbidden = client.post("/simulate", headers=h, json={"situation": situation(1000), "outputs": outputs("irpp")})
    assert forbidden.status_code == 422
    broken = client.post(
        "/simulate",
        headers=h,
        json={"situation": {"individus": {"x": {"foo": 1}}}, "outputs": outputs("ppa")},
    )
    assert broken.status_code == 422
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
