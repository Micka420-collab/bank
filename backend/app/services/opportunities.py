"""Centre d'opportunités : détecteurs branchés sur tes données (aides, santé, frais bancaires, abonnements,
épargne, découvert, documents qui expirent), classement par gain net attendu, décisions (lancer la démarche,
plus tard, pas pour moi) et valeur obtenue — l'estimé et le confirmé ne s'additionnent jamais.

Rien n'est inventé : un montant vient de tes comptes, de tes documents ou d'une simulation OpenFisca (le moteur
de calcul de l'État) ; une règle vient d'une source datée ; ce qui reste supposé est écrit comme tel.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import opportunities as do
from app.domain.aids import compute_gaps
from app.domain.forecast import MonthForecast
from app.domain.initiatives import MONTHS_FR
from app.domain.money import format_eur
from app.domain.procedures import OPEN_STAGES, STAGE_LABELS
from app.domain.recurring import RecurringSeries
from app.domain.wealth import load_regulated_products
from app.models import (
    Account,
    FiscalSimulation,
    OpportunityState,
    Procedure,
    Transaction,
    ValueEvent,
    VaultDocument,
)
from app.security import audit
from app.services import analytics, initiatives, procedures
from app.services.categories import category_ids
from app.services.settings_store import get_setting, set_setting

SIMULATION_FRESH_DAYS = 45
MIN_FEES_CENTS = 1_000
MIN_SUBSCRIPTION_CENTS = 500
MIN_IDLE_CENTS = 30_000
AID_VARIABLES = ("rsa", "ppa", "aide_logement", "af", "asf")
ACTIVE_STAGES = frozenset({*OPEN_STAGES, "sent", "waiting"})

_SIMULATOR = do.Source(
    "OpenFisca — le moteur de calcul open source des droits sociaux et fiscaux", "https://openfisca.org"
)
_RATES = do.Source("info.gouv.fr — taux des livrets réglementés au 1er août 2026 (publié le 31/07/2026)")
_LEP = do.Source(
    "economie.gouv.fr — Livret d'épargne populaire : comment ça marche ? (15/07/2026)",
    "https://www.economie.gouv.fr/node/35358",
    "2026-10-21",
)
_AMELI_CSS = do.Source(
    "Ameli — Complémentaire santé solidaire : comment la demander ? (18/06/2026)",
    "https://www.ameli.fr/assure/droits-demarches/difficultes-acces-droits-soins/complementaire-sante/"
    "complementaire-sante-solidaire-demande",
    "2026-10-21",
)
_INC_CANCEL = do.Source(
    "INC — résilier un contrat d'abonnement en quelques clics",
    "https://www.inc-conso.fr/content/vous-pouvez-resilier-votre-contrat-dabonnement-en-quelques-clics",
    "2026-10-21",
)
_COMPARATOR = do.Source(
    "Comparateur public des tarifs bancaires (ministère de l'Économie et CCSF)",
    "https://www.tarifs-bancaires.gouv.fr/",
    "2026-10-21",
)
# Complémentaire santé payée aujourd'hui (prélèvement mensuel) : ce que la CSS pourrait remplacer.
_MUTUELLE = re.compile(
    r"MUTUELLE|COMPLEMENTAIRE SANTE|\bMUT\b|HARMONIE|\bMGEN\b|\bALAN\b|MALAKOFF|\bAG2R\b|SANTECLAIR|"
    r"(?:APRIL|SWISS ?LIFE|GROUPAMA|MATMUT|MACIF|AXA|MAAF|GENERALI|ALLIANZ)\W+SANTE"
)
# Crédit qui rembourse des frais : un remboursement de soins (CPAM, mutuelle) ou un virement n'y ressemble pas.
_FEE_WORD = r"(?:FRAIS|COM|COMMISSIONS?|AGIOS|INTERV\w*|COTIS\w*|INCIDENTS?)\b"
_BACK_WORD = r"(?:REMB\w*|RBT|REGUL\w*|ANNUL\w*|RETRO\w*|RESTIT\w*|EXONERATION)\b"
_FEE_REFUND = re.compile(
    rf"GESTE COMMERCIAL|\b{_BACK_WORD}\W+(?:\w+\W+){{0,3}}?{_FEE_WORD}|\b{_FEE_WORD}\W+(?:\w+\W+){{0,3}}?{_BACK_WORD}"
)


class OpportunityError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _eur(cents: int) -> str:
    return format_eur(cents)


def _day(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _month_label(key: str) -> str:
    return f"{MONTHS_FR[int(key[5:7]) - 1]} {key[:4]}"


def _display_name(merchant: str) -> str:
    return " ".join(w.capitalize() if len(w) > 3 else w for w in merchant.split()) or merchant


class Ctx:
    """Ce que les détecteurs partagent : la prévision ne tourne qu'une fois par affichage."""

    def __init__(self, db: Session, today: date) -> None:
        self.db, self.today = db, today

    @cached_property
    def inputs(self) -> analytics.ForecastInputs:
        return analytics.forecast_inputs(self.db, self.today)

    @cached_property
    def forecast(self) -> MonthForecast:
        return self.inputs.run()

    @cached_property
    def simulation(self) -> tuple[dict[str, Any], date] | None:
        row = self.db.scalar(select(FiscalSimulation).order_by(FiscalSimulation.id.desc()).limit(1))
        return (json.loads(row.result), row.created_at.date()) if row is not None else None

    @cached_property
    def accounts(self) -> list[Account]:
        return list(self.db.scalars(select(Account).where(Account.archived.is_(False))))

    @cached_property
    def rates(self) -> dict[str, Decimal]:
        return {p.kind: p.rate_percent for p in load_regulated_products()}

    def monthly_series(self, *, categories: tuple[str, ...] | None = None) -> list[RecurringSeries]:
        return [
            s
            for s in self.inputs.series
            if s.direction == "out"
            and s.cadence == "monthly"
            and s.occurrences >= 3
            and (self.today - s.last_date).days <= 45
            and (categories is None or s.category in categories)
        ]


