"""Adaptateurs de messagerie face à de faux serveurs fidèles aux protocoles : Telegram (long polling), Discord
(passerelle websocket locale), Slack (Socket Mode local), WhatsApp (Graph API, signature), Signal (SSE + JSON-RPC),
ntfy. Aucun réseau : transports httpx simulés et serveurs websocket sur 127.0.0.1."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from websockets.asyncio.server import serve

from app.channels.base import AdapterError, Inbound, SendError, check_ws_url
from app.channels.discord import INTENT_DIRECT_MESSAGES, DiscordAdapter
from app.channels.ntfy import NtfyAdapter
from app.channels.signal import SignalAdapter, parse_event
from app.channels.slack import SlackAdapter
from app.channels.telegram import TelegramAdapter
from app.channels.whatsapp import WhatsAppAdapter

TG_TOKEN = "123456789:" + "S" * 35


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def collector() -> tuple[list[Inbound], Any]:
    got: list[Inbound] = []

    async def deliver(inbound: Inbound) -> None:
        got.append(inbound)

    return got, deliver


# ============================================================================ Telegram
def test_telegram_long_polling_parses_private_messages_and_remembers_the_offset() -> None:
    seen: list[dict[str, Any]] = []
    cursors: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content or b"{}")
        seen.append({"method": method, **body})
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"is_bot": True, "username": "bank_bot"}})
        if method == "deleteWebhook":
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getUpdates" and "offset" not in body:
            private = {
                "message_id": 5,
                "chat": {"id": 42, "type": "private"},
                "text": "/budget",
                "from": {"id": 42, "is_bot": False, "username": "micka"},
            }
            group = {
                "message_id": 6,
                "chat": {"id": -100, "type": "supergroup"},
                "text": "salut",
                "from": {"id": 43, "is_bot": False, "first_name": "Léa"},
            }
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"update_id": 10, "message": private},
                        {"update_id": 11, "message": group},
                        {"update_id": 12},
                    ],
                },
            )
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    async def scenario() -> list[Inbound]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

            async def save(cursor: int) -> None:
                cursors.append(cursor)

            adapter = TelegramAdapter(TG_TOKEN, http, on_cursor=save)
            assert await adapter.check() == {"name": "@bank_bot", "username": "bank_bot"}
            got, deliver = collector()
            with pytest.raises(AdapterError, match="Telegram 401"):
                await adapter.run(deliver)
            return got

    got = run(scenario())
    assert [(i.user_id, i.chat_id, i.text, i.direct, i.label) for i in got] == [
        ("42", "42", "/budget", True, "@micka"),
        ("43", "-100", "salut", False, "Léa"),
    ]
    assert got[0].message_id == "42:5"
    polls = [s for s in seen if s["method"] == "getUpdates"]
    assert polls[0]["allowed_updates"] == ["message"] and polls[0]["timeout"] == 50
    assert polls[1]["offset"] == 13 and cursors == [13], "l'offset est gardé : pas de message rejoué au redémarrage"


def test_telegram_sends_html_falls_back_to_plain_and_never_leaks_its_token() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body.get("parse_mode") == "HTML" and "<b>" in body["text"]:
            return httpx.Response(
                400, json={"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
            )
        if body.get("chat_id") == "blocked":
            return httpx.Response(
                403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked"}
            )
        if body.get("chat_id") == "flood":
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 7},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"échec vers {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            adapter = TelegramAdapter(TG_TOKEN, http)
            await adapter.send("42", "Tu peux dépenser **49 €** <aujourd'hui>")
            assert sent[0]["parse_mode"] == "HTML" and "<b>49 €</b>" in sent[0]["text"] and "&lt;" in sent[0]["text"]
            assert sent[0]["link_preview_options"] == {"is_disabled": True}
            assert "parse_mode" not in sent[1] and sent[1]["text"] == "Tu peux dépenser 49 € <aujourd'hui>"
            with pytest.raises(SendError) as blocked:
                await adapter.send("blocked", "x")
            assert blocked.value.permanent
            with pytest.raises(SendError) as flood:
                await adapter.send("flood", "x")
            assert not flood.value.permanent and flood.value.retry_after == 7
            await adapter.typing("42")
        async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
            with pytest.raises(SendError) as exc:
                await TelegramAdapter(TG_TOKEN, http).send("42", "x")
            assert TG_TOKEN not in str(exc.value) and exc.value.__cause__ is None
            with pytest.raises(AdapterError) as bad:
                await TelegramAdapter(TG_TOKEN, http).check()
            assert TG_TOKEN not in str(bad.value)

    run(scenario())


# ============================================================================ Discord
def test_discord_gateway_identifies_heartbeats_and_keeps_only_direct_messages() -> None:
    identify: list[dict[str, Any]] = []
    beats: list[Any] = []
    posted: list[httpx.Request] = []

    async def gateway(ws: Any) -> None:
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 30}}))
        identify.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "999"}, "session_id": "s"}}))
        dm = {"id": "m1", "channel_id": "dm-1", "content": "/budget", "author": {"id": "42", "username": "micka"}}
        guild = {**dm, "id": "m2", "guild_id": "g1", "channel_id": "c1"}
        mine = {**dm, "id": "m3", "author": {"id": "999", "username": "bank", "bot": True}}
        for seq, data in ((2, dm), (3, guild), (4, mine)):
            await ws.send(json.dumps({"op": 0, "t": "MESSAGE_CREATE", "s": seq, "d": data}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("op") == 1:
                beats.append(msg["d"])
                await ws.send(json.dumps({"op": 11}))
                if msg["d"] == 4:
                    break
        await ws.send(json.dumps({"op": 7}))  # « reconnecte-toi »
        await ws.wait_closed()

    async def scenario() -> list[Inbound]:
        async with serve(gateway, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def rest(request: httpx.Request) -> httpx.Response:
                assert request.headers["authorization"] == "Bot tok.en.xyz"
                path = request.url.path
                if path.endswith("/users/@me"):
                    return httpx.Response(200, json={"id": "999", "username": "bank", "bot": True})
                if path.endswith("/oauth2/applications/@me"):
                    return httpx.Response(200, json={"id": "555"})
                if path.endswith("/gateway/bot"):
                    return httpx.Response(200, json={"url": f"ws://127.0.0.1:{port}"})
                posted.append(request)
                if "/channels/gone/" in path:
                    return httpx.Response(403, json={"message": "Cannot send messages to this user", "code": 50007})
                return httpx.Response(200, json={"id": "out"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(rest)) as http:
                adapter = DiscordAdapter("tok.en.xyz", http)
                assert await adapter.check() == {"name": "bank", "app_id": "555"}
                got, deliver = collector()
                await asyncio.wait_for(adapter.run(deliver), timeout=10)
                await adapter.send("dm-1", "Salut @everyone # Titre")
                body = json.loads(posted[-1].content)
                assert body["allowed_mentions"] == {"parse": []}, "jamais de mention de masse"
                with pytest.raises(SendError) as exc:
                    await adapter.send("gone", "x")
                assert exc.value.permanent
                return got

    got = run(scenario())
    assert identify[0]["op"] == 2 and identify[0]["d"]["intents"] == INTENT_DIRECT_MESSAGES
    assert identify[0]["d"]["token"] == "tok.en.xyz"
    assert beats[-1] == 4, "le battement porte le dernier numéro de séquence"
    assert [(i.user_id, i.chat_id, i.direct, i.from_bot) for i in got] == [
        ("42", "dm-1", True, False),
        ("42", "c1", False, False),
        ("999", "dm-1", True, True),
    ]


def test_discord_closes_a_zombie_connection() -> None:
    async def silent(ws: Any) -> None:
        await ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 20}}))
        async for _ in ws:  # ne répond jamais aux battements
            pass

    async def scenario() -> None:
        async with serve(silent, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def rest(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/gateway/bot"):
                    return httpx.Response(200, json={"url": f"ws://127.0.0.1:{port}"})
                if request.url.path.endswith("/users/@me"):
                    return httpx.Response(200, json={"id": "1", "username": "b", "bot": True})
                return httpx.Response(200, json={"id": "2"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(rest)) as http:
                _, deliver = collector()
                with pytest.raises(Exception):  # noqa: B017 - fermeture côté client : connexion close
                    await asyncio.wait_for(DiscordAdapter("a.b.c", http).run(deliver), timeout=5)

    run(scenario())


def test_websocket_urls_must_be_wss() -> None:
    assert check_ws_url("wss://gateway.discord.gg") == "wss://gateway.discord.gg"
    assert check_ws_url("ws://127.0.0.1:9") == "ws://127.0.0.1:9"
    for bad in ("ws://evil.example", "http://gateway.discord.gg", "wss:", ""):
        with pytest.raises(AdapterError):
            check_ws_url(bad)


# ============================================================================ Slack
def test_slack_socket_mode_acks_every_envelope_and_keeps_human_direct_messages() -> None:
    acks: list[str] = []
    posted: list[dict[str, Any]] = []

    def envelope(eid: str, event: dict[str, Any]) -> str:
        return json.dumps({"envelope_id": eid, "type": "events_api", "payload": {"event": event}})

    async def socket(ws: Any) -> None:
        await ws.send(json.dumps({"type": "hello"}))
        human = {
            "type": "message",
            "channel_type": "im",
            "user": "U42",
            "channel": "D1",
            "text": "a &amp; b &lt;3",
            "client_msg_id": "c1",
            "ts": "1.1",
        }
        await ws.send(envelope("e1", human))
        await ws.send(envelope("e2", {**human, "bot_id": "B1", "client_msg_id": "c2"}))
        await ws.send(envelope("e3", {**human, "channel_type": "channel", "client_msg_id": "c3"}))
        await ws.send(envelope("e4", {**human, "subtype": "message_changed", "client_msg_id": "c4"}))
        for _ in range(4):
            acks.append(json.loads(await ws.recv())["envelope_id"])
        await ws.send(json.dumps({"type": "disconnect", "reason": "refresh_requested"}))
        await ws.wait_closed()

    async def scenario() -> list[Inbound]:
        async with serve(socket, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def api(request: httpx.Request) -> httpx.Response:
                method = request.url.path.rsplit("/", 1)[1]
                token = request.headers["authorization"]
                if method == "auth.test":
                    assert token == "Bearer xoxb-bot"
                    return httpx.Response(200, json={"ok": True, "user": "bank", "user_id": "UBOT", "team": "Maison"})
                if method == "apps.connections.open":
                    assert token == "Bearer xapp-app"
                    return httpx.Response(200, json={"ok": True, "url": f"ws://127.0.0.1:{port}/link"})
                body = json.loads(request.content)
                posted.append(body)
                if body["channel"] == "gone":
                    return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
                if body["channel"] == "busy":
                    return httpx.Response(429, headers={"retry-after": "3"})
                return httpx.Response(200, json={"ok": True})

            async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
                adapter = SlackAdapter("xoxb-bot", "xapp-app", http)
                assert await adapter.check() == {"name": "bank", "team": "Maison"}
                got, deliver = collector()
                await asyncio.wait_for(adapter.run(deliver), timeout=10)
                await adapter.send("D1", "**49 €** par jour <ok>")
                assert posted[-1] == {
                    "channel": "D1",
                    "text": "*49 €* par jour &lt;ok&gt;",
                    "unfurl_links": False,
                    "unfurl_media": False,
                }
                with pytest.raises(SendError) as gone:
                    await adapter.send("gone", "x")
                assert gone.value.permanent
                with pytest.raises(SendError) as busy:
                    await adapter.send("busy", "x")
                assert busy.value.retry_after == 3.0 and not busy.value.permanent
                return got

    got = run(scenario())
    assert acks == ["e1", "e2", "e3", "e4"], "chaque enveloppe est acquittée, même ignorée"
    kept = [i for i in got if i.direct and not i.from_bot]
    assert [(i.user_id, i.chat_id, i.text, i.message_id) for i in kept] == [("U42", "D1", "a & b <3", "c1")]


def test_slack_rejects_bad_tokens_with_a_readable_message() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
            with pytest.raises(AdapterError, match="Jetons Slack refusés"):
                await SlackAdapter("xoxb-1", "xapp-1", http).check()

    run(scenario())


# ============================================================================ WhatsApp
SECRET = "0123456789abcdef0123456789abcdef"


def test_whatsapp_checks_challenge_and_signature_in_constant_time() -> None:
    adapter = WhatsAppAdapter("123456", "EAAG" + "x" * 60, SECRET, "verif-token", httpx.AsyncClient())
    assert adapter.verify_challenge("subscribe", "verif-token", "42") == "42"
    assert adapter.verify_challenge("subscribe", "autre", "42") is None
    assert adapter.verify_challenge("unsubscribe", "verif-token", "42") is None
    assert adapter.verify_challenge("subscribe", "verif-token", None) is None
    body = b'{"object":"whatsapp_business_account"}'
    good = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert adapter.verify_signature(body, good)
    assert not adapter.verify_signature(body + b" ", good)
    assert not adapter.verify_signature(body, good.replace("sha256=", "sha1="))
    assert not adapter.verify_signature(body, None)


def test_whatsapp_parses_only_messages_for_our_number() -> None:
    adapter = WhatsAppAdapter("123456", "EAAG" + "x" * 60, SECRET, "v", httpx.AsyncClient())

    def change(messages: list[dict[str, Any]], pnid: str = "123456") -> dict[str, Any]:
        return {
            "field": "messages",
            "value": {
                "metadata": {"phone_number_id": pnid},
                "contacts": [{"wa_id": "336", "profile": {"name": "Micka"}}],
                "messages": messages,
            },
        }

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    change(
                        [
                            {"from": "336", "id": "w1", "type": "text", "text": {"body": "/budget"}},
                            {
                                "from": "336",
                                "id": "w2",
                                "type": "interactive",
                                "interactive": {
                                    "type": "button_reply",
                                    "button_reply": {"id": "b", "title": "Missions"},
                                },
                            },
                            {"from": "336", "id": "w3", "type": "image", "image": {"id": "m", "caption": "mon avis"}},
                            {"from": "336", "id": "w4", "type": "reaction", "reaction": {"emoji": "👍"}},
                        ]
                    ),
                    change([{"from": "337", "id": "w5", "type": "text", "text": {"body": "autre numéro"}}], pnid="999"),
                    {"field": "account_update", "value": {}},
                ]
            }
        ],
    }
    got = adapter.parse_webhook(payload)
    assert [(i.message_id, i.text, i.label, i.direct) for i in got] == [
        ("w1", "/budget", "Micka", True),
        ("w2", "Missions", "Micka", True),
        ("w3", "", "Micka", True),
    ]
    assert adapter.parse_webhook({"object": "page"}) == [] and adapter.parse_webhook([1, 2]) == []


def test_whatsapp_send_and_error_codes() -> None:
    posted: list[dict[str, Any]] = []

    def graph(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        posted.append(body)
        to = body.get("to")
        if to == "closed":
            return httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})
        if to == "busy":
            return httpx.Response(400, json={"error": {"code": 130429, "message": "Rate limit hit"}})
        if to == "expired":
            return httpx.Response(401, json={"error": {"code": 190, "message": "Error validating access token"}})
        if request.method == "GET":
            return httpx.Response(200, json={"display_phone_number": "+33 6 00", "verified_name": "Bank"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(graph)) as http:
            adapter = WhatsAppAdapter("123456", "EAAG" + "x" * 60, SECRET, "v", http)
            assert await adapter.check() == {"name": "Bank", "display_phone": "33600"}
            await adapter.send("336", "**49 €** [Bank](https://x.example)")
            assert posted[-1] == {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": "336",
                "type": "text",
                "text": {"body": "*49 €* Bank (https://x.example)", "preview_url": False},
            }
            with pytest.raises(SendError, match="fenêtre de 24 h") as closed:
                await adapter.send("closed", "x")
            assert closed.value.permanent
            with pytest.raises(SendError) as busy:
                await adapter.send("busy", "x")
            assert not busy.value.permanent and busy.value.retry_after
            with pytest.raises(SendError, match="expiré") as expired:
                await adapter.send("expired", "x")
            assert expired.value.permanent
            adapter.parse_webhook(
                {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "changes": [
                                {
                                    "field": "messages",
                                    "value": {
                                        "metadata": {"phone_number_id": "123456"},
                                        "messages": [
                                            {"from": "336", "id": "wamid.in", "type": "text", "text": {"body": "hey"}}
                                        ],
                                    },
                                }
                            ]
                        }
                    ],
                }
            )
            await adapter.typing("336")
            assert posted[-1] == {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": "wamid.in",
                "typing_indicator": {"type": "text"},
            }

    run(scenario())


# ============================================================================ Signal
def test_signal_reads_the_event_stream_and_sends_by_json_rpc() -> None:
    rpc: list[dict[str, Any]] = []
    stream = "\n".join(
        [
            ": keepalive",
            "event: receive",
            "data: "
            + json.dumps(
                {
                    "envelope": {
                        "sourceNumber": "+33611",
                        "sourceName": "Micka",
                        "timestamp": 1,
                        "dataMessage": {"message": "/budget"},
                    }
                }
            ),
            "data: "
            + json.dumps(
                {
                    "envelope": {
                        "sourceNumber": "+33611",
                        "timestamp": 2,
                        "dataMessage": {"message": "groupe", "groupInfo": {"groupId": "g"}},
                    }
                }
            ),
            "data: "
            + json.dumps(
                {"envelope": {"sourceNumber": "+33700", "timestamp": 3, "dataMessage": {"message": "moi-même"}}}
            ),
            "data: " + json.dumps({"envelope": {"sourceNumber": "+33611", "timestamp": 4, "receiptMessage": {}}}),
            "data: pas du json",
            "",
        ]
    )

    def daemon(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/check":
            return httpx.Response(200)
        if request.url.path == "/api/v1/events":
            assert request.url.params["account"] == "+33700"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream.encode())
        body = json.loads(request.content)
        rpc.append(body)
        if body["params"].get("recipient") == ["+33999"]:
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"results": [{"type": "UNREGISTERED_FAILURE"}]}},
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"results": [{"type": "SUCCESS"}], "timestamp": 5}}
        )

    async def scenario() -> list[Inbound]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(daemon)) as http:
            adapter = SignalAdapter("http://signal:8080/", "+33700", http)
            assert (await adapter.check())["name"] == "Signal +337…00"
            got, deliver = collector()
            with pytest.raises(AdapterError, match="interrompu"):
                await adapter.run(deliver)
            await adapter.send("+33611", "**49 €** par jour")
            assert rpc[-1]["method"] == "send" and rpc[-1]["params"] == {
                "account": "+33700",
                "recipient": ["+33611"],
                "message": "49 € par jour",
            }
            with pytest.raises(SendError) as gone:
                await adapter.send("+33999", "x")
            assert gone.value.permanent
            await adapter.typing("+33611")
            assert rpc[-1]["method"] == "sendTyping"
            return got

    got = run(scenario())
    assert [(i.user_id, i.text, i.direct, i.label, i.message_id) for i in got] == [
        ("+33611", "/budget", True, "Micka", "+33611:1"),
        ("+33611", "groupe", False, "Signal", "+33611:2"),
    ]
    assert (
        parse_event(
            {
                "params": {
                    "envelope": {
                        "sourceUuid": "u-1",
                        "timestamp": 9,
                        "editMessage": {"dataMessage": {"message": "corrigé"}},
                    }
                }
            },
            "+33700",
        ).text
        == "corrigé"
    )  # type: ignore[union-attr]


def test_signal_unreachable_daemon_is_a_clear_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
            with pytest.raises(AdapterError, match="signal-cli injoignable"):
                await SignalAdapter("http://signal:8080", "+33700", http).check()
            with pytest.raises(SendError, match="injoignable"):
                await SignalAdapter("http://signal:8080", "+33700", http).send("+33611", "x")

    run(scenario())


# ============================================================================ ntfy
def test_ntfy_posts_plain_text_with_title_click_and_token() -> None:
    posted: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"healthy": True})
        posted.append(request)
        return httpx.Response(403 if request.url.path == "/interdit" else 200, json={})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
            adapter = NtfyAdapter(
                "https://ntfy.example.org/", "tk_" + "a" * 29, http, click="https://bank/#/aujourdhui"
            )
            assert await adapter.check() == {"name": "ntfy.example.org"}
            await adapter.send("bank-sujet", "**Alerte** : ton solde")
            req = posted[-1]
            assert req.url == "https://ntfy.example.org/bank-sujet" and req.content == b"Alerte : ton solde"
            assert req.headers["title"] == "Bank" and req.headers["click"] == "https://bank/#/aujourdhui"
            assert req.headers["authorization"] == "Bearer tk_" + "a" * 29
            with pytest.raises(SendError) as denied:
                await adapter.send("interdit", "x")
            assert denied.value.permanent

    run(scenario())
