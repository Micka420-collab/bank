"""Patrimoine : valeur nette, matelas de précaution, livrets réglementés, projection d'épargne.

Ce module calcule et informe ; il ne recommande aucun placement. Les taux et plafonds réglementaires
sont des données datées (rates.json), modifiables sans toucher au code.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from app.domain import rates

LIQUID = {"checking"}
REGULATED = {"livret_a", "ldds", "lep", "cel", "savings"}
LONG_TERM = {"pel", "pea", "assurance_vie", "per"}
OTHER_ASSETS = {"crypto", "other"}
LIABILITIES = {"credit"}
REAL_ESTATE = {"real_estate"}  # le logement estimé par les ventes voisines (DVF)


@dataclass(frozen=True)
class AccountBalance:
    id: int
    name: str
    kind: str
    balance_cents: int


@dataclass(frozen=True)
class NetWorth:
    assets_cents: int
    liabilities_cents: int
    net_cents: int
    by_bucket: dict[str, int]


def bucket_of(kind: str) -> str:
    if kind in LIQUID:
        return "liquidites"
    if kind in REGULATED:
        return "epargne_disponible"
    if kind in LONG_TERM:
        return "epargne_long_terme"
    if kind in LIABILITIES:
        return "dettes"
    if kind in REAL_ESTATE:
        return "immobilier"
    return "autres_actifs"


def net_worth(accounts: Sequence[AccountBalance]) -> NetWorth:
    by_bucket: dict[str, int] = defaultdict(int)
    assets = liabilities = 0
    for a in accounts:
        bucket = bucket_of(a.kind)
        if bucket == "dettes" or a.balance_cents < 0:
            # Un compte courant à découvert est aussi une dette.
            liabilities += abs(a.balance_cents)
            by_bucket["dettes"] -= abs(a.balance_cents)
        else:
            assets += a.balance_cents
            by_bucket[bucket] += a.balance_cents
    return NetWorth(assets, liabilities, assets - liabilities, dict(by_bucket))


@dataclass(frozen=True)
class EmergencyFund:
    available_cents: int
    monthly_essentials_cents: int
    months_covered: float
    reference_min_months: int
    reference_max_months: int
    level: str  # insuffisant | correct | confortable


def emergency_fund(accounts: Sequence[AccountBalance], monthly_essentials_cents: int) -> EmergencyFund:
    """Épargne disponible rapidement (livrets) rapportée aux dépenses essentielles mensuelles.
    Repère couramment cité : 3 à 6 mois de dépenses."""
    available = sum(a.balance_cents for a in accounts if a.kind in REGULATED and a.balance_cents > 0)
    months = available / monthly_essentials_cents if monthly_essentials_cents > 0 else 0.0
    level = "insuffisant" if months < 3 else "correct" if months < 6 else "confortable"
    return EmergencyFund(available, monthly_essentials_cents, round(months, 1), 3, 6, level)


# ------------------------------------------------------------------ livrets réglementés
@dataclass(frozen=True)
class RegulatedProduct:
    kind: str
    name: str
    rate_percent: Decimal
    ceiling_cents: int
    as_of: str
    source: str
    note: str


def load_regulated_products() -> list[RegulatedProduct]:
    raw = rates.current()  # fichier daté, recouvert des taux relevés par Bank s'ils sont plus récents
    return [
        RegulatedProduct(
            kind=p["kind"],
            name=p["name"],
            rate_percent=Decimal(p["rate_percent"]),
            ceiling_cents=int(p["ceiling_cents"]),
            as_of=raw["as_of"],
            source=raw["source"],
            note=p.get("note", ""),
        )
        for p in raw["products"]
    ]


@dataclass(frozen=True)
class ProductStatus:
    kind: str
    name: str
    rate_percent: Decimal
    ceiling_cents: int
    balance_cents: int
    room_cents: int
    yearly_interest_cents: int
    at_ceiling: bool


def regulated_status(accounts: Sequence[AccountBalance], products: Sequence[RegulatedProduct]) -> list[ProductStatus]:
    balances: dict[str, int] = defaultdict(int)
    for a in accounts:
        balances[a.kind] += max(0, a.balance_cents)
    out: list[ProductStatus] = []
    for p in products:
        bal = balances.get(p.kind, 0)
        interest = int((Decimal(min(bal, p.ceiling_cents)) * p.rate_percent / 100).to_integral_value(ROUND_HALF_EVEN))
        out.append(
            ProductStatus(
                p.kind,
                p.name,
                p.rate_percent,
                p.ceiling_cents,
                bal,
                max(0, p.ceiling_cents - bal),
                interest,
                bal >= p.ceiling_cents,
            )
        )
    return out


# ------------------------------------------------------------------ projection
@dataclass(frozen=True)
class ProjectionYear:
    year: int
    nominal_cents: int
    real_cents: int  # en euros d'aujourd'hui (corrigé de l'inflation)
    contributed_cents: int
    interest_cents: int


def project_savings(
    initial_cents: int,
    monthly_contribution_cents: int,
    annual_rate_percent: Decimal,
    years: int,
    inflation_percent: Decimal = Decimal("2.0"),
) -> list[ProjectionYear]:
    """Capitalisation mensuelle en Decimal (pas de dérive flottante sur 30 ans)."""
    if years < 1 or years > 60:
        raise ValueError("horizon entre 1 et 60 ans")
    r = Decimal(annual_rate_percent) / 100 / 12
    infl = Decimal(1) + Decimal(inflation_percent) / 100
    balance = Decimal(initial_cents)
    contributed = Decimal(initial_cents)
    out: list[ProjectionYear] = []
    for y in range(1, years + 1):
        for _ in range(12):
            balance = balance * (1 + r) + monthly_contribution_cents
            contributed += monthly_contribution_cents
        nominal = int(balance.to_integral_value(ROUND_HALF_EVEN))
        real = int((balance / (infl**y)).to_integral_value(ROUND_HALF_EVEN))
        out.append(ProjectionYear(y, nominal, real, int(contributed), nominal - int(contributed)))
    return out


def months_to_goal(
    current_cents: int, target_cents: int, monthly_cents: int, annual_rate_percent: Decimal
) -> int | None:
    """Nombre de mois pour atteindre un objectif (None si inatteignable en 50 ans)."""
    if current_cents >= target_cents:
        return 0
    if monthly_cents <= 0 and annual_rate_percent <= 0:
        return None
    r = Decimal(annual_rate_percent) / 100 / 12
    bal = Decimal(current_cents)
    for m in range(1, 601):
        bal = bal * (1 + r) + monthly_cents
        if bal >= target_cents:
            return m
    return None


def monthly_needed(current_cents: int, target_cents: int, target: date, today: date) -> int:
    months = (target.year - today.year) * 12 + (target.month - today.month)
    if months <= 0:
        return max(0, target_cents - current_cents)
    return max(0, -(-(target_cents - current_cents) // months))
