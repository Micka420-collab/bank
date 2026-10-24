"""Retraite : ton relevé de carrière (trimestres, salaire moyen, points — saisis, chiffrés), ton salaire actuel repris
des bulletins du coffre ou des relevés, la génération d'après ton profil de foyer ; le domaine estime les départs
(âge légal, taux plein, 67 ans, un âge au choix) et l'effet d'un versement PER sur ton impôt (tranche de Bank).
Rien ne part vers un organisme ; le chiffre opposable est celui d'info-retraite.fr."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import retirement as dom
from app.domain.recurring import add_months
from app.models import RetirementProfile, VaultDocument
from app.security import audit
from app.services import advisor, analytics, decisions

MAX_CUSTOM_AGE_YEARS = 75
MIN_CUSTOM_AGE_YEARS = 55


class RetirementServiceError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def profile(db: Session) -> dict[str, Any]:
    row = db.get(RetirementProfile, 1)
    if row is None:
        return {}
    try:
        data = json.loads(row.data)
        return dict(data) if isinstance(data, dict) else {}
    except ValueError:
        return {}


def save_profile(
    db: Session,
    *,
    actor: str,
    quarters_acquired: int,
    as_of: date,
    best_years_average_cents: int | None,
    agirc_arrco_points: str | None,
    career_start_year: int | None,
    working: bool,
    birth_date: date | None,
) -> dict[str, Any]:
    points: str | None = None
    if agirc_arrco_points:
        try:
            value = Decimal(agirc_arrco_points.replace(",", ".").replace(" ", ""))
        except InvalidOperation as exc:
            raise RetirementServiceError("Points Agirc-Arrco : un nombre", 422) from exc
        if not 0 <= value <= 100_000:
            raise RetirementServiceError("Points Agirc-Arrco : entre 0 et 100 000", 422)
        points = str(value.quantize(Decimal("0.01")))
    if not 0 <= quarters_acquired <= 200:
        raise RetirementServiceError("Trimestres : entre 0 et 200", 422)
    if best_years_average_cents is not None and not 0 <= best_years_average_cents <= 100_000_000:
        raise RetirementServiceError("Salaire annuel moyen hors limites", 422)
    if career_start_year is not None and not 1950 <= career_start_year <= as_of.year:
        raise RetirementServiceError("Année de début de carrière invalide", 422)
    data = {
        "quarters_acquired": quarters_acquired,
        "as_of": as_of.isoformat(),
        "best_years_average_cents": best_years_average_cents,
        "agirc_arrco_points": points,
        "career_start_year": career_start_year,
        "working": working,
        "birth_date": birth_date.isoformat() if birth_date else None,
    }
    row = db.get(RetirementProfile, 1) or RetirementProfile(id=1, data="{}")
    row.data = json.dumps(data)
    row.updated_at = utcnow()
    db.add(row)
    db.flush()
    audit.record(db, actor, "retirement.profile", quarters=quarters_acquired)
    return data


def remove_profile(db: Session, *, actor: str) -> None:
    row = db.get(RetirementProfile, 1)
    if row is not None:
        db.delete(row)
        db.flush()
        audit.record(db, actor, "retirement.profile_removed")


# ------------------------------------------------------------------ ce que Bank sait déjà
def _birth(db: Session, saved: dict[str, Any]) -> date | None:
    if saved.get("birth_date"):
        return date.fromisoformat(str(saved["birth_date"]))
    household = analytics.load_profile(db) or {}
    raw = household.get("birth_date")
    return date.fromisoformat(str(raw)) if raw else None


def _payslip_gross_annual(db: Session) -> tuple[int | None, str]:
    """Le brut annuel depuis le dernier bulletin vérifié du coffre (brut mensuel × 12), sinon rien."""
    docs = list(
        db.scalars(
            select(VaultDocument)
            .where(VaultDocument.kind == "payslip", VaultDocument.status.in_(("verified", "to_review")))
            .order_by(VaultDocument.document_date.desc().nullslast(), VaultDocument.id.desc())
            .limit(3)
        )
    )
    for doc in docs:
        values = {f.key: json.loads(f.value) for f in doc.fields}
        brut = values.get("brut")
        if isinstance(brut, int) and brut > 0:
            return brut * 12, f"bulletin de paie du coffre ({doc.title})"
    return None, ""


def gross_annual(db: Session, today: date) -> tuple[int, str]:
    """Brut annuel actuel : bulletin du coffre d'abord, sinon le salaire net des relevés converti (× 1,28)."""
    from_payslip, source = _payslip_gross_annual(db)
    if from_payslip:
        return from_payslip, source
    facts = advisor.detected_facts(db, today)
    net = facts.salary_monthly_cents or 0
    if net > 0:
        return dom.gross_from_net_monthly(net), "salaire net des relevés, converti en brut (× 1,28)"
    return 0, ""


