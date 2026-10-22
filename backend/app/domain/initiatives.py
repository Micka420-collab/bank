"""Initiatives : ce que Bank te demande, te signale ou fête — au bon moment, en un geste.

Une application qui veut te connaître ne t'impose pas un formulaire : elle remarque (« ton loyer, c'est bien
650 € chez X ? »), elle demande une chose à la fois en disant pourquoi, elle apprend de ce que tu confirmes,
et elle se tait quand tu lui dis « plus tard ». Chaque carte a une clé stable, une priorité, une à trois
actions ; l'état (faite, remise, écartée) est conservé pour ne jamais redemander la même chose.

Tout est pur ici : un Context décrit ce que Bank sait, build_cards en tire les cartes du moment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.onboarding import Question, Suggestion

CADENCE_FR = {
    "weekly": "chaque semaine",
    "biweekly": "toutes les deux semaines",
    "monthly": "chaque mois",
    "quarterly": "chaque trimestre",
    "yearly": "chaque année",
}
MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
# Catégories dont les prélèvements sont couverts par les faits détectés (loyer, crédits) ou ne sont pas des
# abonnements : inutile de demander « c'est voulu ? ».
SILENT_SERIES_CATEGORIES = frozenset({"logement", "credits", "impots", "epargne", "salaire", "aides", "virements"})
MAX_QUESTION_CARDS = 3


def eur(cents: int) -> str:
    whole = f"{abs(cents) // 100:,}".replace(",", " ")
    sign = "-" if cents < 0 else ""
    rest = abs(cents) % 100
    return f"{sign}{whole} €" if rest == 0 else f"{sign}{whole},{rest:02d} €"


def _fr(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def month_name(key: str) -> str:
    return MONTHS_FR[int(key[5:7]) - 1]


def fmt_day(d: date) -> str:
    return f"{d.day} {MONTHS_FR[d.month - 1]}"


# ------------------------------------------------------------------ cartes
@dataclass(frozen=True)
class Action:
    id: str
    label: str
    style: str = "primary"  # primary | secondary | link
    effect: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Card:
    key: str
    kind: str  # question | notice | celebrate | tip
    priority: int  # plus petit = plus tôt
    title: str
    body: str
    actions: tuple[Action, ...]
    why: str | None = None
    evidence: str | None = None
    input: Mapping[str, Any] | None = None  # saisie en ligne : {"kind": money|int|choice|multi|category, ...}
    topic: str = "profil"  # profil | operations | argent | securite | planning | fete


def snooze(days: int = 7, label: str = "Plus tard") -> Action:
    return Action("snooze", label, "link", {"type": "snooze", "days": days})


def dismiss(label: str = "Ne plus demander") -> Action:
    return Action("dismiss", label, "link", {"type": "dismiss"})


def done(label: str = "OK") -> Action:
    return Action("done", label, "secondary", {"type": "done"})


def navigate(page: str, label: str, style: str = "primary") -> Action:
    return Action(f"go:{page}", label, style, {"type": "navigate", "page": page})


# ------------------------------------------------------------------ ce que Bank sait
@dataclass(frozen=True)
class SeriesInfo:
    key: str
    merchant: str
    amount_cents: int  # négatif pour une sortie
    cadence: str
    occurrences: int
    category: str
    confidence: float
    last_date: date
    next_date: date


@dataclass(frozen=True)
class MerchantGroup:
    merchant: str
    count: int
    total_cents: int
    last_date: date
    hint: str | None = None  # enveloppe proposée par l'assistant (slug), à confirmer
    hint_name: str | None = None


@dataclass(frozen=True)
class SalaryInfo:
    merchant: str
    median_cents: int
    months: int
    day_of_month: int | None
    next_date: date
    last_date: date


@dataclass(frozen=True)
class MonthReview:
    month: str  # 2026-09
    days_of_data: int
    income_cents: int
    spent_cents: int
    left_avg_cents: int  # moyenne de ce qu'il reste sur 3 mois


@dataclass(frozen=True)
class EmergencyInfo:
    months_covered: float
    target_months: int
    available_cents: int


@dataclass(frozen=True)
class GoalInfo:
    id: int
    name: str
    progress: float


@dataclass(frozen=True)
class ConnectionInfo:
    id: int
    name: str
    valid_until: date | None


@dataclass(frozen=True)
class PlanningInfo:
    connected: bool
    has_rules: bool
    pay_date: date | None = None
    predicted_cents: int | None = None
    usual_cents: int | None = None
    extras: tuple[tuple[str, int, int], ...] = ()  # (motif, nombre, total)


@dataclass(frozen=True)
class ObligationInfo:
    """Une déclaration récurrente et sa période du moment (voir domain.obligations)."""

    id: int
    kind: str
    label: str
    organism: str
    state: str  # late | soon | open | upcoming | done
    period_key: str
    period_label: str
    opens: date
    deadline: date
    awaiting_payment: bool = False
    needs_anchor: bool = False
    anchor_label: str | None = None
    consequence: str = ""
    what: str = ""
    automatic: bool = False
    prefill_summary: str | None = None  # déclaration trimestrielle : les chiffres prêts


@dataclass(frozen=True)
class ObligationSuggestion:
    kind: str
    label: str


@dataclass(frozen=True)
class ProvisionInfo:
    """La provision mensuelle qui lisse les pics de l'année (voir domain.yearahead)."""

    key: str
    provision_cents: int
    target_cents: int
    yearly_cents: int
    biggest: str
    count: int
    lowest: str
    status: str


