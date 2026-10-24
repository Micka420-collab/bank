"""Alertes de cours : une fois par franchissement (hystérésis, mise à jour conditionnelle, état en base), lues à
chaque relève des cours et envoyées par le circuit commun des messageries — des constats, jamais des conseils."""

from __future__ import annotations

import asyncio
import re
import threading
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.channels.gateway import Gateway
from app.config import Settings
from app.db import utcnow
from app.main import _crypto_sync_once, create_app
from app.models import AuditLog, ChannelNotice, CryptoPrice, PriceAlert
from app.services import channel_alerts, consents
from app.services import crypto as crypto_service
from app.services import price_alerts as svc
from app.services.settings_store import set_setting
from tests.conftest import PASSWORD
from tests.test_channels import CLOCK, FakeAdapter, _factory, _graph, _link, adapter_for, enable
from tests.test_crypto import FakeNet

PARIS = ZoneInfo("Europe/Paris")
T0 = datetime(2026, 10, 21, 12, 5, tzinfo=UTC)  # 14 h 05 à Paris
# Ce qu'une alerte ne dit jamais : pas d'invitation à agir, pas d'urgence, pas de « bonne affaire ».
FORBIDDEN = (
    "achet", "achat", "vend", "profit", "maintenant", "opportunit", "occasion", "fonce", "urgent", "depeche",
    "signal", "recommand", "conseille", "investi", "rate pas", "dernier moment", "acquier", "renforce", "allege",
    "moment",
)  # fmt: skip


def price(db: Session, asset: str, eur: str, change: str | None = None, at: datetime = T0) -> None:
    row = db.get(CryptoPrice, asset) or CryptoPrice(asset=asset)
    row.eur, row.change_24h, row.fetched_at = eur, change, at
    db.add(row)
    db.flush()


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


# ------------------------------------------------------------------ textes
def test_no_template_ever_reads_as_advice() -> None:
    samples = [*svc.TEMPLATES.values()]
    for kind in svc.KINDS:
        alert = PriceAlert(
            id=1, asset="BTC", kind=kind, threshold="5" if kind == "change" else "50000", label="Mon repère",
            fires=1, triggered_at=T0, triggered_price="49812.4", triggered_change="-7.25", created_at=T0,
        )  # fmt: skip
        samples += [
            svc.text(alert, privacy=p, public_url="https://bank", tz=PARIS) for p in ("full", "redacted", "aggregates")
        ]
    for sample in samples:
        words = fold(sample)
        hits = [w for w in FORBIDDEN if re.search(rf"\b{w}", words)]
        assert not hits, (sample, hits)
        assert "!" not in sample, "pas de ton pressant"


def test_texts_are_factual_and_follow_privacy() -> None:
    alert = PriceAlert(
        id=3, asset="BTC", kind="below", threshold="50000", label="Plancher", fires=1, triggered_at=T0,
        triggered_price="49812.4", created_at=T0,
    )  # fmt: skip
    full = svc.text(alert, privacy="full", public_url="https://bank", tz=PARIS)
    assert full.startswith("📉 BTC est passé sous 50 000 € (49 812 € à 14 h 05) — seuil que tu as fixé.")
    assert "« Plancher »" in full and full.endswith("pas un conseil. https://bank/#/crypto")
    assert "Plancher" not in svc.text(alert, privacy="redacted", public_url="https://bank", tz=PARIS)
    generic = svc.text(alert, privacy="aggregates", public_url="https://bank", tz=PARIS)
    assert "BTC" not in generic and "50" not in generic
    change = PriceAlert(
        id=4, asset="ETH", kind="change", threshold="5", fires=1, triggered_at=T0, triggered_price="2950.5",
        triggered_change="-7.25", created_at=T0,
    )  # fmt: skip
    assert "ETH a varié de −7,25 % sur 24 h (2 950 € à 14 h 05)" in svc.text(
        change, privacy="redacted", public_url="https://bank", tz=PARIS
    )
    assert svc.eur(Decimal("3.4271")) == "3,42 €"
    assert svc.eur(Decimal("0.000123456789")) == "0,000123456 €"
    assert svc.eur(Decimal("0.5")) == "0,5 €"
    assert svc.percent(Decimal("2.5"), signed=True) == "+2,5 %"


