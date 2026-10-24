"""Plan B « perte de revenu » : si le revenu principal s'arrête, baisse de X % ou si les droits au chômage
s'épuisent — combien de mois l'épargne mobilisable tient face aux charges réelles, ce que les aides (recalculées
par OpenFisca) rattrapent, et quoi faire la première semaine, en démarches qui existent déjà dans Bank.

Pur : tout vient d'ailleurs (opérations, projection de l'année, soldes, moteur fiscal). Chaque chiffre renvoie à sa
source ; rien n'est inventé : l'ARE n'est pas simulée au centime (hors OpenFisca), Bank donne la fourchette de la
règle officielle et renvoie au simulateur de France Travail."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

from app.domain.recurring import add_months
from app.domain.yearahead import month_label

ScenarioKey = Literal["stop", "drop", "end_of_rights"]
SCENARIOS: tuple[ScenarioKey, ...] = ("stop", "drop", "end_of_rights")
DEFAULT_DROP = 30
MIN_DROP, MAX_DROP = 10, 90
MONTH_DAYS = 30.44
SETTLED_MONTHS = 3  # RSA et prime d'activité se calculent sur les trois mois précédents : « régime établi »
HORIZON_MONTHS = 36  # au-delà, Bank dit simplement « plus de trois ans »
MAX_ACTIONS = 9  # au plus : la liste complète, du J+1 au livret

# Règles datées (vérifiées le 2026-10-22 sur les pages officielles ; Bank dit quand les revérifier).
RULES_CHECKED = "2026-10-22"
RECHECK_AFTER_DAYS = 180
ARE_RATE = 0.57  # part basse : 57 % du salaire journalier de référence (SJR, brut)
ARE_CAP = 0.75  # plafond : 75 % du SJR
ARE_FIXED_RATE = 0.404  # formule alternative : 40,4 % du SJR + partie fixe
ARE_FIXED_DAY_CENTS = 1_318  # 13,18 €/jour
ARE_FLOOR_DAY_CENTS = 3_213  # allocation minimale : 32,13 €/jour
NET_TO_GROSS = 1.28  # salarié du privé : le net versé vaut environ 78 % du brut (ordre de grandeur)
ASS_DAY_CENTS = 1_948  # ASS : 19,48 €/jour au 1er avril 2026 (montant maximal, réduit selon les ressources)
ASS_CEILING_SINGLE_CENTS = 136_360  # plafond de ressources, personne seule, au 1er avril 2026
ASS_CEILING_COUPLE_CENTS = 214_280


@dataclass(frozen=True)
class Rule:
    key: str
    title: str
    url: str | None
    checked: str = RULES_CHECKED
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "url": self.url, "checked": self.checked, "detail": self.detail}


RULES: dict[str, Rule] = {
    "are": Rule(
        "are",
        "France Travail — « Comment est calculé le montant de mon allocation ? »",
        "https://www.francetravail.fr/faq/candidat/mes-allocations---mes-aides/lallocation-chomage-lessentiel-a/"
        "montant-allocation-france-travai.html",
        detail=(
            "Allocation journalière brute : le plus favorable entre 40,4 % du SJR + 13,18 €/jour, 57 % du SJR et "
            "32,13 €/jour, sans dépasser 75 % du SJR. Le SJR se calcule sur les salaires bruts de la période de "
            "référence."
        ),
    ),
    "are_simulator": Rule(
        "are_simulator",
        "France Travail — guide des simulateurs d'allocations et d'aides",
        "https://candidat.francetravail.fr/portail-simulateurs/",
        detail="Le seul calcul qui fait foi : le simulateur officiel, avec tes bulletins.",
    ),
    "ass": Rule(
        "ass",
        "France Travail — L'Allocation de Solidarité Spécifique (ASS)",
        "https://www.francetravail.fr/candidat/mes-droits-aux-aides-et-allocati/aides-financieres-et-autres-allo/"
        "autres-allocations/lallocation-de-solidarite-specif.html",
        detail=(
            "Après la fin des droits : 5 ans de travail salarié dans les 10 ans précédant la fin du contrat, "
            "ressources sous 1 363,60 €/mois (seul) ou 2 142,80 €/mois (couple) ; 19,48 €/jour au plus (1er avril "
            "2026). La demande s'ouvre dans l'espace personnel 30 jours avant la fin des droits."
        ),
    ),
    "caf_unemployment": Rule(
        "caf_unemployment",
        "Caf — « Je suis au chômage » : prévenir la Caf, ce qui change pour tes droits",
        "https://caf.fr/allocataires/aides-et-demarches/ma-situation/vie-professionnelle/je-suis-au-chomage",
    ),
    "openfisca": Rule(
        "openfisca",
        "OpenFisca-France, le moteur open source des simulateurs publics, exécuté chez toi",
        "https://fr.openfisca.org/",
        detail="RSA, prime d'activité, aide au logement, allocations familiales recalculés avec le revenu du scénario.",
    ),
}


# ------------------------------------------------------------------ entrées
@dataclass(frozen=True)
class Incomes:
    """Revenus mensuels (centimes) par nature, tels que Bank les reconnaît dans les relevés."""

    salary: int = 0
    unemployment: int = 0
    pension: int = 0
    aids: int = 0  # prestations observées (Caf, MSA…), hors chômage et retraite
    other: int = 0  # autres entrées régulières

    @property
    def total(self) -> int:
        return self.salary + self.unemployment + self.pension + self.aids + self.other

    @property
    def main(self) -> str:
        """La nature du revenu principal : ce que le scénario « ça s'arrête » fait tomber."""
        parts = {"salary": self.salary, "unemployment": self.unemployment, "pension": self.pension}
        best = max(parts, key=lambda k: parts[k])
        return best if parts[best] > 0 else "none"


