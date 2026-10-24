"""Exports de plateformes crypto (Kraken, Binance, Coinbase) → opérations du 2086, sans rien envoyer.

Garanties
---------
* Hors ligne : la lecture ne fait aucun appel réseau, aucun cours n'est cherché. Seules les jambes en euros
  donnent un montant ; une contrepartie dans une autre monnaie est signalée, jamais convertie à un cours inventé.
* Rien d'identifiant n'est gardé : ni User_ID (Binance), ni identifiant de transaction, ni adresse, ni note, ni
  les lignes d'en-tête de Coinbase (ton nom). Chaque ligne laisse une empreinte (SHA-256) qui suffit à écarter
  les doublons d'un import à l'autre.
* Taille, lignes et montants bornés ; nombres lus en ``Decimal`` sous une forme stricte (une cellule « =SOMME(…) »
  n'est qu'un texte illisible, « 0,006 » n'est jamais lu 6) ; un export ressaisi par un tableur français
  (séparateur « ; ») lit sa virgule comme décimale ; heures UTC ramenées à Paris pour l'année fiscale.
* Échanger une crypto contre une autre (stablecoins compris) n'est pas imposable (art. 150 VH bis du CGI) : ces
  lignes sont écartées, avec leur raison.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import partial
from zoneinfo import ZoneInfo

from app.importers.base import ImportFormatError

MAX_BYTES = 5_000_000
MAX_ROWS = 20_000
MAX_CENTS = 100_000_000_000  # mêmes bornes que la saisie à la main
PARIS = ZoneInfo("Europe/Paris")
PLATFORMS = {"kraken": "Kraken", "binance": "Binance", "coinbase": "Coinbase"}

FIAT = frozenset({
    "EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "TRY", "BRL", "RUB", "UAH", "NGN", "ZAR", "PLN", "SEK",
    "NOK", "DKK", "CZK", "HUF", "RON", "ARS", "MXN", "KZT", "INR", "IDR", "HKD", "SGD", "AED",
})  # fmt: skip
# Codes historiques de Kraken (XXBT, ZEUR…) : le reste de ses actifs porte déjà son symbole usuel.
_KRAKEN = {
    "XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ETH2": "ETH", "XXDG": "DOGE", "XDG": "DOGE", "XLTC": "LTC",
    "XXRP": "XRP", "XXLM": "XLM", "XXMR": "XMR", "XETC": "ETC", "XZEC": "ZEC", "XREP": "REP", "XMLN": "MLN",
    "XICN": "ICN", "XNMC": "NMC", "XXVN": "XVN", "ZEUR": "EUR", "ZUSD": "USD", "ZGBP": "GBP", "ZCAD": "CAD",
    "ZJPY": "JPY", "ZAUD": "AUD", "ZCHF": "CHF",
}  # fmt: skip

SWAP = "Échange crypto contre crypto : pas imposable (sursis d'imposition, art. 150 VH bis)"
TRANSFER = "Dépôt, retrait ou virement entre tes comptes : pas une cession"
SEND = "Envoi de cryptos : pas une cession — sauf si tu as payé un bien ou un service avec, à noter alors à la main"
REWARD = "Récompense (staking, intérêts, airdrop…) : pas une vente, elle entre dans ton portefeuille sans prix payé"
INTERNAL = "Mouvement interne à la plateforme (épargne, staking, sous-compte)"
DERIVATIVES = "Marge ou produit dérivé : hors du cadre des cessions de particulier, à voir à la main"
FIAT_ONLY = "Change entre monnaies : pas une opération sur crypto"
PENDING = "Ligne en attente sans identifiant : sa ligne définitive suffit"
NO_EUR = "Pas de montant en euros dans l'export : note cette opération à la main si elle compte"


def other_fiat(code: str) -> str:
    return f"Contrepartie en {code} : Bank ne convertit pas avec un cours inventé — note-la à la main en euros"


@dataclass(frozen=True)
class Movement:
    """Une acquisition (achat payé en euros) ou une cession (vente contre des euros) prête à noter."""

    key: str  # empreinte : jamais l'identifiant de la plateforme lui-même
    line: int
    day: date  # date à Paris
    kind: str  # acquisition | cession
    amount_cents: int  # prix payé, ou prix de cession
    fees_cents: int
    asset: str | None
    quantity: Decimal | None


@dataclass(frozen=True)
class Ignored:
    line: int
    reason: str


@dataclass
class CryptoImport:
    platform: str
    format: str
    rows: int = 0
    movements: list[Movement] = field(default_factory=list)
    ignored: list[Ignored] = field(default_factory=list)


# ------------------------------------------------------------------ cellules
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_NUMBER = re.compile(r"[+-]?(?:\d{1,15}(?:\.\d{1,18})?|\.\d{1,18})(?:[eE][+-]?\d{1,2})?")
_SYMBOL = re.compile(r"[A-Z0-9]{2,12}")
_PAIR_NOTE = re.compile(r"\bON ([A-Z0-9]{2,12})-([A-Z0-9]{2,12})\b")


def text(cell: str | None, limit: int = 60) -> str:
    """Une cellule lue comme du texte, rien d'autre : sans caractère de contrôle ni signe de formule en tête."""
    return " ".join(_CTRL.sub(" ", cell or "").split()).lstrip("=+-@ ")[:limit]


