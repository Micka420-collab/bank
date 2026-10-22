"""Crypto : tout ce qui se calcule sans réseau ni base.

* Catalogue d'actifs et de réseaux, avec les seuls contrats de jetons que Bank lit (vérifiés à la source,
  émetteur compris) : un jeton reçu d'un inconnu n'apparaît jamais comme une valeur — c'est souvent un appât.
* Adresses vérifiées pour de vrai : somme de contrôle EIP-55 (Keccak-256), bech32/bech32m (BIP173/BIP350),
  base58check pour Bitcoin, 32 octets pour Solana. Une faute de frappe ne passe pas.
* Secrets repérés avant d'aller plus loin : phrase de récupération (listes BIP39 anglaise et française), clé
  privée (WIF, xprv, hex, clé Solana). Bank n'en a jamais besoin ; personne d'honnête n'en a besoin.
* Ressemblance d'adresses (empoisonnement : mêmes premiers et derniers caractères) et signaux d'arnaque.
* Fiscalité française des cessions (art. 150 VH bis du CGI) : plus-value = prix de cession − prix total
  d'acquisition × prix de cession ÷ valeur globale du portefeuille ; seuil annuel de 305 € de cessions.

Pur et déterministe : testé seul, y compris par propriétés."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import cache
from importlib import resources
from typing import Any


class CryptoError(ValueError):
    """Message lisible, montré tel quel."""


# ------------------------------------------------------------------ catalogue
@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    coingecko: str
    stable: str | None = None  # "EUR" ou "USD" pour un jeton adossé


ASSETS: dict[str, Asset] = {
    a.symbol: a
    for a in (
        Asset("BTC", "Bitcoin", "bitcoin"),
        Asset("ETH", "Ether", "ethereum"),
        Asset("SOL", "Solana", "solana"),
        Asset("USDC", "USD Coin", "usd-coin", "USD"),
        Asset("EURC", "Euro Coin", "euro-coin", "EUR"),
        Asset("USDT", "Tether", "tether", "USD"),
        Asset("WBTC", "Wrapped Bitcoin", "wrapped-bitcoin"),
        Asset("WETH", "Wrapped Ether", "weth"),
        Asset("BNB", "BNB", "binancecoin"),
        Asset("AVAX", "Avalanche", "avalanche-2"),
        Asset("POL", "Polygon", "polygon-ecosystem-token"),
        Asset("XRP", "XRP", "ripple"),
        Asset("ADA", "Cardano", "cardano"),
        Asset("DOT", "Polkadot", "polkadot"),
        Asset("LINK", "Chainlink", "chainlink"),
        Asset("LTC", "Litecoin", "litecoin"),
        Asset("DOGE", "Dogecoin", "dogecoin"),
        Asset("TRX", "TRON", "tron"),
        Asset("TON", "Toncoin", "the-open-network"),
        Asset("ATOM", "Cosmos", "cosmos"),
        Asset("XLM", "Stellar", "stellar"),
        Asset("ARB", "Arbitrum", "arbitrum"),
        Asset("OP", "Optimism", "optimism"),
    )
}
_SYMBOL = re.compile(r"[A-Z0-9]{2,12}")
_COINGECKO_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")


@dataclass(frozen=True)
class Token:
    symbol: str
    contract: str
    decimals: int


@dataclass(frozen=True)
class Chain:
    key: str
    label: str
    native: str
    family: str  # utxo | evm | solana
    decimals: int
    tokens: tuple[Token, ...] = ()


_USDC_ARB = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
_USDC_OP = "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85"
_USDC_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
# Contrats USDC et EURC : pages officielles de Circle (developers.circle.com, relevées le 21 octobre 2026) ;
# USDT, WBTC, WETH : contrats canoniques de leurs émetteurs.
CHAINS: dict[str, Chain] = {
    c.key: c
    for c in (
        Chain("bitcoin", "Bitcoin", "BTC", "utxo", 8),
        Chain(
            "ethereum",
            "Ethereum",
            "ETH",
            "evm",
            18,
            (
                Token("USDC", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
                Token("EURC", "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c", 6),
                Token("USDT", "0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
                Token("WBTC", "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
                Token("WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
            ),
        ),
        Chain(
            "base",
            "Base",
            "ETH",
            "evm",
            18,
            (
                Token("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
                Token("EURC", "0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42", 6),
                Token("WETH", "0x4200000000000000000000000000000000000006", 18),
            ),
        ),
        Chain("arbitrum", "Arbitrum", "ETH", "evm", 18, (Token("USDC", _USDC_ARB, 6),)),
        Chain("optimism", "Optimism", "ETH", "evm", 18, (Token("USDC", _USDC_OP, 6),)),
        Chain("polygon", "Polygon", "POL", "evm", 18, (Token("USDC", _USDC_POLYGON, 6),)),
        Chain(
            "avalanche",
            "Avalanche",
            "AVAX",
            "evm",
            18,
            (
                Token("USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", 6),
                Token("EURC", "0xC891EB4cbdEFf6e073e859e987815Ed1505c2ACD", 6),
            ),
        ),
        Chain("bsc", "BNB Chain", "BNB", "evm", 18),
        Chain(
            "solana",
            "Solana",
            "SOL",
            "solana",
            9,
            (
                Token("USDC", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
                Token("EURC", "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr", 6),
                Token("USDT", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
            ),
        ),
    )
}
# Nœuds publics par défaut (remplaçables par le tien : c'est le plus discret). Relevés le 21 octobre 2026.
DEFAULT_ENDPOINTS: dict[str, str] = {
    "bitcoin": "https://mempool.space/api",
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "base": "https://base-rpc.publicnode.com",
    "arbitrum": "https://arbitrum-one-rpc.publicnode.com",
    "optimism": "https://optimism-rpc.publicnode.com",
    "polygon": "https://polygon-bor-rpc.publicnode.com",
    "avalanche": "https://avalanche-c-chain-rpc.publicnode.com",
    "bsc": "https://bsc-rpc.publicnode.com",
    "solana": "https://solana-rpc.publicnode.com",
}
PRICES_URL = "https://api.coingecko.com/api/v3/simple/price"
SENTIMENT_URL = "https://api.alternative.me/fng/?limit=1"


def symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not _SYMBOL.fullmatch(s):
        raise CryptoError("Symbole : 2 à 12 lettres ou chiffres (BTC, ETH, USDC…)")
    return s


def coingecko_id(asset: str, custom: str | None = None) -> str | None:
    if custom:
        c = custom.strip().lower()
        if not _COINGECKO_ID.fullmatch(c):
            raise CryptoError("Identifiant CoinGecko : lettres minuscules, chiffres et tirets")
        return c
    known = ASSETS.get(asset)
    return known.coingecko if known else None


def quantity(raw: Any) -> Decimal:
    try:
        q = Decimal(str(raw).strip().replace(",", ".").replace(" ", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        raise CryptoError("Quantité illisible") from None
    if not q.is_finite() or q < 0:
        raise CryptoError("Quantité : un nombre positif")
    if q > Decimal("1e15") or -q.as_tuple().exponent > 18:  # type: ignore[operator]
        raise CryptoError("Quantité hors de proportion (18 décimales au plus)")
    return q.normalize() if q else Decimal(0)


def qty_text(q: Decimal) -> str:
    """Écriture décimale exacte, sans notation scientifique (« 100 », pas « 1E+2 »)."""
    text = format(q.normalize(), "f") if q else "0"
    return text.rstrip("0").rstrip(".") if "." in text else text


def value_cents(qty: Decimal, price_eur: Decimal) -> int:
    return int((qty * price_eur * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


# ------------------------------------------------------------------ Keccak-256 (EIP-55)
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
_ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61), (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))
_MASK = (1 << 64) - 1


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _MASK if n else v


def _keccak_f(a: list[list[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 d'origine (celui d'Ethereum, pas SHA3-256 : le remplissage diffère)."""
    rate = 136
    room = rate - len(data) % rate
    # Remplissage pad10*1 de Keccak : 0x01 … 0x80, fondus en 0x81 quand il ne reste qu'un octet.
    padded = bytearray(data) + (b"\x81" if room == 1 else b"\x01" + b"\x00" * (room - 2) + b"\x80")
    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        block = padded[off : off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            state[x][y] ^= int.from_bytes(block[i * 8 : i * 8 + 8], "little")
        _keccak_f(state)
    return b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))


