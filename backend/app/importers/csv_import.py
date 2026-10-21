"""Import CSV « toutes banques françaises » sans configuration.

Détecte seul : encodage (UTF-8/BOM ou Windows-1252), séparateur, lignes d'en-tête parasites
(nom de banque, n° de compte, période…), colonnes (date, libellé, montant OU débit/crédit),
formats de date et de montant FR/EN. Sans en-tête, les colonnes sont devinées par leur contenu.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime

from app.domain.money import AmountError, parse_amount
from app.importers.base import ImportFormatError, ParsedTransaction, ParseResult, SkippedRow

MAX_ROWS = 100_000
_DELIMS = (";", "\t", ",", "|")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "date": (
        "date",
        "date operation",
        "date d operation",
        "date de l operation",
        "date comptable",
        "date de comptabilisation",
        "dateop",
        "date op",
        "started date",
        "completed date",
        "booking date",
        "transaction date",
        "date transaction",
        "date de transaction",
    ),
    "value_date": ("date valeur", "date de valeur", "dateval", "value date"),
    "label": (
        "libelle",
        "libelle operation",
        "libelle simplifie",
        "libelle de l operation",
        "label",
        "description",
        "detail",
        "details",
        "intitule",
        "nature de l operation",
        "operation",
        "motif",
        "libelle court",
    ),
    "label2": (
        "libelle complementaire",
        "informations complementaires",
        "detail de l ecriture",
        "reference",
        "libelle long",
        "infos complementaires",
        "categorie",
    ),
    "amount": (
        "montant",
        "montant eur",
        "montant euros",
        "montant en euros",
        "amount",
        "montant de l operation",
        "valeur",
        "somme",
    ),
    "debit": ("debit", "debit euros", "debit eur", "montant debit", "sortie", "depense", "debits"),
    "credit": ("credit", "credit euros", "credit eur", "montant credit", "entree", "recette", "credits"),
    "balance": ("solde", "balance", "solde euros", "solde eur"),
    "currency": ("devise", "currency", "monnaie"),
}
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d-%m-%y",
    "%Y/%m/%d",
    "%d.%m.%y",
)


def _norm_header(cell: str) -> str:
    s = unicodedata.normalize("NFKD", cell)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def map_header(cells: Sequence[str]) -> dict[str, int]:
    """Associe chaque champ connu à un index de colonne. Correspondance exacte d'abord, puis par préfixe
    (« montant (eur) » -> montant). Une colonne ne sert qu'une fois."""
    norm = [_norm_header(c) for c in cells]
    mapping: dict[str, int] = {}
    used: set[int] = set()
    for exact in (True, False):
        for fname, aliases in FIELD_ALIASES.items():
            if fname in mapping:
                continue
            best: tuple[int, int] | None = (
                None  # (score, -index) : exact -> 1re colonne ; préfixe -> alias le plus long
            )
            for i, h in enumerate(norm):
                if i in used or not h:
                    continue
                for a in aliases:
                    hit = h == a if exact else h.startswith(a)
                    score = 0 if exact else len(a)
                    if hit and (best is None or (score, -i) > best):
                        best = (score, -i)
            if best is not None:
                mapping[fname] = -best[1]
                used.add(-best[1])
    return mapping


def parse_date(value: str) -> date | None:
    s = value.strip().split("T")[0].split(" ")[0]
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()  # noqa: DTZ007 - une date seule, sans heure
        except ValueError:
            continue
        if 1990 <= d.year <= 2100:
            return d
    return None


def _decode(data: bytes) -> tuple[str, str]:
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _sniff_delimiter(lines: Sequence[str]) -> str:
    sample = [ln for ln in lines if ln.strip()][:40]
    best, best_score = ";", -1
    for d in _DELIMS:
        counts = [len(row) for row in csv.reader(sample, delimiter=d)]
        mode, freq = Counter(c for c in counts if c > 1).most_common(1)[0] if any(c > 1 for c in counts) else (1, 0)
        score = freq * 10 + mode
        if score > best_score:
            best, best_score = d, score
    return best


def _is_amount(cell: str) -> bool:
    try:
        parse_amount(cell)
    except AmountError:
        return False
    return True


