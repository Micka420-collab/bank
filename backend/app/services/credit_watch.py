"""Crédit et coûts : rassemble ce que Bank sait déjà (comptes courants et leurs opérations, prêts suivis, agenda de
travail, trajet que tu as indiqué, estimation de l'impôt de l'année, seuils de l'usure datés) et laisse le domaine
dire ce qui ne colle pas. Lecture seule pour les constats, aucun appel réseau : les avis du Journal officiel sont
relus avec les barèmes (services.baremes), sous l'accord « Barèmes tenus à jour ».

Écritures : ton trajet domicile-travail (chiffré) et la clé Webstat facultative (chiffrée, jamais réaffichée)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import credit_watch as dom
from app.domain import income_watch as iw
from app.domain import rates
from app.domain.correlations import Finding
from app.models import Account, AccountKind, CommuteProfile, Loan, PublicSourceKey, RateObservation, Transaction
from app.security import audit
from app.services import income_watch, loans, planning
from app.services.income_watch import guarded

HISTORY_DAYS = 320  # agios des 200 derniers jours et les 100 jours qui les précèdent
WEBSTAT = "webstat"
WEBSTAT_SIGNUP = "https://webstat.banque-france.fr/signup/"


class CreditWatchError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ seuils de l'usure
def observed_usury(db: Session) -> list[dom.UsuryQuarter]:
    out = []
    for row in db.scalars(select(RateObservation).where(RateObservation.series == dom.USURY_SERIES)):
        q = dom.usury_from_observation(row.period, row.value, row.note, row.source_url)
        if q is not None:
            out.append(q)
    return out


def usury_quarters(db: Session) -> dict[str, dom.UsuryQuarter]:
    """Les seuils datés de rates.json, recouverts des avis que Bank a relus au Journal officiel."""
    return dom.merge_usury(dom.load_usury(rates.current()), observed_usury(db))


# ------------------------------------------------------------------ découvert
def _histories(db: Session, today: date) -> list[dom.AccountHistory]:
    out: list[dom.AccountHistory] = []
    accounts = db.scalars(select(Account).where(Account.kind == AccountKind.CHECKING, Account.archived.is_(False)))
    for a in accounts:
        if a.balance_at is None:
            continue  # sans date du solde, impossible de remonter le temps
        balance_on = a.balance_at.date()
        if (today - balance_on).days > dom.FRESH_BALANCE_DAYS:
            continue
        rows = db.execute(
            select(
                Transaction.id,
                Transaction.booking_date,
                Transaction.value_date,
                Transaction.amount_cents,
                Transaction.raw_label,
            ).where(
                Transaction.account_id == a.id,
                Transaction.booking_date >= today - timedelta(days=HISTORY_DAYS),
                Transaction.booking_date <= balance_on,
            )
        )
        movements = [
            dom.Movement(i, v if v is not None and v <= balance_on else b, amount, label or "")
            for i, b, v, amount, label in rows
        ]
        out.append(dom.AccountHistory(a.id, a.name, a.balance_cents, balance_on, movements))
    return out


# ------------------------------------------------------------------ prêts
def _loans(db: Session) -> list[dom.TrackedLoan]:
    return [dom.TrackedLoan(r.id, r.name, loans.to_domain(r)) for r in db.scalars(select(Loan).order_by(Loan.id))]


# ------------------------------------------------------------------ trajet domicile-travail
def commute(db: Session) -> dom.Commute | None:
    row = db.get(CommuteProfile, 1)
    if row is None:
        return None
    try:
        data = json.loads(row.data)
        return dom.Commute(Decimal(str(data["one_way_km"])), int(data["fiscal_hp"]), bool(data.get("electric")))
    except (ValueError, KeyError, TypeError, InvalidOperation):
        return None


def commute_view(db: Session) -> dict[str, Any] | None:
    c = commute(db)
    if c is None:
        return None
    return {"one_way_km": str(c.one_way_km), "fiscal_hp": c.fiscal_hp, "electric": c.electric}


def save_commute(db: Session, *, one_way_km: Decimal, fiscal_hp: int, electric: bool, actor: str) -> None:
    if not Decimal(0) < one_way_km <= Decimal(500):
        raise CreditWatchError("Distance d'un aller : entre 0 et 500 km")
    if not 1 <= fiscal_hp <= 50:
        raise CreditWatchError("Puissance fiscale : entre 1 et 50 CV (carte grise, case P.6)")
    km = one_way_km.quantize(Decimal("0.1"))
    write_lock(db)
    row = db.get(CommuteProfile, 1) or CommuteProfile(id=1)
    row.data = json.dumps({"one_way_km": str(km), "fiscal_hp": fiscal_hp, "electric": electric})
    db.add(row)
    db.flush()
    audit.record(db, actor, "commute.save")  # jamais la distance


def clear_commute(db: Session, *, actor: str) -> None:
    write_lock(db)
    row = db.get(CommuteProfile, 1)
    if row is None:
        raise CreditWatchError("Aucun trajet enregistré", 404)
    db.delete(row)
    db.flush()
    audit.record(db, actor, "commute.clear")


def _work_days(db: Session, today: date) -> dom.WorkDays | None:
    year = today.year
    days = [s.day for s in planning.shifts(db, date(year, 1, 1), date(year, 12, 31)) if s.all_day or s.hours > 0]
    return dom.work_days(days, year)


def _salary(db: Session, today: date) -> dom.SalaryBasis | None:
    est, _rule = income_watch.pas_estimate(db, today)
    if est is None:
        return None
    inp = est.inputs
    payslips = sum(1 for m in inp.months if m.from_payslip)
    plural = "s" if payslips > 1 else ""
    origin = f"{len(inp.months)} mois de {inp.year} projetés sur l'année ({payslips} bulletin{plural} de paie)"
    return dom.SalaryBasis(est.own_yearly_cents, est.partner_yearly_cents, inp.parts, inp.couple, origin)


# ------------------------------------------------------------------ ensemble
def findings(db: Session, today: date) -> list[Finding]:
    # chaque famille de constats isolée : une donnée inattendue ne fait tomber qu'elle (income_watch.guarded)
    found = guarded("seuils de l'usure", lambda: [usury_quarters(db)])
    usury: dict[str, dom.UsuryQuarter] = found[0] if found else {}  # sans seuils : ni découvert ni usure, le reste oui

    def overdraft() -> list[Finding]:
        out: list[Finding] = []
        for hist in _histories(db, today):
            out += dom.overdraft_findings(dom.overdraft_costs(hist, today), usury)
        return out

    def loans_() -> list[Finding]:
        tracked = _loans(db)
        return dom.loan_usury_findings(tracked, usury) + dom.renegotiation_findings(
            tracked, dom.latest_averages(usury, today), today
        )

    def real_expenses() -> list[Finding]:
        raw = rates.current()
        return dom.real_expenses_findings(
            _work_days(db, today),
            commute(db),
            _salary(db, today),
            dom.load_mileage(raw),
            iw.load_tax_model(raw),
            today,
        )

    return guarded("découvert", overdraft) + guarded("crédits", loans_) + guarded("frais réels", real_expenses)


# ------------------------------------------------------------------ Webstat (Banque de France), facultatif
def webstat_status(db: Session) -> dict[str, Any]:
    row = db.get(PublicSourceKey, WEBSTAT)
    if row is None:
        message = (
            "Webstat (Banque de France) demande un compte gratuit, que tu n'as pas encore relié : Bank lit les seuils "
            "de l'usure et les taux moyens des crédits immobiliers dans l'avis trimestriel du Journal officiel."
        )
    else:
        message = (
            f"Clé Webstat enregistrée (…{row.hint}), chiffrée. Bank ne l'utilise pas encore : le point d'accès et le "
            "format de la clé n'ont pas pu être vérifiés sans compte. Les seuils viennent toujours du Journal officiel."
        )
    return {"configured": row is not None, "message": message, "signup_url": WEBSTAT_SIGNUP}


def save_webstat_key(db: Session, api_key: str, *, actor: str, now: datetime) -> None:
    clean = api_key.strip()
    if not 8 <= len(clean) <= 200 or any(c.isspace() for c in clean):
        raise CreditWatchError("Clé Webstat illisible : colle-la telle que Webstat l'affiche")
    write_lock(db)
    row = db.get(PublicSourceKey, WEBSTAT) or PublicSourceKey(slug=WEBSTAT)
    row.api_key, row.hint, row.created_at = clean, clean[-4:], now
    db.add(row)
    db.flush()
    audit.record(db, actor, "webstat.key_saved")


def clear_webstat_key(db: Session, *, actor: str) -> None:
    write_lock(db)
    row = db.get(PublicSourceKey, WEBSTAT)
    if row is not None:
        db.delete(row)
        db.flush()
        audit.record(db, actor, "webstat.key_removed")
