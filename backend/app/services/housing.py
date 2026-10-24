"""Ton logement face aux données publiques (#34) : les DPE de ton immeuble relus à l'ADEME (tu indiques lequel est
ton logement, Bank ne choisit jamais à ta place), la ligne de ta commune dans la carte des loyers de l'ANIL,
l'encadrement des loyers (fiche F1314, loyers de référence de Paris), les taux de taxe foncière votés par ta commune
et la disponibilité de Géorisques, gardés chiffrés, datés et sourcés. Les corrélations les lisent sans réseau.

Un accord, désactivé par défaut (« Ton logement face aux données publiques ») : seul l'identifiant BAN de l'adresse
part vers l'ADEME, seul le code du département vers data.economie.gouv.fr ; la carte des loyers, la fiche F1314, les
quartiers et loyers de référence de Paris sont des fichiers entiers (le quartier de ton adresse se calcule ici), et
Géorisques n'est que sondé. Le réseau est interrogé hors du verrou d'écriture ; une relève ratée garde la précédente
et le dit. Tout passe par netfetch."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import write_lock
from app.domain import correlations as corr
from app.domain import housing as dom
from app.domain import property_tax as pt
from app.domain import rent_control as rc
from app.domain import yearahead as ya
from app.domain.normalize import strip_accents_upper
from app.models import HousingSource, Transaction, VaultDocument, VaultField
from app.security import audit, netfetch
from app.services import consents, home_value, territory

CONSENT = "housing"
DPE_EVERY = timedelta(days=90)  # un nouveau DPE dans l'immeuble est rare : relu chaque trimestre
MARKET_EVERY = timedelta(days=30)  # la fiche du jeu est relue chaque mois ; le CSV seulement s'il a changé
RETRY_AFTER = timedelta(hours=20)  # une source en échec est réessayée le lendemain
MAX_JSON = 600_000
MAX_CSV = 10_000_000  # un fichier de la carte des loyers pèse un peu moins de 5 Mo
PUBLIC_EVERY = timedelta(days=30)  # fiche F1314, jeu parisien, taux locaux, Géorisques : relus chaque mois
RISKS_RETRY = timedelta(days=7)  # Géorisques injoignable : ressondé chaque semaine, pas chaque jour
READABLE = ("verified", "to_review")

Fetch = Callable[[str, int], bytes]  # (adresse, taille maximale) → corps ; lève netfetch.FetchError


class HousingError(ValueError):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str, max_bytes: int) -> bytes:
        accept = "text/csv, */*;q=0.1" if url.endswith(".csv") else "application/json"
        if url.startswith("https://www.service-public.gouv.fr/"):
            accept = "text/html"
        elif "/exports/geojson" in url:
            accept = "application/geo+json, application/json"
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


# ------------------------------------------------------------------ relève : ouverture et clôture hors du verrou
def _begin(db: Session, key: str, now: datetime) -> dict[str, Any]:
    """Note l'essai et rend la relève gardée ; le réseau part ensuite, verrou relâché."""
    write_lock(db)
    row = _row(db, key) or HousingSource(key=key)
    row.last_attempt_at = now
    db.add(row)
    cached = _data(row)
    db.commit()
    return cached


def _reopen(db: Session, key: str) -> HousingSource:
    write_lock(db)
    db.expire_all()
    row = _row(db, key) or HousingSource(key=key)
    db.add(row)
    return row


def _failed(db: Session, row: HousingSource, actor: str, action: str, label: str, error: Exception) -> dict[str, Any]:
    row.last_error = str(error)[:300]
    db.flush()
    audit.record(db, actor, action, ok=False)
    return {"status": "failed", "message": f"{label} indisponible ({error}){_stamp(row)}."}


CHANGED = {"status": "failed", "message": "Ton logement a changé pendant la relève : réessaie."}


# ------------------------------------------------------------------ ce que tu indiques : époque, pièces
def details(db: Session) -> dict[str, Any]:
    data = _data(_row(db, "details"))
    epoque = data.get("epoque") if data.get("epoque") in rc.EPOQUES else None
    rooms = data.get("rooms") if isinstance(data.get("rooms"), int) else None
    return {"epoque": epoque, "rooms": rooms}


