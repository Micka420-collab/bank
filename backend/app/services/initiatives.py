"""Initiatives : construit ce que Bank sait (Context), en tire les cartes du moment, applique ce que tu décides."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import obligations as obligation_specs
from app.domain.initiatives import (
    Card,
    ConnectionInfo,
    Context,
    CorrelationInfo,
    EmergencyInfo,
    GoalInfo,
    MerchantGroup,
    MonthReview,
    ObligationInfo,
    ObligationSuggestion,
    PlanningInfo,
    ProvisionInfo,
    SalaryInfo,
    SeriesInfo,
    State,
    build_cards,
    card_to_dict,
)
from app.domain.onboarding import QUESTIONS, recommended_emergency_months, suggestions
from app.domain.recurring import add_months
from app.domain.wealth import emergency_fund
from app.models import (
    BankConnection,
    Category,
    ConnectionStatus,
    EnergyReading,
    InitiativeState,
    MerchantHint,
    Transaction,
    User,
)
from app.security import audit
from app.services import advisor, analytics, correlations, documents, planning, start, yearahead
from app.services import energy as energy_service
from app.services import obligations as obligations_service
from app.services.categories import recategorize
from app.services.settings_store import get_setting, set_setting

SETTING_KEYS = ("savings_goal_cents", "buffer_cents")
DEFAULT_LIMIT = 3


class InitiativeError(ValueError):
    pass


# ------------------------------------------------------------------ contexte
def _states(db: Session) -> dict[str, State]:
    return {r.key: State(r.status, r.until, r.note) for r in db.scalars(select(InitiativeState))}


def _uncategorized(db: Session, today: date) -> list[MerchantGroup]:
    rows = db.execute(
        select(
            Transaction.merchant,
            func.count(Transaction.id),
            func.sum(Transaction.amount_cents),
            func.max(Transaction.booking_date),
        )
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.slug == "a_categoriser",
            Transaction.booking_date >= today - timedelta(days=90),
            Transaction.is_transfer.is_(False),
            Transaction.merchant != "",
        )
        .group_by(Transaction.merchant)
        .order_by(func.count(Transaction.id).desc())
        .limit(12)
    )
    groups = [MerchantGroup(m, int(n), int(total or 0), last) for m, n, total, last in rows]
    if not groups:
        return groups
    names = {c.slug: c.name for c in db.scalars(select(Category))}
    proposed = {h.merchant: h.category_slug for h in db.scalars(select(MerchantHint))}
    return [
        MerchantGroup(
            g.merchant,
            g.count,
            g.total_cents,
            g.last_date,
            proposed.get(g.merchant),
            names.get(proposed.get(g.merchant, "")),
        )
        if g.merchant in proposed and proposed[g.merchant] in names
        else g
        for g in groups
    ]


def _salary(db: Session, today: date, series: list[Any]) -> SalaryInfo | None:
    s = analytics.salary_series(series)
    if s is None:
        return None
    since = add_months(today.replace(day=1), -6)
    days = db.scalars(
        select(Transaction.booking_date)
        .join(Category, Transaction.category_id == Category.id)
        .where(Category.slug == "salaire", Transaction.amount_cents > 0, Transaction.booking_date >= since)
    )
    months = len({(d.year, d.month) for d in days})
    median = advisor.detected_facts(db, today).salary_monthly_cents or s.amount_cents
    return SalaryInfo(s.merchant, median, months, s.day_of_month, s.next_date, s.last_date)


def _month_review(db: Session, today: date) -> MonthReview | None:
    prev_start = add_months(today.replace(day=1), -1)
    key = f"{prev_start.year:04d}-{prev_start.month:02d}"
    totals = advisor._monthly_totals(db, today, 1).get(key)
    if not totals:
        return None
    first = db.scalar(select(func.min(Transaction.booking_date)))
    if first is None:
        return None
    month_end = today.replace(day=1) - timedelta(days=1)
    days = (month_end - max(first, prev_start)).days + 1
    income = totals.get("income", 0)
    spent = -(totals.get("out:fixed", 0) + totals.get("out:variable", 0) + totals.get("out:uncategorized", 0))
    flow = advisor.cash_flow(db, today)
    return MonthReview(key, days, income, spent, int(flow["left_avg_cents"]))


def build_context(db: Session, today: date) -> Context:
    raw = advisor.answers(db)
    values = {k: v["value"] for k, v in raw.items()}
    first = db.scalar(select(func.min(Transaction.booking_date)))
    series = analytics.recurring_for_budget(db, today)
    salary = _salary(db, today, series)
    cats = [
        (c.slug, c.name)
        for c in db.scalars(
            select(Category).where(Category.kind.in_(["variable", "fixed"])).order_by(Category.sort_order)
        )
    ]
    essentials = analytics.monthly_essentials(db, today)
    ef = emergency_fund(analytics.account_balances(db, today), essentials)
    user = db.scalar(select(User).limit(1))
    energy_conn = energy_service.active_connection(db)
    last_energy = (
        db.scalar(select(func.max(EnergyReading.day)).where(EnergyReading.connection_id == energy_conn.id))
        if energy_conn
        else None
    )
    tax = documents.latest(db, "avis_impot")
    plan_row = planning.plan(db)
    model = planning.pay_model(db)
    preds = planning.predictions(db, today, analytics.salary_series(series)) if plan_row else []
    pinfo = PlanningInfo(
        connected=plan_row is not None,
        has_rules=bool(model and model.rules),
        pay_date=preds[0].pay_date if preds else None,
        predicted_cents=preds[0].amount_cents if preds else None,
        usual_cents=salary.median_cents if salary else None,
        extras=tuple((e.pattern, e.count, e.total_cents) for e in preds[0].extras) if preds else (),
    )
    goals = [
        GoalInfo(int(g["id"]), str(g["name"]), float(g["progress"]))
        for g in analytics.savings_goals_status(db, today, [])
    ]
    return Context(
        today=today,
        has_transactions=first is not None,
        months_of_data=((today - first).days / 30.44) if first else 0.0,
        questions=QUESTIONS,
        answers=values,
        suggestions=suggestions(advisor.detected_facts(db, today), values),
        series=[
            SeriesInfo(
                s.key,
                s.merchant,
                s.amount_cents,
                s.cadence,
                s.occurrences,
                s.category,
                s.confidence,
                s.last_date,
                s.next_date,
            )
            for s in series
            if s.direction == "out"
        ],
        uncategorized=_uncategorized(db, today),
        categories=cats,
        salary=salary,
        month_review=_month_review(db, today),
        savings_goal_cents=int(get_setting(db, "savings_goal_cents")),
        emergency=EmergencyInfo(
            ef.months_covered,
            int(values.get("emergency_target_months") or recommended_emergency_months(values)),
            ef.available_cents,
        ),
        goals=goals,
        totp_enabled=bool(user.totp_enabled) if user else True,
        connections=[
            ConnectionInfo(
                c.id,
                c.aspsp_name,
                c.valid_until.date() if c.valid_until else None,
                # Powens : l'état de la connexion dit quand l'authentification forte est à refaire (SCARequired…)
                reconnect=(c.last_error or "Ta banque demande une nouvelle authentification.")
                if c.status == ConnectionStatus.EXPIRED
                else None,
            )
            for c in db.scalars(
                select(BankConnection).where(
                    or_(
                        BankConnection.status == ConnectionStatus.ACTIVE,
                        and_(BankConnection.status == ConnectionStatus.EXPIRED, BankConnection.provider == "powens"),
                    )
                )
            )
        ],
        energy_connected=energy_conn is not None,
        energy_days_since_data=(today - last_energy).days if last_energy else None,
        latest_tax_period=int(tax["period"]) if tax and str(tax.get("period", "")).isdigit() else None,
        planning=pinfo,
        paid_recently=bool(salary and (today - salary.last_date).days <= 2),
        obligations=_obligations(db, today),
        correlations=[
            CorrelationInfo(
                key=str(f["key"]),
                level=str(f["level"]),
                topic_label=str(f["topic_label"]),
                title=str(f["title"]),
                body=str(f["body"]),
                yearly_cents=f.get("yearly_cents"),
                has_action=bool(f.get("action")) and not f.get("procedure_id"),
            )
            for f in correlations.top_for_cards(db, today)
        ],
        provision=_provision(db, today),
        obligation_suggestions=[
            ObligationSuggestion(k, obligation_specs.SPECS[k].label) for k in obligations_service.suggestions(db, today)
        ],
        states=_states(db),
    )


def _provision(db: Session, today: date) -> ProvisionInfo | None:
    card = yearahead.provision_card(db, today)
    if card is None:
        return None
    return ProvisionInfo(
        key=str(card["key"]),
        provision_cents=int(card["provision_cents"]),
        target_cents=int(card["target_cents"]),
        yearly_cents=int(card["yearly_cents"]),
        biggest=str(card["biggest"]),
        count=int(card["count"]),
        lowest=str(card["lowest"]),
        status=str(card["status"]),
    )


def _obligations(db: Session, today: date) -> list[ObligationInfo]:
    out: list[ObligationInfo] = []
    for r in obligations_service.view(db, today)["obligations"]:
        if not r["active"]:
            continue
        cur = r.get("current")
        if cur is None and not r["needs_anchor"]:
            continue
        summary = None
        if cur and r["kind"] in ("caf_dtr", "msa_dtr") and cur["state"] in ("open", "soon", "late"):
            try:
                summary = obligations_service.prefill(db, int(r["id"]), str(cur["key"]), today)["summary"]
            except obligations_service.ObligationError:
                summary = None
        out.append(
            ObligationInfo(
                id=int(r["id"]),
                kind=str(r["kind"]),
                label=str(r["label"]),
                organism=str(r["organism_name"]),
                state=str(cur["state"]) if cur else "upcoming",
                period_key=str(cur["key"]) if cur else "",
                period_label=str(cur["label"]) if cur else "",
                opens=date.fromisoformat(cur["opens"]) if cur else today,
                deadline=date.fromisoformat(cur["deadline"]) if cur else today,
                awaiting_payment=bool(cur["awaiting_payment"]) if cur else False,
                needs_anchor=bool(r["needs_anchor"]),
                anchor_label=r.get("anchor_label"),
                consequence=str(r["consequence"]),
                what=str(r["what"]),
                automatic=bool(r.get("automatic")),
                prefill_summary=summary,
            )
        )
    return out


def cards(db: Session, today: date) -> list[Card]:
    return build_cards(build_context(db, today))


# Cartes de mise en route : tues tant que la liste « Bien démarrer » se voit (elle pose déjà ces questions-là).
SETUP_KEYS = frozenset({"start:data", "security:totp", "planning:connect"})


def listing(db: Session, today: date, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    all_cards = cards(db, today)
    if start.checklist_visible(db):
        all_cards = [c for c in all_cards if c.key not in SETUP_KEYS]
    return {
        "cards": [card_to_dict(c) for c in all_cards[:limit]],
        "total": len(all_cards),
        "by_topic": dict(_count_by_topic(all_cards)),
    }


def _count_by_topic(all_cards: list[Card]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for c in all_cards:
        out[c.topic] += 1
    return out


# ------------------------------------------------------------------ décisions
def _set_state(db: Session, key: str, status: str, *, until: date | None = None, note: str | None = None) -> None:
    row = db.get(InitiativeState, key)
    if row is None:
        db.add(InitiativeState(key=key, status=status, until=until, note=note))
    else:
        row.status, row.until, row.note, row.updated_at = status, until, note, utcnow()


def act(db: Session, *, key: str, action_id: str, value: Any, actor: str, today: date) -> str:
    """Applique l'action d'une carte. L'action doit exister sur une carte visible aujourd'hui : rien d'autre n'est
    acceptable depuis le navigateur. Retourne une phrase courte pour l'écran."""
    card = next((c for c in cards(db, today) if c.key == key), None)
    if card is None:
        raise InitiativeError("Cette carte n'est plus d'actualité")
    action = next((a for a in card.actions if a.id == action_id), None)
    if action is None:
        raise InitiativeError("Action inconnue pour cette carte")
    effect = dict(action.effect)
    kind = effect.get("type")
    result = "Noté."
    if kind == "snooze":
        _set_state(db, key, "snoozed", until=today + timedelta(days=int(effect.get("days", 7))))
        result = "Je te le redemanderai plus tard."
    elif kind == "dismiss":
        _set_state(db, key, "dismissed")
        result = "Je ne te le redemanderai plus."
    elif kind in ("done", "navigate"):
        if kind == "done":
            _set_state(db, key, "done")
        result = "Noté."
    elif kind == "ack":
        _set_state(db, key, "done", note=str(effect.get("note", ""))[:40])
        result = {
            "voulu": "Compté dans tes charges.",
            "terminé": "Je ne l'attendrai plus.",
            "inconnu": "À vérifier auprès de ta banque.",
        }.get(str(effect.get("note")), "Noté.")
    elif kind == "answer":
        v = value if effect.get("from_input") else effect.get("value")
        if v is None:
            raise InitiativeError("Il manque la réponse")
        advisor.set_answer(db, str(effect["question_id"]), v, source=actor)
        _set_state(db, key, "done")
        result = "Noté : ton agent le saura."
    elif kind == "setting":
        setting = str(effect.get("key"))
        if setting not in SETTING_KEYS:
            raise InitiativeError("Réglage non modifiable ici")
        v = value if effect.get("from_input") else effect.get("value")
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 10_000_000:
            raise InitiativeError("Montant invalide")
        set_setting(db, setting, v)
        audit.record(db, actor, "budget.settings", key=setting)
        _set_state(db, key, "done")
        result = f"Réservé chaque mois : {v // 100} €."
    elif kind == "obligation":
        obligations_service.activate(db, str(effect.get("kind")), actor=actor)
        _set_state(db, key, "done")
        result = "Je m'en occupe : je te préviens à l'ouverture, avant l'échéance, et je constate quand c'est fait."
    elif kind == "obligation_dismiss":
        obligations_service.dismiss_suggestion(db, str(effect.get("kind")), actor=actor)
        _set_state(db, key, "dismissed")
        result = "Je ne te le proposerai plus (tu peux l'ajouter depuis Aides)."
    elif kind == "obligation_done":
        obligations_service.mark_done(db, int(effect["id"]), str(effect["period"]), actor=actor, today=today)
        _set_state(db, key, "done")
        result = "Noté comme fait. Je vérifierai que le versement suit."
    elif kind == "correlation_dismiss":
        correlations.decide(db, str(effect.get("key")), "dismissed", actor=actor)
        _set_state(db, key, "dismissed")
        result = "Écarté. Tu le retrouveras dans Ma situation si tu changes d'avis."
    elif kind == "correlation_start":
        proc_id = correlations.start(db, str(effect.get("key")), actor=actor, today=today)
        _set_state(db, key, "done")
        result = f"Démarche préparée (n° {proc_id}) : Papiers › Démarches, tu relis et tu envoies."
    elif kind == "rule":
        slug = str(value or "")
        category = db.scalar(select(Category).where(Category.slug == slug, Category.kind.in_(["variable", "fixed"])))
        if category is None:
            raise InitiativeError("Choisis une enveloppe")
        merchant = str(effect.get("merchant", ""))
        txn = db.scalar(
            select(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .where(Transaction.merchant == merchant, Category.slug == "a_categoriser")
            .order_by(Transaction.booking_date.desc())
        )
        if txn is None:
            raise InitiativeError("Plus rien à classer pour ce marchand")
        changed = 1 + recategorize(db, txn, category, learn=True)
        hint = db.get(MerchantHint, merchant)
        if hint is not None:
            db.delete(hint)  # la proposition a servi (ou a été corrigée) : la règle apprise prend le relais
        _set_state(db, key, "done")
        result = f"Classé en {category.name} : {changed} opération(s), et les suivantes suivront."
    else:
        raise InitiativeError("Action inconnue")
    audit.record(db, actor, "initiative.act", card=key[:120], choice=action_id)
    return result


def ack_notes(db: Session) -> dict[str, str]:
    """Clé de série -> réponse (« voulu », « terminé », « inconnu ») : pour l'écran des abonnements."""
    return {
        r.key[len("series:") :]: r.note or ""
        for r in db.scalars(select(InitiativeState))
        if r.key.startswith("series:") and r.note
    }


def agent_view(db: Session, today: date, limit: int = 3) -> list[dict[str, Any]]:
    """Ce que Bank aimerait demander ou dire : l'agent peut le relayer en conversation (titres et clés seulement)."""
    return [
        {"key": c.key, "kind": c.kind, "topic": c.topic, "title": c.title, "why": c.why}
        for c in cards(db, today)[:limit]
        if c.topic != "securite"
    ]
