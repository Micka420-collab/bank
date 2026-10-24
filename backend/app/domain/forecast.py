"""Prévision de fin de mois, « reste à dépenser par jour » et suivi des enveloppes.

Le cœur : une simulation jour par jour du solde jusqu'à la fin du mois, qui applique
les échéances récurrentes attendues (salaire, loyer, abonnements, aides) et la dépense variable
habituelle. On en déduit le point bas du mois et la date du premier découvert éventuel —
bien plus utile qu'un simple solde projeté de fin de mois, qui peut masquer un découvert le 25.
"""

from __future__ import annotations

import calendar
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from app.domain.recurring import RecurringSeries, Txn, add_months
from app.domain.territory import next_business_day

Day = date  # le champ « date » des dataclasses ci-dessous masque le type


@dataclass(frozen=True)
class CategoryInfo:
    slug: str
    name: str
    kind: str
    budget_cents: int | None = None


@dataclass(frozen=True)
class UpcomingEvent:
    date: date
    label: str
    amount_cents: int
    category: str
    kind: str  # income | fixed
    confidence: float
    late: bool
    shifted_from: Day | None = None  # prélèvement décalé au jour ouvré suivant (dimanche, jour férié)


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str


@dataclass(frozen=True)
class BudgetLine:
    slug: str
    name: str
    budget_cents: int
    spent_cents: int
    remaining_cents: int
    pace: float  # 1.0 = au rythme ; >1 = en avance sur la dépense
    status: str  # ok | warning | over


@dataclass(frozen=True)
class ProjectionPoint:
    date: date
    balance_cents: int


@dataclass(frozen=True)
class AllowanceSegment:
    start: date
    end: date
    per_day_cents: int
    reason: str | None  # libellé de l'échéance qui contraint ce palier (ex. arrivée du salaire)


@dataclass(frozen=True)
class MonthForecast:
    month: str
    today: date
    days_in_month: int
    days_left: int
    current_balance_cents: int
    income_received_cents: int
    spent_fixed_cents: int
    spent_variable_cents: int
    saved_cents: int
    expected_income_cents: int
    expected_fixed_cents: int
    expected_variable_cents: int
    projected_end_balance_cents: int
    safe_to_spend_cents: int
    safe_to_spend_per_day_cents: int
    daily_variable_rate_cents: int
    lowest_balance_cents: int
    lowest_balance_date: date
    overdraft_date: date | None
    status: str  # ok | tight | risk
    upcoming: list[UpcomingEvent] = field(default_factory=list)
    projection: list[ProjectionPoint] = field(default_factory=list)
    budgets: list[BudgetLine] = field(default_factory=list)
    history: list[ProjectionPoint] = field(default_factory=list)  # solde réel en fin de journée, du 1er à hier
    allowance: list[AllowanceSegment] = field(default_factory=list)  # paliers de « reste à dépenser par jour »
    holidays: list[Holiday] = field(default_factory=list)  # jours fériés du mois (calendrier public, avec ton accord)


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _kind(categories: Mapping[str, CategoryInfo], slug: str) -> str:
    info = categories.get(slug)
    return info.kind if info else "uncategorized"


def is_planned_outflow(series: RecurringSeries, categories: Mapping[str, CategoryInfo]) -> bool:
    """Une sortie récurrente compte comme « charge fixe » si sa catégorie est fixe/épargne,
    ou si elle n'est pas catégorisée mais a un montant stable. Les courses hebdomadaires au même
    supermarché restent de la dépense variable (sinon double comptage avec le rythme quotidien)."""
    kind = _kind(categories, series.category)
    return kind in ("fixed", "savings") or (kind == "uncategorized" and not series.variable)


def _occurrences(series: RecurringSeries, today: date, until: date) -> list[tuple[date, bool]]:
    out: list[tuple[date, bool]] = []
    d = series.next_date
    if d < today:  # attendu mais pas encore arrivé : on le place aujourd'hui
        out.append((today, series.late))
        d = _advance(series, d)
    guard = 0
    while d <= until and guard < 60:
        out.append((d, False))
        d = _advance(series, d)
        guard += 1
    return out


def _advance(series: RecurringSeries, d: date) -> date:
    if series.cadence == "monthly":
        return add_months(d, 1, series.day_of_month)
    if series.cadence == "quarterly":
        return add_months(d, 3, series.day_of_month)
    if series.cadence == "yearly":
        return add_months(d, 12, series.day_of_month)
    return d + timedelta(days=round(series.period_days))


def daily_variable_rate(
    txns: Sequence[Txn],
    today: date,
    categories: Mapping[str, CategoryInfo],
    planned_merchants: set[str],
    window_days: int = 90,
) -> int:
    """Dépense variable moyenne par jour sur la fenêtre, hors charges planifiées, virements internes
    et achats exceptionnels (un frigo à 800 € ne doit pas gonfler le rythme quotidien)."""
    start = today - timedelta(days=window_days)
    amounts: list[int] = []
    first_seen: date | None = None
    for t in txns:
        if not (start <= t.date < today) or t.is_transfer or t.amount_cents >= 0:
            continue
        first_seen = t.date if first_seen is None or t.date < first_seen else first_seen
        if t.merchant in planned_merchants:
            continue
        if _kind(categories, t.category) in ("variable", "uncategorized"):
            amounts.append(-t.amount_cents)
    if not amounts or first_seen is None:
        return 0
    typical = median(amounts)
    threshold = max(15_000, 5 * typical)
    kept = [a for a in amounts if a <= threshold]
    days = max(14, (today - first_seen).days)
    return round(sum(kept) / min(days, window_days))