# ------------------------------------------------------------------ aides
def aid_gaps(ctx: Ctx) -> list[do.Opportunity]:
    sim = ctx.simulation
    if sim is None:
        has_profile = analytics.load_profile(ctx.db) is not None
        return [
            do.Opportunity(
                key="aid:simulate" if has_profile else "aid:profile",
                category="revenus",
                title="Vérifie à quelles aides tu as droit"
                if has_profile
                else "Décris ton foyer pour vérifier tes droits aux aides",
                summary="Ton profil est prêt : la simulation prend une minute."
                if has_profile
                else "Âge, commune, logement, enfants : cinq minutes, une fois, et Bank vérifie tes droits.",
                amount_cents=0,
                period="month",
                probability=0.5,
                effort_minutes=1 if has_profile else 5,
                confidence="moyenne",
                estimates=("Le calcul est fait par OpenFisca, le moteur open source de l'État, sur ton serveur.",),
                sources=(_SIMULATOR,),
                action={"link": "#/aides", "label": "Lancer la simulation" if has_profile else "Décrire mon foyer"},
                urgency=1,
            )
        ]
    result, when = sim
    fresh = (ctx.today - when).days <= SIMULATION_FRESH_DAYS
    estimates = {e["variable"]: int(e["monthly_cents"]) for e in result.get("estimates", [])}
    for var in AID_VARIABLES:  # absente du résultat = calculée à zéro
        estimates.setdefault(var, 0)
    stale = () if fresh else ("La simulation a plus de 45 jours : relance-la, ta situation a pu changer.",)
    out: list[do.Opportunity] = []
    for gap in compute_gaps(estimates, analytics.aids_summary(ctx.db, ctx.today).streams):
        received = (
            f"Tu perçois {_eur(gap.received_cents)} par mois de {gap.name} (moyenne des 3 derniers mois)."
            if gap.received_cents
            else f"Aucun versement de {gap.name} repéré sur tes comptes."
        )
        estimate = f"Droit estimé : {_eur(gap.estimated_cents)} par mois (simulation du {_day(when)})."
        if gap.gap_cents > 0:
            new_claim = gap.received_cents == 0
            template = "prime_activite" if gap.kind == "caf_ppa" and new_claim else None
            out.append(
                do.Opportunity(
                    key=f"aid:{gap.kind}",
                    category="revenus" if gap.kind == "caf_ppa" else "recuperer",
                    title=f"{gap.name} : jusqu'à {_eur(gap.gap_cents)} de plus par mois",
                    summary="Bank prépare la demande ; seul l'organisme peut confirmer ton droit."
                    if new_claim
                    else "Tu la perçois déjà : vérifie que ta dernière déclaration est à jour.",
                    amount_cents=gap.gap_cents,
                    period="month",
                    probability=(0.6 if fresh else 0.4) if new_claim else 0.3,
                    effort_minutes=45 if new_claim else 20,
                    confidence="moyenne",
                    facts=(received,),
                    estimates=(estimate,),
                    hypotheses=(
                        "Ton profil de foyer est à jour (couple, enfants, logement, loyer).",
                        "Tes revenus des derniers mois sont ceux que Bank voit sur tes comptes.",
                        *stale,
                    ),
                    sources=(_SIMULATOR,),
                    action={"template": template} if template else {"link": "#/aides", "label": "Voir mes aides"},
                )
            )
        else:
            out.append(
                do.Opportunity(
                    key=f"aid-risk:{gap.kind}",
                    category="anticiper",
                    title=f"{gap.name} : vérifie ta situation déclarée",
                    summary="Tu perçois plus que l'estimation : une déclaration pas à jour peut entraîner un "
                    "trop-perçu à rembourser.",
                    amount_cents=0,
                    period="once",
                    probability=0.3,
                    effort_minutes=15,
                    confidence="faible",
                    facts=(received,),
                    estimates=(estimate,),
                    hypotheses=("La simulation utilise un profil de foyer complet et à jour.", *stale),
                    sources=(_SIMULATOR,),
                    action={"link": "#/aides", "label": "Voir mes aides"},
                    urgency=1,
                )
            )
    return out


