"""Import des exports de plateformes crypto : un aperçu d'abord, puis les opérations du 2086, sans doublon.

La valeur globale du portefeuille de chaque cession vient de toi, ou à défaut du suivi de Bank (le relevé de la
veille, sinon celui du jour, pris après la vente : une proposition à vérifier) ; sans l'une ni l'autre, la cession
attend que tu la complètes plutôt qu'une valeur inventée.

Une opération déjà notée à la main (même jour, même type, même montant) ou supprimée après un import précédent
n'est pas recréée : elle n'entre que si tu la coches dans l'aperçu (« include »)."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import crypto as dc
from app.domain.money import format_eur
from app.importers import crypto_csv as cc
from app.models import CryptoImportExcluded, CryptoSnapshot, CryptoTaxEvent
from app.security import audit
from app.services.crypto import tax_events

MAX_SHOWN = 500
MAX_VALUE_CENTS = 1_000_000_000_000
WAITING = ("to_complete", "manual_match", "excluded")


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


def _keys(raw: Any) -> set[str]:
    """Les opérations que tu as cochées pour les importer quand même (déjà notées à la main, ou supprimées)."""
    if raw is None:
        return set()
    if not isinstance(raw, list) or len(raw) > cc.MAX_ROWS:
        raise dc.CryptoError("Choix des opérations illisible")
    if not all(isinstance(k, str) and len(k) == 64 for k in raw):
        raise dc.CryptoError("Choix des opérations illisible")
    return set(raw)


def _known(db: Session, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i : i + 500]
        found.update(
            k for k in db.scalars(select(CryptoTaxEvent.import_key).where(CryptoTaxEvent.import_key.in_(chunk))) if k
        )
    return found


def _excluded(db: Session, keys: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(keys), 500):
        chunk = keys[i : i + 500]
        found.update(
            db.scalars(select(CryptoImportExcluded.import_key).where(CryptoImportExcluded.import_key.in_(chunk)))
        )
    return found


def _manual(db: Session, days: set[date]) -> Counter[tuple[date, str, int]]:
    """Les opérations notées à la main (sans empreinte d'import), par jour, type et montant."""
    rows = db.execute(
        select(CryptoTaxEvent.day, CryptoTaxEvent.kind, CryptoTaxEvent.amount_cents).where(
            CryptoTaxEvent.import_key.is_(None), CryptoTaxEvent.day.in_(days)
        )
    )
    return Counter((d, k, a) for d, k, a in rows)


def plan(db: Session, data: bytes, *, today: date, values: Any = None, include: Any = None) -> dict[str, Any]:
    """Ce que l'import créerait : rien n'est écrit, rien ne part sur le réseau."""
    parsed = cc.parse(data)
    given, chosen = _values(values), _keys(include)
    ignored = list(parsed.ignored)
    movements: list[cc.Movement] = []
    for m in parsed.movements:
        if m.day > today or m.day.year < 2009:
            ignored.append(cc.Ignored(m.line, "Date hors de 2009 à aujourd'hui"))
        else:
            movements.append(m)
    known = _known(db, [m.key for m in movements])
    excluded = _excluded(db, [m.key for m in movements])
    manual = _manual(db, {m.day for m in movements})
    days = {m.day for m in movements if m.kind == "cession"}
    days |= {d - timedelta(days=1) for d in days}
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
            "value_day": None,
            "status": "new",
            "issue": None,
        }
        twin = (m.day, m.kind, m.amount_cents)
        if m.key in known or m.key in seen:
            row["status"] = "duplicate"
        elif m.key in excluded and m.key not in chosen:
            row["status"] = "excluded"
            row["issue"] = "Supprimée après un import précédent : coche-la pour la reprendre"
        elif manual[twin] and m.key not in chosen:
            # Une saisie à la main du même jour et du même montant : la même opération, sauf si tu dis le contraire.
            manual[twin] -= 1
            row["status"] = "manual_match"
            row["issue"] = "Déjà notée à la main (même jour, même montant) : coche-la seulement si c'en est une autre"
        if m.kind == "cession" and row["status"] != "duplicate":
            # La veille d'abord : le relevé du jour est le dernier de la journée, pris après la vente.
            mine = given.get(m.key)
            veille = m.day - timedelta(days=1)
            on = veille if veille in tracked else m.day
            suivi = tracked.get(on)
            if mine is not None and mine >= m.amount_cents:
                row["portfolio_value_cents"], row["value_source"] = mine, "saisie"
            elif mine is None and suivi and suivi >= m.amount_cents:
                row["portfolio_value_cents"], row["value_source"], row["value_day"] = suivi, "suivi", on.isoformat()
            elif row["status"] == "new":
                row["status"] = "to_complete"
                row["issue"] = (
                    f"Valeur saisie inférieure au prix de vente : au moins {format_eur(m.amount_cents)}"
                    if mine is not None
                    else f"Le suivi relevait {format_eur(suivi)} le {on:%d/%m/%Y}, moins que le prix de vente : "
                    "indique la valeur juste avant"
                    if suivi
                    else "Pas de relevé du portefeuille ce jour-là : indique sa valeur globale juste avant la vente"
                )
        seen.add(m.key)
        rows.append(row)

    status = Counter(r["status"] for r in rows)
    new = [r for r in rows if r["status"] == "new"]
    # D'abord ce qui attend ton avis (valeur à compléter ou à vérifier, doublon possible) : jamais coupé.
    asks = [r["status"] in WAITING or (r["kind"] == "cession" and r["status"] == "new") for r in rows]
    shown = [r for r, a in zip(rows, asks, strict=True) if a]
    shown += [r for r, a in zip(rows, asks, strict=True) if not a][: max(0, MAX_SHOWN - len(shown))]
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
            "manual_matches": status["manual_match"],
            "excluded": status["excluded"],
            "ignored": len(ignored),
        },
        "events": shown,
        "truncated": len(shown) < len(rows),
        "reasons": [{"reason": r, "count": n} for r, n in Counter(i.reason for i in ignored).most_common()],
        "ignored": [{"line": i.line, "reason": i.reason} for i in ignored[:100]],
        "years": sorted({int(r["day"][:4]) for r in new}),
        "_create": new,
    }


