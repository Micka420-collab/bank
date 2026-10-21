"""Fabrique de l'application : configuration, sécurité, routes, tâche de synchronisation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import (
    accounts,
    agents,
    auth,
    banks,
    decisions,
    deps,
    documents,
    energy,
    imports,
    insights,
    system,
    transactions,
)
from app.config import Settings
from app.db import Base, make_engine, make_sessionmaker
from app.fiscal.client import FiscalClient
from app.mcp_server import AgentGate, McpDispatch, McpMount, build_mcp, mcp_asgi, silence_sdk_logs
from app.providers.enablebanking import EnableBankingClient
from app.security.auth import SlidingWindowLimiter
from app.security.crypto import Keyring, install_keyring
from app.security.middleware import AccessLogMiddleware, BodyLimitMiddleware, SecurityHeadersMiddleware
from app.services import energy as energy_service
from app.services.bank_sync import sync_all
from app.services.categories import seed_categories

log = logging.getLogger("bank")


def _build_enablebanking(settings: Settings) -> EnableBankingClient | None:
    if not settings.enablebanking_enabled or settings.enablebanking_key_file is None:
        return None
    key = settings.enablebanking_key_file.read_bytes()
    return EnableBankingClient(settings.enablebanking_app_id or "", key, settings.enablebanking_base_url)


def _energy_sync_once(app: FastAPI, today: date) -> int:
    with app.state.sessionmaker() as db:
        n = energy_service.sync(db, today, make_client=app.state.energy_client_factory)
        db.commit()
        return int(n)


async def _energy_loop(app: FastAPI) -> None:
    """Les données Linky d'un jour arrivent le lendemain matin : une synchronisation par jour suffit.
    Première passe peu après le démarrage (rattrapage après un arrêt), puis toutes les 24 h, décalée au hasard."""
    delay = 90.0
    while True:
        await asyncio.sleep(delay)
        delay = 24 * 3600 + random.uniform(0, 1800)  # noqa: S311  # nosec B311
        try:
            changed = await asyncio.to_thread(_energy_sync_once, app, date.today())  # noqa: DTZ011
            if changed:
                log.info("énergie : %s relevés mis à jour", changed)
        except Exception:
            log.exception("synchronisation énergie en échec")


async def _sync_loop(app: FastAPI) -> None:
    hours = app.state.settings.sync_interval_hours
    while True:
        # Gigue aléatoire : pas de rafale toujours à la même minute côté banque.
        await asyncio.sleep(hours * 3600 + random.uniform(0, 600))  # noqa: S311  # nosec B311
        try:
            today = date.today()  # noqa: DTZ011 - date locale du serveur
            report = await asyncio.to_thread(sync_all, app.state.sessionmaker, app.state.enablebanking, today)
            log.info("sync auto : %s comptes, %s nouvelles opérations", report.accounts, report.inserted)
        except Exception:
            log.exception("sync auto en échec")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    install_keyring(Keyring(settings.master_keys))  # échoue tôt si la clé est absente ou invalide
    engine = make_engine(settings.database_url)
    factory = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        with factory() as db:
            seed_categories(db)
            db.commit()
        tasks = []
        if app.state.enablebanking is not None and settings.sync_interval_hours > 0:
            tasks.append(asyncio.create_task(_sync_loop(app)))
        if settings.energy_enabled:
            tasks.append(asyncio.create_task(_energy_loop(app)))
        # Le serveur MCP a son propre cycle de vie (gestionnaire de sessions HTTP) : on l'imbrique.
        async with mcp_mount.running() if mcp_mount else contextlib.nullcontext():
            yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        engine.dispose()

    is_dev = settings.env == "dev"
    app = FastAPI(
        title="Bank",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if is_dev else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = factory
    app.state.limiters = {
        "login": SlidingWindowLimiter(10, 300),
        "mfa": SlidingWindowLimiter(10, 300),
        "callback": SlidingWindowLimiter(20, 300),
        "agent": SlidingWindowLimiter(settings.agent_calls_per_minute, 60),
    }
    app.state.enablebanking = _build_enablebanking(settings)
    app.state.fiscal_client = FiscalClient(settings.fiscal_engine_url, settings.fiscal_token_value)
    app.state.energy_client_factory = energy_service.default_client

    for r in (auth, accounts, transactions, imports, insights, banks, agents, decisions, energy, documents, system):
        app.include_router(r.router)

    # Serveur MCP pour les agents IA (HERMÈS). Même horloge que l'API (surchargeable en test).
    silence_sdk_logs()
    mcp = build_mcp(
        factory,
        fiscal=lambda: app.state.fiscal_client,
        today=lambda: app.dependency_overrides.get(deps.today, deps.today)(),
    )
    mcp_mount = (
        McpMount(
            make=lambda: mcp_asgi(mcp, settings),
            wrap=lambda inner: AgentGate(inner, factory, app.state.limiters["agent"]),
        )
        if settings.agents_enabled
        else None
    )
    app.state.mcp = mcp

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # On renvoie l'emplacement et le message, jamais la valeur reçue (elle peut contenir un mot de passe).
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("erreur non gérée", exc_info=exc)
        return JSONResponse({"detail": "Erreur interne"}, status_code=500)

    # Ordre d'exécution : le dernier ajouté est le plus externe. /mcp est aiguillé au plus profond, donc
    # derrière TrustedHost, la limite de taille, les en-têtes de sécurité et le journal d'accès.
    app.add_middleware(McpDispatch, mount=mcp_mount)
    app.add_middleware(
        BodyLimitMiddleware,
        default_limit=256_000,
        upload_limit=settings.max_upload_bytes + 64_000,
        upload_prefixes=("/api/imports", "/api/documents/preview"),
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.cookie_secure,
        csp_exempt_prefixes=("/api/docs",) if is_dev else (),
    )
    # localhost reste accepté pour la sonde de santé interne : une attaque par DNS rebinding arrive avec
    # le nom de domaine de l'attaquant dans Host, jamais « localhost ».
    hosts = [*settings.allowed_hosts, "localhost", "127.0.0.1"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(AccessLogMiddleware)
    return app


def app_factory() -> FastAPI:  # pragma: no cover - point d'entrée uvicorn --factory
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