def eip55(address: str) -> str:
    """Forme à somme de contrôle d'une adresse 0x… (EIP-55)."""
    hexpart = address[2:].lower()
    digest = keccak256(hexpart.encode("ascii")).hex()
    return "0x" + "".join(
        ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch for i, ch in enumerate(hexpart)
    )


# ------------------------------------------------------------------ bech32 et base58
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST, _BECH32M_CONST = 1, 0x2BC830A3
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _polymod(values: Iterable[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _convertbits(data: Sequence[int], frombits: int, tobits: int) -> list[int] | None:
    acc = bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    for v in data:
        acc = (acc << frombits) | v
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return out


def segwit_ok(address: str, hrp: str = "bc") -> bool:
    if address.lower() != address and address.upper() != address:
        return False
    a = address.lower()
    pos = a.rfind("1")
    if a[:pos] != hrp or pos + 7 > len(a) or len(a) > 90:
        return False
    try:
        data = [_BECH32.index(c) for c in a[pos + 1 :]]
    except ValueError:
        return False
    const = _polymod([ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp] + data)
    if not data or data[0] > 16:
        return False
    version = data[0]
    if const != (_BECH32_CONST if version == 0 else _BECH32M_CONST):
        return False
    program = _convertbits(data[1:-6], 5, 8)
    if program is None or not 2 <= len(program) <= 40:
        return False
    return not (version == 0 and len(program) not in (20, 32))


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        i = _B58.find(ch)
        if i < 0:
            raise CryptoError("Caractère hors base58")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + raw


def b58check(text: str) -> bytes | None:
    try:
        raw = b58decode(text)
    except CryptoError:
        return None
    if len(raw) < 5:
        return None
    payload, check = raw[:-4], raw[-4:]
    return payload if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == check else None


# ------------------------------------------------------------------ adresses
_HEX40 = re.compile(r"0x[0-9a-fA-F]{40}")
_HEX64 = re.compile(r"(?:0x)?[0-9a-fA-F]{64}")


def validate_address(chain: str, raw: str) -> str:
    """L'adresse normalisée, ou une erreur claire. Refuse en premier tout ce qui ressemble à un secret."""
    address = (raw or "").strip()
    if chain not in CHAINS:
        raise CryptoError("Réseau inconnu : " + ", ".join(CHAINS))
    if secret_kind(address):
        raise CryptoError(
            "Ceci n'est pas une adresse : c'est un SECRET (phrase de récupération ou clé privée). Ne le colle jamais "
            "nulle part — Bank n'en a pas besoin. Si quelqu'un te l'a demandé, c'est une arnaque : déplace tes fonds "
            "vers un nouveau portefeuille."
        )
    family = CHAINS[chain].family
    if family == "evm":
        if not _HEX40.fullmatch(address):
            raise CryptoError("Adresse attendue : 0x suivi de 40 caractères hexadécimaux")
        body = address[2:]
        if body != body.lower() and body != body.upper() and eip55(address) != address:
            raise CryptoError("Somme de contrôle invalide (EIP-55) : une lettre a été mal recopiée")
        return eip55(address)
    if family == "utxo":
        if address.lower().startswith("bc1"):
            if not segwit_ok(address):
                raise CryptoError("Adresse bc1… invalide : un caractère a été mal recopié")
            return address.lower()
        payload = b58check(address)
        if payload is None or len(payload) != 21 or payload[0] not in (0x00, 0x05):
            raise CryptoError("Adresse Bitcoin invalide (somme de contrôle) : vérifie-la caractère par caractère")
        return address
    try:
        raw_key = b58decode(address)
    except CryptoError:
        raise CryptoError("Adresse Solana invalide (base58)") from None
    if len(raw_key) != 32 or not 32 <= len(address) <= 44:
        raise CryptoError("Adresse Solana invalide : 32 octets attendus")
    return address


def mask(address: str) -> str:
    return address if len(address) <= 14 else f"{address[:6]}…{address[-4:]}"


def lookalike(address: str, known: Iterable[tuple[str, str]]) -> str | None:
    """Le nom d'une adresse connue à laquelle celle-ci RESSEMBLE sans être la même (empoisonnement d'adresse :
    l'escroc fabrique une adresse aux mêmes premiers et derniers caractères et l'envoie dans ton historique)."""
    a = _core(address)
    for label, other in known:
        b = _core(other)
        if a != b and len(a) >= 8 and len(b) >= 8 and a[:4] == b[:4] and a[-4:] == b[-4:]:
            return label
    return None


def _core(address: str) -> str:
    """Ce qui distingue vraiment une adresse : sans « 0x » ni le préfixe commun « bc1q » / « bc1p »."""
    a = address.strip().lower()
    if a.startswith("0x"):
        return a[2:]
    if a.startswith("bc1") and len(a) > 4:
        return a[4:]
    return a


# ------------------------------------------------------------------ secrets
def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word.casefold()) if not unicodedata.combining(c))


