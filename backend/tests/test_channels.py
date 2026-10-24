"""Bank dans tes messageries : réglages chiffrés, appairage, passerelle de bout en bout (adaptateur simulé dans la
vraie boucle de l'application), commandes, assistant, secrets refusés, alertes, webhook WhatsApp signé."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from app.channels.base import Inbound, SendError
from app.channels.gateway import Gateway, build_adapter
from app.config import Settings
from app.main import create_app
from app.models import AuditLog, ChannelConfig, ChannelLink, ChannelNotice, Procedure
from app.security.crypto import get_keyring
from app.services import channel_alerts, channels
from tests.conftest import PASSWORD
from tests.llm import FakeLlm, configure

TOKEN = "123456789:" + "A" * 35
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)  # 12 h à Paris
CLOCK: dict[str, datetime] = {"now": NOW}
PNID = "109876543210987"
APP_SECRET = "0123456789abcdef0123456789abcdef"
WA_TOKEN = "EAAG" + "x" * 60


class FakeAdapter:
    """Messagerie simulée : enregistre ce qu'on lui envoie ; la passerelle lui donne sa fonction de remise."""

    instances: ClassVar[list[FakeAdapter]] = []

    def __init__(self, platform: str, secrets: dict[str, str], options: dict[str, Any], http: Any, **_: Any) -> None:
        self.platform = platform
        self.secrets = secrets
        self.sent: list[tuple[str, str]] = []
        self.typed: list[str] = []
        self.deliver: Callable[[Inbound], Any] | None = None
        self.fail: SendError | None = None
        FakeAdapter.instances.append(self)

    async def check(self) -> dict[str, Any]:
        return {"name": "@bank_test_bot", "username": "bank_test_bot"}

    async def run(self, deliver: Callable[[Inbound], Any]) -> None:
        self.deliver = deliver
        await asyncio.Event().wait()

    async def send(self, chat_id: str, text: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((chat_id, text))

    async def typing(self, chat_id: str) -> None:
        self.typed.append(chat_id)


GRAPH: list[httpx.Request] = []


def _graph(request: httpx.Request) -> httpx.Response:
    GRAPH.append(request)
    if request.method == "GET":
        return httpx.Response(200, json={"display_phone_number": "+33 6 12 34 56 78", "verified_name": "Bank"})
    return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})


def _factory(platform: str, secrets: dict[str, str], options: dict[str, Any], http: Any, **kw: Any) -> Any:
    if platform == "whatsapp":
        return build_adapter(platform, secrets, options, http, **kw)
    return FakeAdapter(platform, secrets, options, http, **kw)


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeAdapter.instances = []
    GRAPH.clear()
    CLOCK["now"] = NOW


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    application = create_app(settings)
    application.state.channels_options = {
        "adapter_factory": _factory,
        "poll_seconds": 0.05,
        "alert_first_delay": 3600.0,
        "clock": lambda: CLOCK["now"],
        "http": httpx.AsyncClient(transport=httpx.MockTransport(_graph)),
    }
    return application


def wait_for(cond: Callable[[], Any], timeout: float = 5.0) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition jamais remplie")


def enable(client: TestClient, platform: str = "telegram", **values: str) -> dict[str, Any]:
    assert client.put("/api/consents/channels", json={"granted": True}).status_code == 200
    r = client.put(f"/api/channels/{platform}", json={"values": values or {"token": TOKEN}, "enabled": True})
    assert r.status_code == 200, r.text
    return r.json()


def adapter_for(platform: str = "telegram") -> FakeAdapter:
    return wait_for(lambda: next((a for a in FakeAdapter.instances if a.platform == platform and a.deliver), None))


def say(client: TestClient, adapter: FakeAdapter, text: str, mid: str, *, user: str = "42", **kw: Any) -> None:
    assert adapter.deliver is not None
    inbound = Inbound(adapter.platform, user, kw.pop("chat", user), text, mid, label=kw.pop("label", "@micka"), **kw)
    client.portal.call(adapter.deliver, inbound)  # type: ignore[union-attr]


