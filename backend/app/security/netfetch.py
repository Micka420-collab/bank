"""Téléchargement sûr d'une ressource publique (veille) : jamais vers le réseau du serveur.

* https seulement, sans identifiants dans l'adresse, port 443 ;
* l'hôte est résolu une fois, toutes ses adresses doivent être publiques ; la connexion se fait vers l'adresse
  vérifiée (le nom ne peut pas être re-résolu vers le réseau local entre la vérification et l'appel), le
  certificat restant vérifié pour le nom d'origine (SNI) ;
* chaque redirection (3 au plus) repasse par les mêmes contrôles ;
* taille et durée bornées ; requêtes conditionnelles (ETag, Last-Modified) pour ne rien retélécharger d'inutile.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx

USER_AGENT = "Bank/0.4 (self-hosted personal finance; veille des sources publiques)"
REDIRECTS = frozenset({301, 302, 303, 307, 308})
LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".arpa", ".intranet", ".corp")
Resolver = Callable[[str], list[str]]


class FetchError(Exception):
    """Message lisible, sans détail technique sensible."""


@dataclass(frozen=True)
class FetchResult:
    status: int  # 200, ou 304 si rien n'a changé depuis la dernière fois
    body: bytes
    url: str  # adresse finale (après redirections)
    content_type: str
    etag: str | None
    last_modified: str | None


def resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchError("Adresse introuvable (DNS)") from exc
    return sorted({str(i[4][0]) for i in infos})


def check_url(url: str, resolver: Resolver | None = None) -> tuple[SplitResult, str]:
    """L'adresse découpée et l'adresse IP publique à laquelle se connecter ; refuse tout le reste."""
    url = url.strip()
    if len(url) > 2_000:
        raise FetchError("Adresse trop longue")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise FetchError("L'adresse doit commencer par https://")
    if parts.username or parts.password:
        raise FetchError("Adresse avec identifiants refusée")
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchError("Port invalide") from exc
    if port not in (None, 443):
        raise FetchError("Seul le port https standard (443) est accepté")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES) or ("." not in host and ":" not in host):
        raise FetchError("Adresse locale refusée")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = (resolver or resolve)(host)
    else:
        addresses = [str(literal)]
    if not addresses:
        raise FetchError("Adresse introuvable (DNS)")
    for a in addresses:
        ip = ipaddress.ip_address(a)
        if not ip.is_global or ip.is_multicast:
            raise FetchError("Adresse locale refusée")
    return parts, addresses[0]


def _proxy_configured() -> bool:
    proxies = urllib.request.getproxies()
    return bool(proxies.get("https") or proxies.get("all"))


def fetch(
    url: str,
    *,
    http: httpx.Client | None = None,
    resolver: Resolver | None = None,
    accept: str = "*/*",
    etag: str | None = None,
    last_modified: str | None = None,
    max_bytes: int = 2_000_000,
    seconds: float = 20.0,
    max_redirects: int = 3,
    pin: bool | None = None,
) -> FetchResult:
    """GET d'une ressource publique, borné et vérifié à chaque saut. Derrière un proxy (le proxy résout lui-même
    les noms), la connexion ne peut pas être épinglée sur l'adresse : les contrôles d'adresse restent faits."""
    pin = (not _proxy_configured()) if pin is None else pin
    client = http or httpx.Client(timeout=httpx.Timeout(seconds, connect=10.0), follow_redirects=False)
    started = time.monotonic()
    current = url
    try:
        for _hop in range(max_redirects + 1):
            parts, ip = check_url(current, resolver)
            host = parts.hostname or ""
            headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Encoding": "gzip, identity"}
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            target, extensions = current, {}
            if pin:
                netloc = f"[{ip}]" if ":" in ip else ip
                target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
                headers["Host"] = host
                extensions = {"sni_hostname": host}
            with client.stream("GET", target, headers=headers, extensions=extensions) as resp:
                if resp.status_code in REDIRECTS:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("Redirection sans destination")
                    current = urljoin(current, location)
                    etag = last_modified = None  # la validation conditionnelle valait pour l'adresse d'origine
                    continue
                if resp.status_code == 304:
                    return FetchResult(304, b"", current, "", etag, last_modified)
                if resp.status_code != 200:
                    raise FetchError(f"La source répond {resp.status_code}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("Ressource trop volumineuse")
                body = _read(resp, max_bytes, started, seconds)
                return FetchResult(
                    200,
                    body,
                    current,
                    resp.headers.get("content-type", "").split(";")[0].strip().lower(),
                    resp.headers.get("etag"),
                    resp.headers.get("last-modified"),
                )
        raise FetchError("Trop de redirections")
    except httpx.HTTPError as exc:
        raise FetchError("Source injoignable (réseau)") from exc
    finally:
        if http is None:
            client.close()


def _read(resp: httpx.Response, max_bytes: int, started: float, seconds: float) -> bytes:
    """Corps brut lu par morceaux ; une réponse compressée est décompressée avec une limite de sortie (une petite
    archive ne peut pas se déployer en gigaoctets en mémoire)."""
    encoding = resp.headers.get("content-encoding", "identity").strip().lower() or "identity"
    if encoding not in ("identity", "gzip", "x-gzip", "deflate"):
        raise FetchError("Encodage de la réponse non pris en charge")
    inflater = zlib.decompressobj(32 + zlib.MAX_WBITS) if encoding != "identity" else None
    chunks: list[bytes] = []
    size = received = 0
    for raw in resp.iter_raw():
        if time.monotonic() - started > seconds * 2:
            raise FetchError("Source trop lente à répondre")
        received += len(raw)
        if received > max_bytes:
            raise FetchError("Ressource trop volumineuse")
        data = raw
        if inflater is not None:
            try:
                data = inflater.decompress(raw, max_bytes + 1 - size)
            except zlib.error as exc:
                raise FetchError("Réponse compressée illisible") from exc
            if inflater.unconsumed_tail:
                raise FetchError("Ressource trop volumineuse")
        size += len(data)
        if size > max_bytes:
            raise FetchError("Ressource trop volumineuse")
        chunks.append(data)
    return b"".join(chunks)
