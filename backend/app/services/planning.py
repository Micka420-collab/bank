"""Planning de travail : agenda iCal (adresse privée ou fichier), paies passées, paie prévue.

Correlation que Bank fait seul : il connaît l'employeur (libellé des virements de salaire), le jour de paie
(série récurrente) et les montants reçus ; l'agenda apporte les astreintes, heures et jours travaillés. Avec
un modèle de paie (déclaré, puis appris), la prochaine paie devient un montant, pas seulement une date.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.planning import (
    MAX_ICS_BYTES,
    LearnedModel,
    PayModel,
    PayObservation,
    PayPrediction,
    PayRule,
    PlanningError,
    Shift,
    learn,
    model_from_dict,
    model_to_dict,
    parse_ics,
    predict,
    typical_base,
    validate_rules,
)
from app.domain.recurring import RecurringSeries, add_months
from app.models import Category, Transaction, WorkPlan, WorkShift
from app.security import audit

PAST_MONTHS = 8  # historique gardé pour apprendre (paies passées)
FUTURE_MONTHS = 3
MAX_SHIFT_ROWS = 20_000
FETCH_SECONDS = 20.0
USER_AGENT = "Bank/0.3 (self-hosted personal finance)"
Fetcher = Callable[[str], bytes]
Resolver = Callable[[str], list[str]]


class PlanningFetchError(PlanningError):
    pass


# ------------------------------------------------------------------ adresse iCal : jamais vers le réseau local
def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise PlanningFetchError("Adresse de l'agenda introuvable (DNS)") from exc
    return sorted({str(i[4][0]) for i in infos})


def validate_url(url: str, resolve: Resolver | None = None) -> str:
    """https seulement, hôte public : une adresse vers le réseau du serveur (SSRF) est refusée avant tout appel."""
    resolve = resolve or _resolve  # résolu à l'appel : remplaçable en test
    url = url.strip()
    if len(url) > 2_000:
        raise PlanningFetchError("Adresse trop longue")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise PlanningFetchError("L'adresse de l'agenda doit commencer par https://")
    if parts.username or parts.password:
        raise PlanningFetchError("Adresse avec identifiants refusée")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".arpa")):
        raise PlanningFetchError("Adresse locale refusée")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        addresses = resolve(host)
    else:
        addresses = [str(literal)]
    if not addresses:
        raise PlanningFetchError("Adresse de l'agenda introuvable (DNS)")
    for a in addresses:
        ip = ipaddress.ip_address(a)
        if not ip.is_global or ip.is_multicast:
            raise PlanningFetchError("Adresse locale refusée")
    return url


def fetch_ics(url: str, http: httpx.Client | None = None, *, resolve: Resolver | None = None) -> bytes:
    """Télécharge l'agenda : taille bornée, délai borné, aucune redirection suivie (elle pourrait viser le LAN)."""
    url = validate_url(url, resolve)
    client = http or httpx.Client(timeout=httpx.Timeout(FETCH_SECONDS, connect=10.0), follow_redirects=False)
    started = time.monotonic()
    chunks: list[bytes] = []
    size = 0
    try:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*"}) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                raise PlanningFetchError("L'adresse redirige ailleurs : utilise l'adresse finale de l'agenda")
            if resp.status_code != 200:
                raise PlanningFetchError(f"L'agenda répond {resp.status_code}")
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_ICS_BYTES:
                    raise PlanningFetchError("Agenda trop volumineux (2 Mo au plus)")
                if time.monotonic() - started > FETCH_SECONDS * 2:
                    raise PlanningFetchError("Agenda trop lent à répondre")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise PlanningFetchError("Agenda injoignable (réseau)") from exc
    finally:
        if http is None:
            client.close()
    return b"".join(chunks)


# ------------------------------------------------------------------ état
def plan(db: Session) -> WorkPlan | None:
    return db.get(WorkPlan, 1)


def pay_model(db: Session) -> PayModel | None:
    row = plan(db)
    if row is None or not row.model:
        return None
    return model_from_dict(json.loads(row.model))


def window(today: date) -> tuple[date, date]:
    return add_months(today.replace(day=1), -PAST_MONTHS), add_months(
        today.replace(day=1), FUTURE_MONTHS + 1
    ) - timedelta(days=1)


def _store_shifts(db: Session, row: WorkPlan, shifts: list[Shift]) -> int:
    db.execute(delete(WorkShift).where(WorkShift.plan_id == row.id))
    kept = shifts[:MAX_SHIFT_ROWS]
    db.add_all(
        WorkShift(
            plan_id=row.id,
            day=s.day,
            start_day=s.start_day or s.day,
            hours=s.hours,
            all_day=s.all_day,
            title=s.title,
            uid_hash=hashlib.sha256(s.uid.encode()).hexdigest()[:32] if s.uid else "",
        )
        for s in kept
    )
    return len(kept)


def _calendar_label(data: bytes) -> str:
    for line in data[:20_000].decode("utf-8", "replace").splitlines():
        if line.startswith("X-WR-CALNAME:"):
            return line.split(":", 1)[1].strip()[:80] or "Planning"
    return "Planning"


