"""Plan B « perte de revenu » : lit dans Bank ce que le domaine attend — revenus par nature (relevés), charges et
rythme (projection de l'année), épargne mobilisable (soldes), faits qui orientent les démarches —, fait recalculer
les prestations par OpenFisca pour chaque scénario quand le foyer est renseigné, et laisse le domaine assembler."""

from __future__ import annotations

import hashlib
import json
import statistics
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.domain import planb as dom
from app.domain.forecast import is_planned_outflow
from app.domain.recurring import add_months
from app.domain.wealth import LIQUID, REGULATED
from app.domain.yearahead import YearAhead
from app.fiscal.client import FiscalClient, FiscalEngineError, SimulationOutcome
from app.fiscal.situation import HouseholdProfileIn, build_situation
from app.services import analytics, documents, yearahead

SCENARIO_ACTIVITY: dict[str, str] = {"stop": "chomeur", "end_of_rights": "chomeur"}


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _last_months(today: date, n: int) -> list[str]:
    first = today.replace(day=1)
    return [_month_key(add_months(first, -i)) for i in range(n, 0, -1)]


def _median(series: dict[str, int], months: list[str]) -> int:
    values = [series.get(m, 0) for m in months]
    return int(statistics.median(values)) if values else 0


def incomes(db: Session, today: date, year: YearAhead | None = None) -> dom.Incomes:
    """Revenus mensuels par nature : médiane des trois derniers mois complets (relevés), prestations observées,
    et le reste des entrées régulières reconnues par la projection."""
    months = _last_months(today, dom.SETTLED_MONTHS)
    by_kind = analytics.monthly_income_series(db, today)
    salary = _median(by_kind["salary"], months)
    unemployment = _median(by_kind["unemployment"], months)
    pension = _median(by_kind["pension"], months)
    aids_by_month: dict[str, int] = dict.fromkeys(months, 0)
    for p in analytics.aid_payments(db, add_months(today.replace(day=1), -dom.SETTLED_MONTHS)):
        key = _month_key(p.date)
        if key in aids_by_month and p.kind not in ("ft_are", "retraite_pension") and p.amount_cents > 0:
            aids_by_month[key] += p.amount_cents
    aids = _median(aids_by_month, months)
    y = year or yearahead.compute(db, today)
    other = max(0, y.income_monthly_cents - (salary + unemployment + pension + aids))
    return dom.Incomes(salary=salary, unemployment=unemployment, pension=pension, aids=aids, other=other)


def needs(db: Session, today: date, year: YearAhead | None = None) -> dom.Needs:
    y = year or yearahead.compute(db, today)
    return dom.Needs(
        fixed=y.fixed_monthly_cents, variable=y.variable_monthly_cents, provision=y.provision_monthly_cents
    )


def cash(db: Session) -> int:
    """Ce qui se mobilise sans casser un placement : comptes courants (même à découvert) et livrets."""
    total = 0
    for a in analytics.account_balances(db):
        if a.id == 0:
            continue  # la ligne crypto n'est jamais de l'argent disponible
        if a.kind in LIQUID or (a.kind in REGULATED and a.balance_cents > 0):
            total += a.balance_cents
    return total


def facts(db: Session, today: date) -> dom.Facts:
    inputs = analytics.forecast_inputs(db, today)
    planned = [s for s in inputs.series if s.direction == "out" and is_planned_outflow(s, inputs.categories)]
    monthly = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1.0}
    credit = sum(
        round(abs(s.amount_cents) * monthly[s.cadence])
        for s in planned
        if s.cadence in monthly and s.category == "credits"
    )
    subs = sum(
        round(abs(s.amount_cents) * monthly[s.cadence])
        for s in planned
        if s.cadence in monthly and s.category in ("abonnements", "telecom")
    )
    raw = analytics.load_profile(db)
    household = analytics.load_financial_profile(db)
    housing = (raw or {}).get("housing") or ((household.get("housing_status") or {}).get("value"))
    tenant = str(housing or "").startswith("locataire")
    return dom.Facts(
        tenant=tenant,
        credit_monthly_cents=credit,
        subscriptions_monthly_cents=subs,
        couple=bool((raw or {}).get("couple")),
        profile_known=raw is not None,
    )


def _as_simulation(outcome: SimulationOutcome) -> dom.Simulation:
    return dom.Simulation(
        estimates=tuple(dom.AidEstimate(e.variable, e.label, e.monthly_cents) for e in outcome.estimates),
        css=outcome.css,
        lep_eligible=outcome.lep_eligible,
        engine_version=outcome.engine_version,
    )


def _with_scenario(series: dict[str, int], months: list[str], value: int) -> dict[str, int]:
    out = dict(series)
    for m in months:
        out[m] = value
    return out


