"""Couleur Tempo (#33) : la couleur d'aujourd'hui et de demain, relue sans clé chez api-couleur-tempo.fr (relais
gratuit et indépendant de la publication de RTE, vers 11 h la veille) et gardée jour par jour.

Avec ton accord « Couleur Tempo » seulement (désactivé par défaut) : ce serveur voit les dates demandées et
l'adresse de ton serveur, rien d'autre. Si ton contrat est en option Tempo, la veille d'un jour rouge part une
alerte dans tes messageries et notifications, en dehors de tes heures calmes. Tout passe par netfetch."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import energy_signals as sig
from app.models import TempoDay
from app.security import audit, netfetch
from app.services import consents
from app.services import energy as energy_service
from app.services.settings_store import get_setting, set_setting

CONSENT = "tempo"
RETRY_EVERY = timedelta(hours=2)  # demain encore inconnu (publié vers 11 h) : une nouvelle relève toutes les 2 h
MAX_BYTES = 50_000
EVENING_HOUR = 17  # l'alerte « demain rouge » part à partir de 17 h, heures calmes respectées
LICENCE = (
    "Service tiers gratuit et sans clé, code ouvert (github.com/jbromain/rte-tempo) sans licence de réutilisation "
    "affichée ; la couleur elle-même est publiée par RTE."
)

Fetch = Callable[[str], bytes]  # adresse → corps ; lève netfetch.FetchError


class TempoError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str) -> bytes:
        return netfetch.fetch(
            url, http=http, resolver=resolver, accept="application/json", max_bytes=MAX_BYTES, seconds=20.0
        ).body

    return get


def _state(db: Session) -> dict[str, Any]:
    raw = get_setting(db, "tempo_state")
    return dict(raw) if isinstance(raw, dict) else {}


def color(db: Session, day: date) -> str | None:
    row = db.get(TempoDay, day)
    return row.color if row is not None else None


def _due(db: Session, now: datetime, today: date) -> bool:
    if color(db, today) is not None and color(db, today + timedelta(days=1)) is not None:
        return False
    last = _state(db).get("last_attempt_at")
    try:
        return last is None or now - datetime.fromisoformat(str(last)) >= RETRY_EVERY
    except ValueError:
        return True


def refresh(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, Any]:
    """Relit hier, aujourd'hui et demain en un seul appel. Le réseau est interrogé hors transaction ; une relève
    ratée garde les couleurs connues et le dit."""
    consents.require(db, CONSENT)
    days = [today - timedelta(days=1), today, today + timedelta(days=1)]
    url = sig.tempo_url(days)
    db.commit()  # rien ne reste verrouillé pendant l'appel
    error: str | None = None
    found: dict[date, str | None] = {}
    try:
        found = sig.parse_tempo(fetch(url))
    except (netfetch.FetchError, ValueError) as exc:  # SignalFormatError est une ValueError
        error = f"Couleur Tempo indisponible ({exc}) : les couleurs connues restent."
    write_lock(db)
    db.expire_all()
    state = _state(db) | {"last_attempt_at": now.isoformat(), "last_error": error}
    stored = 0
    for day, value in found.items():
        if day not in days or value is None:
            continue  # un jour non demandé n'a rien à faire ici ; « pas encore publiée » ne s'écrit pas
        row = db.get(TempoDay, day)
        if row is None:
            db.add(TempoDay(day=day, color=value, source_url=url, fetched_at=now))
            stored += 1
        elif row.color != value:
            row.color, row.source_url, row.fetched_at = value, url, now
            stored += 1
    if error is None:
        state["last_success_at"] = now.isoformat()
    set_setting(db, "tempo_state", state)
    if stored or error:
        audit.record(db, "system", "tempo.refresh", stored=stored, failed=error is not None)
    db.flush()
    return {"stored": stored, "error": error}


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond (toutes les heures) : rien sans ton accord, rien quand aujourd'hui et demain sont connus."""
    if not consents.granted(db, CONSENT) or not _due(db, now, today):
        return {}
    result = refresh(db, now=now, today=today, fetch=fetch)
    return {"tempo_stored": int(result["stored"]), "tempo_errors": int(result["error"] is not None)}


def _day_view(db: Session, day: date) -> dict[str, Any]:
    c = color(db, day)
    return {"day": day.isoformat(), "color": c, "label": sig.TEMPO_LABELS.get(c or "", "pas encore publiée")}


def view(db: Session, today: date) -> dict[str, Any]:
    st = _state(db)
    contract = energy_service.contract(db)
    granted = consents.granted(db, CONSENT)
    return {
        "consent": granted,
        "is_tempo": bool(contract and contract["option"] == "tempo"),
        "today": _day_view(db, today) if granted else None,
        "tomorrow": _day_view(db, today + timedelta(days=1)) if granted else None,
        "source": sig.TEMPO_SOURCE,
        "source_url": sig.TEMPO_URL,
        "licence": LICENCE,
        "last_success_at": st.get("last_success_at"),
        "last_error": st.get("last_error"),
    }


def _date_fr(d: date) -> str:
    days = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    return f"{days[d.weekday()]} {d:%d/%m}"


def alerts(db: Session, *, today: date, local: datetime, public_url: str) -> list[tuple[str, str, bool]]:
    """La veille d'un jour rouge, à partir de 17 h, pour un contrat Tempo : une alerte, une seule fois (clé du jour).
    Jamais urgente : les heures calmes la retiennent."""
    if local.hour < EVENING_HOUR or not consents.granted(db, CONSENT):
        return []
    contract = energy_service.contract(db)
    if not contract or contract["option"] != "tempo":
        return []
    tomorrow = today + timedelta(days=1)
    if color(db, tomorrow) != "rouge":
        return []
    text = f"🔴 Demain, {_date_fr(tomorrow)}, est un jour rouge Tempo (couleur publiée par RTE)."
    prices = sig.tempo_prices(contract["kva"], energy_service.latest_cre(db, f"cre.tempo.{contract['kva']}."))
    if prices is not None:
        k = prices["kwh"]
        text += (
            f" Le kWh y coûte {sig.euro_kwh(k['rouge_hp_ttc'])} en heures pleines et {sig.euro_kwh(k['rouge_hc_ttc'])}"
            f" en heures creuses, contre {sig.euro_kwh(k['bleu_hp_ttc'])} et {sig.euro_kwh(k['bleu_hc_ttc'])} un jour "
            f"bleu (grille de la CRE du {sig.fr_date(date.fromisoformat(prices['as_of']))}, {contract['kva']} kVA)."
        )
    return [(f"tempo_red:{tomorrow.isoformat()}", f"{text} {public_url}/#/connecteurs", False)]
