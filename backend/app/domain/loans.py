"""Crédits : tableau d'amortissement exact au centime (mensualité constante, intérêts sur le capital restant dû,
dernière échéance ajustée), capital restant dû à une date, coût total ; simulateurs de remboursement anticipé (avec
les indemnités plafonnées par la loi, datées) et de renégociation à un autre taux. Pur : aucune lecture de la base.

Règles datées (vérifiées le 2026-10-22 ; Bank dit quand les revérifier) :
- Prêt immobilier : indemnité ≤ 6 mois d'intérêts sur le capital remboursé, et ≤ 3 % du capital restant dû avant le
  remboursement (Code de la consommation, art. L313-47 et R313-25 ; fiche service-public F1669). Nulle si le
  remboursement suit la vente du logement pour changement de lieu de travail, cessation forcée d'activité ou décès.
- Crédit à la consommation : aucune indemnité si les remboursements anticipés ne dépassent pas 10 000 € sur douze
  mois ; sinon 1 % du capital remboursé s'il reste plus d'un an, 0,5 % sinon, sans jamais dépasser les intérêts
  qu'il restait à payer (art. L312-34 ; fiche service-public F2440). Découvert et crédit renouvelable : rien."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.domain.recurring import add_months

LoanKind = Literal["immobilier", "consommation", "auto", "autre"]
KINDS: tuple[LoanKind, ...] = ("immobilier", "consommation", "auto", "autre")
KIND_LABELS = {
    "immobilier": "Prêt immobilier",
    "consommation": "Crédit à la consommation",
    "auto": "Crédit auto",
    "autre": "Autre crédit",
}
MAX_MONTHS = 480
MAX_PRINCIPAL_CENTS = 5_000_000_00
RULES_CHECKED = "2026-10-22"
RECHECK_AFTER_DAYS = 365
CONSUMER_FREE_CENTS = 1_000_000  # 10 000 € sur douze mois sans indemnité
RULES: dict[str, dict[str, Any]] = {
    "ira_immobilier": {
        "title": "Remboursement anticipé d'un prêt immobilier : indemnité plafonnée à 6 mois d'intérêts sur le capital "
        "remboursé et à 3 % du capital restant dû (Code de la consommation, art. L313-47 et R313-25 ; fiche "
        "service-public F1669)",
        "url": "https://www.service-public.fr/particuliers/vosdroits/F1669",
        "checked": RULES_CHECKED,
    },
    "ira_consommation": {
        "title": "Remboursement anticipé d'un crédit à la consommation : rien jusqu'à 10 000 € sur douze mois, sinon "
        "1 % (plus d'un an restant) ou 0,5 % du capital remboursé, jamais plus que les intérêts restants (Code de "
        "la consommation, art. L312-34 ; fiche service-public F2440)",
        "url": "https://www.service-public.fr/particuliers/vosdroits/F2440",
        "checked": RULES_CHECKED,
    },
}

CENT = Decimal("0.01")


class LoanError(ValueError):
    pass


@dataclass(frozen=True)
class Loan:
    principal_cents: int
    annual_rate_bp: int  # taux nominal annuel en points de base (1,85 % = 185)
    months: int
    first_payment: date
    insurance_monthly_cents: int = 0
    kind: LoanKind = "consommation"
    name: str = "Prêt"

    def __post_init__(self) -> None:
        if not 0 < self.principal_cents <= MAX_PRINCIPAL_CENTS:
            raise LoanError("Capital emprunté : entre 1 € et 5 000 000 €")
        if not 0 <= self.annual_rate_bp <= 3_000:
            raise LoanError("Taux annuel : entre 0 et 30 %")
        if not 1 <= self.months <= MAX_MONTHS:
            raise LoanError("Durée : entre 1 et 480 mois")
        if not 0 <= self.insurance_monthly_cents <= 1_000_000:
            raise LoanError("Assurance mensuelle : entre 0 et 10 000 €")
        if self.kind not in KINDS:
            raise LoanError("Genre de crédit inconnu")


@dataclass(frozen=True)
class Row:
    index: int  # 1..months
    day: date
    payment_cents: int  # hors assurance
    interest_cents: int
    principal_cents: int
    insurance_cents: int
    remaining_cents: int  # après cette échéance


@dataclass(frozen=True)
class Schedule:
    loan: Loan
    payment_cents: int  # mensualité hors assurance (la dernière peut différer de quelques centimes)
    rows: tuple[Row, ...]
    total_interest_cents: int
    total_insurance_cents: int

    @property
    def total_cost_cents(self) -> int:
        return self.total_interest_cents + self.total_insurance_cents

    @property
    def monthly_with_insurance_cents(self) -> int:
        return self.payment_cents + self.loan.insurance_monthly_cents


def _rate(loan: Loan) -> Decimal:
    return Decimal(loan.annual_rate_bp) / Decimal(10_000) / Decimal(12)


def payment_cents(principal_cents: int, annual_rate_bp: int, months: int) -> int:
    """Mensualité constante (formule de l'annuité), arrondie au centime le plus proche."""
    if months <= 0:
        raise LoanError("Durée nulle")
    r = Decimal(annual_rate_bp) / Decimal(10_000) / Decimal(12)
    p = Decimal(principal_cents)
    raw = p / months if r == 0 else p * r / (1 - (1 + r) ** (-months))
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def schedule(loan: Loan) -> Schedule:
    """Tableau exact : intérêts du mois = capital restant × taux mensuel (arrondi au centime), part de capital =
    mensualité − intérêts, dernière échéance ajustée pour solder exactement le capital."""
    r = _rate(loan)
    pay = payment_cents(loan.principal_cents, loan.annual_rate_bp, loan.months)
    remaining = loan.principal_cents
    rows: list[Row] = []
    total_interest = 0
    for i in range(1, loan.months + 1):
        interest = int((Decimal(remaining) * r).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        principal = pay - interest
        if i == loan.months or principal >= remaining:
            principal = remaining
            this_pay = principal + interest
        else:
            this_pay = pay
        remaining -= principal
        total_interest += interest
        rows.append(
            Row(
                i,
                add_months(loan.first_payment, i - 1),
                this_pay,
                interest,
                principal,
                loan.insurance_monthly_cents,
                remaining,
            )
        )
        if remaining == 0:
            break
    return Schedule(loan, pay, tuple(rows), total_interest, loan.insurance_monthly_cents * len(rows))


@dataclass(frozen=True)
class Position:
    """Où en est le prêt à une date : échéances passées, capital restant dû, prochaine échéance."""

    paid_rows: int
    remaining_cents: int
    paid_principal_cents: int
    paid_interest_cents: int
    paid_insurance_cents: int
    next_payment: date | None
    next_payment_cents: int
    months_left: int
    interest_left_cents: int
    progress: float  # part du capital remboursée, 0..1


def position(sched: Schedule, on: date) -> Position:
    paid = [row for row in sched.rows if row.day <= on]
    left = [row for row in sched.rows if row.day > on]
    remaining = paid[-1].remaining_cents if paid else sched.loan.principal_cents
    nxt = left[0] if left else None
    return Position(
        paid_rows=len(paid),
        remaining_cents=remaining,
        paid_principal_cents=sum(row.principal_cents for row in paid),
        paid_interest_cents=sum(row.interest_cents for row in paid),
        paid_insurance_cents=sum(row.insurance_cents for row in paid),
        next_payment=nxt.day if nxt else None,
        next_payment_cents=(nxt.payment_cents + nxt.insurance_cents) if nxt else 0,
        months_left=len(left),
        interest_left_cents=sum(row.interest_cents for row in left),
        progress=round(1 - remaining / sched.loan.principal_cents, 4),
    )


# ------------------------------------------------------------------ remboursement anticipé
@dataclass(frozen=True)
class Indemnity:
    cents: int
    rule: str  # clé de RULES, ou "" (aucune indemnité)
    explanation: str


def indemnity(
    loan: Loan,
    *,
    repaid_cents: int,
    remaining_before_cents: int,
    months_left: int,
    interest_left_cents: int,
    repaid_last_12m_cents: int = 0,
) -> Indemnity:
    """L'indemnité maximale que la loi autorise (le contrat peut prévoir moins, jamais plus)."""
    if repaid_cents <= 0:
        return Indemnity(0, "", "Rien à rembourser.")
    if loan.kind == "immobilier":
        six_months = int((Decimal(repaid_cents) * _rate(loan) * 6).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        three_percent = int(
            (Decimal(remaining_before_cents) * Decimal("0.03")).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        )
        cents = min(six_months, three_percent)
        return Indemnity(
            cents,
            "ira_immobilier",
            f"Plafond légal : le plus bas entre six mois d'intérêts sur le capital remboursé "
            f"({six_months / 100:.2f} €) et 3 % du capital restant dû ({three_percent / 100:.2f} €). Nulle si tu "
            "vends pour un changement de lieu de travail, une cessation forcée d'activité ou un décès ; ton contrat "
            "peut prévoir moins.",
        )
    if repaid_last_12m_cents + repaid_cents <= CONSUMER_FREE_CENTS:
        return Indemnity(
            0, "ira_consommation", "Aucune indemnité : moins de 10 000 € remboursés par anticipation sur douze mois."
        )
    rate = Decimal("0.01") if months_left > 12 else Decimal("0.005")
    legal = int((Decimal(repaid_cents) * rate).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    cents = min(legal, interest_left_cents)
    return Indemnity(
        cents,
        "ira_consommation",
        f"Plafond légal : {'1 %' if months_left > 12 else '0,5 %'} du capital remboursé ({legal / 100:.2f} €), "
        f"jamais plus que les intérêts qu'il restait à payer ({interest_left_cents / 100:.2f} €).",
    )


@dataclass(frozen=True)
class EarlyRepayment:
    on: date
    amount_cents: int  # capital remboursé par anticipation (borné au restant dû)
    mode: str  # shorter | lower | full
    indemnity: Indemnity
    remaining_before_cents: int
    remaining_after_cents: int
    old_payment_cents: int
    new_payment_cents: int
    old_months_left: int
    new_months_left: int
    interest_saved_cents: int  # intérêts + assurance évités
    insurance_saved_cents: int
    net_gain_cents: int  # économie − indemnité
    new_end: date | None


def early_repayment(
    loan: Loan, on: date, amount_cents: int, mode: str = "shorter", *, repaid_last_12m_cents: int = 0
) -> EarlyRepayment:
    """Rembourser ``amount_cents`` de capital à la date ``on`` (après l'échéance du mois). ``shorter`` garde la
    mensualité et raccourcit ; ``lower`` garde la durée et baisse la mensualité ; ``full`` solde tout."""
    if amount_cents <= 0 and mode != "full":
        raise LoanError("Montant à rembourser : au moins 1 €")
    if mode not in ("shorter", "lower", "full"):
        raise LoanError("Mode inconnu")
    sched = schedule(loan)
    pos = position(sched, on)
    if pos.remaining_cents <= 0:
        raise LoanError("Ce prêt est déjà soldé")
    repaid = pos.remaining_cents if mode == "full" else min(amount_cents, pos.remaining_cents)
    full = repaid >= pos.remaining_cents
    ind = indemnity(
        loan,
        repaid_cents=repaid,
        remaining_before_cents=pos.remaining_cents,
        months_left=pos.months_left,
        interest_left_cents=pos.interest_left_cents,
        repaid_last_12m_cents=repaid_last_12m_cents,
    )
    old_payment = sched.payment_cents
    insurance_left = pos.months_left * loan.insurance_monthly_cents
    if full:
        saved = pos.interest_left_cents + insurance_left
        return EarlyRepayment(
            on=on,
            amount_cents=repaid,
            mode="full",
            indemnity=ind,
            remaining_before_cents=pos.remaining_cents,
            remaining_after_cents=0,
            old_payment_cents=old_payment,
            new_payment_cents=0,
            old_months_left=pos.months_left,
            new_months_left=0,
            interest_saved_cents=saved,
            insurance_saved_cents=insurance_left,
            net_gain_cents=saved - ind.cents,
            new_end=None,
        )
    remaining_after = pos.remaining_cents - repaid
    start = pos.next_payment or add_months(loan.first_payment, pos.paid_rows)
    if mode == "lower":
        new_loan = Loan(
            remaining_after,
            loan.annual_rate_bp,
            pos.months_left,
            start,
            loan.insurance_monthly_cents,
            loan.kind,
            loan.name,
        )
        new_sched = schedule(new_loan)
        new_months = len(new_sched.rows)
        new_payment = new_sched.payment_cents
    else:
        new_months = _months_for_payment(remaining_after, loan.annual_rate_bp, old_payment)
        new_loan = Loan(
            remaining_after, loan.annual_rate_bp, new_months, start, loan.insurance_monthly_cents, loan.kind, loan.name
        )
        new_sched = _schedule_with_payment(new_loan, old_payment)
        new_months = len(new_sched.rows)
        new_payment = old_payment
    interest_saved = pos.interest_left_cents - new_sched.total_interest_cents
    insurance_saved = insurance_left - new_sched.total_insurance_cents
    return EarlyRepayment(
        on=on,
        amount_cents=repaid,
        mode=mode,
        indemnity=ind,
        remaining_before_cents=pos.remaining_cents,
        remaining_after_cents=remaining_after,
        old_payment_cents=old_payment,
        new_payment_cents=new_payment,
        old_months_left=pos.months_left,
        new_months_left=new_months,
        interest_saved_cents=interest_saved + insurance_saved,
        insurance_saved_cents=insurance_saved,
        net_gain_cents=interest_saved + insurance_saved - ind.cents,
        new_end=new_sched.rows[-1].day,
    )


def _restart(loan: Loan, principal_cents: int, months: int, start: date) -> Loan:
    """Le même prêt, repris sur un capital et une durée (mêmes taux, assurance, genre)."""
    return Loan(principal_cents, loan.annual_rate_bp, months, start, loan.insurance_monthly_cents, loan.kind, loan.name)


def _months_for_payment(principal_cents: int, annual_rate_bp: int, payment: int) -> int:
    """Combien de mois pour solder ``principal_cents`` avec une mensualité ``payment`` (au plus MAX_MONTHS)."""
    r = Decimal(annual_rate_bp) / Decimal(10_000) / Decimal(12)
    remaining = Decimal(principal_cents)
    for m in range(1, MAX_MONTHS + 1):
        interest = (remaining * r).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        if payment <= interest:
            raise LoanError("La mensualité ne couvre pas les intérêts")
        remaining -= payment - interest
        if remaining <= 0:
            return m
    return MAX_MONTHS


def _schedule_with_payment(loan: Loan, payment: int) -> Schedule:
    """Tableau avec une mensualité imposée (plus haute que l'annuité : le prêt finit plus tôt)."""
    r = _rate(loan)
    remaining = loan.principal_cents
    rows: list[Row] = []
    total_interest = 0
    i = 0
    while remaining > 0 and i < MAX_MONTHS:
        i += 1
        interest = int((Decimal(remaining) * r).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        principal = min(payment - interest, remaining)
        if principal <= 0:
            raise LoanError("La mensualité ne couvre pas les intérêts")
        remaining -= principal
        total_interest += interest
        rows.append(
            Row(
                i,
                add_months(loan.first_payment, i - 1),
                principal + interest,
                interest,
                principal,
                loan.insurance_monthly_cents,
                remaining,
            )
        )
    return Schedule(loan, payment, tuple(rows), total_interest, loan.insurance_monthly_cents * len(rows))


# ------------------------------------------------------------------ renégociation
@dataclass(frozen=True)
class Renegotiation:
    on: date
    old_rate_bp: int
    new_rate_bp: int
    remaining_cents: int
    months_left: int
    old_payment_cents: int
    new_payment_cents: int
    old_interest_left_cents: int
    new_interest_cents: int
    fees_cents: int
    indemnity_cents: int  # si le prêt est racheté par une autre banque (remboursement anticipé chez l'ancienne)
    net_gain_cents: int
    worth_it: bool


def renegotiate(loan: Loan, on: date, new_rate_bp: int, *, fees_cents: int = 0, buyout: bool = False) -> Renegotiation:
    """Même durée restante, nouveau taux : la mensualité baisse, et le gain net déduit les frais (et l'indemnité de
    remboursement anticipé quand une autre banque rachète le prêt)."""
    if not 0 <= new_rate_bp <= 3_000:
        raise LoanError("Taux : entre 0 et 30 %")
    if fees_cents < 0 or fees_cents > 10_000_000:
        raise LoanError("Frais : entre 0 et 100 000 €")
    sched = schedule(loan)
    pos = position(sched, on)
    if pos.remaining_cents <= 0 or pos.months_left == 0:
        raise LoanError("Ce prêt est déjà soldé")
    start = pos.next_payment or add_months(loan.first_payment, pos.paid_rows)
    new_loan = Loan(
        pos.remaining_cents, new_rate_bp, pos.months_left, start, loan.insurance_monthly_cents, loan.kind, loan.name
    )
    new_sched = schedule(new_loan)
    ind = 0
    if buyout:
        ind = indemnity(
            loan,
            repaid_cents=pos.remaining_cents,
            remaining_before_cents=pos.remaining_cents,
            months_left=pos.months_left,
            interest_left_cents=pos.interest_left_cents,
            repaid_last_12m_cents=CONSUMER_FREE_CENTS,  # un rachat dépasse le seuil : l'indemnité s'applique
        ).cents
    gain = pos.interest_left_cents - new_sched.total_interest_cents - fees_cents - ind
    return Renegotiation(
        on=on,
        old_rate_bp=loan.annual_rate_bp,
        new_rate_bp=new_rate_bp,
        remaining_cents=pos.remaining_cents,
        months_left=pos.months_left,
        old_payment_cents=sched.payment_cents,
        new_payment_cents=new_sched.payment_cents,
        old_interest_left_cents=pos.interest_left_cents,
        new_interest_cents=new_sched.total_interest_cents,
        fees_cents=fees_cents,
        indemnity_cents=ind,
        net_gain_cents=gain,
        worth_it=gain > 0,
    )


# ------------------------------------------------------------------ vues
def row_to_dict(row: Row) -> dict[str, Any]:
    return {
        "index": row.index,
        "day": row.day.isoformat(),
        "payment_cents": row.payment_cents,
        "interest_cents": row.interest_cents,
        "principal_cents": row.principal_cents,
        "insurance_cents": row.insurance_cents,
        "remaining_cents": row.remaining_cents,
    }


def yearly(sched: Schedule) -> list[dict[str, Any]]:
    """Le tableau replié par année civile : lisible sur un téléphone, exact en somme."""
    out: dict[int, dict[str, Any]] = {}
    for row in sched.rows:
        y = out.setdefault(
            row.day.year,
            {
                "year": row.day.year,
                "payments_cents": 0,
                "interest_cents": 0,
                "principal_cents": 0,
                "insurance_cents": 0,
                "remaining_cents": 0,
                "months": 0,
            },
        )
        y["payments_cents"] += row.payment_cents + row.insurance_cents
        y["interest_cents"] += row.interest_cents
        y["principal_cents"] += row.principal_cents
        y["insurance_cents"] += row.insurance_cents
        y["remaining_cents"] = row.remaining_cents
        y["months"] += 1
    return list(out.values())


def early_to_dict(e: EarlyRepayment) -> dict[str, Any]:
    return {
        "on": e.on.isoformat(),
        "amount_cents": e.amount_cents,
        "mode": e.mode,
        "indemnity_cents": e.indemnity.cents,
        "indemnity_rule": RULES.get(e.indemnity.rule),
        "indemnity_explanation": e.indemnity.explanation,
        "remaining_before_cents": e.remaining_before_cents,
        "remaining_after_cents": e.remaining_after_cents,
        "old_payment_cents": e.old_payment_cents,
        "new_payment_cents": e.new_payment_cents,
        "old_months_left": e.old_months_left,
        "new_months_left": e.new_months_left,
        "interest_saved_cents": e.interest_saved_cents,
        "insurance_saved_cents": e.insurance_saved_cents,
        "net_gain_cents": e.net_gain_cents,
        "new_end": e.new_end.isoformat() if e.new_end else None,
    }


def renegotiation_to_dict(r: Renegotiation) -> dict[str, Any]:
    return {
        "on": r.on.isoformat(),
        "old_rate_bp": r.old_rate_bp,
        "new_rate_bp": r.new_rate_bp,
        "remaining_cents": r.remaining_cents,
        "months_left": r.months_left,
        "old_payment_cents": r.old_payment_cents,
        "new_payment_cents": r.new_payment_cents,
        "old_interest_left_cents": r.old_interest_left_cents,
        "new_interest_cents": r.new_interest_cents,
        "fees_cents": r.fees_cents,
        "indemnity_cents": r.indemnity_cents,
        "net_gain_cents": r.net_gain_cents,
        "worth_it": r.worth_it,
    }


@dataclass(frozen=True)
class Overview:
    """Ce que l'écran montre d'un prêt à une date : la position, le coût, les échéances qui viennent."""

    sched: Schedule
    pos: Position
    rules_recheck: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def overview(loan: Loan, on: date) -> Overview:
    sched = schedule(loan)
    pos = position(sched, on)
    recheck = (on - date.fromisoformat(RULES_CHECKED)).days > RECHECK_AFTER_DAYS
    notes: list[str] = []
    if pos.remaining_cents == 0:
        notes.append("Prêt soldé.")
    if loan.annual_rate_bp == 0:
        notes.append("Taux nul : pas d'intérêts, seule l'assurance coûte.")
    return Overview(sched, pos, recheck, tuple(notes))


def overview_to_dict(o: Overview) -> dict[str, Any]:
    s, p = o.sched, o.pos
    return {
        "payment_cents": s.payment_cents,
        "monthly_with_insurance_cents": s.monthly_with_insurance_cents,
        "total_interest_cents": s.total_interest_cents,
        "total_insurance_cents": s.total_insurance_cents,
        "total_cost_cents": s.total_cost_cents,
        "end": s.rows[-1].day.isoformat(),
        "paid_rows": p.paid_rows,
        "remaining_cents": p.remaining_cents,
        "paid_principal_cents": p.paid_principal_cents,
        "paid_interest_cents": p.paid_interest_cents,
        "paid_insurance_cents": p.paid_insurance_cents,
        "next_payment": p.next_payment.isoformat() if p.next_payment else None,
        "next_payment_cents": p.next_payment_cents,
        "months_left": p.months_left,
        "interest_left_cents": p.interest_left_cents,
        "progress": p.progress,
        "rules_recheck": o.rules_recheck,
        "notes": list(o.notes),
    }