def connect(
    db: Session,
    *,
    actor: str,
    today: date,
    url: str | None = None,
    data: bytes | None = None,
    fetch: Fetcher = fetch_ics,
) -> tuple[WorkPlan, int]:
    """Relie l'agenda (adresse privée https, ou fichier .ics) et lit les événements de la fenêtre utile."""
    if (url is None) == (data is None):
        raise PlanningError("Donne soit l'adresse de l'agenda, soit un fichier .ics")
    if url is not None:
        url = validate_url(url)
        data = fetch(url)
    if data is None:  # pragma: no cover - exclu par le test ci-dessus, garde mypy honnête
        raise PlanningError("Agenda vide")
    start, end = window(today)
    shifts = parse_ics(data, window_start=start, window_end=end)
    row = plan(db)
    if row is None:
        row = WorkPlan(id=1)
        db.add(row)
    row.source = "url" if url else "file"
    row.url = url
    row.label = _calendar_label(data)
    row.status, row.last_error, row.last_sync_at = "active", None, utcnow()
    db.flush()
    count = _store_shifts(db, row, shifts)
    if row.model is None:
        base = typical_base(observations(db, today))
        if base:
            row.model = json.dumps(model_to_dict(PayModel(base, (), 0)))
    audit.record(db, actor, "planning.connect", source=row.source, shifts=count)
    return row, count


def disconnect(db: Session, actor: str) -> None:
    row = plan(db)
    if row is None:
        return
    db.execute(delete(WorkShift).where(WorkShift.plan_id == row.id))
    db.delete(row)
    audit.record(db, actor, "planning.disconnect")


def sync(db: Session, today: date, *, fetch: Fetcher = fetch_ics) -> int:
    """Relit l'agenda (sources par adresse seulement). Retourne le nombre de journées enregistrées."""
    row = plan(db)
    if row is None or row.source != "url" or not row.url:
        return 0
    try:
        data = fetch(row.url)
        start, end = window(today)
        shifts = parse_ics(data, window_start=start, window_end=end)
    except PlanningError as exc:
        row.status, row.last_error = "error", str(exc)[:300]
        audit.record(db, "system", "planning.sync_failed")
        return 0
    count = _store_shifts(db, row, shifts)
    row.status, row.last_error, row.last_sync_at = "active", None, utcnow()
    return count


def set_model(
    db: Session, *, actor: str, base_cents: int | None, rules: Any, offset_months: int, today: date
) -> PayModel:
    row = plan(db)
    if row is None:
        raise PlanningError("Relie d'abord ton agenda")
    if offset_months not in (0, 1):
        raise PlanningError("Décalage de paie : 0 (même mois) ou 1 (mois précédent)")
    clean_rules = validate_rules(rules)
    current = pay_model(db)
    base = (
        base_cents
        if base_cents is not None
        else (current.base_cents if current else typical_base(observations(db, today)) or 0)
    )
    if isinstance(base, bool) or not isinstance(base, int) or not 0 <= base <= 100_000_000:
        raise PlanningError("Part fixe invalide")
    model = PayModel(base, clean_rules, offset_months, base_learned=False)
    row.model = json.dumps(model_to_dict(model))
    audit.record(db, actor, "planning.model", rules=len(clean_rules))
    return model


# ------------------------------------------------------------------ lecture
def shifts(db: Session, start: date | None = None, end: date | None = None) -> list[Shift]:
    row = plan(db)
    if row is None:
        return []
    stmt = select(WorkShift).where(WorkShift.plan_id == row.id).order_by(WorkShift.day)
    if start is not None:
        stmt = stmt.where(WorkShift.day >= start)
    if end is not None:
        stmt = stmt.where(WorkShift.day <= end)
    return [Shift(s.day, s.hours, s.all_day, s.title, s.uid_hash, s.start_day) for s in db.scalars(stmt)]


def observations(db: Session, today: date) -> list[PayObservation]:
    """Paies passées : total des crédits « salaire » par mois, datés du versement principal (hors mois en cours)."""
    since = add_months(today.replace(day=1), -PAST_MONTHS)
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.slug == "salaire",
            Transaction.amount_cents > 0,
            Transaction.is_transfer.is_(False),
            Transaction.booking_date >= since,
            Transaction.booking_date < today.replace(day=1),
        )
    )
    per_month: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for d, amount in rows:
        per_month[f"{d.year:04d}-{d.month:02d}"].append((d, amount))
    out: list[PayObservation] = []
    for credits in per_month.values():
        main_date = max(credits, key=lambda c: c[1])[0]
        out.append(PayObservation(main_date, sum(a for _, a in credits)))
    return sorted(out, key=lambda o: o.pay_date)


def learned_model(db: Session, today: date) -> LearnedModel | None:
    model = pay_model(db)
    if model is None or not model.rules:
        return None
    rows = shifts(db)
    if not rows:
        return None
    obs = observations(db, today)
    # Seules les paies dont la période est couverte par l'agenda apprennent quelque chose.
    first_day, last_day = rows[0].day, rows[-1].day
    covered = [o for o in obs if first_day <= o.pay_date <= add_months(last_day, 1)]
    return learn(model.rules, rows, covered, model.offset_months)


