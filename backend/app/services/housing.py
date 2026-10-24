"""Ton logement face aux données publiques (#34) : les DPE de ton immeuble relus à l'ADEME (tu indiques lequel est
ton logement, Bank ne choisit jamais à ta place), la ligne de ta commune dans la carte des loyers de l'ANIL, gardés
chiffrés, datés et sourcés. Les corrélations les lisent sans réseau.

Un accord, désactivé par défaut (« Ton logement face aux données publiques ») : seul l'identifiant BAN de l'adresse
part vers l'ADEME ; la carte des loyers est un fichier national, rien te concernant ne part. Le réseau est interrogé
hors du verrou d'écriture ; une relève ratée garde la précédente et le dit. Tout passe par netfetch."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import correlations as corr
from app.domain import housing as dom
from app.models import HousingSource
from app.security import audit, netfetch
from app.services import consents, home_value, territory

CONSENT = "housing"
DPE_EVERY = timedelta(days=90)  # un nouveau DPE dans l'immeuble est rare : relu chaque trimestre
MARKET_EVERY = timedelta(days=30)  # la fiche du jeu est relue chaque mois ; le CSV seulement s'il a changé
RETRY_AFTER = timedelta(hours=20)  # une source en échec est réessayée le lendemain
MAX_JSON = 600_000
MAX_CSV = 10_000_000  # un fichier de la carte des loyers pèse un peu moins de 5 Mo

Fetch = Callable[[str, int], bytes]  # (adresse, taille maximale) → corps ; lève netfetch.FetchError


class HousingError(ValueError):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str, max_bytes: int) -> bytes:
        accept = "text/csv, */*;q=0.1" if url.endswith(".csv") else "application/json"
        return netfetch.fetch(url, http=http, resolver=resolver, accept=accept, max_bytes=max_bytes, seconds=60.0).body

    return get


def _row(db: Session, key: str) -> HousingSource | None:
    return db.get(HousingSource, key)


def _data(row: HousingSource | None) -> dict[str, Any]:
    if row is None or not row.data:
        return {}
    try:
        data = json.loads(row.data)
    except ValueError:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _resting(row: HousingSource | None, now: datetime) -> bool:
    return bool(row and row.last_error and row.last_attempt_at and now - row.last_attempt_at < RETRY_AFTER)


def _stamp(row: HousingSource | None) -> str:
    return f" ; la relève du {row.fetched_at:%d/%m/%Y} reste" if row and row.fetched_at else ""


# ------------------------------------------------------------------ DPE : lecture
def _ban_id(db: Session) -> str | None:
    p = territory.place(db) or {}
    return str(p["ban_id"]) if p.get("ban_id") else None


def _dpe_missing(db: Session) -> str | None:
    """Ce qui manque pour chercher le DPE, dit tel quel ; None si la recherche est possible."""
    p = territory.place(db)
    if not p:
        return "Situe d'abord ton adresse (Réglages › Ton adresse) : le DPE se cherche au numéro de rue près."
    if p.get("ban_id"):
        return None
    if p.get("precision") != "housenumber":
        return "Ton adresse n'est pas située au numéro près : ajoute le numéro de rue pour retrouver ton immeuble."
    return "Situe de nouveau ton adresse : Bank garde désormais l'identifiant qui retrouve ton immeuble."


def dpe_candidates(db: Session) -> tuple[list[dom.Dpe], dict[str, Any]]:
    """Les DPE gardés pour l'adresse actuelle (rien si l'adresse a changé depuis la relève)."""
    data = _data(_row(db, "dpe"))
    if not data or data.get("ban_id") != _ban_id(db):
        return [], {}
    out = []
    for d in data.get("candidates") or []:
        try:
            out.append(dom.Dpe.from_dict(d))
        except (KeyError, TypeError, ValueError):
            continue
    return out, data


def chosen_dpe(db: Session) -> dom.Dpe | None:
    """Le DPE que tu as reconnu comme le tien, pour l'adresse actuelle seulement."""
    _cands, data = dpe_candidates(db)
    chosen = data.get("chosen")
    if not isinstance(chosen, dict):
        return None
    try:
        return dom.Dpe.from_dict(chosen)
    except (KeyError, TypeError, ValueError):
        return None