@dataclass(frozen=True)
class Needs:
    """Ce qu'il faut sortir chaque mois : charges fixes réelles, rythme de dépenses, pics lissés."""

    fixed: int = 0
    variable: int = 0
    provision: int = 0

    @property
    def total(self) -> int:
        return self.fixed + self.variable + self.provision


@dataclass(frozen=True)
class Facts:
    """Ce qui oriente les démarches : logement, crédits, abonnements, couple, profil renseigné."""

    tenant: bool = False
    credit_monthly_cents: int = 0
    subscriptions_monthly_cents: int = 0
    couple: bool = False
    profile_known: bool = False


@dataclass(frozen=True)
class AidEstimate:
    variable: str
    label: str
    monthly_cents: int


@dataclass(frozen=True)
class Simulation:
    """Ce que le moteur fiscal a répondu pour un jeu de revenus (ou pourquoi il n'a pas répondu)."""

    estimates: tuple[AidEstimate, ...] = ()
    css: str = "non_eligible"  # gratuite | participation | non_eligible
    lep_eligible: bool = False
    engine_version: str = ""
    error: str = ""

    @property
    def total_cents(self) -> int:
        return sum(e.monthly_cents for e in self.estimates)


# ------------------------------------------------------------------ ARE : la fourchette, jamais le centime
@dataclass(frozen=True)
class AreRange:
    low_cents: int  # 57 % du SJR estimé, par mois
    high_cents: int  # 75 % du SJR estimé, par mois
    formula_cents: int  # la formule officielle appliquée au SJR estimé (brut), par mois
    sjr_day_cents: int  # salaire journalier de référence estimé (brut)
    floor_applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "low_cents": self.low_cents,
            "high_cents": self.high_cents,
            "formula_cents": self.formula_cents,
            "sjr_day_cents": self.sjr_day_cents,
            "floor_applied": self.floor_applied,
            "rule": RULES["are"].to_dict(),
            "simulator": RULES["are_simulator"].to_dict(),
            "note": (
                "Fourchette indicative, en brut, à partir de ton salaire net des relevés (brut estimé × 1,28 ; "
                "le SJR réel dépend de tes bulletins et des jours travaillés). Avant CSG/CRDS éventuelles, et "
                "seulement si tu y as droit (perte involontaire d'emploi, durée d'affiliation suffisante). Le "
                "simulateur officiel fait foi."
            ),
        }