@dataclass(frozen=True)
class CorrelationInfo:
    """Un constat sérieux des corrélations (doublon d'assurance, loyer au-dessus du bail…), voir domain.correlations."""

    key: str
    level: str  # danger | warning
    topic_label: str
    title: str
    body: str
    yearly_cents: int | None = None
    has_action: bool = False


@dataclass(frozen=True)
class State:
    status: str  # done | snoozed | dismissed
    until: date | None = None
    note: str | None = None


@dataclass(frozen=True)
class Context:
    today: date
    has_transactions: bool
    months_of_data: float
    questions: Sequence[Question]
    answers: Mapping[str, Any]
    suggestions: Sequence[Suggestion]
    series: Sequence[SeriesInfo] = ()
    uncategorized: Sequence[MerchantGroup] = ()
    categories: Sequence[tuple[str, str]] = ()  # (slug, nom) proposables pour classer
    salary: SalaryInfo | None = None
    month_review: MonthReview | None = None
    savings_goal_cents: int = 0
    emergency: EmergencyInfo | None = None
    goals: Sequence[GoalInfo] = ()
    totp_enabled: bool = True
    connections: Sequence[ConnectionInfo] = ()
    energy_connected: bool = False
    energy_days_since_data: int | None = None
    latest_tax_period: int | None = None
    planning: PlanningInfo | None = None
    paid_recently: bool = False
    obligations: Sequence[ObligationInfo] = ()
    obligation_suggestions: Sequence[ObligationSuggestion] = ()
    correlations: Sequence[CorrelationInfo] = ()
    provision: ProvisionInfo | None = None
    states: Mapping[str, State] = field(default_factory=dict)


# ------------------------------------------------------------------ générateurs
def _q(ctx: Context, qid: str) -> Question | None:
    return next((q for q in ctx.questions if q.id == qid), None)


def _option_label(q: Question | None, value: Any) -> str:
    if q is None:
        return str(value)
    return next((label for v, label in q.options if v == value), str(value))


def gen_start(ctx: Context) -> list[Card]:
    if ctx.has_transactions:
        return []
    return [
        Card(
            "start:data",
            "tip",
            1,
            "Pour te connaître vraiment, il me faut tes opérations",
            "Connecte ta banque (mise à jour automatique) ou importe un relevé : je reconnais ton salaire, ton loyer, "
            "tes abonnements, et je te demanderai seulement ce que je ne peux pas deviner.",
            (
                navigate("connecteurs", "Connecter ma banque"),
                navigate("operations", "Importer un relevé", "secondary"),
                snooze(7),
            ),
            topic="operations",
        )
    ]


