"""Planning de travail -> paie prévue.

Bank connaît déjà ton salaire (virements reçus), ton employeur (libellé) et ton jour de paie (série récurrente).
Avec ton planning (agenda iCal : astreintes, heures, jours travaillés), il peut dire *combien* tu vas toucher
le mois prochain, pas seulement *quand* : une part fixe, plus des règles « tel événement rapporte tant ».
Quand l'historique le permet, les montants des règles sont **appris** par régression sur les paies passées.

Tout est pur ici : l'agenda arrive en octets, les paies passées en liste, la prévision ressort.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from icalendar import Calendar

from app.domain.recurring import add_months

MAX_ICS_BYTES = 2_000_000
MAX_EVENTS = 5_000  # VEVENT lus au plus
MAX_OCCURRENCES_PER_EVENT = 1_000  # une règle de répétition ne peut pas « déborder »
MAX_HOURS_PER_SHIFT = 24.0
DEFAULT_TZ = "Europe/Paris"
HOURS_PER_DAY = 7.0  # une journée entière compte 7 h quand une règle est horaire

UNITS = ("per_event", "per_day", "per_hour")


class PlanningError(ValueError):
    pass


@dataclass(frozen=True)
class Shift:
    """Une occurrence d'événement, ramenée à une journée : un événement sur trois jours donne trois Shift."""

    day: date
    hours: float  # 0 pour une journée entière (voir HOURS_PER_DAY)
    all_day: bool
    title: str
    uid: str = ""
    start_day: date | None = None  # premier jour de l'occurrence (identité d'un événement sur plusieurs jours)

    @property
    def event_key(self) -> tuple[str, date]:
        return (self.uid or self.title, self.start_day or self.day)


# ------------------------------------------------------------------ lecture iCal
def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().split())


def _as_local_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(tz) if value.tzinfo else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime.combine(value, time(0), tzinfo=tz)
    return None


def _split_days(start: datetime, end: datetime, all_day: bool, title: str, uid: str) -> list[Shift]:
    """Découpe [start, end[ en journées locales. Journée entière : DTEND est exclusif (RFC 5545)."""
    out: list[Shift] = []
    if end <= start:
        end = start + timedelta(hours=1) if not all_day else start + timedelta(days=1)
    cursor = start
    guard = 0
    while cursor < end and guard < 366:
        guard += 1
        next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time(0), tzinfo=cursor.tzinfo)
        segment_end = min(end, next_midnight)
        hours = 0.0 if all_day else min(MAX_HOURS_PER_SHIFT, (segment_end - cursor).total_seconds() / 3600)
        out.append(Shift(cursor.date(), round(hours, 2), all_day, title, uid, start.date()))
        cursor = next_midnight
    return out


