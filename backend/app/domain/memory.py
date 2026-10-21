"""Mémoire de l'assistant : deux carnets bornés, des opérations atomiques, et un filtre strict à l'écriture comme
à la lecture.

Conception reprise de Hermes Agent (Nous Research, licence MIT) puis adaptée à une application financière :

* Deux carnets : ``user`` (qui tu es : situation, préférences, façon de communiquer, attentes) et ``notes`` (ton
  environnement financier : habitudes, contraintes, ce qui marche, ce qu'il faut suivre). Un fait va dans UN seul.
* Budget en caractères sur le carnet entier (entrées jointes par ``\\n§\\n``) : la mémoire est injectée au début de
  chaque conversation, elle doit rester courte et utile.
* Opérations ``add`` / ``patch`` / ``remove`` appliquées en lot, tout ou rien ; le budget n'est vérifié que sur le
  résultat final, pour qu'un lot puisse consolider (retirer + ajouter) en une fois.
* Un ``patch`` remplace un passage qui doit apparaître une seule fois dans une seule entrée ; ``replace`` n'existe
  pas (les modèles s'en servaient pour écraser des entrées entières).
* Filtre strict : injections de consignes, caractères invisibles, liens, secrets — et, en plus de Hermes, tout ce
  qui identifie un compte ou une personne (IBAN, numéro de carte vérifié par Luhn, numéro de sécurité sociale,
  téléphone, e-mail, longues suites de chiffres). Une entrée bloquée n'est jamais injectée.

Ce module est pur : aucune base, aucun réseau. Le service (services/memory.py) persiste et journalise.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

STORES = ("user", "notes")
DELIMITER = "\n§\n"
LIMITS = {"user": 1_500, "notes": 2_200}
MAX_ENTRY_CHARS = 600  # une entrée = un fait ; au-delà, c'est un compte rendu, pas une mémoire
MAX_OPS_PER_BATCH = 12
TITLES = {
    "user": "CE QUE TU SAIS DE LA PERSONNE (qui elle est)",
    "notes": "TES NOTES (son environnement financier)",
}
_RULE = "═" * 46


class MemoryRefusedError(ValueError):
    """Opération refusée ; le message est destiné au modèle (et à toi) : il dit quoi faire."""


# ---------------------------------------------------------------------------- filtre
# Caractères invisibles et de contrôle bidirectionnel (attaque « trojan source ») : écrits en code, jamais en clair.
_INVISIBLE = frozenset(
    chr(c)
    for c in (
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0x2062,
        0x2063,
        0x2064,
        0xFEFF,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    )
)
_GAP = r"(?:\w+(?:\s+|['’]\s*)){0,8}?"  # jusqu'à 8 mots, élisions comprises (« à l'utilisateur »)
_WHO = r"(?:tell|inform|notify|warn|the\s+user|user|utilisat(?:eur|rice)|personne|propri[eé]taire)"
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in (
        (
            "consignes_ignorees",
            rf"\b(?:ignore|disregard|forget|oublie|ignorez|oubliez)\s+{_GAP}(?:previous|all|above|prior|les|tes|vos|toutes?)\s+{_GAP}(?:instructions?|consignes?|r[eè]gles?)",
        ),
        ("prompt_systeme", r"system\s*prompt|prompt\s+syst[eè]me|instructions?\s+syst[eè]me"),
        (
            "contournement",
            r"\b(?:bypass|contourne[rz]?)\s+" + _GAP + r"(?:restrictions?|s[eé]curit[eé]|r[eè]gles?|filtres?)",
        ),
        ("changement_de_role", r"\b(?:you\s+are\s+now|tu\s+es\s+(?:maintenant|d[eé]sormais)|from\s+now\s+on\s+you)\b"),
        (
            "dissimulation",
            r"\b(?:do\s+not|don't|never"
            r"|ne\s+(?:le\s+|la\s+|lui\s+|leur\s+)?(?:dis|pr[eé]viens|signale|mentionne|montre)\s+(?:pas|rien|jamais)"
            r"|sans\s+(?:le\s+|la\s+|lui\s+)?(?:dire|pr[eé]venir|signaler|mentionner)"
            r"|(?:cache|cacher|dissimule|dissimuler)(?:-le|-la|-les)?)\b\s*" + _GAP + _WHO,
        ),
        ("html_cache", r"<!--|<\s*(?:div|span|script|style)\b"),
        ("lien", r"\b(?:https?|ftp)://|\bwww\.[a-z0-9-]+\.[a-z]{2,}"),
        ("exfiltration", r"\b(?:curl|wget|fetch)\b"),
        (
            "acces_confidentiel",
            r"\b(?:api[\s_-]?key|token|password|passwd|mot\s+de\s+passe|mdp|code\s+(?:pin|secret|d'acc[eè]s|de\s+connexion)|identifiant\s+de\s+connexion)\b\s*(?:est|is|:|=)\s*\S{4,}",
        ),
        (
            "code_confidentiel",
            r"\b(?:(?:digi)?code(?!\s+postal)|pin|cvv|cvc|cryptogramme)\b\W{0,3}(?:\w+\W+){0,5}?\d{3,8}\b",
        ),
        ("email", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        ("telephone", r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b"),
        ("iban", r"\b[A-Z]{2}\d{2}(?:[\s.-]?[A-Z0-9]{4}){2,7}(?:[\s.-]?[A-Z0-9]{1,3})?\b"),
        (
            "securite_sociale",
            r"\b[12][\s.]?\d{2}[\s.]?(?:0[1-9]|1[0-2]|[2-9]\d)[\s.]?(?:\d{2}|2[AB])[\s.]?\d{3}[\s.]?\d{3}(?:[\s.]?\d{2})?\b",
        ),
    )
)
_DIGIT_RUN = re.compile(r"(?:\d[\s.-]?){10,}")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scan(text: str) -> list[str]:
    """Raisons de refus (vide = acceptable). Appelé à l'écriture ET à la lecture (une entrée modifiée hors de Bank
    ne doit pas pouvoir piloter l'assistant)."""
    reasons: list[str] = []
    raw = text[:65_536]
    for ch in raw:
        if ch in _INVISIBLE:
            reasons.append(f"caractere_invisible_U+{ord(ch):04X}")
            break
    norm = unicodedata.normalize("NFKC", raw)
    for name, rx in _PATTERNS:
        if rx.search(norm):
            reasons.append(name)
    for m in _CARD.finditer(norm):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            reasons.append("numero_de_carte")
            break
    if "numero_de_carte" not in reasons and "iban" not in reasons and _DIGIT_RUN.search(norm):
        reasons.append("longue_suite_de_chiffres")
    return reasons