def health(ctx: Ctx) -> list[do.Opportunity]:
    sim = ctx.simulation
    if sim is None or sim[0].get("css") not in ("gratuite", "participation"):
        return []
    result, when = sim
    participation = int(result.get("css_participation_cents") or 0) if result["css"] == "participation" else 0
    mutuelles = [s for s in ctx.monthly_series() if _MUTUELLE.search(s.merchant.upper())]
    cost = sum(-s.amount_cents for s in mutuelles)
    saving = max(0, cost - participation)
    facts = tuple(
        f"{_display_name(s.merchant)} : {_eur(-s.amount_cents)} prélevés chaque mois ({s.occurrences} fois)."
        for s in mutuelles
    ) or ("Aucune mutuelle repérée sur tes comptes.",)
    return [
        do.Opportunity(
            key="aid:css",
            category="recuperer",
            title="Complémentaire santé solidaire : tu y aurais droit"
            + (" sans rien payer" if result["css"] == "gratuite" else ""),
            summary="L'aide de l'Assurance maladie pour payer tes dépenses de santé. Demande en ligne sur ameli, "
            "réponse de la CPAM sous deux mois.",
            amount_cents=saving,
            period="month",
            probability=0.6 if (ctx.today - when).days <= SIMULATION_FRESH_DAYS else 0.4,
            effort_minutes=30,
            confidence="moyenne",
            facts=facts,
            estimates=(
                f"Éligibilité selon la simulation OpenFisca du {_day(when)}"
                + (f", participation estimée à {_eur(participation)} par mois." if participation else "."),
                *(
                    (f"Économie : ta mutuelle actuelle ({_eur(cost)} par mois) moins la participation.",)
                    if cost
                    else ()
                ),
            ),
            hypotheses=(
                "La CPAM confirme l'éligibilité après étude de tes ressources.",
                *(("Tu résilies ta mutuelle une fois la CSS accordée.",) if cost else ()),
            ),
            sources=(_AMELI_CSS, _SIMULATOR),
            action={"template": "demander_css", "initial": {"mutuelle_actuelle": cost} if cost else {}},
        )
    ]


# ------------------------------------------------------------------ banque
def _first_of_month(today: date, back: int) -> date:
    y, m = today.year, today.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _debits(db: Session, slug: str, since: date) -> list[Transaction]:
    cat_id = category_ids(db).get(slug)
    if cat_id is None:
        return []
    return list(
        db.scalars(
            select(Transaction)
            .where(Transaction.category_id == cat_id, Transaction.booking_date >= since, Transaction.amount_cents < 0)
            .order_by(Transaction.booking_date)
        )
    )


