"""Passerelle : fait tourner les messageries activées, remet chaque message reçu au service, renvoie les réponses
et les alertes. Tourne dans le processus de l'API (un seul worker : un seul lecteur par robot).

- Superviseur : toutes les quelques secondes, compare ce qui doit tourner (accord donné, messagerie activée et
  complète) avec ce qui tourne ; un jeton changé relance la connexion, un accord retiré l'arrête tout de suite.
  Une panne relance avec une attente croissante ; des identifiants refusés attendent plus longtemps.
- Messages reçus : mis en file par messagerie et traités un par un (pas de réponses qui se croisent) ; pendant
  que l'assistant réfléchit, l'indicateur « écrit… » est rafraîchi.
- Envoi : identifiants retirés, mise en forme et découpe par plateforme, trois essais au plus (en respectant le
  délai demandé par la plateforme) ; un refus définitif n'est pas réessayé.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.channels.base import Adapter, AdapterError, Deliver, Inbound, SendError, backoff_sleep
from app.channels.discord import DiscordAdapter
from app.channels.ntfy import NtfyAdapter
from app.channels.signal import SignalAdapter
from app.channels.slack import SlackAdapter
from app.channels.telegram import TelegramAdapter
from app.channels.text import LIMITS, measure_for, scrub, split_message
from app.channels.whatsapp import WhatsAppAdapter
from app.config import Settings
from app.db import utcnow
from app.models import ChannelConfig, ChannelLink
from app.security.auth import SlidingWindowLimiter
from app.services import channel_alerts, channels
from app.services.assistant import ClientFactory

log = logging.getLogger("bank.channels")
# Le jeton d'un robot Telegram fait partie de l'URL de son API : httpx ne doit jamais journaliser ses requêtes.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

T = TypeVar("T")
QUEUE_SIZE = 100
SEND_ATTEMPTS = 3
TYPING_EVERY = 4.0
_FINGERPRINT_EXTRA = ("verify_token", "topic")


def build_adapter(
    platform: str,
    secrets: dict[str, str],
    options: dict[str, Any],
    http: httpx.AsyncClient,
    *,
    cursor: str | None = None,
    on_cursor: Callable[[int], Awaitable[None]] | None = None,
    public_url: str = "",
) -> Adapter:
    if platform == "telegram":
        return TelegramAdapter(
            secrets["token"], http, cursor=int(cursor) if cursor and cursor.isdigit() else None, on_cursor=on_cursor
        )
    if platform == "discord":
        return DiscordAdapter(secrets["token"], http)
    if platform == "slack":
        return SlackAdapter(secrets["bot_token"], secrets["app_token"], http)
    if platform == "whatsapp":
        return WhatsAppAdapter(
            str(options["phone_number_id"]),
            secrets["access_token"],
            secrets["app_secret"],
            str(options.get("verify_token") or ""),
            http,
        )
    if platform == "signal":
        return SignalAdapter(str(options["url"]), str(options["account"]), http)
    if platform == "ntfy":
        return NtfyAdapter(
            str(options.get("server") or "https://ntfy.sh"),
            secrets.get("token"),
            http,
            click=f"{public_url}/#/aujourdhui" if public_url else None,
        )
    raise AdapterError(f"Messagerie inconnue : {platform}")


AdapterFactory = Callable[..., Adapter]


@dataclass(frozen=True)
class Wanted:
    platform: str
    fingerprint: str
    secrets: dict[str, str]
    options: dict[str, Any]
    cursor: str | None


def fingerprint(platform: str, row: ChannelConfig) -> str:
    """Ce qui, s'il change, impose de se reconnecter : les champs que tu saisis (pas le nom du robot, que la
    plateforme nous apprend à chaque connexion)."""
    sec, opt = channels.secrets_of(row), channels.options_of(row)
    keys = [f.key for f in channels.PLATFORMS[platform].fields] + list(_FINGERPRINT_EXTRA)
    material = {k: sec.get(k) or opt.get(k) for k in keys}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


class Gateway:
    def __init__(
        self,
        factory_db: sessionmaker[Session],
        settings: Settings,
        *,
        llm_factory: Callable[[], ClientFactory],
        limiter: SlidingWindowLimiter,
        adapter_factory: AdapterFactory = build_adapter,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] = utcnow,
        poll_seconds: float = 5.0,
        alert_seconds: float = 900.0,
        alert_first_delay: float = 60.0,
    ) -> None:
        self._db = factory_db
        self._settings = settings
        self._llm_factory = llm_factory
        self._limiter = limiter
        self._adapter_factory = adapter_factory
        self._http = http
        self._own_http = http is None
        self._clock = clock
        self._poll = poll_seconds
        self._alert_every = alert_seconds
        self._alert_first = alert_first_delay
        self._zone = ZoneInfo(settings.timezone)
        self.adapters: dict[str, Adapter] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._queues: dict[str, asyncio.Queue[Inbound]] = {}
        self._fingerprints: dict[str, str] = {}
        self._background: list[asyncio.Task[None]] = []
        # La base est synchrone : son travail part sur un petit groupe de fils à nous, qu'on attend à l'arrêt
        # (aucune connexion SQLite laissée ouverte quand l'application ferme son moteur).
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bank-channels")

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, partial(fn, *args))

    # ------------------------------------------------------------ cycle de vie
    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False)
        self._background = [asyncio.create_task(self._loop()), asyncio.create_task(self._alert_loop())]

    async def stop(self) -> None:
        for task in [*self._background, *self._tasks.values()]:
            task.cancel()
        for task in [*self._background, *self._tasks.values()]:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self.adapters.clear()
        await asyncio.get_running_loop().run_in_executor(None, partial(self._executor.shutdown, wait=True))
        if self._own_http and self._http is not None:
            await self._http.aclose()

    def local_now(self) -> datetime:
        return self._clock().astimezone(self._zone)

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:
                log.exception("messageries : superviseur en échec")
            await asyncio.sleep(self._poll)

    def _wanted(self) -> dict[str, Wanted]:
        if not self._settings.channels_enabled:
            return {}
        with self._db() as db:
            return {
                p: Wanted(p, fingerprint(p, row), channels.secrets_of(row), channels.options_of(row), row.cursor)
                for p, row in channels.active_platforms(db).items()
            }

    async def reconcile(self) -> None:
        wanted = await self._run(self._wanted)
        for platform in list(self._tasks):
            target = wanted.get(platform)
            if target is None or target.fingerprint != self._fingerprints.get(platform):
                await self._stop_platform(platform)
                if target is None:
                    await self._run(self._set_status, platform, "off", None)
        for platform, target in wanted.items():
            if platform not in self._tasks:
                self._start_platform(target)

    def _start_platform(self, target: Wanted) -> None:
        assert self._http is not None  # créé par start()  # noqa: S101  # nosec B101
        adapter = self._adapter_factory(
            target.platform,
            target.secrets,
            target.options,
            self._http,
            cursor=target.cursor,
            on_cursor=partial(self._cursor_saver, target.platform),
            public_url=self._settings.public_url,
        )
        queue: asyncio.Queue[Inbound] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.adapters[target.platform] = adapter
        self._queues[target.platform] = queue
        self._fingerprints[target.platform] = target.fingerprint
        self._tasks[target.platform] = asyncio.create_task(self._supervise(adapter, queue))

    async def _stop_platform(self, platform: str) -> None:
        task = self._tasks.pop(platform, None)
        self.adapters.pop(platform, None)
        self._queues.pop(platform, None)
        self._fingerprints.pop(platform, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _supervise(self, adapter: Adapter, queue: asyncio.Queue[Inbound]) -> None:
        platform = adapter.platform
        worker = asyncio.create_task(self._worker(adapter, queue))
        attempt = 0
        try:
            while True:
                try:
                    await self._run(self._set_status, platform, "connecting", None)
                    info = await adapter.check()
                    await self._run(self._record_check, platform, info)
                    await self._run(self._set_status, platform, "online", None)
                    attempt = 0
                    await adapter.run(self._deliverer(queue))
                    await asyncio.sleep(1.0)  # la plateforme a demandé une reconnexion
                except asyncio.CancelledError:
                    raise
                except AdapterError as exc:
                    attempt += 1
                    await self._run(self._set_status, platform, "error", str(exc))
                    await backoff_sleep(attempt, base=15.0, cap=900.0)
                except Exception as exc:
                    attempt += 1
                    log.warning("messagerie %s : connexion perdue (%s)", platform, type(exc).__name__)
                    await self._run(
                        self._set_status, platform, "error", "Connexion perdue, nouvelle tentative en cours"
                    )
                    await backoff_sleep(attempt)
        finally:
            worker.cancel()

    def _deliverer(self, queue: asyncio.Queue[Inbound]) -> Deliver:
        async def deliver(inbound: Inbound) -> None:
            try:
                queue.put_nowait(inbound)
            except asyncio.QueueFull:
                log.warning("messagerie %s : file pleine, message ignoré", inbound.platform)

        return deliver

    async def submit(self, inbound: Inbound) -> bool:
        """Remise d'un message arrivé par webhook (WhatsApp) : traité par la même file que les autres."""
        queue = self._queues.get(inbound.platform)
        if queue is None:
            return False
        await self._deliverer(queue)(inbound)
        return True

    async def _worker(self, adapter: Adapter, queue: asyncio.Queue[Inbound]) -> None:
        while True:
            inbound = await queue.get()
            try:
                await self.process(adapter, inbound)
            except Exception:
                log.exception("messagerie %s : message non traité", adapter.platform)

    # ------------------------------------------------------------ un message
    async def process(self, adapter: Adapter, inbound: Inbound) -> None:
        adm = await self._run(self._admit, inbound)
        if adm.replies:
            await self.reply(adapter, inbound.chat_id, adm.replies, adm.privacy)
        if adm.announce:
            await self.broadcast(adm.announce, exclude_link=adm.link_id)
        if adm.ask is None:
            return
        typing = asyncio.create_task(self._keep_typing(adapter, inbound.chat_id))
        try:
            answers = await self._run(self._answer, adm)
        finally:
            typing.cancel()
        await self.reply(adapter, inbound.chat_id, answers, adm.privacy)

    def _admit(self, inbound: Inbound) -> channels.Admission:
        with self._db() as db:
            local = self.local_now()
            adm = channels.admit(
                db,
                inbound,
                now=self._clock(),
                today=local.date(),
                public_url=self._settings.public_url,
                limiter=self._limiter,
            )
            db.commit()
            return adm

    def _answer(self, adm: channels.Admission) -> list[str]:
        with self._db() as db:
            out = channels.answer(db, adm, today=self.local_now().date(), make_client=self._llm_factory())
            db.commit()
            return out

    async def _keep_typing(self, adapter: Adapter, chat_id: str) -> None:
        while True:
            with contextlib.suppress(Exception):
                await adapter.typing(chat_id)
            await asyncio.sleep(TYPING_EVERY)

    async def reply(self, adapter: Adapter, chat_id: str, texts: list[str], privacy: str) -> None:
        for text in texts:
            await self.send(adapter, chat_id, text, privacy)

    async def send(self, adapter: Adapter, chat_id: str, text: str, privacy: str) -> None:
        """Retire les identifiants, découpe pour la plateforme (marge pour la mise en forme), envoie avec réessais."""
        platform = adapter.platform
        limit = int(LIMITS.get(platform, 2000) * 0.85)
        for chunk in split_message(scrub(text, privacy=privacy), limit, measure_for(platform)):
            for attempt in range(SEND_ATTEMPTS):
                try:
                    await adapter.send(chat_id, chunk)
                    break
                except SendError as exc:
                    if exc.permanent or attempt == SEND_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(exc.retry_after if exc.retry_after is not None else 2.0**attempt)

    async def broadcast(self, text: str, *, exclude_link: int | None = None) -> None:
        """Prévient tes autres comptes reliés (un nouveau compte vient d'être relié, par exemple)."""
        targets = await self._run(self._targets, exclude_link)
        for platform, chat_ref, privacy in targets:
            adapter = self.adapters.get(platform)
            if adapter is None:
                continue
            try:
                await self.send(adapter, chat_ref, text, privacy)
            except SendError as exc:
                log.warning("messagerie %s : alerte non remise (%s)", platform, exc)

    def _targets(self, exclude: int | None) -> list[tuple[str, str, str]]:
        with self._db() as db:
            out = []
            for link in db.scalars(select(ChannelLink).where(ChannelLink.revoked_at.is_(None))):
                if link.id == exclude:
                    continue
                cfg = db.get(ChannelConfig, link.platform)
                out.append((link.platform, link.chat_ref, cfg.privacy if cfg else "redacted"))
            return out

    # ------------------------------------------------------------ alertes
    async def _alert_loop(self) -> None:
        await asyncio.sleep(self._alert_first)
        while True:
            try:
                await self.run_alerts()
            except Exception:
                log.exception("messageries : alertes en échec")
            await asyncio.sleep(self._alert_every)

    async def run_alerts(self) -> int:
        await self._run(self._prune)
        if not self.adapters:
            return 0
        local = self.local_now()
        deliveries = await self._run(self._plan, set(self.adapters), local)
        sent = 0
        for delivery in deliveries:
            adapter = self.adapters.get(delivery.platform)
            if adapter is None:
                continue
            try:
                await self.send(adapter, delivery.chat_ref, delivery.text, delivery.privacy)
            except SendError as exc:
                await self._run(self._record_delivery, delivery, False, str(exc), exc.permanent)
                continue
            await self._run(self._record_delivery, delivery, True, "", False)
            sent += 1
        return sent

    def _plan(self, platforms: set[str], local: datetime) -> list[channel_alerts.Delivery]:
        with self._db() as db:
            return channel_alerts.plan(
                db, platforms=platforms, today=local.date(), local=local, public_url=self._settings.public_url
            )

    def _record_delivery(self, delivery: channel_alerts.Delivery, ok: bool, error: str, permanent: bool) -> None:
        with self._db() as db:
            channel_alerts.record(db, delivery, ok=ok, error=error, permanent=permanent, now=self._clock())
            db.commit()

    def _prune(self) -> None:
        with self._db() as db:
            channels.prune_seen(db, self._clock())
            db.commit()

    # ------------------------------------------------------------ état en base
    def _set_status(self, platform: str, status: str, error: str | None) -> None:
        with self._db() as db:
            channels.set_status(db, platform, status, error=error, now=self._clock())
            db.commit()

    def _record_check(self, platform: str, info: dict[str, Any]) -> None:
        with self._db() as db:
            channels.record_check(db, platform, info, now=self._clock())
            db.commit()

    async def _cursor_saver(self, platform: str, cursor: int) -> None:
        await self._run(self._save_cursor, platform, cursor)

    def _save_cursor(self, platform: str, cursor: int) -> None:
        with self._db() as db:
            row = db.get(ChannelConfig, platform)
            if row is not None:
                row.cursor = str(cursor)
                db.commit()

    async def test(self, platform: str) -> dict[str, Any]:
        """« Tester » dans l'écran Messageries : vérifie les identifiants enregistrés sans rien démarrer."""
        assert self._http is not None  # noqa: S101  # nosec B101
        with self._db() as db:
            row = db.get(ChannelConfig, platform)
            if row is None:
                raise channels.ChannelError("Messagerie non configurée", 404)
            missing = channels.missing_fields(row, platform)
            if missing:
                raise channels.ChannelError("Il manque : " + ", ".join(missing))
            sec, opt = channels.secrets_of(row), channels.options_of(row)
        adapter = self._adapter_factory(platform, sec, opt, self._http, public_url=self._settings.public_url)
        try:
            info = await adapter.check()
        except AdapterError as exc:
            await self._run(self._set_status, platform, "error", str(exc))
            raise channels.ChannelError(str(exc), 502) from None
        await self._run(self._record_check, platform, info)
        return {"ok": True, "name": info.get("name")}

    def today(self) -> date:
        return self.local_now().date()