def save_details(db: Session, *, epoque: str | None, rooms: int | None, actor: str) -> None:
    """L'époque de construction et le nombre de pièces principales, tels que tu les indiques : Bank ne les devine
    jamais (le DPE dit une période qui ne recoupe pas celles de l'encadrement)."""
    if epoque is not None and epoque not in rc.EPOQUES:
        raise HousingError("Époque de construction inconnue", 422)
    if rooms is not None and not 1 <= rooms <= 20:
        raise HousingError("Nombre de pièces entre 1 et 20", 422)
    write_lock(db)
    row = _row(db, "details") or HousingSource(key="details")
    before = _data(row)
    after = {"epoque": epoque, "rooms": rooms}
    row.data = json.dumps(after)
    db.add(row)
    db.flush()
    # Le journal dit quels champs ont changé, pas leur valeur.
    audit.record(db, actor, "housing.details", fields=sorted(k for k in after if before.get(k) != after[k]))


# ------------------------------------------------------------------ encadrement des loyers
def _control(db: Session) -> tuple[rc.Territory, bool] | None:
    return rc.territory_of(dwelling(db)["code"])


def _lonlat(db: Session) -> tuple[float, float] | None:
    p = territory.place(db) or {}
    try:
        return float(p["lon"]), float(p["lat"])
    except (KeyError, TypeError, ValueError):
        return None


def page_check(db: Session) -> rc.PageCheck | None:
    data = _data(_row(db, "rentctl"))
    try:
        return rc.PageCheck(date.fromisoformat(str(data["verified"])), tuple(str(m) for m in data.get("missing") or ()))
    except (KeyError, ValueError):
        return None


def paris(db: Session) -> dict[str, Any] | None:
    """Le quartier de ton adresse et ses loyers de référence, si l'adresse n'a pas changé depuis la relève."""
    data = _data(_row(db, "paris"))
    at = _lonlat(db)
    if not data or at is None or data.get("at") != list(at):
        return None
    return data


def refresh_control(db: Session, *, fetch: Fetch, now: datetime, actor: str) -> dict[str, Any]:
    """La fiche F1314 (sa date de vérification, ses territoires) ; à Paris, le jeu des loyers de référence, entier,
    retéléchargé seulement s'il a changé. Le quartier se calcule ici : ni l'adresse ni les coordonnées ne partent."""
    consents.require(db, CONSENT)
    found = _control(db)
    at = _lonlat(db) if found and found[0].key == "paris" else None
    _begin(db, "rentctl", now)
    cached = _data(_row(db, "paris")) if at else {}
    error: Exception | None = None
    page: rc.PageCheck | None = None
    fresh: dict[str, Any] | None = None
    try:
        page = rc.parse_page(fetch(rc.PAGE_URL, rc.MAX_PAGE))
        if at is not None:
            modified = rc.parse_paris_meta(fetch(rc.paris_meta_url(), MAX_JSON))
            if cached.get("modified") != modified or cached.get("at") != list(at):
                rows = rc.load_rows(fetch(rc.paris_rows_url(), rc.MAX_PARIS_ROWS))
                years = rc.dataset_years(rows)
                quartiers = rc.parse_quartiers(fetch(rc.paris_shapes_url(max(years)), rc.MAX_PARIS_SHAPES))
                q = rc.quartier_at(at[0], at[1], quartiers)
                refs = rc.parse_references(rows, q.id) if q else []
                fresh = {
                    "modified": modified,
                    "at": list(at),
                    "quartier": q.id if q else None,
                    "quartier_name": q.name if q else None,
                    "years": years,
                    "refs": [r.to_dict() for r in refs],
                }
    except (netfetch.FetchError, rc.RentControlError) as exc:
        error = exc
    row = _reopen(db, "rentctl")
    if _control(db) != found or (at is not None and _lonlat(db) != at):
        return CHANGED
    if error is not None or page is None:
        return _failed(
            db,
            row,
            actor,
            "housing.control_refresh",
            "Encadrement des loyers",
            error or rc.RentControlError("Service-Public : fiche illisible"),
        )
    row.data = json.dumps({"verified": page.verified.isoformat(), "missing": list(page.missing)})
    row.source_url, row.fetched_at, row.last_error = rc.PAGE_URL, now, None
    if fresh is not None:
        prow = _row(db, "paris") or HousingSource(key="paris")
        prow.data = json.dumps(fresh)
        prow.source_url, prow.fetched_at, prow.last_error = rc.PARIS_PAGE, now, None
        db.add(prow)
    elif at is not None and (kept := _row(db, "paris")) is not None:
        kept.fetched_at = now  # même jeu, même adresse : la relève est à jour sans retéléchargement
    db.flush()
    audit.record(db, actor, "housing.control_refresh", ok=True, paris=at is not None)
    if found is None:
        return {"status": "refreshed", "message": "Ta commune n'est pas dans la liste de l'encadrement des loyers."}
    if page.changed:
        return {
            "status": "refreshed",
            "message": f"La fiche F1314 a été revue le {page.verified:%d/%m/%Y} : relis-la, la liste de Bank date du "
            f"{rc.LIST_VERIFIED:%d/%m/%Y}.",
        }
    if at is None:
        return {"status": "refreshed", "message": f"{found[0].name} : loyers encadrés (fiche F1314 relue)."}
    p = paris(db) or {}
    if p.get("quartier") is None:
        return {"status": "refreshed", "message": "Ton adresse n'est dans aucun quartier du jeu de la Ville de Paris."}
    return {"status": "refreshed", "message": f"Loyers de référence lus pour le quartier {p.get('quartier_name')}."}