def _plain(raw: str, comma: bool) -> str | None:
    """« 1,234.56 », « 1.234,56 », ou « 0,006 » quand la virgule est décimale → « 1234.56 » ; None si ambigu."""
    if "," in raw and "." in raw:
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
    else:
        dec = "," if comma and "," in raw else "."
    sep = "." if dec == "," else ","
    whole, _, frac = raw.rpartition(dec) if dec in raw else (raw, "", "")
    if sep in whole:  # des milliers : des groupes de trois chiffres, sinon « 0,006 » deviendrait 6
        if not re.fullmatch(rf"[1-9]\d{{0,2}}(?:{re.escape(sep)}\d{{3}})+", whole):
            return None
        whole = whole.replace(sep, "")
    return f"{whole}.{frac}" if dec in raw else whole


def number(cell: str | None, *, comma: bool = False) -> Decimal | None:
    """Un nombre écrit à la manière des exports (« 0.00120000 », « -€1,234.56 », « 1E-8 ») ; None sinon. Avec
    ``comma``, la virgule est décimale (« -300,00 », « 1 234,56 »)."""
    raw = (cell or "").strip().replace(" ", "").replace(" ", "").replace("\u202f", "")  # espace fine des milliers
    neg = raw.startswith(("-", "("))
    raw = raw.strip("()").lstrip("+-").lstrip("€$£").lstrip("+-")
    for sym in ("EUR", "USD", "GBP"):
        raw = raw.removesuffix(sym)
    plain = _plain(raw, comma)
    if plain is None or not _NUMBER.fullmatch(plain):
        return None
    try:
        value = Decimal(plain)
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) > Decimal("1e15"):
        return None
    return -value if neg else value


def cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def asset(raw: str | None, *, kraken: bool = False) -> str:
    code = text(raw, 24).upper()
    if kraken:
        code = code.split(".")[0]  # XBT.M, ETH2.S, EUR.HOLD : la variante de staking ou d'attente
        code = _KRAKEN.get(code, code)
    return code


def when(raw: str | None) -> datetime | None:
    """Heure UTC des exports (« 2026-03-01 10:00:00 », « … UTC », ISO avec Z, ou l'année sur deux chiffres)."""
    s = text(raw, 40).upper().removesuffix(" UTC").removesuffix("Z").replace("T", " ").strip()
    s = s.split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def paris_day(at: datetime) -> date:
    return at.astimezone(PARIS).date()


def fingerprint(platform: str, ref: str) -> str:
    return hashlib.sha256(f"crypto-import|{platform}|{ref}".encode()).hexdigest()


def _symbol(code: str) -> str | None:
    return code if _SYMBOL.fullmatch(code) else None


# ------------------------------------------------------------------ lecture et reconnaissance
def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _norm(cell: str) -> str:
    return " ".join(cell.strip().strip('"').lower().replace("_", " ").split())


