"""Texte d'une messagerie : découpé sans casser un bloc de code, traduit dans le dialecte de chaque plateforme,
débarrassé des identifiants — et les secrets collés par erreur repérés avant d'aller plus loin.

Tout est pur (aucune E/S) : testé seul, y compris par propriétés."""

from __future__ import annotations

import html
import re
from collections.abc import Callable

# Longueur maximale d'un message, par plateforme (Telegram compte en unités UTF-16).
LIMITS: dict[str, int] = {
    "telegram": 4096,
    "discord": 2000,
    "slack": 3900,
    "whatsapp": 4096,
    "signal": 6000,
    "ntfy": 3800,
}
_SUFFIX_ROOM = 10  # « (12/12) »
_FENCE_ROOM = 24  # fermeture « ``` » + réouverture « ```langage »


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def measure_for(platform: str) -> Callable[[str], int]:
    return utf16_len if platform == "telegram" else len


# ------------------------------------------------------------------ découpe
def _hard_cut(line: str, room: int, measure: Callable[[str], int]) -> list[str]:
    """Coupe une ligne trop longue : aux espaces si possible, sinon au caractère."""
    out: list[str] = []
    rest = line
    while measure(rest) > room:
        cut = len(rest)
        while cut > 1 and measure(rest[:cut]) > room:
            cut = max(1, cut * 9 // 10)
        while cut < len(rest) and measure(rest[: cut + 1]) <= room:
            cut += 1
        space = rest.rfind(" ", 0, cut)
        if space > cut // 2:
            cut = space
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


def split_message(text: str, limit: int, measure: Callable[[str], int] = len) -> list[str]:
    """Découpe un message trop long en morceaux de ``limit`` au plus, numérotés « (i/n) ».

    On coupe entre deux lignes ; une ligne trop longue est coupée aux espaces. Un bloc de code ouvert est
    refermé en fin de morceau et rouvert (même langage) au début du suivant : aucun morceau n'affiche du code
    comme du texte, ni l'inverse."""
    text = text.strip()
    if not text:
        return []
    if measure(text) <= limit:
        return [text]
    room = max(20, limit - _SUFFIX_ROOM - _FENCE_ROOM)
    lines: list[str] = []
    for raw in text.split("\n"):
        lines.extend(_hard_cut(raw, room, measure) if measure(raw) > room else [raw])
    chunks: list[str] = []
    current = ""
    fence: str | None = None
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if current and measure(candidate) > room:
            chunks.append(current + ("\n```" if fence else ""))
            current = f"{fence}\n{line}" if fence else line
        else:
            current = candidate
        if line.lstrip().startswith("```"):
            fence = None if fence else line.strip()[:20]
    if current.strip():
        chunks.append(current)
    total = len(chunks)
    if total == 1:
        return chunks
    # Après une fermeture de bloc, le numéro va à la ligne : « ``` (1/3) » ne refermerait pas le bloc.
    return [
        f"{c.rstrip()}{chr(10) if c.rstrip().endswith('```') else ' '}({i}/{total})" for i, c in enumerate(chunks, 1)
    ]


# ------------------------------------------------------------------ dialectes
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_STAR = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,500})\)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_FENCE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def _protect_code(text: str) -> tuple[str, list[str]]:
    """Met de côté le code (blocs puis en ligne) : aucune conversion ne doit le toucher."""
    saved: list[str] = []

    def keep(match: re.Match[str]) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    return _INLINE_CODE.sub(keep, _FENCE_BLOCK.sub(keep, text)), saved


