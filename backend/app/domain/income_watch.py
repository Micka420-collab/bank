"""Revenus et prestations : ce qui aurait dû bouger et n'a pas bougé, ce que ton taux de prélèvement à la source
dit face à tes revenus de l'année, et le chèque énergie.

Trois constats, rangés avec les autres corrélations (même forme, mêmes décisions) :
* une revalorisation légale (prestations au 1er avril, ARE au 1er juillet, retraites de base au 1er janvier) qui ne
  se voit pas : le premier versement après la date est identique au centime au précédent. Bank ne dit jamais
  qu'un montant est dû : il renvoie à la notification et au délai de recours ;
* un taux de prélèvement décalé de tes revenus de l'année : estimation au barème, avec ses entrées, comparée à la
  règle de modulation (écart de plus de 5 %), ou solde de septembre à provisionner ;
* le chèque énergie : éligible d'après la simulation, et rien de vu dans tes relevés.

Chaque chiffre vient d'une donnée datée et sourcée (rates.json, recouvert de ce que Bank relève) ou de tes pièces.
Module pur.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from app.domain import aids, invest
from app.domain.correlations import Evidence, Finding
from app.domain.money import format_eur
from app.domain.recurring import add_months

# Un versement mensuel est payé à terme échu : celui du mois de la revalorisation arrive le mois suivant (le 5 à la
# CAF, vers le 9 pour les retraites de base). Quelques jours d'avance quand le jour tombe un week-end.
FIRST_PAYMENT_MARGIN_DAYS = 6
MAX_GAP_DAYS = 45  # au-delà, les deux versements comparés ne sont pas deux mois qui se suivent
RECENT_DAYS = 183  # un constat sur un versement plus ancien n'aide plus personne
COMPLEMENTARY = re.compile(r"AGIRC|ARRCO|IRCANTEC|COMPLEMENTAIRE|\bRAFP\b")
KIND_WORDS = {
    "prestations": "Les prestations sociales sont revalorisées",
    "pensions": "Les retraites de base sont revalorisées",
    "are": "Les allocations chômage sont revalorisées",
}
TOPIC_OF = {"prestations": "aides", "pensions": "revenus", "are": "revenus"}
_NAMES = {k.code: k.name for k in aids.AID_KINDS}
_ORG_OF = {k.code: k.organism for k in aids.AID_KINDS}
_ORG_NAMES = {o.code: o.name for o in aids.ORGANISMS}
_ORG_ARTICLE = {
    "caf": "la CAF",
    "msa": "la MSA",
    "cpam": "l'Assurance Maladie",
    "retraite": "ta caisse de retraite",
    "france_travail": "France Travail",
}
_CSS_ORGS = {"caf", "msa", "cpam", "retraite"}  # caisses de sécurité sociale : commission de recours amiable


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


def _dec(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw).replace(",", ".").replace("%", "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def fr_percent(value: Decimal) -> str:
    """« 0,8 % », « 7,4 % », « 12 % »."""
    text = f"{value.normalize():f}"
    return text.replace(".", ",") + " %"


def _eur0(cents: int) -> str:
    """Un ordre de grandeur à l'euro : « 1 234 € »."""
    return f"{round(cents / 100):,}".replace(",", " ") + " €"


# ------------------------------------------------------------------ revalorisations
@dataclass(frozen=True)
class Revaluation:
    id: str
    kind: str  # prestations | pensions | are
    effective: date
    rate_percent: Decimal
    applies_to: frozenset[str]
    source: str
    url: str


@dataclass(frozen=True)
class AppealRule:
    months: int
    source: str
    url: str


@dataclass(frozen=True)
class AidCredit:
    id: int
    day: date
    amount_cents: int
    kind: str
    complementary: bool = False  # retraite complémentaire (Agirc-Arrco…) : pas revalorisée avec la base