def are_range(net_salary_cents: int) -> AreRange | None:
    """La règle officielle appliquée à un SJR estimé depuis le net : trois formules, un plancher, un plafond."""
    if net_salary_cents <= 0:
        return None
    gross_month = net_salary_cents * NET_TO_GROSS
    sjr = gross_month / MONTH_DAYS
    fixed_formula = ARE_FIXED_RATE * sjr + ARE_FIXED_DAY_CENTS
    rate_formula = ARE_RATE * sjr
    best = max(fixed_formula, rate_formula, ARE_FLOOR_DAY_CENTS)
    capped = min(best, ARE_CAP * sjr)
    return AreRange(
        low_cents=round(ARE_RATE * sjr * MONTH_DAYS),
        high_cents=round(ARE_CAP * sjr * MONTH_DAYS),
        formula_cents=round(capped * MONTH_DAYS),
        sjr_day_cents=round(sjr),
        floor_applied=best == ARE_FLOOR_DAY_CENTS and capped == best,
    )


# ------------------------------------------------------------------ scénarios : les revenus d'après
def scenario_incomes(before: Incomes, drop_percent: int = DEFAULT_DROP) -> dict[str, Incomes]:
    """Les revenus de chaque scénario. « stop » fait tomber le revenu principal ; si c'est un salaire, l'ARE entre
    à sa part basse (prudence) à la place. « drop » le réduit de X %. « end_of_rights » : plus de salaire ni de
    chômage, ce qui reste (retraite, prestations, autres) demeure."""
    pct = max(MIN_DROP, min(MAX_DROP, int(drop_percent)))
    main = before.main
    stop = before
    if main == "salary":
        are = are_range(before.salary)
        stop = replace(before, salary=0, unemployment=max(before.unemployment, are.low_cents if are else 0))
    elif main == "unemployment":
        stop = replace(before, unemployment=0)
    elif main == "pension":
        stop = replace(before, pension=0)
    if main == "salary":
        drop = replace(before, salary=round(before.salary * (100 - pct) / 100))
    elif main == "unemployment":
        drop = replace(before, unemployment=round(before.unemployment * (100 - pct) / 100))
    elif main == "pension":
        drop = replace(before, pension=round(before.pension * (100 - pct) / 100))
    else:
        drop = before
    end = replace(before, salary=0, unemployment=0)
    return {"stop": stop, "drop": drop, "end_of_rights": end}


# ------------------------------------------------------------------ sorties
@dataclass(frozen=True)
class AidDelta:
    variable: str
    label: str
    before_cents: int
    after_cents: int

    @property
    def delta_cents(self) -> int:
        return self.after_cents - self.before_cents


@dataclass(frozen=True)
class Action:
    key: str
    title: str
    why: str
    when: str  # J+1 | semaine 1 | avant la fin des droits
    kind: str  # procedure | link | check
    template: str = ""  # démarche du catalogue (kind = procedure)
    url: str | None = None
    amount_cents: int = 0
    rule: str = ""  # clé de RULES

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "title": self.title,
            "why": self.why,
            "when": self.when,
            "kind": self.kind,
            "amount_cents": self.amount_cents,
        }
        if self.template:
            out["template"] = self.template
        if self.url:
            out["url"] = self.url
        if self.rule and self.rule in RULES:
            out["rule"] = RULES[self.rule].to_dict()
        return out


