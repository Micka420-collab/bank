"""Énergie : connexion Linky (Conso API), synchronisation, tarif, analyse, puissance souscrite, couleur Tempo,
consommation corrigée du climat, simulation des options et prix repère du gaz."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import AuthDep, DbDep, TodayDep, client_ip
from app.db import utcnow
from app.security import audit
from app.services import consents, energy, tempo, weather

router = APIRouter(prefix="/api/energy", tags=["energy"])


class LinkyIn(BaseModel):
    prm: str = Field(min_length=14, max_length=20)
    token: str = Field(min_length=40, max_length=4000)


class TariffIn(BaseModel):
    cents_per_100kwh: int = Field(ge=1, le=100_000)
    subscription_cents_per_year: int = Field(ge=0, le=1_000_000)
    label: str = Field(default="Tarif personnalisé", max_length=60)


class ContractIn(BaseModel):
    option: Literal["base", "hphc", "tempo"]
    kva: int = Field(ge=3, le=36)
    hc: str | None = Field(default=None, max_length=40)  # tes heures creuses, ex. « 22:00-06:00 »


def _analysis(db: DbDep, today: date) -> dict[str, Any]:
    """Simulation des options, climat, courbe de charge : seulement avec un compteur connecté."""
    if energy.active_connection(db) is None:
        return {}
    c = energy.contract(db)
    sim = energy.simulation(
        db,
        today,
        tempo_colors=tempo.colors(db, today - timedelta(days=367), today),
        tempo_known=consents.granted(db, tempo.CONSENT),
    )
    check = energy.climate(db, today, weather.temps(db, today - timedelta(days=800)))
    return {
        "simulation": energy.simulation_view(sim, c["option"] if c else None),
        "climate": energy.climate_view(check),
        "curve": energy.curve_view(db, today),
    }


def _status(db: DbDep, today: TodayDep) -> dict[str, Any]:
    return {
        **energy.status(db, today),
        **_analysis(db, today),
        "tempo": tempo.view(db, today),
        "weather": weather.view(db),
        "gas": energy.gas_view(db, today),
    }


@router.get("")
def get_status(_: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    return _status(db, today)


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
    energy.sync_max_power(db, today, make_client=make_client, full=True)
    energy.sync_load_curve(db, today, make_client=make_client)
    return {**_status(db, today), "imported": changed}


@router.post("/sync")
def sync(request: Request, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if energy.active_connection(db) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Aucun compteur connecté")
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    changed = energy.sync(db, today, make_client=request.app.state.energy_client_factory)
    audit.record(db, ctx.user.username, "energy.sync_manual", changed=changed)
    energy.sync_max_power(db, today, make_client=request.app.state.energy_client_factory)
    energy.sync_load_curve(db, today, make_client=request.app.state.energy_client_factory)
    return {**_status(db, today), "imported": changed}


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


@router.put("/contract")
def put_contract(body: ContractIn, ctx: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    try:
        energy.set_contract(db, option=body.option, kva=body.kva, hc=body.hc)
    except energy.EnergyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    audit.record(db, ctx.user.username, "energy.contract", option=body.option, kva=body.kva, hc=bool(body.hc))
    return _status(db, today)


@router.post("/tempo/refresh")
def refresh_tempo(request: Request, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    fetch = tempo.default_fetch(http=request.app.state.tempo_http, resolver=request.app.state.tempo_resolver)
    try:
        result = tempo.refresh(db, now=utcnow(), today=today, fetch=fetch)
        if tempo.missing_days(db, today):  # premier accord : les couleurs de l'année, pour la simulation Tempo
            result["error"] = (
                result["error"] or tempo.refresh_history(db, now=utcnow(), today=today, fetch=fetch)["error"]
            )
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {**_status(db, today), "tempo_error": result["error"]}


@router.post("/weather/refresh")
def refresh_weather(request: Request, _: AuthDep, db: DbDep, today: TodayDep) -> dict[str, Any]:
    if not request.app.state.limiters["callback"].hit(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de tentatives")
    fetch = weather.default_fetch(http=request.app.state.weather_http, resolver=request.app.state.weather_resolver)
    try:
        result = weather.refresh(db, now=utcnow(), today=today, fetch=fetch)
    except consents.ConsentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except weather.WeatherError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {**_status(db, today), "weather_error": result["error"]}
