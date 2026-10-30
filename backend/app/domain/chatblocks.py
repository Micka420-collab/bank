"""Blocs interactifs d'une réponse de l'assistant : boutons, graphique, schéma, sources.

Le modèle ne fabrique jamais un bloc lui-même : il demande (outils) et Bank construit. Un bouton vient d'un
catalogue d'actions calculé par Bank (un écran qui existe, une carte « Bank te demande », une question suivante),
les chiffres d'un graphique viennent des données de Bank, les sources viennent d'une recherche faite par Bank. Le
seul contenu rédigé par le modèle est le texte d'un schéma — borné, sans lien, et présenté comme tel.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

MAX_BLOCKS = 8  # graphique, schéma, interface, simulateur ou tableau, coach d'achat, sources, boutons : jamais tronqué
MAX_ACTIONS = 7  # jusqu'à 4 boutons et 3 questions suivantes
MAX_CATALOGUE = 60  # le modèle lit le catalogue à chaque question : il reste court
MAX_LABEL = 60
MAX_TITLE = 80
MAX_PROMPT = 200
MAX_DIAGRAM_NODES = 12
MAX_DIAGRAM_EDGES = 20
MAX_NODE_LABEL = 48
MAX_NODE_NOTE = 90
MAX_CHART_ITEMS = 24
MAX_SOURCES = 6
MAX_SNIPPET = 300
MAX_INTERACTIVE_FIELDS = 6
MAX_INTERACTIVE_OPTIONS = 8
MAX_SCENARIO_DAYS = 35
MAX_SCENARIO_BALANCE_CENTS = 10**12
INTERACTIVE_KINDS = frozenset({"poll", "checklist", "form"})
FIELD_KINDS = frozenset({"single", "multiple", "text", "number", "boolean", "scale"})

PAGES = frozenset(
    {
        "aujourdhui", "operations", "opportunites", "documents", "demarches", "aides", "patrimoine", "crypto",
        "pilote", "situation", "veille", "messageries", "connecteurs", "reglages", "memoire", "plus",
    }
)  # fmt: skip
_QUERY = re.compile(r"^[a-z_]+=[a-z0-9_]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_LINK = re.compile(r"\S*(?:https?://|www\.)\S*", re.I)  # le lien entier, pas seulement son début
CHART_KINDS = frozenset({"donut", "bars", "pairs", "line"})
CHART_TONES = frozenset({"c1", "c2", "c3", "c4", "c5", "c6", "muted"})  # jetons de styles.css (categorize.TONES)
DIAGRAM_KINDS = frozenset({"flow", "steps"})
_ID = re.compile(r"^[a-z0-9_\-]{1,24}$")


class BlockError(ValueError):
    pass


def clean(text: Any, limit: int, *, links: bool = False) -> str:
    """Texte court et propre : sans caractère de contrôle ni d'écriture bidirectionnelle, espaces réduits, borné.
    Sans lien, sauf là où c'est le sujet (une source)."""
    if not isinstance(text, str):
        return ""
    out = " ".join(_CONTROL.sub("", text).split())
    if not links and _LINK.search(out):
        out = " ".join(_LINK.sub("", out).split())
    return out[:limit].strip()


# ------------------------------------------------------------------ actions
@dataclass(frozen=True)
class ChatAction:
    """Une action proposable : navigation vers un écran, question suivante, ou geste d'une carte « Bank te
    demande » (exécuté par l'API des initiatives, avec ses propres contrôles)."""

    key: str
    label: str
    kind: str  # navigate | ask | act
    page: str = ""
    prompt: str = ""
    card: str = ""
    action: str = ""
    hint: str = ""  # pour le modèle : quand proposer ce bouton

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind}
        if self.kind == "navigate":
            out["page"] = self.page
        elif self.kind == "ask":
            out["prompt"] = self.prompt
        else:
            out["card"], out["action"] = self.card, self.action
        return out