def career(db: Session, today: date) -> tuple[dom.Career | None, list[str], str]:
    saved = profile(db)
    birth = _birth(db, saved)
    notes: list[str] = []
    if birth is None:
        note = "Renseigne ta date de naissance (Ma situation) ou ici : la génération fixe l'âge légal et la durée."
        return None, [note], ""
    gross, source = gross_annual(db, today)
    if not gross:
        notes.append("Aucun salaire reconnu : la pension est estimée à zéro tant que Bank ne voit pas de paie.")
    if not saved:
        notes.append(
            "Sans ton relevé de carrière, Bank compte les trimestres depuis l'année de tes 20 ans, 4 par an : saisis "
            "le vrai chiffre (info-retraite.fr)."
        )
        start_year = birth.year + 20
        quarters = max(0, min(200, (today.year - start_year) * 4))
        as_of = today
        best = None
        points = None
        working = True
    else:
        quarters = int(saved.get("quarters_acquired") or 0)
        as_of = date.fromisoformat(str(saved.get("as_of") or today.isoformat()))
        best = saved.get("best_years_average_cents")
        points = Decimal(str(saved["agirc_arrco_points"])) if saved.get("agirc_arrco_points") else None
        working = bool(saved.get("working", True))
    try:
        c = dom.Career(
            birth=birth,
            quarters_acquired=quarters,
            as_of=as_of,
            gross_annual_cents=gross,
            best_years_average_cents=int(best) if best is not None else None,
            agirc_arrco_points=points,
            career_start_year=int(saved["career_start_year"]) if saved.get("career_start_year") else None,
            working=working,
        )
    except dom.RetirementError as exc:
        raise RetirementServiceError(str(exc), 422) from exc
    return c, notes, source


def _custom_date(c: dom.Career, years: int | None, months: int | None) -> date | None:
    if years is None:
        return None
    if not MIN_CUSTOM_AGE_YEARS <= years <= MAX_CUSTOM_AGE_YEARS or not 0 <= (months or 0) <= 11:
        raise RetirementServiceError("Âge de départ : entre 55 et 75 ans", 422)
    return add_months(c.birth.replace(year=c.birth.year + years), months or 0)