def effective_model(db: Session, today: date) -> PayModel | None:
    """Le modèle appris quand il existe et colle à la réalité (écart médian < 5 % de la base), sinon le déclaré."""
    declared = pay_model(db)
    if declared is None:
        return None
    learned = learned_model(db, today)
    if learned and learned.residual_cents < max(2_000, learned.model.base_cents * 0.05):
        return learned.model
    return declared


def next_pay_dates(salary: RecurringSeries | None, today: date, count: int = 3) -> list[date]:
    if salary is None:
        return []
    first = salary.next_date if salary.next_date >= today else add_months(salary.next_date, 1, salary.day_of_month)
    return [add_months(first, i, salary.day_of_month) for i in range(count)]


def predictions(db: Session, today: date, salary: RecurringSeries | None) -> list[PayPrediction]:
    model = effective_model(db, today)
    if model is None or salary is None or not model.rules:
        return []  # sans règle, la paie prévue serait la paie habituelle : rien à dire de plus
    rows = shifts(db)
    if not rows:
        return []
    out = []
    for pay_date in next_pay_dates(salary, today):
        p = predict(model, rows, pay_date)
        if p.period_end > rows[-1].day and p.shifts_in_period == 0:
            break  # l'agenda ne couvre pas (encore) cette période : pas de prévision inventée
        out.append(p)
    return out


def predicted_amount_for(db: Session, today: date, salary: RecurringSeries | None) -> int | None:
    """Montant prévu de la prochaine paie, pour la prévision du mois ; None si rien ne permet de le dire."""
    preds = predictions(db, today, salary)
    return preds[0].amount_cents if preds else None


def _prediction_dict(p: PayPrediction) -> dict[str, Any]:
    return {
        "pay_date": p.pay_date.isoformat(),
        "period_start": p.period_start.isoformat(),
        "period_end": p.period_end.isoformat(),
        "amount_cents": p.amount_cents,
        "base_cents": p.base_cents,
        "extras": [
            {
                "pattern": e.pattern,
                "unit": e.unit,
                "amount_cents": e.amount_cents,
                "count": e.count,
                "hours": e.hours,
                "total_cents": e.total_cents,
                "learned": e.learned,
            }
            for e in p.extras
        ],
        "worked_days": p.worked_days,
        "worked_hours": p.worked_hours,
        "shifts_in_period": p.shifts_in_period,
    }


def status(db: Session, today: date, salary: RecurringSeries | None) -> dict[str, Any]:
    row = plan(db)
    out: dict[str, Any] = {
        "connected": row is not None,
        "employer": salary.merchant if salary else None,
        "pay_day": salary.day_of_month if salary else None,
        "usual_pay_cents": salary.amount_cents if salary else None,
        "next_pay_date": salary.next_date.isoformat() if salary else None,
        "plan": None,
        "model": None,
        "learned": None,
        "predictions": [],
        "upcoming": [],
    }
    if row is None:
        return out
    rows = shifts(db)
    out["plan"] = {
        "source": row.source,
        "label": row.label,
        "status": row.status,
        "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
        "last_error": row.last_error,
        "shifts": len(rows),
        "coverage_start": rows[0].day.isoformat() if rows else None,
        "coverage_end": rows[-1].day.isoformat() if rows else None,
    }
    model = pay_model(db)
    out["model"] = model_to_dict(model) if model else None
    learned = learned_model(db, today)
    if learned:
        out["learned"] = {
            **model_to_dict(learned.model),
            "periods": learned.periods,
            "residual_cents": learned.residual_cents,
            "note": learned.note,
        }
    out["predictions"] = [_prediction_dict(p) for p in predictions(db, today, salary)]
    horizon = today + timedelta(days=14)
    out["upcoming"] = [
        {"day": s.day.isoformat(), "title": s.title, "hours": s.hours, "all_day": s.all_day}
        for s in rows
        if today <= s.day <= horizon
    ][:40]
    return out


def summary_for_agent(db: Session, today: date, salary: RecurringSeries | None) -> dict[str, Any] | None:
    """Vue compacte : employeur, jour de paie, prochaine paie prévue et ce qui l'explique (pas l'agenda lui-même)."""
    if salary is None:
        return None
    preds = predictions(db, today, salary)
    out: dict[str, Any] = {
        "employer": salary.merchant,
        "pay_day": salary.day_of_month,
        "usual_pay_cents": salary.amount_cents,
        "next_pay_date": salary.next_date.isoformat(),
        "planning_connected": plan(db) is not None,
    }
    if preds:
        p = preds[0]
        out["next_pay_predicted_cents"] = p.amount_cents
        out["next_pay_explained"] = [
            {"pattern": e.pattern, "count": e.count, "total_cents": e.total_cents} for e in p.extras if e.total_cents
        ]
        out["worked_days_in_period"] = p.worked_days
    return out


def declared_rule_hint(db: Session) -> list[PayRule]:
    model = pay_model(db)
    return list(model.rules) if model else []