def valid_page(page: str) -> bool:
    base, _, query = page.partition("?")
    if base not in PAGES:
        return False
    return all(_QUERY.match(part) for part in query.split("&") if part) if query else True


def navigate(key: str, label: str, page: str, hint: str = "") -> ChatAction:
    if not valid_page(page):
        raise BlockError(f"Écran inconnu : {page}")
    return ChatAction(key, clean(label, MAX_LABEL), "navigate", page=page, hint=hint)


def ask(key: str, label: str, prompt: str, hint: str = "") -> ChatAction:
    return ChatAction(key, clean(label, MAX_LABEL), "ask", prompt=clean(prompt, MAX_PROMPT), hint=hint)


def act(key: str, label: str, card: str, action: str, hint: str = "") -> ChatAction:
    return ChatAction(key, clean(label, MAX_LABEL), "act", card=card[:120], action=action[:40], hint=hint)


@dataclass
class Catalogue:
    """Les actions proposables pour une réponse, indexées par clé. Le modèle ne voit que clés, libellés et
    indications ; il ne peut proposer que ce qui est là."""

    actions: dict[str, ChatAction] = field(default_factory=dict)

    def add(self, action: ChatAction) -> None:
        if len(self.actions) < MAX_CATALOGUE:
            self.actions.setdefault(action.key, action)

    def resolve(self, keys: Iterable[Any]) -> list[ChatAction]:
        out: list[ChatAction] = []
        for raw in keys:
            if not isinstance(raw, str):
                continue
            a = self.actions.get(raw.strip())
            if a is not None and a not in out:
                out.append(a)
            if len(out) >= MAX_ACTIONS:
                break
        return out

    def prompt_lines(self) -> list[str]:
        return [f"- {a.key} : « {a.label} »" + (f" — {a.hint}" if a.hint else "") for a in self.actions.values()]


def actions_block(actions: Sequence[ChatAction]) -> dict[str, Any] | None:
    items = [a.to_dict() for a in actions[:MAX_ACTIONS]]
    return {"type": "actions", "items": items} if items else None


# ------------------------------------------------------------------ graphiques (chiffres de Bank)
def chart_block(
    kind: str,
    title: str,
    *,
    unit: str = "eur",
    items: Sequence[Mapping[str, Any]] = (),
    source: str = "",
    dataset: str = "",
) -> dict[str, Any]:
    """Un graphique prêt à dessiner. ``items`` selon le genre : donut/bars → {label, value, color?, tone?} ; pairs →
    {label, a, b} (reçu/dépensé) ; line → {label, value}. Tout est entier (centimes ou unités)."""
    if kind not in CHART_KINDS:
        raise BlockError("Genre de graphique inconnu")
    rows: list[dict[str, Any]] = []
    for it in list(items)[:MAX_CHART_ITEMS]:
        label = clean(it.get("label"), 40)
        if not label:
            continue
        row: dict[str, Any] = {"label": label}
        if kind == "pairs":
            row["a"], row["b"] = _int(it.get("a")), _int(it.get("b"))
        else:
            row["value"] = _int(it.get("value"))
        color = it.get("color")
        if isinstance(color, str) and re.match(r"^#[0-9a-fA-F]{6}$", color):
            row["color"] = color
        tone = it.get("tone")
        if isinstance(tone, str) and tone in CHART_TONES:
            row["tone"] = tone  # teinte du thème (--c1…), prioritaire sur ``color`` côté interface
        rows.append(row)
    return {
        "type": "chart",
        "kind": kind,
        "title": clean(title, MAX_TITLE),
        "unit": unit if unit in ("eur", "pct", "kwh", "count") else "eur",
        "items": rows,
        "source": clean(source, 80),
        "dataset": clean(dataset, 40),
    }


