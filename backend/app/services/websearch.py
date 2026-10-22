"""Recherche web de l'assistant : un moteur que tu branches (SearXNG chez toi, ou Brave avec une clé chiffrée),
un accord explicite, un plafond par jour, et un journal qui compte sans jamais garder la requête.

Deux chemins réseau, pour deux confiances différentes :
* Brave est une API publique : elle passe par le téléchargeur durci (https, adresses publiques seulement) ;
* SearXNG est chez toi, sur ton réseau : son adresse est choisie par toi (mot de passe exigé), jamais par le
  modèle ; l'appel est borné en taille et en durée, sans suivre de redirection.
La lecture d'une page de résultat passe toujours par le téléchargeur durci : le modèle ne choisit jamais une
adresse, seulement un numéro de résultat."""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import websearch as dom
from app.models import SearchProvider
from app.security import audit, netfetch
from app.services import consents
from app.services.settings_store import get_setting, set_setting

KINDS = ("searxng", "brave")
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MIN_DAILY_CAP, MAX_DAILY_CAP = 0, 200
MAX_PER_QUESTION = 3
_BRAVE_KEY = re.compile(r"^[A-Za-z0-9_\-]{20,80}$")


class SearchServiceError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def provider(db: Session) -> SearchProvider | None:
    return db.get(SearchProvider, 1)


def valid_searxng_url(raw: str) -> str:
    """http(s)://hôte[:port][/chemin], sans identifiants, requête ni fragment : l'adresse de TON instance."""
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise SearchServiceError("Adresse SearXNG invalide : http(s)://hôte[:port]", 422)
    if parts.query or parts.fragment or len(url) > 300:
        raise SearchServiceError("Adresse SearXNG invalide : sans paramètres", 422)
    return url


def configure(
    db: Session, *, actor: str, kind: str, base_url: str | None, api_key: str | None, daily_cap: int
) -> SearchProvider:
    if kind not in KINDS:
        raise SearchServiceError("Moteur inconnu : searxng ou brave", 422)
    if not MIN_DAILY_CAP <= daily_cap <= MAX_DAILY_CAP:
        raise SearchServiceError("Plafond journalier hors limites (0 à 200)", 422)
    row = provider(db) or SearchProvider(id=1, kind=kind)
    row.kind = kind
    if kind == "searxng":
        row.base_url = valid_searxng_url(base_url or "")
        row.api_key, row.key_prefix = None, None
    else:
        key = (api_key or "").strip()
        if not _BRAVE_KEY.match(key):
            raise SearchServiceError("Clé Brave non reconnue", 422)
        row.base_url = None
        row.api_key, row.key_prefix = key, key[:6] + "…"
    row.daily_cap = daily_cap
    row.last_error = None
    if row.id is None or db.get(SearchProvider, 1) is None:
        db.add(row)
    db.flush()
    audit.record(db, actor, "search.configure", kind=kind, host=urlsplit(row.base_url or BRAVE_URL).hostname)
    return row


def remove(db: Session, *, actor: str) -> None:
    row = provider(db)
    if row is None:
        return
    db.delete(row)
    db.flush()
    audit.record(db, actor, "search.remove")


def _counter(db: Session, today: date) -> int:
    c = get_setting(db, "web_search_counter") or {}
    return int(c.get("count", 0)) if c.get("day") == today.isoformat() else 0


def _bump(db: Session, today: date) -> None:
    set_setting(db, "web_search_counter", {"day": today.isoformat(), "count": _counter(db, today) + 1})


def status(db: Session, today: date) -> dict[str, Any]:
    row = provider(db)
    base = {
        "configured": row is not None,
        "consent": consents.granted(db, "web_search"),
        "asked": consents.decided(db, "web_search"),
        "kinds": {"searxng": "SearXNG (chez toi)", "brave": "Brave Search (clé d'API)"},
    }
    if row is None:
        return base
    return {
        **base,
        "kind": row.kind,
        "base_url": row.base_url,
        "key_prefix": row.key_prefix,
        "daily_cap": row.daily_cap,
        "used_today": _counter(db, today),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_error": row.last_error,
    }


def _searxng(row: SearchProvider, query: str, http: httpx.Client | None) -> list[dom.Result]:
    params = {"q": query, "format": "json", "language": "fr-FR", "safesearch": "1", "categories": "general"}
    url = f"{row.base_url}/search?{urlencode(params)}"
    client = http or httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=False)
    started = time.monotonic()
    try:
        headers = {"Accept": "application/json", "User-Agent": netfetch.USER_AGENT}
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                raise dom.SearchError(f"SearXNG répond {resp.status_code} (le format JSON est-il activé ?)")
            body = netfetch._read(resp, 1_000_000, started, 12.0)  # même lecture bornée que la veille
    except httpx.HTTPError as exc:
        raise dom.SearchError("SearXNG injoignable") from exc
    finally:
        if http is None:
            client.close()
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise dom.SearchError("Réponse SearXNG illisible") from exc
    return dom.parse_searxng(payload)