# ------------------------------------------------------------------ règles
def test_bounds_and_guards(db: Session) -> None:
    with pytest.raises(svc.PriceAlertError, match="sans cours"):
        svc.create(db, asset="BTC", kind="above", value="70000", label=None, actor="micka", now=T0)
    price(db, "BTC", "60000", "2.5")
    bad = [
        ({"kind": "up"}, "Type"),
        ({"value": "abc"}, "illisible"),
        ({"value": "-5"}, "positif"),
        ({"value": "2000000000"}, "milliard"),
        ({"value": "1.123456789"}, "huit décimales"),
        ({"kind": "change", "value": "1"}, "Variation"),
        ({"kind": "change", "value": "95"}, "Variation"),
        ({"kind": "change", "value": "5.25"}, "dixième"),
        ({"label": "x" * 61}, "60 caractères"),
        ({"label": "abandon ability able about above absent absorb abstract absurd abuse access accident"}, "secret"),
        ({"value": "55000"}, "déjà au-dessus"),
        ({"kind": "below", "value": "65000"}, "déjà sous"),
        ({"kind": "change", "value": "2.5"}, r"varie déjà de \+2,5\s% sur 24 h, au-delà de 2,5\s%"),
    ]
    for over, message in bad:
        args: dict[str, Any] = {"asset": "btc", "kind": "above", "value": "70000", "label": None, **over}
        with pytest.raises(svc.PriceAlertError, match=message):
            svc.create(db, actor="micka", now=T0, **args)
    for i in range(svc.MAX_ALERTS):
        svc.create(
            db, asset="BTC", kind="above", value=str(70_000 + i), label=f"  repère   {i} ", actor="micka", now=T0
        )
    with pytest.raises(svc.PriceAlertError, match="20 alertes au plus"):
        svc.create(db, asset="BTC", kind="above", value="99999", label=None, actor="micka", now=T0)
    assert db.scalar(select(PriceAlert.label)) == "repère 0"
    with pytest.raises(svc.PriceAlertError, match="introuvable"):
        svc.delete(db, 999, actor="micka")
    with pytest.raises(svc.PriceAlertError, match="introuvable"):
        svc.rearm(db, 999, actor="micka", now=T0)


def test_once_per_crossing_with_hysteresis(db: Session) -> None:
    price(db, "BTC", "49000", "1.0")
    up = svc.create(db, asset="BTC", kind="above", value="50000", label=None, actor="micka", now=T0)
    move = svc.create(db, asset="BTC", kind="change", value="5", label=None, actor="micka", now=T0)
    seq = [
        ("49999.99", "1.0", 0, "armed"),
        ("50000", "1.0", 1, "triggered"),  # le seuil lui-même compte
        ("51000", "1.0", 0, "triggered"),  # reste au-dessus : rien de plus
        ("49600", "1.0", 0, "triggered"),  # sous le seuil, mais dans la marge de 1 % : pas de clignotement
        ("49500", "1.0", 0, "armed"),  # 1 % sous le seuil : réarmée
        ("50100", "1.0", 1, "triggered"),  # deuxième franchissement, deuxième envoi
    ]
    for i, (eur, change, fired, state) in enumerate(seq):
        at = T0 + timedelta(minutes=30 * i)
        price(db, "BTC", eur, change, at=at)
        assert svc.evaluate(db, now=at) == fired, eur
        assert db.get(PriceAlert, up.id).state == state, eur  # type: ignore[union-attr]
    row = db.get(PriceAlert, up.id)
    assert row is not None and row.fires == 2 and row.triggered_price == "50100"
    # Variation : en valeur absolue, réarmée un point sous le seuil.
    at = T0 + timedelta(hours=4)
    for change, fired, state in (("-5.5", 1, "triggered"), ("-4.5", 0, "triggered"), ("3.9", 0, "armed")):
        price(db, "BTC", "50100", change, at=at)
        assert svc.evaluate(db, now=at) == fired
        assert db.get(PriceAlert, move.id).state == state  # type: ignore[union-attr]
    # Un cours périmé ne déclenche rien.
    price(db, "BTC", "40000", "-20", at=at)
    down = svc.create(db, asset="BTC", kind="below", value="30000", label=None, actor="micka", now=at)
    price(db, "BTC", "29000", "-20", at=at)
    assert svc.evaluate(db, now=at + timedelta(hours=3)) == 0
    assert db.get(PriceAlert, down.id).state == "armed"  # type: ignore[union-attr]
    # Un cours illisible non plus.
    price(db, "BTC", "n'importe quoi", None, at=at)
    assert svc.evaluate(db, now=at) == 0