REASON_LABELS = {
    "consignes_ignorees": "une consigne qui demande d'ignorer les règles",
    "prompt_systeme": "une référence aux consignes système",
    "contournement": "une demande de contournement",
    "changement_de_role": "un changement de rôle",
    "dissimulation": "une demande de cacher quelque chose",
    "html_cache": "du HTML",
    "lien": "un lien",
    "exfiltration": "une commande réseau",
    "acces_confidentiel": "un secret (mot de passe, code, clé)",
    "code_confidentiel": "un code confidentiel",
    "email": "une adresse e-mail",
    "telephone": "un numéro de téléphone",
    "iban": "un IBAN",
    "securite_sociale": "un numéro de sécurité sociale",
    "numero_de_carte": "un numéro de carte bancaire",
    "longue_suite_de_chiffres": "un long numéro (compte, contrat, identifiant)",
}


def explain(reasons: Sequence[str]) -> str:
    parts = [
        REASON_LABELS.get(r, "un caractère invisible" if r.startswith("caractere_invisible") else r) for r in reasons
    ]
    return "Refusé : la mémoire ne garde jamais " + ", ".join(dict.fromkeys(parts)) + "."


# ---------------------------------------------------------------------------- carnets
@dataclass
class Entry:
    id: int | None
    content: str
    pinned: bool = False


@dataclass(frozen=True)
class Op:
    action: str  # add | patch | remove
    content: str = ""  # add
    old_text: str = ""  # patch, remove
    new_text: str = ""  # patch


@dataclass
class BatchResult:
    entries: list[Entry]
    added: list[str] = field(default_factory=list)
    patched: list[tuple[str, str]] = field(default_factory=list)  # (avant, après)
    removed: list[str] = field(default_factory=list)
    skipped_duplicates: int = 0


def joined_length(contents: Sequence[str]) -> int:
    return len(DELIMITER.join(contents))


def usage_label(store: str, contents: Sequence[str]) -> str:
    used, limit = joined_length(contents), LIMITS[store]
    pct = round(100 * used / limit) if limit else 0
    return f"{pct} % — {used:,}/{limit:,} caractères".replace(",", " ")


_QUOTES = "'‘’‚‛′`´“”«»\""
_DASHES = "-‐‑‒–—―−"