def bank_fees(ctx: Ctx) -> list[do.Opportunity]:
    by_month: dict[str, list[do.Fee]] = defaultdict(list)
    for t in _debits(ctx.db, "frais_bancaires", _first_of_month(ctx.today, 3)):
        by_month[t.booking_date.strftime("%Y-%m")].append(do.Fee(t.booking_date, t.raw_label, -t.amount_cents))
    out: list[do.Opportunity] = []
    for month in sorted(by_month, reverse=True):  # le mois le plus récent avec des frais : une réclamation à la fois
        review = do.review_fees(month, by_month[month])
        if review.incident_total_cents < MIN_FEES_CENTS:
            continue
        over = review.excess_cents > 0
        amount = review.excess_cents if over else review.incident_total_cents
        label = _month_label(month)
        out.append(
            do.Opportunity(
                key=f"fees:{month}",
                category="recuperer",
                title=f"Frais bancaires de {label} : {_eur(review.excess_cents)} au-delà des plafonds légaux"
                if over
                else f"Frais bancaires de {label} : demande leur remboursement",
                summary="Une réclamation écrite qui cite les articles de loi, puis le médiateur si besoin."
                if over
                else "Sous les plafonds, rien n'oblige ta banque à rembourser, mais un geste commercial se demande.",
                amount_cents=amount,
                period="once",
                probability=0.8 if over else 0.35,
                effort_minutes=15,
                confidence="élevée" if over else "faible",
                facts=tuple(f"{f.on.strftime('%d/%m')} — {f.label} — {_eur(f.amount_cents)}" for f in review.fees),
                estimates=tuple(f"Dépassement : {r}." for r in review.reasons)
                or (f"Frais d'incident du mois : {_eur(review.incident_total_cents)}.",),
                hypotheses=() if over else ("Ta banque accepte un geste commercial.",),
                sources=do.FEE_SOURCES,
                action={
                    "template": "contester_frais",
                    "initial": {
                        "montant": amount,
                        "periode": label,
                        "detail": do.fees_detail(review.fees),
                        "motif": "plafond" if over else "geste",
                    },
                },
            )
        )
        break
    yearly = sum(
        -t.amount_cents
        for t in _debits(ctx.db, "frais_bancaires", ctx.today - timedelta(days=365))
        if do.fee_kind(t.raw_label) == "cotisation"
    )
    if yearly >= 6_000:
        out.append(
            do.Opportunity(
                key=f"bank-offer:{ctx.today.year}",
                category="reduire",
                title=f"Ta banque te coûte {_eur(yearly)} par an en cotisations",
                summary="Compare ton offre (carte, tenue de compte) sur le comparateur public des tarifs bancaires.",
                amount_cents=0,
                period="year",
                probability=0.3,
                effort_minutes=20,
                confidence="faible",
                facts=(f"Cotisations prélevées sur les 12 derniers mois : {_eur(yearly)}.",),
                hypotheses=("Une offre moins chère couvre tes besoins (carte, découvert, agence).",),
                sources=(_COMPARATOR,),
                action={"link": "https://www.tarifs-bancaires.gouv.fr/", "label": "Comparer les tarifs"},
            )
        )
    return out


def subscriptions(ctx: Ctx) -> list[do.Opportunity]:
    """Abonnements mensuels : sauf ceux que tu as dit vouloir garder, plus fort pour ceux que tu ne reconnais pas."""
    answers = initiatives.ack_notes(ctx.db)
    out = []
    candidates = [
        s
        for s in ctx.monthly_series(categories=("abonnements", "loisirs"))
        if -s.amount_cents >= MIN_SUBSCRIPTION_CENTS and answers.get(s.key) not in ("voulu", "terminé")
    ]
    for s in sorted(candidates, key=lambda s: s.amount_cents)[:5]:
        monthly, name = -s.amount_cents, _display_name(s.merchant)
        unknown = answers.get(s.key) == "inconnu"
        out.append(
            do.Opportunity(
                key=f"sub:{s.key}"[:160],
                category="reduire",
                title=f"{name} : {_eur(monthly)} par mois que tu ne reconnais pas"
                if unknown
                else f"{name} : {_eur(monthly)} par mois, tu t'en sers encore ?",
                summary="Vérifie auprès de ta banque d'où vient ce prélèvement ; s'il s'agit d'un abonnement, "
                "Bank prépare la lettre de résiliation."
                if unknown
                else "Si non, la résiliation se fait en quelques clics ; Bank prépare la lettre au besoin.",
                amount_cents=monthly,
                period="month",
                probability=0.7 if unknown else 0.25,
                effort_minutes=10,
                confidence="moyenne" if unknown else "faible",
                facts=(f"Prélevé chaque mois ({s.occurrences} fois), dernier passage le {_day(s.last_date)}.",),
                estimates=(f"Soit {_eur(monthly * 12)} par an.",),
                hypotheses=() if unknown else ("Tu ne t'en sers plus assez pour son prix — toi seul le sais.",),
                sources=(_INC_CANCEL,),
                action={
                    "template": "resilier_abonnement",
                    "initial": {"fournisseur": name, "montant_mensuel": monthly},
                },
            )
        )
    return out