def build_forecast(
    *,
    today: date,
    txns: Sequence[Txn],
    current_balance_cents: int,
    series: Sequence[RecurringSeries],
    categories: Mapping[str, CategoryInfo],
    savings_goal_cents: int = 0,
    buffer_cents: int = 0,
    extra_events: Sequence[UpcomingEvent] = (),
    holidays: Mapping[date, str] | None = None,
) -> MonthForecast:
    """`extra_events` : dépenses planifiées (onboarding) ou hypothétiques (« et si j'achète ça ? »).
    `holidays` : jours fériés connus (calendrier public) ; avec eux, un prélèvement qui tombe un samedi, un dimanche
    ou un jour férié part le jour ouvré suivant, éventuellement le mois prochain. Sans eux, rien n'est décalé."""
    start, end = month_bounds(today)
    days_in_month = end.day
    days_left = (end - today).days + 1

    income = fixed = variable = saved = 0
    spent_by_cat: dict[str, int] = defaultdict(int)
    for t in txns:
        if not (start <= t.date <= today):
            continue
        if t.is_transfer:
            saved -= t.amount_cents  # net des virements internes : sortie vers l'épargne = +épargne
            continue
        kind = _kind(categories, t.category)
        if t.amount_cents > 0:
            income += t.amount_cents
        elif kind == "fixed":
            fixed -= t.amount_cents
        elif kind == "savings":
            saved -= t.amount_cents
        else:
            variable -= t.amount_cents
        if t.amount_cents < 0:
            spent_by_cat[t.category] -= t.amount_cents

    planned = [s for s in series if s.direction == "in" or is_planned_outflow(s, categories)]
    planned_merchants = {s.merchant for s in planned if s.direction == "out"}
    rate = daily_variable_rate(txns, today, categories, planned_merchants)

    events: list[UpcomingEvent] = []
    for s in planned:
        for d, late in _occurrences(s, today, end):
            nominal = d
            if holidays is not None and s.direction == "out" and not late:
                d = next_business_day(d, holidays)
            if d > end:
                continue  # décalé au mois suivant : il compte là-bas
            events.append(
                UpcomingEvent(
                    date=d,
                    label=s.merchant,
                    amount_cents=s.amount_cents,
                    category=s.category,
                    kind="income" if s.direction == "in" else "fixed",
                    confidence=s.confidence,
                    late=late,
                    shifted_from=nominal if d != nominal else None,
                )
            )
    events.extend(e for e in extra_events if today <= e.date <= end)
    events.sort(key=lambda e: (e.date, -e.amount_cents))
    expected_income = sum(e.amount_cents for e in events if e.amount_cents > 0)
    expected_fixed = sum(e.amount_cents for e in events if e.amount_cents < 0)

    by_day: dict[date, int] = defaultdict(int)
    for e in events:
        by_day[e.date] += e.amount_cents
    balance = current_balance_cents
    projection: list[ProjectionPoint] = []
    lowest, lowest_date, overdraft = math.inf, today, None
    d = today
    while d <= end:
        if d > today:
            balance -= rate
        balance += by_day.get(d, 0)
        projection.append(ProjectionPoint(d, balance))
        if balance < lowest:
            lowest, lowest_date = balance, d
        if balance < 0 and overdraft is None:
            overdraft = d
        d += timedelta(days=1)

    history = _reconstruct_history(txns, start, today, current_balance_cents)
    goal_remaining = max(0, savings_goal_cents - max(0, saved))
    safe = current_balance_cents + expected_income + expected_fixed - goal_remaining - buffer_cents
    allowance = allowance_schedule(
        today=today,
        end=end,
        balance_cents=current_balance_cents,
        events=events,
        buffer_cents=buffer_cents,
        reserve_at_end_cents=goal_remaining,
    )
    per_day = allowance[0].per_day_cents if allowance else 0
    lowest_int = int(lowest)
    if lowest_int < 0:
        status = "risk"
    elif lowest_int < buffer_cents or (rate > 0 and per_day < 0.6 * rate):
        status = "tight"
    else:
        status = "ok"

    budgets: list[BudgetLine] = []
    elapsed = today.day / days_in_month
    for info in sorted(categories.values(), key=lambda c: c.name):
        if not info.budget_cents:
            continue
        spent = spent_by_cat.get(info.slug, 0)
        pace = spent / (info.budget_cents * elapsed) if elapsed > 0 else 0.0
        st = "over" if spent > info.budget_cents else "warning" if pace > 1.1 else "ok"
        budgets.append(
            BudgetLine(info.slug, info.name, info.budget_cents, spent, info.budget_cents - spent, round(pace, 2), st)
        )

    return MonthForecast(
        month=f"{today.year:04d}-{today.month:02d}",
        today=today,
        days_in_month=days_in_month,
        days_left=days_left,
        current_balance_cents=current_balance_cents,
        income_received_cents=income,
        spent_fixed_cents=fixed,
        spent_variable_cents=variable,
        saved_cents=saved,
        expected_income_cents=expected_income,
        expected_fixed_cents=expected_fixed,
        expected_variable_cents=-rate * (days_left - 1),
        projected_end_balance_cents=balance,
        safe_to_spend_cents=safe,
        safe_to_spend_per_day_cents=per_day,
        daily_variable_rate_cents=rate,
        lowest_balance_cents=lowest_int,
        lowest_balance_date=lowest_date,
        overdraft_date=overdraft,
        status=status,
        upcoming=events,
        projection=projection,
        budgets=budgets,
        history=history,
        allowance=allowance,
        holidays=[Holiday(d, n) for d, n in sorted((holidays or {}).items()) if start <= d <= end],
    )


