"""Alertes de cours : tu les fixes, tu les réarmes, tu les supprimes. Bank les lit à chaque relève des cours et
te prévient dans tes messageries ; un agent ne peut ni en créer ni en changer."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep
from app.db import utcnow
from app.services import price_alerts as svc

router = APIRouter(prefix="/api/crypto/alerts", tags=["crypto"])


class AlertIn(BaseModel):
    asset: str = Field(min_length=2, max_length=12)
    kind: Literal["above", "below", "change"]
    value: str = Field(min_length=1, max_length=32)  # euros, ou points de % pour une variation
    label: str | None = Field(default=None, max_length=svc.MAX_LABEL)


def _fail(exc: svc.PriceAlertError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def list_alerts(_: AuthDep, db: DbDep) -> dict[str, Any]:
    return svc.view(db)


@router.post("", status_code=201)
def create(body: AlertIn, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        svc.create(
            db,
            asset=body.asset,
            kind=body.kind,
            value=body.value,
            label=body.label,
            actor=ctx.user.username,
            now=utcnow(),
        )
    except svc.PriceAlertError as exc:
        raise _fail(exc) from None
    return svc.view(db)


@router.post("/{alert_id}/rearm")
def rearm(alert_id: int, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        svc.rearm(db, alert_id, actor=ctx.user.username, now=utcnow())
    except svc.PriceAlertError as exc:
        raise _fail(exc) from None
    return svc.view(db)


@router.delete("/{alert_id}", status_code=204)
def delete(alert_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        svc.delete(db, alert_id, actor=ctx.user.username)
    except svc.PriceAlertError as exc:
        raise _fail(exc) from None
