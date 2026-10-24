"""Fabrique de l'application : configuration, sécurité, routes, tâche de synchronisation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import (
    accounts,
    agents,
    assistant,
    auth,
    autonomy,
    backups,
    banks,
    channels,
    consents,
    copilot,
    correlations,
    crypto,
    decisions,
    deps,
    documents,
    energy,
    imports,
    initiatives,
    insights,
    loans,
    mail,
    memory,
    obligations,
    opportunities,
    passkeys,
    planb,
    planning,
    procedures,
    push,
    search,
    system,
    transactions,
    vault,
    watch,
    yearahead,
)
from app.channels.gateway import Gateway
from app.config import Settings
from app.db import Base, make_engine, make_sessionmaker, upgrade_schema, utcnow
from app.fiscal.client import FiscalClient
from app.mcp_server import AgentGate, McpDispatch, McpMount, build_mcp, mcp_asgi, silence_sdk_logs
from app.models import CryptoHolding, CryptoWallet
from app.providers.enablebanking import EnableBankingClient
from app.security.auth import SlidingWindowLimiter
from app.security.crypto import Keyring, install_keyring
from app.security.middleware import AccessLogMiddleware, BodyLimitMiddleware, SecurityHeadersMiddleware
from app.services import assistant as assistant_service
from app.services import autonomy as autonomy_service
from app.services import backup as backup_service
from app.services import consents as consent_service
from app.services import crypto as crypto_service
from app.services import energy as energy_service
from app.services import mail as mail_service
from app.services import obligations as obligations_service
from app.services import planning as planning_service
from app.services import vault as vault_service
from app.services import watch as watch_service
from app.services.bank_sync import sync_all
from app.services.categories import seed_categories
from app.services.ingest import repair_aid_kinds

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


def _crypto_sync_once(app: FastAPI) -> int:
    """Relève crypto en arrière-plan : seulement avec ton accord, et seulement s'il y a quelque chose à suivre."""
    with app.state.sessionmaker() as db:
        if not consent_service.granted(db, crypto_service.CONSENT):
            return 0
        if (
            db.scalar(select(func.count()).select_from(CryptoHolding)) == 0
            and db.scalar(select(func.count()).select_from(CryptoWallet)) == 0
        ):
            return 0
        result = crypto_service.sync(db, net=app.state.crypto_net(), now=utcnow(), actor="system")
        crypto_service.snapshot(db, date.today())  # noqa: DTZ011 - date locale du serveur
        db.commit()
        return int(result["wallets_synced"]) + int(result["prices"])


def _autonomy_sweep_once(app: FastAPI) -> bool:
    with app.state.sessionmaker() as db:
        changed = autonomy_service.sweep(db, utcnow())
        db.commit()
        return changed


async def _autonomy_loop(app: FastAPI) -> None:
    """Le pilote automatique n'attend pas qu'on regarde : chaque minute, un mouvement autorisé mais non rapporté
    est compté, le pilote s'arrête, et l'alerte urgente part tout de suite."""
    while True:
        await asyncio.sleep(60)
        try:
            if await asyncio.to_thread(_autonomy_sweep_once, app) and app.state.channels is not None:
                app.state.channels.nudge_threadsafe()
        except Exception:
            log.exception("pilote automatique : ménage en échec")


async def _crypto_loop(app: FastAPI) -> None:
    delay = 120.0
    while True:
        await asyncio.sleep(delay)
        delay = 30 * 60 + random.uniform(0, 300)  # noqa: S311  # nosec B311
        try:
            await asyncio.to_thread(_crypto_sync_once, app)
        except Exception:
            log.exception("relève crypto en échec")


def _planning_sync_once(app: FastAPI, today: date) -> int:
    with app.state.sessionmaker() as db:
        n = planning_service.sync(db, today, fetch=app.state.ics_fetcher)
        db.commit()
        return int(n)


async def _planning_loop(app: FastAPI) -> None:
    """L'agenda de travail change peu : une relecture par jour, peu après le démarrage puis toutes les 24 h."""
    delay = 120.0
    while True:
        await asyncio.sleep(delay)
        delay = 24 * 3600 + random.uniform(0, 1800)  # noqa: S311  # nosec B311
        try:
            count = await asyncio.to_thread(_planning_sync_once, app, date.today())  # noqa: DTZ011
            if count:
                log.info("planning : %s journées relues", count)
        except Exception:
            log.exception("synchronisation du planning en échec")


def _housekeeping_once(app: FastAPI, today: date) -> dict[str, int]:
    with app.state.sessionmaker() as db:
        report = assistant_service.housekeeping(db, today=today, make_client=app.state.llm_client_factory)
        db.commit()
    with app.state.sessionmaker() as db:
        report.update(vault_service.housekeeping(db))
        db.commit()
    with app.state.sessionmaker() as db:  # veille : sources dues seulement, chacune bornée en taille et en durée
        report.update(watch_service.housekeeping(db, now=utcnow(), fetcher=app.state.watch_fetcher))
        db.commit()
    with app.state.sessionmaker() as db:  # déclarations constatées par le versement qui a suivi
        report["obligations_detected"] = obligations_service.auto_detect(db, today)
        db.commit()
    with app.state.sessionmaker() as db:  # sauvegarde chiffrée du jour, restauration d'essai de la semaine
        report.update(backup_service.tick(db, app.state.settings))
        db.commit()
    with app.state.sessionmaker() as db:  # boîte mail en lecture seule : les nouveaux PDF proposés dans Papiers
        factory = getattr(app.state, "mail_client_factory", None) or mail_service.default_client
        report.update(mail_service.tick(db, factory=factory))
        db.commit()
    return report