# ------------------------------------------------------------------ épargne
def savings(ctx: Ctx) -> list[do.Opportunity]:
    out: list[do.Opportunity] = []
    lep, livret_a = ctx.rates.get("lep", Decimal(0)), ctx.rates.get("livret_a", Decimal(0))
    livrets = sum(max(0, a.balance_cents) for a in ctx.accounts if a.kind in ("livret_a", "ldds", "savings"))
    sim = ctx.simulation
    if sim is not None and sim[0].get("lep_eligible") and not any(a.kind == "lep" for a in ctx.accounts):
        movable = min(livrets, 1_000_000)
        gain = int(Decimal(movable) * (lep - livret_a) / 100)
        out.append(
            do.Opportunity(
                key="lep:open",
                category="epargne",
                title="Tu as droit au LEP, le livret le mieux rémunéré",
                summary=f"{lep} % net d'impôt contre {livret_a} % pour le livret A, jusqu'à 10 000 €.",
                amount_cents=gain,
                period="year",
                probability=0.9,
                effort_minutes=30,
                confidence="élevée",
                facts=(f"Épargne sur tes livrets : {_eur(livrets)}.",),
                estimates=(
                    f"Éligibilité selon la simulation OpenFisca du {_day(sim[1])}.",
                    f"Gain : {_eur(movable)} placés à {lep} % au lieu de {livret_a} %.",
                ),
                hypotheses=("Ton revenu fiscal de référence reste sous le plafond.",),
                sources=(_RATES, _LEP),
                action={"template": "ouvrir_lep"},
            )
        )
    checking = [a for a in ctx.accounts if a.kind == "checking" and a.include_in_budget]
    if checking:
        f = ctx.forecast
        month_out = f.spent_fixed_cents + f.spent_variable_cents + f.expected_fixed_cents + f.expected_variable_cents
        idle = f.lowest_balance_cents - ctx.inputs.buffer_cents - month_out
        if idle >= MIN_IDLE_CENTS:
            gain = int(Decimal(idle) * livret_a / 100)
            out.append(
                do.Opportunity(
                    key="idle-cash",
                    category="epargne",
                    title=f"{_eur(idle)} dorment sur ton compte courant",
                    summary=f"Sur un livret à {livret_a} %, ils rapporteraient environ {_eur(gain)} par an, "
                    "disponibles à tout moment.",
                    amount_cents=gain,
                    period="year",
                    probability=0.95,
                    effort_minutes=5,
                    confidence="élevée",
                    facts=(f"Solde de tes comptes courants : {_eur(sum(a.balance_cents for a in checking))}.",),
                    estimates=(
                        f"Point bas prévu ce mois-ci : {_eur(f.lowest_balance_cents)}.",
                        f"Gardé de côté : ton matelas ({_eur(ctx.inputs.buffer_cents)}) et un mois de dépenses "
                        f"({_eur(month_out)}).",
                    ),
                    hypotheses=("Pas de grosse dépense imprévue d'ici la fin du mois.",),
                    sources=(_RATES,),
                    action={"link": "#/patrimoine", "label": "Voir où placer"},
                )
            )
    return out


# ------------------------------------------------------------------ anticiper
def overdraft(ctx: Ctx) -> list[do.Opportunity]:
    f = ctx.forecast
    if f.overdraft_date is None:
        return []
    return [
        do.Opportunity(
            key=f"overdraft:{f.month}",
            category="anticiper",
            title=f"Découvert prévu le {f.overdraft_date.strftime('%d/%m')}",
            summary="Décale une dépense, rapatrie un peu d'épargne ou préviens ta banque avant l'incident.",
            amount_cents=do.INTERVENTION_PER_OP_CENTS,
            period="once",
            probability=0.5,
            effort_minutes=10,
            confidence="moyenne",
            facts=(f"Solde actuel : {_eur(f.current_balance_cents)}.",),
            estimates=(f"Point bas prévu : {_eur(f.lowest_balance_cents)} le {_day(f.lowest_balance_date)}.",),
            hypotheses=(
                "Tes dépenses suivent ton rythme habituel.",
                "Un incident coûte des agios et jusqu'à 8 € de commission d'intervention par opération.",
            ),
            sources=(do.FEE_SOURCES[0],),
            action={"link": "#/aujourdhui", "label": "Voir le mois"},
            deadline=f.overdraft_date,
            urgency=3,
        )
    ]


