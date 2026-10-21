"""Montants : centimes entiers partout. Le float n'entre jamais dans un calcul d'argent."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

_CURRENCY = re.compile(r"€|\bEUR(?:OS?)?\b|\bEuros?\b", re.IGNORECASE)
_SPACES = str.maketrans("", "", "    '’")
_MINUS = ("-", "−", "–")


class AmountError(ValueError):
    pass


def parse_amount(text: str) -> int:
    """Convertit un montant texte de banque (FR ou EN) en centimes.

    Gère : « 1 234,56 », « -1.234,56 », « 1,234.56 », « 12,50- », « (12.50) », « +3 », « −7,10 € ».
    Règle d'ambiguïté : un seul séparateur suivi d'exactement 3 chiffres (« 1.234 ») = milliers —
    un montant en euros n'a jamais 3 décimales.
    """
    s = _CURRENCY.sub("", text or "").strip()
    if not s:
        raise AmountError("montant vide")
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.endswith(_MINUS):
        negative, s = not negative, s[:-1].strip()
    if s.startswith(_MINUS):
        negative, s = not negative, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.translate(_SPACES)
    if not s or not re.fullmatch(r"[0-9.,]+", s):
        raise AmountError(f"montant invalide : {text!r}")

    last = max(s.rfind(","), s.rfind("."))
    if last == -1:
        int_part, frac = s, ""
    else:
        sep = s[last]
        int_part, frac = s[:last], s[last + 1 :]
        other = "." if sep == "," else ","
        if len(frac) == 3 and (s.count(sep) > 1 or other not in s) and int_part:
            int_part, frac = s, ""  # c'était un séparateur de milliers
        elif s.count(sep) > 1:
            raise AmountError(f"séparateur décimal répété : {text!r}")
    int_digits = int_part.replace(",", "").replace(".", "")
    if int_part and not int_digits:
        raise AmountError(f"montant invalide : {text!r}")
    if not (int_digits + frac).isdigit():
        raise AmountError(f"montant invalide : {text!r}")
    value = Decimal(f"{int_digits or '0'}.{frac or '0'}")
    cents = int(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) * 100)
    return -cents if negative else cents


def decimal_to_cents(value: Decimal | str) -> int:
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise AmountError(f"montant invalide : {value!r}") from exc
    if not d.is_finite():
        raise AmountError("montant non fini")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def format_eur(cents: int) -> str:
    """Format français : « -1 234,56 € » (espace fine insécable pour les milliers)."""
    sign = "-" if cents < 0 else ""
    euros, rem = divmod(abs(cents), 100)
    grouped = f"{euros:,}".replace(",", " ")
    return f"{sign}{grouped},{rem:02d} €"