def _int(v: Any) -> int:
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------------------ schémas (texte du modèle, borné)
def diagram_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Un schéma demandé par le modèle : des boîtes et des flèches (flow) ou des étapes (steps). Jamais de lien,
    identifiants courts, tout borné ; une flèche vers une boîte inconnue est ignorée."""
    kind = raw.get("kind") if raw.get("kind") in DIAGRAM_KINDS else "flow"
    nodes: list[dict[str, str]] = []
    seen: set[str] = set()
    for n in (raw.get("nodes") or [])[:MAX_DIAGRAM_NODES]:
        if not isinstance(n, Mapping):
            continue
        nid = str(n.get("id") or "").strip().lower()
        label = clean(n.get("label"), MAX_NODE_LABEL)
        if not _ID.match(nid) or nid in seen or not label:
            continue
        seen.add(nid)
        node = {"id": nid, "label": label}
        note = clean(n.get("note"), MAX_NODE_NOTE)
        if note:
            node["note"] = note
        nodes.append(node)
    if len(nodes) < 2:
        raise BlockError("Un schéma demande au moins deux boîtes")
    edges: list[dict[str, str]] = []
    pairs: set[tuple[str, str]] = set()
    for e in (raw.get("edges") or [])[:MAX_DIAGRAM_EDGES]:
        if not isinstance(e, Mapping):
            continue
        a, b = str(e.get("from") or "").strip().lower(), str(e.get("to") or "").strip().lower()
        if a not in seen or b not in seen or a == b or (a, b) in pairs:
            continue
        pairs.add((a, b))
        edge = {"from": a, "to": b}
        label = clean(e.get("label"), 32)
        if label:
            edge["label"] = label
        edges.append(edge)
    if kind == "steps" and not edges:
        edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"]} for i in range(len(nodes) - 1)]
    title = clean(raw.get("title"), MAX_TITLE)
    return {"type": "diagram", "kind": kind, "title": title, "nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ petites interfaces proposées par le modèle
def interactive_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Questionnaire local, jamais du HTML ni un ordre bancaire.

    La liste blanche des champs, le nombre d'options et leurs textes sont vérifiés côté
    serveur. Les réponses sont renvoyées comme un nouveau message si l'utilisateur valide.
    Un widget n'exécute aucune action, ne sauvegarde aucun choix et n'envoie aucune requête seul.
    """
    kind = raw.get("kind")
    if kind not in INTERACTIVE_KINDS:
        raise BlockError("Type d'interaction inconnu")
    title = clean(raw.get("title"), MAX_TITLE)
    if not title:
        raise BlockError("Le titre est requis")
    description = clean(raw.get("description"), 240)
    submit_label = clean(raw.get("submit_label"), 32) or "Envoyer mes réponses"
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list):
        raise BlockError("Champs manquants")
    for f in raw_fields[:MAX_INTERACTIVE_FIELDS]:
        if not isinstance(f, Mapping):
            continue
        fid = str(f.get("id") or "").strip().lower()
        label = clean(f.get("label"), MAX_LABEL)
        fkind = f.get("kind")
        if not _ID.fullmatch(fid) or fid in seen or not label or fkind not in FIELD_KINDS:
            continue
        options: list[str] = []
        if fkind in ("single", "multiple"):
            raw_options = f.get("options")
            if not isinstance(raw_options, list):
                continue
            for item in raw_options[:MAX_INTERACTIVE_OPTIONS]:
                option = clean(item, MAX_LABEL)
                if option and option not in options:
                    options.append(option)
            if len(options) < 2:
                continue
        elif kind in ("poll", "checklist"):
            # Un sondage et une checklist ne peuvent afficher un champ libre caché.
            continue
        if kind == "poll" and fkind not in ("single", "multiple"):
            continue
        if kind == "checklist" and fkind != "multiple":
            continue
        if kind in ("poll", "checklist") and fields:
            break
        entry: dict[str, Any] = {
            "id": fid,
            "label": label,
            "kind": fkind,
            "required": f.get("required") is True,
        }
        if options:
            entry["options"] = options
        if fkind == "scale":
            lo, hi = f.get("min"), f.get("max")
            if (
                isinstance(lo, int)
                and not isinstance(lo, bool)
                and isinstance(hi, int)
                and not isinstance(hi, bool)
                and 0 <= lo < hi <= 100
            ):
                entry["min"], entry["max"] = lo, hi
            else:
                entry["min"], entry["max"] = 0, 10
        seen.add(fid)
        fields.append(entry)
    if not fields:
        raise BlockError("Aucune question exploitable")
    if kind in ("poll", "checklist") and len(fields) != 1:
        raise BlockError("Un sondage ou une checklist contient une seule question")
    return {
        "type": "interactive",
        "kind": kind,
        "title": title,
        "description": description,
        "submit_label": submit_label,
        "fields": fields,
    }


