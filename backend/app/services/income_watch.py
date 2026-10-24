"""Revenus et prestations : rassemble ce que Bank sait déjà (versements reconnus, bulletins et avis lus dans Papiers,
virements de salaire, profil du foyer, dernière simulation OpenFisca, barèmes datés) et laisse le domaine dire ce
qui ne colle pas. Lecture seule, aucun appel réseau : les constats rejoignent les corrélations, le solde de
septembre estimé rejoint l'Année qui vient."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import income_watch as dom
from app.domain import invest, rates
from app.domain.correlations import Finding
from app.domain.recurring import add_months
from app.models import (
    Category,
    FiscalSimulation,
    OfficialFact,
    RateObservation,
    Transaction,
    VaultDocument,
    VaultField,
)
from app.services import analytics

READ_STATUSES = ("to_review", "verified")
log = logging.getLogger("bank.income_watch")


def guarded[T](label: str, produce: Callable[[], list[T]]) -> list[T]:
    """Un constat facultatif qui casse sur une donnée inattendue (pièce lue de travers, simulation ancienne) ne fait
    tomber que lui : les Corrélations, l'Année qui vient et le Plan B restent lisibles. Seul le type d'erreur est
    journalisé, jamais la donnée."""
    try:
        return produce()
    except Exception as exc:  # isolation voulue, voir la docstring
        log.warning("constats %s ignorés : %s", label, type(exc).__name__)
        return []


# ------------------------------------------------------------------ revalorisations
def _bmaf_points(db: Session) -> list[tuple[date, Decimal, str]]:
    out: list[tuple[date, Decimal, str]] = []
    for row in db.scalars(select(RateObservation).where(RateObservation.series == "openfisca.bmaf")):
        try:
            out.append((date.fromisoformat(row.period), Decimal(row.value), row.source_url))
        except (ValueError, InvalidOperation):
            continue
    return out


def revaluations(db: Session) -> tuple[list[dom.Revaluation], dom.AppealRule | None]:
    known, appeal = dom.load_revaluations(rates.current())
    return known + dom.revaluations_from_bmaf(_bmaf_points(db), known), appeal


def _aid_credits(db: Session, today: date) -> list[dom.AidCredit]:
    rows = db.execute(
        select(
            Transaction.id,
            Transaction.booking_date,
            Transaction.amount_cents,
            Transaction.aid_kind,
            Transaction.raw_label,
        ).where(
            Transaction.aid_kind.is_not(None),
            Transaction.amount_cents > 0,
            Transaction.booking_date >= add_months(today, -24),
        )
    )
    return [
        dom.AidCredit(i, d, a, str(k), bool(dom.COMPLEMENTARY.search(dom.fold(label or ""))))
        for i, d, a, k, label in rows
        if k
    ]


# ------------------------------------------------------------------ taux de prélèvement
def _docs(db: Session, kind: str) -> list[tuple[VaultDocument, dict[str, Any]]]:
    out = []
    for doc in db.scalars(
        select(VaultDocument).where(VaultDocument.kind == kind, VaultDocument.status.in_(READ_STATUSES))
    ):
        fields: dict[str, Any] = {}
        for f in db.scalars(select(VaultField).where(VaultField.document_id == doc.id)):
            try:
                fields[f.key] = json.loads(f.value)
            except ValueError:
                continue
        out.append((doc, fields))
    return out


def _percent(raw: object) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", ".")) if raw is not None and not isinstance(raw, bool) else None
    except InvalidOperation:
        return None
    return value if value is not None and value.is_finite() and Decimal(0) <= value <= Decimal(60) else None


def _avis(db: Session) -> dict[str, Any] | None:
    """Le dernier avis d'impôt connu : validé dans Documents officiels, sinon lu dans Papiers."""
    best: dict[str, Any] | None = None
    for row in db.scalars(select(OfficialFact).where(OfficialFact.kind == "avis_impot")):
        try:
            data = json.loads(row.data)
            year = int(row.period)
        except (ValueError, TypeError):
            continue
        cand = {
            "year": year,
            "rate": _percent(data.get("withholding_rate_percent")),
            "parts": data.get("parts"),
            "declarants": data.get("declarants"),
            "ref": None,
        }
        if best is None or year > best["year"]:
            best = cand
    for doc, fields in _docs(db, "tax_notice"):
        try:
            year = int(fields.get("annee_revenus") or 0)
        except (TypeError, ValueError):
            continue
        if year and (best is None or year > best["year"]):
            best = {
                "year": year,
                "rate": _percent(fields.get("taux_pas")),
                "parts": fields.get("parts"),
                "declarants": None,
                "ref": f"doc:{doc.id}",
            }
    return best


