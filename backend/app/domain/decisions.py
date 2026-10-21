"""Aide à la décision : « puis-je me permettre X ? » et « et si j'épargnais Y par mois ? ».

Principe : le modèle de langage raisonne, Bank calcule. Un agent ne doit jamais faire de l'arithmétique
de trésorerie de tête : il appelle ces fonctions, qui rejouent la vraie prévision jour par jour avec
l'hypothèse ajoutée, et renvoient un verdict chiffré et explicable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.domain.forecast import MonthForecast, UpcomingEvent, month_bounds
from app.domain.money import format_eur
from app.domain.wealth import months_to_goal

RunForecast = Callable[[Sequence[UpcomingEvent]], MonthForecast]


@dataclass(frozen=True)
class Snapshot:
    per_day_cents: int
    lowest_cents: int
    lowest_date: date
    overdraft_date: date | None
    end_balance_cents: int

    @staticmethod
    def of(f: MonthForecast) -> Snapshot:
        return Snapshot(
            f.safe_to_spend_per_day_cents,
            f.lowest_balance_cents,
            f.lowest_balance_date,
            f.overdraft_date,
            f.projected_end_balance_cents,
        )


@dataclass(frozen=True)
class EnvelopeImpact:
    slug: str
    budget_cents: int
    spent_cents: int
    spent_after_cents: int
    over_after: bool


@dataclass(frozen=True)
class PurchaseVerdict:
    verdict: str  # ok | serre | attendre | deconseille
    amount_cents: int
    on: date
    before: Snapshot
    after: Snapshot
    best_date: date | None
    envelope: EnvelopeImpact | None
    savings_months_equivalent: float | None
    explanation: str


def evaluate_purchase(
    run: RunForecast,
    *,
    today: date,
    amount_cents: int,
    on: date | None,
    label: str,
    buffer_cents: int,
    category: str | None = None,
    monthly_savings_cents: int | None = None,
) -> PurchaseVerdict:
    if amount_cents <= 0:
        raise ValueError("Le montant doit être positif")
    on = max(on or today, today)
    _, end = month_bounds(today)
    base = run(())
    target_day = min(on, end)

    def with_purchase(d: date) -> MonthForecast:
        return run((UpcomingEvent(d, label, -amount_cents, category or "divers", "fixed", 1.0, late=False),))

    after_f = with_purchase(target_day)
    before, after = Snapshot.of(base), Snapshot.of(after_f)

    best: date | None = None
    if after.lowest_cents < buffer_cents:
        d = target_day
        while d <= end:
            if Snapshot.of(with_purchase(d)).lowest_cents >= buffer_cents:
                best = d
                break
            d += timedelta(days=1)

    if after.lowest_cents >= buffer_cents and after.per_day_cents >= 0.5 * max(1, base.daily_variable_rate_cents):
        verdict = "ok"
    elif after.lowest_cents >= 0:
        verdict = "serre"
    elif best is not None:
        verdict = "attendre"
    else:
        verdict = "deconseille"

    envelope = None
    if category:
        line = next((b for b in base.budgets if b.slug == category), None)
        if line:
            spent_after = line.spent_cents + amount_cents
            envelope = EnvelopeImpact(
                line.slug, line.budget_cents, line.spent_cents, spent_after, spent_after > line.budget_cents
            )

    equiv = round(amount_cents / monthly_savings_cents, 1) if monthly_savings_cents else None
    explanation = _explain(verdict, amount_cents, before, after, best, envelope, equiv, beyond=on > end)
    return PurchaseVerdict(verdict, amount_cents, on, before, after, best, envelope, equiv, explanation)


def _explain(
    verdict: str,
    amount: int,
    before: Snapshot,
    after: Snapshot,
    best: date | None,
    envelope: EnvelopeImpact | None,
    equiv: float | None,
    *,
    beyond: bool,
) -> str:
    parts = []
    if beyond:
        parts.append("Achat prévu après ce mois : impact estimé sur la fin du mois en cours.")
    parts.append(
        f"Reste à dépenser : {format_eur(before.per_day_cents)}/jour avant, "
        f"{format_eur(after.per_day_cents)}/jour après."
    )
    parts.append(f"Point bas du mois : {format_eur(after.lowest_cents)} le {after.lowest_date:%d/%m}.")
    if after.overdraft_date:
        parts.append(f"Découvert prévu le {after.overdraft_date:%d/%m}.")
    if verdict == "attendre" and best:
        parts.append(f"Sans risque à partir du {best:%d/%m}.")
    if envelope and envelope.over_after:
        parts.append(
            f"L'enveloppe {envelope.slug} serait dépassée ({format_eur(envelope.spent_after_cents)} "
            f"sur {format_eur(envelope.budget_cents)})."
        )
    if equiv:
        parts.append(f"Cela représente {equiv} mois de ta capacité d'épargne.")
    return " ".join(parts)


@dataclass(frozen=True)
class GoalImpact:
    label: str
    months_before: int | None
    months_after: int | None


@dataclass(frozen=True)
class SavingsWhatIf:
    monthly_cents: int
    per_day_now_cents: int
    per_day_after_cents: int
    free_now_cents: int  # budget libre sur le reste du mois
    free_after_cents: int
    feasible_this_month: bool
    emergency_months_now: float
    emergency_reached_in_months: int | None
    goals: list[GoalImpact]
    explanation: str


def what_if_savings(
    run_with_goal: Callable[[int], MonthForecast],
    *,
    monthly_cents: int,
    emergency_available_cents: int,
    emergency_target_cents: int,
    monthly_essentials_cents: int,
    goals: Sequence[tuple[str, int, int, int]],  # (libellé, actuel, cible, versement actuel)
    rate_percent: Decimal = Decimal("1.7"),
) -> SavingsWhatIf:
    base = run_with_goal(0)
    after = run_with_goal(monthly_cents)
    em_now = round(emergency_available_cents / monthly_essentials_cents, 1) if monthly_essentials_cents else 0.0
    reached = months_to_goal(emergency_available_cents, emergency_target_cents, monthly_cents, rate_percent)
    impacts = [
        GoalImpact(
            label,
            months_to_goal(cur, tgt, monthly, rate_percent),
            months_to_goal(cur, tgt, monthly + monthly_cents, rate_percent),
        )
        for label, cur, tgt, monthly in goals
    ]
    # Prudent : virer l'épargne au point bas du mois ne doit pas faire passer sous zéro.
    feasible = base.lowest_balance_cents - monthly_cents >= 0
    text = (
        f"Mettre {format_eur(monthly_cents)} de côté chaque mois ramène le budget libre du mois de "
        f"{format_eur(base.safe_to_spend_cents)} à {format_eur(after.safe_to_spend_cents)}. "
    )
    if after.safe_to_spend_per_day_cents == base.safe_to_spend_per_day_cents:
        # Le plafond du jour vient de la trésorerie avant la prochaine rentrée : l'épargne se fera après.
        today_cap = format_eur(base.safe_to_spend_per_day_cents)
        text += f"Le reste à dépenser d'aujourd'hui ne change pas ({today_cap}/jour). "
    else:
        text += (
            f"Reste à dépenser : {format_eur(base.safe_to_spend_per_day_cents)} -> "
            f"{format_eur(after.safe_to_spend_per_day_cents)} par jour. "
        )
    text += (
        "Le matelas de sécurité est déjà constitué."
        if reached == 0
        else f"Matelas atteint dans {reached} mois."
        if reached
        else "Matelas hors d'atteinte à ce rythme."
    )
    if not feasible:
        text += " Attention : ce mois-ci, cela creuserait un découvert."
    return SavingsWhatIf(
        monthly_cents,
        base.safe_to_spend_per_day_cents,
        after.safe_to_spend_per_day_cents,
        base.safe_to_spend_cents,
        after.safe_to_spend_cents,
        feasible,
        em_now,
        reached,
        impacts,
        text,
    )