_SIGNATURES: tuple[tuple[str, frozenset[str]], ...] = (
    ("kraken_ledgers", frozenset({"txid", "refid", "time", "type", "asset", "amount", "fee"})),
    ("kraken_trades", frozenset({"txid", "pair", "time", "type", "cost", "fee", "vol"})),
    ("binance", frozenset({"utc time", "operation", "coin", "change"})),
    ("coinbase", frozenset({"timestamp", "transaction type", "asset", "quantity transacted"})),
)


def _rows(data: bytes) -> tuple[list[list[str]], str]:
    if len(data) > MAX_BYTES:
        raise ImportFormatError("Fichier trop volumineux (5 Mo au plus)")
    content = _decode(data)
    if not content.strip():
        raise ImportFormatError("Fichier vide")
    head = [ln for ln in content.splitlines()[:20] if ln.strip()]
    delim = max((",", ";", "\t"), key=lambda d: max((ln.count(d) for ln in head), default=0))
    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(content), delimiter=delim):
            rows.append(row)
            if len(rows) > MAX_ROWS + 20:
                raise ImportFormatError(f"Fichier trop long (plus de {MAX_ROWS} lignes)")
    except csv.Error:
        raise ImportFormatError("CSV illisible (cellule démesurée ou guillemets mal fermés)") from None
    return rows, delim


def parse(data: bytes) -> CryptoImport:
    """Reconnaît la plateforme à ses en-têtes, puis lit le fichier."""
    rows, delim = _rows(data)
    for i, row in enumerate(rows[:15]):
        cells = {_norm(c) for c in row}
        for fmt, needed in _SIGNATURES:
            if needed <= cells:
                header = [_norm(c) for c in row]
                body = [
                    (i + 2 + n, dict(zip(header, r, strict=False)))
                    for n, r in enumerate(rows[i + 1 :])
                    if any(c.strip() for c in r)
                ]
                if len(body) > MAX_ROWS:
                    raise ImportFormatError(f"Fichier trop long (plus de {MAX_ROWS} lignes)")
                reader = {"kraken_ledgers": _kraken_ledgers, "kraken_trades": _kraken_trades,
                          "binance": _binance, "coinbase": _coinbase}[fmt]  # fmt: skip
                out = reader(body, delim == ";")  # « ; » : un tableur français, sa virgule est décimale
                out.rows = len(body)
                return out
    raise ImportFormatError(
        "Format non reconnu : exporte le grand livre de Kraken (ledgers.csv) ou ses transactions (trades.csv), "
        "l'historique des transactions de Binance, ou le rapport d'historique des transactions de Coinbase"
    )


# ------------------------------------------------------------------ regroupement commun
@dataclass
class _Group:
    line: int
    at: datetime
    ref: str
    eur: Decimal = Decimal(0)  # jambe en euros, hors frais
    eur_fees: Decimal = Decimal(0)
    crypto: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    fiat: set[str] = field(default_factory=set)  # autres monnaies présentes


def _settle(platform: str, g: _Group, out: CryptoImport) -> None:
    """Une opération complète (ses jambes, ses frais) : acquisition, cession, ou écartée avec sa raison."""
    legs = {a: q for a, q in g.crypto.items() if q}
    bought = [a for a, q in legs.items() if q > 0]
    sold = [a for a, q in legs.items() if q < 0]
    if not legs:
        out.ignored.append(Ignored(g.line, FIAT_ONLY if g.eur or g.fiat else NO_EUR))
        return
    if not g.eur:
        if g.fiat:
            out.ignored.append(Ignored(g.line, other_fiat(sorted(g.fiat)[0])))
        elif bought and sold:
            out.ignored.append(Ignored(g.line, SWAP))
        else:
            out.ignored.append(Ignored(g.line, NO_EUR))
        return
    kind = "acquisition" if g.eur < 0 else "cession"
    side = bought if kind == "acquisition" else sold
    if bought and sold:  # une jambe en euros et deux cryptos : on ne devine pas laquelle a été payée
        out.ignored.append(Ignored(g.line, "Opération mêlant euros et plusieurs cryptos : à noter à la main"))
        return
    amount, fees = cents(abs(g.eur)), cents(g.eur_fees)
    day = paris_day(g.at)
    if amount <= 0:
        out.ignored.append(Ignored(g.line, "Montant en euros nul (poussière) : rien à noter"))
        return
    if amount > MAX_CENTS:
        out.ignored.append(Ignored(g.line, "Montant hors bornes : à vérifier à la main"))
        return
    if fees < 0 or fees > amount:
        out.ignored.append(Ignored(g.line, "Frais illisibles ou supérieurs au montant : à vérifier à la main"))
        return
    code = side[0] if len(side) == 1 else None
    qty = abs(legs[code]) if code else None
    out.movements.append(
        Movement(fingerprint(platform, g.ref), g.line, day, kind, amount, fees, code and _symbol(code), qty)
    )