def gen_celebrations(ctx: Context) -> list[Card]:
    out: list[Card] = []
    if ctx.months_of_data >= 3:
        out.append(
            Card(
                "celebrate:3mois",
                "celebrate",
                5,
                "Trois mois d'historique : mes prévisions deviennent fiables",
                "Je connais maintenant tes rythmes (salaire, loyer, abonnements, dépenses courantes). "
                "Le « reste à dépenser » et la date de point bas s'appuient sur du réel.",
                (done("Super"),),
                topic="fete",
            )
        )
    if (
        ctx.emergency
        and ctx.emergency.months_covered >= ctx.emergency.target_months
        and ctx.emergency.available_cents > 0
    ):
        out.append(
            Card(
                "celebrate:matelas",
                "celebrate",
                5,
                "Ton matelas de sécurité est constitué",
                f"{eur(ctx.emergency.available_cents)} d'épargne disponible, soit {_fr(ctx.emergency.months_covered)} "
                f"mois de dépenses essentielles (objectif : {ctx.emergency.target_months}). L'étape suivante, c'est "
                "le long terme.",
                (done("Merci"), navigate("patrimoine", "Voir où placer", "link")),
                topic="fete",
            )
        )
    for g in ctx.goals:
        if g.progress >= 1:
            out.append(
                Card(
                    f"celebrate:goal:{g.id}",
                    "celebrate",
                    5,
                    f"Objectif « {g.name} » atteint",
                    "Tu l'as fait. Tu peux le garder comme repère ou en fixer un nouveau dans Patrimoine.",
                    (done("Merci"),),
                    topic="fete",
                )
            )
    return out


_FACT_TITLES: dict[str, str] = {
    "net_monthly_income": "Ton salaire net, c'est bien {value} par mois ?",
    "housing_cost": "Ton logement te coûte {value} par mois ?",
    "housing_status": "Tu es {value} ?",
    "savings_capacity": "Il te reste environ {value} par mois : c'est ta capacité d'épargne ?",
    "debts": "Ces prélèvements sont des crédits : {value} ?",
}
_HOUSEHOLD_TITLES: dict[str, str] = {
    "seul": "Tu vis seul·e ?",
    "couple": "Vous vivez en couple, sans enfant ?",
    "couple_enfants": "Vous vivez en couple, avec des enfants ?",
    "parent_solo": "Tu élèves tes enfants seul·e ?",
    "colocation": "Tu vis en colocation ?",
}


def _fact_title(qid: str, value: str, raw: Any, label: str) -> str:
    if qid == "household":
        return _HOUSEHOLD_TITLES.get(str(raw), f"Ton foyer : {value} ?")
    if qid == "dependents" and isinstance(raw, int):
        if raw == 0:
            return "Tu n'as personne à charge ?"
        return f"Tu as {raw} personne{'s' if raw > 1 else ''} à charge ?"
    return _FACT_TITLES.get(qid, "{label} — je pense : {value}").format(value=value, label=label)


def _fact_value(q: Question | None, s: Suggestion) -> str:
    if q is None:
        return str(s.value)
    if q.kind == "money" and isinstance(s.value, int):
        return eur(s.value)
    if q.kind == "choice":
        return _option_label(q, s.value).lower()
    if q.kind == "list" and isinstance(s.value, list):
        return (
            ", ".join(str(it.get("label", "")) for it in s.value if isinstance(it, dict)) or f"{len(s.value)} ligne(s)"
        )
    return str(s.value)


def gen_facts(ctx: Context) -> list[Card]:
    out: list[Card] = []
    for s in ctx.suggestions:
        q = _q(ctx, s.question_id)
        value = _fact_value(q, s)
        title = _fact_title(s.question_id, value, s.value, q.label if q else s.question_id)
        actions: list[Action] = [
            Action("yes", "C'est juste", "primary", {"type": "answer", "question_id": s.question_id, "value": s.value})
        ]
        inp: Mapping[str, Any] | None = None
        if q is not None and q.kind in ("money", "int", "choice"):
            inp = _input_for(q)
            actions.append(
                Action(
                    "fix", "Corriger", "secondary", {"type": "answer", "question_id": s.question_id, "from_input": True}
                )
            )
        else:
            actions.append(navigate("situation", "Corriger", "secondary"))
        actions.append(snooze(7))
        out.append(
            Card(
                f"fact:{s.question_id}",
                "question",
                10,
                title,
                "Si c'est juste, ton agent saura que c'est vérifié.",
                tuple(actions),
                why=q.why if q else None,
                evidence=s.evidence,
                input=inp,
            )
        )
    return out