def parse_ics(
    data: bytes,
    *,
    window_start: date,
    window_end: date,
    tz_name: str = DEFAULT_TZ,
) -> list[Shift]:
    """Occurrences (une par jour) dans [window_start, window_end], répétitions développées, exceptions respectées.

    Lève PlanningError pour un fichier illisible ou trop gros. Les événements annulés ou transparents
    (« disponible ») sont ignorés.
    """
    if len(data) > MAX_ICS_BYTES:
        raise PlanningError("Agenda trop volumineux (2 Mo au plus)")
    try:
        cal = Calendar.from_ical(data)
    except Exception as exc:  # icalendar lève ValueError, mais aussi des erreurs internes sur un fichier forgé
        raise PlanningError("Agenda illisible : exporte-le au format iCal (.ics)") from exc
    tz = ZoneInfo(tz_name)
    w_start = datetime.combine(window_start, time(0), tzinfo=tz)
    w_end = datetime.combine(window_end + timedelta(days=1), time(0), tzinfo=tz)
    events = list(cal.walk("VEVENT"))[:MAX_EVENTS]
    # Une occurrence modifiée (RECURRENCE-ID) remplace celle de la règle : on ne la compte qu'une fois.
    overridden: set[tuple[str, date]] = set()
    for ev in events:
        rid = ev.get("RECURRENCE-ID")
        if rid is not None:
            local = _as_local_datetime(rid.dt, tz)
            if local is not None:
                overridden.add((str(ev.get("UID", "")), local.date()))
    shifts: list[Shift] = []
    for ev in events:
        if str(ev.get("STATUS", "")).upper() == "CANCELLED" or str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        dtstart = ev.get("DTSTART")
        if dtstart is None:
            continue
        start = _as_local_datetime(dtstart.dt, tz)
        if start is None:
            continue
        all_day = not isinstance(dtstart.dt, datetime)
        dtend = ev.get("DTEND")
        duration = ev.get("DURATION")
        if dtend is not None:
            end = _as_local_datetime(dtend.dt, tz) or start
        elif duration is not None:
            end = start + duration.dt
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
        length = end - start
        title = str(ev.get("SUMMARY", "")).strip()[:120]
        uid = str(ev.get("UID", ""))[:200]
        rrule = ev.get("RRULE")
        if rrule is None:
            occurrences: Iterable[datetime] = [start]
        else:
            try:
                rule = rrulestr(rrule.to_ical().decode("ascii", "replace"), dtstart=start)
            except (ValueError, TypeError):
                occurrences = [start]
            else:
                # Les occurrences passées de la fenêtre comptent aussi (un événement commencé la veille).
                occurrences = list(rule.between(w_start - length, w_end, inc=True))[:MAX_OCCURRENCES_PER_EVENT]
        exdates: set[date] = set()
        raw_ex = ev.get("EXDATE")
        for ex in raw_ex if isinstance(raw_ex, list) else ([raw_ex] if raw_ex is not None else []):
            for d in getattr(ex, "dts", []):
                local = _as_local_datetime(d.dt, tz)
                if local is not None:
                    exdates.add(local.date())
        for occ in occurrences:
            occ_local = occ.astimezone(tz) if occ.tzinfo else occ.replace(tzinfo=tz)
            if occ_local.date() in exdates or (rrule is not None and (uid, occ_local.date()) in overridden):
                continue
            occ_end = occ_local + length
            if occ_end <= w_start or occ_local >= w_end:
                continue
            for s in _split_days(occ_local, occ_end, all_day, title, uid):
                if window_start <= s.day <= window_end:
                    shifts.append(s)
    shifts.sort(key=lambda s: (s.day, s.title))
    return shifts


# ------------------------------------------------------------------ modèle de paie
@dataclass(frozen=True)
class PayRule:
    pattern: str  # mot-clé cherché dans le titre (sans accents ni casse) ; « * » = tout événement
    unit: str  # per_event | per_day | per_hour
    amount_cents: int
    learned: bool = False

    def matches(self, shift: Shift) -> bool:
        if self.pattern.strip() == "*":
            return True
        return _norm(self.pattern) in _norm(shift.title)


@dataclass(frozen=True)
class PayModel:
    base_cents: int  # part fixe par paie (salaire « habituel » sans événement)
    rules: tuple[PayRule, ...] = ()
    offset_months: int = 0  # 0 : la paie de M couvre M ; 1 : la paie de M couvre M-1 (variable payée à terme échu)
    base_learned: bool = False


def period_bounds(pay_date: date, offset_months: int) -> tuple[date, date]:
    """Période de travail couverte par une paie : le mois civil, décalé de offset_months."""
    first = add_months(pay_date.replace(day=1), -offset_months, day=1)
    last = add_months(first, 1, day=1) - timedelta(days=1)
    return first, last


@dataclass(frozen=True)
class RuleTotal:
    pattern: str
    unit: str
    amount_cents: int
    count: int  # événements (ou jours, ou heures selon l'unité)
    hours: float
    total_cents: int
    learned: bool