def allowance_schedule(
    *,
    today: date,
    end: date,
    balance_cents: int,
    events: Sequence[UpcomingEvent],
    buffer_cents: int,
    reserve_at_end_cents: int = 0,
) -> list[AllowanceSegment]:
    """Reste à dépenser par jour, contraint par la trésorerie — pas seulement par le total du mois.

    Diviser « solde + salaire à venir - charges » par le nombre de jours restants suppose que le salaire
    du 28 est déjà là : dépenser ce montant dès le 21 mène au découvert. On calcule au contraire le
    calendrier de dépense quotidienne le plus généreux qui garde, CHAQUE jour, le solde au-dessus du
    coussin : c'est la « corde tendue » (enveloppe convexe inférieure) des disponibilités cumulées.
    Résultat : une suite de paliers croissants, ex. 75 €/j jusqu'au salaire, puis 330 €/j.

    Disponible fin du jour j : A(j) = solde + échéances(<= j) - coussin (- réserve d'épargne au dernier jour).
    Palier courant depuis i : s = min_j (A(j) - déjà_dépensé) / (j - i + 1), le j qui réalise le min clôt le palier.
    """
    days: list[date] = []
    d = today
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    if not days:
        return []
    flows: dict[date, int] = defaultdict(int)
    labels: dict[date, str] = {}
    for e in events:
        flows[e.date] += e.amount_cents
        if e.amount_cents > 0:
            labels.setdefault(e.date, e.label)
    available: list[int] = []
    running = balance_cents - buffer_cents
    for day in days:
        running += flows.get(day, 0)
        available.append(running)
    available[-1] -= reserve_at_end_cents

    segments: list[AllowanceSegment] = []
    i, spent = 0, 0
    while i < len(days):
        best_rate, best_j = None, i
        for j in range(i, len(days)):
            rate = (available[j] - spent) / (j - i + 1)
            # À égalité, on prend le palier le plus long : moins de marches, même sécurité.
            if best_rate is None or rate <= best_rate:
                best_rate, best_j = rate, j
        per_day = max(0, math.floor(best_rate or 0))
        nxt = days[best_j + 1] if best_j + 1 < len(days) else None
        reason = labels.get(nxt) if nxt else None
        segments.append(AllowanceSegment(days[i], days[best_j], per_day, reason))
        spent += per_day * (best_j - i + 1)
        i = best_j + 1
    return segments


def _reconstruct_history(txns: Sequence[Txn], start: date, today: date, balance_today: int) -> list[ProjectionPoint]:
    """Remonte le temps depuis le solde actuel : solde fin du jour J-1 = solde fin du jour J - flux du jour J.
    Donne la vraie courbe du mois écoulé sans stocker de soldes quotidiens."""
    flows: dict[date, int] = defaultdict(int)
    for t in txns:
        if start < t.date <= today:
            flows[t.date] += t.amount_cents
    points: list[ProjectionPoint] = []
    bal, d = balance_today, today
    while d > start:
        bal -= flows.get(d, 0)
        d -= timedelta(days=1)
        points.append(ProjectionPoint(d, bal))
    points.reverse()
    return points


def suggest_budgets(
    txns: Sequence[Txn], today: date, categories: Mapping[str, CategoryInfo], months: int = 3
) -> dict[str, int]:
    """Budget suggéré par catégorie variable = médiane des N derniers mois complets, arrondie aux 5 € sup."""
    first_month = add_months(today.replace(day=1), -months)
    current_start = today.replace(day=1)
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in txns:
        if t.is_transfer or t.amount_cents >= 0 or not (first_month <= t.date < current_start):
            continue
        if _kind(categories, t.category) != "variable":
            continue
        totals[t.category][f"{t.date.year}-{t.date.month:02d}"] -= t.amount_cents
    out: dict[str, int] = {}
    for slug, per_month in totals.items():
        values = [
            per_month.get(f"{m.year}-{m.month:02d}", 0) for m in (add_months(first_month, i) for i in range(months))
        ]
        med = median(values)
        if med > 0:
            out[slug] = int(math.ceil(med / 500) * 500)
    return out