def test_manual_rearm_waits_for_the_other_side(db: Session) -> None:
    price(db, "ETH", "3000")
    alert = svc.create(db, asset="ETH", kind="below", value="2900", label=None, actor="micka", now=T0)
    price(db, "ETH", "2800")
    assert svc.evaluate(db, now=T0) == 1
    with pytest.raises(svc.PriceAlertError, match="toujours sous"):
        svc.rearm(db, alert.id, actor="micka", now=T0)
    price(db, "ETH", "2910")  # au-dessus du seuil, mais dans la marge : seule toi peux la réarmer
    assert svc.evaluate(db, now=T0) == 0
    assert svc.rearm(db, alert.id, actor="micka", now=T0).state == "armed"
    assert svc.rearm(db, alert.id, actor="micka", now=T0).state == "armed", "déjà armée : rien ne change"
    actions = list(db.scalars(select(AuditLog.action).where(AuditLog.action.like("price_alert.%"))))
    assert actions == ["price_alert.created", "price_alert.rearmed"]


def test_change_alert_is_never_born_or_rearmed_beyond(db: Session) -> None:
    """Variation : déjà au-delà = refusée, comme les seuils ; sinon elle tirerait au relevé suivant sans
    franchissement, et un réarmement à la demande enverrait un second message pour le même état."""
    price(db, "ETH", "3000", "-9")
    with pytest.raises(svc.PriceAlertError, match=r"ETH varie déjà de −9\s% sur 24 h, au-delà de 5\s%"):
        svc.create(db, asset="ETH", kind="change", value="5", label=None, actor="micka", now=T0)
    assert svc.evaluate(db, now=T0) == 0
    price(db, "ETH", "3000", "-3")
    alert = svc.create(db, asset="ETH", kind="change", value="5", label=None, actor="micka", now=T0)
    price(db, "ETH", "2800", "-7.25")
    assert svc.evaluate(db, now=T0) == 1
    with pytest.raises(svc.PriceAlertError, match=r"toujours de −7,25\s% sur 24 h, au-delà de 5\s%.*sous 4\s%"):
        svc.rearm(db, alert.id, actor="micka", now=T0)
    assert svc.evaluate(db, now=T0) == 0
    price(db, "ETH", "2900", "-4.5")  # revenue sous le seuil, dans la marge : seule toi peux la réarmer
    assert svc.rearm(db, alert.id, actor="micka", now=T0).state == "armed"
    row = db.get(PriceAlert, alert.id)
    assert row is not None and row.fires == 1


def test_message_names_the_day_when_sent_later() -> None:
    alert = PriceAlert(
        id=5, asset="BTC", kind="above", threshold="61000", fires=1, triggered_at=T0, triggered_price="61500",
        created_at=T0,
    )  # fmt: skip
    same_day = svc.text(alert, privacy="redacted", public_url="https://bank", tz=PARIS, now=T0 + timedelta(hours=6))
    assert f"({svc.eur(Decimal(61500))} à 14 h 05)" in same_day
    later = svc.text(alert, privacy="redacted", public_url="https://bank", tz=PARIS, now=T0 + timedelta(hours=18))
    assert f"({svc.eur(Decimal(61500))} le 21 octobre à 14 h 05)" in later


