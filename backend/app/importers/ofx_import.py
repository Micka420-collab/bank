"""Import OFX/QFX (1.x SGML et 2.x XML) — sans parseur XML.

Un parseur XML sur un fichier venu d'ailleurs, c'est la porte ouverte à XXE et aux « billion laughs ».
L'OFX bancaire n'a besoin que de quelques balises : on les extrait par expressions régulières bornées,
qui fonctionnent identiquement sur les deux variantes (balises fermées ou non).
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.money import AmountError, parse_amount
from app.importers.base import ImportFormatError, ParsedTransaction, ParseResult, SkippedRow

_BLOCK = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", re.S | re.I)
_LEDGER = re.compile(r"<LEDGERBAL>(.*?)(?:</LEDGERBAL>|(?=<AVAILBAL>)|(?=</STMTRS>))", re.S | re.I)


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}>([^<\r\n]*)", block, re.I)
    return m.group(1).strip() if m else None


def parse_ofx_date(value: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value or "")
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d if 1990 <= d.year <= 2100 else None


def _decode(data: bytes) -> str:
    head = data[:2048].decode("ascii", errors="ignore").upper()
    if "CHARSET:1252" in head or "ENCODING:USASCII" in head:
        return data.decode("cp1252", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def looks_like_ofx(data: bytes) -> bool:
    head = data[:4096].decode("ascii", errors="ignore").upper()
    return "OFXHEADER" in head or "<OFX>" in head


def parse_ofx(data: bytes) -> ParseResult:
    text = _decode(data)
    if "<OFX>" not in text.upper():
        raise ImportFormatError("Fichier OFX invalide (balise <OFX> absente)")
    result = ParseResult(format="ofx")
    acct = _tag(text, "ACCTID")
    result.account_hint = acct
    ledger = _LEDGER.search(text)
    if ledger:
        try:
            amt = _tag(ledger.group(1), "BALAMT")
            result.balance_cents = parse_amount(amt) if amt else None
            result.balance_date = parse_ofx_date(_tag(ledger.group(1), "DTASOF") or "")
        except AmountError:
            result.balance_cents = None
    currency = (_tag(text, "CURDEF") or "EUR").upper()[:3]

    for n, m in enumerate(_BLOCK.finditer(text), start=1):
        block = m.group(1)
        booking = parse_ofx_date(_tag(block, "DTPOSTED") or "")
        if booking is None:
            result.skipped.append(SkippedRow(n, "DTPOSTED illisible"))
            continue
        try:
            amount = parse_amount(_tag(block, "TRNAMT") or "")
        except AmountError:
            result.skipped.append(SkippedRow(n, "TRNAMT illisible"))
            continue
        name, memo = _tag(block, "NAME") or "", _tag(block, "MEMO") or ""
        label = name if not memo or memo in name else f"{name} {memo}".strip()
        fitid = _tag(block, "FITID")
        result.transactions.append(
            ParsedTransaction(
                booking_date=booking,
                amount_cents=amount,
                label=label[:500] or "(sans libellé)",
                value_date=parse_ofx_date(_tag(block, "DTUSER") or ""),
                external_id=fitid[:128] if fitid else None,
                currency=currency,
            )
        )
    if not result.transactions:
        raise ImportFormatError("Aucune opération (STMTTRN) dans le fichier OFX")
    result.detected = {"version": "2.x" if "<?XML" in text[:200].upper() else "1.x", "currency": currency}
    return result