@cache
def bip39_words() -> frozenset[str]:
    words: set[str] = set()
    for lang in ("english", "french"):
        text = resources.files("app.data.bip39").joinpath(f"{lang}.txt").read_text(encoding="utf-8")
        words.update(_fold(w) for w in text.split())
    return frozenset(words)


_TOKENS = re.compile(r"[^\W\d_]+")
_KEY_CONTEXT = re.compile(r"cl[eé]\s+priv[ée]e|private\s*key|priv\s*key|privkey|secret\s*key|cl[eé]\s+secr[eè]te", re.I)
_XPRV = re.compile(r"\b[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,112}\b|\b[YZ]prv[1-9A-HJ-NP-Za-km-z]{100,112}\b")
_WIF = re.compile(r"\b[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}\b")
_B58_LONG = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{86,90}\b")
_INT_ARRAY = re.compile(r"\[\s*(?:\d{1,3}\s*,\s*){63}\d{1,3}\s*\]")


def is_mnemonic(text: str) -> bool:
    """Douze mots (ou plus) d'une liste BIP39 à la suite, une coquille tolérée : une phrase de récupération."""
    words = bip39_words()
    hits = [_fold(t) in words for t in _TOKENS.findall(text)]
    return any(sum(hits[i : i + 12]) >= 11 for i in range(len(hits) - 11))


