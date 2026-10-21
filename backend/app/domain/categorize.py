"""Catégorisation en couches, de la plus fiable à la moins fiable :

1. choix manuel sur la transaction (jamais écrasé)
2. règles explicites de l'utilisateur
3. règles apprises de tes corrections (marchand -> catégorie)
4. classifieur bayésien naïf entraîné sur TON historique (s'il est assez sûr de lui)
5. dictionnaire intégré de marchands/organismes français
6. « À catégoriser »
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from app.domain.normalize import searchable


@dataclass(frozen=True)
class CategoryDef:
    slug: str
    name: str
    kind: str  # income | fixed | variable | savings | transfer | uncategorized
    color: str
    order: int


BUILTIN_CATEGORIES: tuple[CategoryDef, ...] = (
    CategoryDef("salaire", "Salaire", "income", "#16a34a", 10),
    CategoryDef("aides", "Aides & prestations", "income", "#059669", 11),
    CategoryDef("remboursements", "Remboursements", "income", "#0d9488", 12),
    CategoryDef("autres_revenus", "Autres revenus", "income", "#65a30d", 13),
    CategoryDef("logement", "Logement", "fixed", "#7c3aed", 20),
    CategoryDef("energie", "Énergie & eau", "fixed", "#9333ea", 21),
    CategoryDef("telecom", "Box & mobile", "fixed", "#a855f7", 22),
    CategoryDef("assurances", "Assurances", "fixed", "#6d28d9", 23),
    CategoryDef("abonnements", "Abonnements", "fixed", "#c026d3", 24),
    CategoryDef("credits", "Crédits", "fixed", "#be185d", 25),
    CategoryDef("impots", "Impôts & taxes", "fixed", "#9f1239", 26),
    CategoryDef("courses", "Courses", "variable", "#ea580c", 30),
    CategoryDef("restaurants", "Restaurants & livraison", "variable", "#f97316", 31),
    CategoryDef("transport", "Transport & carburant", "variable", "#0284c7", 32),
    CategoryDef("sante", "Santé", "variable", "#e11d48", 33),
    CategoryDef("shopping", "Shopping", "variable", "#db2777", 34),
    CategoryDef("maison", "Maison & bricolage", "variable", "#a16207", 35),
    CategoryDef("loisirs", "Loisirs & sorties", "variable", "#2563eb", 36),
    CategoryDef("enfants", "Enfants", "variable", "#f59e0b", 37),
    CategoryDef("frais_bancaires", "Frais bancaires", "variable", "#475569", 38),
    CategoryDef("retraits", "Retraits d'espèces", "variable", "#64748b", 39),
    CategoryDef("cadeaux_dons", "Cadeaux & dons", "variable", "#d946ef", 40),
    CategoryDef("divers", "Divers", "variable", "#94a3b8", 41),
    CategoryDef("epargne", "Épargne", "savings", "#0891b2", 50),
    CategoryDef("virements_internes", "Virements internes", "transfer", "#71717a", 60),
    CategoryDef("a_categoriser", "À catégoriser", "uncategorized", "#a1a1aa", 99),
)
CATEGORY_KIND = {c.slug: c.kind for c in BUILTIN_CATEGORIES}


@dataclass(frozen=True)
class KeywordRule:
    category: str
    keywords: tuple[str, ...]
    direction: str = "any"  # in | out | any


# L'ordre compte : la première règle qui matche gagne (plus spécifique d'abord).
BUILTIN_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(
        "epargne",
        (
            "LIVRET A",
            "LDDS",
            "LIVRET DEVELOPPEMENT DURABLE",
            "LEP",
            "LIVRET EPARGNE",
            "VERSEMENT PEA",
            "ASSURANCE VIE",
            "ASSURANCE-VIE",
            "PER INDIVIDUEL",
            "EPARGNE",
        ),
    ),
    KeywordRule("salaire", ("SALAIRE", "PAIE", "PAYE", "REMUNERATION", "TRAITEMENT DGFIP", "PAYROLL"), "in"),
    KeywordRule(
        "aides",
        (
            "CAF",
            "C.A.F",
            "CAISSE D ALLOCATIONS",
            "CAISSE D'ALLOCATIONS",
            "ALLOCATIONS FAMILIALES",
            "FRANCE TRAVAIL",
            "POLE EMPLOI",
            "MSA",
            "PRIME ACTIVITE",
            "PRIME D ACTIVITE",
            "CARSAT",
            "MDPH",
            "CROUS",
            "CONSEIL DEPARTEMENTAL",
            "ASP AGENCE SERVICES PAIEMENT",
        ),
        "in",
    ),
    KeywordRule(
        "remboursements",
        (
            "CPAM",
            "AMELI",
            "ASSURANCE MALADIE",
            "CAISSE PRIMAIRE",
            "MUTUELLE",
            "DGFIP",
            "FINANCES PUBLIQUES",
            "REMBOURSEMENT",
            "REMBT",
            "AVOIR",
        ),
        "in",
    ),
    KeywordRule(
        "credits",
        (
            "CETELEM",
            "COFIDIS",
            "SOFINCO",
            "FLOA",
            "ONEY",
            "FRANFINANCE",
            "YOUNITED",
            "CA CONSUMER",
            "ECHEANCE PRET",
            "ECH PRET",
            "REMBOURSEMENT PRET",
        ),
        "out",
    ),
    KeywordRule(
        "impots",
        (
            "DGFIP",
            "IMPOT",
            "TRESOR PUBLIC",
            "FINANCES PUBLIQUES",
            "TAXE HABITATION",
            "TAXE FONCIERE",
            "AMENDE",
            "ANTAI",
        ),
        "out",
    ),
    KeywordRule(
        "logement",
        (
            "LOYER",
            "FONCIA",
            "NEXITY",
            "CITYA",
            "ORPI",
            "SQUARE HABITAT",
            "OPH",
            "HLM",
            "OFFICE PUBLIC",
            "CDC HABITAT",
            "ACTION LOGEMENT",
            "IMMOBILIERE",
            "SYNDIC",
            "LOGEMENT FRANCAIS",
            "VALLOIRE HABITAT",
            "LES RESIDENCES",
        ),
        "out",
    ),
    KeywordRule(
        "energie",
        (
            "EDF",
            "ENGIE",
            "TOTALENERGIES ELEC",
            "TOTAL DIRECT ENERGIE",
            "TOTALENERGIES GAZ",
            "VEOLIA",
            "SUEZ",
            "SAUR",
            "EAU DE",
            "ENI GAS",
            "EKWATEUR",
            "OHM ENERGIE",
            "MINT ENERGIE",
            "PLANETE OUI",
            "ENERCOOP",
            "OCTOPUS ENERGY",
            "GAZ DE BORDEAUX",
        ),
        "out",
    ),
    KeywordRule(
        "telecom",
        (
            "FREE MOBILE",
            "FREE TELECOM",
            "FREEBOX",
            "ORANGE",
            "SFR",
            "BOUYGUES TELECOM",
            "BOUYGTEL",
            "RED BY SFR",
            "SOSH",
            "B&YOU",
            "PRIXTEL",
            "LA POSTE MOBILE",
            "NRJ MOBILE",
            "CORIOLIS",
            "SYMA",
        ),
        "out",
    ),
    KeywordRule(
        "abonnements",
        (
            "NETFLIX",
            "SPOTIFY",
            "DISNEY PLUS",
            "DISNEYPLUS",
            "CANAL+",
            "CANAL PLUS",
            "AMAZON PRIME",
            "PRIME VIDEO",
            "APPLE.COM/BILL",
            "APPLE COM BILL",
            "GOOGLE PLAY",
            "YOUTUBE",
            "DEEZER",
            "OPENAI",
            "CHATGPT",
            "ANTHROPIC",
            "CLAUDE.AI",
            "MICROSOFT",
            "ADOBE",
            "BASIC FIT",
            "BASIC-FIT",
            "FITNESS PARK",
            "PLAYSTATION",
            "XBOX",
            "NINTENDO ESHOP",
            "DROPBOX",
            "ICLOUD",
            "PATREON",
            "TWITCH",
            "DAZN",
            "MOLOTOV",
            "PARAMOUNT",
            "CRUNCHYROLL",
            "GITHUB",
            "OVH",
            "SCALEWAY",
            "HETZNER",
        ),
        "out",
    ),
    KeywordRule(
        "assurances",
        (
            "MAIF",
            "MACIF",
            "MATMUT",
            "AXA",
            "ALLIANZ",
            "GROUPAMA",
            "GMF",
            "MAAF",
            "MMA",
            "DIRECT ASSURANCE",
            "PACIFICA",
            "GENERALI",
            "HARMONIE MUTUELLE",
            "MGEN",
            "MALAKOFF",
            "AG2R",
            "APRIL",
            "LUKO",
            "ALAN",
            "ASSURANCE",
        ),
        "out",
    ),
    KeywordRule(
        "courses",
        (
            "CARREFOUR",
            "LECLERC",
            "AUCHAN",
            "LIDL",
            "ALDI",
            "INTERMARCHE",
            "SUPER U",
            "HYPER U",
            "SYSTEME U",
            "U EXPRESS",
            "MARCHE U",
            "CASINO",
            "MONOPRIX",
            "FRANPRIX",
            "NETTO",
            "GRAND FRAIS",
            "PICARD",
            "BIOCOOP",
            "CORA",
            "SPAR",
            "LEADER PRICE",
            "NATURALIA",
            "LA VIE CLAIRE",
            "BOULANGERIE",
            "BOUCHERIE",
            "PRIMEUR",
            "MARKET",
            "PROXI",
            "VIVAL",
            "COLRUYT",
            "THIRIET",
        ),
        "out",
    ),
    KeywordRule(
        "restaurants",
        (
            "MCDONALD",
            "MCDONALDS",
            "MC DONALD",
            "MC DONALDS",
            "MCDO",
            "BURGER KING",
            "KFC",
            "QUICK",
            "DOMINOS",
            "UBER EATS",
            "UBER *EATS",
            "UBEREATS",
            "DELIVEROO",
            "JUST EAT",
            "STARBUCKS",
            "RESTAURANT",
            "BRASSERIE",
            "PIZZA",
            "PIZZERIA",
            "SUSHI",
            "O TACOS",
            "FIVE GUYS",
            "BISTRO",
            "CAFE",
            "COLUMBUS",
            "BUFFALO GRILL",
            "COURTEPAILLE",
            "HIPPOPOTAMUS",
            "SUBWAY",
            "BAR ",
        ),
        "out",
    ),
    KeywordRule(
        "transport",
        (
            "SNCF",
            "OUIGO",
            "TGV",
            "TER ",
            "TOTALENERGIES",
            "TOTAL ACCESS",
            "ESSO",
            "SHELL",
            "BP ",
            "AVIA",
            "CARBURANT",
            "STATION",
            "UBER",
            "BLABLACAR",
            "RATP",
            "NAVIGO",
            "TCL",
            "TISSEO",
            "PEAGE",
            "APRR",
            "VINCI AUTOROUTES",
            "SANEF",
            "ASF ",
            "PARKING",
            "INDIGO",
            "EFFIA",
            "LIME",
            "BOLT",
            "AIR FRANCE",
            "EASYJET",
            "RYANAIR",
            "TRANSAVIA",
            "VOLOTEA",
            "FLIXBUS",
            "NORAUTO",
            "MIDAS",
            "SPEEDY",
            "FEU VERT",
            "CONTROLE TECHNIQUE",
        ),
        "out",
    ),
    KeywordRule(
        "sante",
        (
            "PHARMACIE",
            "PHARMA",
            "PHIE",
            "DOCTEUR",
            "DR ",
            "MEDECIN",
            "DENTISTE",
            "DENTAIRE",
            "OPTIC",
            "OPTIQUE",
            "AFFLELOU",
            "KRYS",
            "LABORATOIRE",
            "LABO ",
            "KINE",
            "OSTEO",
            "HOPITAL",
            "CLINIQUE",
            "DOCTOLIB",
            "ORTHOPHONISTE",
            "PSYCHOLOGUE",
            "RADIOLOGIE",
        ),
        "out",
    ),
    KeywordRule(
        "maison",
        (
            "LEROY MERLIN",
            "CASTORAMA",
            "BRICO",
            "BRICORAMA",
            "BRICO DEPOT",
            "IKEA",
            "CONFORAMA",
            "BUT ",
            "MAISONS DU MONDE",
            "WELDOM",
            "MR BRICOLAGE",
            "BRICOMARCHE",
            "JARDILAND",
            "TRUFFAUT",
            "GAMM VERT",
            "LA FOIR FOUILLE",
            "BOULANGER",
            "DARTY",
        ),
        "out",
    ),
    KeywordRule(
        "shopping",
        (
            "AMAZON",
            "AMZN",
            "FNAC",
            "CDISCOUNT",
            "ZALANDO",
            "DECATHLON",
            "H&M",
            "ZARA",
            "KIABI",
            "PRIMARK",
            "SHEIN",
            "TEMU",
            "ALIEXPRESS",
            "VINTED",
            "LEBONCOIN",
            "ACTION",
            "GIFI",
            "LDLC",
            "APPLE STORE",
            "SEPHORA",
            "NOCIBE",
            "JULES",
            "CELIO",
            "UNIQLO",
            "GO SPORT",
            "INTERSPORT",
            "MICROMANIA",
            "CULTURA",
            "NOZ",
        ),
        "out",
    ),
    KeywordRule(
        "loisirs",
        (
            "CINEMA",
            "PATHE",
            "GAUMONT",
            "UGC",
            "CGR",
            "MEGARAMA",
            "STEAM",
            "EPIC GAMES",
            "TICKETMASTER",
            "FNAC SPECTACLES",
            "BOWLING",
            "PARC ",
            "MUSEE",
            "AIRBNB",
            "BOOKING",
            "HOTEL",
            "CAMPING",
            "LASER GAME",
            "ESCAPE",
        ),
        "out",
    ),
    KeywordRule(
        "enfants",
        (
            "CRECHE",
            "CANTINE",
            "PERISCOLAIRE",
            "NOUNOU",
            "ASSISTANTE MATERNELLE",
            "AUBERT",
            "JOUE CLUB",
            "KING JOUET",
            "VERTBAUDET",
            "CYRILLUS",
            "OKAIDI",
            "PAJEMPLOI",
        ),
        "out",
    ),
    KeywordRule(
        "frais_bancaires",
        (
            "FRAIS",
            "COTISATION",
            "COTIS",
            "COMMISSION",
            "AGIOS",
            "INTERETS DEBITEURS",
            "TENUE DE COMPTE",
            "OFFRE GROUPEE",
            "COMMISSION D INTERVENTION",
        ),
        "out",
    ),
    KeywordRule("retraits", ("RETRAIT DAB", "RETRAIT GAB", "RETRAIT", "DAB "), "out"),
    KeywordRule(
        "cadeaux_dons",
        (
            "DON ",
            "HELLOASSO",
            "LEETCHI",
            "CROIX ROUGE",
            "RESTOS DU COEUR",
            "UNICEF",
            "SECOURS POPULAIRE",
            "SECOURS CATHOLIQUE",
            "MEDECINS SANS FRONTIERES",
        ),
        "out",
    ),
)


@lru_cache(maxsize=512)
def _keyword_regex(keyword: str) -> re.Pattern[str]:
    # Bornes « pas alphanumérique » : BP ne matche pas BPCE, CAF ne matche pas CAFE, BUT pas BUTAGAZ.
    return re.compile(rf"(?<![A-Z0-9]){re.escape(keyword.strip())}(?![A-Z0-9])")


def builtin_category(raw_label: str, amount_cents: int) -> str | None:
    text = searchable(raw_label)
    direction = "in" if amount_cents > 0 else "out"
    for rule in BUILTIN_RULES:
        if rule.direction not in ("any", direction):
            continue
        if any(_keyword_regex(k).search(text) for k in rule.keywords):
            return rule.category
    return None


# --------------------------------------------------------------------------- bayésien naïf
_TOKEN = re.compile(r"[A-Z]{3,}")
_IGNORED_TOKENS = {
    "PRLV",
    "SEPA",
    "CARTE",
    "PAIEMENT",
    "VIREMENT",
    "RECU",
    "EMIS",
    "FACTURE",
    "ACHAT",
    "AVOIR",
}


def tokens(raw_label: str) -> list[str]:
    return [t for t in _TOKEN.findall(searchable(raw_label)) if t not in _IGNORED_TOKENS]


@dataclass
class TokenBayes:
    """Classifieur bayésien naïf multinomial (lissage de Laplace), entraîné sur l'historique catégorisé.
    Aucune dépendance, entraînement en O(n), prédiction en O(tokens x catégories)."""

    min_samples: int = 25
    min_confidence: float = 0.75
    _class_counts: Counter[str] = field(default_factory=Counter)
    _token_counts: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    _vocab: set[str] = field(default_factory=set)
    _total: int = 0

    def fit(self, samples: Iterable[tuple[str, str]]) -> TokenBayes:
        for label, category in samples:
            toks = tokens(label)
            if not toks:
                continue
            self._class_counts[category] += 1
            self._token_counts[category].update(toks)
            self._vocab.update(toks)
            self._total += 1
        return self

    @property
    def ready(self) -> bool:
        return self._total >= self.min_samples and len(self._class_counts) >= 2

    def predict(self, raw_label: str) -> tuple[str, float] | None:
        if not self.ready:
            return None
        toks = [t for t in tokens(raw_label) if t in self._vocab]
        if not toks:
            return None
        v = len(self._vocab)
        scores: dict[str, float] = {}
        for cat, n in self._class_counts.items():
            counts = self._token_counts[cat]
            denom = sum(counts.values()) + v
            scores[cat] = math.log(n / self._total) + sum(math.log((counts[t] + 1) / denom) for t in toks)
        best = max(scores, key=lambda c: scores[c])
        m = max(scores.values())
        z = sum(math.exp(s - m) for s in scores.values())
        confidence = 1.0 / z  # exp(best - m) == 1
        return (best, confidence) if confidence >= self.min_confidence else None


# --------------------------------------------------------------------------- orchestrateur
@dataclass(frozen=True)
class Rule:
    pattern: str
    match: str  # merchant | contains
    direction: str
    category: str
    source: str  # user | learned


@dataclass(frozen=True)
class Decision:
    category: str
    source: str  # user_rule | learned | bayes | builtin | none
    confidence: float


class Categorizer:
    def __init__(self, rules: Sequence[Rule] = (), bayes: TokenBayes | None = None) -> None:
        self._user = [r for r in rules if r.source == "user"]
        self._learned = {(r.pattern, r.direction): r.category for r in rules if r.source == "learned"}
        self._bayes = bayes

    def decide(self, raw_label: str, merchant: str, amount_cents: int) -> Decision:
        direction = "in" if amount_cents > 0 else "out"
        text = searchable(raw_label)
        for r in self._user:
            if r.direction not in ("any", direction):
                continue
            hit = (merchant == r.pattern) if r.match == "merchant" else (r.pattern in text)
            if hit:
                return Decision(r.category, "user_rule", 1.0)
        learned = self._learned.get((merchant, direction)) or self._learned.get((merchant, "any"))
        if merchant and learned:
            return Decision(learned, "learned", 0.95)
        builtin = builtin_category(raw_label, amount_cents)
        if self._bayes is not None:
            guess = self._bayes.predict(raw_label)
            # Le bayésien ne contredit le dictionnaire que s'il est très sûr : il capte tes habitudes
            # (ex. « PAYPAL » = abonnements chez toi) sans écraser les cas évidents.
            if guess and (builtin is None or (guess[0] != builtin and guess[1] >= 0.97)):
                return Decision(guess[0], "bayes", round(guess[1], 3))
        if builtin:
            return Decision(builtin, "builtin", 0.8)
        return Decision("a_categoriser", "none", 0.0)