# ------------------------------------------------------------------ simulateur : montants de Bank
def scenario_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """L'IA peut demander un simulateur mais ne fournit jamais la courbe ni les calculs."""
    points = raw.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_SCENARIO_DAYS:
        raise BlockError("Prévision du mois manquante")
    validated: list[dict[str, Any]] = []
    previous: date | None = None
    for point in points:
        if not isinstance(point, Mapping):
            raise BlockError("Point de prévision invalide")
        try:
            day = date.fromisoformat(str(point.get("date")))
        except ValueError as exc:
            raise BlockError("Date invalide") from exc
        value = point.get("balance_cents")
        if not isinstance(value, int) or isinstance(value, bool) or abs(value) > MAX_SCENARIO_BALANCE_CENTS:
            raise BlockError("Solde invalide")
        if previous is not None and (day <= previous or (day - previous).days != 1):
            raise BlockError("Dates de la prévision non consécutives")
        if previous is not None and (day.year, day.month) != (validated[0]["year"], validated[0]["month"]):
            raise BlockError("Mois de la prévision incohérent")
        validated.append({"date": day.isoformat(), "balance_cents": value, "year": day.year, "month": day.month})
        previous = day
    raw_cap = raw.get("daily_savings_cap_cents")
    if not isinstance(raw_cap, int) or isinstance(raw_cap, bool):
        raise BlockError("Plafond de simulation invalide")
    cap = max(0, min(100_000, raw_cap))
    return {
        "type": "scenario",
        "title": clean(raw.get("title"), MAX_TITLE) or "Et si… ?",
        "source": clean(raw.get("source"), 100) or "Bank · prévision du mois",
        "points": [{"date": p["date"], "balance_cents": p["balance_cents"]} for p in validated],
        "daily_savings_cap_cents": cap,
        "expense_cap_cents": 200_000,
    }