@dataclass(frozen=True)
class PayPrediction:
    pay_date: date
    period_start: date
    period_end: date
    amount_cents: int
    base_cents: int
    extras: tuple[RuleTotal, ...]
    worked_days: int
    worked_hours: float
    shifts_in_period: int


def _rule_total(rule: PayRule, shifts: Sequence[Shift], start: date, end: date) -> RuleTotal:
    """Total d'une règle sur [start, end] : heures et jours au prorata des journées dans la période ; un
    événement (même à cheval sur deux mois) compte une seule fois, dans la période où il commence."""
    matched = [s for s in shifts if rule.matches(s)]
    hours = sum(s.hours if s.hours > 0 else HOURS_PER_DAY for s in matched)
    if rule.unit == "per_hour":
        count = round(hours)
        total = round(rule.amount_cents * hours)
    elif rule.unit == "per_day":
        days = len({s.day for s in matched})
        count = days
        total = rule.amount_cents * days
    else:
        events = len({s.event_key for s in matched if start <= (s.start_day or s.day) <= end})
        count = events
        total = rule.amount_cents * events
    return RuleTotal(rule.pattern, rule.unit, rule.amount_cents, count, round(hours, 1), total, rule.learned)


def _events_count(rule: PayRule, shifts: Sequence[Shift], start: date, end: date) -> float:
    """Quantité « facturable » d'une règle sur une période : événements, jours ou heures."""
    t = _rule_total(PayRule(rule.pattern, rule.unit, 1), shifts, start, end)
    return t.hours if rule.unit == "per_hour" else float(t.count)


def predict(model: PayModel, shifts: Sequence[Shift], pay_date: date) -> PayPrediction:
    start, end = period_bounds(pay_date, model.offset_months)
    inside = [s for s in shifts if start <= s.day <= end]
    extras = tuple(_rule_total(r, inside, start, end) for r in model.rules)
    total = model.base_cents + sum(e.total_cents for e in extras)
    return PayPrediction(
        pay_date,
        start,
        end,
        max(0, total),
        model.base_cents,
        extras,
        len({s.day for s in inside}),
        round(sum(s.hours if s.hours > 0 else HOURS_PER_DAY for s in inside), 1),
        len(inside),
    )


# ------------------------------------------------------------------ apprentissage (moindres carrés)
@dataclass(frozen=True)
class PayObservation:
    pay_date: date
    amount_cents: int  # salaire réellement reçu


