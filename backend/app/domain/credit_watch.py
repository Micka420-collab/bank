"""Crédit et coûts : ce que coûte vraiment ton découvert et tes crédits face au seuil de l'usure, la renégociation que
le taux moyen des nouveaux prêts immobiliers rend rentable, et tes trajets domicile-travail face à l'abattement de
10 %.

Trois familles de constats, rangées avec les autres corrélations (même forme, mêmes décisions) :
* usure : les agios rapportés à ton solde débiteur reconstitué jour par jour, et le taux actuariel de tes crédits,
  comparés au seuil du trimestre (avis trimestriel au Journal officiel). Les deux calculs ne comptent que les
  intérêts : le vrai TAEG, frais et assurance compris, est au moins aussi haut. Bank ne dit jamais qu'une somme est
  due : il montre la méthode et renvoie au ticket d'agios ou à l'offre de prêt ;
* renégociation : le simulateur de renégociation lancé seul avec le taux effectif moyen des nouveaux prêts
  immobiliers publié dans le même avis (assurance et frais compris, donc une hypothèse prudente) ;
* frais réels : jours travaillés lus dans ton agenda, distance et puissance que tu as indiquées, barème kilométrique
  officiel daté, comparés à l'abattement de 10 % avec l'estimation d'impôt de domain.income_watch.

Chaque chiffre vient d'une donnée datée et sourcée (rates.json, ou l'avis relu dans les archives de la DILA) ou de
tes relevés. Module pur : la lecture des archives du Journal officiel se fait ici sur des octets déjà reçus.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import defusedxml.ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.domain import loans as dl
from app.domain.baremes import Observation
from app.domain.correlations import Evidence, Finding
from app.domain.income_watch import TaxModel, estimate_tax, fold, fr_percent
from app.domain.money import format_eur
from app.domain.recurring import add_months


def eur0(cents: int) -> str:
    """Un ordre de grandeur à l'euro : « 1 234 € »."""
    return f"{round(cents / 100):,}".replace(",", " ") + " €"


