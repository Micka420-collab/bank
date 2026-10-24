"""Retraite : ce que ta carrière (trimestres, salaire, points — saisis ici, chiffrés en base) et ta génération donnent
comme départs possibles (âge légal, taux plein, 67 ans, un âge au choix) avec une fourchette de pension, et ce qu'un
versement PER change à ton impôt. Ordre de grandeur daté, lecture seule : rien ne part vers une caisse, le chiffre
opposable est celui d'info-retraite.fr."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import retirement as svc

router = APIRouter(prefix="/api/retirement", tags=["retirement"])


class ProfileIn(BaseModel):
    quarters_acquired: int = Field(ge=0, le=200, description="trimestres validés au relevé de carrière")
    as_of: date | None = Field(default=None, description="date du relevé (défaut : aujourd'hui)")
    best_years_average_cents: int | None = Field(default=None, ge=0, le=100_000_000)
    agirc_arrco_points: str | None = Field(default=None, max_length=20, description="points acquis, ex. « 3250,5 »")
    career_start_year: int | None = Field(default=None, ge=1950, le=2100)
    working: bool = True
    birth_date: date | None = None


def _raise(exc: svc.RetirementServiceError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def retirement(
    _: AuthDep,
    db: DbDep,
    today: TodayDep,
    age_years: int | None = Query(default=None, ge=svc.MIN_CUSTOM_AGE_YEARS, le=svc.MAX_CUSTOM_AGE_YEARS),
    age_months: int | None = Query(default=None, ge=0, le=11),
    per_cents: int | None = Query(default=None, ge=0, le=100_000_000),
) -> dict[str, Any]:
    try:
        return svc.view(db, today, age_years=age_years, age_months=age_months, per_cents=per_cents)
    except svc.RetirementServiceError as exc:
        raise _raise(exc) from exc


@router.put("/profile")
def save_profile(body: ProfileIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if body.as_of is not None and body.as_of > today:
        raise HTTPException(422, "La date du relevé ne peut pas être dans le futur")
    if body.birth_date is not None and not date(1900, 1, 1) <= body.birth_date <= today:
        raise HTTPException(422, "Date de naissance invalide")
    try:
        svc.save_profile(
            db,
            actor=ctx.user.username,
            quarters_acquired=body.quarters_acquired,
            as_of=body.as_of or today,
            best_years_average_cents=body.best_years_average_cents,
            agirc_arrco_points=body.agirc_arrco_points,
            career_start_year=body.career_start_year,
            working=body.working,
            birth_date=body.birth_date,
        )
        return svc.view(db, today)
    except svc.RetirementServiceError as exc:
        raise _raise(exc) from exc


@router.delete("/profile")
def delete_profile(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    svc.remove_profile(db, actor=ctx.user.username)
    try:
        return svc.view(db, today)
    except svc.RetirementServiceError as exc:
        raise _raise(exc) from exc