def simulate_all(
    db: Session,
    today: date,
    fiscal: FiscalClient | None,
    current: dom.Incomes,
    variants: Mapping[str, dom.Incomes],
) -> tuple[dom.Simulation | None, dict[str, dom.Simulation], str]:
    """OpenFisca pour aujourd'hui et pour chaque scénario : les trois mois précédents et le mois courant portent le
    revenu du scénario (régime établi). Sans profil ou sans moteur : rien, et la raison."""
    raw = analytics.load_profile(db)
    if raw is None:
        return (
            None,
            {},
            "Prestations supposées inchangées : renseigne ton foyer (Ma situation) pour que Bank les recalcule.",
        )
    if fiscal is None:
        return None, {}, "Prestations supposées inchangées : le moteur fiscal n'est pas configuré."
    profile = documents.with_taxable_income(db, HouseholdProfileIn.model_validate(raw), today)
    history = analytics.monthly_income_series(db, today)
    settled = [*_last_months(today, dom.SETTLED_MONTHS), _month_key(today)]

    def run(inc: dom.Incomes, activity: str | None) -> dom.Simulation:
        prof = profile.model_copy(update={"activity": activity}) if activity else profile
        situation = build_situation(
            prof,
            period=today,
            salary_net_by_month=_with_scenario(history["salary"], settled, inc.salary),
            unemployment_by_month=_with_scenario(history["unemployment"], settled, inc.unemployment),
            pension_by_month=_with_scenario(history["pension"], settled, inc.pension),
        )
        return _simulate_cached(fiscal, situation, today)

    if not _ENGINE_LOCK.acquire(timeout=ENGINE_WAIT_SECONDS):
        return None, {}, "Prestations supposées inchangées : le moteur fiscal est occupé, réessaie dans une minute."
    try:
        baseline = run(current, None)
        if baseline.error:
            return None, {}, f"Prestations supposées inchangées : {baseline.error}."
        sims = {key: run(inc, SCENARIO_ACTIVITY.get(key)) for key, inc in variants.items()}
    finally:
        _ENGINE_LOCK.release()
    return baseline, sims, ""


# Quatre simulations par écran : la même situation ne repasse pas par le moteur pendant quelques heures (cache en
# mémoire du processus, borné, jamais écrit sur disque), et un seul écran à la fois sollicite le moteur.
CACHE_SECONDS = 6 * 3600
CACHE_MAX = 64
ENGINE_WAIT_SECONDS = 30.0
_CACHE: dict[str, tuple[float, dom.Simulation]] = {}
_CACHE_LOCK = threading.Lock()
_ENGINE_LOCK = threading.Lock()


def _fingerprint(situation: dict[str, Any], today: date) -> str:
    raw = json.dumps({"situation": situation, "period": today.isoformat()}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _simulate_cached(fiscal: FiscalClient, situation: dict[str, Any], today: date) -> dom.Simulation:
    key = _fingerprint(situation, today)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    try:
        result = _as_simulation(fiscal.simulate(situation, today))
    except FiscalEngineError as exc:
        return dom.Simulation(error=str(exc))  # une panne ne se met pas en cache
    with _CACHE_LOCK:
        if len(_CACHE) >= CACHE_MAX:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            del _CACHE[oldest]
        _CACHE[key] = (now, result)
    return result


def forget_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def compute(
    db: Session, today: date, *, drop_percent: int = dom.DEFAULT_DROP, fiscal: FiscalClient | None
) -> dom.PlanB:
    year = yearahead.compute(db, today)
    inc = incomes(db, today, year)
    variants = dom.scenario_incomes(inc, drop_percent)
    baseline, sims, note = simulate_all(db, today, fiscal, inc, variants)
    return dom.build(
        today=today,
        incomes=inc,
        needs=needs(db, today, year),
        cash_cents=cash(db),
        facts=facts(db, today),
        drop_percent=drop_percent,
        baseline=baseline,
        simulations=sims,
        engine_note=note,
    )


def view(db: Session, today: date, *, drop_percent: int, fiscal: FiscalClient | None) -> dict[str, Any]:
    return dom.to_dict(compute(db, today, drop_percent=drop_percent, fiscal=fiscal))


def agent_view(db: Session, today: date, *, privacy: str, fiscal: FiscalClient | None) -> dict[str, Any]:
    """Pour un agent (MCP) : les scénarios, les mois d'autonomie, les prestations recalculées et les démarches — en
    agrégats si le jeton l'impose (jamais une opération, jamais un solde par compte)."""
    data = view(db, today, drop_percent=dom.DEFAULT_DROP, fiscal=fiscal)
    if privacy == "aggregates":
        data.pop("incomes", None)
        data.pop("cash_cents", None)
        for s in data["scenarios"]:
            s.pop("incomes_after", None)
            s.pop("cash_cents", None)
            s.pop("income_before_cents", None)
            s.pop("income_after_cents", None)
    data["rule"] = (
        "Un ordre de grandeur calculé par Bank : explique les mois d'autonomie et ce que les aides rattrapent, "
        "propose les démarches dans l'ordre (J+1 d'abord), ne recalcule rien toi-même, n'invente aucun droit. "
        "Rien ne part vers un organisme sans le geste de la personne dans Bank."
    )
    return data


def summary_for_chat(
    db: Session, today: date, *, drop_percent: int = dom.DEFAULT_DROP, fiscal: FiscalClient | None
) -> dict[str, Any]:
    """Pour l'assistant : l'essentiel de chaque scénario, court, avec les démarches à proposer."""
    plan = compute(db, today, drop_percent=drop_percent, fiscal=fiscal)
    return {
        "cash_cents": plan.cash_cents,
        "needs_cents": plan.needs.total,
        "income_cents": plan.incomes.total,
        "main_income": plan.main_income,
        "scenarios": [
            {
                "key": s.key,
                "title": s.title,
                "income_after_cents": s.income_after_cents,
                "gap_cents": s.gap_cents,
                "months": s.months,
                "runway_month": s.runway_month,
                "status": s.status,
                "are": {"low_cents": s.are.low_cents, "high_cents": s.are.high_cents} if s.are else None,
                "aids": [asdict(d) | {"delta_cents": d.delta_cents} for d in s.aids],
                "aids_note": s.aids_note,
                "actions": [{"key": a.key, "title": a.title, "when": a.when} for a in s.actions],
            }
            for s in plan.scenarios
        ],
        "notes": list(plan.notes),
    }