@dataclass(frozen=True)
class Scenario:
    key: ScenarioKey
    title: str
    lead: str
    incomes_after: Incomes
    income_before_cents: int
    income_after_cents: int
    needs_cents: int
    gap_cents: int  # ce qui manque chaque mois (positif) ; négatif ou nul : ça passe
    cash_cents: int
    months: float | None  # None : pas d'entame de l'épargne
    runway_month: str | None  # mois où l'épargne mobilisable serait à zéro
    status: str  # ok | tight | risk
    are: AreRange | None
    aids: tuple[AidDelta, ...]
    aids_total_before_cents: int
    aids_total_after_cents: int
    aids_note: str
    css: str
    actions: tuple[Action, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PlanB:
    today: date
    drop_percent: int
    incomes: Incomes
    needs: Needs
    cash_cents: int
    main_income: str
    scenarios: tuple[Scenario, ...]
    sources: tuple[dict[str, Any], ...]
    rules_recheck: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def _status(months: float | None) -> str:
    if months is None or months >= 6:
        return "ok"
    return "tight" if months >= 3 else "risk"


def _runway(cash: int, gap: int, today: date) -> tuple[float | None, str | None]:
    if gap <= 0:
        return None, None
    months = max(0.0, cash / gap)
    if months > HORIZON_MONTHS:
        return round(months, 1), None
    exhausted = add_months(today.replace(day=1), int(months))
    return round(months, 1), f"{exhausted.year:04d}-{exhausted.month:02d}"


def _deltas(before: Simulation | None, after: Simulation | None) -> tuple[AidDelta, ...]:
    if before is None or after is None or after.error:
        return ()
    labels: dict[str, str] = {}
    pre = {e.variable: e.monthly_cents for e in before.estimates}
    post = {e.variable: e.monthly_cents for e in after.estimates}
    for e in (*before.estimates, *after.estimates):
        labels[e.variable] = e.label
    out = [AidDelta(v, labels[v], pre.get(v, 0), post.get(v, 0)) for v in labels]
    out.sort(key=lambda d: (-abs(d.delta_cents), d.variable))
    return tuple(d for d in out if d.before_cents or d.after_cents)


def _titles(main: str, pct: int) -> dict[ScenarioKey, tuple[str, str]]:
    what = {"salary": "Ton salaire", "unemployment": "Ton allocation chômage", "pension": "Ta pension"}.get(
        main, "Ton revenu principal"
    )
    return {
        "stop": (
            f"{what} s'arrête",
            "Du jour au lendemain : licenciement, fin de contrat, rupture."
            if main == "salary"
            else "Du jour au lendemain, sans relais.",
        ),
        "drop": (f"{what} baisse de {pct} %", "Temps partiel, chômage partiel, mission qui rapporte moins."),
        "end_of_rights": (
            "Fin de droits",
            "Plus de salaire ni d'allocation chômage : il reste les prestations recalculées et l'épargne.",
        ),
    }


def _actions(  # une liste de cas, lisible d'un bloc
    key: ScenarioKey,
    before: Incomes,
    after: Incomes,
    facts: Facts,
    sim_after: Simulation | None,
    are: AreRange | None,
) -> tuple[Action, ...]:
    out: list[Action] = []
    lost_salary = before.salary > 0 and after.salary < before.salary
    sim_ok = sim_after is not None and not sim_after.error
    post = {e.variable: e.monthly_cents for e in sim_after.estimates} if sim_ok and sim_after else {}
    if key in ("stop", "end_of_rights") and before.salary > 0 and after.salary == 0:
        out.append(
            Action(
                "ft_register",
                "S'inscrire à France Travail dès le lendemain",
                "L'allocation court à partir de l'inscription, pas de la fin du contrat : chaque jour de retard est "
                "perdu. Le simulateur officiel te donne ton montant exact."
                + (
                    f" Fourchette Bank : {are.low_cents // 100:,} à {are.high_cents // 100:,} € brut par mois.".replace(
                        ",", " "
                    )
                    if are
                    else ""
                ),
                "J+1",
                "link",
                url=RULES["are_simulator"].url,
                amount_cents=are.formula_cents if are else 0,
                rule="are",
            )
        )
    if key == "end_of_rights" and (before.unemployment > 0 or before.salary > 0):
        out.append(
            Action(
                "ass_check",
                "Vérifier l'ASS avant la fin des droits",
                "Après l'ARE, l'allocation de solidarité spécifique prend le relais si tu as travaillé 5 ans sur les "
                "10 dernières années et que tes ressources restent sous le plafond : la demande s'ouvre dans ton "
                "espace France Travail 30 jours avant la fin des droits.",
                "avant la fin des droits",
                "link",
                url=RULES["ass"].url,
                amount_cents=round(ASS_DAY_CENTS * MONTH_DAYS),
                rule="ass",
            )
        )
    if before.aids > 0 or facts.profile_known or lost_salary:
        out.append(
            Action(
                "caf_declare",
                "Prévenir la Caf du changement de situation",
                "Un revenu qui change se déclare tout de suite : les droits sont recalculés et tu évites un indu "
                "plus tard."
                + (" Bank prépare ta déclaration trimestrielle avec les bons mois." if before.aids > 0 else ""),
                "semaine 1",
                "link",
                url=RULES["caf_unemployment"].url,
                rule="caf_unemployment",
            )
        )
    if post.get("ppa", 0) > 0:
        out.append(
            Action(
                "ppa",
                "Demander ou actualiser la prime d'activité",
                f"Avec ce revenu, OpenFisca l'estime à {post['ppa'] / 100:.0f} € par mois une fois la baisse "
                "installée. Bank remplit la demande avec tes relevés ; c'est toi qui l'envoies.",
                "semaine 1",
                "procedure",
                template="prime_activite",
                amount_cents=post["ppa"],
                rule="openfisca",
            )
        )
    if post.get("rsa", 0) > 0:
        out.append(
            Action(
                "rsa",
                "Demander le RSA",
                f"OpenFisca l'estime à {post['rsa'] / 100:.0f} € par mois dans ce scénario : il se demande sur caf.fr, "
                "il n'est jamais versé seul.",
                "semaine 1",
                "link",
                url=RULES["caf_unemployment"].url,
                amount_cents=post["rsa"],
                rule="openfisca",
            )
        )
    if sim_ok and sim_after and sim_after.css in ("gratuite", "participation"):
        out.append(
            Action(
                "css",
                "Demander la Complémentaire santé solidaire",
                "Gratuite ou à moins d'un euro par jour selon tes ressources : elle remplace une mutuelle qui coûte."
                if sim_after.css == "participation"
                else "Gratuite avec ces ressources : elle remplace la mutuelle, sans reste à charge sur le courant.",
                "semaine 1",
                "procedure",
                template="demander_css",
                rule="openfisca",
            )
        )
    if facts.subscriptions_monthly_cents > 0:
        out.append(
            Action(
                "subscriptions",
                "Passer en revue les abonnements",
                f"{facts.subscriptions_monthly_cents / 100:.0f} € par mois partent en abonnements : résilier ou "
                "suspendre ceux qui ne manqueront pas, en quelques clics (c'est la loi).",
                "semaine 1",
                "procedure",
                template="resilier_abonnement",
                amount_cents=facts.subscriptions_monthly_cents,
            )
        )
    if facts.credit_monthly_cents > 0:
        out.append(
            Action(
                "credit_pause",
                "Demander un report d'échéance de crédit",
                f"{facts.credit_monthly_cents / 100:.0f} € de mensualités : la plupart des contrats prévoient une "
                "pause d'une à trois échéances sur demande écrite. Mieux avant le premier impayé qu'après.",
                "semaine 1",
                "procedure",
                template="libre",
                amount_cents=facts.credit_monthly_cents,
            )
        )
    if facts.tenant and key != "drop":
        out.append(
            Action(
                "landlord",
                "Prévenir le bailleur et vérifier l'aide au logement",
                "Un mot au bailleur avant un retard vaut mieux qu'après ; l'aide au logement se recalcule avec le "
                "nouveau revenu (déclaration Caf).",
                "semaine 1",
                "check",
            )
        )
    if sim_ok and sim_after and sim_after.lep_eligible:
        out.append(
            Action(
                "lep",
                "Ouvrir un LEP pour l'épargne qui reste",
                "Avec ces revenus tu y as droit : le livret le mieux rémunéré, disponible à tout moment.",
                "semaine 1",
                "procedure",
                template="ouvrir_lep",
            )
        )
    return tuple(out[:MAX_ACTIONS])


def build(  # un scénario a beaucoup d'entrées, toutes sourcées
    *,
    today: date,
    incomes: Incomes,
    needs: Needs,
    cash_cents: int,
    facts: Facts,
    drop_percent: int = DEFAULT_DROP,
    baseline: Simulation | None = None,
    simulations: dict[str, Simulation] | None = None,
    engine_note: str = "",
) -> PlanB:
    """Assemble les scénarios. ``simulations`` : la réponse d'OpenFisca par clé de scénario (revenus d'après),
    ``baseline`` : la même pour les revenus d'aujourd'hui ; sans moteur, les prestations observées sont supposées
    inchangées et Bank le dit."""
    pct = max(MIN_DROP, min(MAX_DROP, int(drop_percent)))
    variants = scenario_incomes(incomes, pct)
    sims = simulations or {}
    titles = _titles(incomes.main, pct)
    out: list[Scenario] = []
    for key in SCENARIOS:
        after = variants[key]
        if key == "end_of_rights" and (after == variants["stop"] or after == incomes):
            continue  # déjà couvert (le revenu principal était l'allocation chômage) ou sans objet (retraite)
        sim_after = sims.get(key)
        salary_are = are_range(incomes.salary) if incomes.main == "salary" else None
        are = salary_are if key == "stop" else None
        deltas = _deltas(baseline, sim_after)
        if deltas or (sim_after is not None and not sim_after.error and baseline is not None):
            aids_before = baseline.total_cents if baseline else incomes.aids
            aids_after = sim_after.total_cents if sim_after else incomes.aids
            version = sim_after.engine_version if sim_after and sim_after.engine_version else "?"
            aids_note = (
                f"Prestations recalculées par OpenFisca-France {version}, une fois la baisse installée "
                f"({SETTLED_MONTHS} mois de revenus au nouveau niveau)."
            )
            income_after = after.salary + after.unemployment + after.pension + after.other + aids_after
        else:
            aids_before = aids_after = incomes.aids
            aids_note = engine_note or (
                "Prestations supposées inchangées : renseigne ton foyer (Ma situation) pour que Bank les recalcule."
            )
            income_after = after.total
        gap = needs.total - income_after
        months, runway = _runway(cash_cents, gap, today)
        notes: list[str] = []
        if key == "stop" and are:
            notes.append(
                "L'ARE entre dans ce scénario à sa part basse (57 % du SJR estimé), par prudence ; elle démarre "
                "après un différé et seulement si tu y as droit."
            )
        if months is not None and months > HORIZON_MONTHS:
            notes.append("Plus de trois ans d'autonomie : au-delà, la projection n'a plus de sens.")
        out.append(
            Scenario(
                key=key,
                title=titles[key][0],
                lead=titles[key][1],
                incomes_after=after,
                income_before_cents=incomes.total,
                income_after_cents=income_after,
                needs_cents=needs.total,
                gap_cents=gap,
                cash_cents=cash_cents,
                months=months,
                runway_month=runway,
                status=_status(months),
                are=are,
                aids=deltas,
                aids_total_before_cents=aids_before,
                aids_total_after_cents=aids_after,
                aids_note=aids_note,
                css=sim_after.css if sim_after and not sim_after.error else "",
                actions=_actions(key, incomes, after, facts, sim_after, salary_are),
                notes=tuple(notes),
            )
        )
    sources: list[dict[str, Any]] = [
        {
            "key": "incomes",
            "title": "Revenus : médiane des trois derniers mois complets de tes relevés, par nature",
            "kind": "operations",
        },
        {
            "key": "needs",
            "title": "Charges fixes et rythme de dépenses : prélèvements réguliers détectés, dépenses habituelles, "
            "pics lissés (« L'année qui vient »)",
            "kind": "operations",
        },
        {
            "key": "cash",
            "title": "Épargne mobilisable : comptes courants et livrets (jamais un placement bloqué ni la crypto)",
            "kind": "accounts",
        },
        {**RULES["are"].to_dict(), "kind": "rule"},
        {**RULES["ass"].to_dict(), "kind": "rule"},
        {**RULES["openfisca"].to_dict(), "kind": "rule"},
    ]
    general: list[str] = []
    if incomes.main == "none":
        general.append("Aucun revenu principal reconnu (salaire, chômage, retraite) : les scénarios ne font tomber que "
                       "ce que Bank connaît.")  # fmt: skip
    if needs.variable == 0 and needs.fixed == 0:
        general.append("Pas assez d'historique pour connaître tes charges : les mois d'autonomie seraient flatteurs.")
    recheck = (today - date.fromisoformat(RULES_CHECKED)).days > RECHECK_AFTER_DAYS
    if recheck:
        general.append(
            f"Les règles ARE/ASS ont été vérifiées le {RULES_CHECKED} : elles changent chaque année, revérifie-les."
        )
    return PlanB(
        today=today,
        drop_percent=pct,
        incomes=incomes,
        needs=needs,
        cash_cents=cash_cents,
        main_income=incomes.main,
        scenarios=tuple(out),
        sources=tuple(sources),
        rules_recheck=recheck,
        notes=tuple(general),
    )


# ------------------------------------------------------------------ vues
def incomes_to_dict(i: Incomes) -> dict[str, int]:
    return {
        "salary_cents": i.salary,
        "unemployment_cents": i.unemployment,
        "pension_cents": i.pension,
        "aids_cents": i.aids,
        "other_cents": i.other,
        "total_cents": i.total,
    }


def scenario_to_dict(s: Scenario) -> dict[str, Any]:
    return {
        "key": s.key,
        "title": s.title,
        "lead": s.lead,
        "incomes_after": incomes_to_dict(s.incomes_after),
        "income_before_cents": s.income_before_cents,
        "income_after_cents": s.income_after_cents,
        "lost_cents": s.income_before_cents - s.income_after_cents,
        "needs_cents": s.needs_cents,
        "gap_cents": s.gap_cents,
        "cash_cents": s.cash_cents,
        "months": s.months,
        "runway_month": s.runway_month,
        "runway_label": month_label(s.runway_month) if s.runway_month else None,
        "status": s.status,
        "are": s.are.to_dict() if s.are else None,
        "aids": [
            {
                "variable": d.variable,
                "label": d.label,
                "before_cents": d.before_cents,
                "after_cents": d.after_cents,
                "delta_cents": d.delta_cents,
            }
            for d in s.aids
        ],
        "aids_total_before_cents": s.aids_total_before_cents,
        "aids_total_after_cents": s.aids_total_after_cents,
        "aids_note": s.aids_note,
        "css": s.css,
        "actions": [a.to_dict() for a in s.actions],
        "notes": list(s.notes),
    }


def to_dict(p: PlanB) -> dict[str, Any]:
    return {
        "today": p.today.isoformat(),
        "drop_percent": p.drop_percent,
        "incomes": incomes_to_dict(p.incomes),
        "needs": {
            "fixed_cents": p.needs.fixed,
            "variable_cents": p.needs.variable,
            "provision_cents": p.needs.provision,
            "total_cents": p.needs.total,
        },
        "cash_cents": p.cash_cents,
        "main_income": p.main_income,
        "scenarios": [scenario_to_dict(s) for s in p.scenarios],
        "sources": list(p.sources),
        "rules_checked": RULES_CHECKED,
        "rules_recheck": p.rules_recheck,
        "notes": list(p.notes),
        "disclaimer": (
            "Un ordre de grandeur à partir de tes relevés et des règles publiques, pas une promesse : l'ARE se "
            "calcule sur tes bulletins (simulateur officiel), les prestations sur ta déclaration à la Caf. Bank "
            "n'envoie rien à un organisme : chaque démarche est préparée ici, transmise par toi."
        ),
    }