# ------------------------------------------------------------------ DPE : relève et choix
def refresh_dpe(db: Session, *, fetch: Fetch, now: datetime, actor: str) -> dict[str, Any]:
    consents.require(db, CONSENT)
    missing = _dpe_missing(db)
    ban = _ban_id(db)
    if missing or ban is None:
        raise HousingError(missing or "Adresse inconnue")
    url = dom.dpe_url(ban)
    write_lock(db)
    row = _row(db, "dpe") or HousingSource(key="dpe")
    row.last_attempt_at = now
    db.add(row)
    db.commit()
    error: Exception | None = None
    try:
        found, total = dom.parse_dpe(fetch(url, MAX_JSON), ban)
    except (netfetch.FetchError, dom.HousingError) as exc:
        error = exc
    write_lock(db)
    db.expire_all()
    row = _row(db, "dpe") or HousingSource(key="dpe")
    db.add(row)
    if _ban_id(db) != ban:
        return {"status": "failed", "message": "Ton adresse a changé pendant la relève : réessaie."}
    if error is not None:
        row.last_error = str(error)[:300]
        db.flush()
        audit.record(db, actor, "housing.dpe_refresh", ok=False)
        return {"status": "failed", "message": f"Base des DPE de l'ADEME indisponible ({error}){_stamp(row)}."}
    before = _data(row)
    same = before.get("ban_id") == ban
    row.data = json.dumps(
        {
            "ban_id": ban,
            "candidates": [d.to_dict() for d in found],
            "total": total,
            "chosen": before.get("chosen") if same else None,
            "chosen_at": before.get("chosen_at") if same else None,
            "none_mine": bool(before.get("none_mine")) if same else False,
        }
    )
    row.source_url, row.fetched_at, row.last_error = url, now, None
    db.flush()
    # Le journal dit combien de DPE, jamais l'adresse.
    audit.record(db, actor, "housing.dpe_refresh", ok=True, found=len(found))
    if not found:
        message = (
            "Aucun DPE publié à ton adresse dans la base de l'ADEME (DPE établis depuis juillet 2021). Si ton bail "
            "cite une classe énergie, Bank la lit dans Papiers."
        )
    else:
        more = f" (les {dom.DPE_MAX} plus récents sur {total})" if total > len(found) else ""
        message = f"{len(found)} DPE à ton adresse{more} : indique lequel est ton logement."
    return {"status": "refreshed", "message": message}


def choose_dpe(db: Session, number: str | None, *, actor: str, now: datetime) -> None:
    """Ton choix parmi les DPE de l'adresse (ou « aucun n'est le mien ») : Bank ne retient jamais un DPE seul."""
    write_lock(db)
    cands, data = dpe_candidates(db)
    if not data:
        raise HousingError("Cherche d'abord les DPE de ton adresse")
    row = _row(db, "dpe")
    if row is None:
        raise HousingError("Cherche d'abord les DPE de ton adresse")
    if number is None:
        data.update(chosen=None, chosen_at=now.isoformat(), none_mine=True)
    else:
        pick = next((d for d in cands if d.number == number), None)
        if pick is None:
            raise HousingError("Ce DPE n'est pas parmi ceux de ton adresse", 422)
        data.update(chosen=pick.to_dict(), chosen_at=now.isoformat(), none_mine=False)
    row.data = json.dumps(data)
    db.flush()
    audit.record(db, actor, "housing.dpe_choose", chosen=number is not None)


# ------------------------------------------------------------------ ton logement : type, surface, commune
def dwelling(db: Session) -> dict[str, Any]:
    """Type, pièces et surface de ton logement : ta description (Patrimoine) d'abord, sinon le DPE reconnu ; la
    commune de ton adresse située, sinon celle de ta description. Chaque valeur dit d'où elle vient."""
    prof = home_value.profile(db)
    dpe = chosen_dpe(db)
    place = territory.place(db) or {}
    kind, kind_source = prof.get("kind"), "ta description du logement"
    if kind is None and dpe is not None and dpe.building in ("appartement", "maison"):
        kind, kind_source = dpe.building, "le DPE que tu as reconnu"
    surface: float | None = float(prof["surface"]) if prof.get("surface") else None
    surface_source = "ta description du logement" if surface else None
    if surface is None and dpe is not None and dpe.surface_m2:
        surface, surface_source = dpe.surface_m2, "le DPE que tu as reconnu"
    code = place.get("citycode") or prof.get("code")
    return {
        "kind": kind,
        "kind_source": kind_source if kind else None,
        "rooms": prof.get("rooms"),
        "surface_m2": surface,
        "surface_source": surface_source,
        "code": str(code) if code else None,
        "commune": place.get("city") or prof.get("commune"),
        "depcode": place.get("depcode") or (dpe.depcode if dpe else None),
    }