def _brave(
    row: SearchProvider, query: str, http: httpx.Client | None, resolver: netfetch.Resolver | None
) -> list[dom.Result]:
    params = {"q": query, "count": str(dom.MAX_RESULTS), "country": "FR", "search_lang": "fr", "safesearch": "moderate"}
    try:
        payload = netfetch.get_json(
            f"{BRAVE_URL}?{urlencode(params)}",
            http=http,
            resolver=resolver,
            extra_headers={"X-Subscription-Token": row.api_key or ""},
            max_bytes=1_000_000,
            seconds=12.0,
        )
    except netfetch.FetchError as exc:
        raise dom.SearchError(f"Brave : {exc}") from exc
    return dom.parse_brave(payload)


def search(
    db: Session,
    *,
    actor: str,
    today: date,
    query: str,
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
) -> tuple[str, list[dom.Result]]:
    """Cherche et renvoie (requête nettoyée, résultats). Accord, plafond et nettoyage d'abord ; journal sans la
    requête (seulement le moteur et le nombre de résultats)."""
    row = provider(db)
    if row is None:
        raise dom.SearchError("Aucun moteur de recherche branché (Connecteurs › Recherche web)")
    if not consents.granted(db, "web_search"):
        raise dom.SearchError("Il faut ton accord « Recherche web par l'assistant » (Réglages › Confidentialité)")
    if row.daily_cap <= 0 or _counter(db, today) >= row.daily_cap:
        raise dom.SearchError(f"Plafond de recherches atteint pour aujourd'hui ({row.daily_cap})")
    cleaned = dom.scrub_query(query)
    _bump(db, today)
    try:
        results = _searxng(row, cleaned, http) if row.kind == "searxng" else _brave(row, cleaned, http, resolver)
    except dom.SearchError as exc:
        row.last_error = str(exc)[:300]
        db.flush()
        audit.record(db, actor, "assistant.web_search_failed", engine=row.kind, error=str(exc)[:120])
        raise
    row.last_used_at, row.last_error = utcnow(), None
    db.flush()
    audit.record(db, actor, "assistant.web_search", engine=row.kind, results=len(results), chars=len(cleaned))
    return cleaned, results


def probe(
    db: Session,
    *,
    actor: str,
    today: date,
    http: httpx.Client | None = None,
    resolver: netfetch.Resolver | None = None,
) -> dict[str, Any]:
    """Une recherche d'essai (sujet neutre) : prouve que le moteur répond, sans compter dans le plafond."""
    row = provider(db)
    if row is None:
        raise SearchServiceError("Aucun moteur de recherche branché", 409)
    query = "service-public.fr aides au logement"
    try:
        results = _searxng(row, query, http) if row.kind == "searxng" else _brave(row, query, http, resolver)
    except dom.SearchError as exc:
        row.last_error = str(exc)[:300]
        db.flush()
        audit.record(db, actor, "search.test_failed", engine=row.kind)
        raise SearchServiceError(str(exc), 502) from exc
    row.last_used_at, row.last_error = utcnow(), None
    db.flush()
    audit.record(db, actor, "search.test", engine=row.kind, results=len(results))
    return {"ok": True, "results": len(results), "sample": [r.host for r in results[:3]]}


def read_page(url: str, *, http: httpx.Client | None = None, resolver: netfetch.Resolver | None = None) -> str:
    """Le texte d'une page publique (téléchargeur durci : https, adresses publiques, taille et durée bornées)."""
    try:
        accept = "text/html, text/plain;q=0.9, */*;q=0.1"
        got = netfetch.fetch(url, http=http, resolver=resolver, accept=accept, max_bytes=1_500_000, seconds=15.0)
    except netfetch.FetchError as exc:
        raise dom.SearchError(f"Page illisible : {exc}") from exc
    if got.content_type and not got.content_type.startswith(("text/html", "text/plain", "application/xhtml")):
        raise dom.SearchError("Cette page n'est pas du texte (PDF ou autre) : elle n'est pas lue")
    text = dom.html_to_text(got.body)
    if len(text) < 80:
        raise dom.SearchError("Page vide ou illisible")
    return text
