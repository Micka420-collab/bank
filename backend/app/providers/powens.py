"""Client Powens (ex-Budget Insight), agrégateur agréé : comptes courants, livrets, assurance-vie, PEA, crédits.

Prêt à brancher, sur contrat : l'API de production n'est ouverte qu'aux clients sous contrat ; le bac à sable gratuit
(domaine « …-sandbox ») ne montre que la banque de démonstration. Protocole (docs.powens.com, relu le 24/10/2026) :

1. ``POST /auth/init`` avec client_id et client_secret : un utilisateur Powens et son jeton permanent (``auth_token``).
2. ``GET /auth/token/code`` : un code temporaire à usage unique pour la webview.
3. ``https://webview.powens.com/fr/connect?domain=…&client_id=…&redirect_uri=…&code=…&state=…`` : tu choisis ta
   banque et t'authentifies chez elle, dans la page de Powens ; Bank ne voit jamais tes identifiants bancaires.
4. Retour sur ``redirect_uri?connection_id=…&state=…`` (ou ``error``/``error_description``).
5. ``GET /users/me/connections/{id}/accounts``, ``/users/me/accounts/{id}/transactions`` (paginé : ``_links.next``),
   ``/users/me/accounts/{id}/investments``.

Le réseau passe par netfetch (https, hôte public vérifié puis épinglé, aucune redirection suivie, réponse bornée). Le
secret client ne part que dans le corps de /auth/init et /auth/renew ; il n'apparaît dans aucun message d'erreur.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.domain.money import AmountError, decimal_to_cents
from app.importers.base import ParsedTransaction
from app.security import netfetch

API_SUFFIX = ".biapi.pro"
API_VERSION = "/2.0"
WEBVIEW = "https://webview.powens.com/fr"
PAGE_LIMIT = 1000  # maximum documenté
MAX_PAGES = 200
MAX_BODY = 8_000_000
SECONDS = 30.0
DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# États de connexion qui demandent ton geste (authentification forte, mot de passe changé…) : la synchronisation
# est bloquée jusqu'à une reconnexion dans la webview. Les autres états sont passagers.
REAUTH_STATES = frozenset(
    {
        "SCARequired",
        "webauthRequired",
        "additionalInformationNeeded",
        "decoupled",
        "actionNeeded",
        "passwordExpired",
        "wrongpass",
    }
)
STATE_MESSAGES = {
    "SCARequired": "Ta banque demande une nouvelle authentification forte : reconnecte-la.",
    "webauthRequired": "Ta banque demande de te reconnecter sur sa page : reconnecte-la.",
    "additionalInformationNeeded": "Ta banque attend une information (code reçu par SMS…) : reconnecte-la.",
    "decoupled": "Ta banque attend ta validation dans son application : reconnecte-la.",
    "actionNeeded": "Une action t'attend sur le site de ta banque (conditions à accepter…), puis reconnecte-la.",
    "passwordExpired": "Ton mot de passe bancaire a expiré : change-le chez ta banque, puis reconnecte-la.",
    "wrongpass": "Ta banque refuse les identifiants enregistrés chez Powens : reconnecte-la.",
    "validating": "Powens vérifie encore la connexion : réessaie dans quelques minutes.",
    "rateLimiting": "Ta banque limite les accès pour l'instant : la prochaine synchronisation réessaiera.",
    "websiteUnavailable": "Le site de ta banque est indisponible : la prochaine synchronisation réessaiera.",
    "bug": "Powens a rencontré une erreur sur cette banque : la prochaine synchronisation réessaiera.",
    "notSupported": "Powens ne sait plus lire cette banque.",
}


class PowensError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class PowensAccount:
    id: str
    name: str | None
    iban: str | None
    currency: str
    type: str
    balance_cents: int | None
    deleted: bool


@dataclass(frozen=True)
class PowensConnection:
    id: str
    state: str | None
    bank_name: str | None
    error_message: str | None


def api_base(domain: str) -> str:
    return f"https://{domain}{API_SUFFIX}{API_VERSION}"


class PowensClient:
    def __init__(
        self,
        domain: str,
        client_id: str,
        client_secret: str,
        user_token: str | None = None,
        *,
        http: httpx.Client | None = None,
        resolver: netfetch.Resolver | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not DOMAIN_RE.fullmatch(domain):
            raise ValueError("domaine Powens invalide")
        self.domain = domain
        self.client_id = client_id
        self._secret = client_secret
        self.user_token = user_token
        self._base = api_base(domain)
        self._host = f"{domain}{API_SUFFIX}"
        self._http = http  # transport de netfetch (remplaçable en test)
        self._resolver = resolver
        self._sleep = sleep

    def __repr__(self) -> str:  # jamais le secret ni le jeton dans un journal
        return f"PowensClient(domain={self.domain!r})"

    @property
    def sandbox(self) -> bool:
        return self.domain.endswith("-sandbox")

    # -------------------------------------------------------------- transport
    def _send(self, method: str, url: str, payload: Any, auth: bool) -> tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        if auth:
            if not self.user_token:
                raise PowensError("Aucun utilisateur Powens : enregistre d'abord tes identifiants")
            headers["Authorization"] = f"Bearer {self.user_token}"
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        try:
            r = netfetch.request_bytes(
                method,
                url,
                body,
                headers=headers,
                http=self._http,
                resolver=self._resolver,
                max_bytes=MAX_BODY,
                seconds=SECONDS,
            )
        except netfetch.FetchError as exc:
            raise _NetworkError(str(exc)) from exc
        return r.status, r.body

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: Any = None,
        auth: bool = True,
        url: str | None = None,
    ) -> Any:
        """Requête avec 3 tentatives sur 429/5xx/erreur réseau (back-off exponentiel)."""
        target = url or f"{self._base}{path}" + (f"?{urlencode(params)}" if params else "")
        last: Exception | None = None
        for attempt in range(3):
            try:
                code, content = self._send(method, target, payload, auth)
            except _NetworkError as exc:
                last = exc
                self._sleep(2**attempt)
                continue
            if code == 429 or code >= 500:
                last = PowensError(f"Powens indisponible ({code})", code)
                self._sleep(2.0**attempt)
                continue
            if code >= 400:
                raise _error(code, content)
            if not content:
                return {}
            try:
                return json.loads(content)
            except ValueError as exc:
                raise PowensError("Réponse illisible de Powens") from exc
        raise PowensError(str(last) if last else "échec réseau", getattr(last, "status", None))

    def _next(self, data: Any) -> str | None:
        """Le lien de la page suivante (``_links.next.href``), seulement s'il reste sur l'API de ce domaine : un lien
        opaque venu du réseau ne doit pas emmener le jeton ailleurs."""
        links = data.get("_links") if isinstance(data, dict) else None
        nxt = (links or {}).get("next") if isinstance(links, dict) else None
        href = nxt.get("href") if isinstance(nxt, dict) else None
        if not isinstance(href, str) or not href:
            return None
        parts = urlsplit(href)
        if parts.scheme != "https" or parts.hostname != self._host or parts.port not in (None, 443):
            raise PowensError("Lien de pagination inattendu : synchronisation interrompue")
        if not parts.path.startswith(API_VERSION + "/"):
            raise PowensError("Lien de pagination inattendu : synchronisation interrompue")
        return href

    # -------------------------------------------------------------- utilisateur
    def init_user(self) -> tuple[str, str | None]:
        """Crée l'utilisateur Powens de Bank : jeton permanent (client_id et secret fournis) et identifiant."""
        data = self._request(
            "POST", "/auth/init", payload={"client_id": self.client_id, "client_secret": self._secret}, auth=False
        )
        token = data.get("auth_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise PowensError("Powens n'a pas rendu de jeton")
        if data.get("type") not in (None, "permanent"):
            raise PowensError("Powens a rendu un jeton temporaire : vérifie le client_id et le secret")
        uid = data.get("id_user")
        self.user_token = token
        return token, (str(uid) if uid is not None else None)

    def renew_token(self, user_ref: str) -> str:
        """Nouveau jeton permanent pour l'utilisateur existant (secret changé) ; les anciens sont révoqués."""
        try:
            id_user: int | str = int(user_ref)
        except ValueError:
            id_user = user_ref
        data = self._request(
            "POST",
            "/auth/renew",
            payload={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._secret,
                "id_user": id_user,
                "revoke_previous": True,
            },
            auth=False,
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise PowensError("Powens n'a pas rendu de jeton")
        self.user_token = token
        return token

    def get_user(self) -> dict[str, Any]:
        data = self._request("GET", "/users/me")
        return data if isinstance(data, dict) else {}

    def delete_user(self) -> None:
        """Efface l'utilisateur Powens et toutes ses données chez Powens (RGPD)."""
        self._request("DELETE", "/users/me")

    def temp_code(self) -> str:
        data = self._request("GET", "/auth/token/code", params={"type": "singleAccess"})
        code = data.get("code") if isinstance(data, dict) else None
        if not isinstance(code, str) or not code:
            raise PowensError("Powens n'a pas rendu de code temporaire")
        return code

    def webview_url(self, code: str, redirect_uri: str, state: str, connection_id: str | None = None) -> str:
        """Adresse de la webview : ajout d'une connexion, ou reconnexion (authentification forte) d'une existante."""
        params = {
            "domain": self._host,
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "code": code,
            "state": state,
        }
        if connection_id is None:
            params["connector_capabilities"] = "bank,bankwealth"
            return f"{WEBVIEW}/connect?{urlencode(params)}"
        params["connection_id"] = connection_id
        return f"{WEBVIEW}/reconnect?{urlencode(params)}"

    # -------------------------------------------------------------- données
    def get_connection(self, connection_id: str) -> PowensConnection:
        data = self._request("GET", f"/users/me/connections/{_id(connection_id)}", params={"expand": "connector"})
        return _connection(data if isinstance(data, dict) else {})

    def delete_connection(self, connection_id: str) -> None:
        self._request("DELETE", f"/users/me/connections/{_id(connection_id)}")

    def list_accounts(self, connection_id: str) -> list[PowensAccount]:
        """Les comptes que tu as activés dans la webview (Powens laisse les autres désactivés : c'est ton accord)."""
        data = self._request("GET", f"/users/me/connections/{_id(connection_id)}/accounts")
        raw = data.get("accounts", []) if isinstance(data, dict) else []
        return [a for a in (_account(x) for x in raw if isinstance(x, dict)) if a is not None]

    def investments_valuation(self, account_id: str) -> int | None:
        """Valorisation totale des supports d'un compte d'investissement (assurance-vie, PEA…), en centimes."""
        data = self._request("GET", f"/users/me/accounts/{_id(account_id)}/investments")
        return _cents(data.get("valuation")) if isinstance(data, dict) else None

    def iter_transactions(self, account_id: str, min_date: date, currency: str = "EUR") -> Iterator[ParsedTransaction]:
        params = {"min_date": min_date.isoformat(), "limit": str(PAGE_LIMIT)}
        data = self._request("GET", f"/users/me/accounts/{_id(account_id)}/transactions", params=params)
        for _ in range(MAX_PAGES):
            for raw in data.get("transactions", []) if isinstance(data, dict) else []:
                parsed = map_transaction(raw, currency) if isinstance(raw, dict) else None
                if parsed is not None:
                    yield parsed
            href = self._next(data)
            if href is None:
                return
            data = self._request("GET", "", url=href)
        raise PowensError("Pagination anormalement longue — interrompue")


class _NetworkError(Exception):
    """Panne réseau (rejouée), distincte d'un refus de l'API."""


def _id(value: str) -> str:
    """Les identifiants Powens sont des entiers : rien d'autre n'entre dans un chemin d'URL."""
    if not value.isdigit() or len(value) > 18:
        raise PowensError("Identifiant Powens invalide")
    return value


def _error(status: int, content: bytes) -> PowensError:
    """Seul le code d'erreur documenté (``code``) est repris : ni la description technique, ni le corps."""
    code: str | None = None
    try:
        body = json.loads(content) if content else {}
        if isinstance(body, dict) and isinstance(body.get("code"), str):
            code = re.sub(r"[^A-Za-z0-9_]", "", body["code"])[:40] or None
    except ValueError:
        pass
    suffix = f" : {code}" if code else ""
    return PowensError(f"Powens a refusé la requête ({status}){suffix}", status, code)


def _cents(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return decimal_to_cents(str(value))
    except AmountError:
        return None


def _code(value: Any) -> str:
    """Le type de compte : une chaîne, ou un objet (``{"name": …}`` / ``{"id": …}``) selon la page."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("id") or value.get("code")
    return str(value or "").strip()


def _currency(value: Any, default: str = "EUR") -> str:
    """La devise : un code ISO, ou l'objet ``{"id": "EUR", "symbol": "€", "name": "Euro"}`` (le code est ``id``,
    jamais le nom affiché)."""
    if isinstance(value, dict):
        value = value.get("id") or value.get("code")
    code = str(value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else default


def _account(a: dict[str, Any]) -> PowensAccount | None:
    if a.get("id") is None:
        return None
    iban = a.get("iban")
    return PowensAccount(
        id=str(a["id"]),
        name=(str(a.get("name") or a.get("original_name") or "") or None),
        iban=str(iban).replace(" ", "") if iban else None,
        currency=_currency(a.get("currency")),
        type=_code(a.get("type")) or "unknown",
        balance_cents=_cents(a.get("balance")),
        deleted=bool(a.get("deleted")) or bool(a.get("disabled")),
    )


def _connection(c: dict[str, Any]) -> PowensConnection:
    connector = c.get("connector") if isinstance(c.get("connector"), dict) else {}
    state = c.get("state")
    msg = c.get("error_message")
    return PowensConnection(
        id=str(c.get("id", "")),
        state=str(state) if state else None,
        bank_name=(str(connector.get("name"))[:120] if connector and connector.get("name") else None),
        error_message=str(msg)[:200] if msg else None,
    )


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def map_transaction(raw: dict[str, Any], currency: str = "EUR") -> ParsedTransaction | None:
    """Une opération Powens vers notre modèle : montant signé (``value``) dans la devise du compte, date de
    comptabilisation (``date``), libellé de la banque (``original_wording``). Les opérations à venir (``coming``) sont
    marquées en attente, les opérations effacées par la banque (``deleted``) ignorées."""
    if raw.get("deleted"):
        return None
    cents = _cents(raw.get("value"))
    if cents is None:
        return None
    booking = _parse_date(raw.get("date")) or _parse_date(raw.get("rdate")) or _parse_date(raw.get("application_date"))
    if booking is None:
        return None
    label = str(raw.get("original_wording") or raw.get("wording") or raw.get("simplified_wording") or "").strip()
    ext = raw.get("id")
    return ParsedTransaction(
        booking_date=booking,
        amount_cents=cents,
        label=(label or "(sans libellé)")[:500],
        value_date=_parse_date(raw.get("vdate")) or _parse_date(raw.get("rdate")),
        external_id=str(ext)[:128] if ext is not None else None,
        currency=currency,  # ``value`` est dans la devise du compte (``original_value`` dans celle d'origine)
        status="pending" if raw.get("coming") else "booked",
    )
