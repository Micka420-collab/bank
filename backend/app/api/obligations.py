"""Calendrier des obligations : ce que tu dois déclarer, quand, et les chiffres préparés. Rien n'est envoyé à un
organisme depuis ici : tu déclares toi-même, Bank note que c'est fait (ou le constate par le versement)."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import obligations as svc

router = APIRouter(prefix="/api/obligations", tags=["obligations"])


class ObligationIn(BaseModel):
    kind: str = Field(min_length=2, max_length=24)
    anchor: date | None = None
    label: str | None = Field(default=None, max_length=80)
    cadence_months: int | None = Field(default=None, ge=1, le=60)
    window_days: int | None = Field(default=None, ge=1, le=120)
    consequence: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=300)
    automatic: bool | None = None


class ObligationPatch(BaseModel):
    anchor: date | None = None
    active: bool | None = None
    label: str | None = Field(default=None, max_length=80)
    cadence_months: int | None = Field(default=None, ge=1, le=60)
    window_days: int | None = Field(default=None, ge=1, le=120)
    consequence: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=300)
    automatic: bool | None = None


class DoneIn(BaseModel):
    period: str = Field(min_length=4, max_length=12)
    declared: dict[str, int] | None = None  # montants déclarés par type de ressource, en centimes


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _settings(body: ObligationIn | ObligationPatch) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if body.consequence is not None:
        out["consequence"] = " ".join(body.consequence.split())[:300]
    if body.url is not None:
        url = body.url.strip()
        if url and not url.startswith("https://"):
            raise svc.ObligationError("Le lien doit commencer par https://")
        out["url"] = url[:300]
    if body.automatic is not None:
        out["automatic"] = body.automatic
    return out or None


@router.get("")
def listing(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return svc.view(db, today)


@router.post("", status_code=201)
def add(body: ObligationIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.activate(
            db,
            body.kind,
            actor=ctx.user.username,
            anchor=body.anchor,
            label=body.label,
            cadence_months=body.cadence_months,
            window_days=body.window_days,
            settings=_settings(body),
        )
    except svc.ObligationError as exc:
        raise _fail(exc) from None
    return svc.view(db, today)


@router.post("/suggestions/{kind}/dismiss")
def dismiss(kind: str, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.dismiss_suggestion(db, kind[:24], actor=ctx.user.username)
    except svc.ObligationError as exc:
        raise _fail(exc) from None
    return svc.view(db, today)


@router.patch("/{obligation_id}")
def patch(obligation_id: int, body: ObligationPatch, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.update(
            db,
            obligation_id,
            actor=ctx.user.username,
            anchor=body.anchor,
            active=body.active,
            label=body.label,
            cadence_months=body.cadence_months,
            window_days=body.window_days,
            settings=_settings(body),
        )
    except svc.ObligationError as exc:
        raise _fail(exc) from None
    return svc.view(db, today)


@router.delete("/{obligation_id}", status_code=204)
def remove(obligation_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        svc.delete(db, obligation_id, actor=ctx.user.username)
    except svc.ObligationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.post("/{obligation_id}/done")
def done(obligation_id: int, body: DoneIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    declared = None
    if body.declared is not None:
        if any(v < 0 or v > 100_000_000 for v in body.declared.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Montants déclarés invalides")
        declared = {str(k)[:16]: int(v) for k, v in list(body.declared.items())[:10]}
    try:
        svc.mark_done(db, obligation_id, body.period, actor=ctx.user.username, today=today, declared=declared)
    except svc.ObligationError as exc:
        raise _fail(exc) from None
    return svc.view(db, today)


@router.delete("/{obligation_id}/done/{period}")
def undo(obligation_id: int, period: str, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        svc.undo_done(db, obligation_id, period[:12], actor=ctx.user.username)
    except svc.ObligationError as exc:
        raise _fail(exc) from None
    return svc.view(db, today)


@router.get("/{obligation_id}/prefill")
def prefill(
    obligation_id: int, _: AuthDep, db: DbDep, today: TodayDep, period: str = Query(min_length=7, max_length=12)
) -> dict[str, Any]:
    try:
        return svc.prefill(db, obligation_id, period, today)
    except svc.ObligationError as exc:
        raise _fail(exc) from None
