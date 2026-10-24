"""Crédits : prêts suivis (tableau d'amortissement exact, restant dû, coût total, mensualité reconnue dans les
relevés), simulateurs de remboursement anticipé et de renégociation. Lecture et saisie ; rien ne part."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.domain import loans as dom
from app.services import loans as svc

router = APIRouter(prefix="/api/loans", tags=["loans"])


class LoanIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lender: str | None = Field(default=None, max_length=80)
    kind: Literal["immobilier", "consommation", "auto", "autre"] = "consommation"
    principal_cents: int = Field(gt=0, le=dom.MAX_PRINCIPAL_CENTS)
    annual_rate_bp: int = Field(ge=0, le=3_000, description="taux nominal annuel en points de base (1,85 % = 185)")
    months: int = Field(ge=1, le=dom.MAX_MONTHS)
    first_payment: date
    insurance_monthly_cents: int = Field(default=0, ge=0, le=1_000_000)
    account_id: int | None = None


class EarlyIn(BaseModel):
    amount_cents: int = Field(default=0, ge=0, le=dom.MAX_PRINCIPAL_CENTS)
    mode: Literal["shorter", "lower", "full"] = "shorter"
    on: date | None = None


class RenegotiateIn(BaseModel):
    new_rate_bp: int = Field(ge=0, le=3_000)
    fees_cents: int = Field(default=0, ge=0, le=10_000_000)
    buyout: bool = False
    on: date | None = None


def _raise(exc: svc.LoanServiceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def list_loans(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today)


@router.post("", status_code=201)
def create_loan(body: LoanIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        row = svc.create(db, actor=ctx.user.username, **body.model_dump())
    except svc.LoanServiceError as exc:
        raise _raise(exc) from exc
    out = svc.view(db, today)
    out["created_id"] = row.id
    return out


@router.delete("/{loan_id}")
def delete_loan(loan_id: int, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.remove(db, loan_id, actor=ctx.user.username)
    except svc.LoanServiceError as exc:
        raise _raise(exc) from exc
    return svc.view(db, today)


@router.get("/{loan_id}/schedule")
def schedule(loan_id: int, _: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        return svc.schedule_view(db, loan_id)
    except svc.LoanServiceError as exc:
        raise _raise(exc) from exc


@router.post("/{loan_id}/early")
def early(loan_id: int, body: EarlyIn, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        return svc.early(db, loan_id, on=body.on or today, amount_cents=body.amount_cents, mode=body.mode)
    except svc.LoanServiceError as exc:
        raise _raise(exc) from exc


@router.post("/{loan_id}/renegotiate")
def renegotiate(loan_id: int, body: RenegotiateIn, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        return svc.renegotiate(
            db,
            loan_id,
            on=body.on or today,
            new_rate_bp=body.new_rate_bp,
            fees_cents=body.fees_cents,
            buyout=body.buyout,
        )
    except svc.LoanServiceError as exc:
        raise _raise(exc) from exc
