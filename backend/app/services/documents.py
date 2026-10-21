"""Documents officiels : aperçu, validation par toi, puis faits conservés (chiffrés) et profil mis à jour."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain.documents import ALLOWANCES, CAF_LABELS, DocumentError, ParsedDocument, parse_document
from app.domain.invest import load_references, marginal_rate
from app.models import HouseholdProfile, OfficialFact
from app.security import audit
from app.services import advisor

MAX_PDF_BYTES = 5_000_000  # même borne que les relevés (nginx 6 Mo, middleware 5 Mo + marge)

# Champs acceptés à la validation, par type : tout le reste est ignoré (jamais d'identifiant).
ALLOWED_FIELDS: dict[str, tuple[str, ...]] = {
    "avis_impot": (
        "incomes_year",
        "rfr_cents",
        "taxable_income_cents",
        "parts",
        "income_tax_cents",
        "withholding_rate_percent",
        "declarants",
    ),
    "caf": ("prestations", "month", "quotient_familial_cents", "children"),
    "france_travail": ("daily_allowance_cents", "monthly_estimate_cents", "end_of_rights", "days_left", "allowance"),
}


def preview(pdf_bytes: bytes) -> ParsedDocument:
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise DocumentError("Fichier trop volumineux (5 Mo au plus)")
    if not pdf_bytes.startswith(b"%PDF"):
        raise DocumentError("Ce n'est pas un PDF")
    return parse_document(pdf_bytes)


def _clean(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = ALLOWED_FIELDS.get(kind)
    if allowed is None:
        raise DocumentError("Type de document non pris en charge")
    out: dict[str, Any] = {}
    for key in allowed:
        value = fields.get(key)
        if value is None or value == "":  # champ effacé dans l'aperçu : « non renseigné »
            continue
        if key.endswith("_cents") or key in _INT_MAX:
            top = _INT_MAX.get(key, 10_000_000_000)  # 100 M€ pour les montants
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                raise DocumentError(f"Valeur invalide pour {key}")
        elif key in ("parts", "withholding_rate_percent"):
            value = _bounded_decimal(key, value, low=Decimal("0.5") if key == "parts" else Decimal(0), high=_HIGH[key])
        elif key == "prestations":
            value = _clean_prestations(value)
        elif key in ("end_of_rights", "month"):
            value = str(value)[:10]
            try:
                date.fromisoformat(value if key == "end_of_rights" else value + "-01")
            except ValueError as exc:
                raise DocumentError(f"Date invalide pour {key}") from exc
        elif key == "allowance":
            if value not in ALLOWANCES:
                raise DocumentError("Allocation inconnue (ARE ou ASS)")
        else:  # pragma: no cover - tous les champs admis sont traités ci-dessus
            raise DocumentError(f"Champ inattendu : {key}")
        out[key] = value
    return out


_HIGH = {"parts": Decimal(20), "withholding_rate_percent": Decimal(100)}
_INT_MAX = {"incomes_year": 2100, "declarants": 2, "days_left": 5_000, "children": 20}


def _bounded_decimal(key: str, value: Any, *, low: Decimal, high: Decimal) -> str:
    """Décimal fini dans [low, high] (« NaN », « Infinity », 0 part : refusés avant de diviser quoi que ce soit)."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise DocumentError(f"Valeur invalide pour {key}")
    try:
        d = Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation as exc:
        raise DocumentError(f"Valeur invalide pour {key}") from exc
    if not d.is_finite() or not low <= d <= high:
        raise DocumentError(f"Valeur invalide pour {key}")
    return str(d)


def _clean_prestations(value: Any) -> list[dict[str, Any]]:
    """Code dans la table connue, libellé dérivé du code (jamais du texte libre), montant borné."""
    if not isinstance(value, list) or len(value) > 20:
        raise DocumentError("Prestations invalides")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in value:
        if not isinstance(p, dict):
            raise DocumentError("Prestations invalides")
        code, cents = p.get("code"), p.get("monthly_cents")
        if code not in CAF_LABELS:
            raise DocumentError("Prestation inconnue")
        if isinstance(cents, bool) or not isinstance(cents, int) or not 0 <= cents <= 500_000:
            raise DocumentError(f"Montant invalide pour {CAF_LABELS[code]}")
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "label": CAF_LABELS[code], "monthly_cents": cents})
    return out