def decision_board_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Une seule donnée chiffrée source, plusieurs vues et actions locales synchronisées."""
    scenario = scenario_block(raw)
    return {
        **scenario,
        "type": "decision_board",
        "title": clean(raw.get("title"), MAX_TITLE) or "Mon plan, en direct",
    }


PURCHASE_ANSWERS = frozenset({"oui", "oui_mais", "attendre", "plus_tard", "hors_de_portee"})


def purchase_coach_block(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Verdict déjà calculé par Bank. L'interface ne peut pas le recalculer librement."""
    answer = raw.get("answer")
    if not isinstance(answer, str) or answer not in PURCHASE_ANSWERS:
        raise BlockError("Verdict d'achat invalide")
    amount = raw.get("amount_cents")
    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 < amount <= 1_000_000_000:
        raise BlockError("Montant d'achat invalide")
    check = raw.get("this_month", raw)
    if not isinstance(check, Mapping):
        raise BlockError("Prévision d'achat absente")
    before, after = check.get("before"), check.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise BlockError("Données de prévision insuffisantes")

    def snapshot(source: Mapping[str, Any]) -> dict[str, Any]:
        numbers: dict[str, int] = {}
        for key in ("per_day_cents", "lowest_cents", "end_balance_cents"):
            value = source.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or abs(value) > MAX_SCENARIO_BALANCE_CENTS:
                raise BlockError("Valeur de trésorerie invalide")
            numbers[key] = value
        try:
            low_date = date.fromisoformat(str(source.get("lowest_date")))
        except ValueError as exc:
            raise BlockError("Date de prévision invalide") from exc
        overdraft = source.get("overdraft_date")
        if overdraft is not None:
            try:
                overdraft = date.fromisoformat(str(overdraft)).isoformat()
            except ValueError as exc:
                raise BlockError("Date de découvert invalide") from exc
        return {**numbers, "lowest_date": low_date.isoformat(), "overdraft_date": overdraft}

    best_date = check.get("best_date")
    if best_date is not None:
        try:
            best_date = date.fromisoformat(str(best_date)).isoformat()
        except ValueError as exc:
            raise BlockError("Date d'achat invalide") from exc
    alternatives_raw = raw.get("alternatives", [])
    if not isinstance(alternatives_raw, list):
        raise BlockError("Comparaisons de prix invalides")
    alternatives = []
    seen_discounts: set[int] = set()
    for candidate in alternatives_raw[:2]:
        if not isinstance(candidate, Mapping):
            raise BlockError("Comparaison de prix invalide")
        discount = candidate.get("discount_percent")
        if not isinstance(discount, int) or isinstance(discount, bool) or discount not in (20, 40):
            raise BlockError("Réduction hypothétique invalide")
        price = candidate.get("amount_cents")
        expected = amount * (100 - discount) // 100
        if (
            discount in seen_discounts
            or expected < 1
            or not isinstance(price, int)
            or isinstance(price, bool)
            or price != expected
        ):
            raise BlockError("Prix hypothétique incohérent")
        verdict = candidate.get("answer")
        if not isinstance(verdict, str) or verdict not in PURCHASE_ANSWERS:
            raise BlockError("Verdict de comparaison invalide")
        alt_check = candidate.get("this_month", candidate)
        if not isinstance(alt_check, Mapping) or not isinstance(alt_check.get("after"), Mapping):
            raise BlockError("Prévision alternative absente")
        alt_date = alt_check.get("best_date")
        if alt_date is not None:
            try:
                alt_date = date.fromisoformat(str(alt_date)).isoformat()
            except ValueError as exc:
                raise BlockError("Date alternative invalide") from exc
        alternatives.append(
            {
                "discount_percent": discount,
                "amount_cents": price,
                "answer": verdict,
                "headline": clean(candidate.get("headline"), 180),
                "after": snapshot(alt_check["after"]),
                "best_date": alt_date,
            }
        )
        seen_discounts.add(discount)
    from_savings = raw.get("from_savings_cents", 0)
    if not isinstance(from_savings, int) or isinstance(from_savings, bool) or not 0 <= from_savings <= amount:
        raise BlockError("Part prise sur l'épargne invalide")
    paths = raw.get("paths", [])
    if not isinstance(paths, list):
        raise BlockError("Chemins d'épargne invalides")
    options = []
    for item in paths[:2]:
        if not isinstance(item, Mapping):
            continue
        months = item.get("months")
        if months is not None and (not isinstance(months, int) or isinstance(months, bool) or not 0 <= months <= 120):
            continue
        options.append(
            {
                "label": clean(item.get("label"), MAX_LABEL),
                "months": months,
                "note": clean(item.get("note"), 150),
            }
        )
    return {
        "type": "purchase_coach",
        "title": clean(raw.get("title"), MAX_TITLE) or "Est-ce que je peux me l'offrir ?",
        "label": clean(raw.get("label"), 80) or "Cet achat",
        "answer": answer,
        "headline": clean(raw.get("headline"), 180),
        "amount_cents": amount,
        "before": snapshot(before),
        "after": snapshot(after),
        "best_date": best_date,
        "from_savings_cents": from_savings,
        "paths": options,
        "alternatives": alternatives,
        "explanation": clean(raw.get("explanation"), 360),
        "source": "Bank · prévision et plan d'épargne",
    }


