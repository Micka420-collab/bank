"""Veille : ce qui change dans les sources suivies, données de référence à revérifier, sources."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.services import future_radar, investment_compare, purchasing_power, watch

router = APIRouter(prefix="/api/watch", tags=["watch"])


class RefreshIn(BaseModel):
    force: bool = False
    source_id: int | None = None


class ItemIn(BaseModel):
    status: str = Field(pattern="^(new|seen|dismissed)$")


class SourceIn(BaseModel):
    url: str = Field(min_length=9, max_length=2_000)
    name: str | None = Field(default=None, max_length=120)


class SourceUpdateIn(BaseModel):
    enabled: bool


def _http(exc: watch.WatchError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("")
def listing(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return watch.listing(db, today)


@router.get("/radar")
def radar(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    """Prévisions personnelles et contexte macro vérifiable, sans entraînement externe ni scraping de compte."""
    return future_radar.view(db, today)


@router.get("/purchasing-power")
def salary_power(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    """Salaires observés et inflation INSEE documentée, jamais une estimation de bulletin absent."""
    return purchasing_power.view(db, today)


class YieldScenario(BaseModel):
    gross_pct: float = Field(ge=-99, le=200)
    fees_pct: float = Field(ge=0, le=30)
    tax_pct: float = Field(ge=0, le=100)
    inflation_pct: float = Field(ge=-29, le=100)


@router.post("/purchasing-power/yield")
def estimate_real_yield(body: YieldScenario, _: AuthDep) -> dict[str, Any]:
    """Hypothèse mathématique nette de frais et de fiscalité simplifiée, non une offre."""
    from app.domain.purchasing_power import real_yield_pct

    return {
        **real_yield_pct(body.gross_pct, body.fees_pct, body.tax_pct, body.inflation_pct),
        "assumptions": body.model_dump(),
        "note": "Simulation annuelle théorique. La fiscalité réelle dépend de l'enveloppe, de la durée "
        "et de la situation personnelle. Rendement et capital des placements risqués non garantis.",
    }


class InvestmentCompareIn(BaseModel):
    capital_cents: int = Field(ge=10_000, le=100_000_000)
    horizon_months: int = Field(ge=1, le=360)
    inflation_pct: float = Field(ge=-29, le=100)
    custom_gross_pct: float | None = Field(default=None, gt=-99, le=200)
    custom_fees_pct: float = Field(default=0, ge=0, le=30)
    custom_tax_pct: float = Field(default=0, ge=0, le=100)
    custom_loss_pct: float = Field(default=25, ge=0, le=100)
    custom_locked_months: int = Field(default=0, ge=0, le=360)


@router.post("/investments/compare")
def compare_investments(body: InvestmentCompareIn, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    """Même capital/horizon ; vérification du matelas et des lignes rouges avant la comparaison."""
    try:
        return investment_compare.view(db, today, **body.model_dump())
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/refresh")
async def refresh(body: RefreshIn, request: Request, ctx: AuthDep, today: TodayDep) -> dict[str, Any]:
    """Relève à la demande (bornée) ; le réseau est interrogé hors de la boucle principale."""
    if not request.app.state.limiters["watch"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Relève déjà demandée il y a peu : réessaie plus tard")

    def run() -> dict[str, int]:
        with request.app.state.sessionmaker() as db:
            report = watch.refresh(
                db,
                now=utcnow(),
                fetcher=request.app.state.watch_fetcher,
                force=body.force,
                source_id=body.source_id,
            )
            db.commit()
            return report

    report = await asyncio.to_thread(run)
    with request.app.state.sessionmaker() as db:
        return {"report": report, **watch.listing(db, today)}


@router.patch("/items/{item_id}", status_code=204)
def set_item(item_id: int, body: ItemIn, ctx: AuthDep, db: DbDep) -> None:
    try:
        watch.set_item_status(db, item_id, body.status, actor=ctx.user.username)
    except watch.WatchError as exc:
        raise _http(exc) from exc


@router.post("/items/seen")
def all_seen(ctx: AuthDep, db: DbDep) -> dict[str, int]:
    return {"seen": watch.mark_all_seen(db, actor=ctx.user.username)}


@router.post("/sources", status_code=201)
def add_source(body: SourceIn, request: Request, ctx: AuthDep, db: DbDep) -> dict[str, Any]:
    try:
        source = watch.add_source(
            db, body.url, name=body.name, actor=ctx.user.username, resolver=request.app.state.watch_resolver
        )
    except watch.WatchError as exc:
        raise _http(exc) from exc
    return {"id": source.id, "name": source.name}


@router.patch("/sources/{source_id}", status_code=204)
def update_source(source_id: int, body: SourceUpdateIn, ctx: AuthDep, db: DbDep) -> None:
    try:
        watch.update_source(db, source_id, enabled=body.enabled, actor=ctx.user.username)
    except watch.WatchError as exc:
        raise _http(exc) from exc


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, ctx: AuthDep, db: DbDep) -> None:
    try:
        watch.delete_source(db, source_id, actor=ctx.user.username)
    except watch.WatchError as exc:
        raise _http(exc) from exc