def _market_target(db: Session) -> tuple[str, str]:
    home = dwelling(db)
    if not home["code"]:
        raise HousingError("Commune inconnue : situe ton adresse (Réglages) ou décris ton logement (Patrimoine).")
    file_key = dom.market_file(home["kind"], home["rooms"])
    if file_key is None:
        raise HousingError(
            "Type de logement inconnu (appartement ou maison) : décris ton logement dans Patrimoine, ou reconnais "
            "ton DPE."
        )
    return str(home["code"]), file_key


def market(db: Session) -> dom.Market | None:
    """La ligne gardée, si elle est celle de ta commune et de ton type de logement actuels."""
    data = _data(_row(db, "market"))
    raw = data.get("market")
    if not isinstance(raw, dict):
        return None
    try:
        target = _market_target(db)
        found = dom.Market.from_dict(raw)
    except (HousingError, KeyError, TypeError):
        return None
    return found if (found.code, found.file) == target else None


# ------------------------------------------------------------------ carte des loyers : relève
def _latest_edition(fetch: Fetch, today: date, file_key: str) -> tuple[int, str, str | None]:
    """(millésime, adresse du CSV, mise en ligne) de la carte la plus récente : l'année en cours, sinon les deux
    précédentes (une fiche absente répond 404)."""
    last: Exception | None = None
    for year in (today.year, today.year - 1, today.year - 2):
        try:
            body = fetch(dom.market_dataset_url(year), MAX_JSON)
        except netfetch.FetchError as exc:
            if str(exc).endswith(" 404"):
                continue
            raise
        try:
            url, published = dom.pick_resource(body, file_key)
        except dom.HousingError as exc:
            last = exc
            continue
        return year, url, published
    raise dom.HousingError(str(last) if last else "Carte des loyers introuvable sur data.gouv.fr")