def _ranked(items: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Sans identifiant dans l'export : la ligne normalisée et son rang d'apparition (deux achats identiques à
    la même seconde restent deux achats ; réimporter, ou un export qui chevauche, redonne les mêmes)."""
    seen: Counter[str] = Counter()
    out: dict[int, str] = {}
    for line, raw in items:
        out[line] = f"{raw}#{seen[raw]}"
        seen[raw] += 1
    return out


# ------------------------------------------------------------------ Kraken
_KRAKEN_TRADES = {"trade", "spend", "receive", "sale", "conversion"}


def _kraken_kind(kind: str) -> str | None:
    if kind in ("deposit", "withdrawal"):
        return TRANSFER if kind == "deposit" else SEND
    if kind in ("staking", "earn", "dividend", "reward", "invite bonus", "airdrop", "credit"):
        return REWARD
    if kind in ("margin", "rollover", "settled", "futures"):
        return DERIVATIVES
    if kind in ("transfer", "adjustment", "nfttrade"):
        return INTERNAL
    return None


def _kraken_ledgers(body: Sequence[tuple[int, dict[str, str]]], comma: bool = False) -> CryptoImport:
    num = partial(number, comma=comma)
    out = CryptoImport("kraken", "kraken_ledgers")
    groups: dict[str, _Group] = {}
    for line, row in body:
        kind = text(row.get("type"), 24).lower()
        if not text(row.get("txid")):
            out.ignored.append(Ignored(line, PENDING))
            continue
        if kind not in _KRAKEN_TRADES:
            out.ignored.append(
                Ignored(line, _kraken_kind(kind) or f"Type Kraken non reconnu (« {kind} ») : à vérifier")
            )
            continue
        at, amount, fee = when(row.get("time")), num(row.get("amount")), num(row.get("fee") or "0")
        ref = text(row.get("refid"), 64)
        if at is None or amount is None or fee is None or not ref:
            out.ignored.append(Ignored(line, "Ligne illisible (date, montant ou référence)"))
            continue
        g = groups.setdefault(ref, _Group(line, at, ref))
        code = asset(row.get("asset"), kraken=True)
        if code == "EUR":
            g.eur += amount
            g.eur_fees += fee
        elif code in FIAT:
            g.fiat.add(code)
        else:
            g.crypto[code] += amount - fee
    for g in sorted(groups.values(), key=lambda x: x.line):
        _settle("kraken", g, out)
    return out


def _split_pair(pair: str) -> tuple[str, str] | None:
    if "/" in pair:
        base, quote = pair.upper().split("/", 1)
        return asset(base, kraken=True), asset(quote, kraken=True)
    p = pair.upper().replace("-", "")
    for quote in sorted(_KRAKEN.keys() | FIAT | {"XBT", "ETH", "USDT", "USDC", "DAI"}, key=len, reverse=True):
        # ZEUR, XXBT : seulement derrière une base de quatre lettres (XXBTZEUR) — XTZEUR reste du Tezos en euros
        legacy = len(quote) == 4 and quote[0] in "XZ" and quote in _KRAKEN
        if p.endswith(quote) and len(p) - len(quote) >= (4 if legacy else 2):
            return asset(p[: -len(quote)], kraken=True), asset(quote, kraken=True)
    return None


def _kraken_trades(body: Sequence[tuple[int, dict[str, str]]], comma: bool = False) -> CryptoImport:
    num = partial(number, comma=comma)
    out = CryptoImport("kraken", "kraken_trades")
    for line, row in body:
        side = text(row.get("type"), 8).lower()
        pair = _split_pair(text(row.get("pair"), 24))
        at, cost, fee, vol = (
            when(row.get("time")),
            num(row.get("cost")),
            num(row.get("fee") or "0"),
            num(row.get("vol")),
        )
        ref = text(row.get("txid"), 64)
        if num(row.get("margin") or "0"):
            out.ignored.append(Ignored(line, DERIVATIVES))
            continue
        if (
            side not in ("buy", "sell")
            or pair is None
            or at is None
            or cost is None
            or fee is None
            or vol is None
            or not ref
        ):
            out.ignored.append(Ignored(line, "Ligne illisible (sens, paire, date, montant ou référence)"))
            continue
        base, quote = pair
        g = _Group(line, at, ref)  # même empreinte que le grand livre : txid de l'échange = refid de ses lignes
        sign = 1 if side == "buy" else -1
        g.crypto[base] += sign * vol
        if quote == "EUR":
            g.eur, g.eur_fees = -sign * cost, fee
        elif quote in FIAT:
            g.fiat.add(quote)
        else:
            g.crypto[quote] -= sign * cost
        _settle("kraken", g, out)
    return out


# ------------------------------------------------------------------ Binance
_BINANCE_TRADES = {
    "buy", "sell", "fee", "transaction buy", "transaction spend", "transaction fee", "transaction revenue",
    "transaction sold", "binance convert", "small assets exchange bnb", "large otc trading", "buy crypto",
    "auto-invest transaction", "binance card spending", "p2p trading", "convert",
}  # fmt: skip
_BINANCE_FEES = {"fee", "transaction fee"}
CRYPTO_FEE = "Frais payés en crypto (BNB…) : sans cours, Bank ne les chiffre pas — ajoute-les à la main au 2086"
_BINANCE_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("futures", "margin", "realized profit", "funding fee", "insurance", "liquidation", "options"), DERIVATIVES),
    (("withdraw",), SEND),
    (("deposit",), TRANSFER),
    (("interest", "reward", "distribution", "airdrop", "cashback", "commission", "bonus", "mining", "rebate"), REWARD),
    (("transfer", "subscription", "redemption", "purchase", "savings", "staking", "earn", "launchpool"), INTERNAL),
)


def _binance(body: Sequence[tuple[int, dict[str, str]]], comma: bool = False) -> CryptoImport:
    num = partial(number, comma=comma)
    out = CryptoImport("binance", "binance")
    trades: list[tuple[int, datetime, str, str, Decimal]] = []
    for line, row in body:  # User_ID et Remark ne sont jamais lus
        op = text(row.get("operation"), 60).lower()
        if op not in _BINANCE_TRADES:
            reason = next((r for words, r in _BINANCE_WORDS if any(w in op for w in words)), None)
            out.ignored.append(Ignored(line, reason or f"Opération Binance non reconnue (« {op} ») : à vérifier"))
            continue
        at, change, code = when(row.get("utc time")), num(row.get("change")), asset(row.get("coin"))
        if at is None or change is None or not code:
            out.ignored.append(Ignored(line, "Ligne illisible (date, monnaie ou montant)"))
            continue
        trades.append((line, at, op, code, change))
    # Pas d'identifiant d'échange chez Binance : les jambes d'un même ordre partagent leur seconde.
    by_time: dict[datetime, list[tuple[int, datetime, str, str, Decimal]]] = defaultdict(list)
    for t in trades:
        by_time[t[1]].append(t)
    raws = _ranked(
        (
            (
                legs[0][0],
                at.isoformat() + "|" + ";".join(sorted(f"{op}:{c}:{q.normalize()}" for _, _, op, c, q in legs)),
            )
            for at, legs in sorted(by_time.items())
        ),
    )
    for at, legs in sorted(by_time.items()):
        g = _Group(legs[0][0], at, raws[legs[0][0]])
        crypto_fees: list[tuple[int, str, Decimal]] = []
        for line, _, op, code, change in legs:
            if code == "EUR":
                if op in _BINANCE_FEES:
                    g.eur_fees -= change
                else:
                    g.eur += change
            elif code in FIAT:
                g.fiat.add(code)
            elif op in _BINANCE_FEES:
                crypto_fees.append((line, code, change))
            else:
                g.crypto[code] += change
        for line, code, change in crypto_fees:
            if code in g.crypto:  # frais pris sur la crypto achetée ou vendue : déduits de sa quantité
                g.crypto[code] += change
            elif g.eur:  # payés à part (BNB) : l'opération se lit sur ses jambes, les frais restent à noter
                out.ignored.append(Ignored(line, CRYPTO_FEE))
            else:
                g.crypto[code] += change
        _settle("binance", g, out)
    return out


# ------------------------------------------------------------------ Coinbase
_COINBASE_BUY = {"buy", "advanced trade buy", "recurring buy"}
_COINBASE_SELL = {"sell", "advanced trade sell"}
_COINBASE_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("convert",), SWAP),
    (("send", "withdrawal"), SEND),
    (("receive", "deposit"), TRANSFER),
    (("reward", "income", "earn", "learning"), REWARD),
    (("transfer", "staking", "unstaking", "wrap"), INTERNAL),
)


def _coinbase(body: Sequence[tuple[int, dict[str, str]]], comma: bool = False) -> CryptoImport:
    num = partial(number, comma=comma)
    out = CryptoImport("coinbase", "coinbase")
    cols = ("timestamp", "transaction type", "asset", "quantity transacted", "subtotal")
    raws = _ranked((line, "|".join(_CTRL.sub(" ", row.get(k) or "").strip()[:40] for k in cols)) for line, row in body)
    for line, row in body:  # les notes (adresses d'envoi) ne servent qu'à lire la paire, jamais gardées
        kind = text(row.get("transaction type"), 40).lower()
        if kind not in _COINBASE_BUY | _COINBASE_SELL:
            reason = next((r for words, r in _COINBASE_WORDS if any(w in kind for w in words)), None)
            out.ignored.append(Ignored(line, reason or f"Type Coinbase non reconnu (« {kind} ») : à vérifier"))
            continue
        code = asset(row.get("asset"))
        currency = asset(row.get("price currency") or row.get("spot price currency"))
        at, qty = when(row.get("timestamp")), num(row.get("quantity transacted"))
        # Les anciens rapports (années passées) disent « Fees » et « Total (inclusive of fees) ».
        subtotal, fees = num(row.get("subtotal")), num(row.get("fees and/or spread") or row.get("fees") or "0")
        total = num(row.get("total (inclusive of fees and/or spread)") or row.get("total (inclusive of fees)"))
        if at is None or qty is None or not code or fees is None or (subtotal is None and total is None):
            out.ignored.append(Ignored(line, "Ligne illisible (date, quantité ou montant)"))
            continue
        pair = _PAIR_NOTE.search(text(row.get("notes"), 300).upper())
        ref = text(row.get("id"), 64) or raws[line]
        g = _Group(line, at, ref)
        sign = 1 if kind in _COINBASE_BUY else -1
        g.crypto[code] += sign * abs(qty)
        if pair and pair.group(2) != "EUR":  # ETH-BTC, SOL-USDC : payé en crypto, le sous-total n'est qu'un équivalent
            quote = pair.group(2)
            if quote in FIAT:
                g.fiat.add(quote)
            else:
                g.crypto[quote] -= sign
        elif currency != "EUR":
            g.fiat.add(currency or "devise inconnue")
        else:
            fees = abs(fees)
            gross = abs(subtotal) if subtotal is not None else abs(total or Decimal(0)) + (-fees if sign > 0 else fees)
            g.eur, g.eur_fees = -sign * gross, fees
        _settle("coinbase", g, out)
    return out