def control_link(db: Session) -> rc.ControlLink | None:
    found = _control(db)
    if found is None:
        return None
    t, partial = found
    p = paris(db) if t.key == "paris" else None
    refs: list[rc.Reference] = []
    for r in (p or {}).get("refs") or []:
        try:
            refs.append(rc.Reference.from_dict(r))
        except (KeyError, TypeError):
            continue
    d = details(db)
    prof_rooms = home_value.profile(db).get("rooms")
    rooms, rooms_source = d["rooms"], "ce que tu as indiqué"
    if rooms is None and isinstance(prof_rooms, int):
        rooms, rooms_source = prof_rooms, "ta description du logement"
    return rc.ControlLink(
        territory=t,
        partial=partial,
        page=page_check(db),
        located=_lonlat(db) is not None,
        quartier=(p or {}).get("quartier_name"),
        years=tuple(int(y) for y in (p or {}).get("years") or ()),
        refs=tuple(refs),
        epoque=d["epoque"],
        rooms=rooms,
        rooms_source=rooms_source if rooms is not None else None,
    )


# ------------------------------------------------------------------ taxe foncière : taux votés
def _tax_code(db: Session) -> str:
    code = dwelling(db)["code"]
    if not code:
        raise HousingError("Commune inconnue : situe ton adresse (Réglages) ou décris ton logement (Patrimoine).")
    return pt.tax_commune(str(code))


def rates(db: Session) -> dict[int, pt.Rates]:
    """Les taux gardés, s'ils sont ceux de ta commune actuelle."""
    data = _data(_row(db, "taxes"))
    try:
        code = _tax_code(db)
    except HousingError:
        return {}
    if data.get("code") != code:
        return {}
    out: dict[int, pt.Rates] = {}
    for y, r in (data.get("rates") or {}).items():
        try:
            out[int(y)] = pt.Rates.from_dict(r)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def refresh_taxes(db: Session, *, fetch: Fetch, now: datetime, actor: str) -> dict[str, Any]:
    """Les taux du département entier (seul son code part), dont Bank garde la ligne de ta commune."""
    consents.require(db, CONSENT)
    code = _tax_code(db)
    try:
        url = pt.rates_url(pt.department(code))
    except pt.PropertyTaxError as exc:
        raise HousingError(str(exc)) from exc
    _begin(db, "taxes", now)
    error: Exception | None = None
    found: dict[int, pt.Rates] = {}
    try:
        found = pt.parse_rates(fetch(url, pt.MAX_RATES), code)
    except (netfetch.FetchError, pt.PropertyTaxError) as exc:
        error = exc
    row = _reopen(db, "taxes")
    try:
        if _tax_code(db) != code:
            return CHANGED
    except HousingError:
        return CHANGED
    if error is not None:
        return _failed(db, row, actor, "housing.taxes_refresh", "Fiscalité locale (data.economie.gouv.fr)", error)
    row.data = json.dumps({"code": code, "rates": {str(y): r.to_dict() for y, r in sorted(found.items())}})
    row.source_url, row.fetched_at, row.last_error = url, now, None
    db.flush()
    audit.record(db, actor, "housing.taxes_refresh", ok=True, years=len(found))
    if not found:
        return {"status": "refreshed", "message": "Ta commune n'est pas dans le jeu de la fiscalité locale."}
    last = found[max(found)]
    return {"status": "refreshed", "message": f"Taux {last.year} lus pour {last.commune.title()}."}