def documents(ctx: Ctx) -> list[do.Opportunity]:
    rows = ctx.db.scalars(
        select(VaultDocument)
        .where(VaultDocument.expires_on.is_not(None), VaultDocument.expires_on <= ctx.today + timedelta(days=60))
        .where(VaultDocument.status.in_(("to_review", "verified")))
    )
    out = []
    for doc in rows:
        expires = doc.expires_on
        if expires is None:  # pragma: no cover - filtré par la requête
            continue
        days = (expires - ctx.today).days
        out.append(
            do.Opportunity(
                key=f"doc:{doc.id}",
                category="anticiper",
                title=f"{doc.title} : {'expiré depuis' if days < 0 else 'expire'} le {_day(expires)}",
                summary="Anticipe le renouvellement : certains rendez-vous se prennent des semaines à l'avance.",
                amount_cents=0,
                period="once",
                probability=1.0,
                effort_minutes=20,
                confidence="élevée",
                facts=(f"Date lue dans le document : {_day(expires)}.",),
                action={"link": f"#/documents/{doc.id}", "label": "Voir le document"},
                deadline=expires,
                urgency=3 if days <= 30 else 1,
            )
        )
    return out


DETECTORS = (aid_gaps, health, bank_fees, subscriptions, savings, overdraft, documents)


# ------------------------------------------------------------------ centre
def time_value(db: Session) -> int:
    return int(get_setting(db, "time_value_cents_per_hour"))


def candidates(db: Session, today: date) -> list[do.Opportunity]:
    ctx = Ctx(db, today)
    found: list[do.Opportunity] = []
    for detector in DETECTORS:
        found.extend(detector(ctx))
    return found


def _view(o: do.Opportunity, state: OpportunityState | None, proc: Procedure | None, tv: int) -> dict[str, Any]:
    return {
        "key": o.key,
        "category": o.category,
        "category_label": do.CATEGORIES[o.category],
        "title": o.title,
        "summary": o.summary,
        "amount_cents": o.amount_cents,
        "period": o.period,
        "yearly_cents": do.yearly(o.amount_cents, o.period),
        "probability": o.probability,
        "effort_minutes": o.effort_minutes,
        "cost_cents": o.cost_cents,
        "expected_net_cents": do.expected_net(o, tv),
        "confidence": o.confidence,
        "facts": list(o.facts),
        "estimates": list(o.estimates),
        "hypotheses": list(o.hypotheses),
        "sources": [{"title": s.title, "url": s.url, "verified_on": s.verified_on} for s in o.sources],
        "action": o.action,
        "deadline": o.deadline.isoformat() if o.deadline else None,
        "status": state.status if state else "new",
        "procedure": {"id": proc.id, "stage": proc.stage, "stage_label": STAGE_LABELS.get(proc.stage, proc.stage)}
        if proc is not None
        else None,
    }


def center(db: Session, today: date) -> dict[str, Any]:
    tv = time_value(db)
    states = {s.key: s for s in db.scalars(select(OpportunityState))}
    procs = {p.id: p for p in db.scalars(select(Procedure).where(Procedure.opportunity_key.is_not(None)))}
    visible, hidden = [], 0
    for o in do.rank(candidates(db, today), today, tv):
        state = states.get(o.key)
        if state is not None and (
            state.status in ("dismissed", "done") or (state.status == "snoozed" and state.until and state.until > today)
        ):
            hidden += 1
            continue
        proc = procs.get(state.procedure_id) if state is not None and state.procedure_id else None
        visible.append(_view(o, state, proc, tv))
    return {
        "opportunities": visible,
        "hidden": hidden,
        "categories": do.CATEGORIES,
        "time_value_cents_per_hour": tv,
        "value": value_summary(db),
        "confirmations": confirmations(db, today),
        "disclaimer": "Des estimations, jamais des promesses : un droit se confirme auprès de l'organisme, un gain se "
        "constate sur tes comptes. Bank t'aide à décider ; ce n'est pas un conseiller financier agréé.",
    }


def top(db: Session, today: date, limit: int = 3) -> list[dict[str, Any]]:
    """Les meilleures pistes du moment (écran Aujourd'hui) : ni écartées, ni remises à plus tard, ni lancées."""
    tv = time_value(db)
    states = {s.key: s for s in db.scalars(select(OpportunityState))}
    out = []
    for o in do.rank(candidates(db, today), today, tv):
        state = states.get(o.key)
        if state is None or (state.status == "snoozed" and (state.until is None or state.until <= today)):
            out.append(_view(o, state, None, tv))
        if len(out) >= limit:
            break
    return out


def _find(db: Session, key: str, today: date) -> do.Opportunity:
    opp = next((o for o in candidates(db, today) if o.key == key), None)
    if opp is None:
        raise OpportunityError("Cette opportunité n'est plus d'actualité", 404)
    return opp