def view(
    db: Session,
    today: date,
    *,
    age_years: int | None = None,
    age_months: int | None = None,
    per_cents: int | None = None,
) -> dict[str, Any]:
    c, notes, salary_source = career(db, today)
    out: dict[str, Any] = {
        "profile": profile(db),
        "notes": notes,
        "rules": dom.RULES,
        "rules_checked": dom.RULES_CHECKED,
        "rules_recheck": (today - date.fromisoformat(dom.RULES_CHECKED)).days > dom.RECHECK_AFTER_DAYS,
        "disclaimer": (
            "Un ordre de grandeur à partir de règles publiques datées et de ce que Bank sait de ta carrière : le "
            "chiffre opposable est celui de ton relevé de carrière et du simulateur officiel (info-retraite.fr). "
            "Rien n'est envoyé à une caisse."
        ),
    }
    if c is None:
        out.update({"career": None, "departures": {}, "per": None})
        return out
    gen = dom.generation(c.birth)
    marks = dom.milestones(c)
    departures = {
        "legal": dom.estimate(c, marks["legal"]),
        "full_rate": dom.estimate(c, marks["full_rate"]),
        "age_67": dom.estimate(c, marks["age_67"]),
    }
    custom = _custom_date(c, age_years, age_months)
    if custom is not None:
        departures["custom"] = dom.estimate(c, custom)
    out["career"] = {
        "birth_date": c.birth.isoformat(),
        "age_years": dom.age_at(c.birth, today)[0],
        "quarters_acquired": c.quarters_acquired,
        "as_of": c.as_of.isoformat(),
        "quarters_required": gen.quarters_required,
        "legal_age_years": gen.legal_age[0],
        "legal_age_months": gen.legal_age[1],
        "gross_annual_cents": c.gross_annual_cents,
        "salary_source": salary_source,
        "best_years_average_cents": c.best_years_average_cents,
        "agirc_arrco_points": str(c.agirc_arrco_points) if c.agirc_arrco_points is not None else None,
        "points_per_year": str(dom.points_per_year(c.gross_annual_cents)),
        "working": c.working,
        "quarter_salary_cents": dom.QUARTER_SALARY_2026_CENTS,
    }
    out["departures"] = {key: dom.departure_to_dict(d) for key, d in departures.items()}
    # PER : ce qu'un versement change à l'impôt de l'année, avec la tranche que Bank connaît.
    tax = decisions.tax_estimate(db, today)
    payment = per_cents if per_cents is not None else min(300_000, max(50_000, c.gross_annual_cents // 20 or 50_000))
    effect = dom.per_effect(
        payment, professional_income_cents=c.gross_annual_cents, marginal_rate_percent=tax.marginal_rate_percent
    )
    out["per"] = {
        **asdict(effect),
        "notes": list(effect.notes),
        "tax_source": tax.source,
        "tax_detail": tax.detail,
    }
    return out


def summary_for_chat(db: Session, today: date, *, age_years: int | None = None) -> dict[str, Any]:
    """Pour l'assistant : la génération, les trimestres connus, les départs en fourchette et l'effet PER, court."""
    try:
        data = view(db, today, age_years=age_years)
    except RetirementServiceError as exc:
        return {"error": str(exc)}
    career_data = data.get("career")
    if not career_data:
        return {"career": None, "notes": data["notes"], "simulator": dom.RULES["simulator"]["url"]}
    keep = (
        "at", "age_years", "age_months", "quarters_total", "quarters_required", "missing_quarters",
        "extra_quarters", "rate_percent", "status", "total_low_cents", "total_high_cents", "replacement_percent",
    )  # fmt: skip
    career_keys = (
        "age_years", "quarters_acquired", "as_of", "quarters_required", "legal_age_years", "legal_age_months",
        "gross_annual_cents", "salary_source", "working",
    )  # fmt: skip
    per_keys = (
        "payment_cents", "ceiling_cents", "deductible_cents", "marginal_rate_percent", "tax_saved_cents",
        "effort_cents",
    )  # fmt: skip
    per = data["per"] or {}
    return {
        "career": {k: career_data[k] for k in career_keys},
        "departures": {key: {k: d[k] for k in keep} for key, d in data["departures"].items()},
        "per": {k: per.get(k) for k in per_keys},
        "notes": data["notes"] + [n for d in data["departures"].values() for n in d["notes"]][:4],
        "rules_checked": data["rules_checked"],
        "simulator": dom.RULES["simulator"]["url"],
    }


def agent_view(db: Session, today: date, privacy: str) -> dict[str, Any]:
    data = view(db, today)
    if privacy == "aggregates":
        data.pop("profile", None)
        if data.get("career"):
            for key in ("gross_annual_cents", "best_years_average_cents", "agirc_arrco_points"):
                data["career"].pop(key, None)
    data["rule"] = (
        "Un ordre de grandeur calculé par Bank avec des règles datées : explique l'âge légal, la durée requise, ce "
        "qu'un départ plus tôt ou plus tard change, et renvoie au simulateur officiel pour le chiffre opposable. "
        "Ne recalcule rien, n'invente aucun droit."
    )
    return data
