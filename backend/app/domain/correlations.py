"""Corrélations : ce que tes contrats, tes relevés et tes aides disent ensemble, et que chacun tait séparément.

Deux familles, bien distinctes dans la façon d'en parler :
* des **mécanismes connus** — un bail qui nomme un assureur et une autre assurance habitation prélevée (doublon),
  un loyer payé qui dépasse le bail (et l'IRL), une prime qui monte à l'échéance, un bulletin qui ne colle pas au
  virement, une quittance qui ne colle pas au loyer payé, des ressources déclarées que le versement CAF ne reflète
  pas, une mensualité d'énergie décalée de la consommation ;
* des **pistes statistiques**, prudentes : au moins huit mois, corrélation de rang, test par permutation, et une
  formulation « Bank a remarqué » qui n'affirme jamais une cause.

Tout est pur ici : les entrées sont des lignes déjà lues, chaque constat renvoie à ses pièces.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from app.domain import contracts
from app.domain.energy_signals import OPTIONS, PowerCheck, kva_label
from app.domain.money import format_eur
from app.domain.recurring import add_months

MIN_MONTHS_FOR_STATS = 8
RHO_MIN = 0.6
P_MAX = 0.05
PERMUTATIONS = 400
PRICE_DRIFT = 0.05  # +5 % et au moins 1 € : une hausse qu'on signale
CENTS_TOLERANCE = 100

TOPICS = {
    "assurances": "Assurances",
    "logement": "Logement",
    "revenus": "Revenus",
    "aides": "Aides",
    "energie": "Énergie",
    "habitudes": "Habitudes",
}


# ------------------------------------------------------------------ entrées
@dataclass(frozen=True)
class Series:
    """Un prélèvement régulier (sens sortie), avec son historique (date, montant positif) du plus ancien au plus
    récent."""

    key: str
    merchant: str
    category: str
    cadence: str  # weekly | biweekly | monthly | quarterly | yearly
    amount_cents: int  # typique, positif
    history: tuple[tuple[date, int], ...]


@dataclass(frozen=True)
class Doc:
    id: int
    kind: str  # lease | insurance | payslip | rent_receipt | invoice
    day: date | None
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class Credit:
    id: int
    day: date
    amount_cents: int
    kind: str | None = None  # code d'aide (caf_ppa…) ou None pour un salaire


@dataclass(frozen=True)
class Declared:
    period: str  # mois de déclaration AAAA-MM
    deadline: date
    totals: Mapping[str, int]  # par type de ressource, centimes


@dataclass(frozen=True)
class EnergyLink:
    provider: str | None
    monthly_payment_cents: int | None
    recommended_monthly_cents: int | None
    regularisation_cents: int | None  # > 0 : rattrapage probable ; < 0 : remboursement probable


@dataclass(frozen=True)
class Inputs:
    today: date
    series: Sequence[Series] = ()
    docs: Sequence[Doc] = ()
    salary_credits: Sequence[Credit] = ()
    aid_credits: Sequence[Credit] = ()
    declared: Sequence[Declared] = ()
    energy: EnergyLink | None = None
    power: PowerCheck | None = None  # puissance maximale Linky face aux kVA souscrits (#33)
    monthly_spend: Mapping[str, Mapping[str, int]] = field(default_factory=dict)  # catégorie -> AAAA-MM -> centimes
    monthly_income: Mapping[str, int] = field(default_factory=dict)  # AAAA-MM -> centimes
    daily_variable_spend: Mapping[date, int] = field(default_factory=dict)  # dépenses variables par jour
    paydays: Sequence[date] = ()
    category_names: Mapping[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ sorties
@dataclass(frozen=True)
class Evidence:
    label: str
    value: str
    ref: str | None = None  # doc:<id> | txn:<id> | series:<key> | url:<…>


@dataclass(frozen=True)
class Finding:
    key: str
    kind: str
    level: str  # danger | warning | info
    topic: str
    title: str
    body: str
    evidence: tuple[Evidence, ...] = ()
    yearly_cents: int | None = None  # ce qui est en jeu sur un an
    action: Mapping[str, Any] | None = None
    confidence: float = 0.8
    statistical: bool = False


def _yearly(series: Series) -> int:
    per_year = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4, "yearly": 1}.get(series.cadence, 12)
    return series.amount_cents * per_year


def _fold(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


_CADENCE_FR = {
    "weekly": "par semaine",
    "biweekly": "toutes les deux semaines",
    "monthly": "par mois",
    "quarterly": "par trimestre",
    "yearly": "par an",
}


def _cadence_fr(c: str) -> str:
    return _CADENCE_FR.get(c, "par mois")


# ------------------------------------------------------------------ assurances : le doublon
@dataclass(frozen=True)
class Payer:
    series: Series
    insurer: str
    bank: bool
    confidence: float
    how: str


def home_insurance_payers(series: Iterable[Series], docs: Iterable[Doc]) -> list[Payer]:
    """Qui te prélève une assurance habitation : un assureur connu avec « habitation » dans le libellé (sûr), un
    assureur qu'une attestation habitation nomme (sûr), ou un assureur connu sans autre type reconnaissable
    (probable)."""
    insured_by_doc = {
        _fold(str(d.fields.get("assureur") or ""))
        for d in docs
        if d.kind == "insurance" and _home_doc(d) and d.fields.get("assureur")
    }
    out: list[Payer] = []
    for s in series:
        folded = _fold(s.merchant)
        ins = contracts.insurer_of(folded)
        kind = contracts.insurance_type(folded)
        if ins is None and s.category != "assurances":
            continue
        name = ins.name if ins else s.merchant.title()
        if kind == "habitation":
            out.append(Payer(s, name, bool(ins and ins.bank), 0.95, "« habitation » dans le libellé"))
        elif kind is None and ins is not None and _fold(ins.name) in insured_by_doc:
            out.append(Payer(s, name, ins.bank, 0.9, "nommé sur ton attestation habitation"))
        elif kind is None and ins is not None and s.category == "assurances" and 400 <= s.amount_cents <= 9_000:
            out.append(Payer(s, name, ins.bank, 0.55, "assureur connu, type non écrit sur le libellé"))
    return out


def _home_doc(d: Doc) -> bool:
    kind = str(d.fields.get("type") or "")
    garantie = _fold(str(d.fields.get("garantie") or ""))
    return kind == "habitation" or "HABITATION" in garantie or "MULTIRISQUE" in garantie


def duplicate_insurance(series: Sequence[Series], docs: Sequence[Doc]) -> list[Finding]:
    payers = home_insurance_payers(series, docs)
    by_insurer: dict[str, Payer] = {}
    for p in payers:
        cur = by_insurer.get(p.insurer)
        if cur is None or p.confidence > cur.confidence:
            by_insurer[p.insurer] = p
    distinct = sorted(by_insurer.values(), key=lambda p: -p.confidence)
    lease = next((d for d in docs if d.kind == "lease"), None)
    lease_insurer = str(lease.fields.get("assureur") or "") if lease else ""
    out: list[Finding] = []
    if len(distinct) >= 2:
        a, b = distinct[0], distinct[1]
        cheaper = min((a, b), key=lambda p: _yearly(p.series))
        sure = a.confidence >= 0.9 and b.confidence >= 0.9
        evidence = [
            Evidence(p.insurer, f"{format_eur(p.series.amount_cents)} {_cadence_fr(p.series.cadence)}, "
                     f"dernier prélèvement le {p.series.history[-1][0]:%d/%m/%Y} ({p.how})", f"series:{p.series.key}")
            for p in (a, b)
        ]  # fmt: skip
        if lease is not None and lease_insurer:
            evidence.append(Evidence("Assureur nommé dans le bail", lease_insurer, f"doc:{lease.id}"))
        bank_note = (
            " L'une est vendue par ta banque : c'est le cas classique du contrat souscrit avec le compte et oublié."
            if a.bank or b.bank
            else ""
        )
        out.append(
            Finding(
                key=f"dup_home:{'+'.join(sorted(_fold(p.insurer) for p in (a, b)))}",
                kind="duplicate_insurance",
                level="danger" if sure else "warning",
                topic="assurances",
                title="Deux assurances habitation ?" if sure else "Peut-être deux assurances habitation",
                body=(
                    f"{a.insurer} et {b.insurer} te prélèvent chacun une assurance qui ressemble à une assurance "
                    "habitation. "
                    f"Un seul contrat couvre le logement : le second coûte {format_eur(_yearly(cheaper.series))} "
                    f"par an pour rien.{bank_note} "
                    "Après un an de contrat, la résiliation est possible à tout moment (loi Hamon) ; garde celui que "
                    "le bail "
                    "exige ou le moins cher à garanties égales, et demande à l'autre le remboursement du trop-perçu."
                ),
                evidence=tuple(evidence),
                yearly_cents=_yearly(cheaper.series),
                action={
                    "template": "resilier_abonnement",
                    "label": "Préparer la résiliation",
                    "merchant": cheaper.series.merchant,
                },
                confidence=min(a.confidence, b.confidence),
            )
        )
    elif (
        len(distinct) == 1
        and lease is not None
        and lease_insurer
        and _fold(lease_insurer) != _fold(distinct[0].insurer)
    ):
        p = distinct[0]
        out.append(
            Finding(
                key=f"lease_insurer:{_fold(lease_insurer)}:{_fold(p.insurer)}",
                kind="lease_insurer_mismatch",
                level="info",
                topic="assurances",
                title="Le bail nomme un autre assureur que celui que tu paies",
                body=(
                    f"Ton bail cite {lease_insurer} ; c'est {p.insurer} qui te prélève. Si {lease_insurer} n'est plus "
                    "payé, c'est seulement une attestation à mettre à jour auprès du bailleur. Sinon, vérifie que tu "
                    "ne "
                    "paies pas deux contrats."
                ),
                evidence=(
                    Evidence("Bail", lease_insurer, f"doc:{lease.id}"),
                    Evidence(
                        p.insurer,
                        f"{format_eur(p.series.amount_cents)} {_cadence_fr(p.series.cadence)}",
                        f"series:{p.series.key}",
                    ),
                ),
                confidence=0.6,
            )
        )
    return out


# ------------------------------------------------------------------ logement : le bail face au loyer payé
def _fr2(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


IRL_WINDOW_DAYS = 45


def _applied_irl(
    lease: Doc,
    rent: Series,
    loyer: int,
    charges: int,
    start: date | None,
    revision: str,
    evidence: list[Evidence],
) -> Finding | None:
    """La révision elle-même, recalculée : l'IRL qu'elle applique face à l'indice publié par l'INSEE.

    Seulement quand Bank sait tout sans deviner : le trimestre de référence est écrit dans la clause, et l'ancien
    loyer est celui du bail, vu payé juste avant la hausse. Révision (loi ALUR) : nouveau loyer = ancien loyer ×
    IRL(trimestre de l'année de révision) / IRL(même trimestre un an plus tôt) ; l'IRL appliqué s'en déduit."""
    ref = contracts.quarter_in(_fold(revision))
    history = rent.history
    if ref is None or start is None or len(history) < 2:
        return None
    paid = history[-1][1]
    i = len(history) - 1
    while i > 0 and abs(history[i - 1][1] - paid) <= CENTS_TOLERANCE:
        i -= 1
    if i == 0 or abs(history[i - 1][1] - (loyer + charges)) > CENTS_TOLERANCE:
        return None  # pas de hausse vue, ou l'ancien loyer n'est pas celui du bail : on ne sait pas
    changed_on = history[i][0]
    # La hausse doit tomber à l'anniversaire du bail (à IRL_WINDOW_DAYS près, prélèvement en avance ou en retard) :
    # une hausse ailleurs dans l'année (régularisation des charges…) n'est pas lue comme une révision.
    years = round((changed_on - start).days / 365.25)
    if years < 1 or loyer <= 0 or abs((changed_on - add_months(start, 12 * years)).days) > IRL_WINDOW_DAYS:
        return None
    prev_q, cur_q = (ref[0] + years - 1, ref[1]), (ref[0] + years, ref[1])
    previous, published = contracts.irl_value(prev_q), contracts.irl_value(cur_q)
    if previous is None or published is None:
        return None
    new_rent = paid - charges
    applied = previous * new_rent / loyer
    allowed = round(loyer * published / previous)
    excess = new_rent - allowed
    if excess <= CENTS_TOLERANCE:
        return None
    with_charges = f" (charges du bail inchangées, {format_eur(charges)})" if charges else ""
    return Finding(
        key=f"rent_irl_applied:{lease.id}:{paid}",
        kind="rent_irl_misapplied",
        level="danger",
        topic="logement",
        title="Révision du loyer au-dessus de l'IRL publié",
        body=(
            f"Ta révision applique un IRL de {_fr2(applied)} ; l'indice publié pour ce trimestre "
            f"({contracts.quarter_label(cur_q)}) est {_fr2(published)} : {format_eur(excess)}/mois de trop"
            f"{with_charges}. Le loyer révisé ne peut pas dépasser {format_eur(allowed)} hors charges, soit "
            f"{format_eur(excess * 12)} par an : demande la régularisation au bailleur en citant l'indice de l'INSEE."
        ),
        evidence=(
            *evidence,
            Evidence("Avant la révision", f"{format_eur(history[i - 1][1])} le {history[i - 1][0]:%d/%m/%Y}", None),
            Evidence(
                "IRL",
                f"{contracts.quarter_label(prev_q)} : {_fr2(previous)} → {contracts.quarter_label(cur_q)} : "
                f"{_fr2(published)} ({contracts.irl_source(prev_q, cur_q)})",
                f"url:{contracts.IRL_SERIES_URL}",
            ),
        ),
        yearly_cents=excess * 12,
        confidence=0.85,
    )


def _rent_series(series: Sequence[Series]) -> Series | None:
    rents = [s for s in series if s.category == "logement" and s.cadence == "monthly"]
    return max(rents, key=lambda s: s.amount_cents) if rents else None


def rent_findings(series: Sequence[Series], docs: Sequence[Doc], today: date) -> list[Finding]:
    lease = next((d for d in docs if d.kind == "lease" and d.fields.get("loyer")), None)
    rent = _rent_series(series)
    out: list[Finding] = []
    if lease is None:
        return out
    loyer = int(lease.fields["loyer"])
    charges = int(lease.fields.get("charges") or 0)
    furnished = str(lease.fields.get("meuble") or "") == "oui"
    deposit = lease.fields.get("depot_garantie")
    if isinstance(deposit, int) and deposit > contracts.deposit_cap_cents(loyer, furnished):
        cap = contracts.deposit_cap_cents(loyer, furnished)
        out.append(
            Finding(
                key=f"deposit:{lease.id}",
                kind="deposit_over_cap",
                level="warning",
                topic="logement",
                title="Dépôt de garantie au-dessus du plafond légal",
                body=(
                    f"Le bail prévoit {format_eur(deposit)} de dépôt ; la loi du 6 juillet 1989 le limite à "
                    f"{'deux mois' if furnished else 'un mois'} de loyer hors charges, soit {format_eur(cap)}. "
                    f"L'excédent ({format_eur(deposit - cap)}) peut être réclamé au bailleur."
                ),
                evidence=(
                    Evidence("Bail", f"dépôt {format_eur(deposit)}, loyer {format_eur(loyer)}", f"doc:{lease.id}"),
                ),
                yearly_cents=deposit - cap,
                confidence=0.8,
            )
        )
    if rent is None or not rent.history:
        return out
    paid = rent.history[-1][1]
    expected = loyer + charges
    diff = paid - expected
    if abs(diff) <= CENTS_TOLERANCE:
        return out
    revision = str(lease.fields.get("revision") or "")
    start = lease.fields.get("date_debut")
    start_date = date.fromisoformat(str(start)) if start else None
    evidence = [
        Evidence(
            "Bail",
            f"loyer {format_eur(loyer)}" + (f" + charges {format_eur(charges)}" if charges else ""),
            f"doc:{lease.id}",
        ),
        Evidence(rent.merchant, f"{format_eur(paid)} le {rent.history[-1][0]:%d/%m/%Y}", f"series:{rent.key}"),
    ]
    if diff < 0:
        out.append(
            Finding(
                key=f"rent_low:{lease.id}:{paid}",
                kind="rent_mismatch",
                level="info",
                topic="logement",
                title="Tu paies moins que le bail",
                body=(
                    f"Le bail dit {format_eur(expected)} charges comprises, tu paies {format_eur(paid)}. Les charges "
                    "sont peut-être réglées à part, ou le loyer a été renégocié : vérifie que le bailleur ne te "
                    "réclamera "
                    "pas l'écart plus tard."
                ),
                evidence=tuple(evidence),
                confidence=0.6,
            )
        )
        return out
    if not revision:
        out.append(
            Finding(
                key=f"rent_high_norev:{lease.id}:{paid}",
                kind="rent_mismatch",
                level="warning",
                topic="logement",
                title="Loyer payé au-dessus du bail, sans clause de révision",
                body=(
                    f"Tu paies {format_eur(paid)} ; le bail dit {format_eur(expected)}"
                    f"{' charges comprises' if charges else ''} et ne prévoit pas de révision : sans clause, le loyer "
                    f"ne peut pas augmenter en cours de bail (loi du 6 juillet 1989, art. 17-1). L'écart fait "
                    f"{format_eur(diff * 12)} par an."
                ),
                evidence=tuple(evidence),
                yearly_cents=diff * 12,
                confidence=0.7,
            )
        )
        return out
    allowed = None
    if start_date is not None:
        allowed = contracts.allowed_rent_cents(
            loyer, start_date, today, reference=contracts.quarter_in(_fold(revision))
        )
    if allowed is None:
        out.append(
            Finding(
                key=f"rent_high_rev:{lease.id}:{paid}",
                kind="rent_mismatch",
                level="info",
                topic="logement",
                title="Loyer payé au-dessus du bail : révision à vérifier",
                body=(
                    f"Tu paies {format_eur(paid)} pour un bail à {format_eur(expected)}. La clause de révision "
                    f"({revision}) "
                    "l'autorise une fois par an, dans la limite de la variation de l'indice de référence des loyers : "
                    "vérifie l'avis de révision du bailleur."
                ),
                evidence=tuple(evidence),
                confidence=0.6,
            )
        )
        return out
    applied = _applied_irl(lease, rent, loyer, charges, start_date, revision, evidence)
    if applied is not None:
        out.append(applied)
        return out
    max_rent, ref_q, cur_q = allowed
    max_total = max_rent + charges
    ratio = contracts.irl_ratio(ref_q, cur_q) or 1.0
    evidence.append(
        Evidence(
            "IRL",
            f"{contracts.quarter_label(ref_q)} → {contracts.quarter_label(cur_q)} : {(ratio - 1) * 100:+.2f} % "
            f"({contracts.irl_source(ref_q, cur_q)})",
            f"url:{contracts.IRL_SERIES_URL}",
        )
    )
    if paid > max_total + CENTS_TOLERANCE:
        out.append(
            Finding(
                key=f"rent_over_irl:{lease.id}:{paid}",
                kind="rent_over_irl",
                level="danger",
                topic="logement",
                title="Loyer au-dessus de ce que l'IRL autorise",
                body=(
                    f"Avec la clause de révision du bail, le loyer ne peut pas dépasser {format_eur(max_rent)} hors "
                    "charges "
                    f"aujourd'hui ({format_eur(max_total)} avec les charges) ; tu paies {format_eur(paid)}. Le "
                    "trop-payé fait "
                    f"{format_eur((paid - max_total) * 12)} par an : demande au bailleur la régularisation, en "
                    "citant l'IRL."
                ),
                evidence=tuple(evidence),
                yearly_cents=(paid - max_total) * 12,
                confidence=0.75,
            )
        )
    else:
        out.append(
            Finding(
                key=f"rent_irl_ok:{lease.id}:{paid}",
                kind="rent_revised",
                level="info",
                topic="logement",
                title="Loyer révisé dans la limite de l'IRL",
                body=(
                    f"Tu paies {format_eur(paid)} pour un bail signé à {format_eur(expected)} : la hausse "
                    f"({format_eur(diff)} par mois) reste sous le plafond de l'indice de référence des loyers "
                    f"({format_eur(max_total)})."
                ),
                evidence=tuple(evidence),
                confidence=0.7,
            )
        )
    return out


# ------------------------------------------------------------------ primes et abonnements qui montent
DRIFT_CATEGORIES = ("assurances", "telecom", "abonnements", "energie")


def price_drift(series: Sequence[Series]) -> list[Finding]:
    out: list[Finding] = []
    for s in series:
        if s.category not in DRIFT_CATEGORIES or len(s.history) < 4:
            continue
        before = [a for _, a in s.history[:-1]]
        base = int(median(before[-6:]))
        last_day, last = s.history[-1]
        if base <= 0 or last < base * (1 + PRICE_DRIFT) or last - base < 100:
            continue
        # une hausse qui dure : le dernier montant n'est pas un accident isolé si le précédent était déjà au-dessus
        steady = len(s.history) >= 2 and s.history[-2][1] >= base * (1 + PRICE_DRIFT / 2)
        pct = (last - base) / base * 100
        yearly = (last - base) * {"monthly": 12, "quarterly": 4, "yearly": 1}.get(s.cadence, 12)
        ins = contracts.insurer_of(_fold(s.merchant))
        if s.category == "assurances" or ins:
            tip = (
                "Après un an de contrat, tu peux résilier à tout moment (loi Hamon) ; avant, à l'échéance avec le "
                "préavis "
                "de l'avis d'échéance (loi Chatel). Un devis concurrent suffit souvent à faire baisser la prime."
            )
        elif s.category == "telecom":
            tip = (
                "Une hausse de prix te donne le droit de résilier sans frais dans les quatre mois (code de la "
                "consommation, art. L224-33)."
            )
        else:
            tip = "Vérifie l'avis de hausse ; une offre concurrente ou une renégociation se tente en quelques minutes."
        out.append(
            Finding(
                key=f"drift:{s.key}:{last}",
                kind="price_drift",
                level="info" if not steady else "warning",
                topic="assurances"
                if s.category == "assurances"
                else "logement"
                if s.category == "energie"
                else "habitudes",
                title=f"{s.merchant.title()} : {pct:+.0f} %",
                body=(
                    f"Le prélèvement est passé de {format_eur(base)} à {format_eur(last)} ({last_day:%d/%m/%Y}), soit "
                    f"{format_eur(yearly)} de plus par an. {tip}"
                ),
                evidence=(Evidence(s.merchant, f"{format_eur(base)} → {format_eur(last)}", f"series:{s.key}"),),
                yearly_cents=yearly,
                confidence=0.75 if steady else 0.6,
            )
        )
    return out


# ------------------------------------------------------------------ documents face aux relevés
def salary_vs_payslips(docs: Sequence[Doc], credits: Sequence[Credit]) -> list[Finding]:
    out: list[Finding] = []
    for d in docs:
        if d.kind != "payslip":
            continue
        net = d.fields.get("net_paye")
        month = str(d.fields.get("periode") or "")
        if not isinstance(net, int) or len(month) != 7:
            continue
        first = date.fromisoformat(month + "-01")
        # la paie du mois tombe en fin de mois ou dans les premiers jours du suivant
        window = [
            c for c in credits if first + timedelta(days=15) <= c.day <= add_months(first, 1) + timedelta(days=12)
        ]
        if not window:
            continue
        closest = min(window, key=lambda c: abs(c.amount_cents - net))
        gap = closest.amount_cents - net
        if abs(gap) <= CENTS_TOLERANCE:
            continue
        out.append(
            Finding(
                key=f"payslip:{d.id}:{closest.id}",
                kind="salary_mismatch",
                level="info" if abs(gap) < net * 0.1 else "warning",
                topic="revenus",
                title=f"Bulletin de {month} : {format_eur(net)}, reçu {format_eur(closest.amount_cents)}",
                body=(
                    f"Le net à payer du bulletin et le virement le plus proche diffèrent de {format_eur(abs(gap))}. "
                    "Acompte, saisie, retenue, ou erreur de paie : demande le détail à l'employeur si rien ne "
                    "l'explique."
                ),
                evidence=(
                    Evidence("Bulletin", format_eur(net), f"doc:{d.id}"),
                    Evidence(
                        "Virement", f"{format_eur(closest.amount_cents)} le {closest.day:%d/%m/%Y}", f"txn:{closest.id}"
                    ),
                ),
                confidence=0.65,
            )
        )
    return out


def receipts_vs_rent(docs: Sequence[Doc], series: Sequence[Series]) -> list[Finding]:
    rent = _rent_series(series)
    if rent is None:
        return []
    by_month = {f"{d.year:04d}-{d.month:02d}": a for d, a in rent.history}
    out: list[Finding] = []
    for d in docs:
        if d.kind != "rent_receipt":
            continue
        amount, month = d.fields.get("montant"), str(d.fields.get("periode") or "")
        if not isinstance(amount, int) or month not in by_month or abs(by_month[month] - amount) <= CENTS_TOLERANCE:
            continue
        out.append(
            Finding(
                key=f"receipt:{d.id}",
                kind="receipt_mismatch",
                level="info",
                topic="logement",
                title=f"Quittance de {month} : {format_eur(amount)}, payé {format_eur(by_month[month])}",
                body=(
                    "La quittance et le virement de loyer du même mois ne disent pas la même chose : demande une "
                    "quittance corrigée (elle sert de justificatif, pour l'APL notamment)."
                ),
                evidence=(
                    Evidence("Quittance", format_eur(amount), f"doc:{d.id}"),
                    Evidence(rent.merchant, format_eur(by_month[month]), f"series:{rent.key}"),
                ),
                confidence=0.7,
            )
        )
    return out


# ------------------------------------------------------------------ aides : déclaré face à versé
def declared_vs_paid(declared: Sequence[Declared], aid_credits: Sequence[Credit]) -> list[Finding]:
    """Ce que tu as déclaré à la CAF un trimestre, et le versement qui a suivi, comparés au trimestre précédent.
    Pas de recalcul du droit (c'est OpenFisca qui le fait ailleurs) : on repère l'incohérence de sens."""
    out: list[Finding] = []
    caf = sorted((c for c in aid_credits if c.kind in ("caf_ppa", "caf_rsa")), key=lambda c: c.day)
    rows = sorted(declared, key=lambda d: d.period)
    for prev, cur in pairwise(rows):
        before = sum(prev.totals.values())
        after = sum(cur.totals.values())
        if before <= 0 and after <= 0:
            continue
        paid_prev = _payment_after(caf, prev.deadline)
        paid_cur = _payment_after(caf, cur.deadline)
        if paid_prev is None or paid_cur is None:
            continue
        income_change = (after - before) / max(before, 1)
        paid_change = (paid_cur.amount_cents - paid_prev.amount_cents) / max(paid_prev.amount_cents, 1)
        evidence = (
            Evidence(f"Déclaré ({prev.period})", format_eur(before), None),
            Evidence(f"Déclaré ({cur.period})", format_eur(after), None),
            Evidence(
                "Versé ensuite",
                f"{format_eur(paid_prev.amount_cents)} puis {format_eur(paid_cur.amount_cents)}",
                f"txn:{paid_cur.id}",
            ),
        )
        if abs(income_change) >= 0.10 and abs(paid_change) < 0.02:
            out.append(
                Finding(
                    key=f"declared_flat:{cur.period}",
                    kind="declared_vs_paid",
                    level="warning",
                    topic="aides",
                    title="Ressources déclarées en nette variation, versement CAF inchangé",
                    body=(
                        f"Tes ressources déclarées ont bougé de {income_change * 100:+.0f} % d'un trimestre à "
                        "l'autre, le "
                        "versement qui a suivi n'a pas changé. La déclaration a-t-elle bien été prise en compte ? "
                        "Vérifie "
                        "dans Mon Compte : un indu se forme vite, et il se rembourse."
                    ),
                    evidence=evidence,
                    confidence=0.6,
                )
            )
        elif income_change <= 0.02 and paid_change <= -0.30:
            out.append(
                Finding(
                    key=f"paid_drop:{cur.period}",
                    kind="declared_vs_paid",
                    level="warning",
                    topic="aides",
                    title="Versement CAF en forte baisse sans hausse de ressources",
                    body=(
                        f"Le versement a baissé de {abs(paid_change) * 100:.0f} % alors que tes ressources déclarées "
                        "sont "
                        "stables. Changement de situation pris en compte, régularisation d'un indu, ou erreur : "
                        "demande "
                        "le détail du calcul (notification de droits), et contestez sous deux mois si besoin."
                    ),
                    evidence=evidence,
                    confidence=0.6,
                )
            )
    return out


def _payment_after(caf: Sequence[Credit], deadline: date) -> Credit | None:
    window = [c for c in caf if deadline < c.day <= deadline + timedelta(days=40)]
    return window[0] if window else None


# ------------------------------------------------------------------ énergie : mensualité face à consommation
def energy_findings(energy: EnergyLink | None) -> list[Finding]:
    if energy is None or energy.regularisation_cents is None or energy.monthly_payment_cents is None:
        return []
    reg = energy.regularisation_cents
    if abs(reg) < 5_000:
        return []
    who = energy.provider or "ton fournisseur"
    if reg > 0:
        return [
            Finding(
                key=f"energy_catchup:{reg // 1000}",
                kind="energy_vs_bills",
                level="warning",
                topic="energie",
                title=f"Électricité : rattrapage probable de {format_eur(reg)}",
                body=(
                    f"Ta consommation Linky vaut environ {format_eur(energy.recommended_monthly_cents or 0)} par "
                    "mois au tarif "
                    f"réglé ; {who} te prélève {format_eur(energy.monthly_payment_cents)}. L'écart se paiera à la "
                    "régularisation annuelle : relève la mensualité, ou mets la différence de côté."
                ),
                yearly_cents=reg,
                confidence=0.7,
            )
        ]
    return [
        Finding(
            key=f"energy_refund:{-reg // 1000}",
            kind="energy_vs_bills",
            level="info",
            topic="energie",
            title=f"Électricité : tu paies trop, remboursement probable de {format_eur(-reg)}",
            body=(
                f"Ta consommation vaut environ {format_eur(energy.recommended_monthly_cents or 0)} par mois ; {who} "
                "prélève "
                f"{format_eur(energy.monthly_payment_cents)}. Baisser la mensualité te rend cet argent tout de suite "
                "plutôt qu'à la régularisation."
            ),
            yearly_cents=-reg,
            confidence=0.7,
        )
    ]


# ------------------------------------------------------------------ énergie : puissance souscrite trop haute (#33)
def power_findings(check: PowerCheck | None) -> list[Finding]:
    """Un constat seulement quand douze mois de puissance maximale restent au moins 1 kVA sous une puissance plus
    basse : le message et l'économie viennent tels quels du calcul (relevés Linky, grille de la CRE datée)."""
    if check is None or check.status != "too_high" or check.target_kva is None or check.max_va is None:
        return []
    saving = check.yearly_saving_cents
    option = OPTIONS.get(check.option or "", "")
    evidence = [
        Evidence(
            "Puissance maximale sur 12 mois",
            f"{kva_label(check.max_va)} le {check.max_day:%d/%m/%Y}" if check.max_day else kva_label(check.max_va),
        ),
        Evidence("Puissance souscrite", f"{check.subscribed_kva} kVA ({option})"),
        Evidence("Puissance qui suffirait", f"{check.target_kva} kVA (1 kVA de marge au moins)"),
    ]
    if check.grid_source:
        evidence.append(Evidence("Grille", check.grid_source, f"url:{check.grid_url}" if check.grid_url else None))
    title = f"Puissance souscrite trop haute : {check.subscribed_kva} kVA pour un maximum de {kva_label(check.max_va)}"
    return [
        Finding(
            key=f"power_too_high:{check.subscribed_kva}:{check.target_kva}",
            kind="power_too_high",
            level="warning" if (saving or 0) >= 2_000 else "info",
            topic="energie",
            title=title,
            body=check.message,
            evidence=tuple(evidence),
            yearly_cents=saving,
            confidence=0.85,
        )
    ]


# ------------------------------------------------------------------ habitudes : pistes statistiques
def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return float(sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sxx * syy) ** 0.5)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return _pearson(_ranks(x), _ranks(y))


