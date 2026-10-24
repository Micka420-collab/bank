"""Crédits : enregistre un prêt, le relie à son prélèvement reconnu dans les relevés, et laisse le domaine calculer
le tableau d'amortissement, la position du jour, les simulateurs. Le capital restant dû des prêts non reliés à un
compte « crédit » entre dans le patrimoine net (voir analytics.account_balances)."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import loans as dom
from app.domain.recurring import RecurringSeries
from app.models import Account, AccountKind, Loan
from app.security import audit
from app.services import analytics

MAX_LOANS = 20
MATCH_TOLERANCE = 0.03  # une mensualité reconnue dans les relevés : à ±3 % de la mensualité assurance comprise


class LoanServiceError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def to_domain(row: Loan) -> dom.Loan:
    return dom.Loan(
        principal_cents=row.principal_cents,
        annual_rate_bp=row.annual_rate_bp,
        months=row.months,
        first_payment=row.first_payment,
        insurance_monthly_cents=row.insurance_monthly_cents,
        kind=row.kind if row.kind in dom.KINDS else "autre",
        name=row.name,
    )


def get(db: Session, loan_id: int) -> Loan:
    row = db.get(Loan, loan_id)
    if row is None:
        raise LoanServiceError("Prêt introuvable", 404)
    return row


def create(
    db: Session,
    *,
    name: str,
    lender: str | None,
    kind: str,
    principal_cents: int,
    annual_rate_bp: int,
    months: int,
    first_payment: date,
    insurance_monthly_cents: int,
    account_id: int | None,
    actor: str,
) -> Loan:
    if db.scalar(select(Loan.id).limit(1).offset(MAX_LOANS - 1)) is not None:
        raise LoanServiceError(f"{MAX_LOANS} prêts au plus", 409)
    clean_name = " ".join(name.split())[:80] or "Prêt"
    clean_lender = " ".join((lender or "").split())[:80] or None
    try:
        dom.Loan(principal_cents, annual_rate_bp, months, first_payment, insurance_monthly_cents, kind, clean_name)  # type: ignore[arg-type]
    except dom.LoanError as exc:
        raise LoanServiceError(str(exc), 422) from exc
    if account_id is not None:
        account = db.get(Account, account_id)
        if account is None or account.kind != AccountKind.CREDIT:
            raise LoanServiceError("Le compte relié doit être un compte « crédit »", 422)
    row = Loan(
        name=clean_name,
        lender=clean_lender,
        kind=kind,
        principal_cents=principal_cents,
        annual_rate_bp=annual_rate_bp,
        months=months,
        first_payment=first_payment,
        insurance_monthly_cents=insurance_monthly_cents,
        account_id=account_id,
    )
    db.add(row)
    db.flush()
    audit.record(db, actor, "loan.create", loan_id=row.id, kind=kind, months=months)
    return row


def remove(db: Session, loan_id: int, *, actor: str) -> None:
    row = get(db, loan_id)
    db.delete(row)
    db.flush()
    audit.record(db, actor, "loan.delete", loan_id=loan_id)


# ------------------------------------------------------------------ reconnaissance dans les relevés
def matched_series(series: list[RecurringSeries], monthly_cents: int) -> RecurringSeries | None:
    """Le prélèvement régulier qui ressemble à la mensualité (assurance comprise) : même montant à ±3 %, mensuel,
    sortant — catégorie « crédits » d'abord, sinon n'importe quelle sortie stable."""
    if monthly_cents <= 0:
        return None
    candidates = [
        s
        for s in series
        if s.direction == "out"
        and s.cadence == "monthly"
        and abs(abs(s.amount_cents) - monthly_cents) <= monthly_cents * MATCH_TOLERANCE
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda s: (s.category != "credits", abs(abs(s.amount_cents) - monthly_cents)))
    return candidates[0]


def _match_view(series: RecurringSeries | None) -> dict[str, Any] | None:
    if series is None:
        return None
    return {
        "merchant": series.merchant,
        "amount_cents": abs(series.amount_cents),
        "last_date": series.last_date.isoformat(),
        "next_date": series.next_date.isoformat(),
        "late": series.late,
        "occurrences": series.occurrences,
    }


# ------------------------------------------------------------------ vues
def loan_view(row: Loan, today: date, series: list[RecurringSeries], accounts: dict[int, Account]) -> dict[str, Any]:
    loan = to_domain(row)
    ov = dom.overview(loan, today)
    match = matched_series(series, ov.sched.monthly_with_insurance_cents)
    account = accounts.get(row.account_id) if row.account_id else None
    bank_says = -account.balance_cents if account and account.balance_cents < 0 else None
    notes = list(ov.notes)
    if (
        bank_says is not None
        and ov.pos.remaining_cents
        and abs(bank_says - ov.pos.remaining_cents) > max(1_000, ov.pos.remaining_cents * 0.01)
    ):
        bank_eur, table_eur = f"{bank_says / 100:,.0f}", f"{ov.pos.remaining_cents / 100:,.0f}"
        notes.append(
            f"Ta banque affiche {bank_eur} € restant dû, le tableau dit {table_eur} € : vérifie la date de première "
            "échéance ou un remboursement anticipé passé.".replace(",", " ")
        )
    return {
        "id": row.id,
        "name": row.name,
        "lender": row.lender,
        "kind": row.kind,
        "kind_label": dom.KIND_LABELS.get(row.kind, "Crédit"),
        "principal_cents": row.principal_cents,
        "annual_rate_bp": row.annual_rate_bp,
        "months": row.months,
        "first_payment": row.first_payment.isoformat(),
        "insurance_monthly_cents": row.insurance_monthly_cents,
        "account_id": row.account_id,
        "bank_remaining_cents": bank_says,
        **dom.overview_to_dict(ov),
        "notes": notes,
        "matched": _match_view(match),
    }


def view(db: Session, today: date) -> dict[str, Any]:
    rows = list(db.scalars(select(Loan).order_by(Loan.id)))
    series = analytics.recurring_for_budget(db, today) if rows else []
    accounts = {a.id: a for a in db.scalars(select(Account))}
    loans = [loan_view(r, today, series, accounts) for r in rows]
    credit_accounts = [
        {"id": a.id, "name": a.name, "balance_cents": a.balance_cents}
        for a in accounts.values()
        if a.kind == AccountKind.CREDIT and not a.archived
    ]
    return {
        "loans": loans,
        "total_remaining_cents": sum(x["remaining_cents"] for x in loans),
        "total_monthly_cents": sum(x["monthly_with_insurance_cents"] for x in loans if x["months_left"] > 0),
        "credit_accounts": credit_accounts,
        "kinds": [{"key": k, "label": dom.KIND_LABELS[k]} for k in dom.KINDS],
        "rules": dom.RULES,
        "disclaimer": (
            "Tableau calculé à taux fixe et mensualité constante, intérêts sur le capital restant dû : exact au "
            "centime pour un prêt classique ; un prêt à paliers, à taux variable ou avec différé s'en écarte. Les "
            "indemnités affichées sont les plafonds légaux (datés) : ton contrat peut prévoir moins, jamais plus."
        ),
    }


def schedule_view(db: Session, loan_id: int) -> dict[str, Any]:
    loan = to_domain(get(db, loan_id))
    sched = dom.schedule(loan)
    return {
        "payment_cents": sched.payment_cents,
        "rows": [dom.row_to_dict(r) for r in sched.rows],
        "yearly": dom.yearly(sched),
        "total_interest_cents": sched.total_interest_cents,
        "total_insurance_cents": sched.total_insurance_cents,
    }


def early(db: Session, loan_id: int, *, on: date, amount_cents: int, mode: str) -> dict[str, Any]:
    loan = to_domain(get(db, loan_id))
    try:
        return dom.early_to_dict(dom.early_repayment(loan, on, amount_cents, mode))
    except dom.LoanError as exc:
        raise LoanServiceError(str(exc), 422) from exc


def renegotiate(
    db: Session, loan_id: int, *, on: date, new_rate_bp: int, fees_cents: int, buyout: bool
) -> dict[str, Any]:
    loan = to_domain(get(db, loan_id))
    try:
        return dom.renegotiation_to_dict(dom.renegotiate(loan, on, new_rate_bp, fees_cents=fees_cents, buyout=buyout))
    except dom.LoanError as exc:
        raise LoanServiceError(str(exc), 422) from exc


def for_agent(db: Session, today: date, privacy: str) -> list[dict[str, Any]]:
    """Les prêts pour le dossier de l'agent : restant dû, mensualité, taux, fin — le nom seulement en mode complet."""
    out: list[dict[str, Any]] = []
    for row in db.scalars(select(Loan).order_by(Loan.id)):
        ov = dom.overview(to_domain(row), today)
        out.append(
            {
                "label": row.name if privacy == "full" else dom.KIND_LABELS.get(row.kind, "Crédit"),
                "kind": row.kind,
                "remaining_cents": ov.pos.remaining_cents,
                "monthly_cents": ov.sched.monthly_with_insurance_cents if ov.pos.months_left else 0,
                "rate_percent": str(row.annual_rate_bp / 100),
                "months_left": ov.pos.months_left,
                "interest_left_cents": ov.pos.interest_left_cents,
                "end": ov.sched.rows[-1].day.isoformat(),
                "source": "amortization",
            }
        )
    return out