def secret_kind(text: str) -> str | None:
    """« une phrase de récupération » ou « une clé privée » si le texte en contient une, sinon None."""
    if not text:
        return None
    if is_mnemonic(text):
        return "une phrase de récupération (seed)"
    if _XPRV.search(text):
        return "une clé privée étendue (xprv)"
    for m in _WIF.finditer(text):
        payload = b58check(m.group(0))
        if payload and payload[0] in (0x80, 0xEF) and len(payload) in (33, 34):
            return "une clé privée (WIF)"
    for m in _B58_LONG.finditer(text):
        try:
            if len(b58decode(m.group(0))) == 64:
                return "une clé privée Solana"
        except CryptoError:
            continue
    if _INT_ARRAY.search(text):
        return "une clé privée (tableau d'octets)"
    if _KEY_CONTEXT.search(text) and _HEX64.search(text):
        return "une clé privée"
    stripped = text.strip()
    if _HEX64.fullmatch(stripped) and not stripped.startswith("0x"):
        return "peut-être une clé privée (64 caractères hexadécimaux)"
    return None


# ------------------------------------------------------------------ arnaques
@dataclass(frozen=True)
class Signal:
    key: str
    label: str
    weight: int
    pattern: re.Pattern[str]


_SIGNALS = (
    Signal("seed", "On te demande ta phrase de récupération ou ta clé privée", 5, re.compile(
        r"phrase\s+(?:de\s+)?(?:r[ée]cup[ée]ration|secr[eè]te|mn[ée]monique)|seed\s*phrase|recovery\s*phrase|"
        r"\b(?:12|24)\s*mots\b|cl[eé]\s+priv[ée]e|private\s*key|mot\s+de\s+passe\s+du\s+portefeuille", re.I)),
    Signal("guarantee", "Un rendement garanti ou sans risque : ça n'existe pas", 5, re.compile(
        r"rendements?\s+garantis?|gains?\s+garantis?|sans\s+(?:aucun\s+)?risque|\b\d{1,3}\s?%\s+par\s+"
        r"(?:jour|semaine|mois)|guaranteed\s+(?:returns?|profits?)|risk[-\s]?free", re.I)),
    Signal("miracle", "Un gain miraculeux promis", 3, re.compile(
        r"doubler?\s+(?:ton|votre|vos|tes)\s+(?:argent|mise|capital|investissement)|\bx\s?(?:10|20|50|100)\b|"
        r"devenir\s+riche|get\s+rich", re.I)),
    Signal("urgency", "De l'urgence pour t'empêcher de réfléchir", 2, re.compile(
        r"derni[eè]re\s+chance|urgent|imm[ée]diatement|dans\s+les?\s+\d+\s*(?:h|heures?|minutes?)|avant\s+minuit|"
        r"offre\s+limit[ée]e|act\s+now|expire\s+(?:aujourd|bient)", re.I)),
    Signal("connect", "On te pousse à connecter ou « valider » ton portefeuille sur un site", 4, re.compile(
        r"connect(?:e[rz]?|ez)\s+(?:ton|votre)\s+(?:portefeuille|wallet)|connect\s+(?:your\s+)?wallet|"
        r"valide[rz]?\s+(?:ton|votre)\s+(?:portefeuille|wallet)|synchronise[rz]?\s+(?:ton|votre)\s+wallet|"
        r"approve|signer\s+(?:la|une)\s+(?:transaction|autorisation)|wallet\s*connect", re.I)),
    Signal("airdrop", "Un « airdrop » ou des jetons gratuits à réclamer", 3, re.compile(
        r"airdrop|r[ée]clame[rz]?\s+(?:tes|vos)\s+(?:jetons|tokens|r[ée]compenses)|claim\s+(?:your\s+)?"
        r"(?:tokens|reward|airdrop)|jetons?\s+gratuits?|free\s+tokens", re.I)),
    Signal("support", "Un faux support qui te contacte en privé", 3, re.compile(
        r"(?:support|service\s+client|assistance|conseiller)\s+(?:technique\s+)?(?:binance|coinbase|ledger|kraken|"
        r"metamask|trust\s*wallet|bitpanda|revolut)|(?:ledger|metamask)\s+(?:support|s[ée]curit[ée])|"
        r"contacte[rz]?[- ]nous\s+sur\s+(?:whatsapp|telegram)", re.I)),
    Signal("authority", "Une « autorité » qui réclame un paiement en crypto", 5, re.compile(
        r"(?:police|gendarmerie|imp[oô]ts|douane|tribunal|amende|huissier|AMF|banque\s+de\s+france)[^.\n]{0,80}"
        r"(?:bitcoin|crypto|usdt|btc)|(?:bitcoin|crypto|usdt|btc)[^.\n]{0,80}(?:amende|police|imp[oô]ts|douane)",
        re.I)),
    Signal("recovery", "Une offre de « récupérer » des fonds perdus (arnaque à la récupération)", 4, re.compile(
        r"r[ée]cup[ée]r(?:er|ation)\s+(?:de\s+)?(?:vos|tes)\s+(?:fonds|cryptos?|bitcoins?)|fund\s+recovery|"
        r"recover\s+(?:your\s+)?(?:funds|crypto)", re.I)),
    Signal("advance_fee", "Des frais à payer d'abord pour débloquer un gain", 4, re.compile(
        r"(?:frais|taxe|commission)\s+(?:de\s+)?(?:d[ée]blocage|retrait|lib[ée]ration|activation)|"
        r"payer?\s+(?:des\s+)?frais\s+pour\s+(?:retirer|d[ée]bloquer)|unlock\s+(?:fee|your\s+withdrawal)", re.I)),
    Signal("romance", "Un inconnu sympathique qui te guide vers « sa » plateforme", 3, re.compile(
        r"(?:mon|ma)\s+(?:oncle|tante|mentor|coach)\s+(?:trader|investit)|je\s+(?:vais\s+)?t['’]apprendre\s+[àa]\s+"
        r"trader|plateforme\s+(?:que\s+)?(?:j['’]utilise|de\s+mon)|pig\s*butchering", re.I)),
    Signal("short_link", "Un lien raccourci ou un faux domaine", 2, re.compile(
        r"\b(?:bit\.ly|tinyurl\.com|t\.co|is\.gd|cutt\.ly|rb\.gy)/|https?://[^\s/]*(?:-login|-verify|-secure|"
        r"-support|-airdrop|-claim)[^\s/]*", re.I)),
)  # fmt: skip

