"""Corrélations : rassemble ce que Bank sait (prélèvements réguliers, contrats lus dans Papiers, salaires, aides,
ressources déclarées, Linky, dépenses par mois) et laisse le domaine dire ce qui est lié. Les constats sont
calculés à la lecture, jamais stockés ; ce que tu en fais (démarche, écarté) l'est."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import contracts
from app.domain import correlations as dom
from app.domain.categorize import CATEGORY_KIND
from app.domain.energy_analysis import ClimateCheck
from app.domain.recurring import add_months, detect_recurring
from app.models import Category, Obligation, ObligationDone, OpportunityState, Transaction, VaultDocument, VaultField
from app.security import audit
from app.services import analytics, procedures
from app.services import energy as energy_service
from app.services import housing as housing_service
from app.services import weather as weather_service

DOC_KINDS = ("lease", "insurance", "payslip", "rent_receipt")
STATE_PREFIX = "corr:"


class CorrelationError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ entrées
def _series(db: Session, today: date) -> list[dom.Series]:
    accounts = [a.id for a in analytics.budget_accounts(db)]
    txns = analytics.load_txns(db, accounts, add_months(today, -24))
    by_merchant: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for t in txns:
        if t.amount_cents < 0 and not t.is_transfer and t.merchant:
            by_merchant[t.merchant].append((t.date, -t.amount_cents))
    out: list[dom.Series] = []
    for s in detect_recurring(txns, today):
        if s.direction != "out":
            continue
        history = tuple(sorted(by_merchant.get(s.merchant, ())))
        out.append(dom.Series(s.key, s.merchant, s.category, s.cadence, abs(s.amount_cents), history))
    return out


def _docs(db: Session) -> list[dom.Doc]:
    out: list[dom.Doc] = []
    docs = db.scalars(
        select(VaultDocument).where(
            VaultDocument.kind.in_(DOC_KINDS), VaultDocument.status.in_(("to_review", "verified"))
        )
    )
    for doc in docs:
        fields: dict[str, Any] = {}
        for f in db.scalars(select(VaultField).where(VaultField.document_id == doc.id)):
            try:
                fields[f.key] = json.loads(f.value)
            except ValueError:
                continue
        out.append(dom.Doc(doc.id, doc.kind, doc.document_date, fields))
    # le bail le plus récent d'abord : c'est lui qui compte
    out.sort(key=lambda d: (d.kind != "lease", -(d.day.toordinal() if d.day else 0)))
    return out


def _credits(db: Session, since: date) -> tuple[list[dom.Credit], list[dom.Credit]]:
    rows = db.execute(
        select(Transaction.id, Transaction.booking_date, Transaction.amount_cents, Transaction.aid_kind, Category.slug)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.booking_date >= since,
            Transaction.amount_cents > 0,
            Transaction.is_transfer.is_(False),
        )
    )
    salary: list[dom.Credit] = []
    aids: list[dom.Credit] = []
    for i, d, a, aid, slug in rows:
        if aid:
            aids.append(dom.Credit(i, d, a, aid))
        elif slug == "salaire":
            salary.append(dom.Credit(i, d, a))
    return salary, aids


def _declared(db: Session) -> list[dom.Declared]:
    out: list[dom.Declared] = []
    rows = db.execute(
        select(ObligationDone.period, ObligationDone.declared)
        .join(Obligation, Obligation.id == ObligationDone.obligation_id)
        .where(Obligation.kind.in_(("caf_dtr", "msa_dtr")), ObligationDone.declared.is_not(None))
    )
    for period, declared in rows:
        try:
            totals = {str(k): int(v) for k, v in json.loads(declared or "{}").items()}
        except (ValueError, TypeError):
            continue
        first = date.fromisoformat(period + "-01")
        out.append(dom.Declared(period, add_months(first, 1) - timedelta(days=1), totals))
    return out


def _energy(db: Session, today: date) -> dom.EnergyLink | None:
    summary = energy_service.summary_for_agent(db, today)
    if summary is None or summary.get("stale"):
        return None
    conn = energy_service.active_connection(db)
    return dom.EnergyLink(
        provider=getattr(conn, "label", None),
        monthly_payment_cents=summary.get("monthly_payment_cents"),
        recommended_monthly_cents=summary.get("recommended_monthly_cents"),
        regularisation_cents=summary.get("regularisation_cents"),
    )


def _monthly(db: Session, today: date) -> tuple[dict[str, dict[str, int]], dict[str, int], dict[date, int], list[date]]:
    since = add_months(today.replace(day=1), -18)
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents, Category.slug)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.booking_date >= since,
            Transaction.booking_date < today.replace(day=1),
            Transaction.is_transfer.is_(False),
        )
    )
    spend: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    income: dict[str, int] = defaultdict(int)
    daily: dict[date, int] = defaultdict(int)
    paydays: list[date] = []
    for d, a, slug in rows:
        key = f"{d.year:04d}-{d.month:02d}"
        if a > 0:
            if slug in ("salaire", "aides", "autres_revenus"):
                income[key] += a
            if slug == "salaire":
                paydays.append(d)
        elif slug and CATEGORY_KIND.get(slug) == "variable":
            spend[slug][key] += -a
            daily[d] += -a
    return {k: dict(v) for k, v in spend.items()}, dict(income), dict(daily), sorted(paydays)


def _climate(db: Session, today: date) -> ClimateCheck | None:
    """Consommation à climat égal (#33) : seulement avec un compteur et des températures (accord « Météo »)."""
    if energy_service.active_connection(db) is None:
        return None
    temps = weather_service.temps(db, today - timedelta(days=800))
    return energy_service.climate(db, today, temps) if temps else None


def build_inputs(db: Session, today: date) -> dom.Inputs:
    salary, aids = _credits(db, add_months(today, -24))
    spend, income, daily, paydays = _monthly(db, today)
    names = {c.slug: c.name for c in db.scalars(select(Category))}
    return dom.Inputs(
        today=today,
        series=_series(db, today),
        docs=_docs(db),
        salary_credits=salary,
        aid_credits=aids,
        declared=_declared(db),
        energy=_energy(db, today),
        power=energy_service.power(db, today) if energy_service.active_connection(db) is not None else None,
        climate=_climate(db, today),
        monthly_spend=spend,
        monthly_income=income,
        daily_variable_spend=daily,
        paydays=paydays,
        category_names=names,
        housing=housing_service.link(db, today),
    )


# ------------------------------------------------------------------ vue
def _states(db: Session) -> dict[str, OpportunityState]:
    return {
        s.key[len(STATE_PREFIX) :]: s
        for s in db.scalars(select(OpportunityState).where(OpportunityState.key.like(STATE_PREFIX + "%")))
    }


def findings(db: Session, today: date) -> list[dom.Finding]:
    return dom.analyse(build_inputs(db, today))


def housing_checks(db: Session, today: date) -> dict[str, Any]:
    """Les constats logement (DPE, carte des loyers) et ce qui manque pour en dire plus (#34)."""
    found, notes = dom.housing_checks(build_inputs(db, today))
    states = _states(db)
    rows = []
    for f in found:
        d = dom.finding_to_dict(f)
        state = states.get(f.key)
        d["status"] = state.status if state else "new"
        d["procedure_id"] = state.procedure_id if state else None
        rows.append(d)
    return {"findings": rows, "notes": notes}


def view(db: Session, today: date) -> dict[str, Any]:
    states = _states(db)
    rows = []
    hidden = 0
    for f in findings(db, today):
        state = states.get(f.key)
        d = dom.finding_to_dict(f)
        d["status"] = state.status if state else "new"
        d["procedure_id"] = state.procedure_id if state else None
        if state and state.status == "dismissed":
            hidden += 1
            continue
        rows.append(d)
    return {
        "findings": rows,
        "hidden": hidden,
        "irl_source": contracts.irl_source(contracts.latest_irl_quarter()),
        "disclaimer": (
            "Chaque constat renvoie à ses pièces (contrat, opération). Un lien statistique est une piste, jamais une "
            "cause ; un doublon est « probable » tant que les deux contrats ne sont pas lus. Bank n'envoie rien : "
            "résilier, réclamer, contester restent tes gestes."
        ),
    }


def decide(db: Session, key: str, status: str, *, actor: str) -> None:
    if status not in ("dismissed", "new"):
        raise CorrelationError("Décision inconnue")
    skey = STATE_PREFIX + key[:150]
    state = db.get(OpportunityState, skey)
    if status == "new":
        if state is not None:
            db.delete(state)
    else:
        if state is None:
            state = OpportunityState(key=skey, status=status)
            db.add(state)
        else:
            state.status = status
    db.flush()
    audit.record(db, actor, "correlation.decide", key=key[:60], status=status)


def start(db: Session, key: str, *, actor: str, today: date) -> int:
    """Préparer la démarche qu'un constat propose (résiliation du doublon…) : une démarche par constat."""
    found = next((f for f in findings(db, today) if f.key == key), None)
    if found is None or not found.action or not found.action.get("template"):
        raise CorrelationError("Rien à préparer pour ce constat", 409)
    initial: dict[str, Any] = {k: v for k, v in dict(found.action.get("initial") or {}).items() if v is not None}
    merchant = found.action.get("merchant")
    if merchant:
        initial["fournisseur"] = str(merchant).title()
        series = next((s for s in build_inputs(db, today).series if s.merchant == merchant), None)
        if series is not None and series.cadence == "monthly":
            initial["montant_mensuel"] = series.amount_cents
    proc = procedures.create(
        db,
        str(found.action["template"]),
        actor=actor,
        today=today,
        opportunity_key=STATE_PREFIX + key[:150],
        initial=initial,
        title=f"Résilier {str(merchant).title()}" if merchant else found.action.get("title"),
    )
    return proc.id


# ------------------------------------------------------------------ pour l'agent, les cartes et les alertes
def agent_view(db: Session, today: date, privacy: str) -> dict[str, Any]:
    data = view(db, today)
    rows = []
    for f in data["findings"]:
        item = {
            k: f[k]
            for k in ("key", "kind", "level", "topic_label", "title", "yearly_cents", "statistical", "confidence")
        }
        if privacy != "aggregates":
            item["body"] = f["body"]
            item["evidence"] = (
                f["evidence"]
                if privacy == "full"
                else [{"label": e["label"], "value": e["value"]} for e in f["evidence"]]
            )
        rows.append(item)
    return {
        "findings": rows,
        "rule": (
            "Explique le constat avec ses pièces ; une piste statistique n'est jamais une cause. Résilier, réclamer ou "
            "contester restent des gestes de la personne, dans Bank ou auprès de l'organisme."
        ),
    }


def top_for_cards(db: Session, today: date) -> list[dict[str, Any]]:
    return [f for f in view(db, today)["findings"] if f["level"] in ("danger", "warning") and not f["statistical"]][:3]


def alerts(db: Session, *, today: date, privacy: str, public_url: str) -> list[tuple[str, str, bool]]:
    out: list[tuple[str, str, bool]] = []
    for f in view(db, today)["findings"]:
        if f["level"] != "danger":
            continue
        money = f" ({f['yearly_cents'] // 100} € par an en jeu)" if f.get("yearly_cents") else ""
        text = f"🔎 {f['title']}{money}. {public_url}/#/situation"
        if privacy == "full":
            text = f"🔎 {f['title']}{money} — {f['body'][:160]} {public_url}/#/situation"
        out.append((f"corr:{f['key']}", text, False))
    return out