def start(db: Session, key: str, *, actor: str, today: date) -> dict[str, Any]:
    """Lancer : la démarche liée est créée et préremplie ; le gain attendu est noté comme estimation."""
    opp = _find(db, key, today)
    template = opp.action.get("template")
    if not template:
        raise OpportunityError("Rien à préparer ici : suis le lien proposé", 409)
    proc = procedures.create(
        db,
        str(template),
        actor=actor,
        today=today,
        opportunity_key=opp.key,
        initial=dict(opp.action.get("initial") or {}),
    )
    already = db.scalar(
        select(ValueEvent.id).where(ValueEvent.opportunity_key == opp.key, ValueEvent.kind == "estimated")
    )
    if already is None and opp.amount_cents > 0:
        db.add(
            ValueEvent(
                opportunity_key=opp.key,
                kind="estimated",
                amount_cents=round(opp.amount_cents * opp.probability),
                period=opp.period,
                on_date=today,
                label=opp.title[:200],
                evidence=f"{_eur(opp.amount_cents)} possibles × {round(opp.probability * 100)} % de chances "
                "(estimation au lancement)",
                source="auto",
            )
        )
    db.flush()
    audit.record(db, actor, "opportunity.start", key=opp.key[:80], procedure=proc.id)
    return {"procedure_id": proc.id}


def decide(db: Session, key: str, decision: str, *, actor: str, today: date, days: int = 30) -> None:
    """Pas pour moi (écartée), plus tard (remise à dans N jours), ou de retour dans la liste."""
    if decision not in ("dismissed", "snoozed", "new"):
        raise OpportunityError("Décision inconnue")
    key = key[:160]
    state = db.get(OpportunityState, key)
    if decision == "new":
        if state is not None and state.status in ("dismissed", "snoozed"):
            db.delete(state)
    elif state is not None and state.status == "started":
        raise OpportunityError("Une démarche est en cours : gère-la depuis l'écran Démarches", 409)
    else:
        if state is None:
            state = OpportunityState(key=key, status=decision)
            db.add(state)
        state.status = decision
        state.until = today + timedelta(days=max(1, min(days, 365))) if decision == "snoozed" else None
    db.flush()
    audit.record(db, actor, f"opportunity.{decision}", key=key[:80])


def set_time_value(db: Session, cents_per_hour: int, *, actor: str) -> None:
    if not 0 <= cents_per_hour <= 50_000:
        raise OpportunityError("Entre 0 et 500 € de l'heure")
    set_setting(db, "time_value_cents_per_hour", cents_per_hour)
    audit.record(db, actor, "opportunity.time_value", cents_per_hour=cents_per_hour)


# ------------------------------------------------------------------ valeur obtenue
def _yearly(amounts: dict[str, int]) -> int:
    return amounts["once"] + amounts["year"] + 12 * amounts["month"]


def value_summary(db: Session) -> dict[str, Any]:
    """Confirmé (constaté ou déclaré par toi) d'un côté ; estimé (démarches encore en cours) de l'autre.
    Une démarche refusée, abandonnée ou supprimée sort de l'estimé ; rien n'est jamais additionné."""
    rows = list(db.scalars(select(ValueEvent).order_by(ValueEvent.on_date, ValueEvent.id)))
    active_procs = list(
        db.execute(select(Procedure.id, Procedure.opportunity_key).where(Procedure.stage.in_(ACTIVE_STAGES)))
    )
    active = {key for _, key in active_procs if key} | {f"procedure:{pid}" for pid, _ in active_procs}
    confirmed = {"once": 0, "month": 0, "year": 0}
    estimated = {"once": 0, "month": 0, "year": 0}
    confirmed_keys = {v.opportunity_key for v in rows if v.kind == "confirmed"}
    pending: list[ValueEvent] = []
    for v in rows:
        if v.kind == "confirmed":
            confirmed[v.period] += v.amount_cents
        elif v.opportunity_key in active and v.opportunity_key not in confirmed_keys:
            estimated[v.period] += v.amount_cents
            pending.append(v)

    def event(v: ValueEvent) -> dict[str, Any]:
        return {
            "key": v.opportunity_key,
            "amount_cents": v.amount_cents,
            "period": v.period,
            "on": v.on_date.isoformat(),
            "label": v.label,
            "evidence": v.evidence,
            "source": v.source,
        }

    return {
        "confirmed": confirmed,
        "confirmed_yearly_cents": _yearly(confirmed),
        "estimated": estimated,
        "estimated_yearly_cents": _yearly(estimated),
        "history": [event(v) for v in reversed(rows) if v.kind == "confirmed"][:50],
        "pending": [event(v) for v in reversed(pending)][:50],
    }