RESOURCES: tuple[dict[str, str], ...] = (
    {
        "label": "Listes blanches de l'AMF : seuls les prestataires crypto autorisés (PSCA) peuvent te servir en "
        "France depuis le 1er juillet 2026",
        "url": "https://www.amf-france.org/fr/espace-epargnants/proteger-son-epargne/listes-blanches",
    },
    {
        "label": "Mises en garde et listes noires de l'AMF, réunies en un tableau",
        "url": "https://www.amf-france.org/fr/actualites-publications/actualites/protection-des-epargnants-lamf-facilite-"
        "la-consultation-des-mises-en-garde-et-listes-noires-par-les",
    },
    {"label": "Info Escroqueries : 0 805 805 817 (gratuit, du lundi au vendredi, 9 h – 18 h 30)", "url": ""},
    {"label": "Signaler un site ou un message : Pharos", "url": "https://www.internet-signalement.gouv.fr"},
    {"label": "Aide aux victimes de cybermalveillance", "url": "https://www.cybermalveillance.gouv.fr"},
    {"label": "Porter plainte en ligne pour une escroquerie : Thésée", "url": "https://www.service-public.gouv.fr/particuliers/vosdroits/N31138"},
    {"label": "Urgence cyber, conseil ou plainte : 17Cyber", "url": "https://17cyber.gouv.fr"},
)  # fmt: skip


