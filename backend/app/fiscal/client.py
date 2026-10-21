"""Client du moteur fiscal (conteneur OpenFisca isolé, réseau interne uniquement)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.fiscal.situation import FAMILLE_OUTPUTS, INDIVIDU_OUTPUTS

LABELS = {
    "rsa": "RSA",
    "ppa": "Prime d'activité",
    "aide_logement": "Aide au logement (APL/ALS/ALF)",
    "af": "Allocations familiales",
    "asf": "Allocation de soutien familial",
}


class FiscalEngineError(Exception):
    pass


@dataclass(frozen=True)
class AidEstimate:
    variable: str
    label: str
    monthly_cents: int


@dataclass(frozen=True)
class SimulationOutcome:
    period: str
    engine_version: str
    estimates: list[AidEstimate]
    css: str  # gratuite | participation | non_eligible
    css_participation_cents: int
    lep_eligible: bool


def interpret(period: str, version: str, values: dict[str, Any]) -> SimulationOutcome:
    def num(v: str) -> float:
        raw = values.get(v)
        return float(raw) if isinstance(raw, int | float) else 0.0

    estimates = [AidEstimate(v, LABELS[v], round(num(v) * 100)) for v in LABELS if num(v) > 0.005]
    participation = round(num("css_participation_forfaitaire") * 100)
    eligible = bool(values.get("css_cmu_acs_eligibilite"))
    css = "participation" if participation > 0 else ("gratuite" if eligible else "non_eligible")
    return SimulationOutcome(
        period=period,
        engine_version=version,
        estimates=sorted(estimates, key=lambda e: -e.monthly_cents),
        css=css,
        css_participation_cents=participation,
        lep_eligible=bool(values.get("livret_epargne_populaire_eligibilite")),
    )


class FiscalClient:
    def __init__(self, base_url: str, token: str | None = None, http: httpx.Client | None = None) -> None:
        headers = {"X-Fiscal-Token": token} if token else {}
        self._http = http or httpx.Client(base_url=base_url, timeout=httpx.Timeout(45.0, connect=5.0), headers=headers)
        if http is not None and token:
            self._http.headers.update(headers)

    def simulate(self, situation: dict[str, Any], period: date) -> SimulationOutcome:
        p = f"{period.year:04d}-{period.month:02d}"
        outputs = [{"variable": v, "period": p} for v in (*FAMILLE_OUTPUTS, *INDIVIDU_OUTPUTS)]
        try:
            resp = self._http.post("/simulate", json={"situation": situation, "outputs": outputs})
        except httpx.HTTPError as exc:
            raise FiscalEngineError("Moteur fiscal injoignable") from exc
        if resp.status_code != 200:
            raise FiscalEngineError(f"Moteur fiscal : erreur {resp.status_code}")
        body = resp.json()
        values = {k: v.get(p) for k, v in body.get("results", {}).items() if isinstance(v, dict)}
        return interpret(p, str(body.get("engine_version", "?")), values)
