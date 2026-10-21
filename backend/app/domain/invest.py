"""« Où placer mon argent ? » — un plan de placement calculé, par ordre de priorité, à partir de TA situation.

Principes (les mêmes que ceux du cadre de décision de l'agent) :
1. pas de découvert ; 2. le matelas de sécurité, sur les livrets les mieux rémunérés auxquels tu as droit ;
3. la dette chère, qui coûte plus que ce qu'un placement rapporte ; 4. les projets datés, sans risque ;
5. le long terme, seulement quand 1 à 4 sont en ordre, avec une part d'actions qui dépend de ton profil et de ton
horizon, et des enveloppes choisies pour leur fiscalité (PEA, assurance vie, PER si ta tranche d'impôt le justifie).

Bank donne des ordres de grandeur et des enveloppes, jamais un produit commercial ni une promesse de rendement.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from importlib import resources
from typing import Any

from app.domain.money import format_eur
from app.domain.wealth import ProductStatus


# ------------------------------------------------------------------ barème et repères
@dataclass(frozen=True)
class TaxBracket:
    up_to_cents: int | None
    rate_percent: int


@dataclass(frozen=True)
class References:
    income_tax_year: int
    income_tax_incomes_year: int
    income_tax_source: str
    brackets: tuple[TaxBracket, ...]
    pea_ceiling_cents: int
    pea_tax_free_after_years: int
    av_abattement_after_years: int
    av_abattement_single_cents: int
    social_levies_percent: Decimal
    expensive_debt_rate_percent: Decimal
    equity_share_by_profile: Mapping[str, int]
    source: str


def load_references() -> References:
    raw: dict[str, Any] = json.loads(resources.files("app.domain").joinpath("rates.json").read_text("utf-8"))
    tax, inv = raw["income_tax"], raw["investing"]
    return References(
        income_tax_year=int(tax["year"]),
        income_tax_incomes_year=int(tax["incomes_year"]),
        income_tax_source=str(tax["source"]),
        brackets=tuple(TaxBracket(b["up_to_cents"], int(b["rate_percent"])) for b in tax["brackets"]),
        pea_ceiling_cents=int(inv["pea_ceiling_cents"]),
        pea_tax_free_after_years=int(inv["pea_tax_free_after_years"]),
        av_abattement_after_years=int(inv["av_abattement_after_years"]),
        av_abattement_single_cents=int(inv["av_abattement_single_cents"]),
        social_levies_percent=Decimal(str(inv["social_levies_percent"])),
        expensive_debt_rate_percent=Decimal(str(inv["expensive_debt_rate_percent"])),
        equity_share_by_profile=dict(inv["equity_share_by_profile"]),
        source=str(inv["source"]),
    )


# ------------------------------------------------------------------ impôt : tranche marginale
@dataclass(frozen=True)
class TaxEstimate:
    marginal_rate_percent: int
    source: str  # declared | document | estimated | unknown
    taxable_income_cents: int | None
    parts: Decimal | None
    detail: str


def household_parts(*, couple: bool, children: int, single_parent: bool = False) -> Decimal:
    """Parts fiscales : 1 (ou 2 en couple), + 0,5 par enfant pour les deux premiers, + 1 au-delà ;
    parent isolé : le premier enfant compte une part entière."""
    parts = Decimal(2 if couple else 1)
    for i in range(children):
        parts += Decimal(1) if i >= 2 else Decimal("0.5")
    if single_parent and children:
        parts += Decimal("0.5")
    return parts


def marginal_rate(taxable_income_cents: int, parts: Decimal, refs: References) -> int:
    quotient = Decimal(taxable_income_cents) / parts
    for b in refs.brackets:
        if b.up_to_cents is None or quotient <= b.up_to_cents:
            return b.rate_percent
    return refs.brackets[-1].rate_percent


def income_tax(taxable_income_cents: int, parts: Decimal, refs: References) -> int:
    """Impôt brut par le barème (sans décote ni réductions) : un ordre de grandeur, pas l'avis."""
    quotient = Decimal(taxable_income_cents) / parts
    tax = Decimal(0)
    lower = Decimal(0)
    for b in refs.brackets:
        upper = Decimal(b.up_to_cents) if b.up_to_cents is not None else quotient
        if quotient > lower:
            tax += (min(quotient, upper) - lower) * b.rate_percent / 100
        lower = upper
    return int((tax * parts).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def estimate_tax(
    *,
    declared_bracket: str | None,
    document_taxable_cents: int | None,
    document_parts: Decimal | None,
    net_monthly_income_cents: int | None,
    yearly_extras_cents: int | None,
    couple: bool,
    children: int,
    single_parent: bool,
    refs: References,
) -> TaxEstimate:
    """Priorité : ce que tu as déclaré > ton avis d'impôt importé > une estimation à partir de ton salaire."""
    if declared_bracket and declared_bracket.isdigit():
        return TaxEstimate(int(declared_bracket), "declared", None, None, "Tranche que tu as indiquée.")
    parts = document_parts or household_parts(couple=couple, children=children, single_parent=single_parent)
    if document_taxable_cents is not None:
        rate = marginal_rate(document_taxable_cents, parts, refs)
        return TaxEstimate(rate, "document", document_taxable_cents, parts, "D'après ton avis d'impôt importé.")
    if net_monthly_income_cents:
        # Le net imposable est un peu supérieur au net perçu (CSG non déductible) : on majore de 3 %.
        taxable = int((net_monthly_income_cents * 12 + (yearly_extras_cents or 0)) * 1.03)
        rate = marginal_rate(taxable, parts, refs)
        return TaxEstimate(rate, "estimated", taxable, parts, "Estimation à partir de ton salaire net : à confirmer.")
    return TaxEstimate(0, "unknown", None, None, "Tranche inconnue : réponds à la question ou importe ton avis.")


# ------------------------------------------------------------------ lignes rouges
_RED_LINE_RULES: tuple[tuple[str, str], ...] = (
    ("crypto", r"crypto|bitcoin|btc"),
    ("actions", r"\bbourse\b|\bactions?\b|\betf\b|\bpea\b|march[eé]s? financiers?"),
    ("assurance_vie", r"assurance[\s-]?vie|\bav\b"),
    ("per", r"\bper\b|plan [eé]pargne retraite"),
    ("immobilier", r"immobilier|\bscpi\b|pierre"),
    ("credit", r"cr[eé]dit|emprunt|dette"),
)


def excluded_by_red_lines(red_lines: Sequence[str]) -> set[str]:
    text = " ".join(r.lower() for r in red_lines)
    return {name for name, pattern in _RED_LINE_RULES if re.search(pattern, text)}


# ------------------------------------------------------------------ entrées et sorties
@dataclass(frozen=True)
class DebtLine:
    label: str
    remaining_cents: int
    monthly_cents: int
    rate_percent: Decimal


@dataclass(frozen=True)
class ProjectLine:
    label: str
    amount_cents: int
    target_date: date | None
    priority: int  # 1 indispensable … 3 si possible
    saved_cents: int = 0


@dataclass(frozen=True)
class PlanInputs:
    today: date
    monthly_capacity_cents: int
    liquid_savings_cents: int
    emergency_target_cents: int
    monthly_essentials_cents: int
    debts: Sequence[DebtLine]
    projects: Sequence[ProjectLine]
    regulated: Sequence[ProductStatus]
    lep_eligible: bool | None
    risk_label: str  # prudent | equilibre | dynamique | inconnu
    risk_horizon: str | None  # moins_2 | 2_5 | 5_10 | plus_10
    red_lines: Sequence[str]
    tax: TaxEstimate
    age: int | None
    overdraft: bool = False
    outside_assets: Mapping[str, int] = field(default_factory=dict)  # kind -> valeur (pea, assurance_vie, per…)


@dataclass(frozen=True)
class PlanStep:
    order: int
    kind: str  # urgence | matelas | dette | projet | long_terme | attente
    title: str
    monthly_cents: int
    where: str
    why: str
    months: int | None
    target_cents: int | None


@dataclass(frozen=True)
class Allocation:
    vehicle: str  # pea | assurance_vie | per | livret | fonds_euros
    label: str
    share_percent: int
    monthly_cents: int
    note: str


@dataclass(frozen=True)
class InvestmentPlan:
    monthly_capacity_cents: int
    allocated_cents: int
    steps: list[PlanStep]
    long_term: list[Allocation]
    long_term_monthly_cents: int
    equity_share_percent: int
    horizon_years: int | None
    tax: TaxEstimate
    excluded: list[str]
    cautions: list[str]
    summary: str
    references: str


# ------------------------------------------------------------------ le plan
def _months(total: int, monthly: int) -> int | None:
    return -(-total // monthly) if monthly > 0 and total > 0 else (0 if total <= 0 else None)


def _livret_route(regulated: Sequence[ProductStatus], lep_eligible: bool | None) -> str:
    """Où loger le matelas : le LEP d'abord si tu y as droit, puis Livret A et LDDS, dans la limite des plafonds."""
    by = {p.kind: p for p in regulated}
    parts: list[str] = []
    if lep_eligible and "lep" in by and by["lep"].room_cents > 0:
        parts.append(f"LEP ({by['lep'].rate_percent} %, encore {format_eur(by['lep'].room_cents)} de place)")
    elif lep_eligible and "lep" not in by:
        parts.append("LEP à ouvrir (le livret réglementé le mieux rémunéré, tu y as droit)")
    for kind in ("livret_a", "ldds"):
        p = by.get(kind)
        if p is not None and p.room_cents > 0:
            parts.append(f"{p.name} ({p.rate_percent} %, encore {format_eur(p.room_cents)} de place)")
    return " puis ".join(parts) if parts else "Livret A / LDDS (livrets réglementés, disponibles à tout moment)"


def _horizon_years(h: str | None) -> int | None:
    return {"moins_2": 1, "2_5": 3, "5_10": 7, "plus_10": 12}.get(h or "")


def build_plan(inp: PlanInputs, refs: References) -> InvestmentPlan:
    steps: list[PlanStep] = []
    cautions: list[str] = []
    capacity = max(0, inp.monthly_capacity_cents)
    left = capacity
    excluded = sorted(excluded_by_red_lines(inp.red_lines))
    order = 0

    def push(kind: str, title: str, monthly: int, where: str, why: str, months: int | None, target: int | None) -> None:
        nonlocal order, left
        order += 1
        left -= monthly
        steps.append(PlanStep(order, kind, title, monthly, where, why, months, target))

    # 0. Découvert : rien ne part en placement tant que le mois n'est pas sécurisé.
    if inp.overdraft:
        push(
            "urgence",
            "Sécuriser le mois en cours",
            0,
            "Compte courant",
            "Un découvert prévu coûte des agios et des frais : aucun virement d'épargne avant la prochaine paie.",
            None,
            None,
        )

    gap = max(0, inp.emergency_target_cents - inp.liquid_savings_cents)
    expensive = [d for d in inp.debts if d.rate_percent >= refs.expensive_debt_rate_percent and d.remaining_cents > 0]
    one_month = inp.monthly_essentials_cents

    # 1. Mini-matelas d'un mois si tu n'as rien devant toi : priorité absolue.
    if inp.liquid_savings_cents < one_month and left > 0:
        need = one_month - inp.liquid_savings_cents
        monthly = left
        push(
            "matelas",
            "Un premier mois de dépenses devant toi",
            monthly,
            _livret_route(inp.regulated, inp.lep_eligible),
            "Sans un mois d'avance, le moindre imprévu finit en découvert ou à crédit.",
            _months(need, monthly),
            one_month,
        )
        gap = max(0, inp.emergency_target_cents - one_month)

    # 2. Dette chère : rembourser rapporte « à coup sûr » son taux, davantage que n'importe quel livret.
    if expensive and left > 0:
        total = sum(d.remaining_cents for d in expensive)
        monthly = left if gap == 0 else (left * 7) // 10
        worst = max(expensive, key=lambda d: d.rate_percent)
        push(
            "dette",
            f"Rembourser par anticipation : {worst.label} ({worst.rate_percent} %)",
            monthly,
            "Remboursement anticipé (vérifie l'indemnité éventuelle dans le contrat)",
            f"Un crédit à {worst.rate_percent} % coûte plus que ce que rapporte un livret : chaque euro remboursé "
            "en avance est un placement garanti à ce taux.",
            _months(total, monthly + sum(d.monthly_cents for d in expensive)),
            total,
        )
        cautions.append(
            "Avant un remboursement anticipé, relis le contrat : indemnité plafonnée (crédit à la consommation : "
            "1 % ou 0,5 % du capital remboursé, nulle sous 10 000 € sur 12 mois ; immobilier : au plus 6 mois "
            "d'intérêts et 3 % du capital restant dû)."
        )

    # 3. Compléter le matelas.
    if gap > 0 and left > 0:
        monthly = left if not inp.projects else max(left // 2, min(left, gap // 6 or left))
        push(
            "matelas",
            f"Compléter le matelas de sécurité ({format_eur(inp.emergency_target_cents)} visés)",
            monthly,
            _livret_route(inp.regulated, inp.lep_eligible),
            "Disponible en un jour, sans risque, exonéré d'impôt : c'est ce qui te permet de dire non au crédit.",
            _months(gap, monthly),
            inp.emergency_target_cents,
        )

    # 4. Projets datés : sans risque, en priorité décroissante.
    for pr in sorted(inp.projects, key=lambda p: (p.priority, p.target_date or date.max)):
        if left <= 0:
            cautions.append(f"Projet « {pr.label} » : plus de marge mensuelle disponible pour l'instant.")
            continue
        remaining = max(0, pr.amount_cents - pr.saved_cents)
        if remaining == 0:
            continue
        months_to = (
            max(1, (pr.target_date.year - inp.today.year) * 12 + pr.target_date.month - inp.today.month)
            if pr.target_date
            else None
        )
        needed = -(-remaining // months_to) if months_to else min(left, remaining)
        monthly = min(left, needed)
        where = (
            "Livrets réglementés (horizon court : aucun risque)"
            if (months_to or 0) <= 24
            else "Livrets, compte à terme ou fonds euros d'assurance vie (capital garanti)"
        )
        why = (
            f"Échéance dans {months_to} mois : à moins de 5 ans, on ne place pas en actions "
            "un argent dont on aura besoin."
            if months_to
            else "Sans date : on provisionne à ton rythme, toujours sans risque."
        )
        push("projet", f"Projet : {pr.label}", monthly, where, why, _months(remaining, monthly), pr.amount_cents)
        if months_to and monthly < needed:
            cautions.append(
                f"« {pr.label} » : il faudrait {format_eur(needed)} par mois pour tenir la date, "
                f"tu peux y mettre {format_eur(monthly)}. Reporter, réduire, ou arbitrer avec un autre projet."
            )

    # 5. Long terme.
    horizon = _horizon_years(inp.risk_horizon)
    long_term: list[Allocation] = []
    equity = 0
    lt_monthly = max(0, left)
    ready = gap == 0 and not expensive and not inp.overdraft
    if not ready:
        push(
            "attente",
            "Long terme : pas encore",
            0,
            "—",
            "Le matelas et la dette chère passent avant tout placement de long terme : c'est l'ordre qui protège.",
            None,
            None,
        )
        lt_monthly = 0
    elif lt_monthly > 0:
        equity = int(refs.equity_share_by_profile.get(inp.risk_label, 30))
        if horizon is not None and horizon < 5:
            equity = 0
        elif horizon is not None and horizon < 8:
            equity = min(equity, 50)
        if inp.age is not None and inp.age >= 60:
            equity = min(equity, 40)
        if "actions" in excluded:
            equity = 0
        if inp.risk_label == "inconnu":
            equity = min(equity, 30)
            cautions.append("Profil de risque incomplet (questions « Rapport au risque ») : part d'actions plafonnée.")
        per_share = 0
        if inp.tax.marginal_rate_percent >= 30 and "per" not in excluded and (inp.age is None or inp.age < 58):
            per_share = 25
        av_ok = "assurance_vie" not in excluded
        equity_m = lt_monthly * equity // 100
        per_m = lt_monthly * per_share // 100
        safe_m = lt_monthly - equity_m - per_m
        if equity_m:
            long_term.append(
                Allocation(
                    "pea",
                    "PEA — fonds indiciel (ETF) monde ou Europe",
                    equity,
                    equity_m,
                    f"Actions pour le long terme, fiscalité allégée après {refs.pea_tax_free_after_years} ans "
                    f"(prélèvements sociaux {refs.social_levies_percent} % seulement). Plafond de versements "
                    f"{format_eur(refs.pea_ceiling_cents)}. Peut baisser fortement certaines années.",
                )
            )
        if per_m:
            long_term.append(
                Allocation(
                    "per",
                    "PER — versements déductibles",
                    per_share,
                    per_m,
                    f"Ta tranche à {inp.tax.marginal_rate_percent} % rend la déduction intéressante (plafond : "
                    "10 % des revenus). Argent bloqué jusqu'à la retraite, sauf achat de la résidence principale "
                    "ou accident de la vie ; imposé à la sortie.",
                )
            )
        if safe_m:
            long_term.append(
                Allocation(
                    "assurance_vie" if av_ok else "livret",
                    "Assurance vie — fonds euros (capital garanti)" if av_ok else "Livrets réglementés",
                    100 - equity - per_share,
                    safe_m,
                    f"Souple, transmissible, fiscalité allégée après {refs.av_abattement_after_years} ans "
                    f"(abattement annuel {format_eur(refs.av_abattement_single_cents)} sur les gains). "
                    "Choisis un contrat à frais bas, sans frais sur versement."
                    if av_ok
                    else "Sans risque et disponibles, mais rendement proche de l'inflation.",
                )
            )
        push(
            "long_terme",
            "Long terme : faire travailler l'argent",
            lt_monthly,
            " + ".join(a.label.split(" — ")[0] for a in long_term) or "Livrets",
            f"Matelas constitué, pas de dette chère : {equity} % d'actions pour un horizon "
            + (f"d'environ {horizon} ans" if horizon else "long")
            + f" et un profil {inp.risk_label}.",
            None,
            None,
        )

    if inp.lep_eligible and "lep" not in {p.kind for p in inp.regulated}:
        cautions.append("Tu es éligible au LEP et tu n'en as pas : c'est le meilleur taux garanti disponible.")
    cautions.append(
        "Repères généraux, pas un conseil en investissement personnalisé : les rendements passés ne préjugent "
        "pas des rendements futurs, et seul un professionnel agréé peut te recommander un produit précis."
    )
    allocated = sum(s.monthly_cents for s in steps)
    summary = _summary(steps, capacity, inp)
    return InvestmentPlan(
        capacity,
        allocated,
        steps,
        long_term,
        lt_monthly,
        equity,
        horizon,
        inp.tax,
        excluded,
        cautions,
        summary,
        refs.source,
    )


def _summary(steps: Sequence[PlanStep], capacity: int, inp: PlanInputs) -> str:
    if capacity <= 0:
        return (
            "Aucune marge mensuelle pour l'instant : la priorité est de dégager de la place dans le budget "
            "(enveloppes, abonnements) avant de placer quoi que ce soit."
        )
    bits = [f"{format_eur(s.monthly_cents)} → {s.title.lower()}" for s in steps if s.monthly_cents > 0]
    head = f"Chaque mois, {format_eur(capacity)} de capacité : " + " ; ".join(bits) + "."
    if any(s.kind == "attente" for s in steps):
        head += " Le long terme attendra que le matelas et la dette chère soient réglés."
    if any(s.kind == "long_terme" and s.monthly_cents > 0 for s in steps):
        head += " Tes bases sont en place : c'est le moment de penser long terme."
    elif not any(s.kind in ("attente", "urgence") for s in steps) and any(s.kind == "projet" for s in steps):
        head += (
            " Tes projets datés absorbent toute la capacité : le long terme viendra ensuite, ou si tu étales un projet."
        )
    return head
