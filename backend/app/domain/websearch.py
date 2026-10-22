"""Recherche web de l'assistant : ce qui part vers le moteur (une requête nettoyée), ce qui en revient (des
résultats normalisés), et la lecture d'une page publique réduite à son texte.

La requête est rédigée par le modèle à partir de ta question : avant de partir, elle perd tout ce qui pourrait
t'identifier (IBAN, e-mail, téléphone, longs numéros, nom d'une personne), et un secret l'arrête net. Les
résultats ne sont jamais des consignes : ce sont des données montrées au modèle, balisées comme telles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from app.channels.text import secret_kind
from app.domain import redact

MAX_QUERY = 200
MAX_RESULTS = 6
MAX_SNIPPET = 300
MAX_PAGE_CHARS = 8_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")  # ni \n ni \t
_PERSON_WORDS = re.compile(r"\b(?:M\.|Mr|Mme|Mlle|Monsieur|Madame)\s+[A-ZÉÈ][\w'\-]+(?:\s+[A-ZÉÈ][\w'\-]+)?", re.U)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "head", "iframe", "canvas"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "section", "article",
     "header", "footer", "nav", "table", "blockquote", "pre", "dd", "dt", "figcaption", "main", "aside"}
)  # fmt: skip


class SearchError(ValueError):
    """Message lisible pour la personne et pour le modèle, sans détail technique."""


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str
    host: str
    date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "host": self.host, "date": self.date}


def scrub_query(raw: str) -> str:
    """La requête telle qu'elle partira : courte, propre, sans identifiant. Un secret dedans → refus."""
    text = " ".join(_CONTROL.sub("", raw or "").split())
    if not text:
        raise SearchError("Requête vide")
    leaked = secret_kind(text)
    if leaked:
        raise SearchError(f"La requête contient {leaked} : elle ne part pas")
    for pattern in (redact._IBAN, redact._EMAIL, redact._PHONE, redact._LONG_NUMBER):
        text = pattern.sub(" ", text)
    text = _PERSON_WORDS.sub(" ", text)
    text = " ".join(text.split())[:MAX_QUERY].strip()
    if len(text) < 3:
        raise SearchError("Requête trop courte une fois nettoyée")
    return text


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL.sub("", _strip_tags(value)).split())[:limit].strip()


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]{0,200}>", " ", text)


def _accept_url(raw: Any) -> tuple[str, str] | None:
    if not isinstance(raw, str):
        return None
    parts = urlsplit(raw.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return raw.strip()[:500], parts.hostname.lower()


def _result(title: Any, url: Any, snippet: Any, date: Any = None) -> Result | None:
    accepted = _accept_url(url)
    if accepted is None:
        return None
    clean_url, host = accepted
    return Result(
        _clean(title, 120) or host,
        clean_url,
        _clean(snippet, MAX_SNIPPET),
        host,
        _clean(date, 10) if isinstance(date, str) else "",
    )


def parse_searxng(payload: Any) -> list[Result]:
    """Format JSON de SearXNG (``format=json``) : ``results`` avec url, title, content, publishedDate."""
    rows = payload.get("results") if isinstance(payload, dict) else None
    out: list[Result] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        res = _result(r.get("title"), r.get("url"), r.get("content"), r.get("publishedDate"))
        if res and res.url not in seen:
            seen.add(res.url)
            out.append(res)
        if len(out) >= MAX_RESULTS:
            break
    return out


def parse_brave(payload: Any) -> list[Result]:
    """Format de l'API Brave Web Search : ``web.results`` avec title, url, description, age."""
    web = payload.get("web") if isinstance(payload, dict) else None
    rows = web.get("results") if isinstance(web, dict) else None
    out: list[Result] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        res = _result(r.get("title"), r.get("url"), r.get("description"), r.get("page_age") or r.get("age"))
        if res and res.url not in seen:
            seen.add(res.url)
            out.append(res)
        if len(out) >= MAX_RESULTS:
            break
    return out


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        # Un retour à la ligne dans le HTML n'en est pas un : seuls les blocs font des lignes.
        self.parts.append(" ".join(data.split()) + (" " if data[-1:].isspace() else ""))


def html_to_text(body: bytes | str, limit: int = MAX_PAGE_CHARS) -> str:
    """Le texte lisible d'une page : sans scripts, styles ni balises, lignes réduites, borné."""
    raw = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    parser = _TextExtractor()
    try:
        parser.feed(raw[:1_500_000])
        parser.close()
    except Exception:  # noqa: S110 - une page tordue ne doit rien casser : on garde ce qui a été lu
        pass
    text = _CONTROL.sub("", "".join(parser.parts))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit].strip()


def results_for_model(results: list[Result]) -> list[dict[str, Any]]:
    """Ce que voit le modèle : numéro, titre, site, extrait — l'adresse reste côté Bank (il cite [n])."""
    return [
        {"n": i, "title": r.title, "site": r.host, "snippet": r.snippet, **({"date": r.date} if r.date else {})}
        for i, r in enumerate(results, start=1)
    ]
