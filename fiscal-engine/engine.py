"""Moteur fiscal Bank : OpenFisca France derrière une API minimale et fermée.

* Réseau interne Docker uniquement, jeton partagé optionnel (défense en profondeur).
* Seules des variables de sortie en liste blanche peuvent être calculées.
* Le système socio-fiscal (~3 s, ~300 Mo) est chargé UNE fois au démarrage.
* Aucune journalisation du contenu des situations (ce sont des revenus).

openfisca-france est sous AGPL-3.0 : en usage strictement personnel rien à faire ; si tu exposes
un jour ce service à des tiers, tu dois leur proposer le code source correspondant.
"""

from __future__ import annotations

import hmac
import os
import re
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

ALLOWED_OUTPUTS = {
    "rsa",
    "ppa",
    "aide_logement",
    "af",
    "asf",
    "css_participation_forfaitaire",
    "css_cmu_acs_eligibilite",
    "livret_epargne_populaire_eligibilite",
    "cheque_energie",
}
_PERIOD = re.compile(r"^\d{4}(-\d{2})?$")


def _load_token() -> str:
    if os.environ.get("FISCAL_TOKEN_FILE"):
        with open(os.environ["FISCAL_TOKEN_FILE"], encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("FISCAL_TOKEN", "")


_TOKEN = _load_token()
_STATE: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    from openfisca_france import CountryTaxBenefitSystem

    _STATE["tbs"] = CountryTaxBenefitSystem()
    _STATE["version"] = version("openfisca-france")
    yield


app = FastAPI(title="bank-fiscal-engine", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class Output(BaseModel):
    variable: str = Field(max_length=64)
    period: str = Field(max_length=7)


class SimulateRequest(BaseModel):
    situation: dict[str, Any]
    outputs: list[Output] = Field(min_length=1, max_length=20)


def _check_token(token: str | None) -> None:
    if _TOKEN and not hmac.compare_digest(_TOKEN, token or ""):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if "tbs" in _STATE else "loading", "engine_version": _STATE.get("version", "?")}


@app.post("/simulate")
def simulate(req: SimulateRequest, x_fiscal_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_token(x_fiscal_token)
    from openfisca_core.errors import SituationParsingError
    from openfisca_core.simulation_builder import SimulationBuilder

    for out in req.outputs:
        if out.variable not in ALLOWED_OUTPUTS or not _PERIOD.match(out.period):
            raise HTTPException(status_code=422, detail=f"sortie non autorisée : {out.variable}")
    try:
        sim = SimulationBuilder().build_from_entities(_STATE["tbs"], req.situation)
    except SituationParsingError as exc:
        raise HTTPException(status_code=422, detail="situation invalide") from exc
    results: dict[str, dict[str, Any]] = {}
    for out in req.outputs:
        value = sim.calculate(out.variable, out.period)[0]
        results.setdefault(out.variable, {})[out.period] = value.item() if hasattr(value, "item") else value
    return {"engine_version": _STATE["version"], "results": results}
