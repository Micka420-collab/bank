"""Démarches : modèles, dossiers, valeurs, pièces, revue, validation, envoi déclaré, suivi, résultat."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep
from app.services import procedures

router = APIRouter(prefix="/api/procedures", tags=["procedures"])


class CreateIn(BaseModel):
    template: str = Field(min_length=1, max_length=32)
    opportunity_key: str | None = Field(default=None, max_length=160)
    initial: dict[str, Any] = Field(default_factory=dict, max_length=30)
    title: str | None = Field(default=None, max_length=120)


class FieldsIn(BaseModel):
    values: dict[str, Any] = Field(max_length=30)


class PiecesIn(BaseModel):
    document_ids: list[int] = Field(max_length=20)


class UpdateIn(BaseModel):
    notes: str | None = Field(default=None, max_length=4_000)
    title: str | None = Field(default=None, max_length=120)


class ApproveIn(BaseModel):
    hash: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")
    declarations: list[str] = Field(default_factory=list, max_length=20)


class SentIn(BaseModel):
    reference: str | None = Field(default=None, max_length=120)
    sent_on: date | None = None


class SnoozeIn(BaseModel):
    days: int = Field(ge=1, le=120)


class CloseIn(BaseModel):
    outcome: str = Field(pattern="^(obtenu|refuse|termine|abandon)$")
    amount_cents: int | None = Field(default=None, ge=1, le=100_000_000)
    period: str = Field(default="once", pattern="^(once|month|year)$")
    note: str | None = Field(default=None, max_length=300)


def _http(exc: procedures.ProcedureError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def listing(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return procedures.listing(db, today)


@router.post("", status_code=201)
def create(body: CreateIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        proc = procedures.create(
            db,
            body.template,
            actor=ctx.user.username,
            today=today,
            opportunity_key=body.opportunity_key,
            initial=body.initial,
            title=body.title,
        )
        return procedures.view(db, proc.id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.get("/{proc_id}")
def detail(proc_id: int, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.patch("/{proc_id}/fields")
def set_fields(proc_id: int, body: FieldsIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.set_fields(db, proc_id, body.values, actor=ctx.user.username, today=today)
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.put("/{proc_id}/pieces/{piece_key}")
def set_pieces(
    proc_id: int, piece_key: str, body: PiecesIn, ctx: AuthDep, db: DbDep, today: TodayDep
) -> dict[str, Any]:
    try:
        procedures.set_pieces(db, proc_id, piece_key[:32], body.document_ids, actor=ctx.user.username, today=today)
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.patch("/{proc_id}")
def update(proc_id: int, body: UpdateIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.update(db, proc_id, actor=ctx.user.username, notes=body.notes, title=body.title)
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.post("/{proc_id}/approve")
def approve(proc_id: int, body: ApproveIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.approve(
            db, proc_id, payload_hash=body.hash, declarations=body.declarations, actor=ctx.user.username, today=today
        )
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.post("/{proc_id}/sent")
def mark_sent(proc_id: int, body: SentIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.mark_sent(
            db, proc_id, actor=ctx.user.username, today=today, reference=body.reference, sent_on=body.sent_on
        )
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.post("/{proc_id}/snooze")
def snooze(proc_id: int, body: SnoozeIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.snooze(db, proc_id, days=body.days, actor=ctx.user.username, today=today)
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.post("/{proc_id}/close")
def close(proc_id: int, body: CloseIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        procedures.close(
            db,
            proc_id,
            outcome=body.outcome,
            actor=ctx.user.username,
            today=today,
            amount_cents=body.amount_cents,
            period=body.period,
            note=body.note,
        )
        return procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc


@router.get("/{proc_id}/letter")
def letter(proc_id: int, _: AuthDep, db: DbDep, today: TodayDep) -> Response:
    try:
        view = procedures.view(db, proc_id, today)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc
    if not view["letter"]:
        raise HTTPException(404, "Pas de lettre pour cette démarche")
    name = f"{view['title'][:60]}.txt"
    return Response(
        view["letter"],
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{proc_id}", status_code=204)
def delete(proc_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        procedures.delete(db, proc_id, actor=ctx.user.username)
    except procedures.ProcedureError as exc:
        raise _http(exc) from exc
