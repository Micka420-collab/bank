"""Fausses plateformes fidèles aux vraies API : Kraken (HMAC-SHA512 du chemin + SHA-256(nonce + corps), nonce
croissant, réponses « EGeneral:Permission denied »), Binance (HMAC-SHA256 de la requête, horodatage dans la fenêtre,
``apiRestrictions``), Coinbase (JWT ES256 signé par la clé CDP, ``kid`` et ``uri`` vérifiés, ``key_permissions``,
pagination par curseur). Chaque appel est journalisé ; un ordre réellement soumis ou un retrait serait visible."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

KRAKEN_KEY = "kRaKeN/readonly+KEY/abcdefghijklmnopqrstuvwxyz0123456789ABCD=="
KRAKEN_SECRET = base64.b64encode(b"k" * 64).decode()
KRAKEN_FULL_KEY = "kRaKeN/full+KEY/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH=="
BINANCE_KEY = "binanceReadOnlyKey0123456789abcdefghijklmnopqrstuvwxyzABCDEFGH"
BINANCE_SECRET = "binanceSecret0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN"
BINANCE_TRADE_KEY = "binanceTradeKey0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJK"
COINBASE_KEY = "organizations/11111111-1111-1111-1111-111111111111/apiKeys/22222222-2222-2222-2222-222222222222"
COINBASE_TRANSFER_KEY = (
    "organizations/11111111-1111-1111-1111-111111111111/apiKeys/33333333-3333-3333-3333-333333333333"
)

_EC = ec.generate_private_key(ec.SECP256R1())
COINBASE_SECRET = _EC.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
).decode()
COINBASE_PUBLIC = (
    _EC.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
)


def _ok(payload: Any, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode()


@dataclass
class FakeExchanges:
    """Un transport (get/post) qui joue les trois plateformes. ``balances`` se modifie en cours de test."""

    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (plateforme, chemin, données)
    orders_placed: list[dict[str, Any]] = field(default_factory=list)
    withdrawals: list[dict[str, Any]] = field(default_factory=list)
    kraken_balances: dict[str, str] = field(
        default_factory=lambda: {"XXBT": "0.5000", "ZEUR": "120.50", "ETH.F": "1.25", "XETH": "0.75", "XXDG": "0"}
    )
    binance_balances: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"asset": "BTC", "free": "0.1", "locked": "0.02"},
            {"asset": "LDETH", "free": "2", "locked": "0"},
            {"asset": "EUR", "free": "50", "locked": "0"},
            {"asset": "BNB", "free": "0", "locked": "0"},
        ]
    )
    coinbase_pages: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "accounts": [
                    {"uuid": "a", "currency": "BTC", "available_balance": {"value": "0.3"}, "hold": {"value": "0.1"}},
                    {"uuid": "b", "currency": "EUR", "available_balance": {"value": "10"}, "hold": {"value": "0"}},
                    {"uuid": "c", "currency": "ETH", "available_balance": {"value": "0"}, "hold": {"value": "0"}},
                ],
                "has_next": True,
                "cursor": "p2",
            },
            {
                "accounts": [{"uuid": "d", "currency": "SOL", "available_balance": {"value": "12.5"}, "hold": {}}],
                "has_next": False,
                "cursor": "",
            },
        ]
    )
    kraken_nonces: dict[str, int] = field(default_factory=dict)
    down: set[str] = field(default_factory=set)  # plateformes « en panne » (503)

    # ------------------------------------------------------------ transport
    def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        host = urlsplit(url).hostname or ""
        if host == "api.binance.com":
            return self._binance(url, headers)
        if host == "api.coinbase.com":
            return self._coinbase(url, headers)
        return _ok({"error": "unknown host"}, 404)

    def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        host = urlsplit(url).hostname or ""
        if host == "api.kraken.com":
            return self._kraken(url, headers, body)
        return _ok({"error": "unknown host"}, 404)

    # ------------------------------------------------------------ Kraken
    def _kraken(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        if "kraken" in self.down:
            return 503, b"<html>maintenance</html>"
        path = urlsplit(url).path
        key = headers.get("API-Key", "")
        perms = {KRAKEN_KEY: {"query_funds"}, KRAKEN_FULL_KEY: {"query_funds", "orders", "withdraw"}}.get(key)
        if perms is None:
            return _ok({"error": ["EAPI:Invalid key"]})
        data = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        nonce = int(data.get("nonce", "0"))
        if nonce <= self.kraken_nonces.get(key, 0):
            return _ok({"error": ["EAPI:Invalid nonce"]})
        self.kraken_nonces[key] = nonce
        digest = hashlib.sha256((data["nonce"] + body.decode()).encode()).digest()
        expected = hmac.new(base64.b64decode(KRAKEN_SECRET), path.encode() + digest, hashlib.sha512).digest()
        if not hmac.compare_digest(base64.b64encode(expected).decode(), headers.get("API-Sign", "")):
            return _ok({"error": ["EAPI:Invalid signature"]})
        self.calls.append(("kraken", path, data))
        if path == "/0/private/Balance":
            if "query_funds" not in perms:
                return _ok({"error": ["EGeneral:Permission denied"]})
            return _ok({"error": [], "result": dict(self.kraken_balances)})
        if path == "/0/private/AddOrder":
            if "orders" not in perms:
                return _ok({"error": ["EGeneral:Permission denied"]})
            if data.get("validate") != "true":
                self.orders_placed.append(data)
                return _ok({"error": [], "result": {"txid": ["OQCLML-BW3P3-BUCMWZ"]}})
            if data.get("volume") in ("0", "0.0", None):
                return _ok({"error": ["EGeneral:Invalid arguments:volume"]})
            return _ok({"error": [], "result": {"descr": {"order": "buy XBTEUR @ market"}}})
        if path == "/0/private/WithdrawInfo":
            if "withdraw" not in perms:
                return _ok({"error": ["EGeneral:Permission denied"]})
            return _ok({"error": ["EFunding:Unknown withdraw key"]})
        if path == "/0/private/Withdraw":
            self.withdrawals.append(data)
            return _ok({"error": [], "result": {"refid": "AGBSO6T-UFMTTQ-I7KGS6"}})
        return _ok({"error": ["EGeneral:Unknown method"]})

    # ------------------------------------------------------------ Binance
    def _binance(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        if "binance" in self.down:
            return 503, b"{}"
        parts = urlsplit(url)
        key = headers.get("X-MBX-APIKEY", "")
        flags = {
            BINANCE_KEY: {"enableReading": True, "ipRestrict": True},
            BINANCE_TRADE_KEY: {"enableReading": True, "enableSpotAndMarginTrading": True, "ipRestrict": False},
        }.get(key)
        if flags is None:
            return _ok({"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."}, 401)
        query = parts.query
        assert "&signature=" in query
        unsigned, signature = query.rsplit("&signature=", 1)
        expected = hmac.new(BINANCE_SECRET.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return _ok({"code": -1022, "msg": "Signature for this request is not valid."}, 400)
        params = {k: v[0] for k, v in parse_qs(unsigned).items()}
        if abs(int(params["timestamp"]) - int(time.time() * 1000)) > int(params.get("recvWindow", "5000")):
            return _ok({"code": -1021, "msg": "Timestamp outside of recvWindow."}, 400)
        self.calls.append(("binance", parts.path, params))
        if parts.path == "/sapi/v1/account/apiRestrictions":
            base = {
                "ipRestrict": False, "createTime": 1700000000000, "enableReading": True,
                "enableSpotAndMarginTrading": False, "enableWithdrawals": False, "enableInternalTransfer": False,
                "enableMargin": False, "enableFutures": False, "permitsUniversalTransfer": False,
                "enableVanillaOptions": False, "enableFixApiTrade": False, "enableFixReadOnly": False,
                "enablePortfolioMarginTrading": False,
            }  # fmt: skip
            return _ok({**base, **flags})
        if parts.path == "/api/v3/account":
            return _ok({"balances": list(self.binance_balances), "accountType": "SPOT"})
        return _ok({"code": -1121, "msg": "unknown"}, 400)

    # ------------------------------------------------------------ Coinbase
    def _coinbase(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        if "coinbase" in self.down:
            return 503, b"{}"
        parts = urlsplit(url)
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _ok({"error": "Unauthorized"}, 401)
        token = auth.removeprefix("Bearer ")
        try:
            header = jwt.get_unverified_header(token)
            claims = jwt.decode(token, COINBASE_PUBLIC, algorithms=["ES256"], options={"require": ["exp", "nbf"]})
        except jwt.PyJWTError:
            return _ok({"error": "Unauthorized"}, 401)
        key = header.get("kid", "")
        if key not in (COINBASE_KEY, COINBASE_TRANSFER_KEY) or claims.get("sub") != key or claims.get("iss") != "cdp":
            return _ok({"error": "Unauthorized"}, 401)
        if claims.get("uri") != f"GET api.coinbase.com{parts.path}" or not header.get("nonce"):
            return _ok({"error": "Unauthorized"}, 401)
        self.calls.append(("coinbase", parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}))
        if parts.path == "/api/v3/brokerage/key_permissions":
            transfer = key == COINBASE_TRANSFER_KEY
            return _ok({"can_view": True, "can_trade": False, "can_transfer": transfer, "portfolio_type": "DEFAULT"})
        if parts.path == "/api/v3/brokerage/accounts":
            cursor = {k: v[0] for k, v in parse_qs(parts.query).items()}.get("cursor", "")
            return _ok(self.coinbase_pages[1] if cursor == "p2" else self.coinbase_pages[0])
        return _ok({"error": "not found"}, 404)