# ------------------------------------------------------------------ taxe foncière : ce que tu as payé, la prévision
def _payments(db: Session, today: date) -> tuple[list[tuple[date, int]], set[str]]:
    """Les prélèvements « taxe foncière » des trois dernières années (libellé de la banque), et leurs marchands."""
    rows = db.execute(
        select(Transaction.booking_date, Transaction.amount_cents, Transaction.raw_label, Transaction.merchant).where(
            Transaction.booking_date >= date(today.year - 3, 1, 1),
            Transaction.amount_cents < 0,
            Transaction.is_transfer.is_(False),
            Transaction.status == "booked",
        )
    )
    out: list[tuple[date, int]] = []
    merchants: set[str] = set()
    for d, cents, label, merchant in rows:
        if pt.LABEL.search(strip_accents_upper(str(label or ""))):
            out.append((d, -int(cents)))
            if merchant:
                merchants.add(str(merchant))
    return sorted(out), merchants


def _notices(db: Session) -> list[pt.Known]:
    out: list[pt.Known] = []
    for doc in db.scalars(
        select(VaultDocument).where(VaultDocument.kind == "property_tax", VaultDocument.status.in_(READABLE))
    ):
        fields: dict[str, Any] = {}
        for f in db.scalars(select(VaultField).where(VaultField.document_id == doc.id)):
            try:
                fields[f.key] = json.loads(f.value)
            except ValueError:
                continue
        try:
            year, amount = int(fields["annee"]), int(fields["montant"])
            teom = int(fields["teom"]) if fields.get("teom") is not None else None
            due = date.fromisoformat(str(fields["date_limite"])) if fields.get("date_limite") else None
        except (KeyError, TypeError, ValueError):
            continue
        if 2000 <= year <= 2100 and amount > 0:
            out.append(pt.Known(year, amount, "avis", teom, due, False, f"doc:{doc.id}"))
    return out


def tax_forecast(db: Session, today: date) -> tuple[pt.Forecast | None, list[str], set[str]]:
    """(prévision, ce qui manque, marchands des prélèvements) : rien n'est deviné, le manque est dit."""
    payments, merchants = _payments(db, today)
    paid = pt.from_payments(payments, today)
    base = pt.latest([*_notices(db), *([paid] if paid else [])])
    if base is None:
        return None, ["Taxe foncière : ni avis dans Papiers, ni prélèvement « taxe foncière » reconnu."], merchants
    if paid is not None and base.source == "avis" and paid.year == base.year:
        base = pt.Known(base.year, base.amount_cents, "avis", base.teom_cents, base.due or paid.due, paid.monthly,
                        base.ref, paid.payments)  # fmt: skip
    notes: list[str] = []
    found = rates(db)
    if not found:
        notes.append(
            "Taxe foncière : taux de ta commune pas encore lus (data.economie.gouv.fr) ; la prévision les compte "
            "inchangés."
        )
    f = pt.forecast(base, found, today)
    if f.due is None:
        notes.append("Taxe foncière : date d'échéance inconnue (ni date limite sur l'avis, ni prélèvement vu).")
    return f, notes + list(f.notes), merchants


def tax_provision(db: Session, today: date) -> ya.ForecastSpike | None:
    """La taxe foncière prévue, posée à son échéance dans l'année qui vient (à la place du pic détecté)."""
    if not consents.granted(db, CONSENT):
        return None
    f, _notes, merchants = tax_forecast(db, today)
    if f is None or f.due is None or f.base.monthly:
        return None
    amount = f.amount_cents if f.amount_cents is not None else f.base.amount_cents
    how = (
        "prévue à bases inchangées"
        if f.amount_cents is not None
        else f"montant {f.base.year}, effet des taux non chiffré"
    )
    return ya.ForecastSpike(
        key=f"housing:taxe_fonciere:{f.target}",
        label=f"Taxe foncière {f.target}",
        amount_cents=amount,
        day=f.due,
        replaces=frozenset(merchants),
        note=f"Taxe foncière {f.target} : {amount / 100:.0f} € ({how} ; voir Ton logement).",
    )