def gen_salary_change(ctx: Context) -> list[Card]:
    sal = ctx.salary
    declared = ctx.answers.get("net_monthly_income")
    if sal is None or sal.months < 2 or not isinstance(declared, int) or declared <= 0:
        return []
    if abs(sal.median_cents - declared) <= max(2_000, declared * 0.05):
        return []
    direction = "augmenté" if sal.median_cents > declared else "baissé"
    return [
        Card(
            f"salary:{sal.median_cents}",
            "question",
            12,
            f"Ton salaire a {direction} ? Je vois {eur(sal.median_cents)}, tu m'avais dit {eur(declared)}",
            f"Médiane des virements de {sal.merchant} sur {sal.months} mois. Je mets à jour ?",
            (
                Action(
                    "yes",
                    "Oui, mettre à jour",
                    "primary",
                    {"type": "answer", "question_id": "net_monthly_income", "value": sal.median_cents},
                ),
                Action("no", "Non, c'est ponctuel", "secondary", {"type": "dismiss"}),
            ),
            why="Ton salaire est la base du budget, de la capacité d'épargne et du plan de placement.",
            topic="argent",
        )
    ]


def gen_series(ctx: Context) -> list[Card]:
    out: list[Card] = []
    for s in ctx.series:
        if s.amount_cents >= -200 or s.category in SILENT_SERIES_CATEGORIES or s.confidence < 0.6:
            continue
        cadence = CADENCE_FR.get(s.cadence, s.cadence)
        out.append(
            Card(
                f"series:{s.key}",
                "question",
                20,
                f"{s.merchant} : {eur(-s.amount_cents)} {cadence}, c'est voulu ?",
                f"Prélèvement régulier repéré ({s.occurrences} fois, dernier le {fmt_day(s.last_date)}). "
                "Les abonnements oubliés coûtent cher : je te demande une fois, puis je le compte dans tes charges.",
                (
                    Action("wanted", "Oui, c'est voulu", "primary", {"type": "ack", "note": "voulu"}),
                    Action("ended", "C'est terminé", "secondary", {"type": "ack", "note": "terminé"}),
                    Action("unknown", "Je ne le reconnais pas", "link", {"type": "ack", "note": "inconnu"}),
                ),
                evidence=f"Prochain passage prévu le {fmt_day(s.next_date)}",
                topic="operations",
            )
        )
    return out


def gen_uncategorized(ctx: Context) -> list[Card]:
    out: list[Card] = []
    options = [{"value": slug, "label": name} for slug, name in ctx.categories]
    for g in ctx.uncategorized:
        if g.count < 3:
            continue
        body = (
            f"L'assistant propose « {g.hint_name} » : confirme, ou choisis une autre enveloppe — je classe "
            "celles-ci et toutes les suivantes."
            if g.hint and g.hint_name
            else "Dis-moi l'enveloppe : je classe celles-ci et toutes les suivantes."
        )
        out.append(
            Card(
                f"merchant:{g.merchant}",
                "question",
                25,
                f"{g.merchant} : {g.count} opérations non classées ({eur(g.total_cents)})",
                body,
                (
                    Action(
                        "classify", "Classer", "primary", {"type": "rule", "merchant": g.merchant, "from_input": True}
                    ),
                    dismiss("Ignorer ce marchand"),
                ),
                why="Des opérations classées = un budget juste et des prévisions fiables.",
                input={"kind": "category", "options": options, "default": g.hint},
                topic="operations",
            )
        )
    return out