def pas_inputs(db: Session, today: date) -> dom.PasInputs | None:
    year = today.year
    payslips: dict[str, tuple[int, dict[str, Any]]] = {}
    for doc, fields in _docs(db, "payslip"):
        month = str(fields.get("periode") or "")
        if len(month) == 7 and month.startswith(f"{year:04d}-") and month < f"{today:%Y-%m}":
            payslips[month] = (doc.id, fields)
    avis = _avis(db)
    rate: dom.RateSeen | None = None
    for month in sorted(payslips, reverse=True):
        doc_id, fields = payslips[month]
        pct = _percent(fields.get("pas_taux"))
        if pct is not None:
            rate = dom.RateSeen(pct, f"bulletin de paie de {month[5:]}/{month[:4]}", f"doc:{doc_id}")
            break
    if rate is None and avis is not None and avis["rate"] is not None:
        rate = dom.RateSeen(avis["rate"], f"avis d'impôt sur les revenus {avis['year']}", avis["ref"])
    if rate is None:
        return None

    credits: dict[str, int] = defaultdict(int)
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Category.slug == "salaire",
            Transaction.amount_cents > 0,
            Transaction.is_transfer.is_(False),
            Transaction.booking_date >= date(year, 1, 1),
            Transaction.booking_date < today.replace(day=1),
        )
    )
    for d, a in rows:
        credits[f"{d:%Y-%m}"] += a
    months: list[dom.MonthIncome] = []
    for m in range(1, today.month):
        key = f"{year:04d}-{m:02d}"
        taxable = payslips.get(key, (0, {}))[1].get("net_imposable")
        if isinstance(taxable, int) and not isinstance(taxable, bool) and taxable > 0:
            months.append(dom.MonthIncome(key, taxable, True))
        elif credits.get(key):
            months.append(dom.MonthIncome(key, dom.taxable_from_credit(credits[key], rate.percent), False))

    profile = analytics.load_profile(db) or {}
    couple = bool(profile.get("couple"))
    if avis is not None and avis.get("declarants") in (1, 2):
        couple = avis["declarants"] == 2
    parts: Decimal | None = None
    parts_origin = ""
    if avis is not None and avis.get("parts") is not None:
        try:
            parts = Decimal(str(avis["parts"]))
            parts_origin = f"avis d'impôt sur les revenus {avis['year']}"
        except InvalidOperation:
            parts = None
    if parts is None and profile:
        children = len(profile.get("children_birth_dates") or [])
        parts = invest.household_parts(couple=couple, children=children)
        parts_origin = "ton profil de foyer"
    if parts is None or parts <= 0:
        return None
    partner = int(profile.get("partner_monthly_net_cents") or 0) if couple else 0
    return dom.PasInputs(
        year=year,
        rate=rate,
        months=months,
        parts=parts,
        parts_origin=parts_origin,
        couple=couple,
        partner_taxable_monthly_cents=int(Decimal(partner) * dom.TAXABLE_UPLIFT),
        partner_origin=(
            "net mensuel de ton profil de foyer, +3 % pour le net imposable" if partner else "aucun revenu indiqué"
        )
        if couple
        else None,
    )


def pas_estimate(db: Session, today: date) -> tuple[dom.PasEstimate | None, dom.WithholdingRule | None]:
    raw = rates.current()
    model, rule = dom.load_tax_model(raw), dom.load_withholding_rule(raw)
    inp = pas_inputs(db, today)
    if model is None or inp is None:
        return None, rule
    return dom.estimate_withholding(inp, model), rule


def withholding(db: Session, today: date) -> list[Finding]:
    """Le constat sur le taux ; en couple sans revenu du conjoint, ce qui manque plutôt qu'une comparaison faussée."""
    inp = pas_inputs(db, today)
    if inp is not None and dom.partner_missing(inp):
        return dom.partner_missing_findings(inp)
    return dom.withholding_findings(*pas_estimate(db, today))


def september_balance(db: Session, today: date) -> list[tuple[date, str, int]]:
    """Pour l'Année qui vient : le solde d'impôt estimé, à son mois."""
    return guarded("solde de septembre", lambda: dom.september_balance(*pas_estimate(db, today)))


# ------------------------------------------------------------------ chèque énergie
def _energy_cheque(db: Session) -> dom.EnergyCheque | None:
    row = db.scalar(select(FiscalSimulation).order_by(FiscalSimulation.id.desc()).limit(1))
    if row is None:
        return None
    try:
        result = json.loads(row.result)
        amount = result.get("cheque_energie_cents")
        year = int(str(row.period)[:4])
    except (ValueError, TypeError):
        return None
    if not isinstance(amount, int) or isinstance(amount, bool):
        return None  # simulation faite avant que Bank demande le chèque énergie
    inputs = result.get("inputs") if isinstance(result.get("inputs"), dict) else {}
    return dom.EnergyCheque(
        year,
        amount,
        row.created_at.date(),
        str(result.get("engine_version") or "?"),
        rfr_known=bool(inputs.get("used_declared_taxable_income")),
    )


def _cheques_seen(db: Session, today: date) -> list[date]:
    rows = db.execute(
        select(Transaction.booking_date, Transaction.raw_label).where(
            Transaction.booking_date >= date(today.year, 1, 1),
            Transaction.raw_label.ilike("%nergie%"),  # « ÉNERGIE » comme « ENERGIE » ; le motif tranche ensuite
        )
    )
    return [d for d, label in rows if dom.is_energy_cheque(label or "")]


# ------------------------------------------------------------------ ensemble
def findings(db: Session, today: date) -> list[Finding]:
    def revals() -> list[Finding]:
        known, appeal = revaluations(db)
        return dom.missing_revaluations(known, _aid_credits(db, today), today, appeal)

    out = guarded("revalorisations", revals)
    out += guarded("taux de prélèvement", lambda: withholding(db, today))
    out += guarded(
        "chèque énergie",
        lambda: dom.energy_cheque_findings(_energy_cheque(db), _cheques_seen(db, today), today),
    )
    return out