def _infer_headerless(rows: Sequence[Sequence[str]]) -> dict[str, int]:
    width = Counter(len(r) for r in rows).most_common(1)[0][0]
    sample = [r for r in rows if len(r) == width][:50]
    if not sample:
        return {}
    date_cols = [i for i in range(width) if sum(parse_date(r[i]) is not None for r in sample) >= 0.8 * len(sample)]
    num_cols = [
        i
        for i in range(width)
        if i not in date_cols
        and sum(_is_amount(r[i]) for r in sample if r[i].strip())
        >= 0.8 * max(1, sum(bool(r[i].strip()) for r in sample))
        and any(r[i].strip() for r in sample)
    ]
    text_cols = [i for i in range(width) if i not in date_cols and i not in num_cols]
    if not date_cols or not num_cols or not text_cols:
        return {}
    mapping = {"date": date_cols[0]}
    if len(date_cols) > 1:
        mapping["value_date"] = date_cols[1]
    mapping["label"] = max(text_cols, key=lambda i: sum(len(r[i]) for r in sample))
    # Deux colonnes numériques creuses et complémentaires = débit / crédit ; sinon la première = montant.
    if len(num_cols) >= 2:
        a, b = num_cols[0], num_cols[1]
        exclusive = all(not (r[a].strip() and r[b].strip()) for r in sample)
        if exclusive:
            mapping["debit"], mapping["credit"] = a, b
            return mapping
    mapping["amount"] = num_cols[0]
    return mapping


def _clean(cell: str) -> str:
    return _CTRL.sub(" ", cell).strip()[:500]


def parse_csv(data: bytes) -> ParseResult:
    text, encoding = _decode(data)
    lines = text.splitlines()
    if not any(ln.strip() for ln in lines):
        raise ImportFormatError("Fichier vide")
    delim = _sniff_delimiter(lines)
    rows = [[_clean(c) for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    if len(rows) > MAX_ROWS:
        raise ImportFormatError(f"Fichier trop long (> {MAX_ROWS} lignes)")

    header_idx, mapping = -1, {}
    for i, row in enumerate(rows[:40]):
        m = map_header(row)
        if "date" in m and ("amount" in m or ("debit" in m and "credit" in m) or "debit" in m):
            header_idx, mapping = i, m
            break
    data_rows = rows[header_idx + 1 :] if header_idx >= 0 else rows
    if header_idx < 0:
        mapping = _infer_headerless([r for r in rows if any(c for c in r)])
        if not mapping:
            raise ImportFormatError("Colonnes introuvables : il faut au moins une date, un libellé et un montant")
    if "label" not in mapping:
        free = [i for i in range(max(len(r) for r in data_rows[:50] or [[]])) if i not in mapping.values()]
        if not free:
            raise ImportFormatError("Colonne libellé introuvable")
        mapping["label"] = free[0]

    result = ParseResult(format="csv")
    result.detected = {
        "encoding": encoding,
        "delimiter": {"\t": "TAB"}.get(delim, delim),
        "header_line": str(header_idx + 1 if header_idx >= 0 else 0),
        **{k: str(v) for k, v in sorted(mapping.items(), key=lambda kv: kv[1])},
    }
    first_line = header_idx + 2
    for n, row in enumerate(data_rows, start=first_line):
        if not any(row):
            continue

        def cell(name: str, row: list[str] = row) -> str:
            idx = mapping.get(name)
            return row[idx] if idx is not None and idx < len(row) else ""

        booking = parse_date(cell("date"))
        if booking is None:
            result.skipped.append(SkippedRow(n, "date illisible"))
            continue
        try:
            if "amount" in mapping and cell("amount"):
                amount = parse_amount(cell("amount"))
            else:
                debit, credit = cell("debit"), cell("credit")
                if debit:
                    amount = -abs(parse_amount(debit))
                elif credit:
                    amount = abs(parse_amount(credit))
                else:
                    result.skipped.append(SkippedRow(n, "montant manquant"))
                    continue
        except AmountError:
            result.skipped.append(SkippedRow(n, "montant illisible"))
            continue
        label = " ".join(p for p in (cell("label"), cell("label2")) if p).strip() or "(sans libellé)"
        currency = (cell("currency") or "EUR").upper()[:3]
        result.transactions.append(
            ParsedTransaction(
                booking_date=booking,
                amount_cents=amount,
                label=label,
                value_date=parse_date(cell("value_date")),
                currency=currency if currency.isalpha() else "EUR",
            )
        )
    if not result.transactions:
        raise ImportFormatError("Aucune opération lisible dans le fichier")
    return result