@dataclass(frozen=True)
class LearnedModel:
    model: PayModel
    periods: int
    residual_cents: int  # écart médian entre la prévision et la réalité sur l'historique
    note: str


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Résout A x = b par élimination de Gauss avec pivot partiel ; None si le système est singulier."""
    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-9:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                factor = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def learn(
    rules: Sequence[PayRule], shifts: Sequence[Shift], observations: Sequence[PayObservation], offset_months: int = 0
) -> LearnedModel | None:
    """Estime la part fixe et le montant de chaque règle à partir des paies reçues.

    Modèle : paie = base + Σ montant_règle × quantité_règle. Il faut au moins 4 paies, et pour chaque règle une
    quantité qui varie d'une période à l'autre (sinon elle est indiscernable de la part fixe et reste déclarée).
    """
    obs = sorted(observations, key=lambda o: o.pay_date)
    if len(obs) < 4:
        return None
    rows: list[list[float]] = []
    ys: list[float] = []
    for o in obs:
        start, end = period_bounds(o.pay_date, offset_months)
        inside = [s for s in shifts if start <= s.day <= end]
        rows.append([1.0, *(_events_count(r, inside, start, end) for r in rules)])
        ys.append(float(o.amount_cents))
    # Une règle sans variation (toujours la même quantité) n'est pas identifiable : on la garde telle que déclarée.
    identifiable = [i for i, _ in enumerate(rules) if len({row[i + 1] for row in rows}) > 1]
    cols = [0, *(i + 1 for i in identifiable)]
    xt_x = [[sum(row[p] * row[q] for row in rows) for q in cols] for p in cols]
    xt_y = [sum(row[p] * y for row, y in zip(rows, ys, strict=True)) for p in cols]
    # Les règles non identifiables sont retranchées de la cible avec leur montant déclaré.
    for i, r in enumerate(rules):
        if i not in identifiable:
            for k, row in enumerate(rows):
                ys[k] -= r.amount_cents * row[i + 1]
            xt_y = [sum(row[p] * y for row, y in zip(rows, ys, strict=True)) for p in cols]
    solution = _solve(xt_x, xt_y)
    if solution is None or solution[0] <= 0:
        return None
    learned_rules: list[PayRule] = []
    for i, r in enumerate(rules):
        if i in identifiable:
            amount = round(solution[1 + identifiable.index(i)])
            learned_rules.append(PayRule(r.pattern, r.unit, max(0, amount), learned=True))
        else:
            learned_rules.append(r)
    model = PayModel(round(solution[0]), tuple(learned_rules), offset_months, base_learned=True)
    residuals = [abs(predict(model, shifts, o.pay_date).amount_cents - o.amount_cents) for o in obs]
    return LearnedModel(
        model,
        len(obs),
        round(median(residuals)),
        f"Appris sur {len(obs)} paies : écart médian {round(median(residuals)) / 100:.0f} €",
    )


def typical_base(observations: Sequence[PayObservation]) -> int | None:
    amounts = [o.amount_cents for o in observations]
    return round(median(amounts)) if amounts else None


# ------------------------------------------------------------------ validation (entrées utilisateur)
_PATTERN_RE = re.compile(r"^[\w\s\-'’*./]{1,40}$", re.UNICODE)


def validate_rules(raw: Any) -> tuple[PayRule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > 8:
        raise PlanningError("Huit règles au plus")
    rules: list[PayRule] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise PlanningError("Règle invalide")
        pattern = str(item.get("pattern", "")).strip()
        unit = str(item.get("unit", "per_event"))
        amount = item.get("amount_cents", 0)
        if not pattern or not _PATTERN_RE.match(pattern):
            raise PlanningError("Mot-clé invalide (lettres, chiffres, espaces ; « * » pour tout)")
        if unit not in UNITS:
            raise PlanningError("Unité inconnue (par événement, par jour ou par heure)")
        if isinstance(amount, bool) or not isinstance(amount, int) or not -1_000_000 <= amount <= 1_000_000:
            raise PlanningError("Montant de règle invalide")
        key = _norm(pattern)
        if key in seen:
            raise PlanningError(f"Mot-clé en double : {pattern}")
        seen.add(key)
        rules.append(PayRule(pattern, unit, amount, bool(item.get("learned", False))))
    return tuple(rules)


def model_to_dict(model: PayModel) -> dict[str, Any]:
    return {
        "base_cents": model.base_cents,
        "base_learned": model.base_learned,
        "offset_months": model.offset_months,
        "rules": [
            {"pattern": r.pattern, "unit": r.unit, "amount_cents": r.amount_cents, "learned": r.learned}
            for r in model.rules
        ],
    }


def model_from_dict(raw: Mapping[str, Any] | None) -> PayModel | None:
    if not raw:
        return None
    base = raw.get("base_cents")
    if isinstance(base, bool) or not isinstance(base, int) or not 0 <= base <= 100_000_000:
        return None
    offset = raw.get("offset_months", 0)
    if offset not in (0, 1):
        offset = 0
    try:
        rules = validate_rules(raw.get("rules"))
    except PlanningError:
        rules = ()
    return PayModel(base, rules, int(offset), bool(raw.get("base_learned", False)))


@dataclass(frozen=True)
class PlanningSummary:
    """Ce que le planning dit pour la paie à venir et les suivantes."""

    next_pay: PayPrediction | None
    following: tuple[PayPrediction, ...] = ()
    learned: LearnedModel | None = None
    shifts_total: int = 0
    coverage_start: date | None = None
    coverage_end: date | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
