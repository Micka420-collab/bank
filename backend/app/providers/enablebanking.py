"""Client Enable Banking (agrégateur DSP2 agréé AISP, ~2 500 banques européennes).

Mode « restreint » gratuit : l'application ne voit que les comptes que tu as liés toi-même dans le
Control Panel — exactement le cas d'usage perso. Auth : JWT RS256 signé avec la clé privée de
l'application (kid = application id), durée de vie max 24 h côté API ; on émet des jetons d'1 h.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import jwt

from app.domain.money import AmountError, decimal_to_cents
from app.importers.base import ParsedTransaction

API_ISS = "enablebanking.com"
API_AUD = "api.enablebanking.com"
JWT_TTL = 3600
MAX_PAGES = 200
# Préférence de solde : disponible intermédiaire > disponible clôture > comptable intermédiaire > clôture…
BALANCE_PREFERENCE = ("ITAV", "CLAV", "ITBD", "CLBD", "XPCD", "OPBD", "PRCD")


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RemoteAccount:
    uid: str
    iban: str | None
    name: str | None
    currency: str


@dataclass(frozen=True)
class AuthStart:
    url: str
    authorization_id: str


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    accounts: list[RemoteAccount]
    valid_until: datetime | None


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key_pem: bytes,
        base_url: str = "https://api.enablebanking.com",
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.app_id = app_id
        self._key = private_key_pem
        self._clock = clock
        self._sleep = sleep
        self._http = http or httpx.Client(base_url=base_url, timeout=httpx.Timeout(20.0, connect=10.0))
        self._token: str | None = None
        self._token_exp = 0.0

    # -------------------------------------------------------------- auth
    def _jwt(self) -> str:
        now = self._clock()
        if self._token is None or now > self._token_exp - 300:
            iat = int(now)
            self._token = jwt.encode(
                {"iss": API_ISS, "aud": API_AUD, "iat": iat, "exp": iat + JWT_TTL},
                self._key,
                algorithm="RS256",
                headers={"kid": self.app_id, "typ": "JWT"},
            )
            self._token_exp = iat + JWT_TTL
        return self._token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Requête avec 3 tentatives sur 429/5xx/erreur réseau (back-off exponentiel, Retry-After respecté)."""
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._http.request(method, path, headers={"Authorization": f"Bearer {self._jwt()}"}, **kwargs)
            except httpx.TransportError as exc:
                last = exc
                self._sleep(2**attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0**attempt
                last = ProviderError(f"Enable Banking indisponible ({resp.status_code})", resp.status_code)
                self._sleep(min(delay, 30.0))
                continue
            if resp.status_code >= 400:
                raise ProviderError(_safe_error(resp), resp.status_code)
            return resp.json() if resp.content else {}
        raise ProviderError(str(last) if last else "échec réseau")

    # -------------------------------------------------------------- endpoints
    def list_aspsps(self, country: str = "FR") -> list[dict[str, Any]]:
        data = self._request("GET", "/aspsps", params={"country": country, "psu_type": "personal"})
        return [
            {
                "name": a.get("name"),
                "country": a.get("country"),
                "logo": a.get("logo"),
                "max_consent_days": a.get("maximum_consent_validity"),
            }
            for a in data.get("aspsps", [])
        ]

    def start_auth(
        self, aspsp_name: str, country: str, redirect_url: str, state: str, valid_days: int = 90
    ) -> AuthStart:
        valid_until = (datetime.fromtimestamp(self._clock(), UTC) + timedelta(days=valid_days)).isoformat()
        data = self._request(
            "POST",
            "/auth",
            json={
                "access": {"valid_until": valid_until},
                "aspsp": {"name": aspsp_name, "country": country},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": "personal",
            },
        )
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderError("URL d'autorisation invalide")
        return AuthStart(url=url, authorization_id=str(data.get("authorization_id", "")))

    def create_session(self, code: str) -> SessionInfo:
        data = self._request("POST", "/sessions", json={"code": code})
        accounts = [_remote_account(a) for a in data.get("accounts", [])]
        valid_until = _parse_dt((data.get("access") or {}).get("valid_until"))
        return SessionInfo(session_id=str(data["session_id"]), accounts=accounts, valid_until=valid_until)

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{session_id}")

    def get_balance(self, account_uid: str) -> tuple[int, str] | None:
        data = self._request("GET", f"/accounts/{account_uid}/balances")
        balances = {b.get("balance_type"): b for b in data.get("balances", [])}
        for btype in (*BALANCE_PREFERENCE, *balances.keys()):
            b = balances.get(btype)
            if b:
                amt = b.get("balance_amount") or {}
                try:
                    return decimal_to_cents(str(amt.get("amount"))), str(amt.get("currency", "EUR"))
                except AmountError:
                    continue
        return None

    def iter_transactions(self, account_uid: str, date_from: date) -> Iterator[ParsedTransaction]:
        params: dict[str, str] = {"date_from": date_from.isoformat()}
        for _ in range(MAX_PAGES):
            data = self._request("GET", f"/accounts/{account_uid}/transactions", params=params)
            for raw in data.get("transactions", []):
                parsed = map_transaction(raw)
                if parsed is not None:
                    yield parsed
            key = data.get("continuation_key")
            if not key:
                return
            params = {"date_from": date_from.isoformat(), "continuation_key": str(key)}
        raise ProviderError("Pagination anormalement longue — interrompue")


def _safe_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        msg = body.get("message") or body.get("error") or ""
    except ValueError:
        msg = ""
    return f"Enable Banking a refusé la requête ({resp.status_code}) {str(msg)[:160]}".strip()


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _remote_account(a: dict[str, Any]) -> RemoteAccount:
    ident = a.get("account_id") or {}
    return RemoteAccount(
        uid=str(a["uid"]),
        iban=ident.get("iban"),
        name=a.get("name") or a.get("product"),
        currency=str(a.get("currency") or "EUR"),
    )


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def map_transaction(raw: dict[str, Any]) -> ParsedTransaction | None:
    """Mappe une transaction DSP2 (format Enable Banking) vers notre modèle. Montant signé selon
    credit_debit_indicator (DBIT = sortie). Libellé = informations de remise, à défaut le nom
    de la contrepartie."""
    amount_obj = raw.get("transaction_amount") or {}
    try:
        cents = abs(decimal_to_cents(str(amount_obj.get("amount"))))
    except AmountError:
        return None
    if str(raw.get("credit_debit_indicator", "")).upper() == "DBIT":
        cents = -cents
    booking = (
        _parse_date(raw.get("booking_date"))
        or _parse_date(raw.get("value_date"))
        or _parse_date(raw.get("transaction_date"))
    )
    if booking is None:
        return None
    remittance = raw.get("remittance_information") or []
    label = " ".join(str(r) for r in remittance if r).strip()
    if not label:
        party = raw.get("creditor") if cents < 0 else raw.get("debtor")
        label = str((party or {}).get("name") or raw.get("note") or "(sans libellé)")
    ext = raw.get("entry_reference") or raw.get("transaction_id")
    status = "pending" if str(raw.get("status", "BOOK")).upper() == "PDNG" else "booked"
    return ParsedTransaction(
        booking_date=booking,
        amount_cents=cents,
        label=label[:500],
        value_date=_parse_date(raw.get("value_date")),
        external_id=str(ext)[:128] if ext else None,
        currency=str(amount_obj.get("currency") or "EUR")[:3],
        status=status,
    )
