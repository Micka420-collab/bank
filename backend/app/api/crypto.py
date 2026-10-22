"""Crypto : adresses suivies (lecture seule), avoirs saisis, relève (avec ton accord), fiscalité, vérification
d'un message ou d'une adresse. Aucun endpoint n'accepte ni ne renvoie de clé : il n'y en a pas."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.domain.crypto import CryptoError
from app.services import consents
from app.services import crypto as svc
from app.services.settings_store import set_setting

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


class WalletIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    chain: str = Field(min_length=2, max_length=16)
    address: str = Field(min_length=10, max_length=200)
    confirm_lookalike: bool = False


class HoldingIn(BaseModel):
    asset: str = Field(min_length=2, max_length=12)
    quantity: str = Field(min_length=1, max_length=48)
    platform: str | None = Field(default=None, max_length=60)
    foreign: bool = False
    coingecko_id: str | None = Field(default=None, max_length=64)


class TaxIn(BaseModel):
    day: date
    kind: Literal["acquisition", "cession"]
    amount_cents: int = Field(gt=0, le=100_000_000_000)
    fees_cents: int = Field(default=0, ge=0, le=100_000_000_000)
    portfolio_value_cents: int | None = Field(default=None, gt=0, le=1_000_000_000_000)
    asset: str | None = Field(default=None, max_length=12)
    note: str | None = Field(default=None, max_length=200)


class CheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)


class SettingsIn(BaseModel):
    endpoints: dict[str, str | None] | None = Field(default=None, max_length=12)
    in_wealth: bool | None = None


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, consents.ConsentError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("")
def overview(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today=today, now=utcnow())


@router.post("/wallets", status_code=201)
def add_wallet(body: WalletIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        wallet = svc.add_wallet(
            db,
            label=body.label,
            chain=body.chain,
            address=body.address,
            actor=ctx.user.username,
            confirm_lookalike=body.confirm_lookalike,
        )
    except CryptoError as exc:
        raise _fail(exc) from None
    return {"id": wallet.id, "address": wallet.address}


@router.delete("/wallets/{wallet_id}", status_code=204)
def delete_wallet(wallet_id: int, ctx: AuthDep, db: DbDep, today: TodayDep) -> None:
    try:
        svc.delete_wallet(db, wallet_id, actor=ctx.user.username)
    except CryptoError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    svc.snapshot(db, today)


@router.post("/holdings", status_code=201)
def add_holding(body: HoldingIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        row = svc.set_holding(
            db,
            actor=ctx.user.username,
            asset=body.asset,
            quantity=body.quantity,
            platform=body.platform,
            foreign=body.foreign,
            coingecko=body.coingecko_id,
        )
    except CryptoError as exc:
        raise _fail(exc) from None
    svc.snapshot(db, today)
    return {"id": row.id}


@router.put("/holdings/{holding_id}")
def update_holding(holding_id: int, body: HoldingIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        row = svc.set_holding(
            db,
            actor=ctx.user.username,
            asset=body.asset,
            quantity=body.quantity,
            platform=body.platform,
            foreign=body.foreign,
            coingecko=body.coingecko_id,
            holding_id=holding_id,
        )
    except CryptoError as exc:
        raise _fail(exc) from None
    svc.snapshot(db, today)
    return {"id": row.id}


@router.delete("/holdings/{holding_id}", status_code=204)
def delete_holding(holding_id: int, ctx: AuthDep, db: DbDep, today: TodayDep) -> None:
    try:
        svc.delete_holding(db, holding_id, actor=ctx.user.username)
    except CryptoError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    svc.snapshot(db, today)


@router.post("/sync")
def sync(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["crypto"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Relève toute fraîche : réessaie dans quelques minutes")
    try:
        result = svc.sync(db, net=request.app.state.crypto_net(), now=utcnow(), actor=ctx.user.username)
    except consents.ConsentError as exc:
        raise _fail(exc) from None
    return {"result": result, "view": svc.view(db, today=today, now=utcnow())}


@router.post("/check")
def check(body: CheckIn, _: AuthDep, db: DbDep) -> dict[str, Any]:
    # Rien n'est gardé ni envoyé : l'analyse se fait ici, sur ton serveur.
    return svc.check(db, body.text)


@router.get("/tax")
def tax(_: AuthDep, db: DbDep, today: TodayDep, year: int | None = None) -> dict[str, Any]:
    return svc.tax_view(db, year=year or today.year, today=today)


@router.post("/tax", status_code=201)
def add_tax(body: TaxIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        row = svc.add_tax_event(db, body.model_dump(), actor=ctx.user.username, today=today)
    except CryptoError as exc:
        raise _fail(exc) from None
    return {"id": row.id, "tax": svc.tax_view(db, year=row.day.year, today=today)}


@router.delete("/tax/{event_id}", status_code=204)
def delete_tax(event_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        svc.delete_tax_event(db, event_id, actor=ctx.user.username)
    except CryptoError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.put("/settings")
def settings(body: SettingsIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        if body.endpoints is not None:
            svc.set_endpoints(db, body.endpoints, actor=ctx.user.username)
    except CryptoError as exc:
        raise _fail(exc) from None
    if body.in_wealth is not None:
        set_setting(db, "crypto_in_wealth", body.in_wealth)
    return svc.view(db, today=today, now=utcnow())
