"""Onboarding « Ma situation » : ce qu'un gestionnaire d'argent doit savoir de toi pour bien décider.

Trois idées :
1. Chaque question dit POURQUOI elle compte (affiché à toi et à l'agent) : pas de collecte gratuite.
2. Ce que tes relevés révèlent déjà (salaire, loyer, crédits, capacité d'épargne) est pré-rempli :
   tu confirmes au lieu de taper — et l'agent sait ce qui est déclaré vs détecté.
3. Une complétude pondérée dit à l'agent ce qu'il lui manque, dans l'ordre d'importance.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.domain.money import AmountError, decimal_to_cents

Kind = Literal["choice", "multi", "money", "int", "text", "list"]


# ------------------------------------------------------------------ éléments de liste
class Debt(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["immobilier", "conso", "auto", "etudiant", "familial", "decouvert", "autre"] = "autre"
    remaining_cents: int = Field(ge=0, le=10_000_000_000)
    monthly_cents: int = Field(ge=0, le=100_000_000)
    rate_percent: Decimal = Field(default=Decimal(0), ge=0, le=40)
    end_date: date | None = None


class OutsideAsset(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["pea", "assurance_vie", "per", "epargne_salariale", "crypto", "immobilier", "autre"] = "autre"
    value_cents: int = Field(ge=0, le=10_000_000_000)
    monthly_contribution_cents: int = Field(default=0, ge=0, le=100_000_000)


class LifeGoal(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["securite", "achat", "immobilier", "voyage", "retraite", "enfants", "liberte", "autre"] = "autre"
    amount_cents: int = Field(default=0, ge=0, le=10_000_000_000)
    target_date: date | None = None
    priority: int = Field(default=2, ge=1, le=3)  # 1 = indispensable


class PlannedExpense(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    amount_cents: int = Field(ge=0, le=1_000_000_000)
    date: date


class RedLine(BaseModel):
    rule: str = Field(min_length=3, max_length=160)

    @field_validator("rule")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())


LIST_ITEMS: dict[str, type[BaseModel]] = {
    "debts": Debt,
    "outside_assets": OutsideAsset,
    "life_goals": LifeGoal,
    "planned_expenses": PlannedExpense,
    "red_lines": RedLine,
}


# ------------------------------------------------------------------ catalogue
@dataclass(frozen=True)
class Question:
    id: str
    section: str
    label: str
    why: str
    kind: Kind
    weight: int = 1
    options: tuple[tuple[str, str], ...] = ()
    max_items: int = 20
    minimum: int | None = None
    maximum: int | None = None


SECTIONS: tuple[tuple[str, str], ...] = (
    ("travail", "Revenus et travail"),
    ("foyer", "Foyer et logement"),
    ("dettes", "Crédits"),
    ("epargne", "Épargne et placements"),
    ("projets", "Projets"),
    ("risque", "Rapport au risque"),
    ("valeurs", "Ce qui compte pour toi"),
    ("mandat", "Le mandat de ton agent"),
)

QUESTIONS: tuple[Question, ...] = (
    Question(
        "employment",
        "travail",
        "Quelle est ta situation professionnelle ?",
        "La stabilité de ton revenu fixe la taille du matelas de sécurité et le risque raisonnable.",
        "choice",
        3,
        (
            ("cdi_prive", "CDI dans le privé"),
            ("public", "Fonction publique ou établissement public"),
            ("cdd", "CDD ou intérim"),
            ("independant", "Indépendant ou freelance"),
            ("chomage", "Demandeur d'emploi"),
            ("etudiant", "Étudiant"),
            ("retraite", "Retraité"),
            ("autre", "Autre"),
        ),
    ),
    Question(
        "net_monthly_income",
        "travail",
        "Ton salaire net mensuel habituel, après impôt à la source ?",
        "C'est la base de tout calcul : budget, capacité d'épargne, endettement.",
        "money",
        3,
    ),
    Question(
        "income_variability",
        "travail",
        "Ton revenu varie-t-il d'un mois à l'autre ?",
        "Un revenu variable demande une marge plus grande et un budget calé sur les mois creux.",
        "choice",
        1,
        (("stable", "Stable"), ("un_peu", "Un peu (primes, heures sup.)"), ("beaucoup", "Beaucoup")),
    ),
    Question(
        "yearly_extras",
        "travail",
        "Primes, 13e mois, intéressement : combien net par an ?",
        "Ces sommes arrivent en bloc : mieux vaut décider à l'avance de leur usage.",
        "money",
        1,
    ),
    Question(
        "side_income",
        "travail",
        "Autres revenus réguliers par mois (activité annexe, location…) ?",
        "Ils changent ta capacité d'épargne et parfois ta fiscalité.",
        "money",
        1,
    ),
    Question(
        "tax_bracket",
        "travail",
        "Ta tranche marginale d'imposition (TMI) ?",
        "Elle décide si un PER ou certains placements sont fiscalement intéressants pour toi.",
        "choice",
        1,
        (("0", "0 %"), ("11", "11 %"), ("30", "30 %"), ("41", "41 %"), ("45", "45 %"), ("inconnue", "Je ne sais pas")),
    ),
    Question(
        "household",
        "foyer",
        "Tu vis…",
        "Le foyer change les aides, le reste à vivre et les priorités.",
        "choice",
        2,
        (
            ("seul", "Seul"),
            ("couple", "En couple"),
            ("couple_enfants", "En couple avec enfants"),
            ("parent_solo", "Seul avec enfants"),
            ("colocation", "En colocation"),
        ),
    ),
    Question(
        "dependents",
        "foyer",
        "Combien de personnes à charge ?",
        "Chaque personne à charge augmente le matelas de sécurité nécessaire.",
        "int",
        1,
        minimum=0,
        maximum=12,
    ),
    Question(
        "housing_status",
        "foyer",
        "Ton logement ?",
        "Locataire ou propriétaire : les stratégies diffèrent.",
        "choice",
        2,
        (
            ("locataire", "Locataire"),
            ("proprietaire_credit", "Propriétaire avec crédit"),
            ("proprietaire", "Propriétaire sans crédit"),
            ("heberge", "Hébergé"),
        ),
    ),
    Question(
        "housing_cost",
        "foyer",
        "Ton coût de logement mensuel (loyer ou mensualité) ?",
        "Au-delà d'un tiers du revenu, le budget devient fragile.",
        "money",
        2,
    ),
    Question(
        "housing_project",
        "foyer",
        "Un projet immobilier ?",
        "Un achat proche impose de garder l'apport disponible, pas investi en actions.",
        "choice",
        1,
        (
            ("aucun", "Aucun"),
            ("achat_5ans", "Acheter d'ici 5 ans"),
            ("achat_plus", "Acheter plus tard"),
            ("demenager", "Déménager bientôt"),
        ),
    ),
    Question(
        "debts",
        "dettes",
        "Tes crédits en cours (laisse vide si aucun) ?",
        "Rembourser un crédit cher rapporte souvent plus qu'épargner : c'est une priorité à calculer.",
        "list",
        3,
        max_items=10,
    ),
    Question(
        "outside_assets",
        "epargne",
        "Placements non suivis dans Bank (PEA, assurance vie, PER, crypto…) ?",
        "Sans eux, l'agent sous-estime ton patrimoine et ton exposition au risque.",
        "list",
        2,
        max_items=15,
    ),
    Question(
        "emergency_target_months",
        "epargne",
        "Combien de mois de dépenses veux-tu garder de côté ?",
        "Le matelas de sécurité passe avant tout placement risqué. Repère courant : 3 à 6 mois.",
        "int",
        2,
        minimum=1,
        maximum=24,
    ),
    Question(
        "savings_capacity",
        "epargne",
        "Combien peux-tu mettre de côté par mois sans te priver ?",
        "C'est le moteur de tous tes objectifs.",
        "money",
        2,
    ),
    Question(
        "life_goals",
        "projets",
        "Tes projets et objectifs (montant, échéance, priorité) ?",
        "L'agent arbitre entre tes projets : il doit savoir lesquels comptent le plus et quand.",
        "list",
        3,
        max_items=12,
    ),
    Question(
        "planned_expenses",
        "projets",
        "Grosses dépenses prévues dans les 12 mois ?",
        "Les anticiper évite qu'elles cassent le budget le mois venu.",
        "list",
        2,
        max_items=20,
    ),
    Question(
        "risk_horizon",
        "risque",
        "L'argent que tu placerais, tu pourrais t'en passer combien de temps ?",
        "Plus l'horizon est long, plus les fluctuations peuvent être supportées.",
        "choice",
        1,
        (("moins_2", "Moins de 2 ans"), ("2_5", "2 à 5 ans"), ("5_10", "5 à 10 ans"), ("plus_10", "Plus de 10 ans")),
    ),
    Question(
        "risk_drop",
        "risque",
        "Un placement perd 20 % en six mois. Tu…",
        "Ta réaction réelle compte plus que le rendement espéré.",
        "choice",
        1,
        (
            ("vends_tout", "Vends tout"),
            ("vends_partie", "Vends une partie"),
            ("attends", "Attends que ça remonte"),
            ("renforces", "En profites pour en racheter"),
        ),
    ),
    Question(
        "risk_experience",
        "risque",
        "Ton expérience des placements ?",
        "On n'investit que dans ce que l'on comprend.",
        "choice",
        1,
        (
            ("aucune", "Aucune"),
            ("livrets", "Livrets uniquement"),
            ("quelques", "Quelques placements"),
            ("reguliere", "J'investis régulièrement"),
        ),
    ),
    Question(
        "risk_priority",
        "risque",
        "Entre sécurité et rendement, tu préfères…",
        "C'est l'arbitrage central de toute allocation.",
        "choice",
        1,
        (
            ("securite", "La sécurité avant tout"),
            ("plutot_securite", "Plutôt la sécurité"),
            ("equilibre", "Un équilibre"),
            ("plutot_rendement", "Plutôt le rendement"),
        ),
    ),
    Question(
        "priorities",
        "valeurs",
        "Ce qui compte le plus pour toi (3 au plus) ?",
        "L'agent arbitre selon TES valeurs, pas selon une moyenne.",
        "multi",
        1,
        (
            ("securite", "Me sentir en sécurité"),
            ("liberte", "Ma liberté"),
            ("projets", "Réaliser mes projets"),
            ("plaisir", "Profiter du présent"),
            ("famille", "Ma famille"),
            ("retraite", "Préparer la retraite"),
            ("independance", "L'indépendance financière"),
            ("generosite", "Aider les autres"),
        ),
        max_items=3,
    ),
    Question(
        "money_stress",
        "valeurs",
        "En ce moment, à quel point l'argent te stresse-t-il (1 = pas du tout, 5 = beaucoup) ?",
        "L'agent adapte son ton et la fréquence de ses alertes.",
        "int",
        1,
        minimum=1,
        maximum=5,
    ),
    Question(
        "notes",
        "valeurs",
        "Autre chose que ton agent doit savoir ?",
        "Contexte libre : un changement de vie, une contrainte, une habitude. "
        "Lu par ton agent (sauf en mode agrégats).",
        "text",
        0,
    ),
    Question(
        "autonomy",
        "mandat",
        "Quel rôle donnes-tu à ton agent ?",
        "Il ne déplace jamais d'argent : il observe, conseille, et propose des réglages que tu valides.",
        "choice",
        2,
        (
            ("observateur", "Il observe et m'informe"),
            ("conseiller", "Il me conseille et propose, je valide tout"),
            ("gestionnaire", "Il range seul (classement), propose le reste"),
        ),
    ),
    Question(
        "report_frequency",
        "mandat",
        "À quelle fréquence veux-tu un point ?",
        "Un bon conseiller est présent sans être envahissant.",
        "choice",
        1,
        (
            ("quotidien", "Chaque matin"),
            ("hebdomadaire", "Chaque semaine"),
            ("mensuel", "Chaque mois"),
            ("alertes", "Seulement en cas d'alerte"),
        ),
    ),
    Question(
        "alert_threshold",
        "mandat",
        "Préviens-moi pour toute dépense imprévue au-dessus de…",
        "Fixe le seuil au-delà duquel une dépense mérite un signalement.",
        "money",
        1,
    ),
    Question(
        "red_lines",
        "mandat",
        "Tes lignes rouges (ex. « jamais de crédit conso », « pas de crypto ») ?",
        "L'agent ne proposera jamais rien qui les franchit.",
        "list",
        1,
        max_items=10,
    ),
    Question(
        "tone",
        "mandat",
        "Comment veux-tu qu'il te parle ?",
        "Le fond reste le même, la forme s'adapte.",
        "choice",
        1,
        (
            ("direct", "Direct et concis"),
            ("pedagogique", "Pédagogue, explique ses calculs"),
            ("encourageant", "Encourageant"),
        ),
    ),
)
BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}


class AnswerError(ValueError):
    pass


def _to_cents(value: Any, unit: str) -> int:
    if isinstance(value, bool):
        raise AnswerError("montant attendu")
    if unit == "cents":
        if not isinstance(value, int):
            raise AnswerError("montant en centimes (entier) attendu")
        return value
    try:
        return decimal_to_cents(str(value).replace(",", ".").replace(" ", "").replace("€", ""))
    except AmountError as exc:
        raise AnswerError("montant en euros attendu") from exc


def _convert_item_money(item: Any, unit: str) -> Any:
    """Côté agent, les montants sont donnés en euros (`remaining_eur`) et convertis en `remaining_cents`."""
    if unit == "cents" or not isinstance(item, Mapping):
        return item
    out: dict[str, Any] = {}
    for key, value in item.items():
        if key.endswith("_eur"):
            try:
                out[key[:-4] + "_cents"] = _to_cents(value, "euros")
            except AnswerError as exc:
                raise AnswerError(f"{key} : montant en euros attendu") from exc
        else:
            out[key] = value
    return out


def validate_answer(question_id: str, value: Any, unit: Literal["cents", "euros"] = "cents") -> Any:
    """Valide et normalise une réponse. Lève AnswerError avec un message lisible (aussi pour l'agent)."""
    q = BY_ID.get(question_id)
    if q is None:
        raise AnswerError(f"Question inconnue : {question_id}")
    keys = {k for k, _ in q.options}
    if q.kind == "choice":
        if not isinstance(value, str) or value not in keys:
            raise AnswerError(f"Choix attendu parmi : {', '.join(sorted(keys))}")
        return value
    if q.kind == "multi":
        if not isinstance(value, list) or not value or len(value) > q.max_items or not set(value) <= keys:
            raise AnswerError(f"Liste de 1 à {q.max_items} choix parmi : {', '.join(sorted(keys))}")
        return list(dict.fromkeys(value))
    if q.kind == "money":
        cents = _to_cents(value, unit)
        if not 0 <= cents <= 10_000_000_000:
            raise AnswerError("Montant hors limites")
        return cents
    if q.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise AnswerError("Nombre entier attendu")
        if (q.minimum is not None and value < q.minimum) or (q.maximum is not None and value > q.maximum):
            raise AnswerError(f"Entre {q.minimum} et {q.maximum}")
        return value
    if q.kind == "text":
        if not isinstance(value, str) or len(value) > 2000:
            raise AnswerError("Texte de 2000 caractères au plus")
        return " ".join(value.split())
    # list
    if not isinstance(value, list) or len(value) > q.max_items:
        raise AnswerError(f"Liste de {q.max_items} éléments au plus attendue")
    model = LIST_ITEMS[question_id]
    items: list[BaseModel] = []
    for n, raw in enumerate(value):
        try:
            items.append(model.model_validate(_convert_item_money(raw, unit)))
        except AnswerError as exc:
            raise AnswerError(f"Élément {n + 1} invalide ({exc})") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            raise AnswerError(f"Élément {n + 1} invalide ({where}) : {first['msg']}") from exc
    return [i.model_dump(mode="json") for i in items]


# ------------------------------------------------------------------ complétude
@dataclass(frozen=True)
class Completeness:
    percent: int
    answered: int
    total: int
    missing: list[Question]


def completeness(answers: Mapping[str, Any]) -> Completeness:
    weighted = [q for q in QUESTIONS if q.weight > 0]
    total = sum(q.weight for q in weighted)
    done = sum(q.weight for q in weighted if q.id in answers)
    missing = sorted((q for q in weighted if q.id not in answers), key=lambda q: -q.weight)
    return Completeness(round(100 * done / total), sum(1 for q in weighted if q.id in answers), len(weighted), missing)


# ------------------------------------------------------------------ profil de risque
_RISK_POINTS: dict[str, dict[str, int]] = {
    "risk_horizon": {"moins_2": 1, "2_5": 2, "5_10": 3, "plus_10": 4},
    "risk_drop": {"vends_tout": 1, "vends_partie": 2, "attends": 3, "renforces": 4},
    "risk_experience": {"aucune": 1, "livrets": 1, "quelques": 2, "reguliere": 3},
    "risk_priority": {"securite": 1, "plutot_securite": 2, "equilibre": 3, "plutot_rendement": 4},
}


@dataclass(frozen=True)
class RiskProfile:
    label: str  # prudent | equilibre | dynamique | inconnu
    score: float | None
    capacity_limited: bool  # matelas incomplet ou dettes chères : la capacité de risque prime sur l'envie
    reason: str


def risk_profile(answers: Mapping[str, Any], emergency_months: float, expensive_debt: bool) -> RiskProfile:
    points = [_RISK_POINTS[q][answers[q]] for q in _RISK_POINTS if answers.get(q) in _RISK_POINTS[q]]
    target = answers.get("emergency_target_months") or 3
    limited = emergency_months < target or expensive_debt
    reason = (
        "Le matelas de sécurité n'est pas encore constitué"
        if emergency_months < target
        else "Un crédit à taux élevé coûte plus que ce qu'un placement rapporte"
        if expensive_debt
        else "Matelas constitué, pas de dette chère"
    )
    if len(points) < 2:
        return RiskProfile("inconnu", None, limited, reason)
    score = sum(points) / len(points)
    label = "prudent" if score <= 1.75 else "equilibre" if score <= 2.75 else "dynamique"
    return RiskProfile(label, round(score, 2), limited, reason)


def recommended_emergency_months(answers: Mapping[str, Any]) -> int:
    """Repère indicatif selon la stabilité du revenu et le foyer (affiché comme suggestion, jamais imposé)."""
    base = {"public": 3, "cdi_prive": 3, "retraite": 3, "cdd": 6, "independant": 6, "chomage": 6}.get(
        str(answers.get("employment")), 4
    )
    if answers.get("income_variability") == "beaucoup":
        base += 2
    return min(12, base + min(2, int(answers.get("dependents") or 0)))


# ------------------------------------------------------------------ pré-remplissage
@dataclass(frozen=True)
class Suggestion:
    question_id: str
    value: Any
    evidence: str


@dataclass(frozen=True)
class DetectedFacts:
    salary_monthly_cents: int | None = None
    salary_months: int = 0
    housing_cost_cents: int | None = None
    housing_label: str | None = None
    credit_lines: Sequence[tuple[str, int]] = ()  # (marchand, mensualité)
    savings_capacity_cents: int | None = None
    household_couple: bool | None = None
    children: int | None = None
    housing_status: str | None = None
    tracked_kinds: Sequence[str] = ()


def suggestions(facts: DetectedFacts, answers: Mapping[str, Any]) -> list[Suggestion]:
    out: list[Suggestion] = []

    def add(qid: str, value: Any, evidence: str) -> None:
        if qid not in answers:
            out.append(Suggestion(qid, value, evidence))

    if facts.salary_monthly_cents:
        add(
            "net_monthly_income",
            facts.salary_monthly_cents,
            f"Médiane de tes salaires détectés sur {facts.salary_months} mois",
        )
    if facts.housing_cost_cents:
        add("housing_cost", facts.housing_cost_cents, f"Prélèvement mensuel {facts.housing_label or 'logement'}")
        add(
            "housing_status",
            "locataire"
            if facts.housing_status in (None, "locataire_vide", "locataire_meuble", "locataire_hlm", "locataire_foyer")
            else "proprietaire_credit",
            "Déduit de ton profil et de tes prélèvements",
        )
    if facts.credit_lines:
        add(
            "debts",
            [
                {
                    "label": m.title(),
                    "kind": "autre",
                    "remaining_cents": 0,
                    "monthly_cents": c,
                    "rate_percent": "0",
                    "end_date": None,
                }
                for m, c in facts.credit_lines
            ],
            "Mensualités récurrentes classées en crédit : complète le capital restant et le taux",
        )
    if facts.savings_capacity_cents is not None and facts.savings_capacity_cents > 0:
        add("savings_capacity", facts.savings_capacity_cents, "Moyenne de ce qu'il t'est resté sur 3 mois complets")
    if facts.household_couple is not None:
        kids = facts.children or 0
        value = (
            ("couple_enfants" if kids else "couple") if facts.household_couple else ("parent_solo" if kids else "seul")
        )
        add("household", value, "D'après ton profil de foyer (simulation des aides)")
    if facts.children is not None:
        add("dependents", facts.children, "D'après ton profil de foyer")
    return out