def _period(kind: str, fields: dict[str, Any], today: date) -> str:
    if kind == "avis_impot":
        year = fields.get("incomes_year")
        if not isinstance(year, int) or not 2000 <= year <= today.year:
            raise DocumentError("Année des revenus manquante ou invalide")
        return str(year)
    if kind == "caf":
        return str(fields.get("month") or f"{today.year:04d}-{today.month:02d}")
    return today.isoformat()[:7]


def apply(db: Session, *, kind: str, fields: dict[str, Any], actor: str, today: date) -> OfficialFact:
    """Enregistre les faits validés et met à jour ce qui en découle (tranche d'impôt, revenu N-2 du foyer)."""
    clean = _clean(kind, fields)
    period = _period(kind, clean, today)
    title = {
        "avis_impot": f"Avis d'impôt sur les revenus {period}",
        "caf": f"Attestation CAF ({period})",
        "france_travail": f"Attestation France Travail ({period})",
    }[kind]
    row = db.scalar(select(OfficialFact).where(OfficialFact.kind == kind, OfficialFact.period == period))
    payload = json.dumps(clean, ensure_ascii=False)
    if row is None:
        row = OfficialFact(kind=kind, period=period, title=title, data=payload)
        db.add(row)
    else:
        row.title, row.data, row.imported_at = title, payload, utcnow()
    db.flush()
    if kind == "avis_impot":
        _apply_tax(db, clean, actor, today)
    audit.record(db, actor, "document.apply", kind=kind, period=period)
    return row


def _apply_tax(db: Session, f: dict[str, Any], actor: str, today: date) -> None:
    taxable = f.get("taxable_income_cents") or f.get("rfr_cents")
    parts = Decimal(str(f.get("parts") or "1"))
    if taxable:
        rate = marginal_rate(int(taxable), parts, load_references())
        advisor.set_answer(db, "tax_bracket", str(rate), source=f"{actor} (avis d'impôt {f.get('incomes_year')})")
    # Revenu imposable N-2 pour la simulation des aides : seulement si le foyer ne l'a pas déjà renseigné.
    if f.get("incomes_year") == today.year - 2 and f.get("rfr_cents"):
        row = db.get(HouseholdProfile, 1)
        if row is not None:
            data = json.loads(row.data)
            if not data.get("annual_taxable_income_n2_cents"):
                data["annual_taxable_income_n2_cents"] = int(f["rfr_cents"])
                row.data, row.updated_at = json.dumps(data), utcnow()


def facts(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "period": r.period,
            "title": r.title,
            "fields": json.loads(r.data),
            "imported_at": r.imported_at.isoformat(),
        }
        for r in db.scalars(select(OfficialFact).order_by(OfficialFact.kind, OfficialFact.period.desc()))
    ]


def latest(db: Session, kind: str) -> dict[str, Any] | None:
    r = db.scalar(select(OfficialFact).where(OfficialFact.kind == kind).order_by(OfficialFact.period.desc()).limit(1))
    return {**json.loads(r.data), "period": r.period} if r else None


def remove(db: Session, fact_id: int, actor: str) -> bool:
    r = db.get(OfficialFact, fact_id)
    if r is None:
        return False
    db.delete(r)
    audit.record(db, actor, "document.delete", kind=r.kind, period=r.period)
    return True


def taxable_income_for(db: Session, year: int) -> int | None:
    r = db.scalar(select(OfficialFact).where(OfficialFact.kind == "avis_impot", OfficialFact.period == str(year)))
    if r is None:
        return None
    f = json.loads(r.data)
    value = f.get("rfr_cents") or f.get("taxable_income_cents")
    return int(value) if value else None


def with_taxable_income(db: Session, profile: Any, today: date) -> Any:
    """Complète le revenu imposable N-2 du foyer depuis l'avis d'impôt importé, si le profil ne l'a pas."""
    if getattr(profile, "annual_taxable_income_n2_cents", None):
        return profile
    value = taxable_income_for(db, today.year - 2)
    return profile.model_copy(update={"annual_taxable_income_n2_cents": value}) if value else profile