# ------------------------------------------------------------------ sources (recherche faite par Bank)
def sources_block(results: Sequence[Mapping[str, Any]], query: str = "") -> dict[str, Any] | None:
    items = []
    for i, r in enumerate(list(results)[:MAX_SOURCES], start=1):
        url = r.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        items.append(
            {
                "n": i,
                "title": clean(r.get("title"), 120, links=True) or url[:120],
                "url": url[:500],
                "host": clean(r.get("host"), 80, links=True),
                "snippet": clean(r.get("snippet"), MAX_SNIPPET, links=True),
                "date": clean(r.get("date"), 10),
            }
        )
    if not items:
        return None
    return {"type": "sources", "query": clean(query, MAX_PROMPT), "items": items}


def note_block(text: str, tone: str = "info") -> dict[str, Any]:
    return {"type": "note", "tone": tone if tone in ("info", "warning") else "info", "text": clean(text, 200)}


# ------------------------------------------------------------------ relecture défensive (base, API)
def validate_blocks(raw: Any) -> list[dict[str, Any]]:
    """Ce qui sort vers le navigateur repasse par ici : un bloc inconnu ou malformé est écarté, pas renvoyé."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for b in raw[:MAX_BLOCKS]:
        if not isinstance(b, Mapping):
            continue
        kind = b.get("type")
        try:
            if kind == "actions":
                items = [
                    it for it in (b.get("items") or [])[:MAX_ACTIONS]
                    if isinstance(it, Mapping) and it.get("kind") in ("navigate", "ask", "act")
                    and clean(it.get("label"), MAX_LABEL)
                    and (it.get("kind") != "navigate" or valid_page(str(it.get("page") or "")))
                ]  # fmt: skip
                if items:
                    out.append({"type": "actions", "items": [dict(it) for it in items]})
            elif kind == "chart":
                out.append(
                    chart_block(
                        str(b.get("kind")),
                        str(b.get("title") or ""),
                        unit=str(b.get("unit") or "eur"),
                        items=[it for it in (b.get("items") or []) if isinstance(it, Mapping)],
                        source=str(b.get("source") or ""),
                        dataset=str(b.get("dataset") or ""),
                    )
                )
            elif kind == "diagram":
                out.append(diagram_block(b))
            elif kind == "interactive":
                out.append(interactive_block(b))
            elif kind == "scenario":
                out.append(scenario_block(b))
            elif kind == "decision_board":
                out.append(decision_board_block(b))
            elif kind == "purchase_coach":
                out.append(purchase_coach_block(b))
            elif kind == "sources":
                rows = [it for it in (b.get("items") or []) if isinstance(it, Mapping)]
                block = sources_block(rows, str(b.get("query") or ""))
                if block:
                    out.append(block)
            elif kind == "note":
                out.append(note_block(str(b.get("text") or ""), str(b.get("tone") or "info")))
        except BlockError:
            continue
    return out


def text_fallback(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    """Pour une messagerie (texte seul) : les boutons deviennent des indications, les sources des liens."""
    lines: list[str] = []
    for b in blocks:
        if b.get("type") == "actions":
            labels = [str(it.get("label")) for it in b.get("items", []) if it.get("kind") != "ask"]
            if labels:
                lines.append("Dans Bank : " + " · ".join(labels))
        elif b.get("type") == "sources":
            lines.append("Sources :")
            lines.extend(f"[{it['n']}] {it['title']} — {it['url']}" for it in b.get("items", []))
        elif b.get("type") == "chart":
            lines.append(f"(graphique « {b.get('title')} » à voir dans Bank)")
        elif b.get("type") == "diagram":
            lines.append(f"(schéma « {b.get('title') or 'étapes'} » à voir dans Bank)")
        elif b.get("type") == "interactive":
            lines.append(f"(questionnaire « {b.get('title')} » à remplir dans Bank)")
        elif b.get("type") == "scenario":
            lines.append("(simulateur de budget interactif à utiliser dans Bank)")
        elif b.get("type") == "decision_board":
            lines.append("(tableau de décision lié, à explorer dans Bank)")
        elif b.get("type") == "purchase_coach":
            lines.append("(coach d'achat interactif : voir le verdict et les choix dans Bank)")
    return lines