async def _housekeeping_loop(app: FastAPI) -> None:
    """Toutes les heures : conversations au-delà de la durée choisie effacées, conversations endormies fermées et
    relues (l'assistant apprend même si tu n'as pas cliqué « Nouvelle conversation »)."""
    delay = 300.0
    while True:
        await asyncio.sleep(delay)
        delay = 3600 + random.uniform(0, 300)  # noqa: S311  # nosec B311
        try:
            report = await asyncio.to_thread(_housekeeping_once, app, date.today())  # noqa: DTZ011
            if any(report.values()):
                log.info("entretien : %s", report)
        except Exception:
            log.exception("entretien en échec")


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
        upgrade_schema(engine, Base.metadata)  # colonnes et index apparus depuis la création de la base
        with factory() as db:
            seed_categories(db)
            repaired = repair_aid_kinds(db)  # remboursements pris pour des bourses par une ancienne règle
            if repaired:
                log.info("aides relues : %s opérations corrigées", repaired)
            crypto_service.upgrade(db)
            db.commit()
        tasks = []
        if app.state.enablebanking is not None and settings.sync_interval_hours > 0:
            tasks.append(asyncio.create_task(_sync_loop(app)))
        if settings.energy_enabled:
            tasks.append(asyncio.create_task(_energy_loop(app)))
        tasks.append(asyncio.create_task(_planning_loop(app)))
        tasks.append(asyncio.create_task(_crypto_loop(app)))
        tasks.append(asyncio.create_task(_autonomy_loop(app)))
        tasks.append(asyncio.create_task(_housekeeping_loop(app)))
        # Messageries : rien ne tourne tant qu'aucune n'est configurée, activée et couverte par ton accord.
        gateway: Gateway | None = None
        if settings.channels_enabled:
            gateway = Gateway(
                factory,
                settings,
                llm_factory=lambda: app.state.llm_client_factory,
                limiter=app.state.limiters["channel"],
                push_http=app.state.push_http,
                push_resolver=app.state.watch_resolver,
                fiscal=lambda: app.state.fiscal_client,
                **app.state.channels_options,
            )
            await gateway.start()
        app.state.channels = gateway
        # Le serveur MCP a son propre cycle de vie (gestionnaire de sessions HTTP) : on l'imbrique.
        async with mcp_mount.running() if mcp_mount else contextlib.nullcontext():
            yield
        if gateway is not None:
            await gateway.stop()
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
        "passkey": SlidingWindowLimiter(30, 300),  # un défi par tentative : l'écran de connexion en redemande souvent
        "callback": SlidingWindowLimiter(20, 300),
        "agent": SlidingWindowLimiter(settings.agent_calls_per_minute, 60),
        "assistant": SlidingWindowLimiter(30, 300),  # questions au modèle : 30 par 5 min, en plus des plafonds
        "watch": SlidingWindowLimiter(6, 600),  # relèves de veille à la demande
        "channel": SlidingWindowLimiter(20, 300),  # messages par compte relié (messageries), avant « doucement »
        "backups": SlidingWindowLimiter(6, 3600),  # sauvegardes et restaurations d'essai à la demande
        "mail_scan": SlidingWindowLimiter(6, 3600),  # lectures de la boîte mail à la demande
        "crypto": SlidingWindowLimiter(6, 600),  # relèves crypto à la demande (nœuds publics : on reste poli)
    }
    app.state.enablebanking = _build_enablebanking(settings)
    app.state.fiscal_client = FiscalClient(settings.fiscal_engine_url, settings.fiscal_token_value)
    app.state.energy_client_factory = energy_service.default_client
    app.state.ics_fetcher = planning_service.fetch_ics
    app.state.llm_client_factory = assistant_service.default_client
    app.state.search_http = None  # client HTTP de la recherche web (remplaçable en test)
    app.state.push_http = None  # client HTTP synchrone des notifications Web Push (remplaçable en test)
    app.state.watch_fetcher = watch_service.default_fetcher
    app.state.watch_resolver = None  # résolution DNS réelle (remplaçable en test)
    app.state.channels = None
    app.state.crypto_net = crypto_service.default_net  # réseau réel (remplaçable en test)
    app.state.channels_options = {}  # surcharges de la passerelle (tests : fabrique d'adaptateurs, horloge…)

    routers = (
        auth,
        accounts,
        assistant,
        memory,
        consents,
        transactions,
        imports,
        insights,
        banks,
        agents,
        decisions,
        energy,
        documents,
        planning,
        initiatives,
        vault,
        procedures,
        opportunities,
        watch,
        copilot,
        channels,
        autonomy,
        crypto,
        obligations,
        correlations,
        yearahead,
        passkeys,
        search,
        push,
        planb,
        loans,
        backups,
        mail,
        system,
    )
    for r in routers:
        app.include_router(r.router)

    # Serveur MCP pour les agents IA (HERMÈS). Même horloge que l'API (surchargeable en test).
    silence_sdk_logs()
    mcp = build_mcp(
        factory,
        fiscal=lambda: app.state.fiscal_client,
        today=lambda: app.dependency_overrides.get(deps.today, deps.today)(),
        # Cadence des consignes du pilote : le jour civil de la personne (fuseau BANK_TIMEZONE), pas celui du serveur.
        local_today=lambda: app.dependency_overrides.get(
            deps.today, lambda: utcnow().astimezone(ZoneInfo(settings.timezone)).date()
        )(),
        # Pilote automatique : un mouvement autorisé ou rapporté réveille les alertes des messageries.
        on_autonomy=lambda: app.state.channels.nudge_threadsafe() if app.state.channels is not None else None,
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
        upload_prefixes=("/api/imports", "/api/documents/preview", "/api/planning/file"),
        special_limits={"/api/vault/upload": settings.vault_upload_bytes + 256_000},
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