# ------------------------------------------------------------------ Géorisques : sondé, sans rien envoyer de toi
def risks(db: Session) -> dict[str, Any]:
    row = _row(db, "risks")
    data = _data(row)
    return {
        "reachable": data.get("reachable") if data else None,
        "checked_at": row.last_attempt_at.isoformat() if row and row.last_attempt_at else None,
        "last_error": row.last_error if row else None,
    }


def refresh_risks(db: Session, *, fetch: Fetch, now: datetime, actor: str) -> dict[str, Any]:
    """Sonde l'API de Géorisques par sa description publique (aucun paramètre) : le 24 octobre 2026, toute adresse
    en /api/ y coupait la connexion depuis ce serveur, alors que le site répondait. Joignable ou non, Bank ne lit
    pas de risque dont il n'a pas pu vérifier le format, et renvoie vers la page officielle."""
    consents.require(db, CONSENT)
    _begin(db, "risks", now)
    error: Exception | None = None
    try:
        fetch(rc.GEORISQUES_PROBE, MAX_JSON)
    except netfetch.FetchError as exc:
        error = exc
    row = _reopen(db, "risks")
    row.data = json.dumps({"reachable": error is None})
    row.source_url = rc.GEORISQUES_PROBE
    if error is not None:
        row.last_error = str(error)[:300]
        db.flush()
        audit.record(db, actor, "housing.risks_probe", ok=False)
        return {"status": "failed", "message": f"Géorisques injoignable depuis ton serveur ({error})."}
    row.fetched_at, row.last_error = now, None
    db.flush()
    audit.record(db, actor, "housing.risks_probe", ok=True)
    return {
        "status": "refreshed",
        "message": "Géorisques répond depuis ton serveur ; le format de ses risques n'a pas encore pu être vérifié, "
        "Bank n'en tire rien : la page officielle est liée.",
    }


def _home_insurance(db: Session) -> dict[str, Any] | None:
    """Ton assurance habitation rangée dans Papiers, pour lire les risques à côté."""
    for doc in db.scalars(
        select(VaultDocument)
        .where(VaultDocument.kind == "insurance", VaultDocument.status.in_(READABLE))
        .order_by(VaultDocument.document_date.desc())
    ):
        fields: dict[str, Any] = {}
        for f in db.scalars(select(VaultField).where(VaultField.document_id == doc.id)):
            try:
                fields[f.key] = json.loads(f.value)
            except ValueError:
                continue
        if fields.get("type") == "habitation":
            return {"id": doc.id, "insurer": fields.get("assureur"), "until": fields.get("valable_jusqu")}
    return None


def _due(row: HousingSource | None, now: datetime, every: timedelta, rest: timedelta = RETRY_AFTER) -> bool:
    if row is not None and row.last_error and row.last_attempt_at and now - row.last_attempt_at < rest:
        return False
    return row is None or row.fetched_at is None or now - row.fetched_at >= every