def pct(value: Decimal, places: str = "0.1") -> str:
    return fr_percent(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def fr_num(value: Decimal | int) -> str:
    """« 38 », « 12,5 », « 8 480 »."""
    text = f"{Decimal(value).normalize():f}"
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{whole},{frac}" if frac else whole


def _dec(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw).replace(",", ".").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


# ------------------------------------------------------------------ trimestres
def quarter_id(day: date) -> str:
    return f"{day.year:04d}-Q{(day.month - 1) // 3 + 1}"


def quarter_start(qid: str) -> date:
    m = re.fullmatch(r"(\d{4})-Q([1-4])", qid)
    if not m:
        raise ValueError(qid)
    return date(int(m[1]), (int(m[2]) - 1) * 3 + 1, 1)


def shift_quarter(qid: str, n: int) -> str:
    return quarter_id(add_months(quarter_start(qid), 3 * n))


def quarter_label(qid: str) -> str:
    q = int(qid[-1])
    return f"{q}{'er' if q == 1 else 'e'} trimestre {qid[:4]}"


# ------------------------------------------------------------------ seuils de l'usure
CATEGORIES: dict[str, str] = {
    "conso_3000": "crédit à la consommation ou découvert de 3 000 € ou moins",
    "conso_6000": "crédit à la consommation ou découvert de plus de 3 000 € et jusqu'à 6 000 €",
    "conso_plus": "crédit à la consommation ou découvert de plus de 6 000 €",
    "immo_fixe_lt10": "prêt immobilier à taux fixe de moins de 10 ans",
    "immo_fixe_10_20": "prêt immobilier à taux fixe de 10 à moins de 20 ans",
    "immo_fixe_20": "prêt immobilier à taux fixe de 20 ans et plus",
    "immo_variable": "prêt immobilier à taux variable",
    "immo_relais": "prêt relais",
}
MATURITY = {"immo_fixe_lt10": "moins de 10 ans", "immo_fixe_10_20": "10 à 20 ans", "immo_fixe_20": "20 ans et plus"}
REQUIRED = ("conso_3000", "conso_6000", "conso_plus", "immo_fixe_lt10", "immo_fixe_10_20", "immo_fixe_20")
USURY_SERIES = "jorf.usure"


class UsuryError(ValueError):
    """Avis illisible ou incomplet : message lisible, les seuils connus restent."""


@dataclass(frozen=True)
class UsuryQuarter:
    quarter: str  # 2026-Q4 : trimestre où les seuils s'appliquent
    effective: date
    thresholds: Mapping[str, Decimal]
    averages: Mapping[str, Decimal]  # taux effectifs moyens pratiqués au trimestre précédent
    source: str
    url: str

    @property
    def averages_quarter(self) -> str:
        return shift_quarter(self.quarter, -1)


def _rates(raw: object) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            d = _dec(value)
            if key in CATEGORIES and d is not None and Decimal(0) < d < Decimal(50):
                out[str(key)] = d
    return out


def _quarter_of(item: Mapping[str, Any]) -> UsuryQuarter | None:
    try:
        qid = str(item["quarter"])
        q = UsuryQuarter(
            qid,
            date.fromisoformat(str(item.get("effective") or quarter_start(qid).isoformat())),
            _rates(item.get("thresholds")),
            _rates(item.get("averages")),
            str(item["source"]),
            str(item["url"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return q if all(k in q.thresholds for k in REQUIRED) else None


def load_usury(raw: Mapping[str, Any]) -> list[UsuryQuarter]:
    """Les seuils datés et sourcés de rates.json (section « usury »)."""
    out = []
    for item in (raw.get("usury") or {}).get("items") or []:
        q = _quarter_of(item) if isinstance(item, Mapping) else None
        if q is not None:
            out.append(q)
    return out


def usury_from_observation(period: str, value: str, note: str | None, url: str) -> UsuryQuarter | None:
    """Un avis relu par Bank (rate_observations, série jorf.usure) : la valeur est un JSON {thresholds, averages}."""
    try:
        data = json.loads(value)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return _quarter_of({**data, "quarter": period, "source": note or "Journal officiel", "url": url})


def merge_usury(static: Iterable[UsuryQuarter], observed: Iterable[UsuryQuarter]) -> dict[str, UsuryQuarter]:
    """Par trimestre ; un avis relu par Bank passe devant le fichier (c'est le même texte, lu plus récemment)."""
    out = {q.quarter: q for q in static}
    out.update({q.quarter: q for q in observed})
    return dict(sorted(out.items()))


def usury_observation(q: UsuryQuarter) -> Observation:
    value = {
        "effective": q.effective.isoformat(),
        "thresholds": {k: str(v) for k, v in q.thresholds.items()},
        "averages": {k: str(v) for k, v in q.averages.items()},
    }
    return Observation(USURY_SERIES, q.quarter, json.dumps(value, sort_keys=True), q.url, q.source[:300])


# ------------------------------------------------------------------ Journal officiel : archives quotidiennes de la DILA
DILA_JORF_URL = "https://echanges.dila.gouv.fr/OPENDATA/JORF/"
LEGIFRANCE_TEXT = "https://www.legifrance.gouv.fr/jorf/id/{cid}"
HTML_ACCEPT = "text/html, */*;q=0.1"
GZIP_ACCEPT = "application/gzip, application/x-gzip;q=0.9, */*;q=0.1"
MAX_ARCHIVES = 12  # archives ouvertes au plus par trimestre cherché et par relève
MAX_MEMBERS = 200_000
MAX_MEMBER_BYTES = 300_000
MAX_TAR_BYTES = 400_000_000  # décompressé : une archive quotidienne en fait quelques dizaines de Mo
_ARCHIVE = re.compile(r'href="(JORF_(\d{8})-\d{6}\.tar\.gz)"')
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        (
            "JANVIER",
            "FEVRIER",
            "MARS",
            "AVRIL",
            "MAI",
            "JUIN",
            "JUILLET",
            "AOUT",
            "SEPTEMBRE",
            "OCTOBRE",
            "NOVEMBRE",
            "DECEMBRE",
        )
    )
}


def search_window(qid: str) -> tuple[date, date]:
    """L'avis paraît dans les derniers jours du trimestre qui précède (le 26/09/2026 pour le 4e trimestre 2026) : on
    cherche du 18 du dernier mois au 9 du premier mois du trimestre."""
    start = quarter_start(qid)
    return (start - timedelta(days=1)).replace(day=18), start + timedelta(days=8)


def wanted_quarters(today: date, known: Iterable[str]) -> list[str]:
    """Le trimestre en cours, puis le suivant dès que son avis peut être paru, s'ils ne sont pas encore connus."""
    have = set(known)
    current = quarter_id(today)
    out = []
    for qid in (current, shift_quarter(current, 1)):
        if qid not in have and search_window(qid)[0] <= today:
            out.append(qid)
    return out


def archives_for(listing: bytes, qid: str, today: date) -> list[str]:
    """Les archives quotidiennes où chercher l'avis d'un trimestre : de la plus récente à la plus ancienne avant le
    début du trimestre (l'avis paraît en fin de mois), puis les premiers jours du trimestre."""
    text = listing.decode("latin-1", errors="replace")
    first, last = search_window(qid)
    start = quarter_start(qid)
    found: list[tuple[date, str]] = []
    for name, day in dict.fromkeys(_ARCHIVE.findall(text)):
        try:
            d = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        except ValueError:
            continue
        if first <= d <= last and d <= today:
            found.append((d, name))
    before = sorted((x for x in found if x[0] < start), reverse=True)
    after = sorted(x for x in found if x[0] >= start)
    return [name for _d, name in (*before, *after)][:MAX_ARCHIVES]


def usury_article(archive: bytes) -> bytes | None:
    """Le fichier XML de l'avis sur l'usure dans une archive quotidienne, s'il y est. L'archive est lue en mémoire,
    jamais écrite sur le disque ; tailles et nombre de fichiers bornés."""
    seen = total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                seen += 1
                total += max(0, member.size)
                if seen > MAX_MEMBERS or total > MAX_TAR_BYTES:
                    raise UsuryError("Archive du Journal officiel trop volumineuse")
                if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                    continue
                if "/article/JORF/ARTI/" not in member.name or not member.name.endswith(".xml"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                body = handle.read(MAX_MEMBER_BYTES + 1)
                if b"concernant l'usure" in body and b"<BLOC_TEXTUEL>" in body:
                    return body
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise UsuryError("Archive du Journal officiel illisible") from exc
    return None


def _text(el: Any) -> str:
    # un <br/> colle deux morceaux (« supérieur à<br/>3 000 euros ») : on les sépare d'une espace
    return re.sub(r"\s+", " ", " ".join(el.itertext()).replace("\xa0", " ")).strip()


def _percent(text: str) -> Decimal | None:
    m = re.search(r"(\d{1,2}(?:,\d{1,3})?)\s*%", text)
    return _dec(m[1]) if m else None


def _category(section: str, label: str) -> str | None:
    f = fold(label)
    if section == "conso":
        if "SUPERIEUR A 6 000" in f:
            return "conso_plus"
        if "SUPERIEUR A 3 000" in f:
            return "conso_6000"
        if "3 000" in f:
            return "conso_3000"
        return None
    if "INFERIEURE A 10 ANS" in f:
        return "immo_fixe_lt10"
    if "10 ANS ET MOINS DE 20" in f:
        return "immo_fixe_10_20"
    if "20 ANS ET PLUS" in f:
        return "immo_fixe_20"
    if "TAUX VARIABLE" in f:
        return "immo_variable"
    if "RELAIS" in f:
        return "immo_relais"
    return None


def parse_usury(body: bytes) -> UsuryQuarter:
    """L'avis trimestriel (XML de la DILA) : seuils pour les particuliers (consommation et immobilier) et taux
    effectifs moyens du trimestre écoulé. Les tableaux des prêts aux entreprises sont ignorés."""
    try:
        root = SafeET.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise UsuryError("Avis illisible (XML invalide ou interdit)") from exc
    texte = root.find("./CONTEXTE/TEXTE")
    title = root.find("./CONTEXTE/TEXTE/TITRE_TXT")
    content = root.find("./BLOC_TEXTUEL/CONTENU")
    if texte is None or title is None or content is None or "usure" not in _text(title):
        raise UsuryError("Ce n'est pas l'avis sur l'usure")
    m = re.search(r"COMPTER DU 1 ?(?:ER)? (\w+) (\d{4})", fold(_text(content)))
    if not m or m[1] not in _MONTHS:
        raise UsuryError("Date d'application introuvable dans l'avis")
    effective = date(int(m[2]), _MONTHS[m[1]], 1)
    if effective.month not in (1, 4, 7, 10):
        raise UsuryError("Date d'application inattendue")
    thresholds: dict[str, Decimal] = {}
    averages: dict[str, Decimal] = {}
    for table in content.iter("table"):
        section = None
        for tr in table.iter("tr"):
            cells = [_text(c) for c in tr if c.tag in ("td", "th")]
            if len(cells) == 1:
                head = fold(cells[0])
                if "CONSOMMATEURS N'ENTRANT PAS" in head:
                    section = "conso"
                elif "CREDIT IMMOBILIER" in head:
                    section = "immo"
                continue
            if section is None or len(cells) != 3:
                continue
            cat = _category(section, cells[0])
            avg, threshold = _percent(cells[1]), _percent(cells[2])
            if cat is None or threshold is None or cat in thresholds:
                continue
            if not Decimal(0) < threshold < Decimal(50):
                raise UsuryError("Seuil hors bornes dans l'avis")
            thresholds[cat] = threshold
            if avg is not None and Decimal(0) < avg < threshold:
                averages[cat] = avg
    missing = [CATEGORIES[k] for k in REQUIRED if k not in thresholds]
    if missing:
        raise UsuryError(f"Avis incomplet : {missing[0]} introuvable")
    attrs = texte.attrib
    signed, published = attrs.get("date_signature", ""), attrs.get("date_publi", "")
    try:
        signed_d, published_d = date.fromisoformat(signed), date.fromisoformat(published)
    except ValueError as exc:
        raise UsuryError("Dates de l'avis illisibles") from exc
    cid = attrs.get("cid", "")
    if not re.fullmatch(r"JORFTEXT\d{12}", cid):
        raise UsuryError("Identifiant de l'avis illisible")
    number = attrs.get("num_parution_jo", "")
    source = (
        f"Avis du {signed_d:%d/%m/%Y} relatif à l'usure (art. L. 314-6 du code de la consommation), JO n° {number} "
        f"du {published_d:%d/%m/%Y}"
    )
    return UsuryQuarter(quarter_id(effective), effective, thresholds, averages, source, LEGIFRANCE_TEXT.format(cid=cid))


# ------------------------------------------------------------------ découvert : agios face au seuil
AGIOS = re.compile(r"AGIOS|INTERETS? DEBITEURS?|INT\.? DEBITEURS?")
FRESH_BALANCE_DAYS = 10  # au-delà, le solde du compte est trop ancien pour remonter le temps
PREVIOUS_CHARGE_DAYS = 100  # agios précédents plus anciens : période prise sur 92 jours
DEFAULT_PERIOD_DAYS = 92
MIN_DEBIT_DAYS = 3
MIN_AVERAGE_DEBIT_CENTS = 5_000
MIN_AGIOS_CENTS = 100
MARGIN_POINTS = Decimal(1)  # le taux estimé doit dépasser le seuil d'au moins un point
RECENT_CHARGE_DAYS = 200
YEAR_DAYS = 365


@dataclass(frozen=True)
class Movement:
    id: int
    day: date  # date de valeur si la banque la donne, sinon date d'opération
    amount_cents: int
    label: str


@dataclass(frozen=True)
class AccountHistory:
    id: int
    name: str
    balance_cents: int
    balance_on: date  # jour du solde connu (fin de journée)
    movements: Sequence[Movement]


@dataclass(frozen=True)
class OverdraftCost:
    account: AccountHistory
    charge: Movement
    start: date
    end: date
    days: int
    debit_days: int
    debit_cent_days: int  # somme des soldes débiteurs de fin de journée
    max_debit_cents: int
    rate: Decimal  # % par an

    @property
    def average_debit_cents(self) -> int:
        return round(self.debit_cent_days / self.debit_days) if self.debit_days else 0

    @property
    def agios_cents(self) -> int:
        return -self.charge.amount_cents


def is_agios(label: str) -> bool:
    return bool(AGIOS.search(fold(label)))


def end_of_day_balances(hist: AccountHistory, start: date, end: date) -> dict[date, int]:
    """Solde de fin de journée de `start` à `end`, reconstitué depuis le solde connu : solde(J) = solde connu −
    mouvements datés après J (jusqu'au jour du solde connu)."""
    flows: dict[date, int] = {}
    for m in hist.movements:
        if m.day <= hist.balance_on:
            flows[m.day] = flows.get(m.day, 0) + m.amount_cents
    out: dict[date, int] = {}
    bal, d = hist.balance_cents, hist.balance_on
    while d >= start:
        if d <= end:
            out[d] = bal
        bal -= flows.get(d, 0)
        d -= timedelta(days=1)
    return out


def overdraft_costs(hist: AccountHistory, today: date) -> list[OverdraftCost]:
    """Chaque prélèvement d'agios récent, rapporté au solde débiteur de la période qu'il couvre : depuis les agios
    précédents (au plus 100 jours avant), sinon les 92 jours d'avant ; la veille du prélèvement en dernier jour. Une
    période plus longue que la vraie ne peut que baisser le taux trouvé."""
    charges = sorted((m for m in hist.movements if m.amount_cents < 0 and is_agios(m.label)), key=lambda m: m.day)
    out: list[OverdraftCost] = []
    for i, c in enumerate(charges):
        if (today - c.day).days > RECENT_CHARGE_DAYS or -c.amount_cents < MIN_AGIOS_CENTS or c.day > hist.balance_on:
            continue
        prev = charges[i - 1].day if i else None
        start = prev if prev and (c.day - prev).days <= PREVIOUS_CHARGE_DAYS else c.day - timedelta(DEFAULT_PERIOD_DAYS)
        end = c.day - timedelta(days=1)
        if end < start:
            continue
        balances = end_of_day_balances(hist, start, end)
        debits = [-b for b in balances.values() if b < 0]
        if len(debits) < MIN_DEBIT_DAYS:
            continue
        cent_days = sum(debits)
        if cent_days // len(debits) < MIN_AVERAGE_DEBIT_CENTS:
            continue
        rate = Decimal(-c.amount_cents) * YEAR_DAYS * 100 / Decimal(cent_days)
        out.append(OverdraftCost(hist, c, start, end, len(balances), len(debits), cent_days, max(debits), rate))
    return out


def consumer_category(amount_cents: int) -> str:
    if amount_cents <= 300_000:
        return "conso_3000"
    if amount_cents <= 600_000:
        return "conso_6000"
    return "conso_plus"


def threshold_for(
    quarters: Mapping[str, UsuryQuarter], days: Iterable[date], category: str
) -> tuple[Decimal, UsuryQuarter] | None:
    """Le seuil le plus haut des trimestres couverts (le plus prudent) ; None si l'un d'eux n'est pas connu."""
    best: tuple[Decimal, UsuryQuarter] | None = None
    for qid in sorted({quarter_id(d) for d in days}):
        q = quarters.get(qid)
        if q is None or category not in q.thresholds:
            return None
        if best is None or q.thresholds[category] > best[0]:
            best = (q.thresholds[category], q)
    return best


def overdraft_findings(costs: Iterable[OverdraftCost], quarters: Mapping[str, UsuryQuarter]) -> list[Finding]:
    out: list[Finding] = []
    for cost in costs:
        category = consumer_category(cost.average_debit_cents)
        days = [cost.start + timedelta(days=i) for i in range((cost.end - cost.start).days + 1)]
        found = threshold_for(quarters, days, category)
        if found is None:
            continue
        threshold, q = found
        if cost.rate < threshold + MARGIN_POINTS:
            continue
        at_threshold = int(
            (Decimal(cost.debit_cent_days) * threshold / 100 / YEAR_DAYS).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        )
        c = cost.charge
        out.append(
            Finding(
                key=f"usure_decouvert:{cost.account.id}:{c.day.isoformat()}",
                kind="usury_overdraft",
                level="warning",
                topic="credits",
                title=f"Découvert : agios du {c.day:%d/%m/%Y} à environ {pct(cost.rate)} par an, au-dessus du seuil "
                f"de l'usure ({fr_percent(threshold)})",
                body=(
                    f"Estimation Bank : {format_eur(cost.agios_cents)} d'agios prélevés le {c.day:%d/%m/%Y} sur "
                    f"{cost.account.name}, pour un solde débiteur moyen de {eur0(cost.average_debit_cents)} pendant "
                    f"{cost.debit_days} jours entre le {cost.start:%d/%m/%Y} et le {cost.end:%d/%m/%Y}, "
                    f"correspondent à environ {pct(cost.rate)} par an. Le seuil de l'usure pour un "
                    f"{CATEGORIES[category]} est de {fr_percent(threshold)} depuis le {q.effective:%d/%m/%Y}. "
                    "Au seuil, ces jours auraient coûté "
                    f"environ {format_eur(at_threshold)}. Méthode : agios × 365 ÷ somme des soldes débiteurs de fin de "
                    "journée, soldes reconstitués depuis ton solde actuel et tes opérations (dates de valeur quand ta "
                    "banque les donne) ; les autres frais du découvert ne sont pas comptés, ils ne feraient que monter "
                    "le taux. Le ticket d'agios ou ton relevé indique le taux et le TAEG appliqués : c'est la pièce "
                    "à comparer."
                ),
                evidence=(
                    Evidence("Agios", f"{format_eur(cost.agios_cents)} le {c.day:%d/%m/%Y}", f"txn:{c.id}"),
                    Evidence(
                        "Solde débiteur",
                        f"{cost.debit_days} jours sur {cost.days}, moyenne {eur0(cost.average_debit_cents)}, au plus "
                        f"{eur0(cost.max_debit_cents)}",
                    ),
                    Evidence("Taux estimé", f"{pct(cost.rate, '0.01')} par an (intérêts seuls, base 365 jours)"),
                    Evidence(
                        "Seuil de l'usure",
                        f"{fr_percent(threshold)} — {CATEGORIES[category]}, {quarter_label(q.quarter)} — {q.source}",
                        f"url:{q.url}",
                    ),
                ),
                yearly_cents=None,
                confidence=0.5,
            )
        )
    return out


# ------------------------------------------------------------------ crédits : taux actuariel face au seuil
@dataclass(frozen=True)
class TrackedLoan:
    id: int
    name: str
    loan: dl.Loan


def actuarial_rate(loan: dl.Loan) -> Decimal:
    """Taux annuel actuariel des seules échéances hors assurance (capital reçu un mois avant la première échéance) :
    (1 + i)^12 − 1, i mensuel trouvé par dichotomie. Sans frais ni assurance, il est au plus égal au TAEG."""
    sched = dl.schedule(loan)
    flows = [r.payment_cents for r in sched.rows]
    principal = Decimal(loan.principal_cents)

    def npv(i: Decimal) -> Decimal:
        total, factor = Decimal(0), Decimal(1)
        for f in flows:
            factor /= 1 + i
            total += Decimal(f) * factor
        return total - principal

    lo, hi = Decimal(0), Decimal("0.05")
    if npv(lo) <= 0:
        return Decimal(0)
    for _ in range(60):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return ((1 + (lo + hi) / 2) ** 12 - 1) * 100


def loan_category(loan: dl.Loan) -> str | None:
    if loan.kind == "immobilier":
        return "immo_fixe_lt10" if loan.months < 120 else "immo_fixe_10_20" if loan.months < 240 else "immo_fixe_20"
    if loan.kind in ("consommation", "auto"):
        return consumer_category(loan.principal_cents)
    return None  # « autre » : la catégorie de l'usure n'est pas connue


def loan_usury_findings(loans: Iterable[TrackedLoan], quarters: Mapping[str, UsuryQuarter]) -> list[Finding]:
    """La date de l'offre n'est pas connue : les trimestres d'un à trois mois avant la première échéance comptent,
    le seuil le plus haut sert. Si l'un d'eux n'est pas connu de Bank, rien n'est dit."""
    out: list[Finding] = []
    for t in loans:
        category = loan_category(t.loan)
        if category is None:
            continue
        first = t.loan.first_payment
        found = threshold_for(quarters, (add_months(first, -1), add_months(first, -3)), category)
        if found is None:
            continue
        threshold, q = found
        rate = actuarial_rate(t.loan)
        if rate <= threshold:
            continue
        out.append(
            Finding(
                key=f"usure_pret:{t.id}",
                kind="usury_loan",
                level="warning",
                topic="credits",
                title=f"{t.name} : taux actuariel d'environ {pct(rate, '0.01')}, au-dessus du seuil de l'usure "
                f"({fr_percent(threshold)})",
                body=(
                    f"Estimation Bank : au taux nominal de {fr_percent(Decimal(t.loan.annual_rate_bp) / 100)} sur "
                    f"{t.loan.months} mois, les seules échéances de ce prêt donnent un taux annuel actuariel d'environ "
                    f"{pct(rate, '0.01')}. Le seuil de l'usure d'un {CATEGORIES[category]} était de "
                    f"{fr_percent(threshold)} au {quarter_label(q.quarter)}. Les frais de dossier, de garantie et "
                    "l'assurance ne sont pas comptés : le TAEG de ton offre est au moins aussi haut. Bank ne connaît "
                    "pas la date de ton offre : il prend le seuil le plus haut des trimestres d'un à trois mois avant "
                    "ta première échéance. Vérifie les chiffres du prêt saisis dans Bank, puis le TAEG écrit dans "
                    "ton offre."
                ),
                evidence=(
                    Evidence(
                        "Prêt",
                        f"{format_eur(t.loan.principal_cents)} sur {t.loan.months} mois, première "
                        f"échéance le {first:%d/%m/%Y}",
                    ),
                    Evidence("Taux actuariel estimé", f"{pct(rate, '0.01')} (échéances hors assurance, sans frais)"),
                    Evidence(
                        "Seuil de l'usure",
                        f"{fr_percent(threshold)} — {CATEGORIES[category]} — {q.source}",
                        f"url:{q.url}",
                    ),
                ),
                confidence=0.5,
            )
        )
    return out


# ------------------------------------------------------------------ renégociation déclenchée par le marché
MIN_RENEGOTIATION_GAIN_CENTS = 100_000  # en dessous, des frais ordinaires mangent le gain : pas de constat
MAX_AVERAGES_AGE_QUARTERS = 2


def market_category(months_left: int) -> str:
    return "immo_fixe_lt10" if months_left < 120 else "immo_fixe_10_20" if months_left < 240 else "immo_fixe_20"


def latest_averages(quarters: Mapping[str, UsuryQuarter], today: date) -> UsuryQuarter | None:
    """L'avis le plus récent qui porte des taux moyens, s'il n'a pas plus de deux trimestres."""
    current = quarter_id(today)
    for qid in sorted(quarters, reverse=True):
        q = quarters[qid]
        if qid > current or not q.averages:
            continue
        if qid < shift_quarter(current, -MAX_AVERAGES_AGE_QUARTERS):
            return None
        return q
    return None


def renegotiation_findings(loans: Iterable[TrackedLoan], market: UsuryQuarter | None, today: date) -> list[Finding]:
    if market is None:
        return []
    out: list[Finding] = []
    for t in loans:
        if t.loan.kind != "immobilier":
            continue
        pos = dl.position(dl.schedule(t.loan), today)
        if pos.months_left == 0 or pos.remaining_cents <= 0:
            continue
        category = market_category(pos.months_left)
        avg = market.averages.get(category)
        if avg is None:
            continue
        new_bp = int((avg * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if new_bp >= t.loan.annual_rate_bp:
            continue
        try:
            sim = dl.renegotiate(t.loan, today, new_bp, fees_cents=0, buyout=True)
        except dl.LoanError:
            continue
        if sim.net_gain_cents < MIN_RENEGOTIATION_GAIN_CENTS:
            continue
        out.append(
            Finding(
                key=f"renegociation:{t.id}:{market.quarter}",
                kind="renegotiation",
                level="info",
                topic="credits",
                title=f"{t.name} : à {fr_percent(avg)}, le simulateur de renégociation donne environ "
                f"{eur0(sim.net_gain_cents)} de gain net",
                body=(
                    f"Estimation Bank : les nouveaux prêts immobiliers à taux fixe ({MATURITY[category]}) se "
                    f"sont faits en moyenne à {fr_percent(avg)} au {quarter_label(market.averages_quarter)}, assurance "
                    f"et frais compris ; ton prêt est à {fr_percent(Decimal(t.loan.annual_rate_bp) / 100)} hors "
                    f"assurance. Le simulateur de renégociation, lancé à ce taux sur tes {sim.months_left} mois "
                    f"restants ({eur0(sim.remaining_cents)} restant dû), donne une mensualité hors assurance de "
                    f"{format_eur(sim.new_payment_cents)} au lieu de {format_eur(sim.old_payment_cents)}, "
                    f"{eur0(sim.old_interest_left_cents - sim.new_interest_cents)} d'intérêts en moins et "
                    f"{eur0(sim.net_gain_cents)} de gain net après l'indemnité de remboursement anticipé maximale "
                    f"({eur0(sim.indemnity_cents)}, cas d'un rachat par une autre banque). Les frais de dossier, de "
                    f"garantie ou de courtage ne sont pas comptés : au-delà de {eur0(sim.net_gain_cents)} au total, "
                    "l'opération ne gagne plus rien. Un taux moyen n'est pas une offre : ta banque ou une autre "
                    "fixera le sien."
                ),
                evidence=(
                    Evidence(
                        "Taux moyen du marché",
                        f"{fr_percent(avg)} — taux effectif moyen pratiqué au {quarter_label(market.averages_quarter)}"
                        f", {CATEGORIES[category]} — {market.source}",
                        f"url:{market.url}",
                    ),
                    Evidence(
                        "Simulateur",
                        f"nouveau taux {fr_percent(avg)}, frais 0 €, rachat par une autre banque : gain net "
                        f"{format_eur(sim.net_gain_cents)}",
                        f"loan:{t.id}:{new_bp}",
                    ),
                    Evidence(
                        "Indemnité",
                        f"{format_eur(sim.indemnity_cents)} — {dl.RULES['ira_immobilier']['title']}",
                        f"url:{dl.RULES['ira_immobilier']['url']}",
                    ),
                ),
                yearly_cents=None,
                confidence=0.5,
            )
        )
    return out


# ------------------------------------------------------------------ frais réels : trajets domicile-travail
@dataclass(frozen=True)
class MileageBand:
    hp: int
    upto_5000: Decimal  # €/km jusqu'à 5 000 km
    mid_rate: Decimal  # de 5 001 à 20 000 km : d × taux + forfait
    mid_fixed_cents: int
    over_20000: Decimal


@dataclass(frozen=True)
class MileageScale:
    incomes_year: int  # dernier millésime de revenus pour lequel le barème est confirmé
    bands: Mapping[int, MileageBand]
    max_one_way_km: int
    electric_uplift_percent: Decimal
    source: str
    url: str
    rules_source: str
    rules_url: str

    def cost_cents(self, km: int, hp: int, electric: bool) -> int:
        band = self.bands[min(max(hp, min(self.bands)), max(self.bands))]
        d = Decimal(km)
        if km <= 5_000:
            euros = d * band.upto_5000
        elif km <= 20_000:
            euros = d * band.mid_rate + Decimal(band.mid_fixed_cents) / 100
        else:
            euros = d * band.over_20000
        if electric:
            euros *= 1 + self.electric_uplift_percent / 100
        return int((euros * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def load_mileage(raw: Mapping[str, Any]) -> MileageScale | None:
    m = raw.get("mileage_allowance") or {}
    try:
        bands = {
            int(b["hp"]): MileageBand(
                int(b["hp"]),
                Decimal(str(b["upto_5000"])),
                Decimal(str(b["mid_rate"])),
                int(b["mid_fixed_cents"]),
                Decimal(str(b["over_20000"])),
            )
            for b in m["cars"]
        }
        if not bands:
            return None
        return MileageScale(
            int(m["incomes_year"]),
            bands,
            int(m["max_one_way_km"]),
            Decimal(str(m["electric_uplift_percent"])),
            str(m["source"]),
            str(m["url"]),
            str(m["rules_source"]),
            str(m["rules_url"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class Commute:
    one_way_km: Decimal
    fiscal_hp: int
    electric: bool


@dataclass(frozen=True)
class WorkDays:
    year: int
    seen: int  # jours de travail distincts dans l'agenda sur la période couverte
    covered_from: date
    covered_to: date

    @property
    def covered_days(self) -> int:
        return (self.covered_to - self.covered_from).days + 1

    @property
    def projected(self) -> int:
        """Jours de l'année : ceux de l'agenda, rapportés à l'année entière quand l'agenda n'en couvre qu'une part."""
        span = (date(self.year, 12, 31) - date(self.year, 1, 1)).days + 1
        if self.covered_days >= span:
            return self.seen
        return round(self.seen * span / self.covered_days)


MIN_COVERED_DAYS = 90
MIN_WORK_DAYS_TO_ASK = 40


def work_days(days: Iterable[date], year: int) -> WorkDays | None:
    """Les jours de l'agenda de travail dans l'année, et la période qu'il couvre (du premier au dernier mois vus)."""
    inside = sorted({d for d in days if d.year == year})
    if not inside:
        return None
    first = inside[0].replace(day=1)
    last = add_months(inside[-1].replace(day=1), 1) - timedelta(days=1)
    return WorkDays(year, len(inside), first, min(last, date(year, 12, 31)))


@dataclass(frozen=True)
class SalaryBasis:
    """Ce que l'estimation du taux de prélèvement sait de l'année : salaires projetés, parts, foyer."""

    own_yearly_cents: int
    partner_yearly_cents: int
    parts: Decimal
    couple: bool
    origin: str


def real_expenses_findings(
    days: WorkDays | None,
    commute: Commute | None,
    salary: SalaryBasis | None,
    scale: MileageScale | None,
    model: TaxModel | None,
    today: date,
) -> list[Finding]:
    year = today.year
    if days is None or scale is None or model is None:
        return []
    if commute is None:
        if days.seen < MIN_WORK_DAYS_TO_ASK:
            return []
        return [
            Finding(
                key=f"frais_reels_ask:{year}",
                kind="real_expenses_ask",
                level="info",
                topic="impots",
                title=f"Frais réels {year} : il manque ta distance domicile-travail et ton véhicule",
                body=(
                    f"Ton agenda compte {days.seen} jours de travail en {year}. Avec la distance d'un aller simple "
                    "jusqu'à ton travail et la puissance fiscale de ton véhicule (carte grise, case P.6), Bank compare "
                    "tes frais de trajet au barème kilométrique avec l'abattement de 10 % appliqué d'office. Ces deux "
                    "chiffres restent chiffrés sur ton serveur."
                ),
                evidence=(Evidence("Barème kilométrique", scale.source, f"url:{scale.url}"),),
                confidence=0.6,
            )
        ]
    if days.covered_days < MIN_COVERED_DAYS:
        return []
    if salary is None:
        return [
            Finding(
                key=f"frais_reels_missing:{year}",
                kind="real_expenses",
                level="info",
                topic="impots",
                title=f"Frais réels {year} : Bank ne connaît pas encore tes salaires imposables de l'année",
                body=(
                    "Pour comparer tes frais de trajet à l'abattement de 10 %, Bank a besoin de tes salaires "
                    f"imposables de {year} et de ton taux de prélèvement : dépose tes bulletins de paie ou ton dernier "
                    "avis d'impôt dans Papiers."
                ),
                confidence=0.6,
            )
        ]
    km_one_way = min(commute.one_way_km, Decimal(scale.max_one_way_km))
    yearly_km = int((Decimal(days.projected) * 2 * km_one_way).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    real = scale.cost_cents(yearly_km, commute.fiscal_hp, commute.electric)
    own = salary.own_yearly_cents
    flat = int((Decimal(own) * model.deduction_rate / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    flat = min(max(flat, model.deduction_min_cents), model.deduction_max_cents, own)
    if real <= flat:
        return []
    salaries = [own, salary.partner_yearly_cents] if salary.couple else [own]
    tax_flat = estimate_tax(model, salaries, salary.parts, salary.couple)
    deductions: list[int | None] = [real, None] if salary.couple else [real]
    tax_real = estimate_tax(model, salaries, salary.parts, salary.couple, deductions_cents=deductions)
    saved = tax_flat - tax_real
    capped = commute.one_way_km > scale.max_one_way_km
    hp = min(max(commute.fiscal_hp, min(scale.bands)), max(scale.bands))
    effect = (
        f"Avec l'estimation d'impôt de Bank, environ {eur0(saved)} d'impôt en moins sur les revenus {year}."
        if saved > 0
        else "Avec l'estimation d'impôt de Bank, l'impôt ne baisse pas (il est déjà nul ou presque)."
    )
    days_text = (
        f"{days.seen} jours de travail dans ton agenda"
        if days.projected == days.seen
        else f"{days.seen} jours de travail dans ton agenda du {days.covered_from:%d/%m} au {days.covered_to:%d/%m}, "
        f"soit environ {days.projected} sur l'année"
    )
    return [
        Finding(
            key=f"frais_reels:{year}",
            kind="real_expenses",
            level="info",
            topic="impots",
            title=f"Frais réels {year} : environ {eur0(real)} de trajets, contre {eur0(flat)} d'abattement de 10 %",
            body=(
                f"Estimation Bank : {days_text}, 2 × {fr_num(km_one_way)} km par jour"
                f"{' (40 km par trajet au plus, sauf circonstances particulières)' if capped else ''}, soit environ "
                f"{fr_num(yearly_km)} km avec un véhicule de {hp} CV{' électrique (+20 %)' if commute.electric else ''}"
                f" : environ {eur0(real)} au barème kilométrique, contre {eur0(flat)} d'abattement de 10 % sur "
                f"{eur0(own)} de salaires imposables projetés. {effect} Le barème utilisé est celui des revenus "
                f"{scale.incomes_year} ; celui des revenus {year} n'est pas encore publié, le chiffre bougera avec "
                "lui. Seuls les trajets sont comptés (pas les repas, péages ni parkings) ; opter pour les frais "
                "réels demande de garder les justificatifs (distance, jours travaillés, carte grise)."
            ),
            evidence=(
                Evidence("Jours travaillés", days_text),
                Evidence(
                    "Trajet",
                    f"{fr_num(commute.one_way_km)} km aller, {commute.fiscal_hp} CV"
                    f"{', électrique' if commute.electric else ''} (indiqués par toi)",
                ),
                Evidence("Barème kilométrique", f"revenus {scale.incomes_year} — {scale.source}", f"url:{scale.url}"),
                Evidence("Règles", scale.rules_source, f"url:{scale.rules_url}"),
                Evidence("Salaires", f"{eur0(own)} sur {year} — {salary.origin}"),
                Evidence(
                    "Impôt estimé",
                    f"{eur0(tax_flat)} avec l'abattement, {eur0(tax_real)} aux frais réels — {model.label}",
                    f"url:{model.source_url}",
                ),
            ),
            yearly_cents=saved if saved > 0 else None,
            confidence=0.55,
        )
    ]
