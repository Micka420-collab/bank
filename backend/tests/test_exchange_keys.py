"""Clés d'API de plateforme en lecture seule : les droits sont vérifiés à la connexion face à de fausses plateformes
fidèles (signatures contrôlées, « Permission denied » de Kraken, ``apiRestrictions`` de Binance, JWT ES256 et
``key_permissions`` de Coinbase) ; une clé qui peut retirer ou passer un ordre est refusée et jamais gardée ; les
sondes de Kraken ne soumettent rien ; la clé gardée est chiffrée, jamais renvoyée, exportée ni journalisée ; les
soldes relevés rejoignent le portefeuille et se relèvent avec les adresses ; révocation en un geste."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.db import utcnow
from app.models import CryptoHolding, ExchangeKey
from app.providers import exchanges as ex
from app.services import consents
from app.services import crypto as crypto_service
from app.services import exchange_keys as svc
from tests import fake_exchanges as fx
from tests.conftest import PASSWORD

SEED = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


@pytest.fixture
def platforms() -> fx.FakeExchanges:
    return fx.FakeExchanges()


# ------------------------------------------------------------------ fournisseurs
def test_rights_are_read_from_the_platform_or_probed_without_ever_acting(platforms: fx.FakeExchanges) -> None:
    kraken = ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, platforms)
    r = kraken.rights()
    assert r.read_only and r.source == "probes" and r.to_dict()["withdraw"] is False
    full = ex.make("kraken", fx.KRAKEN_FULL_KEY, fx.KRAKEN_SECRET, platforms).rights()
    assert full.trade and full.withdraw and not full.read_only
    # Les sondes : validation seulement, volume nul, adresse de retrait inexistante — rien n'est jamais soumis.
    orders = [d for p, path, d in platforms.calls if p == "kraken" and path == "/0/private/AddOrder"]
    assert orders and all(d["validate"] == "true" and d["volume"] == "0" for d in orders)
    assert platforms.orders_placed == [] and platforms.withdrawals == []
    assert not any(path == "/0/private/Withdraw" for _, path, _ in platforms.calls)
    binance = ex.make("binance", fx.BINANCE_KEY, fx.BINANCE_SECRET, platforms).rights()
    assert binance.read_only and binance.source == "permissions" and binance.ip_restricted is True
    trader = ex.make("binance", fx.BINANCE_TRADE_KEY, fx.BINANCE_SECRET, platforms).rights()
    assert trader.trade and not trader.withdraw and trader.ip_restricted is False
    coinbase = ex.make("coinbase", fx.COINBASE_KEY, fx.COINBASE_SECRET, platforms).rights()
    assert coinbase.read_only and coinbase.source == "permissions"
    mover = ex.make("coinbase", fx.COINBASE_TRANSFER_KEY, fx.COINBASE_SECRET, platforms).rights()
    assert mover.withdraw and not mover.trade
    # Un secret faux est refusé par la plateforme (signature), sans que l'erreur cite quoi que ce soit.
    with pytest.raises(ex.ExchangeError) as err:
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET.replace("a", "b"), platforms).rights()
    assert "incorrect" in str(err.value) and fx.KRAKEN_KEY not in str(err.value)
    with pytest.raises(ex.ExchangeError):
        ex.make("binance", fx.BINANCE_KEY, "mauvais-secret", platforms).rights()
    with pytest.raises(ex.ExchangeError):
        ex.make("coinbase", "pas-une-cle-cdp", fx.COINBASE_SECRET, platforms)
    with pytest.raises(ex.ExchangeError):
        ex.make("coinbase", fx.COINBASE_KEY, "pas un PEM", platforms)
    with pytest.raises(ex.ExchangeError):
        ex.make("kraken", fx.KRAKEN_KEY, "pas du base64 !!", platforms)


def test_balances_are_normalised_fiat_excluded_and_pages_followed(platforms: fx.FakeExchanges) -> None:
    kraken = ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, platforms).balances()
    assert kraken == {"BTC": Decimal("0.5000"), "ETH": Decimal("2.00")}, "XXBT → BTC, ETH.F + XETH → ETH, ZEUR exclu"
    binance = ex.make("binance", fx.BINANCE_KEY, fx.BINANCE_SECRET, platforms).balances()
    assert binance == {"BTC": Decimal("0.12"), "ETH": Decimal("2")}, "free + locked, LDETH → ETH, EUR et 0 exclus"
    coinbase = ex.make("coinbase", fx.COINBASE_KEY, fx.COINBASE_SECRET, platforms).balances()
    assert coinbase == {"BTC": Decimal("0.4"), "SOL": Decimal("12.5")}
    assert [path for p, path, _ in platforms.calls if p == "coinbase"].count("/api/v3/brokerage/accounts") == 2
    assert ex.kraken_symbol("XXBT") == "BTC" and ex.kraken_symbol("SOL.S") == "SOL" and ex.kraken_symbol("XTZ") == "XTZ"
    assert ex.binance_symbol("LDBTC") == "BTC" and ex.binance_symbol("LDO") == "LDO"
    platforms.down.add("kraken")
    with pytest.raises(ex.ExchangeError) as err:
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, platforms).balances()
    assert "503" in str(err.value)


# ------------------------------------------------------------------ service et API
def _grant(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.sessionmaker() as s:
        consents.set_consent(s, crypto_service.CONSENT, True, actor="micka")
        s.commit()


def _connect(c: TestClient, **over: object) -> object:
    body = {"password": PASSWORD, "platform": "kraken", "label": "Kraken perso", "api_key": fx.KRAKEN_KEY,
            "api_secret": fx.KRAKEN_SECRET, "foreign": True}  # fmt: skip
    return c.post("/api/crypto/exchanges", json={**body, **over})


def test_connecting_checks_consent_password_and_rights_then_keeps_an_encrypted_key(
    auth_client: TestClient, app, platforms: fx.FakeExchanges
) -> None:  # type: ignore[no-untyped-def]
    app.state.exchange_transport = lambda: platforms
    c = auth_client
    assert c.get("/api/crypto/exchanges").json()["keys"] == []
    assert _connect(c).status_code == 409, "sans accord « crypto_network », rien ne part"
    _grant(app)
    assert _connect(c, password="faux").status_code == 403
    assert platforms.calls == [], "mot de passe faux : la plateforme n'a pas été appelée"
    # Une clé qui peut retirer et passer des ordres : refusée, rien de gardé, journal sans la clé.
    r = _connect(c, api_key=fx.KRAKEN_FULL_KEY)
    assert r.status_code == 422 and "retirer des fonds et passer des ordres" in r.json()["detail"]
    assert c.get("/api/crypto/exchanges").json()["keys"] == [] and platforms.orders_placed == []
    r = _connect(c, platform="binance", api_key=fx.BINANCE_TRADE_KEY, api_secret=fx.BINANCE_SECRET)
    assert r.status_code == 422 and "passer des ordres" in r.json()["detail"]
    r = _connect(c, platform="coinbase", api_key=fx.COINBASE_TRANSFER_KEY, api_secret=fx.COINBASE_SECRET)
    assert r.status_code == 422 and "retirer des fonds" in r.json()["detail"]
    # Une phrase de récupération ou une clé privée collée là : refusée avant tout appel.
    before = len(platforms.calls)
    r = _connect(c, api_key=SEED)
    assert r.status_code == 422 and "clé de portefeuille" in r.json()["detail"] and len(platforms.calls) == before
    # La bonne clé : droits vérifiés, soldes relevés, lignes « plateforme » dans le portefeuille.
    r = _connect(c)
    assert r.status_code == 201, r.text
    key = r.json()["keys"][0]
    assert key["platform_label"] == "Kraken" and key["label"] == "Kraken perso" and key["foreign"] is True
    assert key["rights"] == {"read": True, "trade": False, "withdraw": False, "source": "probes", "ip_restricted": None}
    assert key["key_hint"] == "…CD==" and key["error"] is None and key["synced_at"]
    assert {a["asset"]: a["quantity"] for a in key["assets"]} == {"BTC": "0.5", "ETH": "2"}
    assert fx.KRAKEN_SECRET not in r.text and fx.KRAKEN_KEY not in r.text
    view = r.json()["view"]
    btc = next(a for a in view["assets"] if a["asset"] == "BTC")
    assert any(s["where"] == "Kraken perso" and s["watched"] for s in btc["sources"])
    assert view["manual"] == [], "une ligne relevée par une clé n'est pas une saisie manuelle"
    assert "Kraken perso" in view["tax"]["foreign_accounts"], "compte hors de France : 3916-bis"
    with app.state.sessionmaker() as s:
        row = s.get(ExchangeKey, key["id"])
        assert row is not None and row.api_key == fx.KRAKEN_KEY and row.api_secret == fx.KRAKEN_SECRET
        raw = " ".join(str(v) for v in s.execute(text("SELECT api_key, api_secret FROM crypto_exchange_keys")).first())
        assert fx.KRAKEN_KEY not in raw and fx.KRAKEN_SECRET not in raw, "chiffrés en base"
        held = s.scalars(select_holdings(key["id"])).all()
        assert all(h.exchange_key_id == key["id"] and h.platform == "Kraken perso" for h in held)
        hid = held[0].id
    # Jamais modifiables à la main, jamais dans l'export ni le journal.
    assert c.put(f"/api/crypto/holdings/{hid}", json={"asset": "BTC", "quantity": "9"}).status_code == 422
    assert c.delete(f"/api/crypto/holdings/{hid}").status_code == 404
    export = c.post("/api/export", json={"password": PASSWORD})
    assert fx.KRAKEN_KEY not in export.text and fx.KRAKEN_SECRET not in export.text
    assert export.json()["crypto"]["exchanges"] == [
        {"platform": "kraken", "label": "Kraken perso", "foreign": True, "verified_at": key["verified_at"]}
    ]
    audit = c.get("/api/audit").text
    assert fx.KRAKEN_KEY not in audit and fx.KRAKEN_SECRET not in audit
    actions = [e["action"] for e in c.get("/api/audit").json()["entries"]]
    assert (
        "crypto.exchange.add" in actions
        and "crypto.exchange.refused" in actions
        and "crypto.exchange.denied" in actions
    )
    # Chaque tentative passe par le limiteur des mots de passe (10 par 5 min) : on le remet à zéro pour la suite.
    from app.security.auth import SlidingWindowLimiter

    app.state.limiters["login"] = SlidingWindowLimiter(10, 300)
    # Dix clés au plus, et la même plateforme peut être reliée deux fois (deux comptes).
    assert (
        _connect(
            c, platform="binance", api_key=fx.BINANCE_KEY, api_secret=fx.BINANCE_SECRET, label="Binance", foreign=False
        ).status_code
        == 201
    )
    keys = c.get("/api/crypto/exchanges").json()
    assert [k["platform"] for k in keys["keys"]] == ["kraken", "binance"] and keys["max_keys"] == 10
    assert {p["key"] for p in keys["platforms"]} == {"kraken", "binance", "coinbase"}
    assert all(p["url"].startswith("https://") and p["help"] for p in keys["platforms"])


def select_holdings(key_id: int):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return select(CryptoHolding).where(CryptoHolding.exchange_key_id == key_id)


def test_sync_updates_balances_reports_failures_and_revocation_removes_everything(
    auth_client: TestClient, app, platforms: fx.FakeExchanges
) -> None:  # type: ignore[no-untyped-def]
    app.state.exchange_transport = lambda: platforms
    app.state.crypto_net = lambda: _NoNet()
    _grant(app)
    c = auth_client
    kid = _connect(c).json()["created_id"]
    cid = _connect(
        c, platform="coinbase", api_key=fx.COINBASE_KEY, api_secret=fx.COINBASE_SECRET, label="Coinbase"
    ).json()["created_id"]
    platforms.kraken_balances = {"XXBT": "0.25", "ETH.F": "0", "XETH": "0"}  # vendu depuis
    platforms.down.add("coinbase")
    r = c.post(f"/api/crypto/exchanges/{kid}/sync")
    assert r.status_code == 200
    kraken = next(k for k in r.json()["keys"] if k["id"] == kid)
    assert {a["asset"]: a["quantity"] for a in kraken["assets"]} == {"BTC": "0.25"}, "l'ETH vendu tombe à 0"
    # « Relever » le portefeuille relève aussi les plateformes ; une plateforme en panne le dit sous sa clé.
    r = c.post("/api/crypto/sync")
    assert (
        r.status_code == 200
        and r.json()["result"]["exchanges_synced"] == 1
        and r.json()["result"]["exchanges_failed"] == 1
    )
    coinbase = next(k for k in c.get("/api/crypto/exchanges").json()["keys"] if k["id"] == cid)
    assert coinbase["error"] == "Coinbase répond 503" and coinbase["synced_at"] is not None
    assert {a["asset"] for a in coinbase["assets"]} == {"BTC", "SOL"}, "en panne : les derniers soldes restent"
    # Révocation : la clé et ses lignes disparaissent, le portefeuille se recalcule.
    assert c.delete(f"/api/crypto/exchanges/{cid}").status_code == 204
    assert c.delete(f"/api/crypto/exchanges/{cid}").status_code == 404
    with app.state.sessionmaker() as s:
        assert s.get(ExchangeKey, cid) is None
        assert s.scalars(select_holdings(cid)).all() == []
    view = c.get("/api/crypto").json()
    assert all(s["where"] != "Coinbase" for a in view["assets"] for s in a["sources"])
    # Relève planifiée (housekeeping) : même chemin, sans accord rien ne part.
    with app.state.sessionmaker() as s:
        consents.set_consent(s, crypto_service.CONSENT, False, actor="micka")
        s.commit()
    with app.state.sessionmaker() as s:
        assert svc.sync_all(s, transport=platforms, now=utcnow(), actor="system") == {
            "exchanges_synced": 0,
            "exchanges_failed": 0,
        }
    assert c.post(f"/api/crypto/exchanges/{kid}/sync").status_code == 409


class _NoNet:
    """Le réseau des nœuds publics et de CoinGecko, hors sujet ici : tout échoue proprement."""

    def get_json(self, url: str) -> object:
        raise crypto_service.FetchError("hors ligne")

    def post_json(self, url: str, payload: object) -> object:
        raise crypto_service.FetchError("hors ligne")


def test_service_guards(db: Session, platforms: fx.FakeExchanges) -> None:
    consents.set_consent(db, crypto_service.CONSENT, True, actor="micka")
    with pytest.raises(svc.ExchangeKeyError) as err:
        svc.connect(db, actor="micka", platform="kraken", label="Ledger " + SEED, api_key=fx.KRAKEN_KEY, api_secret=fx.KRAKEN_SECRET, foreign=True, transport=platforms, now=utcnow())  # fmt: skip
    assert "secret" in str(err.value)
    with pytest.raises(svc.ExchangeKeyError):
        svc.connect(db, actor="micka", platform="nope", label="", api_key="k", api_secret="s", foreign=True, transport=platforms, now=utcnow())  # fmt: skip
    with pytest.raises(svc.ExchangeKeyError) as err:
        svc.connect(db, actor="micka", platform="kraken", label="", api_key=fx.KRAKEN_KEY, api_secret="x" * 5_000, foreign=True, transport=platforms, now=utcnow())  # fmt: skip
    assert "trop longs" in str(err.value)
    key = svc.connect(db, actor="micka", platform="kraken", label="", api_key=fx.KRAKEN_KEY, api_secret=fx.KRAKEN_SECRET, foreign=True, transport=platforms, now=utcnow())  # fmt: skip
    assert key.label == "Kraken", "sans nom : celui de la plateforme"
    # Une saisie manuelle sur la même plateforme ne touche pas la ligne relevée.
    manual = crypto_service.set_holding(db, actor="micka", asset="BTC", quantity="1", platform="Kraken")
    assert manual.exchange_key_id is None and manual.id != next(iter(db.scalars(select_holdings(key.id)))).id
    with pytest.raises(svc.ExchangeKeyError):
        svc.revoke(db, 999, actor="micka", now=utcnow())


def test_safe_transport_and_odd_answers_turn_into_readable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import netfetch

    def refused(*a: object, **k: object) -> object:
        raise netfetch.FetchError("Adresse introuvable (DNS)")

    monkeypatch.setattr(ex, "get_bytes", refused)
    monkeypatch.setattr(ex, "post_bytes", refused)
    safe = ex.SafeTransport()
    with pytest.raises(ex.ExchangeError, match="DNS"):
        safe.get("https://api.binance.com/x", {})
    with pytest.raises(ex.ExchangeError, match="DNS"):
        safe.post("https://api.kraken.com/x", {}, b"")

    class Odd:
        """Une plateforme qui répond n'importe quoi : jamais une trace, toujours une phrase."""

        def __init__(self) -> None:
            self.answers: list[tuple[int, bytes]] = []

        def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
            return self.answers.pop(0)

        def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
            return self.answers.pop(0)

    odd = Odd()
    odd.answers = [(500, b"boom")]
    with pytest.raises(ex.ExchangeError, match="répond 500"):
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, odd).balances()
    odd.answers = [(200, b"not json")]
    with pytest.raises(ex.ExchangeError, match="illisible"):
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, odd).balances()
    odd.answers = [(200, b'{"error": [], "result": "?"}')]
    with pytest.raises(ex.ExchangeError, match="inattendue"):
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, odd).balances()
    odd.answers = [(200, b'{"error": [], "result": {"XXBT": "-1"}}')]
    with pytest.raises(ex.ExchangeError, match="hors de proportion"):
        ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, odd).balances()
    odd.answers = [(200, b'{"nope": 1}')]
    with pytest.raises(ex.ExchangeError, match="inattendue"):
        ex.make("binance", fx.BINANCE_KEY, fx.BINANCE_SECRET, odd).rights()
    odd.answers = [(418, b'{"code": -1000}')]
    with pytest.raises(ex.ExchangeError, match="répond 418"):
        ex.make("binance", fx.BINANCE_KEY, fx.BINANCE_SECRET, odd).balances()
    odd.answers = [(200, b'{"accounts": "?"}')]
    with pytest.raises(ex.ExchangeError, match="inattendue"):
        ex.make("coinbase", fx.COINBASE_KEY, fx.COINBASE_SECRET, odd).balances()
    odd.answers = [(503, b"{}")]
    with pytest.raises(ex.ExchangeError, match="répond 503"):
        ex.make("coinbase", fx.COINBASE_KEY, fx.COINBASE_SECRET, odd).rights()
    # Trop d'actifs : les plus gros soldes sont gardés, dans la limite.
    many = {f"T{i:03d}": str(i + 1) for i in range(80)}
    odd.answers = [(200, json_bytes({"error": [], "result": many}))]
    kept = ex.make("kraken", fx.KRAKEN_KEY, fx.KRAKEN_SECRET, odd).balances()
    assert len(kept) == ex.MAX_ASSETS and max(kept.values()) == Decimal("80")
    with pytest.raises(ex.ExchangeError, match="Plateforme inconnue"):
        ex.make("bitpanda", "k", "s", odd)


def json_bytes(payload: object) -> bytes:
    import json

    return json.dumps(payload).encode()