def permutation_p(x: Sequence[float], y: Sequence[float], rho: float, seed: str, n: int = PERMUTATIONS) -> float:
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))  # noqa: S311  # nosec B311
    ry = list(y)
    hits = 0
    for _ in range(n):
        rng.shuffle(ry)
        if abs(spearman(x, ry)) >= abs(rho):
            hits += 1
    return (hits + 1) / (n + 1)


def statistical_links(
    monthly_spend: Mapping[str, Mapping[str, int]], monthly_income: Mapping[str, int], names: Mapping[str, str]
) -> list[Finding]:
    """Entre ton revenu du mois et chaque catégorie variable, puis entre catégories : les liens de rang nets et
    peu probables par hasard. Deux pistes au plus, formulées comme des pistes."""
    months = sorted(set(monthly_income) | {m for cat in monthly_spend.values() for m in cat})
    months = [m for m in months if m in monthly_income][-18:]
    if len(months) < MIN_MONTHS_FOR_STATS:
        return []
    columns: dict[str, list[float]] = {"revenu": [float(monthly_income.get(m, 0)) for m in months]}
    for cat, by_month in monthly_spend.items():
        col = [float(by_month.get(m, 0)) for m in months]
        if sum(1 for v in col if v > 0) >= MIN_MONTHS_FOR_STATS - 2:
            columns[cat] = col
    keys = list(columns)
    found: list[tuple[float, float, str, str]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            rho = spearman(columns[a], columns[b])
            if abs(rho) < RHO_MIN:
                continue
            p = permutation_p(columns[a], columns[b], rho, f"{a}|{b}")
            if p <= P_MAX:
                found.append((abs(rho), rho, a, b))
    found.sort(reverse=True)
    out: list[Finding] = []
    for _, rho, a, b in found[:2]:
        la, lb = names.get(a, a) if a != "revenu" else "ton revenu du mois", names.get(b, b)
        direction = "montent ensemble" if rho > 0 else "vont en sens inverse"
        out.append(
            Finding(
                key=f"stat:{a}:{b}",
                kind="statistical_link",
                level="info",
                topic="habitudes",
                title=f"Piste : {la} et {lb} {direction}",
                body=(
                    f"Sur {len(months)} mois, quand {la} est plus haut, {lb} l'est aussi"
                    if rho > 0
                    else f"Sur {len(months)} mois, quand {la} est plus haut, {lb} est plus bas"
                )
                + f" (corrélation de rang {rho:+.2f}, peu probable par hasard). Ce n'est pas une cause, c'est une "
                "régularité à regarder : utile pour poser une enveloppe, rien de plus.",
                confidence=min(0.6, abs(rho)),
                statistical=True,
            )
        )
    return out


def payday_concentration(
    daily_spend: Mapping[date, int], paydays: Sequence[date], names: Mapping[str, str]
) -> list[Finding]:
    """La part de tes dépenses variables qui part dans les sept jours qui suivent la paie, sur au moins six paies."""
    days = sorted(d for d in paydays)
    if len(days) < 6 or not daily_spend:
        return []
    first, last = days[0], days[-1] + timedelta(days=30)
    total = sum(v for d, v in daily_spend.items() if first <= d <= last)
    if total <= 0:
        return []
    after = 0
    for p in days:
        after += sum(v for d, v in daily_spend.items() if p <= d < p + timedelta(days=7))
    share = after / total
    if share < 0.45:
        return []
    return [
        Finding(
            key=f"payday:{round(share * 100)}",
            kind="payday_concentration",
            level="info",
            topic="habitudes",
            title=f"{round(share * 100)} % de tes dépenses variables partent dans la semaine qui suit la paie",
            body=(
                f"Sur {len(days)} paies, près de la moitié de tes dépenses du mois tombe dans les sept jours qui "
                "suivent "
                "le virement (une semaine sur quatre). Rien d'anormal — c'est le moment où l'on respire — mais c'est "
                "souvent là que le mois se décide : un virement d'épargne le jour de paie, avant le reste, change la "
                "fin "
                "du mois."
            ),
            confidence=0.6,
            statistical=True,
        )
    ]


# ------------------------------------------------------------------ orchestration
LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}