def platform_view(client: TestClient, key: str = "telegram") -> dict[str, Any]:
    return next(p for p in client.get("/api/channels").json()["platforms"] if p["key"] == key)


def pair(client: TestClient, adapter: FakeAdapter, *, user: str = "42", mid: str = "pair") -> None:
    code = client.post("/api/channels/pairing", json={"password": PASSWORD}).json()["raw"]
    before = len(adapter.sent)
    say(client, adapter, f"/start {code}", mid, user=user)
    wait_for(lambda: len(adapter.sent) > before)


# ============================================================================ réglages
def test_settings_are_validated_encrypted_and_never_returned(auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    view = auth_client.get("/api/channels").json()
    assert view["consent"] is False and view["server_enabled"] is True
    assert [p["key"] for p in view["platforms"]] == [
        "telegram",
        "whatsapp",
        "whatsapp_qr",
        "signal",
        "discord",
        "slack",
        "ntfy",
    ]
    bad = auth_client.put("/api/channels/telegram", json={"values": {"token": "pas-un-jeton"}})
    assert bad.status_code == 422 and "format inattendu" in bad.json()["detail"]
    assert auth_client.put("/api/channels/telegram", json={"values": {"nope": "x"}}).status_code == 422
    assert (
        auth_client.put("/api/channels/whatsapp", json={"values": {"phone_number_id": PNID}, "enabled": True})
        .json()["detail"]
        .startswith("Il manque : Jeton d'accès")
    )
    assert auth_client.put("/api/channels/inconnue", json={}).status_code == 404
    r = auth_client.put("/api/channels/telegram", json={"values": {"token": TOKEN}, "privacy": "aggregates"})
    assert r.status_code == 200 and TOKEN not in r.text and TOKEN[10:] not in r.text
    tg = platform_view(auth_client)
    assert tg["configured"] is True and tg["enabled"] is False and tg["privacy"] == "aggregates"
    assert tg["fields"][0] == {
        "key": "token",
        "label": "Jeton du robot",
        "secret": True,
        "required": True,
        "example": "123456789:AAH…",
        "set": True,
        "hint": "123456…",
        "value": None,
    }
    row = db.get(ChannelConfig, "telegram")
    assert TOKEN not in (db.connection().exec_driver_sql("SELECT secrets FROM channel_configs").scalar() or "")
    assert channels.secrets_of(row) == {"token": TOKEN}
    # Champ vide = valeur gardée ; l'audit trace quels champs ont changé, jamais leur valeur.
    assert auth_client.put("/api/channels/telegram", json={"values": {"token": ""}}).status_code == 200
    audits = [
        json.loads(a.details) for a in db.scalars(select(AuditLog).where(AuditLog.action == "channel.configured"))
    ]
    assert audits[0]["fields"] == ["token"] and audits[-1]["fields"] == [] and TOKEN not in json.dumps(audits)
    assert auth_client.delete("/api/channels/telegram").status_code == 204
    assert platform_view(auth_client)["configured"] is False


def test_whatsapp_and_ntfy_get_what_bank_generates(auth_client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    auth_client.put("/api/consents/channels", json={"granted": True})
    auth_client.put(
        "/api/channels/whatsapp",
        json={"values": {"phone_number_id": PNID, "access_token": WA_TOKEN, "app_secret": APP_SECRET}},
    )
    wa = platform_view(auth_client, "whatsapp")
    assert wa["extra"]["webhook_url"] == "https://testserver/api/channels/whatsapp/webhook"
    assert (
        len(wa["extra"]["verify_token"]) >= 24 and APP_SECRET not in json.dumps(wa) and WA_TOKEN not in json.dumps(wa)
    )
    auth_client.put("/api/channels/ntfy", json={"values": {}, "enabled": True})
    nt = platform_view(auth_client, "ntfy")
    url = nt["extra"]["subscribe_url"]
    assert url.startswith("https://ntfy.sh/bank-") and len(url.rsplit("/", 1)[1]) >= 25
    assert [link["label"] for link in nt["links"]] == ["Alertes ntfy"], "ntfy : le sujet tient lieu de compte relié"
    auth_client.put("/api/channels/ntfy", json={"values": {"server": "https://ntfy.example.org/"}})
    assert platform_view(auth_client, "ntfy")["extra"]["subscribe_url"].startswith("https://ntfy.example.org/bank-")
    export = auth_client.post("/api/export", json={"password": PASSWORD}).json()
    assert {c["platform"] for c in export["channels"]} == {"whatsapp", "ntfy"}
    dumped = json.dumps(export)
    assert APP_SECRET not in dumped and WA_TOKEN not in dumped and url.rsplit("/", 1)[1] not in dumped


def test_a_connection_check_never_overwrites_settings_saved_meanwhile(app, db) -> None:  # type: ignore[no-untyped-def]
    """La passerelle note ce que la plateforme dit du robot pendant que tu enregistres tes réglages : ni l'un ni
    l'autre ne réécrit les options lues avant l'autre (ce qui remettait l'ancien serveur ntfy, par intermittence)."""
    channels.configure(db, "ntfy", values={}, privacy=None, enabled=None, actor="t")
    db.commit()
    with app.state.sessionmaker() as check:
        channels.record_check(check, "ntfy", {"name": "ntfy.sh", "username": "bank"}, now=NOW)

        def save() -> None:
            with app.state.sessionmaker() as s:
                server = {"server": "https://ntfy.example.org"}
                channels.configure(s, "ntfy", values=server, privacy=None, enabled=None, actor="t")
                s.commit()

        saver = threading.Thread(target=save)
        saver.start()
        saver.join(timeout=0.5)  # sans verrou, l'enregistrement passe ici, entre la lecture et l'écriture du contrôle
        check.commit()
    saver.join()
    db.expire_all()
    opt = channels.options_of(db.get(ChannelConfig, "ntfy"))
    assert opt["server"] == "https://ntfy.example.org" and opt["username"] == "bank"


def test_pairing_needs_consent_and_password(auth_client: TestClient, db) -> None:  # type: ignore[no-untyped-def]
    assert auth_client.post("/api/channels/pairing", json={"password": PASSWORD}).status_code == 409
    auth_client.put("/api/consents/channels", json={"granted": True})
    assert auth_client.post("/api/channels/pairing", json={"password": "pas le bon"}).status_code == 403
    r = auth_client.post("/api/channels/pairing", json={"password": PASSWORD})
    assert r.status_code == 201
    code = r.json()
    assert len(code["raw"]) == 8 and code["code"] == f"{code['raw'][:4]}-{code['raw'][4:]}"
    assert set(code["raw"]) <= set(channels._ALPHABET)
    stored = json.dumps(db.connection().exec_driver_sql("SELECT value FROM app_settings").scalars().all())
    assert code["raw"] not in stored, "seule l'empreinte salée est gardée"
    assert auth_client.get("/api/channels").json()["pairing"]["active"] is True
    assert auth_client.delete("/api/channels/pairing").status_code == 204
    assert auth_client.get("/api/channels").json()["pairing"]["active"] is False
    assert {"channel.pairing_denied", "channel.pairing_started", "channel.pairing_cancelled"} <= set(
        db.scalars(select(AuditLog.action))
    )


def test_codes_expire_lock_after_five_misses_and_serve_once(db) -> None:  # type: ignore[no-untyped-def]
    from app.services import consents

    consents.set_consent(db, "channels", True, actor="t")
    out = channels.start_pairing(db, now=NOW, actor="t")
    assert channels._try_pair(db, "AAAAAAAA", NOW) == "bad"
    assert channels._try_pair(db, out["raw"], NOW + timedelta(minutes=16)) == "none", "expiré"
    out = channels.start_pairing(db, now=NOW, actor="t")
    for _ in range(5):
        assert channels._try_pair(db, "AAAAAAAA", NOW) == "bad"
    assert channels._try_pair(db, out["raw"], NOW) == "none", "cinq erreurs : le code est brûlé"
    out = channels.start_pairing(db, now=NOW, actor="t")
    assert channels._try_pair(db, out["raw"], NOW) == "ok"
    assert channels._try_pair(db, out["raw"], NOW) == "none", "usage unique"
    assert channels.normalize_code("/start abcd-efgh") == "ABCDEFGH"
    assert channels.normalize_code("/lier@bank_bot ABCD EFGH") == "ABCDEFGH"
    assert channels.normalize_code("bonjour tout le monde") is None
    assert channels.normalize_code("ABCD0OIL") is None, "0, O, I et L n'existent pas dans un code"


# ============================================================================ passerelle de bout en bout
def test_a_full_conversation_on_telegram(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    assert configure(world).status_code == 200
    enable(world)
    adapter = adapter_for()
    assert platform_view(world)["status"] == "online" and platform_view(world)["bot_name"] == "@bank_test_bot"
    # Un inconnu : aucune réponse, il ne sait même pas que ce robot est relié à Bank.
    say(world, adapter, "salut", "m1", user="999")
    wait_for(lambda: platform_view(world)["ignored"] == 1)
    assert adapter.sent == []
    pair(world, adapter)
    welcome = adapter.sent[-1][1]
    assert adapter.sent[-1][0] == "42" and "relié à Bank" in welcome and "/budget" in welcome
    links = platform_view(world)["links"]
    assert [(link["label"], link["notify"]) for link in links] == [("@micka", True)]
    link = db.scalar(select(ChannelLink))
    assert link.user_index == get_keyring().blind_index("42", "channel.telegram")
    raw = db.connection().exec_driver_sql("SELECT user_ref, chat_ref, label FROM channel_links").one()
    assert "42" not in raw and "@micka" not in raw, "identifiant et nom du compte ne sont stockés que chiffrés"
    # Commandes
    say(world, adapter, "/budget", "m3")
    wait_for(lambda: len(adapter.sent) == 2)
    assert "par jour" in adapter.sent[-1][1] and "https://testserver/#/aujourdhui" in adapter.sent[-1][1]
    say(world, adapter, "missions", "m4")  # sans barre oblique, comme on l'écrit sur WhatsApp
    wait_for(lambda: len(adapter.sent) == 3)
    assert "Ta tirelire" in adapter.sent[-1][1]
    say(world, adapter, "/demarches", "m5")
    wait_for(lambda: len(adapter.sent) == 4)
    assert "Aucune démarche en cours" in adapter.sent[-1][1]
    # Une vraie question : l'assistant, avec les consignes « messagerie » et l'indicateur de saisie.
    calls = len(FakeLlm.calls)
    say(world, adapter, "Je peux m'offrir un resto ce soir ?", "m6")
    wait_for(lambda: len(adapter.sent) == 5)
    assert adapter.sent[-1] == ("42", FakeLlm.reply)
    assert "42" in adapter.typed
    system = [m["content"] for m in FakeLlm.calls[calls]["messages"] if m["role"] == "system"]
    assert any("Tu réponds dans Telegram" in s for s in system)
    first_conv = db.scalar(select(ChannelLink.conversation_id))
    say(world, adapter, "Et demain ?", "m7")
    wait_for(lambda: len(adapter.sent) == 6)
    db.expire_all()
    assert db.scalar(select(ChannelLink.conversation_id)) == first_conv, (
        "même conversation tant qu'on ne dit pas /nouveau"
    )
    say(world, adapter, "/nouveau", "m8")
    wait_for(lambda: len(adapter.sent) == 7)
    db.expire_all()
    assert db.scalar(select(ChannelLink.conversation_id)) is None
    # Le même message réessayé par la plateforme : une seule réponse.
    say(world, adapter, "/budget", "m3")
    time.sleep(0.2)
    assert len(adapter.sent) == 7
    # Un secret collé par erreur : refusé, jamais montré au modèle.
    calls = len(FakeLlm.calls)
    say(world, adapter, "mon mot de passe est Soleil2024!", "m9")
    wait_for(lambda: len(adapter.sent) == 8)
    assert adapter.sent[-1][1].startswith("Stop : ton message contient un mot de passe ou un code")
    assert len(FakeLlm.calls) == calls
    # Groupes et robots : jamais.
    say(world, adapter, "/budget", "g1", direct=False)
    say(world, adapter, "/budget", "b1", from_bot=True)
    time.sleep(0.2)
    assert len(adapter.sent) == 8
    # Délier depuis la messagerie : plus aucune réponse ensuite.
    say(world, adapter, "/delier", "m10")
    wait_for(lambda: len(adapter.sent) == 9)
    say(world, adapter, "/budget", "m11")
    time.sleep(0.2)
    assert len(adapter.sent) == 9 and platform_view(world)["links"] == []
    actions = set(db.scalars(select(AuditLog.action)))
    assert {"channel.linked", "channel.secret_refused", "channel.unlinked", "assistant.ask"} <= actions


def test_channel_privacy_only_tightens_what_the_model_sees(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    configure(world, privacy="full")
    enable(world)
    world.put("/api/channels/telegram", json={"privacy": "aggregates"})
    adapter = adapter_for()
    pair(world, adapter)
    calls = len(FakeLlm.calls)
    say(world, adapter, "Où part mon argent ?", "q1")
    wait_for(lambda: len(FakeLlm.calls) > calls)
    dossier = next(m["content"] for m in FakeLlm.calls[calls]["messages"] if m["content"].startswith("<dossier>"))
    assert "Confidentialité : aggregates" in dossier


def test_without_assistant_commands_still_work_and_a_flood_is_slowed(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    pair(world, adapter)
    say(world, adapter, "Combien il me reste ?", "a1")
    wait_for(lambda: len(adapter.sent) == 2)
    assert "pas encore branché" in adapter.sent[-1][1]
    for i in range(25):
        say(world, adapter, "/aide", f"f{i}")
    wait_for(lambda: any("Doucement" in text for _, text in adapter.sent))


def test_strangers_are_throttled_before_any_write(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    for i in range(30):
        say(world, adapter, "spam", f"s{i}", user=str(1000 + i))
    wait_for(lambda: platform_view(world)["ignored"] == 20)
    time.sleep(0.3)
    assert platform_view(world)["ignored"] == 20, "au-delà de 20 inconnus en 5 minutes : plus rien n'est écrit"
    assert adapter.sent == []


def test_a_new_link_is_announced_to_the_other_accounts(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    pair(world, adapter, user="42")
    pair(world, adapter, user="77", mid="pair2")
    wait_for(lambda: any(chat == "42" and "Nouveau compte relié" in text for chat, text in adapter.sent))
    assert not any(chat == "77" and "Nouveau compte relié" in text for chat, text in adapter.sent)


def test_revoking_consent_stops_everything(world: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter_for()
    gateway: Gateway = app.state.channels
    assert "telegram" in gateway.adapters
    world.put("/api/consents/channels", json={"granted": False})
    wait_for(lambda: "telegram" not in gateway.adapters)
    wait_for(lambda: platform_view(world)["status"] == "off")
    world.put("/api/consents/channels", json={"granted": True})
    wait_for(lambda: "telegram" in gateway.adapters)
    # Un nouveau jeton relance la connexion avec le nouveau jeton.
    new_token = "987654321:" + "B" * 35
    world.put("/api/channels/telegram", json={"values": {"token": new_token}})
    wait_for(lambda: any(a.secrets.get("token") == new_token and a.deliver for a in FakeAdapter.instances))


def test_a_new_bot_unlinks_accounts_but_a_renewed_whatsapp_token_does_not(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    pair(world, adapter)
    assert len(platform_view(world)["links"]) == 1
    world.put("/api/channels/telegram", json={"privacy": "full"})
    assert len(platform_view(world)["links"]) == 1, "la confidentialité ne touche pas aux comptes reliés"
    world.put("/api/channels/telegram", json={"values": {"token": "987654321:" + "Z" * 35}})
    assert platform_view(world)["links"] == [], "nouveau robot : on se relie à nouveau, mot de passe compris"
    assert "channel.links_reset" in set(db.scalars(select(AuditLog.action)))
    _link(db, "whatsapp", "33600")
    world.put(
        "/api/channels/whatsapp",
        json={"values": {"phone_number_id": PNID, "access_token": WA_TOKEN, "app_secret": APP_SECRET}},
    )
    assert len(platform_view(world, "whatsapp")["links"]) == 0, "nouveau numéro : comptes déliés"
    _link(db, "whatsapp", "33601")
    world.put("/api/channels/whatsapp", json={"values": {"access_token": "EAAG" + "n" * 60}})
    assert len(platform_view(world, "whatsapp")["links"]) == 1, "jeton renouvelé : rien ne change pour toi"


def test_send_an_essai_to_a_linked_account(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    pair(world, adapter)
    link_id = platform_view(world)["links"][0]["id"]
    assert world.post(f"/api/channels/links/{link_id}/test").json() == {"ok": True}
    assert "Essai réussi" in adapter.sent[-1][1]
    adapter.fail = SendError("Telegram 403 : bot was blocked by the user", permanent=True)
    r = world.post(f"/api/channels/links/{link_id}/test")
    assert r.status_code == 502 and "blocked" in r.json()["detail"]
    assert world.patch(f"/api/channels/links/{link_id}", json={"notify": False}).json() == {
        "id": link_id,
        "notify": False,
    }
    assert world.delete(f"/api/channels/links/{link_id}").status_code == 204
    assert world.delete(f"/api/channels/links/{link_id}").status_code == 404


# ============================================================================ alertes
def _waiting_procedure(db, next_check: date) -> Procedure:  # type: ignore[no-untyped-def]
    proc = Procedure(
        template="prime_activite", title="Prime d'activité de Micka", stage="waiting", data="{}", next_check=next_check
    )
    db.add(proc)
    db.commit()
    return proc


def _link(db, platform: str = "telegram", user: str = "42") -> ChannelLink:  # type: ignore[no-untyped-def]
    link = ChannelLink(
        platform=platform,
        user_index=get_keyring().blind_index(user, f"channel.{platform}"),
        user_ref=user,
        chat_ref=user,
        label="@micka",
    )
    db.add(link)
    db.commit()
    return link


def test_alerts_are_grouped_sent_once_and_respect_quiet_hours(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    _link(db)
    proc = _waiting_procedure(db, date(2026, 10, 20))
    gateway: Gateway = app.state.channels
    CLOCK["now"] = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)  # 0 h 30 à Paris : heures calmes
    assert world.portal.call(gateway.run_alerts) == 0  # type: ignore[union-attr]
    CLOCK["now"] = NOW
    assert world.portal.call(gateway.run_alerts) == 1  # type: ignore[union-attr]
    text = adapter.sent[-1][1]
    assert "« Demander la prime d'activité » : pas de réponse depuis l'envoi" in text, "titre du modèle (anonymisé)"
    assert "Micka" not in text
    assert f"https://testserver/#/demarches/{proc.id}" in text
    assert world.portal.call(gateway.run_alerts) == 0, "déjà envoyée"  # type: ignore[union-attr]
    assert db.scalar(select(ChannelNotice.key)) == f"proc:{proc.id}:2026-10-20"
    # Changer la confidentialité ne coupe pas la connexion : elle s'applique au message suivant.
    world.put("/api/channels/telegram", json={"privacy": "full"})
    time.sleep(0.2)
    assert gateway.adapters["telegram"] is adapter
    # Le point du dimanche soir, une fois par semaine.
    CLOCK["now"] = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)  # dimanche 18 h 30 à Paris
    assert world.portal.call(gateway.run_alerts) == 1  # type: ignore[union-attr]
    assert adapter.sent[-1][1].startswith("☀️ Ta semaine avec Bank")
    assert world.portal.call(gateway.run_alerts) == 0  # type: ignore[union-attr]


def test_a_dead_target_is_paused_after_three_refusals(world: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(world)
    adapter = adapter_for()
    link = _link(db)
    gateway: Gateway = app.state.channels
    adapter.fail = SendError("Telegram 403 : bot was blocked by the user", permanent=True)
    for day in (18, 19, 20):
        _waiting_procedure(db, date(2026, 10, day))
        world.portal.call(gateway.run_alerts)  # type: ignore[union-attr]
    db.expire_all()
    row = db.get(ChannelLink, link.id)
    assert row.notify is False and row.failures == 3 and "blocked" in (row.last_error or "")
    assert db.scalar(select(ChannelNotice)) is None, "rien n'est noté comme envoyé"


def test_quiet_hours_and_preferences(auth_client: TestClient) -> None:
    assert channel_alerts.is_quiet(datetime(2026, 1, 1, 23), [21, 8])
    assert channel_alerts.is_quiet(datetime(2026, 1, 1, 3), [21, 8])
    assert not channel_alerts.is_quiet(datetime(2026, 1, 1, 12), [21, 8])
    assert channel_alerts.is_quiet(datetime(2026, 1, 1, 13), [13, 14])
    assert not channel_alerts.is_quiet(datetime(2026, 1, 1, 13), [9, 9])
    assert (
        auth_client.put("/api/channels/preferences", json={"quiet_hours": [22, 7], "weekly": False}).status_code == 200
    )
    view = auth_client.get("/api/channels").json()
    assert view["quiet_hours"] == [22, 7] and view["weekly"] is False
    assert auth_client.put("/api/channels/preferences", json={"quiet_hours": [25, 7]}).status_code == 422
    assert auth_client.put("/api/channels/preferences", json={"quiet_hours": [1]}).status_code == 422


def test_compose_groups_and_summarises() -> None:
    notices = [channel_alerts.Notice(f"k{i}", f"alerte {i}") for i in range(7)]
    text = channel_alerts.compose(notices)
    assert text.startswith("Bank te prévient :") and "alerte 4" in text and "alerte 5" not in text
    assert text.endswith("… et 2 autres choses dans l'application.")
    assert channel_alerts.compose(notices[:1]) == "alerte 0"


# ============================================================================ webhook WhatsApp
def _signed(body: bytes) -> dict[str, str]:
    return {"x-hub-signature-256": "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()}


def _wa_message(text: str, mid: str, sender: str = "33612345678", pnid: str = PNID) -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": pnid},
                            "contacts": [{"wa_id": sender, "profile": {"name": "Micka"}}],
                            "messages": [{"from": sender, "id": mid, "type": "text", "text": {"body": text}}],
                            "statuses": [{"id": "wamid.s", "status": "read"}],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


def test_whatsapp_webhook_is_verified_before_anything(auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    url = "/api/channels/whatsapp/webhook"
    assert auth_client.get(url, params={"hub.mode": "subscribe"}).status_code == 404, "rien d'activé : rien à dire"
    enable(auth_client, "whatsapp", phone_number_id=PNID, access_token=WA_TOKEN, app_secret=APP_SECRET)
    gateway: Gateway = app.state.channels
    wait_for(lambda: "whatsapp" in gateway.adapters and platform_view(auth_client, "whatsapp")["status"] == "online")
    wa = platform_view(auth_client, "whatsapp")
    assert wa["bot_name"] == "Bank" and wa["extra"]["display_phone"] == "33612345678"
    verify = wa["extra"]["verify_token"]
    ok = auth_client.get(
        url, params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "1158201444"}
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    assert (
        auth_client.get(
            url, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}
        ).status_code
        == 403
    )
    body = _wa_message("bonjour", "wamid.1")
    assert auth_client.post(url, content=body, headers={"x-hub-signature-256": "sha256=00"}).status_code == 401
    assert auth_client.post(url, content=body).status_code == 401
    tampered = body.replace(b"bonjour", b"bonsoir")
    assert auth_client.post(url, content=tampered, headers=_signed(body)).status_code == 401
    assert auth_client.post(url, content=body, headers=_signed(body)).json() == {"ok": True}
    wait_for(lambda: platform_view(auth_client, "whatsapp")["ignored"] == 1)
    assert not [r for r in GRAPH if r.method == "POST"], "un inconnu n'obtient aucune réponse"
    code = auth_client.post("/api/channels/pairing", json={"password": PASSWORD}).json()["code"]
    body = _wa_message(code.lower(), "wamid.2")
    assert auth_client.post(url, content=body, headers=_signed(body)).status_code == 200
    sent = wait_for(lambda: [r for r in GRAPH if r.method == "POST" and json.loads(r.content).get("type") == "text"])
    payload = json.loads(sent[0].content)
    assert payload["to"] == "33612345678" and payload["messaging_product"] == "whatsapp"
    assert "relié à Bank" in payload["text"]["body"] and payload["text"]["preview_url"] is False
    assert sent[0].headers["authorization"] == f"Bearer {WA_TOKEN}"
    assert sent[0].url.path == f"/v20.0/{PNID}/messages"
    # Meta réessaie : même identifiant, aucune seconde réponse.
    before = len(GRAPH)
    auth_client.post(url, content=body, headers=_signed(body))
    time.sleep(0.2)
    assert len(GRAPH) == before
    # Un message pour un autre numéro du même compte Meta : ignoré.
    other = _wa_message("/budget", "wamid.3", pnid="111111111111")
    auth_client.post(url, content=other, headers=_signed(other))
    time.sleep(0.2)
    assert len(GRAPH) == before


# ============================================================================ interrupteurs serveur
def test_test_button_checks_saved_credentials(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    assert auth_client.post("/api/channels/telegram/test").status_code == 404, "rien d'enregistré"
    assert auth_client.post("/api/channels/inconnue/test").status_code == 404
    auth_client.put("/api/channels/whatsapp", json={"values": {"phone_number_id": PNID}})
    r = auth_client.post("/api/channels/whatsapp/test")
    assert r.status_code == 422 and r.json()["detail"].startswith("Il manque")
    auth_client.put("/api/channels/telegram", json={"values": {"token": TOKEN}})
    assert auth_client.post("/api/channels/telegram/test").json() == {"ok": True, "name": "@bank_test_bot"}
    assert platform_view(auth_client)["bot_name"] == "@bank_test_bot"


def test_server_switch_and_public_webhook_address(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import SETUP_TOKEN, USERNAME, _key

    with pytest.raises(ValueError, match="https"):
        Settings(env="test", database_url="sqlite://", master_key=_key(), channels_public_url="http://x.example")
    off = Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path / 'off.db'}",
        master_key=_key(),
        setup_token=SETUP_TOKEN,
        public_url="https://testserver",
        allowed_hosts=["testserver"],
        fiscal_engine_url="http://fiscal.invalid",
        channels_enabled=False,
        channels_public_url="https://webhook.example.org/",
    )
    with TestClient(create_app(off), base_url="https://testserver") as client:
        client.post("/api/setup", json={"setup_token": SETUP_TOKEN, "username": USERNAME, "password": PASSWORD})
        client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
        client.headers["x-csrf-token"] = client.cookies["bank_csrf"]
        view = client.get("/api/channels").json()
        assert view["server_enabled"] is False
        wa = next(p for p in view["platforms"] if p["key"] == "whatsapp")
        assert wa["extra"]["webhook_url"] == "https://webhook.example.org/api/channels/whatsapp/webhook"
        assert client.post("/api/channels/telegram/test").status_code == 503
        assert client.get("/api/channels/whatsapp/webhook", params={"hub.mode": "subscribe"}).status_code == 404
