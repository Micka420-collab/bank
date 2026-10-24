"""Plateformes d'échange (Kraken, Binance, Coinbase) par clé d'API : les soldes, rien d'autre.

Garanties
---------
* Chaque appel passe par la porte sûre de Bank (https, hôte public vérifié, taille et durée bornées, aucune
  redirection suivie) ; seuls les trois hôtes officiels sont appelés.
* Les droits de la clé sont vérifiés À LA CONNEXION, avant tout enregistrement : par les droits que la plateforme
  renvoie (Binance ``apiRestrictions``, Coinbase ``key_permissions``) ou, quand elle n'en renvoie pas (Kraken), par
  deux opérations inoffensives qui DOIVENT échouer — une validation d'ordre à volume nul (``validate=true``, jamais
  soumise) et une demande d'information de retrait vers une adresse de retrait inexistante. Une clé qui peut retirer
  ou passer un ordre est refusée ; Bank ne la garde pas.
* Les signatures (HMAC-SHA512, HMAC-SHA256, JWT ES256) se font ici, le secret ne sort jamais de ce module ; aucune
  erreur ne cite la clé ni le secret.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlencode

import jwt

from app.domain import crypto as dc
from app.security.netfetch import FetchError, get_bytes, post_bytes

MAX_BODY = 1_000_000
SECONDS = 20.0
MAX_ASSETS = 60  # au-delà, ce n'est plus un portefeuille perso : on garde les plus gros soldes
FIAT = frozenset({"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY"})
HOSTS = {
    "kraken": "https://api.kraken.com",
    "binance": "https://api.binance.com",
    "coinbase": "https://api.coinbase.com",
}


class ExchangeError(Exception):
    """Message lisible, sans la clé ni le secret."""


@dataclass(frozen=True)
class Rights:
    read: bool
    trade: bool
    withdraw: bool
    source: str  # permissions (renvoyées par la plateforme) | probes (opérations inoffensives refusées)
    ip_restricted: bool | None = None  # None : la plateforme ne le dit pas

    @property
    def read_only(self) -> bool:
        return self.read and not self.trade and not self.withdraw

    def to_dict(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "trade": self.trade,
            "withdraw": self.withdraw,
            "source": self.source,
            "ip_restricted": self.ip_restricted,
        }


class Transport(Protocol):
    """GET/POST signés vers une plateforme : code HTTP et corps, sans exception sur un code d'erreur."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]: ...
    def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class SafeTransport:
    """Le vrai réseau, par la porte sûre."""

    def get(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        try:
            r = get_bytes(url, headers=headers, max_bytes=MAX_BODY, seconds=SECONDS)
        except FetchError as exc:
            raise ExchangeError(str(exc)) from exc
        return r.status, r.body

    def post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        try:
            r = post_bytes(url, body, headers=headers, max_bytes=MAX_BODY, seconds=SECONDS)
        except FetchError as exc:
            raise ExchangeError(str(exc)) from exc
        return r.status, r.body


def default_transport() -> Transport:
    return SafeTransport()


class Exchange(Protocol):
    platform: str

    def rights(self) -> Rights: ...
    def balances(self) -> dict[str, Decimal]: ...


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExchangeError("Réponse illisible de la plateforme") from exc


def _amount(value: Any) -> Decimal:
    try:
        q = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeError("Solde illisible") from exc
    if not q.is_finite() or q < 0 or q > dc.MAX_QUANTITY:
        raise ExchangeError("Solde hors de proportion")
    return q


def _merge(found: dict[str, Decimal], sym: str, qty: Decimal) -> None:
    if qty <= 0 or sym in FIAT:
        return
    try:
        sym = dc.symbol(sym)
    except dc.CryptoError:
        return  # un nom d'actif qui n'en est pas un (jeton promotionnel au nom farfelu) : ignoré
    found[sym] = found.get(sym, Decimal(0)) + qty


def _cap(found: dict[str, Decimal]) -> dict[str, Decimal]:
    if len(found) <= MAX_ASSETS:
        return found
    top = sorted(found.items(), key=lambda kv: -kv[1])[:MAX_ASSETS]
    return dict(top)


# ------------------------------------------------------------------ Kraken
# Noms Kraken → symboles usuels ; les suffixes .F (Flex), .S (staké), .M, .B (bonded), .HOLD sont des sous-comptes
# du même actif.
KRAKEN_ASSETS = {
    "XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXDG": "DOGE", "XDG": "DOGE", "XXRP": "XRP", "XLTC": "LTC",
    "XXLM": "XLM", "XXMR": "XMR", "XZEC": "ZEC", "XETC": "ETC", "XREP": "REP", "XMLN": "MLN", "ZEUR": "EUR",
    "ZUSD": "USD", "ZGBP": "GBP", "ZCAD": "CAD", "ZJPY": "JPY", "ZAUD": "AUD", "CHF": "CHF",
}  # fmt: skip
KRAKEN_PERMISSION_DENIED = "EGeneral:Permission denied"


def kraken_symbol(name: str) -> str:
    base = name.split(".")[0].upper()
    if base in KRAKEN_ASSETS:
        return KRAKEN_ASSETS[base]
    if len(base) == 4 and base[0] in "XZ" and base[1:] in dc.ASSETS:
        return base[1:]
    return base


class Kraken:
    platform = "kraken"

    def __init__(self, api_key: str, api_secret: str, transport: Transport, base: str = HOSTS["kraken"]) -> None:
        self._key, self._transport, self._base = api_key, transport, base
        try:
            self._secret = base64.b64decode(api_secret, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ExchangeError("Secret Kraken illisible : c'est la « Private Key » affichée à la création") from exc
        if len(self._secret) < 32:
            raise ExchangeError("Secret Kraken trop court")

    def _call(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        nonce = str(time.time_ns() // 1_000)
        post = urlencode({"nonce": nonce, **data})
        digest = hashlib.sha256((nonce + post).encode()).digest()
        sig = hmac.new(self._secret, path.encode() + digest, hashlib.sha512).digest()
        headers = {
            "API-Key": self._key,
            "API-Sign": base64.b64encode(sig).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        status, raw = self._transport.post(self._base + path, headers, post.encode())
        if status != 200:
            raise ExchangeError(f"Kraken répond {status}")
        body = _json(raw)
        if not isinstance(body, dict) or not isinstance(body.get("error"), list):
            raise ExchangeError("Réponse inattendue de Kraken")
        return body

    @staticmethod
    def _denied(body: dict[str, Any]) -> bool:
        return any(str(e).startswith(KRAKEN_PERMISSION_DENIED) for e in body["error"])

    def rights(self) -> Rights:
        """Kraken ne renvoie pas les droits d'une clé : on demande ce qu'une clé de lecture ne peut pas faire, de
        façon à ce que rien ne puisse se produire même si la plateforme répondait autrement que prévu."""
        bal = self._call("/0/private/Balance", {})
        if bal["error"]:
            if self._denied(bal):
                raise ExchangeError("Cette clé ne peut pas lire les soldes : donne-lui le droit « Query Funds »")
            raise ExchangeError("Kraken refuse la clé (identifiant ou secret incorrect)")
        # Validation d'ordre SANS soumission (validate=true) et volume nul : même soumise par erreur, Kraken la
        # rejetterait. Une clé sans droit d'ordre répond « Permission denied » ; toute autre réponse prouve le droit.
        order = self._call(
            "/0/private/AddOrder",
            {"validate": "true", "pair": "XBTEUR", "type": "buy", "ordertype": "market", "volume": "0"},
        )
        trade = not self._denied(order)
        # Information de retrait vers une adresse de retrait qui n'existe pas, montant nul : rien ne part.
        probe = {"asset": "XBT", "key": "bank-verification-sans-adresse", "amount": "0"}
        info = self._call("/0/private/WithdrawInfo", probe)
        withdraw = not self._denied(info)
        return Rights(read=True, trade=trade, withdraw=withdraw, source="probes")

    def balances(self) -> dict[str, Decimal]:
        body = self._call("/0/private/Balance", {})
        if body["error"]:
            denied = self._denied(body)
            raise ExchangeError("Kraken refuse la clé (droits ou secret)" if denied else "Kraken répond une erreur")
        result = body.get("result")
        if not isinstance(result, dict):
            raise ExchangeError("Réponse inattendue de Kraken")
        found: dict[str, Decimal] = {}
        for name, value in result.items():
            _merge(found, kraken_symbol(str(name)), _amount(value))
        return _cap(found)


# ------------------------------------------------------------------ Binance
BINANCE_ACTIVE_RIGHTS = (
    "enableWithdrawals", "enableSpotAndMarginTrading", "enableMargin", "enableFutures", "enableInternalTransfer",
    "permitsUniversalTransfer", "enableVanillaOptions", "enableFixApiTrade", "enablePortfolioMarginTrading",
)  # fmt: skip
BINANCE_TRADE_RIGHTS = frozenset({
    "enableSpotAndMarginTrading", "enableMargin", "enableFutures", "enableVanillaOptions", "enableFixApiTrade",
    "enablePortfolioMarginTrading",
})  # fmt: skip
BINANCE_MOVE_RIGHTS = frozenset({"enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer"})


def binance_symbol(name: str) -> str:
    """« LDBTC » (Earn bloqué) compte comme du BTC ; un nom inconnu reste tel quel."""
    s = name.upper()
    if len(s) > 2 and s.startswith("LD") and s[2:] in dc.ASSETS:
        return s[2:]
    return s


class Binance:
    platform = "binance"

    def __init__(self, api_key: str, api_secret: str, transport: Transport, base: str = HOSTS["binance"]) -> None:
        self._key, self._secret, self._transport, self._base = api_key, api_secret.encode(), transport, base

    def _get(self, path: str, params: dict[str, str]) -> Any:
        query = urlencode({**params, "recvWindow": "5000", "timestamp": str(int(time.time() * 1000))})
        sig = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self._base}{path}?{query}&signature={sig}"
        status, raw = self._transport.get(url, {"X-MBX-APIKEY": self._key, "Accept": "application/json"})
        body = _json(raw)
        if status != 200:
            code = body.get("code") if isinstance(body, dict) else None
            if status in (401, 403) or code in (-2014, -2015, -1022):
                raise ExchangeError("Binance refuse la clé (identifiant, secret, adresse IP ou droits)")
            raise ExchangeError(f"Binance répond {status}")
        return body

    def rights(self) -> Rights:
        body = self._get("/sapi/v1/account/apiRestrictions", {})
        if not isinstance(body, dict) or "enableReading" not in body:
            raise ExchangeError("Réponse inattendue de Binance")
        active = {k for k in BINANCE_ACTIVE_RIGHTS if body.get(k) is True}
        return Rights(
            read=bool(body.get("enableReading")),
            trade=bool(active & BINANCE_TRADE_RIGHTS),
            withdraw=bool(active & BINANCE_MOVE_RIGHTS),
            source="permissions",
            ip_restricted=bool(body.get("ipRestrict")) if "ipRestrict" in body else None,
        )

    def balances(self) -> dict[str, Decimal]:
        body = self._get("/api/v3/account", {"omitZeroBalances": "true"})
        rows = body.get("balances") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise ExchangeError("Réponse inattendue de Binance")
        found: dict[str, Decimal] = {}
        for row in rows:
            if not isinstance(row, dict) or "asset" not in row:
                continue
            qty = _amount(row.get("free", "0")) + _amount(row.get("locked", "0"))
            _merge(found, binance_symbol(str(row["asset"])), qty)
        return _cap(found)


# ------------------------------------------------------------------ Coinbase (clés CDP, Advanced Trade)
COINBASE_KEY_PREFIX = "organizations/"
COINBASE_MAX_PAGES = 5


class Coinbase:
    platform = "coinbase"

    def __init__(self, api_key: str, api_secret: str, transport: Transport, base: str = HOSTS["coinbase"]) -> None:
        self._key, self._transport, self._base = api_key.strip(), transport, base
        if not self._key.startswith(COINBASE_KEY_PREFIX) or "/apiKeys/" not in self._key:
            raise ExchangeError(
                "Identifiant Coinbase attendu sous la forme organizations/…/apiKeys/… (clé « CDP » du portail "
                "développeur, droits « View » seulement)"
            )
        pem = api_secret.strip().replace("\\n", "\n")
        if "BEGIN EC PRIVATE KEY" not in pem and "BEGIN PRIVATE KEY" not in pem:
            raise ExchangeError("Secret Coinbase attendu : la clé « EC PRIVATE KEY » (PEM) fournie avec la clé CDP")
        try:
            jwt.encode({"t": 1}, pem, algorithm="ES256")
        except (ValueError, TypeError) as exc:
            raise ExchangeError("Secret Coinbase illisible (PEM EC attendu)") from exc
        self._pem = pem

    def _token(self, method: str, path: str) -> str:
        now = int(time.time())
        host = self._base.removeprefix("https://")
        payload = {"sub": self._key, "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method} {host}{path}"}
        headers = {"kid": self._key, "nonce": secrets.token_hex(16)}
        return jwt.encode(payload, self._pem, algorithm="ES256", headers=headers)

    def _get(self, path: str, query: str = "") -> Any:
        token = self._token("GET", path)
        url = f"{self._base}{path}{('?' + query) if query else ''}"
        status, raw = self._transport.get(url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if status in (401, 403):
            raise ExchangeError("Coinbase refuse la clé (identifiant, secret ou droits)")
        if status != 200:
            raise ExchangeError(f"Coinbase répond {status}")
        return _json(raw)

    def rights(self) -> Rights:
        body = self._get("/api/v3/brokerage/key_permissions")
        if not isinstance(body, dict) or "can_view" not in body:
            raise ExchangeError("Réponse inattendue de Coinbase")
        return Rights(
            read=bool(body.get("can_view")),
            trade=bool(body.get("can_trade")),
            withdraw=bool(body.get("can_transfer")),
            source="permissions",
        )

    def balances(self) -> dict[str, Decimal]:
        found: dict[str, Decimal] = {}
        cursor = ""
        for _ in range(COINBASE_MAX_PAGES):
            query = "limit=250" + (f"&cursor={cursor}" if cursor else "")
            body = self._get("/api/v3/brokerage/accounts", query)
            accounts = body.get("accounts") if isinstance(body, dict) else None
            if not isinstance(accounts, list):
                raise ExchangeError("Réponse inattendue de Coinbase")
            for acc in accounts:
                if not isinstance(acc, dict) or not acc.get("currency"):
                    continue
                avail = acc.get("available_balance") or {}
                hold = acc.get("hold") or {}
                qty = _amount((avail or {}).get("value", "0")) + _amount((hold or {}).get("value", "0"))
                _merge(found, str(acc["currency"]), qty)
            cursor = str(body.get("cursor") or "") if body.get("has_next") else ""
            if not cursor:
                break
        return _cap(found)


# ------------------------------------------------------------------ fabrique
PLATFORMS: dict[str, dict[str, str]] = {
    "kraken": {
        "label": "Kraken",
        "help": "Settings › API › Create API key : coche seulement « Query Funds » (rien dans Orders, Funding ni "
        "Withdraw), sans mot de passe 2FA sur la clé. Bank vérifie en demandant une validation d'ordre à volume nul "
        "et une information de retrait : les deux doivent être refusées.",
        "url": "https://www.kraken.com/u/security/api",
        "key_label": "API Key",
        "secret_label": "Private Key",  # nosec B105 - libellé du champ, pas un secret
    },
    "binance": {
        "label": "Binance",
        "help": "Gestion API › Créer une clé (système) : laisse seulement « Activer la lecture », ajoute ton IP si tu "
        "le peux. Bank lit les restrictions de la clé et refuse tout droit de trading ou de retrait.",
        "url": "https://www.binance.com/fr/my/settings/api-management",
        "key_label": "API Key",
        "secret_label": "Secret Key",  # nosec B105 - libellé du champ
    },
    "coinbase": {
        "label": "Coinbase",
        "help": "Portail développeur (CDP) › API keys › Create : permission « View » seulement, signature ECDSA (pas "
        "Ed25519). Colle le nom de la clé (organizations/…/apiKeys/…) et la clé privée EC (PEM) du fichier "
        "téléchargé. Bank lit les permissions de la clé et refuse « Trade » et « Transfer ».",
        "url": "https://portal.cdp.coinbase.com/access/api",
        "key_label": "Nom de la clé",
        "secret_label": "Clé privée EC (PEM)",  # nosec B105 - libellé du champ
    },
}


def make(platform: str, api_key: str, api_secret: str, transport: Transport, base: str | None = None) -> Exchange:
    if platform == "kraken":
        return Kraken(api_key, api_secret, transport, base or HOSTS["kraken"])
    if platform == "binance":
        return Binance(api_key, api_secret, transport, base or HOSTS["binance"])
    if platform == "coinbase":
        return Coinbase(api_key, api_secret, transport, base or HOSTS["coinbase"])
    raise ExchangeError("Plateforme inconnue")