def commit(
    db: Session, data: bytes, *, actor: str, today: date, values: Any = None, include: Any = None
) -> dict[str, Any]:
    """Crée les acquisitions et les cessions complètes ; les doublons, les opérations déjà notées à la main ou
    supprimées, et les cessions sans valeur globale restent de côté (un nouvel import les reprendra)."""
    write_lock(db)  # lire ce qui existe puis écrire : une seule requête à la fois
    out = plan(db, data, today=today, values=values, include=include)
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
    keys = [r["key"] for r in create]
    for i in range(0, len(keys), 500):  # reprise exprès d'une opération supprimée : elle n'est plus écartée
        db.execute(delete(CryptoImportExcluded).where(CryptoImportExcluded.import_key.in_(keys[i : i + 500])))
    try:
        db.flush()
    except IntegrityError:  # le même export importé au même instant (deux onglets, une requête rejouée)
        db.rollback()
        raise dc.CryptoError("Cet export vient d'être importé : relance l'aperçu") from None
    out["created"] = len(create)
    audit.record(
        db,
        actor,
        "crypto.tax.import",
        platform=out["platform"],
        created=len(create),
        duplicates=out["counts"]["duplicates"],
        to_complete=out["counts"]["to_complete"],
        manual_matches=out["counts"]["manual_matches"],
        excluded=out["counts"]["excluded"],
        ignored=out["counts"]["ignored"],
    )
    return out


def preview(db: Session, data: bytes, *, today: date, values: Any = None, include: Any = None) -> dict[str, Any]:
    out = plan(db, data, today=today, values=values, include=include)
    out.pop("_create")
    return out