def analyse(inputs: Inputs) -> list[Finding]:
    out: list[Finding] = []
    out += duplicate_insurance(inputs.series, inputs.docs)
    out += rent_findings(inputs.series, inputs.docs, inputs.today)
    out += price_drift(inputs.series)
    out += salary_vs_payslips(inputs.docs, inputs.salary_credits)
    out += receipts_vs_rent(inputs.docs, inputs.series)
    out += declared_vs_paid(inputs.declared, inputs.aid_credits)
    out += energy_findings(inputs.energy)
    out += power_findings(inputs.power)
    out += statistical_links(inputs.monthly_spend, inputs.monthly_income, inputs.category_names)
    out += payday_concentration(inputs.daily_variable_spend, inputs.paydays, inputs.category_names)
    seen: set[str] = set()
    unique = []
    for f in out:
        if f.key in seen:
            continue
        seen.add(f.key)
        unique.append(f)
    unique.sort(key=lambda f: (LEVEL_ORDER.get(f.level, 3), -(f.yearly_cents or 0), f.key))
    return unique


def finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        "key": f.key,
        "kind": f.kind,
        "level": f.level,
        "topic": f.topic,
        "topic_label": TOPICS.get(f.topic, f.topic),
        "title": f.title,
        "body": f.body,
        "evidence": [{"label": e.label, "value": e.value, "ref": e.ref} for e in f.evidence],
        "yearly_cents": f.yearly_cents,
        "action": dict(f.action) if f.action else None,
        "confidence": round(f.confidence, 2),
        "statistical": f.statistical,
    }
