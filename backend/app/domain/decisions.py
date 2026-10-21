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


# ------------------------------------------------------------------ « oui, non, ou dans combien de temps ? »
@dataclass(frozen=True)
class SavingPath:
    label: str
    months: int | None  # None : hors de portée à ce rythme
    affordable_on: date | None
    monthly_cents: int
    note: str


@dataclass(frozen=True)
class PurchasePlan:
    answer: str  # oui | oui_mais | attendre | plus_tard | hors_de_portee
    headline: str
    amount_cents: int
    this_month: PurchaseVerdict
    from_savings_cents: int  # part qu'on peut prendre sur l'épargne au-delà du matelas, sans y toucher
    paths: list[SavingPath]
    explanation: str


def plan_purchase(
    run: RunForecast,
    *,
    today: date,
    amount_cents: int,
    label: str,
    buffer_cents: int,
    monthly_capacity_cents: int,
    committed_monthly_cents: int = 0,
    liquid_savings_cents: int = 0,
    emergency_target_cents: int = 0,
    category: str | None = None,
    horizon_months: int = 36,
) -> PurchasePlan:
    """Peut-on se l'offrir ? Ce mois-ci (prévision jour par jour), sinon en combien de mois d'épargne.

    Deux chemins honnêtes quand il faut attendre : « matelas d'abord » (on finit le matelas de sécurité, puis on
    épargne pour l'achat) et « au plus vite » (on suspend le matelas). L'épargne au-delà du matelas compte comme
    disponible ; le matelas, jamais.
    """
    now = evaluate_purchase(
        run,
        today=today,
        amount_cents=amount_cents,
        on=None,
        label=label,
        buffer_cents=buffer_cents,
        category=category,
        monthly_savings_cents=monthly_capacity_cents if monthly_capacity_cents > 0 else None,
    )
    spare_savings = max(0, liquid_savings_cents - emergency_target_cents)
    emergency_gap = max(0, emergency_target_cents - liquid_savings_cents)
    free_monthly = max(0, monthly_capacity_cents - committed_monthly_cents)
    if now.verdict == "ok":
        return PurchasePlan("oui", "Oui, tu peux l'acheter maintenant.", amount_cents, now, 0, [], now.explanation)
    if now.verdict == "serre":
        return PurchasePlan(
            "oui_mais",
            "Oui, mais ça entame ton coussin de sécurité ce mois-ci"
            + (f" : attendre le {now.best_date:%d/%m} serait plus confortable." if now.best_date else "."),
            amount_cents,
            now,
            0,
            [],
            now.explanation,
        )
    if now.verdict == "attendre" and now.best_date is not None:
        return PurchasePlan(
            "attendre",
            f"Oui, à partir du {now.best_date:%d/%m} : avant, ton compte passerait dans le rouge.",
            amount_cents,
            now,
            0,
            [],
            now.explanation,
        )

    # Pas ce mois-ci. L'épargne au-delà du matelas peut-elle couvrir l'achat ?
    if spare_savings >= amount_cents:
        return PurchasePlan(
            "oui_mais",
            f"Oui, en prenant {format_eur(amount_cents)} sur ton épargne disponible : "
            "ton matelas de sécurité reste intact.",
            amount_cents,
            now,
            amount_cents,
            [],
            now.explanation + " Ton épargne au-delà du matelas couvre l'achat.",
        )
    need = amount_cents - spare_savings

    def path(label_: str, total: int, monthly: int, note: str) -> SavingPath:
        if monthly <= 0:
            return SavingPath(label_, None, None, 0, "Aucune capacité d'épargne libre aujourd'hui.")
        months = -(-total // monthly)
        if months > horizon_months:
            return SavingPath(label_, None, None, monthly, f"Plus de {horizon_months // 12} ans à ce rythme.")
        return SavingPath(label_, months, _month_start(today, months), monthly, note)

    paths = [
        path(
            "Matelas d'abord (conseillé)" if emergency_gap else "En épargnant chaque mois",
            emergency_gap + need,
            free_monthly,
            f"Tu complètes d'abord ton matelas ({format_eur(emergency_gap)}), puis tu mets de côté pour l'achat."
            if emergency_gap
            else "Tu mets de côté ta capacité d'épargne libre chaque mois.",
        )
    ]
    if emergency_gap:
        paths.append(path("Au plus vite", need, free_monthly, "Tu suspends le matelas le temps de l'achat."))
    best = next((p for p in paths if p.months is not None), None)
    if best is None:
        headline = "Pas raisonnable aujourd'hui : il faut d'abord dégager de la marge chaque mois."
        answer = "hors_de_portee"
    else:
        headline = (
            f"Pas maintenant. Achetable vers {_month_name(best.affordable_on)} "
            f"({best.months} mois) en mettant {format_eur(best.monthly_cents)} de côté par mois."
        )
        answer = "plus_tard"
    detail = now.explanation
    if spare_savings:
        detail += f" {format_eur(spare_savings)} d'épargne disponible au-delà du matelas comptent déjà."
    return PurchasePlan(answer, headline, amount_cents, now, spare_savings, paths, detail)


_MONTHS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]


def _month_start(today: date, months: int) -> date:
    y, m = divmod(today.month - 1 + months, 12)
    return date(today.year + y, m + 1, 1)


def _month_name(d: date | None) -> str:
    return f"{_MONTHS_FR[d.month - 1]} {d.year}" if d else "?"
