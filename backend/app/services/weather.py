"""Météo de ta commune (#33) : la température moyenne de chaque jour, relue à l'archive d'Open-Meteo aux
coordonnées de ton adresse arrondies au dixième de degré (environ 10 km), pour les degrés-jours de chauffe.

Avec ton accord « Météo de ta commune » seulement (désactivé par défaut) : seules ces coordonnées arrondies partent,
jamais l'adresse. Une relève par jour au plus ; une relève ratée garde les températures connues et le dit. Les
températures sont effacées quand ton adresse change de commune ou est retirée. Tout passe par netfetch."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import energy_analysis as dom
from app.domain.energy_signals import SignalFormatError
from app.models import WeatherDay
from app.security import audit, netfetch
from app.services import consents, territory
from app.services.settings_store import get_setting, set_setting

CONSENT = "meteo"
EVERY = timedelta(hours=20)  # une relève par jour (l'archive ajoute la veille dans la journée)
HISTORY_MONTHS = 25  # deux ans et le mois en cours : les douze derniers mois et les mêmes mois de l'an dernier
OVERLAP_DAYS = 5  # les derniers jours de l'archive sont révisés : on les relit
MAX_BYTES = 200_000  # 800 jours de températures pèsent environ 15 Ko
NO_PLACE = (
    "Ton adresse n'est pas encore située : enregistre-la dans Réglages › Ton adresse et ton calendrier, la météo de "
    "ta commune suivra."
)

Fetch = Callable[[str], bytes]  # adresse → corps ; lève netfetch.FetchError


class WeatherError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str) -> bytes:
        return netfetch.fetch(
            url, http=http, resolver=resolver, accept="application/json", max_bytes=MAX_BYTES, seconds=30.0
        ).body

    return get


def _state(db: Session) -> dict[str, Any]:
    raw = get_setting(db, "meteo_state")
    return dict(raw) if isinstance(raw, dict) else {}


def _cell(db: Session) -> tuple[float, float] | None:
    p = territory.place(db) or {}
    try:
        return dom.meteo_cell(p.get("lat"), p.get("lon"))
    except SignalFormatError:
        return None


def _history_start(today: date) -> date:
    n = today.year * 12 + today.month - 1 - (HISTORY_MONTHS - 1)
    return date(n // 12, n % 12 + 1, 1)


def forget(db: Session) -> None:
    """Températures effacées (adresse retirée ou changée de commune) : elles disent où tu habites."""
    db.execute(delete(WeatherDay))


@consents.on_revoke(CONSENT)
def _revoked(db: Session) -> None:
    """Accord retiré : les températures gardées grâce à lui sont effacées."""
    forget(db)
    set_setting(db, "meteo_state", {})


def refresh(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, Any]:
    """Relit les jours manquants jusqu'à hier, en un appel. Le réseau est interrogé hors transaction."""
    consents.require(db, CONSENT)
    cell = _cell(db)
    if cell is None:
        raise WeatherError(NO_PLACE, 409)
    place = territory.place(db) or {}
    same_place = place.get("meteo_cell") == list(cell)
    last = db.scalar(select(WeatherDay.day).order_by(WeatherDay.day.desc())) if same_place else None
    start = max(_history_start(today), last - timedelta(days=OVERLAP_DAYS)) if last else _history_start(today)
    end = today - timedelta(days=1)
    url = dom.meteo_url(cell, start, end)
    db.commit()  # rien ne reste verrouillé pendant l'appel
    error: str | None = None
    found: dict[date, float] = {}
    try:
        found = dom.parse_meteo(fetch(url))
    except (netfetch.FetchError, ValueError, ArithmeticError) as exc:  # SignalFormatError est une ValueError
        error = f"Météo indisponible ({exc}) : les températures connues restent."
    write_lock(db)
    db.expire_all()
    if not consents.granted(db, CONSENT):
        return {"stored": 0, "error": None}  # accord retiré pendant l'appel : rien ne s'écrit
    if _cell(db) != cell:
        return {"stored": 0, "error": "Ton adresse a changé pendant la relève : la météo sera relue."}
    state = _state(db) | {"last_attempt_at": now.isoformat(), "last_error": error}
    stored = 0
    if error is None:
        if not same_place:
            forget(db)  # nouvelle commune : les températures de l'ancienne ne servent plus
            territory.mark_place(db, "meteo_cell", list(cell))
        existing = {r.day: r for r in db.scalars(select(WeatherDay).where(WeatherDay.day >= start))}
        for day, t in found.items():
            if not start <= day <= end:
                continue
            row = existing.get(day)
            if row is None:
                db.add(WeatherDay(day=day, temp_c=t, fetched_at=now))
                stored += 1
            elif row.temp_c != t:
                row.temp_c, row.fetched_at = t, now
                stored += 1
        state["last_success_at"] = now.isoformat()
    set_setting(db, "meteo_state", state)
    if stored or error:
        audit.record(db, "system", "weather.refresh", stored=stored, failed=error is not None)
    db.flush()
    return {"stored": stored, "error": error}


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond : rien sans ton accord ni adresse située, une relève par jour au plus."""
    if not consents.granted(db, CONSENT) or _cell(db) is None:
        return {}
    last = _state(db).get("last_attempt_at")
    try:
        if last is not None and now - datetime.fromisoformat(str(last)) < EVERY:
            return {}
    except ValueError:
        pass
    result = refresh(db, now=now, today=today, fetch=fetch)
    return {"weather_stored": int(result["stored"]), "weather_errors": int(result["error"] is not None)}


def temps(db: Session, since: date) -> dict[date, float]:
    """Températures gardées, seulement avec ton accord en cours et pour l'adresse actuelle."""
    if not consents.granted(db, CONSENT):
        return {}
    cell = _cell(db)
    if cell is None or (territory.place(db) or {}).get("meteo_cell") != list(cell):
        return {}
    return {r.day: r.temp_c for r in db.scalars(select(WeatherDay).where(WeatherDay.day >= since))}


def view(db: Session) -> dict[str, Any]:
    st = _state(db)
    return {
        "consent": consents.granted(db, CONSENT),
        "place_known": _cell(db) is not None,
        "source": dom.METEO_SOURCE,
        "source_url": dom.METEO_TERMS_URL,
        "base_c": dom.HDD_BASE_C,
        "last_success_at": st.get("last_success_at"),
        "last_error": st.get("last_error"),
    }