def scam_check(text: str, *, known: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """Les signaux d'arnaque d'un message, d'une offre ou d'une adresse. Jamais une certitude : un repère."""
    text = (text or "")[:5_000]
    found: list[dict[str, str]] = []
    score = 0
    kind = secret_kind(text)
    if kind:
        found.append({"key": "secret", "label": f"Le texte contient {kind} : ne l'envoie à personne"})
        score += 5
    for s in _SIGNALS:
        if s.pattern.search(text):
            found.append({"key": s.key, "label": s.label})
            score += s.weight
    known_list = list(known)
    for m in re.finditer(r"0x[0-9a-fA-F]{40}|\bbc1[02-9ac-hj-np-z]{11,71}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text):
        twin = lookalike(m.group(0), known_list)
        if twin:
            found.append({"key": "lookalike", "label": f"L'adresse {mask(m.group(0))} imite « {twin} » sans être elle"})
            score += 5
    if score >= 5:
        verdict, level = "Très probablement une arnaque : ne réponds pas, ne clique pas, n'envoie rien.", "danger"
    elif score >= 2:
        verdict, level = "Prudence : plusieurs signaux d'arnaque classiques.", "warning"
    else:
        verdict, level = "Aucun signal classique repéré — ce n'est pas une garantie : vérifie la source.", "ok"
    return {
        "level": level,
        "verdict": verdict,
        "signals": found,
        "advice": [
            "Personne d'honnête ne te demandera ta phrase de récupération ni ta clé privée.",
            "Un rendement garanti n'existe pas en crypto.",
            "Vérifie qu'une plateforme est autorisée sur la liste blanche de l'AMF avant d'y envoyer un euro.",
            "Avant un envoi, compare l'adresse EN ENTIER, pas seulement le début et la fin.",
        ],
        "resources": list(RESOURCES),
    }


# ------------------------------------------------------------------ fiscalité (art. 150 VH bis du CGI)
SMALL_CESSIONS_CENTS = 30_500  # total annuel des prix de cession sous lequel les plus-values sont exonérées
TAX_REFERENCES: dict[int, dict[str, Any]] = {
    2026: {
        "ir_percent": "12.8",
        "social_percent": "18.6",
        "total_percent": "31.4",
        "status": "référence",
        "note": "Prélèvements sociaux à 18,6 % (CSG portée à 10,6 % par l'article 12 de la loi n° 2025-1403 du "
        "30 décembre 2025, LFSS 2026) : flat tax de 31,4 % sur les cessions de 2026, sauf option pour le barème.",
        "checked": "2026-10-21",
    },
    2025: {
        "ir_percent": "12.8",
        "social_percent": None,
        "total_percent": None,
        "status": "à vérifier",
        "note": "Pour les cessions de 2025, les sources divergent sur l'application de la hausse de CSG (17,2 % ou "
        "18,6 % de prélèvements sociaux) : reprends le taux indiqué par ta déclaration en ligne.",
        "checked": "2026-10-21",
    },
}
TAX_SOURCES: tuple[dict[str, str], ...] = (
    {"label": "Comment déclarer les plus ou moins-values sur cessions d'actifs numériques (impots.gouv.fr)",
     "url": "https://www.impots.gouv.fr/particulier/questions/comment-declarer-les-plus-ou-moins-values-sur-cessions-dactifs-numeriques"},
    {"label": "Formulaire 2086, campagne 2026 (impots.gouv.fr)",
     "url": "https://www.impots.gouv.fr/sites/default/files/formulaires/2086/2026/2086_5515.pdf"},
    {"label": "Notice 3916 / 3916-bis, comptes d'actifs numériques à l'étranger (impots.gouv.fr, mars 2026)",
     "url": "https://static.impots.gouv.fr/tdir/static/2026-03-30T16:36:08Z/aide/notices/notices2020/3916/notice3916.pdf"},
)  # fmt: skip


@dataclass(frozen=True)
class TaxEvent:
    id: int
    day: date
    kind: str  # acquisition | cession
    amount_cents: int  # prix payé (acquisition) ou prix de cession C
    fees_cents: int = 0
    portfolio_value_cents: int | None = None  # V : valeur globale du portefeuille juste avant la cession


@dataclass
class CessionResult:
    id: int
    day: date
    price_cents: int
    fees_cents: int
    portfolio_value_cents: int
    acquisition_share_cents: int  # A × C / V
    gain_cents: int  # (C − frais) − A × C / V


@dataclass
class TaxYear:
    year: int
    cessions: list[CessionResult] = field(default_factory=list)
    total_price_cents: int = 0
    net_gain_cents: int = 0
    exempt: bool = True
    taxable_cents: int = 0
    rate: dict[str, Any] | None = None
    estimated_tax_cents: int | None = None


def tax_years(events: Sequence[TaxEvent]) -> tuple[dict[int, TaxYear], int]:
    """Les années fiscales et le prix total d'acquisition encore « en portefeuille » (A net).

    Ordre chronologique ; à date égale, les acquisitions d'abord. Une cession sans valeur globale (V) valable,
    ou avec V inférieure au prix de cession, est une erreur de saisie : on la signale au lieu d'inventer."""
    years: dict[int, TaxYear] = {}
    acquired = Decimal(0)
    for e in sorted(events, key=lambda ev: (ev.day, ev.kind != "acquisition", ev.id)):
        if e.kind == "acquisition":
            acquired += e.amount_cents + e.fees_cents
            continue
        v = e.portfolio_value_cents
        if v is None or v <= 0 or v < e.amount_cents:
            raise CryptoError(
                f"Cession du {e.day.isoformat()} : indique la valeur globale de ton portefeuille juste avant la "
                "cession (au moins le prix de cession)"
            )
        share = (acquired * e.amount_cents / v).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
        gain = e.amount_cents - e.fees_cents - int(share)
        acquired -= share
        ty = years.setdefault(e.day.year, TaxYear(e.day.year))
        ty.cessions.append(CessionResult(e.id, e.day, e.amount_cents, e.fees_cents, v, int(share), gain))
    for ty in years.values():
        ty.total_price_cents = sum(c.price_cents for c in ty.cessions)
        ty.net_gain_cents = sum(c.gain_cents for c in ty.cessions)
        ty.exempt = ty.total_price_cents <= SMALL_CESSIONS_CENTS
        ty.taxable_cents = 0 if ty.exempt else max(0, ty.net_gain_cents)
        ty.rate = TAX_REFERENCES.get(ty.year)
        total = ty.rate.get("total_percent") if ty.rate else None
        ty.estimated_tax_cents = (
            int((Decimal(ty.taxable_cents) * Decimal(total) / 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
            if total
            else None
        )
    return years, int(acquired)


def tax_reminders(year: int, *, foreign_accounts: int, has_cessions: bool) -> list[str]:
    out = []
    if has_cessions:
        out.append(
            f"Cessions en {year} : formulaire 2086 joint à ta déclaration de {year + 1}, même sous 305 € de "
            "cessions, puis report en case 3AN (plus-value) ou 3BN (moins-value) de la 2042 C."
        )
    if foreign_accounts:
        s = "s" if foreign_accounts > 1 else ""
        out.append(
            f"{foreign_accounts} compte{s} sur une plateforme étrangère : un formulaire 3916-bis par compte, même "
            "inactif ou clos dans l'année (oubli : 750 € par compte, 1 500 € si plus de 50 000 € à l'étranger)."
        )
    out.append(
        "Échanger une crypto contre une autre n'est pas imposable ; la vendre contre des euros ou payer avec, si. "
        "Si tu trades comme un professionnel, d'autres règles peuvent s'appliquer."
    )
    return out


def sentiment(payload: Any) -> dict[str, Any] | None:
    """Indice « peur et avidité » (alternative.me) : un thermomètre de l'humeur du marché, pas un signal."""
    try:
        item = payload["data"][0]
        value = int(item["value"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not 0 <= value <= 100:
        return None
    label = (
        "Peur extrême" if value < 25 else "Peur" if value < 45 else "Neutre" if value < 56 else "Avidité" if value < 76
        else "Avidité extrême"
    )  # fmt: skip
    return {"value": value, "label": label}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
