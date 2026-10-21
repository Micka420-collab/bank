"""Énergie : connexion Linky (Conso API), synchronisation, tarif, analyse."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.security import audit
from app.services import energy

router = APIRouter(prefix="/api/energy", tags=["energy"])


class LinkyIn(BaseModel):
    prm: str = Field(min_length=14, max_length=20)
    token: str = Field(min_length=40, max_length=4000)


class TariffIn(BaseModel):
    cents_per_100kwh: int = Field(ge=1, le=100_000)
    subscription_cents_per_year: int = Field(ge=0, le=1_000_000)
    label: str = Field(default="Tarif personnalisé", max_length=60)


@router.get("")
def get_status(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return energy.status(db, today)


@router.post("/linky", status_code=201)
def connect_linky(body: LinkyIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    # Le test de connexion appelle Enedis : limité comme une authentification, pour ne pas faire bloquer l'IP.
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    make_client = request.app.state.energy_client_factory
    try:
        energy.connect_linky(
            db, prm=body.prm, token=body.token, actor=ctx.user.username, today=today, make_client=make_client
        )
    except energy.EnergyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    changed = energy.sync(db, today, make_client=make_client, full=True)
    return {**energy.status(db, today), "imported": changed}


@router.post("/sync")
def sync(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if energy.active_connection(db) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun compteur connecté")
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    changed = energy.sync(db, today, make_client=request.app.state.energy_client_factory)
    audit.record(db, ctx.user.username, "energy.sync_manual", changed=changed)
    return {**energy.status(db, today), "imported": changed}


@router.delete("/linky", status_code=204)
def disconnect(ctx: AuthDep, db: DbDep) -> None:
    energy.disconnect(db, ctx.user.username)


@router.put("/tariff")
def put_tariff(body: TariffIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        energy.set_tariff(
            db,
            cents_per_100kwh=body.cents_per_100kwh,
            subscription_cents_per_year=body.subscription_cents_per_year,
            label=body.label,
        )
    except energy.EnergyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit.record(db, ctx.user.username, "energy.tariff")
    return body.model_dump()