def test_two_refreshes_at_once_fire_only_once(app, client) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        price(db, "BTC", "60000")
        alert = svc.create(db, asset="BTC", kind="above", value="61000", label=None, actor="micka", now=T0)
        price(db, "BTC", "62000")
        db.commit()
        alert_id = alert.id
    with app.state.sessionmaker() as a, app.state.sessionmaker() as b:
        seen_a, seen_b = a.get(PriceAlert, alert_id), b.get(PriceAlert, alert_id)
        assert seen_a is not None and seen_b is not None and seen_a.state == seen_b.state == "armed"
        assert svc.fire(a, seen_a, seen=0, price=Decimal(62000), change=None, now=T0) is True
        a.commit()
        assert svc.fire(b, seen_b, seen=0, price=Decimal(62000), change=None, now=T0) is False
        b.commit()
    with app.state.sessionmaker() as db:
        assert db.get(PriceAlert, alert_id).fires == 1  # type: ignore[union-attr]
        # Relancer le processus (une nouvelle session, le même état en base) ne redéclenche rien.
        assert svc.evaluate(db, now=T0) == 0
        keys = [k for k, _, _ in svc.alerts(db, now=T0, privacy="full", public_url="https://bank", tz=PARIS)]
        assert len(keys) == 1 and keys[0].startswith(f"price:{alert_id}:") and keys[0].endswith(":1")
        assert svc.alerts(db, now=T0 + timedelta(hours=49), privacy="full", public_url="https://bank", tz=PARIS) == []


def test_two_threads_evaluating_together_fire_once(app, client) -> None:  # type: ignore[no-untyped-def]
    """La vraie course : deux relèves lisent l'alerte armée en même temps ; une seule la déclenche."""
    with app.state.sessionmaker() as db:
        price(db, "BTC", "60000")
        alert = svc.create(db, asset="BTC", kind="above", value="61000", label=None, actor="micka", now=T0)
        price(db, "BTC", "62000")
        db.commit()
        alert_id = alert.id
    barrier = threading.Barrier(2)
    fired: list[int] = []

    def worker() -> None:
        with app.state.sessionmaker() as s:
            barrier.wait()
            fired.append(svc.evaluate(s, now=T0))
            s.commit()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(fired) == [0, 1]
    with app.state.sessionmaker() as db:
        assert db.get(PriceAlert, alert_id).fires == 1  # type: ignore[union-attr]


def test_alerts_are_collected_with_the_others(db: Session) -> None:
    price(db, "BTC", "60000")
    svc.create(db, asset="BTC", kind="above", value="61000", label=None, actor="micka", now=T0)
    price(db, "BTC", "61500")
    svc.evaluate(db, now=T0)
    local = T0.astimezone(PARIS)
    found = channel_alerts.collect(db, today=local.date(), local=local, privacy="redacted", public_url="https://bank")
    texts = [n.text for n in found if n.key.startswith("price:")]
    assert texts and "BTC est passé au-dessus de 61 000 € (61 500 € à 14 h 05)" in texts[0]
    assert not any(n.urgent for n in found if n.key.startswith("price:")), "jamais pendant tes heures calmes"


def test_no_agent_tool_touches_alerts(app, client) -> None:  # type: ignore[no-untyped-def]
    names = [t.name for t in asyncio.run(app.state.mcp.list_tools())]
    writes = [n for n in names if ("alert" in n or "price" in n) and not n.startswith("get_")]
    assert names and writes == []


