"""Pilote automatique : régler le cadre (mot de passe, et code de double authentification si tu l'as activée),
voir le journal, et tout arrêter d'un geste (sans mot de passe : arrêter ne doit jamais être difficile)."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow, write_lock
from app.security import audit
from app.security.auth import verify_password, verify_totp
from app.services import autonomy

router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])
log = logging.getLogger("bank.autonomy")

Symbol = Annotated[str, Field(min_length=2, max_length=12)]


class DestinationIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    chain: str = Field(min_length=2, max_length=24)
    address: str = Field(min_length=10, max_length=128)


class RuleIn(BaseModel):
    kind: Literal["buy", "sell", "swap", "transfer"]
    asset: Symbol
    to_asset: Symbol | None = None
    destination: str | None = Field(default=None, max_length=60)
    amount_cents: int = Field(ge=100, le=autonomy.HARD_MAX_OP)
    every: Literal["day", "week", "month"]


class ConfigIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)  # donner la main sur ton argent : confirmation exigée
    code: str | None = Field(default=None, max_length=12)  # double authentification, si activée
    enabled: bool = False
    trial: bool = True
    agent_token_id: int | None = None
    mode: Literal["rules", "free"] = "rules"
    scopes: list[Literal["crypto_spot", "transfer"]] = Field(min_length=1, max_length=2)
    assets: list[Symbol] = Field(min_length=1, max_length=20)
    rules: list[RuleIn] = Field(default_factory=list, max_length=10)
    destinations: list[DestinationIn] = Field(default_factory=list, max_length=10)
    per_op_cents: int = Field(ge=100, le=autonomy.HARD_MAX_OP)
    per_day_cents: int = Field(ge=100, le=autonomy.HARD_MAX_DAY)
    per_month_cents: int = Field(ge=100, le=autonomy.HARD_MAX_MONTH)
    max_moves_per_day: int = Field(ge=1, le=20)
    max_loss_cents: int = Field(ge=0, le=autonomy.HARD_MAX_MONTH)
    days: int = Field(default=0, ge=0, le=autonomy.MAX_DAYS)


@router.get("")
def get_pilot(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return autonomy.view(db, now=utcnow(), today=today)


@router.put("")
def configure(body: ConfigIn, request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["login"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    ok, _ = verify_password(ctx.user.password_hash, body.password)
    if not ok:
        audit.record(db, ctx.user.username, "autonomy.denied", why="password")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe incorrect")
    if ctx.user.totp_enabled and ctx.user.totp_secret:
        # Verrou d'écriture puis relecture : deux requêtes avec le même code ne passent pas toutes les deux.
        write_lock(db)
        db.refresh(ctx.user)
        step = verify_totp(ctx.user.totp_secret or "", body.code or "", ctx.user.totp_last_step)
        if step is None:
            audit.record(db, ctx.user.username, "autonomy.denied", why="totp")
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Code de double authentification requis ou incorrect")
        ctx.user.totp_last_step = step
        db.commit()  # le code est consommé même si le cadre est ensuite refusé : pas de rejeu
    payload = body.model_dump(exclude={"password", "code"})
    try:
        autonomy.configure(db, payload, actor=ctx.user.username, now=utcnow())
    except autonomy.AutonomyError as exc:
        db.rollback()
        raise HTTPException(exc.status, str(exc)) from None
    return autonomy.view(db, now=utcnow(), today=today)


@router.post("/stop")
def stop(ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    autonomy.stop(db, actor=ctx.user.username, now=utcnow())
    db.commit()  # l'arrêt est acquis avant tout le reste : rien après ne peut l'annuler
    try:
        return autonomy.view(db, now=utcnow(), today=today)
    except Exception:
        log.exception("pilote arrêté, mais la vue n'a pas pu être construite")
        db.rollback()
        return {"active": False, "enabled": False, "disabled_reason": "Arrêté par toi"}
