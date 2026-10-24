"""Import des exports de plateformes crypto : un aperçu d'abord, puis les opérations du 2086, sans doublon.

La valeur globale du portefeuille de chaque cession vient du suivi de Bank (le point de la courbe ce jour-là) ou
de toi ; sans l'une ni l'autre, la cession attend que tu la complètes plutôt qu'une valeur inventée."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import crypto as dc
from app.domain.money import format_eur
from app.importers import crypto_csv as cc
from app.models import CryptoSnapshot, CryptoTaxEvent
from app.security import audit
from app.services.crypto import tax_events

MAX_SHOWN = 500
MAX_VALUE_CENTS = 1_000_000_000_000


def _values(raw: Any) -> dict[str, int]:
    """Les valeurs globales que tu as saisies dans l'aperçu, par empreinte de cession."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > cc.MAX_ROWS:
        raise dc.CryptoError("Valeurs du portefeuille illisibles")
    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) != 64 or not isinstance(value, int) or isinstance(value, bool):
            raise dc.CryptoError("Valeurs du portefeuille illisibles")
        if not 0 < value <= MAX_VALUE_CENTS:
            raise dc.CryptoError("Valeur du portefeuille : un montant positif")
        out[key] = value
    return out


def _known(db: Session, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i : i + 500]
        found.update(
            k for k in db.scalars(select(CryptoTaxEvent.import_key).where(CryptoTaxEvent.import_key.in_(chunk))) if k
        )
    return found


def plan(db: Session, data: bytes, *, today: date, values: Any = None) -> dict[str, Any]:
    """Ce que l'import créerait : rien n'est écrit, rien ne part sur le réseau."""
    parsed = cc.parse(data)
    given = _values(values)
    ignored = list(parsed.ignored)
    movements: list[cc.Movement] = []
    for m in parsed.movements:
        if m.day > today or m.day.year < 2009:
            ignored.append(cc.Ignored(m.line, "Date hors de 2009 à aujourd'hui"))
        else:
            movements.append(m)
    known = _known(db, [m.key for m in movements])
    days = {m.day for m in movements if m.kind == "cession"}
    tracked = {s.day: s.value_cents for s in db.scalars(select(CryptoSnapshot).where(CryptoSnapshot.day.in_(days)))}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for m in sorted(movements, key=lambda x: (x.day, x.kind != "acquisition", x.line)):
        row: dict[str, Any] = {
            "key": m.key,
            "line": m.line,
            "day": m.day.isoformat(),
            "kind": m.kind,
            "asset": m.asset,
            "quantity": dc.qty_text(m.quantity) if m.quantity is not None else None,
            "amount_cents": m.amount_cents,
            "fees_cents": m.fees_cents,
            "portfolio_value_cents": None,
            "value_source": None,
            "status": "new",
            "issue": None,
        }
        if m.key in known or m.key in seen:
            row["status"] = "duplicate"
        elif m.kind == "cession":
            mine, suivi = given.get(m.key), tracked.get(m.day)
            if mine is not None and mine >= m.amount_cents:
                row["portfolio_value_cents"], row["value_source"] = mine, "saisie"
            elif mine is None and suivi and suivi >= m.amount_cents:
                row["portfolio_value_cents"], row["value_source"] = suivi, "suivi"
            else:
                row["status"] = "to_complete"
                row["issue"] = (
                    "Valeur saisie inférieure au prix de vente"
                    if mine is not None
                    else f"Le suivi relevait {format_eur(suivi)} ce jour-là, moins que le prix de vente : indique la "
                    "valeur juste avant"
                    if suivi
                    else "Pas de relevé du portefeuille ce jour-là : indique sa valeur globale juste avant la vente"
                )
        seen.add(m.key)
        rows.append(row)

    status = Counter(r["status"] for r in rows)
    new = [r for r in rows if r["status"] == "new"]
    shown = [r for r in rows if r["status"] == "to_complete"]
    shown += [r for r in rows if r["status"] != "to_complete"][: max(0, MAX_SHOWN - len(shown))]
    shown.sort(key=lambda r: (r["day"], r["kind"] != "acquisition", r["line"]))
    ignored.sort(key=lambda x: x.line)
    return {
        "platform": parsed.platform,
        "platform_label": cc.PLATFORMS[parsed.platform],
        "format": parsed.format,
        "rows": parsed.rows,
        "counts": {
            "acquisitions": sum(r["kind"] == "acquisition" for r in new),
            "cessions": sum(r["kind"] == "cession" for r in new),
            "to_complete": status["to_complete"],
            "duplicates": status["duplicate"],
            "ignored": len(ignored),
        },
        "events": shown,
        "truncated": len(shown) < len(rows),
        "reasons": [{"reason": r, "count": n} for r, n in Counter(i.reason for i in ignored).most_common()],
        "ignored": [{"line": i.line, "reason": i.reason} for i in ignored[:100]],
        "years": sorted({int(r["day"][:4]) for r in new}),
        "_create": new,
    }


def commit(db: Session, data: bytes, *, actor: str, today: date, values: Any = None) -> dict[str, Any]:
    """Crée les acquisitions et les cessions complètes ; les doublons et les cessions sans valeur globale restent
    de côté (un nouvel import les reprendra)."""
    out = plan(db, data, today=today, values=values)
    create: list[dict[str, Any]] = out.pop("_create")
    label = out["platform_label"]
    # Vérifier le calcul du 2086 avec ces opérations avant d'écrire quoi que ce soit.
    pending = [
        dc.TaxEvent(-n - 1, date.fromisoformat(r["day"]), r["kind"], r["amount_cents"], r["fees_cents"],
                    r["portfolio_value_cents"])
        for n, r in enumerate(create)
    ]  # fmt: skip
    dc.tax_years([*tax_events(db), *pending])
    for r in create:
        what = f" : {r['quantity']} {r['asset']}" if r["quantity"] and r["asset"] else ""
        db.add(
            CryptoTaxEvent(
                day=date.fromisoformat(r["day"]),
                kind=r["kind"],
                amount_cents=r["amount_cents"],
                fees_cents=r["fees_cents"],
                portfolio_value_cents=r["portfolio_value_cents"],
                asset=r["asset"],
                note=f"Import {label}{what}"[:200],
                import_key=r["key"],
            )
        )
    db.flush()
    out["created"] = len(create)
    audit.record(
        db,
        actor,
        "crypto.tax.import",
        platform=out["platform"],
        created=len(create),
        duplicates=out["counts"]["duplicates"],
        to_complete=out["counts"]["to_complete"],
        ignored=out["counts"]["ignored"],
    )
    return out


def preview(db: Session, data: bytes, *, today: date, values: Any = None) -> dict[str, Any]:
    out = plan(db, data, today=today, values=values)
    out.pop("_create")
    return out