def _round_down_10(cents: int) -> int:
    return (cents // 1_000) * 1_000


def gen_month_review(ctx: Context) -> list[Card]:
    r = ctx.month_review
    if r is None or ctx.today.day > 7 or r.days_of_data < 20:
        return []
    month = month_name(r.month).capitalize()
    left = r.income_cents - r.spent_cents
    if left <= 0:
        return [
            Card(
                f"review:{r.month}",
                "notice",
                15,
                f"{month} : tu as dépensé {eur(-left)} de plus que reçu",
                f"Reçu {eur(r.income_cents)}, dépensé {eur(r.spent_cents)}. Ça arrive ; l'important est de voir où "
                "c'est parti.",
                (navigate("operations", "Voir où c'est parti"), done("OK")),
                topic="argent",
            )
        ]
    goal = _round_down_10(min(left, max(r.left_avg_cents, 0)) // 2)
    if goal < 2_000 or ctx.savings_goal_cents >= goal:
        return [
            Card(
                f"review:{r.month}",
                "notice",
                15,
                f"{month} : il t'est resté {eur(left)}",
                f"Reçu {eur(r.income_cents)}, dépensé {eur(r.spent_cents)}. Beau mois.",
                (done("Merci"),),
                topic="argent",
            )
        ]
    return [
        Card(
            f"review:{r.month}",
            "question",
            15,
            f"{month} : il t'est resté {eur(left)}. On en met une partie de côté chaque mois ?",
            f"Reçu {eur(r.income_cents)}, dépensé {eur(r.spent_cents)}. Sur trois mois, il te reste en moyenne "
            f"{eur(r.left_avg_cents)} : {eur(goal)} par mois serait réservé avant le « reste à dépenser ».",
            (
                Action(
                    "yes",
                    f"Oui, {eur(goal)} par mois",
                    "primary",
                    {"type": "setting", "key": "savings_goal_cents", "value": goal},
                ),
                Action(
                    "other",
                    "Un autre montant",
                    "secondary",
                    {"type": "setting", "key": "savings_goal_cents", "from_input": True},
                ),
                dismiss("Non merci"),
            ),
            why="Une épargne décidée d'avance est la seule qui arrive vraiment.",
            input={"kind": "money", "placeholder": str(goal // 100)},
            topic="argent",
        )
    ]


def gen_payday(ctx: Context) -> list[Card]:
    if not ctx.paid_recently or ctx.savings_goal_cents <= 0 or ctx.salary is None:
        return []
    key = f"payday:{ctx.salary.last_date:%Y-%m}"
    return [
        Card(
            key,
            "tip",
            8,
            f"Ton salaire est arrivé : {eur(ctx.savings_goal_cents)} vers ton épargne maintenant ?",
            "« Se payer en premier » : le virement part le lendemain de la paie, avant les envies. "
            "Un virement automatique à cette date fait le travail sans y penser.",
            (done("C'est fait"), dismiss("Pas ce mois-ci")),
            topic="argent",
        )
    ]


def _input_for(q: Question) -> Mapping[str, Any]:
    if q.kind == "choice":
        return {"kind": "choice", "options": [{"value": v, "label": label} for v, label in q.options]}
    if q.kind == "multi":
        return {
            "kind": "multi",
            "options": [{"value": v, "label": label} for v, label in q.options],
            "max_items": q.max_items,
        }
    if q.kind == "int":
        return {"kind": "int", "minimum": q.minimum, "maximum": q.maximum}
    return {"kind": "money"}


def gen_questions(ctx: Context) -> list[Card]:
    suggested = {s.question_id for s in ctx.suggestions}
    pending = [
        q
        for q in ctx.questions
        if q.weight > 0 and q.id not in ctx.answers and q.id not in suggested and q.id != "notes"
    ]
    pending.sort(key=lambda q: (-q.weight, q.section != "mandat", q.id))
    out: list[Card] = []
    for rank, q in enumerate(pending[:MAX_QUESTION_CARDS]):
        if q.kind in ("list", "text"):
            actions: tuple[Action, ...] = (navigate("situation", "Répondre"), snooze(3), dismiss())
            inp = None
        else:
            actions = (
                Action("answer", "Enregistrer", "primary", {"type": "answer", "question_id": q.id, "from_input": True}),
                snooze(3),
                dismiss(),
            )
            inp = _input_for(q)
        out.append(
            Card(
                f"ask:{q.id}",
                "question",
                30 + rank + (3 - min(3, q.weight)),
                q.label,
                "Une question à la fois : tu réponds, j'ajuste.",
                actions,
                why=q.why,
                input=inp,
            )
        )
    return out


def gen_hygiene(ctx: Context) -> list[Card]:
    out: list[Card] = []
    if not ctx.totp_enabled:
        out.append(
            Card(
                "security:totp",
                "tip",
                40,
                "Ton accès n'est protégé que par un mot de passe",
                "Un code de ton téléphone en plus : indispensable pour des données bancaires. Deux minutes.",
                (navigate("reglages", "Activer la double authentification"), snooze(30)),
                topic="securite",
            )
        )
    soon = ctx.today + timedelta(days=10)
    for c in ctx.connections:
        if c.valid_until and c.valid_until <= soon:
            out.append(
                Card(
                    f"consent:{c.id}:{c.valid_until:%Y%m%d}",
                    "notice",
                    9,
                    f"L'accès à {c.name} expire le {fmt_day(c.valid_until)}",
                    "Les banques limitent le consentement à 180 jours : reconnecte-la pour garder la mise à jour "
                    "automatique.",
                    (navigate("connecteurs", "Reconnecter"), snooze(2)),
                    topic="operations",
                )
            )
    if ctx.energy_connected and ctx.energy_days_since_data is not None and ctx.energy_days_since_data > 7:
        out.append(
            Card(
                "energy:stale",
                "notice",
                35,
                f"Linky : aucune donnée depuis {ctx.energy_days_since_data} jours",
                "Le consentement Enedis a peut-être expiré, ou la passerelle est indisponible. Vérifie dans "
                "Connecteurs.",
                (navigate("connecteurs", "Vérifier"), snooze(7)),
                topic="operations",
            )
        )
    if ctx.salary is not None:
        year = ctx.today.year
        if ctx.latest_tax_period is None:
            out.append(
                Card(
                    "doc:avis:first",
                    "tip",
                    38,
                    "Importe ton avis d'impôt : ta tranche exacte, et des aides mieux calculées",
                    "Deux minutes sur impots.gouv (Documents › Avis d'impôt). Je lis les chiffres utiles, je jette "
                    "le numéro fiscal et l'adresse.",
                    (navigate("connecteurs", "Importer"), snooze(14), dismiss()),
                    topic="profil",
                )
            )
        elif ctx.latest_tax_period < year - 1 and ctx.today.month >= 9:
            out.append(
                Card(
                    f"doc:avis:{year - 1}",
                    "tip",
                    36,
                    f"Ton avis d'impôt sur les revenus {year - 1} est disponible",
                    "Importe-le pour une tranche d'impôt et un revenu N-2 à jour (les aides en dépendent).",
                    (navigate("connecteurs", "Importer"), snooze(14)),
                    topic="profil",
                )
            )
    return out


def gen_planning(ctx: Context) -> list[Card]:
    p = ctx.planning
    out: list[Card] = []
    if ctx.salary is None:
        return out
    if p is None or not p.connected:
        out.append(
            Card(
                "planning:connect",
                "tip",
                32,
                "Relie ton agenda de travail : je prévoirai le montant de ta paie, pas seulement sa date",
                f"Je sais déjà que {ctx.salary.merchant} te paie vers le {ctx.salary.day_of_month or '?'} "
                f"({eur(ctx.salary.median_cents)} d'habitude). Avec tes astreintes, heures ou jours planifiés, "
                "je calcule la prochaine paie, et j'apprends ce que chaque événement te rapporte.",
                (navigate("connecteurs", "Relier mon agenda"), snooze(14), dismiss()),
                topic="planning",
            )
        )
        return out
    if not p.has_rules:
        out.append(
            Card(
                "planning:rules",
                "question",
                18,
                "Comment ta paie varie-t-elle avec ton planning ?",
                "Astreinte payée tant, heure de nuit majorée, jour travaillé… Une règle par événement : je ferai le "
                "calcul, "
                "et je corrigerai les montants avec tes prochaines paies.",
                (navigate("connecteurs", "Régler les règles de paie"), snooze(7), dismiss()),
                topic="planning",
            )
        )
    if p.pay_date and p.predicted_cents is not None and p.usual_cents:
        diff = p.predicted_cents - p.usual_cents
        if abs(diff) >= max(2_000, p.usual_cents * 0.03):
            sign = "+" if diff > 0 else "−"
            explained = (
                ", ".join(f"{n} × {pattern}" for pattern, n, total in p.extras if total) or "d'après ton planning"
            )
            out.append(
                Card(
                    f"planning:pay:{p.pay_date:%Y%m%d}:{p.predicted_cents}",
                    "notice",
                    7,
                    f"Paie du {fmt_day(p.pay_date)} : {eur(p.predicted_cents)} prévus ({sign}{eur(abs(diff))})",
                    f"D'après ton planning : {explained}. Le budget du mois en tient déjà compte.",
                    (done("Vu"), navigate("connecteurs", "Détail", "link")),
                    topic="planning",
                )
            )
    return out


_SUGGEST_TITLES = {
    "ft_actualisation": "Tu reçois France Travail : je te rappelle l'actualisation chaque mois ?",
    "caf_dtr": "Tu touches la prime d'activité ou le RSA : je suis ta déclaration trimestrielle ?",
    "msa_dtr": "Tu touches une prestation MSA : je suis ta déclaration trimestrielle ?",
    "tax_return": "Je te rappelle la déclaration de revenus et la date limite de ton département ?",
}
_SUGGEST_BODIES = {
    "ft_actualisation": "Du 28 au 15 du mois suivant, sinon radiation et allocation non versée. Je te préviens à "
    "l'ouverture, trois jours avant la fin, et je constate que c'est fait quand le virement arrive.",
    "caf_dtr": "Tous les trois mois, sinon prime d'activité ou RSA suspendus. Je prépare les chiffres des trois mois "
    "de référence depuis tes relevés, et je constate la déclaration au versement suivant.",
    "msa_dtr": "Tous les trois mois, sinon prestation suspendue. Je prépare les chiffres et je constate la déclaration "
    "au versement suivant.",
    "tax_return": "D'avril à la date limite de ta zone. Elle sert aussi au calcul de tes aides (APL, LEP, "
    "Complémentaire santé solidaire).",
}


def gen_obligations(ctx: Context) -> list[Card]:
    """Déclarations récurrentes : une proposition quand les versements la trahissent, puis la fenêtre ouverte,
    l'échéance proche, le retard, le versement qui manque."""
    out: list[Card] = []
    for s in ctx.obligation_suggestions:
        out.append(
            Card(
                f"obligation:suggest:{s.kind}",
                "question",
                12,
                _SUGGEST_TITLES.get(s.kind, f"Je suis « {s.label} » pour toi ?"),
                _SUGGEST_BODIES.get(s.kind, "Je te préviens à l'ouverture, avant l'échéance, et en cas de retard."),
                (
                    Action("obligation_on", "Oui, surveille", "primary", {"type": "obligation", "kind": s.kind}),
                    snooze(14),
                    Action("obligation_off", "Non merci", "link", {"type": "obligation_dismiss", "kind": s.kind}),
                ),
                why="Un oubli suspend l'aide ou crée un indu : Bank peut le voir venir d'après tes versements.",
                topic="aides",
            )
        )
    for o in ctx.obligations:
        if o.needs_anchor:
            out.append(
                Card(
                    f"obligation:{o.id}:anchor",
                    "question",
                    10,
                    f"{o.label} : il me manque une date",
                    f"{o.anchor_label or 'La prochaine échéance'} : indique-la dans Aides pour que je calcule tes "
                    "fenêtres et tes rappels.",
                    (navigate("aides", "Régler"), snooze(7)),
                    topic="aides",
                )
            )
            continue
        done_action = Action(
            "obligation_done",
            "C'est fait",
            "secondary",
            {"type": "obligation_done", "id": o.id, "period": o.period_key},
        )
        see = navigate("aides", "Voir mes chiffres" if o.prefill_summary else "Voir", "link")
        key = f"obligation:{o.id}:{o.period_key}"
        if o.awaiting_payment:
            out.append(
                Card(
                    f"{key}:nopay",
                    "notice",
                    1,
                    f"{o.label} : le versement n'est pas arrivé",
                    f"Il devrait être là. Déclaration faite ? Vérifie vite sur le site de {o.organism} : "
                    f"{o.consequence}",
                    (navigate("aides", "Voir"), snooze(1, "Demain")),
                    topic="aides",
                )
            )
        elif o.state == "late":
            out.append(
                Card(
                    f"{key}:late",
                    "notice",
                    1,
                    f"En retard : {o.label}",
                    f"La date limite était le {fmt_day(o.deadline)}. {o.consequence} Fais-la maintenant, puis "
                    "dis-le moi.",
                    (done_action, see),
                    topic="aides",
                )
            )
        elif o.state in ("open", "soon"):
            left = (o.deadline - ctx.today).days
            when = "aujourd'hui, dernier jour" if left <= 0 else f"jusqu'au {fmt_day(o.deadline)}"
            body = o.what if o.state == "open" else f"Plus que {left} jour{'s' if left > 1 else ''}. {o.consequence}"
            if o.prefill_summary:
                body += f" Tes chiffres sont prêts : {o.prefill_summary}."
            if o.automatic:
                body += " Pour toi, c'est normalement automatique : vérifie seulement le courrier."
            out.append(
                Card(
                    f"{key}:{o.state}",
                    "notice",
                    2 if o.state == "soon" else 4,
                    f"{o.label} : {when}",
                    body,
                    (done_action, see, snooze(1, "Demain")),
                    topic="aides",
                )
            )
    return out


def gen_provision(ctx: Context) -> list[Card]:
    """Les pics de l'année (assurance annuelle, impôts, factures trimestrielles) lissés par une provision mensuelle,
    réservée avant le reste à dépenser."""
    p = ctx.provision
    if p is None or ctx.savings_goal_cents >= p.target_cents:
        return []
    risk = " Sans ça, ton solde passerait sous zéro vers " + p.lowest + "." if p.status == "risk" else ""
    return [
        Card(
            p.key,
            "tip",
            6 if p.status == "risk" else 14,
            f"Mettre {eur(p.provision_cents)} de côté chaque mois pour tes pics ?",
            f"Sur l'année qui vient, {p.count} dépense{'s' if p.count > 1 else ''} trimestrielle"
            f"{'s' if p.count > 1 else ''} ou annuelle{'s' if p.count > 1 else ''} ({eur(p.yearly_cents)} au total, "
            f"la plus grosse : {p.biggest}). "
            f"Réservée chaque mois avec ton épargne visée ({eur(ctx.savings_goal_cents)} → {eur(p.target_cents)}), "
            f"elle ne surprend plus personne.{risk}",
            (
                Action(
                    "yes",
                    "Oui, réserver",
                    "primary",
                    {"type": "setting", "key": "savings_goal_cents", "value": p.target_cents},
                ),
                Action(
                    "fix",
                    "Un autre montant",
                    "secondary",
                    {"type": "setting", "key": "savings_goal_cents", "from_input": True},
                ),
                snooze(30),
                dismiss(),
            ),
            why="Le reste à dépenser du jour déduit l'épargne visée : les pics sont ainsi payés d'avance, sans "
            "découvert.",
            input={"kind": "money", "placeholder": str(p.target_cents // 100)},
            topic="argent",
        )
    ]


def gen_correlations(ctx: Context) -> list[Card]:
    """Ce que tes contrats et tes relevés disent ensemble, quand ça coûte : une carte par constat sérieux."""
    out: list[Card] = []
    for c in ctx.correlations:
        actions: list[Action] = []
        if c.has_action:
            actions.append(
                Action("corr_start", "Préparer la démarche", "primary", {"type": "correlation_start", "key": c.key})
            )
        actions.append(navigate("situation", "Voir les pièces", "link" if c.has_action else "primary"))
        actions.append(Action("corr_dismiss", "Pas un problème", "link", {"type": "correlation_dismiss", "key": c.key}))
        stake = f" ({eur(c.yearly_cents)} par an en jeu)" if c.yearly_cents else ""
        out.append(
            Card(
                f"correlation:{c.key}",
                "notice",
                2 if c.level == "danger" else 5,
                f"{c.title}{stake}",
                c.body,
                tuple(actions),
                why="Bank croise tes contrats lus dans Papiers avec tes relevés : ce que chacun tait séparément.",
                topic="argent",
            )
        )
    return out


GENERATORS = (
    gen_start,
    gen_celebrations,
    gen_obligations,
    gen_correlations,
    gen_provision,
    gen_planning,
    gen_payday,
    gen_facts,
    gen_salary_change,
    gen_month_review,
    gen_series,
    gen_uncategorized,
    gen_questions,
    gen_hygiene,
)


def visible(card: Card, states: Mapping[str, State], today: date) -> bool:
    st = states.get(card.key)
    if st is None:
        return True
    if st.status in ("done", "dismissed"):
        return False
    return st.until is not None and st.until <= today


def build_cards(ctx: Context) -> list[Card]:
    cards: list[Card] = []
    seen: set[str] = set()
    for gen in GENERATORS:
        for c in gen(ctx):
            if c.key in seen:
                continue
            seen.add(c.key)
            if visible(c, ctx.states, ctx.today):
                cards.append(c)
    cards.sort(key=lambda c: (c.priority, c.key))
    return cards


def card_to_dict(c: Card) -> dict[str, Any]:
    return {
        "key": c.key,
        "kind": c.kind,
        "priority": c.priority,
        "title": c.title,
        "body": c.body,
        "why": c.why,
        "evidence": c.evidence,
        "topic": c.topic,
        "input": dict(c.input) if c.input else None,
        "actions": [{"id": a.id, "label": a.label, "style": a.style, "effect": dict(a.effect)} for a in c.actions],
    }
