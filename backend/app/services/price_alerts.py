"""Alertes de cours : un seuil haut, un seuil bas ou une variation sur 24 h, sur un actif dont Bank suit déjà le
cours. Elles se lisent quand les cours sont relevés (aucune autre source, aucun appel en plus) et partent dans tes
messageries et sur ton téléphone par le circuit commun des alertes (heures calmes, une fois par compte relié).

Une alerte est un constat, jamais un conseil : le texte dit ce qui s'est passé et rappelle que le seuil est le
tien — pas de « c'est le moment », pas d'invitation à acheter ou à vendre (un test relit chaque modèle).

Une fois par franchissement : armée, elle passe « déclenchée » par une mise à jour conditionnelle (deux relèves en
même temps ne la déclenchent qu'une fois) ; elle se réarme seule quand le cours revient de l'autre côté avec une
marge (1 % du seuil, ou 1 point de variation), ou quand tu la réarmes. L'état est en base : un redémarrage ne
renvoie rien. Seule la personne crée ses alertes (aucun outil d'agent n'en écrit)."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain import crypto as dc
from app.models import CryptoPrice, PriceAlert
from app.security import audit

KINDS = {"above": "Seuil haut", "below": "Seuil bas", "change": "Variation sur 24 h"}
MAX_ALERTS = 20
MAX_LABEL = 60
MAX_PRICE = Decimal(1_000_000_000)  # comme les cours eux-mêmes : au-delà, c'est une erreur de saisie
MIN_CHANGE, MAX_CHANGE = Decimal(2), Decimal(90)  # points de % sur 24 h
PRICE_MARGIN = Decimal("0.01")  # réarmement : 1 % de l'autre côté du seuil
CHANGE_MARGIN = Decimal(1)  # réarmement : 1 point sous la variation fixée
FRESH = timedelta(hours=2)  # un cours plus ancien ne déclenche rien
WINDOW = timedelta(hours=48)  # un déclenchement s'annonce pendant 48 h au plus (messagerie hors ligne, heures calmes)

# Chaque phrase qu'une alerte peut envoyer : factuelle, sans verbe d'action (tests/test_price_alerts.py les relit).
TEMPLATES = {
    "above": "📈 {asset} est passé au-dessus de {threshold} ({price} à {time}) — seuil que tu as fixé.",
    "below": "📉 {asset} est passé sous {threshold} ({price} à {time}) — seuil que tu as fixé.",
    "change": "↕️ {asset} a varié de {change} sur 24 h ({price} à {time}) — variation de {threshold} que tu as fixée.",
    "label": " Ton repère : « {label} ».",
    "footer": " Une information, pas un conseil. {url}",
    "aggregates": "🔔 Une alerte de cours que tu as fixée s'est déclenchée. Une information, pas un conseil. {url}",
}


class PriceAlertError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ écriture française
def _group(digits: str) -> str:
    return f"{int(digits):,}".replace(",", " ")


def eur(value: Decimal) -> str:
    """« 49 812 € » au-delà de 1 000 €, « 3,42 € » au-dessus de 1 €, « 0,000123 € » en dessous (sans arrondi
    trompeur : six chiffres significatifs au plus)."""
    if value >= 1000:
        return f"{_group(str(int(value.quantize(Decimal(1), rounding=ROUND_DOWN))))} €"
    if value >= 1:
        whole, cents = f"{value.quantize(Decimal('0.01'), rounding=ROUND_DOWN):f}".split(".")
        return f"{_group(whole)},{cents} €"
    text = f"{value.normalize():f}" if value else "0"
    if "." in text:
        head, tail = text.split(".")
        significant = len(tail) - len(tail.lstrip("0"))
        text = f"{head},{tail[: significant + 6].rstrip('0') or '0'}"
    return f"{text} €"


def percent(value: Decimal, *, signed: bool = False) -> str:
    text = f"{value.quantize(Decimal('0.01')).normalize():f}".replace(".", ",").replace("-", "−")
    return f"{'+' if signed and value > 0 else ''}{text} %"


def threshold_text(alert: PriceAlert) -> str:
    value = Decimal(alert.threshold)
    return percent(value) if alert.kind == "change" else eur(value)


# ------------------------------------------------------------------ règles
def _parse(raw: Any, kind: str) -> Decimal:
    try:
        value = Decimal(str(raw).strip().replace(",", ".").replace(" ", "").replace(" ", ""))
    except (InvalidOperation, ValueError):
        raise PriceAlertError("Valeur illisible : un nombre est attendu") from None
    if not value.is_finite() or value <= 0:
        raise PriceAlertError("Valeur : un nombre positif")
    if kind == "change":
        if not MIN_CHANGE <= value <= MAX_CHANGE or value != value.quantize(Decimal("0.1")):
            raise PriceAlertError(f"Variation : de {MIN_CHANGE} à {MAX_CHANGE} %, au dixième près")
    elif value > MAX_PRICE or value != value.quantize(Decimal("1e-8")):
        raise PriceAlertError("Seuil : jusqu'à un milliard d'euros, huit décimales au plus")
    return value


def _price(row: CryptoPrice | None) -> tuple[Decimal | None, Decimal | None]:
    if row is None:
        return None, None
    try:
        eur_value = Decimal(row.eur)
        change = Decimal(row.change_24h) if row.change_24h is not None else None
    except InvalidOperation:
        return None, None
    if not eur_value.is_finite() or eur_value <= 0:
        return None, None
    return eur_value, change if change is not None and change.is_finite() else None


def crossed(kind: str, threshold: Decimal, price: Decimal | None, change: Decimal | None) -> bool:
    if kind == "above":
        return price is not None and price >= threshold
    if kind == "below":
        return price is not None and price <= threshold
    return change is not None and abs(change) >= threshold


def back(kind: str, threshold: Decimal, price: Decimal | None, change: Decimal | None) -> bool:
    """Revenu de l'autre côté, avec la marge : l'alerte peut se réarmer sans clignoter autour du seuil."""
    if kind == "above":
        return price is not None and price <= threshold * (1 - PRICE_MARGIN)
    if kind == "below":
        return price is not None and price >= threshold * (1 + PRICE_MARGIN)
    return change is not None and abs(change) <= threshold - CHANGE_MARGIN


def _clean_label(raw: str | None) -> str | None:
    label = " ".join((raw or "").split())
    if dc.secret_kind(label):
        raise PriceAlertError("Ce repère contient un secret : ne colle jamais ta phrase de récupération ni ta clé")
    if len(label) > MAX_LABEL:
        raise PriceAlertError(f"Repère : {MAX_LABEL} caractères au plus")
    return label or None


# ------------------------------------------------------------------ côté personne
def create(
    db: Session, *, asset: str, kind: str, value: Any, label: str | None, actor: str, now: datetime
) -> PriceAlert:
    if kind not in KINDS:
        raise PriceAlertError("Type : seuil haut, seuil bas ou variation sur 24 h")
    symbol = (asset or "").strip().upper()
    row = db.get(CryptoPrice, symbol) if symbol else None
    if row is None:
        raise PriceAlertError("Actif sans cours suivi : ajoute-le à tes avoirs, Bank relèvera son cours")
    if (db.scalar(select(func.count()).select_from(PriceAlert)) or 0) >= MAX_ALERTS:
        raise PriceAlertError(f"{MAX_ALERTS} alertes au plus : supprime celles qui ne servent plus")
    threshold = _parse(value, kind)
    clean = _clean_label(label)
    price, _ = _price(row)
    if kind != "change" and crossed(kind, threshold, price, None):
        assert price is not None  # garanti par crossed  # noqa: S101  # nosec B101
        side = "au-dessus de" if kind == "above" else "sous"
        raise PriceAlertError(f"{symbol} est déjà {side} {eur(threshold)} ({eur(price)}) : choisis un autre seuil")
    stored = format(threshold.normalize(), "f")
    alert = PriceAlert(asset=symbol, kind=kind, threshold=stored, label=clean, state="armed", created_at=now)
    db.add(alert)
    db.flush()
    audit.record(db, actor, "price_alert.created", alert=alert.id, asset=symbol, kind=kind)
    return alert


def delete(db: Session, alert_id: int, *, actor: str) -> None:
    alert = db.get(PriceAlert, alert_id)
    if alert is None:
        raise PriceAlertError("Alerte introuvable", 404)
    db.delete(alert)
    db.flush()
    audit.record(db, actor, "price_alert.deleted", alert=alert_id)


def rearm(db: Session, alert_id: int, *, actor: str, now: datetime) -> PriceAlert:
    alert = db.get(PriceAlert, alert_id)
    if alert is None:
        raise PriceAlertError("Alerte introuvable", 404)
    if alert.state != "armed":
        price, change = _price(db.get(CryptoPrice, alert.asset))
        threshold = Decimal(alert.threshold)
        if alert.kind != "change" and crossed(alert.kind, threshold, price, change):
            assert price is not None  # garanti par crossed  # noqa: S101  # nosec B101
            raise PriceAlertError(
                f"{alert.asset} est toujours {'au-dessus de' if alert.kind == 'above' else 'sous'} "
                f"{eur(threshold)} ({eur(price)}) : elle se réarmera seule quand le cours repassera de l'autre côté"
            )
        alert.state, alert.rearmed_at = "armed", now
        db.flush()
        audit.record(db, actor, "price_alert.rearmed", alert=alert_id)
    return alert


def view(db: Session) -> dict[str, Any]:
    prices = {p.asset: p for p in db.scalars(select(CryptoPrice))}
    rows = db.scalars(select(PriceAlert).order_by(PriceAlert.asset, PriceAlert.id))
    return {
        "alerts": [
            {
                "id": a.id,
                "asset": a.asset,
                "kind": a.kind,
                "threshold": a.threshold,
                "threshold_text": threshold_text(a),
                "label": a.label,
                "state": a.state,
                "fires": a.fires,
                "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
                "triggered_price": a.triggered_price,
                "price_eur": prices[a.asset].eur if a.asset in prices else None,
                "change_24h": prices[a.asset].change_24h if a.asset in prices else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in rows
        ],
        "assets": sorted(prices),
        "kinds": KINDS,
        "max": MAX_ALERTS,
        "bounds": {"change_min": str(MIN_CHANGE), "change_max": str(MAX_CHANGE), "margin_percent": "1"},
    }


# ------------------------------------------------------------------ à chaque relève des cours
def fire(db: Session, alert: PriceAlert, *, seen: int, price: Decimal, change: Decimal | None, now: datetime) -> bool:
    """Armée → déclenchée, seulement si personne ne l'a fait entre-temps (même état, même compteur)."""
    done = db.execute(
        update(PriceAlert)
        .where(PriceAlert.id == alert.id, PriceAlert.state == "armed", PriceAlert.fires == seen)
        .values(
            state="triggered",
            fires=seen + 1,
            triggered_at=now,
            triggered_price=format(price, "f"),
            triggered_change=format(change, "f") if change is not None else None,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(done.rowcount)  # type: ignore[attr-defined]


def _rearm_auto(db: Session, alert: PriceAlert, *, seen: int, now: datetime) -> bool:
    done = db.execute(
        update(PriceAlert)
        .where(PriceAlert.id == alert.id, PriceAlert.state == "triggered", PriceAlert.fires == seen)
        .values(state="armed", rearmed_at=now)
        .execution_options(synchronize_session=False)
    )
    return bool(done.rowcount)  # type: ignore[attr-defined]


def evaluate(db: Session, *, now: datetime) -> int:
    """Compare chaque alerte au dernier cours (s'il est frais). Renvoie le nombre d'alertes déclenchées."""
    db.flush()  # ce que la relève vient d'écrire part avant les mises à jour conditionnelles
    fired = 0
    prices = {p.asset: p for p in db.scalars(select(CryptoPrice))}
    for alert in list(db.scalars(select(PriceAlert).order_by(PriceAlert.id))):
        row = prices.get(alert.asset)
        if row is None or now - row.fetched_at > FRESH:
            continue
        price, change = _price(row)
        if price is None:
            continue
        threshold = Decimal(alert.threshold)
        changed = False
        if alert.state == "armed" and crossed(alert.kind, threshold, price, change):
            changed = fire(db, alert, seen=alert.fires, price=price, change=change, now=row.fetched_at)
            fired += changed
        elif alert.state == "triggered" and back(alert.kind, threshold, price, change):
            changed = _rearm_auto(db, alert, seen=alert.fires, now=now)
        if changed:
            db.expire(alert)  # la mise à jour conditionnelle est passée à côté de l'objet chargé
    return fired


def text(alert: PriceAlert, *, privacy: str, public_url: str, tz: tzinfo) -> str:
    url = f"{public_url}/#/crypto"
    if privacy == "aggregates" or alert.triggered_at is None or alert.triggered_price is None:
        return TEMPLATES["aggregates"].format(url=url)
    local = alert.triggered_at.astimezone(tz)
    change = Decimal(alert.triggered_change) if alert.triggered_change is not None else Decimal(0)
    out = TEMPLATES[alert.kind].format(
        asset=alert.asset,
        threshold=threshold_text(alert),
        price=eur(Decimal(alert.triggered_price)),
        time=f"{local.hour} h {local.minute:02d}",
        change=percent(change, signed=True),
    )
    if alert.label and privacy == "full":
        out += TEMPLATES["label"].format(label=alert.label)
    return out + TEMPLATES["footer"].format(url=url)


def alerts(db: Session, *, now: datetime, privacy: str, public_url: str, tz: tzinfo) -> list[tuple[str, str, bool]]:
    """(clé, texte, urgent) : chaque déclenchement récent, une clé par franchissement (jamais deux fois). La date de
    création entre dans la clé : une alerte recréée sous le même numéro n'hérite pas des envois de l'ancienne."""
    rows = db.scalars(
        select(PriceAlert).where(PriceAlert.triggered_at >= now - WINDOW, PriceAlert.fires > 0).order_by(PriceAlert.id)
    )
    out = []
    for a in rows:
        key = f"price:{a.id}:{int(a.created_at.timestamp())}:{a.fires}"
        out.append((key, text(a, privacy=privacy, public_url=public_url, tz=tz), False))
    return out
