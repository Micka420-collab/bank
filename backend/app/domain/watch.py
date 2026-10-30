"""Veille des sources : lecture des flux (RSS 2.0, RSS 1.0, Atom), thèmes, pertinence pour ta situation,
empreintes de contenu (détection des changements), versions du moteur de calcul, fraîcheur des données de
référence que Bank utilise (taux, barèmes, plafonds, sources des démarches). Module pur : aucune base, aucun réseau.

Un flux est une donnée venue d'ailleurs : rien n'y est exécuté ni interprété comme une consigne ; le XML est lu
sans DTD ni entités (defusedxml), les textes sont réduits à du texte brut, les liens à des adresses web.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import defusedxml.ElementTree as SafeET
from defusedxml.common import DefusedXmlException

MAX_ENTRIES = 100
MAX_TITLE = 300
MAX_SUMMARY = 600
MAX_LINK = 500
PARIS = ZoneInfo("Europe/Paris")


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    guid: str
    title: str
    link: str | None
    summary: str
    published: datetime | None  # UTC


# ------------------------------------------------------------------ lecture
_TAG = re.compile(r"<[^>]{0,2000}>")
_SPACES = re.compile(r"\s+")


def clean_text(raw: str | None, limit: int) -> str:
    """Texte brut : balises retirées, entités décodées, espaces normalisés, longueur bornée."""
    if not raw:
        return ""
    text = html.unescape(_TAG.sub(" ", raw))
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = _SPACES.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def safe_link(raw: str | None) -> str | None:
    """Une adresse web (http ou https) et rien d'autre : pas de javascript:, data:, file:…"""
    if not raw:
        return None
    link = raw.strip()
    if len(link) > MAX_LINK or not re.match(r"^https?://[^\s<>\"']+$", link, re.IGNORECASE):
        return None
    return link


def parse_date(raw: str | None) -> datetime | None:
    """RFC 822 (RSS), ISO 8601 (Atom), ou « AAAA-MM-JJ HH:MM:SS » sans fuseau (heure de Paris), en UTC."""
    if not raw:
        return None
    text = raw.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PARIS)
    when = parsed.astimezone(UTC)
    return when if datetime(1990, 1, 1, tzinfo=UTC) <= when <= datetime(2200, 1, 1, tzinfo=UTC) else None


def _local(tag: object) -> str:
    name = tag if isinstance(tag, str) else ""
    return name.rsplit("}", 1)[-1]


def _child(el: Any, name: str) -> Any:
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el: Any, *names: str) -> str | None:
    for name in names:
        child = _child(el, name)
        if child is not None:
            value = "".join(child.itertext())
            if value.strip():
                return str(value)
    return None


def _atom_link(entry: Any) -> str | None:
    links = [c for c in entry if _local(c.tag) == "link"]
    for link in sorted(links, key=lambda c: c.get("rel", "alternate") != "alternate"):
        href = link.get("href")
        if href:
            return str(href)
    return None


def parse_feed(body: bytes) -> list[Entry]:
    try:
        root = SafeET.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise FeedError("Flux illisible (XML invalide ou interdit)") from exc
    kind = _local(root.tag)
    if kind in ("rss", "RDF"):
        nodes = [el for el in root.iter() if _local(el.tag) == "item"]
    elif kind == "feed":
        nodes = [el for el in root if _local(el.tag) == "entry"]
    else:
        raise FeedError("Ce n'est pas un flux RSS ou Atom")
    entries: list[Entry] = []
    seen: set[str] = set()
    for node in nodes[:MAX_ENTRIES]:
        title = clean_text(_text(node, "title"), MAX_TITLE)
        link = safe_link(_atom_link(node) if kind == "feed" else _text(node, "link"))
        guid = (_text(node, "guid", "id") or link or title).strip()[:500]
        if not title or not guid or guid in seen:
            continue
        seen.add(guid)
        entries.append(
            Entry(
                guid=guid,
                title=title,
                link=link,
                summary=clean_text(_text(node, "description", "summary", "content", "encoded"), MAX_SUMMARY),
                published=parse_date(_text(node, "pubDate", "published", "updated", "date")),
            )
        )
    return entries


def content_hash(entry: Entry) -> str:
    return hashlib.sha256(f"{entry.title}\n{entry.summary}\n{entry.link or ''}".encode()).hexdigest()


def external_id(source_id: int, guid: str) -> str:
    return hashlib.sha256(f"{source_id}:{guid}".encode()).hexdigest()