def refresh_market(db: Session, *, fetch: Fetch, now: datetime, today: date, actor: str) -> dict[str, Any]:
    consents.require(db, CONSENT)
    code, file_key = _market_target(db)
    write_lock(db)
    row = _row(db, "market") or HousingSource(key="market")
    row.last_attempt_at = now
    db.add(row)
    cached = _data(row)
    db.commit()
    error: Exception | None = None
    found: dom.Market | None = None
    year, url, published, unchanged = 0, "", None, False
    try:
        year, url, published = _latest_edition(fetch, today, file_key)
        unchanged = cached.get("csv_url") == url and cached.get("code") == code and cached.get("file") == file_key
        if not unchanged:
            found = dom.parse_market(fetch(url, MAX_CSV), code, file_key, year)
    except (netfetch.FetchError, dom.HousingError) as exc:
        error = exc
    write_lock(db)
    db.expire_all()
    row = _row(db, "market") or HousingSource(key="market")
    db.add(row)
    try:
        if _market_target(db) != (code, file_key):
            return {"status": "failed", "message": "Ton logement a changé pendant la relève : réessaie."}
    except HousingError:
        return {"status": "failed", "message": "Ton logement a changé pendant la relève : réessaie."}
    if error is not None:
        row.last_error = str(error)[:300]
        db.flush()
        audit.record(db, actor, "housing.market_refresh", ok=False)
        return {"status": "failed", "message": f"Carte des loyers indisponible ({error}){_stamp(row)}."}
    if not unchanged:
        row.data = json.dumps(
            {
                "code": code,
                "file": file_key,
                "year": year,
                "csv_url": url,
                "published": published,
                "page": dom.MARKET_PAGE.format(year=year),
                "market": found.to_dict() if found else None,
            }
        )
        row.source_url = url
    row.fetched_at, row.last_error = now, None
    db.flush()
    audit.record(db, actor, "housing.market_refresh", ok=True, changed=not unchanged)
    current = market(db)
    if current is None:
        return {"status": "refreshed", "message": f"Ta commune n'est pas dans la carte des loyers {year}."}
    return {"status": "refreshed", "message": f"Carte des loyers {current.year} lue pour {current.commune}."}


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond : rien sans ton accord ; le DPE relu chaque trimestre (ou à un changement d'adresse), la fiche
    de la carte des loyers chaque mois. Une donnée manquante n'est pas une erreur : le panneau la dit."""
    if not consents.granted(db, CONSENT):
        return {}
    report: dict[str, int] = {}
    row = _row(db, "dpe")
    ban = _ban_id(db)
    if ban and not _resting(row, now):
        data = _data(row)
        if data.get("ban_id") != ban or row is None or row.fetched_at is None or now - row.fetched_at >= DPE_EVERY:
            result = refresh_dpe(db, fetch=fetch, now=now, actor="system")
            report[f"housing_dpe_{result['status']}"] = 1
    row = _row(db, "market")
    try:
        code, file_key = _market_target(db)
    except HousingError:
        return report
    data = _data(row)
    stale = (
        row is None
        or row.fetched_at is None
        or (data.get("code"), data.get("file")) != (code, file_key)
        or now - row.fetched_at >= MARKET_EVERY
    )
    if stale and not _resting(row, now):
        result = refresh_market(db, fetch=fetch, now=now, today=today, actor="system")
        report[f"housing_market_{result['status']}"] = 1
    return report


# ------------------------------------------------------------------ pour les corrélations et le panneau
def link(db: Session) -> corr.HousingLink | None:
    """Ce que les corrélations lisent, sans réseau : rien sans ton accord."""
    if not consents.granted(db, CONSENT):
        return None
    home = dwelling(db)
    return corr.HousingLink(
        dpe=chosen_dpe(db),
        depcode=home["depcode"],
        market=market(db),
        surface_m2=home["surface_m2"],
        surface_source=home["surface_source"],
    )


def _source(row: HousingSource | None, name: str) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "name": name,
        "url": row.source_url,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "last_error": row.last_error,
    }


def view(db: Session) -> dict[str, Any]:
    cands, data = dpe_candidates(db)
    chosen = chosen_dpe(db)
    m = market(db)
    mdata = _data(_row(db, "market"))
    try:
        target: tuple[str, str] | None = _market_target(db)
        target_error = None
    except HousingError as exc:
        target, target_error = None, str(exc)
    home = dwelling(db)
    return {
        "consent": consents.granted(db, CONSENT),
        "dpe": {
            "missing": _dpe_missing(db),
            "candidates": [d.to_dict() for d in cands],
            "total": data.get("total"),
            "chosen": chosen.to_dict() if chosen else None,
            "none_mine": bool(data.get("none_mine")),
            "chosen_at": data.get("chosen_at"),
            "source": _source(_row(db, "dpe"), dom.DPE_SOURCE),
            "page": dom.DPE_PAGE,
        },
        "rule": {
            "text": (
                "Réviser le loyer est interdit pour un logement classé F ou G quand le bail est signé, renouvelé ou "
                "tacitement reconduit depuis le 24 août 2022 en métropole (depuis le 1er juillet 2024 en Guadeloupe, "
                "Guyane, Martinique, à La Réunion et à Mayotte)."
            ),
            "source": dom.RULE_SOURCE,
            "url": dom.RULE_URL,
            "checked_on": dom.RULE_CHECKED_ON,
        },
        "home": home,
        "market": {
            "missing": target_error,
            "file_label": dom.MARKET_FILES[target[1]][1] if target else None,
            "row": m.to_dict() if m else None,
            "reliable": m.reliable if m else None,
            "rule": (
                f"Fiable seulement avec un R² ajusté d'au moins 0,5 et au moins {dom.MIN_OBS} annonces dans la commune "
                "(ANIL)."
            ),
            "year": mdata.get("year") if m else None,
            "published": mdata.get("published") if m else None,
            "page": mdata.get("page") if m else None,
            "credit": dom.MARKET_CREDIT,
            "source": _source(_row(db, "market"), "ANIL, carte des loyers (data.gouv.fr)"),
        },
    }