_GENERIC_WORDS = frozenset({"SAS", "SARL", "FRANCE", "COM", "WWW", "SEPA", "PRLV", "THE"})


def _words(name: str) -> list[str]:
    return [w for w in re.findall(r"[A-Z0-9]{3,}", name.upper()) if w not in _GENERIC_WORDS]


def confirmations(db: Session, today: date) -> list[dict[str, Any]]:
    """Gains que Bank croit constater (frais remboursés, prélèvement arrêté) : rien n'est compté sans ton accord."""
    out: list[dict[str, Any]] = []
    confirmed = set(db.scalars(select(ValueEvent.opportunity_key).where(ValueEvent.kind == "confirmed")))
    for proc in db.scalars(select(Procedure).where(Procedure.stage.in_(("sent", "waiting")))):
        key = proc.opportunity_key or f"procedure:{proc.id}"
        data = json.loads(proc.data)
        sent_on = date.fromisoformat(data["sent_on"]) if data.get("sent_on") else None
        if key in confirmed or sent_on is None:
            continue
        values = {k: v.get("value") for k, v in data.get("fields", {}).items()}
        if proc.template == "contester_frais":
            claimed = values.get("montant")
            credits = db.scalars(
                select(Transaction)
                .where(Transaction.booking_date >= sent_on, Transaction.amount_cents > 0)
                .order_by(Transaction.booking_date)
            )
            refunds = [
                t
                for t in credits
                if _FEE_REFUND.search(t.raw_label.upper())
                and (not isinstance(claimed, int) or t.amount_cents <= claimed)
            ]
            if refunds:
                out.append(
                    {
                        "key": key,
                        "procedure_id": proc.id,
                        "title": proc.title,
                        "label": f"Remboursement de frais reçu le {_day(refunds[-1].booking_date)} : "
                        + " ; ".join(t.raw_label for t in refunds[:3]),
                        "amount_cents": sum(t.amount_cents for t in refunds),
                        "period": "once",
                    }
                )
        elif proc.template == "resilier_abonnement" and values.get("fournisseur"):
            effect = date.fromisoformat(str(values["date_effet"])) if values.get("date_effet") else sent_on
            monthly = values.get("montant_mensuel")
            words = _words(str(values["fournisseur"]))
            if (today - effect).days < 35 or not isinstance(monthly, int) or monthly <= 0 or not words:
                continue
            labels = db.scalars(
                select(Transaction.raw_label).where(Transaction.booking_date > effect, Transaction.amount_cents < 0)
            )
            if not any(any(w in label.upper() for w in words) for label in labels):
                out.append(
                    {
                        "key": key,
                        "procedure_id": proc.id,
                        "title": proc.title,
                        "label": f"Plus aucun prélèvement de {values['fournisseur']} depuis le {_day(effect)}",
                        "amount_cents": monthly,
                        "period": "month",
                    }
                )
    return out


def confirm(
    db: Session,
    key: str,
    *,
    amount_cents: int,
    period: str,
    label: str,
    actor: str,
    today: date,
    procedure_id: int | None = None,
) -> None:
    """Tu confirmes un gain constaté : il passe dans « obtenu » et la démarche liée est close « obtenu »."""
    if period not in ("once", "month", "year") or not 0 < amount_cents <= 100_000_000:
        raise OpportunityError("Montant ou périodicité invalide")
    key = key[:160]
    if db.scalar(select(ValueEvent.id).where(ValueEvent.opportunity_key == key, ValueEvent.kind == "confirmed")):
        raise OpportunityError("Ce gain est déjà confirmé", 409)
    proc = db.get(Procedure, procedure_id) if procedure_id is not None else None
    if procedure_id is not None and (proc is None or (proc.opportunity_key or f"procedure:{proc.id}") != key):
        raise OpportunityError("Cette démarche ne correspond pas à ce gain", 409)
    db.add(
        ValueEvent(
            opportunity_key=key,
            kind="confirmed",
            amount_cents=amount_cents,
            period=period,
            on_date=today,
            label=" ".join(label.split())[:200] or "Gain confirmé",
            evidence="Constaté par Bank sur tes comptes, confirmé par toi",
            source="auto",
        )
    )
    db.flush()
    if proc is not None and proc.stage in ACTIVE_STAGES:
        procedures.close(
            db, proc.id, outcome="obtenu", actor=actor, today=today, amount_cents=amount_cents, period=period
        )
    audit.record(db, actor, "opportunity.confirm", key=key[:80], amount_cents=amount_cents)