# ------------------------------------------------------------------ thèmes et pertinence
TOPIC_LABELS = {
    "aides": "Aides et prestations",
    "epargne": "Épargne",
    "impots": "Impôts",
    "energie": "Énergie",
    "banque": "Banque et crédit",
    "logement": "Logement",
    "sante": "Santé",
    "emploi": "Emploi et salaire",
    "retraite": "Retraite",
    "conso": "Consommation",
    "moteur": "Moteur de calcul",
    "marches": "Marchés et politique monétaire",
    "geopolitique": "Risques géopolitiques",
    "transparence": "Transparence fiscale CRS",
}
_TOPICS: dict[str, tuple[str, ...]] = {
    "aides": (
        r"\bprime d activite\b",
        r"\brsa\b",
        r"\bapl\b",
        r"\baides? au logement\b",
        r"\ballocations?\b",
        r"\bcaf\b",
        r"\bprestations? sociales?\b",
        r"\bcheques? energie\b",
        r"\bbourses?\b",
        r"\bminima sociaux\b",
        r"\baides? sociales?\b",
        r"\baah\b",
    ),
    "epargne": (
        r"\blivrets?\b",
        r"\blep\b",
        r"\bepargne\b",
        r"\bassurance vie\b",
        r"\bpea\b",
        r"\bplacements?\b",
        r"\btaux d interet\b",
    ),
    "impots": (
        r"\bimpots?\b",
        r"\bfiscal",
        r"\bdeclaration de revenus\b",
        r"\bprelevement a la source\b",
        r"\btaxes?\b",
        r"\bcredits? d impot\b",
    ),
    "energie": (
        r"\benergie\b",
        r"\belectricite\b",
        r"\bgaz\b",
        r"\bcarburants?\b",
        r"\btarifs? reglementes?\b",
        r"\bfioul\b",
    ),
    "banque": (
        r"\bbanques?\b",
        r"\bbancaires?\b",
        r"\bcredits? (?:a la )?conso",
        r"\bprets?\b",
        r"\bsurendettement\b",
        r"\bdecouvert\b",
        r"\bmoyens? de paiement\b",
        r"\bfraudes?\b",
        r"\barnaques?\b",
    ),
    "logement": (r"\blogements?\b", r"\bloyers?\b", r"\bmaprimerenov", r"\brenovation\b", r"\blocataires?\b"),
    "sante": (r"\bsante\b", r"\bmutuelles?\b", r"\bassurance maladie\b", r"\bcss\b", r"\bsoins\b"),
    "emploi": (
        r"\bemplois?\b",
        r"\bchomage\b",
        r"\bfrance travail\b",
        r"\bsalaires?\b",
        r"\bsmic\b",
        r"\bsalaries?\b",
    ),
    "retraite": (r"\bretraites?\b", r"\bpensions?\b"),
    "marches": (
        r"\bmarches? financiers?\b",
        r"\bbourses?\b",
        r"\bindices? boursiers?\b",
        r"\binflation\b",
        r"\bbanque centrale\b",
        r"\bbce\b",
        r"\becb\b",
        r"\bpolitique monetaire\b",
        r"\btaux directeurs?\b",
        r"\bobligations? souveraines?\b",
        r"\bactions? europeennes?\b",
        r"\bstock market\b",
        r"\binterest rates?\b",
        r"\bmonetary policy\b",
        r"\bfomc\b",
        r"\bfederal reserve\b",
        r"\bgoverning council\b",
        r"\bconseil des gouverneurs\b",
    ),
    "geopolitique": (
        r"\bsanctions? economiques?\b",
        r"\bembargo\b",
        r"\bconflits? commerciaux?\b",
        r"\bguerres? commerciales?\b",
        r"\bdroits? de douane\b",
        r"\btariffs?\b",
        r"\btrade war\b",
        r"\bgeopolitic",
    ),
    "transparence": (
        r"\bcrs\b",
        r"\bdac ?2\b",
        r"\bdac ?8\b",
        r"\bfatca\b",
        r"\bechange automatique\b",
        r"\bcommon reporting standard\b",
        r"\bdeclaration de comptes? etrangers?\b",
    ),
    "conso": (
        r"\bconsommat",
        r"\bresili(?:er|ation|ations)\b",
        r"\babonnements?\b",
        r"\bdemarchage\b",
        r"\bgaranties?\b",
        r"\bremboursements?\b",
    ),
}
_COMPILED = {topic: [re.compile(p) for p in patterns] for topic, patterns in _TOPICS.items()}
ALWAYS_INTERESTING = frozenset({"impots", "banque", "conso", "epargne", "sante", "moteur"})


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[’'`´]", " ", ascii_text)


def topics_of(*texts: str) -> list[str]:
    text = normalize(" ".join(texts))
    return [topic for topic, patterns in _COMPILED.items() if any(p.search(text) for p in patterns)]


def relevant(topics: Iterable[str], interests: Iterable[str]) -> bool:
    return bool(set(topics) & set(interests))


# ------------------------------------------------------------------ versions
def version_key(text: str) -> tuple[int, ...]:
    """« 176.1.2 » → (176, 1, 2) ; ce qui n'est pas un numéro de version publié (rc, dev) → ()."""
    value = text.strip()
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", value):
        return ()
    return tuple(int(x) for x in value.split("."))


def latest_version(titles: Iterable[str]) -> str | None:
    best: tuple[tuple[int, ...], str] | None = None
    for title in titles:
        key = version_key(title)
        if key and (best is None or key > best[0]):
            best = (key, title.strip())
    return best[1] if best else None


# ------------------------------------------------------------------ fraîcheur des données de référence
@dataclass(frozen=True)
class Reference:
    key: str
    title: str
    as_of: date | None  # date de la donnée ou de sa dernière vérification
    next_check: date
    status: str  # ok | stale
    detail: str
    source: str | None = None


def next_semester(d: date) -> date:
    """Prochain 1er février ou 1er août strictement après d."""
    for candidate in (date(d.year, 2, 1), date(d.year, 8, 1), date(d.year + 1, 2, 1)):
        if candidate > d:
            return candidate
    return date(d.year + 1, 2, 1)  # pragma: no cover - inaccessible


def _ref(
    key: str, title: str, as_of: date | None, next_check: date, today: date, ok: str, stale: str, src: str | None
) -> Reference:
    late = today >= next_check
    return Reference(key, title, as_of, next_check, "stale" if late else "ok", stale if late else ok, src)


def reference_checks(
    rates: Mapping[str, Any],
    sources: Sequence[tuple[str, str, str | None]],
    today: date,
    *,
    source_max_age_days: int = 180,
) -> list[Reference]:
    """Ce qui doit être revérifié, et quand : Bank ne laisse pas une donnée vieillir en silence.
    `sources` : (clé, titre, date de vérification AAAA-MM-JJ) des sources citées par les démarches et les règles."""
    out: list[Reference] = []
    as_of = date.fromisoformat(str(rates["as_of"]))
    nxt = next_semester(as_of)
    out.append(
        _ref(
            "rates",
            "Taux des livrets réglementés",
            as_of,
            nxt,
            today,
            f"Taux en vigueur depuis le {as_of:%d/%m/%Y} ; Bank te demandera de les revérifier le {nxt:%d/%m/%Y}.",
            f"Les taux appliqués datent du {as_of:%d/%m/%Y} : ils ont pu être révisés le {nxt:%d/%m/%Y}. "
            "Vérifie-les avant de te fier aux comparaisons d'épargne.",
            str(rates.get("source") or "") or None,
        )
    )
    tax = rates.get("income_tax") or {}
    if tax.get("year"):
        year = int(tax["year"])
        out.append(
            _ref(
                "income_tax",
                "Barème de l'impôt sur le revenu",
                date(year, 1, 1),
                date(year + 1, 1, 1),
                today,
                f"Barème {year} (revenus {tax.get('incomes_year', year - 1)}).",
                f"Le barème utilisé est celui de {year} : la loi de finances suivante a pu le changer.",
                str(tax.get("source") or "") or None,
            )
        )
    elec = rates.get("electricity_trv") or {}
    if elec.get("as_of"):
        e_as_of = date.fromisoformat(str(elec["as_of"]))
        e_next = next_semester(e_as_of)
        out.append(
            _ref(
                "electricity",
                "Tarif réglementé de l'électricité",
                e_as_of,
                e_next,
                today,
                f"Grille du {e_as_of:%d/%m/%Y} ; à revérifier le {e_next:%d/%m/%Y}.",
                f"La grille utilisée date du {e_as_of:%d/%m/%Y} : vérifie le tarif sur ta dernière facture.",
                str(elec.get("source") or "") or None,
            )
        )
    for key, title, verified in sources:
        if not verified:
            continue
        on = date.fromisoformat(verified)
        out.append(
            _ref(
                f"source:{key}",
                title,
                on,
                on + timedelta(days=source_max_age_days),
                today,
                f"Vérifiée le {on:%d/%m/%Y}.",
                f"Source vérifiée le {on:%d/%m/%Y} : à relire, la page officielle a pu changer.",
                None,
            )
        )
    return out
