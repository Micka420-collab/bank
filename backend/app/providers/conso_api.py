"""Client Conso API (https://conso.boris.sh) : passerelle gratuite et open source vers les données Linky d'Enedis.

Tu donnes ton consentement chez Enedis via conso.boris.sh, qui te remet un jeton personnel. Bank l'utilise pour lire
ta consommation quotidienne ; il ne peut rien faire d'autre. Limites fixées par Enedis : 5 requêtes/s et
10 000/h pour l'ensemble des utilisateurs du service — Bank reste donc sobre (quelques appels par jour).

La réponse est traitée comme n'importe quelle donnée extérieure : taille bornée (même décompressée), délai
total borné, dates ramenées à la fenêtre demandée, valeurs plausibles seulement.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from typing import Any

import httpx

from app.domain.energy import DailyReading, EnergyFormatError, parse_daily

BASE_URL = "https://conso.boris.sh/api"
USER_AGENT = "Bank/0.3 (self-hosted personal finance; contact via GitHub issues of the deployment)"  # ASCII only
MAX_DAYS_PER_CALL = 180  # Enedis plafonne la fenêtre d'une requête ; on reste large en dessous d'un an
MAX_BODY_BYTES = 2_000_000  # 180 jours de relevés pèsent quelques dizaines de Ko ; au-delà, on coupe
MAX_SECONDS_PER_CALL = 60.0  # délai total d'une réponse, octet par octet compris (le timeout httpx est par opération)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}$")


class ConsoApiError(Exception):
    def __init__(self, message: str, status: int | None = None, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.permanent = permanent  # jeton invalide, PRM inconnu : inutile de réessayer tel quel


def looks_like_token(token: str) -> bool:
    return bool(TOKEN_RE.match(token.strip()))


class ConsoApiClient:
    def __init__(self, token: str, http: httpx.Client | None = None, base_url: str = BASE_URL) -> None:
        self._token = token.strip()
        self._http = http or httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        self._base = base_url.rstrip("/")

    def _read_bounded(self, resp: httpx.Response, started: float) -> bytes:
        """Corps lu par morceaux (déjà décompressé par httpx) : taille et durée bornées quoi que dise le serveur."""
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise ConsoApiError("Réponse trop volumineuse", resp.status_code, permanent=True)
            if time.monotonic() - started > MAX_SECONDS_PER_CALL:
                raise ConsoApiError("Conso API trop lente (délai dépassé)", resp.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    def _get(self, kind: str, prm: str, start: date, end: date) -> Any:
        # dateFin exclusive (documentation Conso API) ; alias « start/end/prm » encore acceptés mais on
        # utilise les noms de l'API Mesure synchrone V2 d'Enedis.
        params = {"pointId": prm, "dateDebut": start.isoformat(), "dateFin": end.isoformat()}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # pas de « bombe » gzip ; si le serveur compresse quand même, la
        }  # lecture bornée ci-dessous compte les octets décompressés
        started = time.monotonic()
        try:
            with self._http.stream("GET", f"{self._base}/{kind}", params=params, headers=headers) as resp:
                if resp.status_code == 401:
                    raise ConsoApiError("Jeton refusé ou PRM non autorisé (401)", 401, permanent=True)
                if resp.status_code == 400:
                    raise ConsoApiError("Requête refusée (400) : vérifie le PRM à 14 chiffres", 400, permanent=True)
                if resp.status_code == 429:
                    raise ConsoApiError("Trop de requêtes vers Enedis (429) : réessaie plus tard", 429)
                if resp.status_code >= 500:
                    raise ConsoApiError(f"Enedis / Conso API indisponible ({resp.status_code})", resp.status_code)
                if resp.status_code != 200:
                    raise ConsoApiError(f"Réponse inattendue ({resp.status_code})", resp.status_code)
                body = self._read_bounded(resp, started)
        except httpx.HTTPError as exc:
            raise ConsoApiError("Conso API injoignable (réseau)") from exc
        try:
            return json.loads(body)
        except (ValueError, RecursionError) as exc:  # JSON invalide, ou imbriqué au-delà de ce que Python accepte
            raise ConsoApiError("Réponse non JSON", 200, permanent=True) from exc

    def daily_consumption(self, prm: str, start: date, end_exclusive: date) -> list[DailyReading]:
        """Consommation quotidienne [start, end_exclusive[, par tranches sûres, dans l'ordre, un relevé par jour.

        Un jour hors de la fenêtre demandée (futur, ou antérieur) est ignoré : il ne peut pas être vrai, et une
        date aberrante casserait les calculs en aval.
        """
        by_day: dict[date, int] = {}
        cursor = start
        while cursor < end_exclusive:
            chunk_end = min(end_exclusive, cursor + timedelta(days=MAX_DAYS_PER_CALL))
            payload = self._get("consommation_quotidienne", prm, cursor, chunk_end)
            try:
                rows = parse_daily(payload)
            except EnergyFormatError as exc:
                raise ConsoApiError(
                    f"Format de réponse non reconnu ({exc}) : le format Enedis a peut-être changé, mets Bank à "
                    "jour ou signale-le (integrations/README)",
                    200,
                    permanent=True,
                ) from exc
            for r in rows:
                if start <= r.day < end_exclusive:
                    by_day[r.day] = r.wh  # un jour présent dans deux tranches : la dernière lecture l'emporte
            cursor = chunk_end
        return [DailyReading(d, wh) for d, wh in sorted(by_day.items())]