def _restore(text: str, saved: list[str], render: Callable[[str], str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: render(saved[int(m.group(1))]), text)


def to_plain(text: str) -> str:
    """Texte brut lisible (Signal, ntfy, repli Telegram) : gras et titres sans symboles, liens « texte (url) »."""
    body, saved = _protect_code(text)
    body = _HEADING.sub(r"\1", body)
    body = _BOLD.sub(r"\1", body)
    body = _LINK.sub(r"\1 (\2)", body)
    body = _BULLET.sub(r"\1• ", body)
    return _restore(body, saved, lambda c: c.strip("`") if not c.startswith("```") else c)


def to_telegram_html(text: str) -> str:
    """HTML de Telegram : on n'échappe que & < > et on n'émet que b, i, code, pre et a (https)."""
    body, saved = _protect_code(text)
    body = html.escape(body, quote=False)
    body = _HEADING.sub(r"<b>\1</b>", body)
    body = _BOLD.sub(r"<b>\1</b>", body)
    body = _ITALIC_STAR.sub(r"<i>\1</i>", body)
    body = _LINK.sub(lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', body)
    body = _BULLET.sub(r"\1• ", body)

    def render(code: str) -> str:
        if code.startswith("```"):
            inner = _FENCE_BLOCK.match(code)
            return f"<pre>{html.escape(inner.group(1) if inner else code.strip('`'), quote=False)}</pre>"
        return f"<code>{html.escape(code.strip('`'), quote=False)}</code>"

    return _restore(body, saved, render)


def to_slack(text: str) -> str:
    """mrkdwn de Slack : *gras*, _italique_, liens <url|texte>, et & < > échappés comme Slack l'exige."""
    body, saved = _protect_code(text)
    body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = _ITALIC_STAR.sub(r"_\1_", body)
    body = _HEADING.sub(r"*\1*", body)
    body = _BOLD.sub(r"*\1*", body)
    body = _LINK.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", body)
    body = _BULLET.sub(r"\1• ", body)
    return _restore(body, saved, lambda c: c)


def to_whatsapp(text: str) -> str:
    """WhatsApp : *gras*, _italique_, ```bloc```, liens en clair."""
    body, saved = _protect_code(text)
    body = _ITALIC_STAR.sub(r"_\1_", body)
    body = _HEADING.sub(r"*\1*", body)
    body = _BOLD.sub(r"*\1*", body)
    body = _LINK.sub(r"\1 (\2)", body)
    body = _BULLET.sub(r"\1• ", body)
    return _restore(body, saved, lambda c: c)


def to_discord(text: str) -> str:
    """Discord lit le Markdown ; on évite seulement les titres géants et les aperçus de liens envahissants."""
    body, saved = _protect_code(text)
    body = _HEADING.sub(r"**\1**", body)
    body = _LINK.sub(r"[\1](<\2>)", body)
    return _restore(body, saved, lambda c: c)


FORMATTERS: dict[str, Callable[[str], str]] = {
    "telegram": to_telegram_html,
    "discord": to_discord,
    "slack": to_slack,
    "whatsapp": to_whatsapp,
    "signal": to_plain,
    "ntfy": to_plain,
}


# ------------------------------------------------------------------ identifiants retirés en sortie
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s.\-]?[A-Z0-9]{4}){2,7}(?:[\s.\-]?[A-Z0-9]{1,3})?\b")
_IBAN_ANY = re.compile(_IBAN.pattern, re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)(?<!\d[.,])(?:\d[ -]?){12,18}\d(?!\d|[.,]\d)")
_NIR = re.compile(
    r"(?<!\d)[12]\s?\d{2}\s?(?:0[1-9]|1[0-2]|[2-9]\d)\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}(?:\s?\d{2})?(?!\d)"
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+33\s?|\b0)[1-9](?:[\s.-]?\d{2}){4}\b")
# Sept chiffres et plus (numéro d'allocataire, fiscal, de dossier), sauf un montant écrit sans séparateur.
_LONG_NUMBER = re.compile(r"(?<!\d)(?<!\d[.,])\d{7,}(?!\d|[.,]\d|\s?€)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _card_token(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "[CARTE]" if 13 <= len(digits) <= 19 and _luhn(digits) else match.group(0)


def scrub(text: str, *, privacy: str) -> str:
    """Retire d'un message sortant ce qui n'a rien à faire dans une messagerie : IBAN, numéro de carte, numéro
    de sécurité sociale, longs numéros (fiscal, de dossier) — toujours. E-mails et téléphones aussi, sauf en mode
    complet où ce sont le plus souvent ceux d'un organisme que tu dois contacter."""
    out = _IBAN.sub("[IBAN]", text)
    out = _CARD.sub(_card_token, out)
    out = _NIR.sub("[N° SÉCU]", out)
    if privacy != "full":
        out = _EMAIL.sub("[E-MAIL]", out)
        out = _PHONE.sub("[TÉLÉPHONE]", out)
    return _LONG_NUMBER.sub("[NUMÉRO]", out)


# ------------------------------------------------------------------ secrets collés par erreur (entrée)
_SECRET_WORDS = re.compile(
    r"\b(?:mot\s+de\s+passe|mdp|password|passwd|pass|code\s+(?:secret|pin|confidentiel|d['’]acc[eè]s)|pin|cvv|cvc|"
    r"cryptogramme|identifiants?)\b\s*(?:est|:|=|c['’]est|->)\s*[«\"']?(?=\S*[\d!@#$%^&*?_+=/\\])\S{4,}",
    re.IGNORECASE,
)


def secret_kind(text: str) -> str | None:
    """Ce qui ressemble à un secret dans un message reçu : le message n'ira ni au modèle ni dans l'historique."""
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return "un numéro de carte"
    if _IBAN_ANY.search(text) and re.search(r"\b[A-Z]{2}\d{2}\s?\d{4}", text, re.IGNORECASE):
        return "un IBAN"
    if _SECRET_WORDS.search(text):
        return "un mot de passe ou un code"
    return None