# ------------------------------------------------------------------ API
def test_api_create_list_rearm_delete(auth_client: TestClient, app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as db:
        price(db, "BTC", "60000", "2.5", at=utcnow())
        db.commit()
    r = auth_client.post(
        "/api/crypto/alerts", json={"asset": "BTC", "kind": "below", "value": "50\u00a0000", "label": "Plancher"}
    )
    assert r.status_code == 201, r.text
    (a,) = r.json()["alerts"]
    assert a["state"] == "armed" and a["threshold"] == "50000" and a["threshold_text"] == "50 000 €"
    assert a["price_eur"] == "60000" and r.json()["assets"] == ["BTC"] and r.json()["max"] == 20
    bad = auth_client.post("/api/crypto/alerts", json={"asset": "BTC", "kind": "below", "value": "70000"})
    assert bad.status_code == 422 and "déjà sous" in bad.json()["detail"]
    assert auth_client.post("/api/crypto/alerts", json={"asset": "BTC", "kind": "buy", "value": "1"}).status_code == 422
    assert auth_client.post(f"/api/crypto/alerts/{a['id']}/rearm").json()["alerts"][0]["state"] == "armed"
    assert auth_client.post("/api/crypto/alerts/999/rearm").status_code == 404
    # L'export RGPD emporte tes alertes : ce sont des données saisies (le repère est en texte libre).
    exported = auth_client.post("/api/export", json={"password": PASSWORD}).json()["crypto"]["price_alerts"]
    assert exported == [
        {"asset": "BTC", "kind": "below", "threshold": "50000", "label": "Plancher", "state": "armed",
         "created_at": a["created_at"]}
    ]  # fmt: skip
    assert auth_client.delete("/api/crypto/alerts/999").status_code == 404
    assert auth_client.delete(f"/api/crypto/alerts/{a['id']}").status_code == 204
    assert auth_client.get("/api/crypto/alerts").json()["alerts"] == []
    auth_client.headers.pop("x-csrf-token")
    assert (
        auth_client.post("/api/crypto/alerts", json={"asset": "BTC", "kind": "above", "value": "70000"}).status_code
        == 403
    )
    auth_client.cookies.clear()
    assert auth_client.get("/api/crypto/alerts").status_code == 401


# ------------------------------------------------------------------ de la relève à la messagerie
class MovingNet(FakeNet):
    btc = 60_000.0

    def get_json(self, url: str) -> Any:
        out = super().get_json(url)
        if "coingecko" in url:
            out["bitcoin"] = {"eur": self.btc, "eur_24h_change": 1.5}
        return out


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    FakeAdapter.instances = []
    application = create_app(settings)
    application.state.channels_options = {
        "adapter_factory": _factory,
        "poll_seconds": 0.05,
        "alert_first_delay": 3600.0,
        "clock": lambda: CLOCK["now"],
        "http": httpx.AsyncClient(transport=httpx.MockTransport(_graph)),
    }
    return application


def test_refresh_to_channel_end_to_end(auth_client: TestClient, app, db) -> None:  # type: ignore[no-untyped-def]
    enable(auth_client)
    adapter = adapter_for()
    _link(db)
    set_setting(db, "channel_quiet_hours", [0, 0])  # le test ne dépend pas de l'heure qu'il est
    consents.set_consent(db, crypto_service.CONSENT, True, actor="micka")
    db.commit()
    net = MovingNet()
    app.state.crypto_net = lambda: net
    assert auth_client.post("/api/crypto/holdings", json={"asset": "BTC", "quantity": "0.5"}).status_code == 201
    gateway: Gateway = app.state.channels
    nudged: list[int] = []
    # Le réveil est noté, pas suivi : c'est le test qui fait passer les alertes, une fois par relève.
    gateway.nudge_threadsafe = lambda: nudged.append(1)  # type: ignore[method-assign]

    def tick(btc: float) -> int:
        net.btc = btc
        _crypto_sync_once(app)
        CLOCK["now"] = utcnow() + timedelta(seconds=1)
        return int(auth_client.portal.call(gateway.run_alerts))  # type: ignore[union-attr]

    assert tick(60_000) == 0  # premier cours connu
    r = auth_client.post("/api/crypto/alerts", json={"asset": "BTC", "kind": "above", "value": "61000"})
    assert r.status_code == 201, r.text
    before = len(adapter.sent)
    assert tick(61_500) == 1 and nudged == [1], "la relève réveille les alertes"
    assert "BTC est passé au-dessus de 61 000 € (61 500 € à " in adapter.sent[-1][1]
    assert tick(62_000) == 0 and tick(61_500) == 0, "une fois par franchissement, quelle que soit la relève"
    assert tick(60_800) == 0, "dans la marge : toujours déclenchée"
    assert auth_client.get("/api/crypto/alerts").json()["alerts"][0]["state"] == "triggered"
    assert tick(60_000) == 0  # réarmée, rien à dire
    assert auth_client.get("/api/crypto/alerts").json()["alerts"][0]["state"] == "armed"
    assert tick(61_200) == 1, "deuxième franchissement"
    assert len(adapter.sent) == before + 2
    keys = list(db.scalars(select(ChannelNotice.key).where(ChannelNotice.key.like("price:%"))))
    assert len(keys) == 2 and len(set(keys)) == 2
    # La relève à la demande passe par le même chemin.
    net.btc = 60_000
    assert auth_client.post("/api/crypto/sync").json()["result"]["alerts"] == 0
    net.btc = 61_500
    assert auth_client.post("/api/crypto/sync").json()["result"]["alerts"] == 1
