"""Middlewares ASGI purs (pas de BaseHTTPMiddleware : streaming préservé, pas de fuite de contexte)."""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Iterable, Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("bank.access")

_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, csp_exempt_prefixes: Iterable[str] = ()) -> None:
        self.app = app
        self.hsts = hsts
        self.csp_exempt = tuple(csp_exempt_prefixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                extra: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
                    # Données financières : jamais en cache (proxy, navigateur, service worker).
                    (b"cache-control", b"no-store"),
                    (b"pragma", b"no-cache"),
                ]
                if not path.startswith(self.csp_exempt):
                    extra.append((b"content-security-policy", _API_CSP.encode()))
                if self.hsts:
                    extra.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains"))
                existing = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in extra if k not in existing)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLargeError(Exception):
    pass


class BodyLimitMiddleware:
    """Coupe toute requête dont le corps dépasse la limite — même sans Content-Length (chunked)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        upload_limit: int,
        upload_prefixes: Iterable[str],
        special_limits: Mapping[str, int] | None = None,
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit
        self.upload_prefixes = tuple(upload_prefixes)
        # Chemins exacts avec leur propre plafond (dépôt de plusieurs documents au coffre, par exemple).
        self.special_limits = dict(special_limits or {})

    def _limit(self, path: str) -> int:
        if path in self.special_limits:
            return self.special_limits[path]
        return self.upload_limit if path.startswith(self.upload_prefixes) else self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["path"])
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    if int(value) > limit:
                        await _reject(send)
                        return
                except ValueError:
                    await _reject(send, 400, b'{"detail":"Content-Length invalide"}')
                    return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if not started:
                await _reject(send)


async def _reject(
    send: Send, status: int = 413, body: bytes = b'{"detail":"Corps de requete trop volumineux"}'
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AccessLogMiddleware:
    """Journal d'accès minimal : jamais la query string (le callback bancaire y porte un code d'autorisation)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = secrets.token_hex(6)
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "%s %s %s %d %.1fms",
                rid,
                scope["method"],
                scope["path"],
                status,
                (time.perf_counter() - start) * 1000,
            )