def _fold(text: str) -> str:
    """Comparaison tolérante à la typographie : guillemets (et leurs espaces), tirets, espaces, casse."""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\\n", " ")
    t = re.sub(f"[{re.escape(_QUOTES)}]", "'", t)
    t = re.sub(f"[{re.escape(_DASHES)}]", "-", t)
    t = re.sub(r"\s*'\s*", "'", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _tolerant_pattern(needle: str) -> re.Pattern[str]:
    """Motif qui retrouve ``needle`` dans le texte d'origine malgré la typographie : n'importe quel guillemet (avec
    ou sans espaces autour, « » à la française), n'importe quel tiret, espaces multiples, casse. Les positions
    trouvées sont celles du texte d'origine : on peut remplacer exactement le passage cité."""
    text = needle.replace("\\n", " ").strip()
    quote = rf"\s*[{re.escape(_QUOTES)}]\s*"
    parts: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            j = i
            while j < len(text) and text[j].isspace():
                j += 1
            near_quote = (i > 0 and text[i - 1] in _QUOTES) or (j < len(text) and text[j] in _QUOTES)
            if not near_quote:  # les espaces autour d'un guillemet sont déjà facultatifs
                parts.append(r"\s+")
            i = j
            continue
        if ch in _QUOTES:
            parts.append(quote)
        elif ch in _DASHES:
            parts.append(f"[{re.escape(_DASHES)}]")
        else:
            parts.append(re.escape(ch))
        i += 1
    return re.compile("".join(parts), re.IGNORECASE)


def _check_needle(old_text: str) -> str:
    needle = old_text.strip()
    if not needle or not re.search(r"\w", needle):
        raise MemoryRefusedError("Indique un extrait (old_text) de l'entrée à modifier ou retirer.")
    if "§" in needle:
        raise MemoryRefusedError("old_text doit venir d'une seule entrée (sans « § »).")
    return needle


def _match(entries: Sequence[Entry], old_text: str) -> int:
    """Index de l'entrée visée par ``old_text`` : égalité, puis sous-chaîne exacte, puis sous-chaîne tolérante
    (typographie). Deux entrées différentes qui correspondent : refus (on ne devine pas)."""
    needle = _check_needle(old_text)
    for i, e in enumerate(entries):
        if e.content == needle:
            return i
    pattern = _tolerant_pattern(needle)
    for tolerant in (False, True):
        hits = [i for i, e in enumerate(entries) if (pattern.search(e.content) if tolerant else needle in e.content)]
        distinct = {entries[i].content for i in hits}
        if len(distinct) > 1:
            previews = " | ".join(c[:80] for c in sorted(distinct))
            raise MemoryRefusedError(
                f"Plusieurs entrées correspondent à « {needle[:60]} » : sois plus précis. ({previews})"
            )
        if hits:
            return hits[0]
    words = set(_fold(needle).split())
    scored = sorted(
        (
            (
                SequenceMatcher(None, _fold(needle), _fold(e.content)).ratio(),
                len(words & set(_fold(e.content).split())),
                e.content,
            )
            for e in entries
        ),
        reverse=True,
    )
    close = [c[:80] for ratio, common, c in scored[:3] if ratio >= 0.3 or common >= 2]
    hint = f" Entrées proches : {' | '.join(close)}" if close else ""
    raise MemoryRefusedError(f"Aucune entrée ne correspond à « {needle[:60]} ».{hint}")


def patch_text(content: str, old_text: str, new_text: str) -> str:
    """L'entrée corrigée : le passage cité (une seule occurrence, exacte ou à la typographie près) remplacé."""
    span = _check_needle(old_text)
    replacement = new_text.strip()
    if not replacement:
        raise MemoryRefusedError(
            "Pour effacer un passage, corrige un passage plus long qui le contient ; "
            "« remove » retire l'entrée entière."
        )
    if content == span:
        return replacement
    if span in content:
        if content.count(span) > 1:
            raise MemoryRefusedError("Le passage apparaît plusieurs fois dans l'entrée : prends un extrait plus long.")
        return content.replace(span, replacement, 1)
    found = list(_tolerant_pattern(span).finditer(content))
    if len(found) > 1:
        raise MemoryRefusedError("Le passage apparaît plusieurs fois dans l'entrée : prends un extrait plus long.")
    if not found:
        raise MemoryRefusedError("Reprends l'extrait exact de l'entrée (copié tel quel) pour la corriger.")
    g = found[0]
    # Les espaces facultatifs autour d'un guillemet appartiennent au texte voisin : on les garde.
    start = g.start() + len(g.group(0)) - len(g.group(0).lstrip())
    end = g.end() - (len(g.group(0)) - len(g.group(0).rstrip()))
    return content[:start] + replacement + content[end:]


def apply_batch(
    store: str, entries: Sequence[Entry], ops: Sequence[Op], *, allow_destructive: bool = True
) -> BatchResult:
    """Applique un lot sur une copie ; ne renvoie le nouvel état que si TOUT passe (budget vérifié à la fin)."""
    if store not in STORES:
        raise MemoryRefusedError("Carnet inconnu : « user » (la personne) ou « notes » (son environnement).")
    if not ops:
        raise MemoryRefusedError("Aucune opération.")
    if len(ops) > MAX_OPS_PER_BATCH:
        raise MemoryRefusedError(f"Au plus {MAX_OPS_PER_BATCH} opérations par lot.")
    work = [Entry(e.id, e.content, e.pinned) for e in entries]
    result = BatchResult(entries=work)
    for op in ops:
        if op.action == "add":
            content = op.content.strip()
            if not content:
                raise MemoryRefusedError("Une entrée vide n'a pas de sens.")
            if len(content) > MAX_ENTRY_CHARS:
                raise MemoryRefusedError(
                    f"Entrée trop longue ({len(content)} caractères, {MAX_ENTRY_CHARS} au plus) : un fait par entrée."
                )
            reasons = scan(content)
            if reasons:
                raise MemoryRefusedError(explain(reasons))
            if any(_fold(e.content) == _fold(content) for e in work):
                result.skipped_duplicates += 1
                continue
            work.append(Entry(None, content))
            result.added.append(content)
        elif op.action == "patch":
            if not allow_destructive:
                raise MemoryRefusedError("Modification réservée à une conversation ou à ta validation.")
            if not op.new_text.strip():
                raise MemoryRefusedError(
                    "Pour effacer un passage, corrige un passage plus long qui le contient ; "
                    "« remove » retire l'entrée entière."
                )
            i = _match(work, op.old_text)
            old_entry = work[i]
            if old_entry.pinned:
                raise MemoryRefusedError("Entrée épinglée : seul l'utilisateur peut la modifier.")
            updated = patch_text(old_entry.content, op.old_text, op.new_text)
            if updated == old_entry.content:
                raise MemoryRefusedError("Cette correction ne change rien.")
            if len(updated) > MAX_ENTRY_CHARS:
                raise MemoryRefusedError(f"Entrée trop longue après correction ({len(updated)} caractères).")
            reasons = scan(updated)
            if reasons:
                raise MemoryRefusedError(explain(reasons))
            work[i] = Entry(old_entry.id, updated, old_entry.pinned)
            result.patched.append((old_entry.content, updated))
        elif op.action == "remove":
            if not allow_destructive:
                raise MemoryRefusedError("Suppression réservée à une conversation ou à ta validation.")
            i = _match(work, op.old_text)
            if work[i].pinned:
                raise MemoryRefusedError("Entrée épinglée : seul l'utilisateur peut la retirer.")
            result.removed.append(work.pop(i).content)
        else:
            raise MemoryRefusedError("Action inconnue : add, patch ou remove (pas de replace).")
    if entries and not work and len(ops) > 1:
        raise MemoryRefusedError("Refus de vider tout le carnet en un lot : retire les entrées une à une.")
    used = joined_length([e.content for e in work])
    if used > LIMITS[store]:
        current = DELIMITER.join(e.content for e in entries)
        raise MemoryRefusedError(
            f"Carnet « {store} » plein : {used}/{LIMITS[store]} caractères après ce lot. Refais UN lot qui retire ou "
            f"raccourcit des entrées périmées ET ajoute la nouvelle. Entrées actuelles :\n{current}"
        )
    result.entries = work
    return result


def render_block(store: str, contents: Sequence[str]) -> str:
    """Bloc injecté dans le prompt système ; vide si le carnet est vide."""
    if not contents:
        return ""
    return f"{_RULE}\n{TITLES[store]} [{usage_label(store, contents)}]\n{_RULE}\n" + DELIMITER.join(contents)


def safe_contents(contents: Sequence[str]) -> list[str]:
    """Contenu prêt à injecter : une entrée qui ne passe plus le filtre (modifiée ailleurs) est neutralisée."""
    out: list[str] = []
    for c in contents:
        reasons = scan(c)
        out.append(f"[BLOQUÉ : entrée retirée du contexte ({', '.join(reasons)}) — à supprimer]" if reasons else c)
    return out