def load_revaluations(raw: Mapping[str, Any]) -> tuple[list[Revaluation], AppealRule | None]:
    section = raw.get("revaluations") or {}
    out: list[Revaluation] = []
    for item in section.get("items") or []:
        try:
            rate = _dec(item["rate_percent"])
            if rate is None:
                continue
            out.append(
                Revaluation(
                    str(item["id"]),
                    str(item["kind"]),
                    date.fromisoformat(str(item["effective"])),
                    rate,
                    frozenset(str(k) for k in item["applies_to"]),
                    str(item["source"]),
                    str(item["url"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    appeal = None
    if section.get("appeal_months") and section.get("appeal_url"):
        appeal = AppealRule(
            int(section["appeal_months"]), str(section.get("appeal_source") or ""), str(section["appeal_url"])
        )
    return out, appeal


def revaluations_from_bmaf(
    points: Iterable[tuple[date, Decimal, str]], known: Sequence[Revaluation]
) -> list[Revaluation]:
    """La base des allocations familiales relue dans OpenFisca : chaque hausse postérieure à la dernière
    revalorisation de rates.json devient une revalorisation des prestations, datée et sourcée par ces deux
    valeurs."""
    dated = sorted((r for r in known if r.kind == "prestations"), key=lambda r: r.effective)
    if not dated:
        return []
    template, last_known = dated[-1], dated[-1].effective  # seules les hausses plus récentes que le fichier
    ordered = sorted(points)
    out: list[Revaluation] = []
    for (_d0, v0, _u0), (d1, v1, url) in pairwise(ordered):
        if d1 <= last_known or v0 <= 0 or v1 <= v0:
            continue
        rate = ((v1 / v0 - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        out.append(
            Revaluation(
                f"prestations-{d1:%Y-%m}",
                "prestations",
                d1,
                rate,
                template.applies_to,
                f"Base mensuelle des allocations familiales relue dans OpenFisca : {format_eur(round(v0 * 100))} "
                f"puis {format_eur(round(v1 * 100))} au {d1:%d/%m/%Y}",
                url,
            )
        )
    return out


def _same_month(items: Sequence[AidCredit], day: date) -> int:
    return sum(1 for c in items if (c.day.year, c.day.month) == (day.year, day.month))


def missing_revaluations(
    revaluations: Sequence[Revaluation], credits: Sequence[AidCredit], today: date, appeal: AppealRule | None
) -> list[Finding]:
    out: list[Finding] = []
    for r in revaluations:
        if r.rate_percent <= 0 or r.effective > today:
            continue  # pas de revalorisation cette année-là (ARE 2026) : un versement identique est normal
        due_from = add_months(r.effective, 1) - timedelta(days=FIRST_PAYMENT_MARGIN_DAYS)
        for kind in sorted(r.applies_to):
            items = sorted((c for c in credits if c.kind == kind and not c.complementary), key=lambda c: c.day)
            after = next((c for c in items if c.day >= due_from), None)
            before = [c for c in items if c.day < due_from]
            if after is None or not before or after.day > today or (today - after.day).days > RECENT_DAYS:
                continue
            prev = before[-1]
            if (after.day - prev.day).days > MAX_GAP_DAYS:
                continue
            # un rappel ou un acompte dans l'un des deux mois brouille la comparaison : on ne dit rien
            if _same_month(items, after.day) != 1 or _same_month(items, prev.day) != 1:
                continue
            if after.amount_cents != prev.amount_cents:
                continue
            out.append(_reval_finding(r, kind, prev, after, appeal))
    return out


def _reval_finding(r: Revaluation, kind: str, prev: AidCredit, after: AidCredit, appeal: AppealRule | None) -> Finding:
    org = _ORG_OF.get(kind, "")
    org_name = _ORG_ARTICLE.get(org) or _ORG_NAMES.get(org) or "l'organisme"
    name = _NAMES.get(kind, kind)
    if org in _CSS_ORGS and appeal is not None:
        recourse = (
            f"Si rien ne l'explique, tu as {appeal.months} mois après la notification pour saisir la commission de "
            f"recours amiable de {org_name}."
        )
    else:
        recourse = "Si rien ne l'explique, la notification indique la voie et le délai de recours."
    evidence = [
        Evidence("Avant", f"{format_eur(prev.amount_cents)} le {prev.day:%d/%m/%Y}", f"txn:{prev.id}"),
        Evidence("Après", f"{format_eur(after.amount_cents)} le {after.day:%d/%m/%Y}", f"txn:{after.id}"),
        Evidence(
            "Revalorisation", f"{fr_percent(r.rate_percent)} au {r.effective:%d/%m/%Y} — {r.source}", f"url:{r.url}"
        ),
    ]
    if org in _CSS_ORGS and appeal is not None:
        evidence.append(Evidence("Recours", f"{appeal.months} mois après la notification", f"url:{appeal.url}"))
    return Finding(
        key=f"reval:{r.id}:{kind}",
        kind="revaluation_missing",
        level="warning",
        topic=TOPIC_OF.get(r.kind, "aides"),
        title=f"{name} : versement du {after.day:%d/%m/%Y} identique au centime au précédent",
        body=(
            f"{KIND_WORDS.get(r.kind, 'Ce versement est revalorisé')} de {fr_percent(r.rate_percent)} au "
            f"{r.effective:%d/%m/%Y} ; ton versement du {after.day:%d/%m/%Y} est au centime près celui du "
            f"{prev.day:%d/%m/%Y} ({format_eur(after.amount_cents)}). Une baisse de ressources prise en compte, une "
            f"retenue sur un trop-perçu ou un changement de situation peuvent l'expliquer : lis la notification de "
            f"{org_name} dans ton espace en ligne. {recourse}"
        ),
        evidence=tuple(evidence),
        confidence=0.6,
    )


# ------------------------------------------------------------------ impôt : modèle au barème
@dataclass(frozen=True)
class TaxModel:
    refs: invest.References
    decote_single_cents: int
    decote_couple_cents: int
    decote_rate: Decimal  # en %
    qf_cap_half_part_cents: int
    deduction_rate: Decimal  # en %
    deduction_min_cents: int
    deduction_max_cents: int
    deduction_year: int
    source_url: str

    @property
    def label(self) -> str:
        return (
            f"barème des revenus {self.refs.income_tax_incomes_year}, abattement de 10 % (bornes des revenus "
            f"{self.deduction_year}), décote, plafond du quotient familial (cas général) ; sans réductions ni crédits "
            "d'impôt"
        )


def load_tax_model(raw: Mapping[str, Any]) -> TaxModel | None:
    tax = raw.get("income_tax") or {}
    try:
        decote, ded = tax["decote"], tax["professional_deduction"]
        return TaxModel(
            refs=invest.load_references(),
            decote_single_cents=int(decote["single_cents"]),
            decote_couple_cents=int(decote["couple_cents"]),
            decote_rate=Decimal(str(decote["rate_percent"])),
            qf_cap_half_part_cents=int(tax["qf_cap_half_part_cents"]),
            deduction_rate=Decimal(str(ded["rate_percent"])),
            deduction_min_cents=int(ded["min_cents"]),
            deduction_max_cents=int(ded["max_cents"]),
            deduction_year=int(ded["incomes_year"]),
            source_url=str(decote.get("url") or ""),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def estimate_tax(
    model: TaxModel,
    salaries_cents: Sequence[int],
    parts: Decimal,
    couple: bool,
    deductions_cents: Sequence[int | None] | None = None,
) -> int:
    """Impôt au barème sur des salaires imposables annuels (un par déclarant) : abattement de 10 % borné, ou frais
    réels pour le déclarant dont `deductions_cents` donne le montant, quotient familial plafonné (cas général),
    décote. Un ordre de grandeur, pas l'avis."""
    net = 0
    for i, s in enumerate(salaries_cents):
        if s <= 0:
            continue
        real = deductions_cents[i] if deductions_cents is not None and i < len(deductions_cents) else None
        if real is not None:
            net += s - min(max(real, 0), s)
            continue
        deduction = int((Decimal(s) * model.deduction_rate / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        deduction = min(max(deduction, model.deduction_min_cents), model.deduction_max_cents, s)
        net += s - deduction
    base = Decimal(2 if couple else 1)
    brut = invest.income_tax(net, parts, model.refs)
    if parts > base:
        half_parts = int((parts - base) * 2)
        brut = max(brut, invest.income_tax(net, base, model.refs) - half_parts * model.qf_cap_half_part_cents)
    ceiling = model.decote_couple_cents if couple else model.decote_single_cents
    decote = Decimal(ceiling) - Decimal(brut) * model.decote_rate / 100
    decote_cents = max(0, min(brut, int(decote.quantize(Decimal(1), rounding=ROUND_HALF_UP))))
    return max(0, brut - decote_cents)


# ------------------------------------------------------------------ taux de prélèvement à la source
TAXABLE_UPLIFT = Decimal("1.03")  # net imposable ≈ net versé + 3 % (CSG et CRDS non déductibles), comme invest
MIN_MONTHS = 3
MIN_BALANCE_CENTS = 5_000  # sous 50 €, le solde de septembre n'est pas une surprise à provisionner


@dataclass(frozen=True)
class WithholdingRule:
    decrease_gap_percent: Decimal
    decrease_source: str
    decrease_url: str
    balance_single_max_cents: int
    balance_source: str
    balance_url: str


def load_withholding_rule(raw: Mapping[str, Any]) -> WithholdingRule | None:
    w = raw.get("withholding_tax") or {}
    try:
        return WithholdingRule(
            Decimal(str(w["decrease_gap_percent"])),
            str(w["decrease_source"]),
            str(w["decrease_url"]),
            int(w["balance_single_max_cents"]),
            str(w["balance_source"]),
            str(w["balance_url"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


@dataclass(frozen=True)
class RateSeen:
    percent: Decimal
    origin: str  # « bulletin de paie de 09/2026 », « avis d'impôt sur les revenus 2025 »
    ref: str | None = None


@dataclass(frozen=True)
class MonthIncome:
    month: str  # AAAA-MM
    taxable_cents: int
    from_payslip: bool


@dataclass(frozen=True)
class PasInputs:
    year: int
    rate: RateSeen
    months: Sequence[MonthIncome]
    parts: Decimal
    parts_origin: str
    couple: bool
    partner_taxable_monthly_cents: int = 0
    partner_origin: str | None = None


@dataclass(frozen=True)
class PasEstimate:
    inputs: PasInputs
    own_yearly_cents: int
    partner_yearly_cents: int
    tax_cents: int
    withheld_cents: int  # ce que ton taux prélèverait sur l'année, sans modulation
    estimated_percent: Decimal
    model_label: str
    model_url: str

    @property
    def total_cents(self) -> int:
        return self.own_yearly_cents + self.partner_yearly_cents

    @property
    def balance_cents(self) -> int:
        return self.tax_cents - self.withheld_cents


def taxable_from_credit(net_paid_cents: int, rate: Decimal) -> int:
    """Net imposable d'un mois vu par son seul virement : net versé + impôt prélevé au taux, majoré de 3 %.
    NI = 1,03 × net versé / (1 − 1,03 × taux)."""
    denominator = 1 - TAXABLE_UPLIFT * rate / 100
    if denominator <= 0:
        return 0
    value = TAXABLE_UPLIFT * Decimal(net_paid_cents) / denominator
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def estimate_withholding(inp: PasInputs, model: TaxModel) -> PasEstimate | None:
    months = [m for m in inp.months if m.taxable_cents > 0]
    if len(months) < MIN_MONTHS or inp.rate.percent < 0:
        return None
    own_yearly = round(sum(m.taxable_cents for m in months) / len(months) * 12)
    partner_yearly = inp.partner_taxable_monthly_cents * 12 if inp.couple else 0
    salaries = [own_yearly, partner_yearly] if inp.couple else [own_yearly]
    tax = estimate_tax(model, salaries, inp.parts, inp.couple)
    total = own_yearly + partner_yearly
    if total <= 0:
        return None
    estimated = (Decimal(tax) / total * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    withheld = int((Decimal(total) * inp.rate.percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return PasEstimate(inp, own_yearly, partner_yearly, tax, withheld, estimated, model.label, model.source_url)


def _months_label(months: Sequence[MonthIncome]) -> str:
    ordered = sorted(m.month for m in months)
    payslips = sum(1 for m in months if m.from_payslip)
    span = f"{ordered[0][5:]}/{ordered[0][:4]} à {ordered[-1][5:]}/{ordered[-1][:4]}"
    detail = f"{payslips} bulletin{'s' if payslips > 1 else ''}" if payslips else "aucun bulletin"
    credits = len(months) - payslips
    if credits:
        detail += f" ; {credits} mois lu{'s' if credits > 1 else ''} sur les virements de salaire, net imposable estimé"
    return f"{len(months)} mois, de {span} ({detail})"


def _pas_evidence(est: PasEstimate, rule: WithholdingRule) -> tuple[Evidence, ...]:
    inp = est.inputs
    rows = [
        Evidence("Taux appliqué", f"{fr_percent(inp.rate.percent)} — {inp.rate.origin}", inp.rate.ref),
        Evidence("Revenus vus", _months_label(inp.months)),
        Evidence(
            "Salaires imposables projetés",
            f"{_eur0(est.own_yearly_cents)} sur {inp.year} (moyenne × 12, salaires seulement)",
        ),
    ]
    if inp.couple:
        rows.append(
            Evidence(
                "Conjoint",
                f"{_eur0(est.partner_yearly_cents)} sur l'année — {inp.partner_origin or 'non renseigné'}",
            )
        )
    rows += [
        Evidence("Parts", f"{inp.parts.normalize():f} — {inp.parts_origin}".replace(".", ",")),
        Evidence("Impôt estimé", f"{_eur0(est.tax_cents)} — {est.model_label}", f"url:{est.model_url}"),
        Evidence("Règle", rule.decrease_source, f"url:{rule.decrease_url}"),
    ]
    return tuple(rows)


def withholding_findings(est: PasEstimate | None, rule: WithholdingRule | None) -> list[Finding]:
    if est is None or rule is None:
        return []
    inp = est.inputs
    cur, new = inp.rate.percent, est.estimated_percent
    head = (
        f"Estimation Bank : tes salaires de {inp.year} projetés sur l'année donnent environ "
        f"{_eur0(est.total_cents)} de revenus imposables, soit environ {_eur0(est.tax_cents)} d'impôt au barème. "
        f"Ton taux de {fr_percent(cur)} en prélèverait {_eur0(est.withheld_cents)}. "
    )
    if est.withheld_cents > 0 and est.balance_cents < 0:
        gap = Decimal(-est.balance_cents) / est.withheld_cents * 100
        if gap <= rule.decrease_gap_percent:
            return []
        monthly = round((-est.balance_cents) / 12)
        return [
            Finding(
                key=f"pas_lower:{inp.year}",
                kind="withholding_rate",
                level="info",
                topic="revenus",
                title=f"Taux de prélèvement : {fr_percent(cur)} appliqué, environ {fr_percent(new)} d'après tes "
                f"revenus {inp.year}",
                body=head
                + (
                    f"L'écart ({gap.quantize(Decimal(1))} %) dépasse {fr_percent(rule.decrease_gap_percent)}, le seuil "
                    f"au-delà duquel le taux peut être baissé (CGI art. 204 J). À un taux d'environ "
                    f"{fr_percent(new)}, environ {_eur0(monthly)} de plus resteraient chaque mois sur tes salaires. "
                    f"Si tes revenus remontent d'ici décembre, la différence se règle en septembre {inp.year + 1}. "
                    "La demande se fait dans ton espace impots.gouv.fr."
                ),
                evidence=_pas_evidence(est, rule),
                yearly_cents=-est.balance_cents,
                confidence=0.55,
            )
        ]
    if est.balance_cents >= MIN_BALANCE_CENTS and new > cur:
        when = (
            f"en septembre {inp.year + 1}"
            if est.balance_cents <= rule.balance_single_max_cents
            else f"de septembre à décembre {inp.year + 1}, en 4 fois"
        )
        return [
            Finding(
                key=f"pas_balance:{inp.year}",
                kind="withholding_rate",
                level="warning",
                topic="revenus",
                title=f"Revenus {inp.year} : environ {_eur0(est.balance_cents)} de solde d'impôt {when}",
                body=head
                + (
                    f"La différence, environ {_eur0(est.balance_cents)}, serait prélevée {when} : Bank la place "
                    "dans l'Année qui vient. Tu peux aussi relever ton taux dès maintenant dans ton espace "
                    "impots.gouv.fr (la hausse n'a pas de condition d'écart)."
                ),
                evidence=(
                    *_pas_evidence(est, rule)[:-1],
                    Evidence("Solde", rule.balance_source, f"url:{rule.balance_url}"),
                ),
                yearly_cents=est.balance_cents,
                confidence=0.55,
            )
        ]
    return []


def september_balance(est: PasEstimate | None, rule: WithholdingRule | None) -> list[tuple[date, str, int]]:
    """Le solde estimé, posé dans l'Année qui vient : en une fois en septembre jusqu'à 300 €, sinon en quatre
    prélèvements de septembre à décembre (Service-Public F35898). Seul le mois est connu ; le 25 sert de repère."""
    if est is None or rule is None or est.balance_cents < MIN_BALANCE_CENTS:
        return []
    if est.estimated_percent <= est.inputs.rate.percent:
        return []
    year = est.inputs.year + 1
    label = f"Solde d'impôt {est.inputs.year} estimé"
    if est.balance_cents <= rule.balance_single_max_cents:
        return [(date(year, 9, 25), label, est.balance_cents)]
    quarter, rest = divmod(est.balance_cents, 4)
    return [(date(year, 9 + i, 25), f"{label} ({i + 1}/4)", quarter + (rest if i == 0 else 0)) for i in range(4)]


# ------------------------------------------------------------------ chèque énergie
CHEQUE_LABEL = re.compile(r"CHE?QU?E?S?\s*ENERGIE|\bCHQ\.?\s*ENERGIE|E-?CHEQUE\s*ENERGIE")


@dataclass(frozen=True)
class EnergyCheque:
    year: int
    amount_cents: int
    simulated_on: date
    engine_version: str
    rfr_known: bool = True  # revenu de l'année N-2 déclaré ou lu sur l'avis ; sinon OpenFisca le croit nul


def is_energy_cheque(label: str) -> bool:
    return bool(CHEQUE_LABEL.search(fold(label)))


def energy_cheque_findings(sim: EnergyCheque | None, seen: Sequence[date], today: date) -> list[Finding]:
    """Éligible d'après la simulation de cette année, et aucun chèque énergie dans les relevés de l'année. Les dates
    de la campagne n'ont pas pu être relues sur chequeenergie.gouv.fr : Bank n'en cite aucune."""
    if sim is None or sim.amount_cents <= 0 or sim.year != today.year:
        return []
    if any(d.year == sim.year for d in seen):
        return []
    if not sim.rfr_known:
        return [
            Finding(
                key=f"cheque_energie_rfr:{sim.year}",
                kind="energy_cheque",
                level="info",
                topic="energie",
                title=f"Chèque énergie {sim.year} : Bank ne sait pas dire si tu y as droit",
                body=(
                    f"Le chèque énergie {sim.year} dépend de ton revenu fiscal de référence {sim.year - 2}, que Bank "
                    f"ne connaît pas : la simulation l'a pris pour nul. Importe ton avis d'impôt sur les revenus "
                    f"{sim.year - 2} (Documents officiels) ou indique ce revenu dans ton profil de foyer, puis relance "
                    "la simulation."
                ),
                evidence=(Evidence("Site officiel", "chequeenergie.gouv.fr", "url:https://chequeenergie.gouv.fr"),),
                confidence=0.5,
            )
        ]
    return [
        Finding(
            key=f"cheque_energie:{sim.year}",
            kind="energy_cheque",
            level="info",
            topic="energie",
            title=f"Chèque énergie {sim.year} : Bank ne voit pas de chèque énergie utilisé",
            body=(
                f"La simulation OpenFisca du {sim.simulated_on:%d/%m/%Y} te donne éligible (environ "
                f"{_eur0(sim.amount_cents)} pour {sim.year}). Bank ne voit pas de chèque énergie utilisé dans tes "
                "relevés ; il est souvent réglé directement au fournisseur, donc invisible ici. Vérifie que tu l'as "
                "reçu (courrier ou e-chèque) et qu'il a servi."
            ),
            evidence=(
                Evidence("Simulation", f"{format_eur(sim.amount_cents)} — OpenFisca {sim.engine_version}"),
                Evidence("Site officiel", "chequeenergie.gouv.fr", "url:https://chequeenergie.gouv.fr"),
            ),
            confidence=0.5,
        )
    ]