def tick(db: Session, *, now: datetime, today: date, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond : rien sans ton accord ; le DPE relu chaque trimestre (ou à un changement d'adresse), la fiche
    de la carte des loyers chaque mois, la fiche F1314 (et le jeu parisien) chaque mois si ta commune est encadrée,
    les taux locaux chaque mois si une taxe foncière est connue, Géorisques sondé chaque mois (chaque semaine s'il
    est injoignable). Une donnée manquante n'est pas une erreur : le panneau la dit."""
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
    except HousingError:
        pass
    found = _control(db)
    if found is not None:
        moved = found[0].key == "paris" and _lonlat(db) is not None and paris(db) is None
        if (moved and not _resting(_row(db, "rentctl"), now)) or _due(_row(db, "rentctl"), now, PUBLIC_EVERY):
            result = refresh_control(db, fetch=fetch, now=now, actor="system")
            report[f"housing_control_{result['status']}"] = 1
    f, _notes, _m = tax_forecast(db, today)
    if f is not None:
        try:
            stale_code = _data(_row(db, "taxes")).get("code") != _tax_code(db)
            if (stale_code and not _resting(_row(db, "taxes"), now)) or _due(_row(db, "taxes"), now, PUBLIC_EVERY):
                result = refresh_taxes(db, fetch=fetch, now=now, actor="system")
                report[f"housing_taxes_{result['status']}"] = 1
        except HousingError:
            pass
    if _due(_row(db, "risks"), now, PUBLIC_EVERY, RISKS_RETRY):
        result = refresh_risks(db, fetch=fetch, now=now, actor="system")
        report[f"housing_risks_{result['status']}"] = 1
    return report


# ------------------------------------------------------------------ pour les corrélations et le panneau
def link(db: Session, today: date) -> corr.HousingLink | None:
    """Ce que les corrélations lisent, sans réseau : rien sans ton accord."""
    if not consents.granted(db, CONSENT):
        return None
    home = dwelling(db)
    tax, tax_notes, _m = tax_forecast(db, today)
    return corr.HousingLink(
        dpe=chosen_dpe(db),
        depcode=home["depcode"],
        market=market(db),
        surface_m2=home["surface_m2"],
        surface_source=home["surface_source"],
        control=control_link(db),
        tax=tax,
        tax_notes=tuple(tax_notes),
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


def view(db: Session, today: date) -> dict[str, Any]:
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
        "details": details(db) | {"epoques": [{"key": k, "label": v} for k, v in rc.EPOQUES.items()]},
        "control": _control_view(db),
        "taxes": _taxes_view(db, today),
        "risks": risks(db)
        | {
            "page": rc.GEORISQUES_PAGE,
            "insurance": _home_insurance(db),
            "source": _source(_row(db, "risks"), "Géorisques (sonde de l'API, sans paramètre)"),
        },
    }


def _control_view(db: Session) -> dict[str, Any]:
    found = _control(db)
    page = page_check(db)
    t, partial = found if found else (None, False)
    p = paris(db) if t is not None and t.key == "paris" else None
    return {
        "commune": dwelling(db)["commune"],
        "territory": None
        if t is None
        else {
            "name": t.name,
            "start": t.start.isoformat(),
            "partial": partial,
            "simulator": t.simulator,
            "simulator_label": t.simulator_label,
            "open_data": t.key == "paris",
        },
        "list": {
            "source": rc.PAGE_SOURCE,
            "url": rc.PAGE_URL,
            "verified": rc.LIST_VERIFIED.isoformat(),
            "checked_on": rc.LIST_CHECKED_ON,
            "end": rc.END.isoformat(),
        },
        "page": None
        if page is None
        else {"verified": page.verified.isoformat(), "missing": list(page.missing), "changed": page.changed},
        "paris": None
        if p is None
        else {
            "quartier": p.get("quartier_name"),
            "years": p.get("years") or [],
            "page": rc.PARIS_PAGE,
            "source": rc.PARIS_SOURCE,
            "arrete_2026": rc.PARIS_ARRETE_2026,
        },
        "cdc": {"source": rc.CDC_SOURCE, "url": rc.CDC_URL},
        "source": _source(_row(db, "rentctl"), rc.PAGE_SOURCE),
        "paris_source": _source(_row(db, "paris"), rc.PARIS_SOURCE) if t is not None and t.key == "paris" else None,
    }


def _taxes_view(db: Session, today: date) -> dict[str, Any]:
    f, notes, _m = tax_forecast(db, today)
    found = rates(db)
    try:
        code: str | None = _tax_code(db)
    except HousingError:
        code = None
    return {
        "code": code,
        "known": None
        if f is None
        else {
            "year": f.base.year,
            "amount_cents": f.base.amount_cents,
            "teom_cents": f.base.teom_cents,
            "source": f.base.source,
            "monthly": f.base.monthly,
        },
        "forecast": None
        if f is None
        else {
            "target": f.target,
            "amount_cents": f.amount_cents,
            "due": f.due.isoformat() if f.due else None,
            "unknown_rates": list(f.unknown_rates),
        },
        "rates": [r.to_dict() for _y, r in sorted(found.items())],
        "notes": notes,
        "page": pt.RATES_PAGE,
        "bases": {"source": pt.BASES_SOURCE, "url": pt.BASES_URL},
        "source": _source(_row(db, "taxes"), pt.RATES_SOURCE),
    }
