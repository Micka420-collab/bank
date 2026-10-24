"""Valeur de ton logement : ta description (chiffrée), les ventes de ta commune téléchargées au plus une fois par
trimestre depuis les fichiers DVF géolocalisés d'Etalab (réduites, chiffrées, extraction datée), l'estimation par
les ventes comparables (domain.dvf) et sa ligne à part dans le patrimoine net, en face du prêt immobilier.

Seul le fichier communal part du réseau, par le téléchargeur durci (netfetch) et avec ton accord : ni adresse, ni
surface, ni section ne sortent ; le filtre par section se fait ici."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import dvf
from app.domain import loans as loans_domain
from app.domain.recurring import add_months
from app.domain.wealth import AccountBalance
from app.models import Account, DvfCache, HomeProfile, Loan
from app.security import audit, netfetch
from app.services import consents

CONSENT = "home_value"
BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv"
GEO_URL = "https://geo.api.gouv.fr/communes"
# Un fichier communal d'une grande ville pèse quelques Mo par an (Toulouse, Nice : jusqu'à ~15 Mo) : 40 Mo au plus.
MAX_FILE_BYTES = 40_000_000
YEARS_BACK = 3  # l'année en cours et les trois précédentes : 24 mois pleins malgré le retard de publication
MAX_ATTEMPTS_PER_DAY = 3  # essais de téléchargement par jour (manuels et automatiques) : on reste poli
HOME_LINE = "Logement (estimation DVF)"
HOME_KIND = "real_estate"
HOME_LINE_ID = -1_000_000_000  # ligne à part du patrimoine : jamais un vrai compte (> 0) ni un prêt (−id)
SEARCH_RE = re.compile(r"^(?:\d{5}|[^\W\d_][\w'’ -]{1,59})$")

Fetch = Callable[[str], bytes | None]  # None : pas de fichier pour cette année (404)


class HomeValueError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def default_fetch(http: Any = None, resolver: netfetch.Resolver | None = None) -> Fetch:
    def get(url: str) -> bytes | None:
        try:
            result = netfetch.fetch(
                url, http=http, resolver=resolver, accept="text/csv, */*;q=0.1", max_bytes=MAX_FILE_BYTES, seconds=60.0
            )
        except netfetch.FetchError as exc:
            if str(exc).endswith(" 404"):
                return None
            raise
        return result.body

    return get


def quarter_of(day: date) -> str:
    return f"{day.year}-T{(day.month - 1) // 3 + 1}"


def next_quarter(day: date) -> date:
    return add_months(date(day.year, (day.month - 1) // 3 * 3 + 1, 1), 3)


# ------------------------------------------------------------------ ta description
def profile(db: Session) -> dict[str, Any]:
    row = db.get(HomeProfile, 1)
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
    kind: str,
    surface: int,
    code: str,
    commune: str | None,
    section: str | None,
    rooms: int | None,
    in_wealth: bool,
) -> dict[str, Any]:
    try:
        clean_code = dvf.normalize_code(code)
        clean_section = dvf.normalize_section(section)
    except dvf.DvfError as exc:
        raise HomeValueError(str(exc), 422) from exc
    if kind not in dvf.KINDS:
        raise HomeValueError("Type de logement : appartement ou maison", 422)
    if not dvf.MIN_SURFACE <= surface <= dvf.MAX_SURFACE:
        raise HomeValueError(f"Surface habitable : entre {dvf.MIN_SURFACE} et {dvf.MAX_SURFACE} m²", 422)
    if rooms is not None and not dvf.MIN_ROOMS <= rooms <= dvf.MAX_ROOMS:
        raise HomeValueError(f"Nombre de pièces : entre {dvf.MIN_ROOMS} et {dvf.MAX_ROOMS}", 422)
    data = {
        "kind": kind,
        "surface": surface,
        "code": clean_code,
        "commune": " ".join((commune or "").split())[:80] or None,
        "section": clean_section,
        "rooms": rooms,
        "in_wealth": in_wealth,
    }
    before = profile(db)
    row = db.get(HomeProfile, 1) or HomeProfile(id=1, data="{}")
    row.data = json.dumps(data)
    db.add(row)
    db.flush()
    # Le journal dit ce qui a changé, pas ta surface ni ta commune.
    changed = sorted(k for k in data if before.get(k) != data[k])
    audit.record(db, actor, "home.profile", changed=",".join(changed) or "-")
    recompute(db)
    return data


def remove_profile(db: Session, *, actor: str) -> None:
    for model in (HomeProfile, DvfCache):
        row = db.get(model, 1)
        if row is not None:
            db.delete(row)
    db.flush()
    audit.record(db, actor, "home.profile_removed")


# ------------------------------------------------------------------ ventes en cache
def _cached(row: DvfCache | None) -> tuple[str | None, list[dvf.Sale], list[int]]:
    if row is None or not row.data:
        return None, [], []
    try:
        data = json.loads(row.data)
        return str(data["code"]), [dvf.Sale.from_row(r) for r in data["sales"]], [int(y) for y in data["years"]]
    except (ValueError, KeyError, TypeError):
        return None, [], []


def download(code: str, today: date, fetch: Fetch) -> tuple[list[dvf.Sale], list[int]]:
    """Les fichiers annuels de la commune (les années pas encore publiées manquent : normal), réduits aux ventes
    d'un seul logement des 24 mois qui précèdent la plus récente. Une année illisible fait tout échouer : une
    estimation sur des données trouées serait fausse sans le dire."""
    dep = dvf.department(code)
    sales: list[dvf.Sale] = []
    years: list[int] = []
    for year in range(today.year - YEARS_BACK, today.year + 1):
        body = fetch(f"{BASE_URL}/{year}/communes/{dep}/{code}.csv")
        if body is None:
            continue
        sales.extend(dvf.parse_commune(body))
        years.append(year)
    if not years:
        raise dvf.DvfError(
            "Aucun fichier DVF pour cette commune : vérifie le code INSEE (DVF ne couvre ni l'Alsace-Moselle "
            "ni Mayotte)"
        )
    if sales:
        since = add_months(max(s.day for s in sales), -dvf.WINDOW_MONTHS)
        sales = sorted((s for s in sales if s.day > since), key=lambda s: s.day)
    return sales, years


def refresh(db: Session, *, today: date, now: datetime, fetch: Fetch, actor: str) -> dict[str, Any]:
    """Télécharge les ventes si le cache n'est pas celui de ta commune ou date d'un trimestre passé ; sinon ne fait
    rien (même règle pour le bouton et pour la tâche de fond). Trois essais par jour au plus."""
    consents.require(db, CONSENT)
    prof = profile(db)
    if not prof:
        raise HomeValueError("Décris d'abord ton logement", 409)
    code = str(prof["code"])
    row = db.get(DvfCache, 1)
    if row is None:
        row = DvfCache(id=1, attempts=0)
        db.add(row)
    cached_code, _sales, _years = _cached(row)
    if cached_code == code and row.quarter == quarter_of(today):
        return {
            "status": "fresh",
            "message": "Données déjà à jour ce trimestre : prochaine extraction possible le "
            f"{next_quarter(today).strftime('%d/%m/%Y')}.",
        }
    if row.attempts_day != today.isoformat():
        row.attempts_day, row.attempts = today.isoformat(), 0
    if (row.attempts or 0) >= MAX_ATTEMPTS_PER_DAY:
        return {"status": "capped", "message": f"{MAX_ATTEMPTS_PER_DAY} essais aujourd'hui : réessaie demain."}
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = now
    try:
        sales, years = download(code, today, fetch)
    except (netfetch.FetchError, dvf.DvfError) as exc:
        row.last_error = str(exc)[:300]
        db.flush()
        audit.record(db, actor, "home.dvf_refresh", ok=False)
        kept = cached_code == code and row.fetched_at is not None
        message = f"Téléchargement DVF en échec : {exc}."
        if kept and row.fetched_at is not None:
            message += f" L'estimation garde l'extraction du {row.fetched_at.strftime('%d/%m/%Y')}."
        return {"status": "failed", "message": message}
    row.data = json.dumps({"code": code, "years": years, "sales": [s.to_row() for s in sales]}, separators=(",", ":"))
    row.fetched_at, row.quarter, row.last_error = now, quarter_of(today), None
    db.flush()
    audit.record(db, actor, "home.dvf_refresh", ok=True, sales=len(sales))
    recompute(db)
    return {"status": "refreshed", "message": f"{len(sales)} ventes d'un seul logement lues pour ta commune."}


def recompute(db: Session) -> dict[str, Any] | None:
    """L'estimation est calculée une fois (à l'extraction, à chaque changement de ta description) et gardée : le
    patrimoine la lit sans relire toutes les ventes."""
    row = db.get(DvfCache, 1)
    if row is None:
        return None
    prof = profile(db)
    code, sales, _years = _cached(row)
    result: dict[str, Any] | None = None
    if prof and code == prof.get("code"):
        est = dvf.estimate(
            sales,
            kind=str(prof["kind"]),
            surface=int(prof["surface"]),
            rooms=prof.get("rooms"),
            section=prof.get("section"),
        )
        result = est.to_dict() | {"code": code}
    row.result = json.dumps(result) if result else None
    db.flush()
    return result


def _result(db: Session, prof: dict[str, Any]) -> dict[str, Any] | None:
    row = db.get(DvfCache, 1)
    if row is None or not row.result or not prof:
        return None
    try:
        result = json.loads(row.result)
    except ValueError:
        return None
    return result if isinstance(result, dict) and result.get("code") == prof.get("code") else None


# ------------------------------------------------------------------ patrimoine
def wealth_line(db: Session) -> AccountBalance | None:
    """La ligne « Logement (estimation DVF) » du patrimoine net : la médiane, si l'estimation tient et si tu veux
    la compter. Le prêt reste de son côté (restant dû en dette) : rien n'est compté deux fois."""
    prof = profile(db)
    if not prof or not prof.get("in_wealth", True):
        return None
    result = _result(db, prof)
    if not result or not result.get("ok"):
        return None
    return AccountBalance(HOME_LINE_ID, HOME_LINE, HOME_KIND, int(result["median_cents"]))


def home_loans(db: Session, today: date) -> list[dict[str, Any]]:
    """Les prêts immobiliers suivis, avec le restant dû tel que le patrimoine le compte : le solde du compte
    crédit relié s'il y en a un, sinon le tableau d'amortissement."""
    out = []
    for loan in db.scalars(select(Loan).where(Loan.kind == "immobilier").order_by(Loan.id)):
        account = db.get(Account, loan.account_id) if loan.account_id else None
        if account is not None and not account.archived:
            remaining = max(0, -account.balance_cents)
        else:
            dom = loans_domain.Loan(
                principal_cents=loan.principal_cents,
                annual_rate_bp=loan.annual_rate_bp,
                months=loan.months,
                first_payment=loan.first_payment,
                insurance_monthly_cents=loan.insurance_monthly_cents,
                kind="immobilier",
                name=loan.name,
            )
            remaining = loans_domain.position(loans_domain.schedule(dom), today).remaining_cents
        out.append({"id": loan.id, "name": loan.name, "remaining_cents": remaining})
    return out


# ------------------------------------------------------------------ recherche de commune
def search_communes(
    db: Session, query: str, *, http: Any = None, resolver: netfetch.Resolver | None = None
) -> list[dict[str, Any]]:
    """Ta commune par son nom ou son code postal (geo.api.gouv.fr, Etalab) : seul ce que tu tapes part, avec ton
    accord. Paris, Lyon et Marseille sortent par arrondissement, comme dans DVF."""
    consents.require(db, CONSENT)
    clean = " ".join(query.split())
    if not SEARCH_RE.match(clean):
        raise HomeValueError("Un nom de commune ou un code postal à 5 chiffres", 422)
    params = {"codePostal": clean} if clean.isdigit() else {"nom": clean, "boost": "population"}
    params |= {"fields": "nom,code,codesPostaux", "type": "commune-actuelle,arrondissement-municipal", "limit": "10"}
    try:
        data = netfetch.get_json(
            f"{GEO_URL}?{urlencode(params)}", http=http, resolver=resolver, max_bytes=200_000, seconds=10.0
        )
    except netfetch.FetchError as exc:
        raise HomeValueError(f"Recherche de commune indisponible : {exc}", 502) from exc
    out = []
    for item in data if isinstance(data, list) else []:
        code = str(item.get("code") or "") if isinstance(item, dict) else ""
        if not dvf.CODE_RE.match(code) or code in dvf.CITIES_BY_DISTRICT:
            continue
        postcodes = [str(p) for p in item.get("codesPostaux") or [] if isinstance(p, str)][:5]
        out.append({"code": code, "name": str(item.get("nom") or code)[:80], "postcodes": postcodes})
    return out[:10]


# ------------------------------------------------------------------ vue et tâche de fond
def view(db: Session, today: date) -> dict[str, Any]:
    prof = profile(db)
    row = db.get(DvfCache, 1)
    code, sales, years = _cached(row)
    matches = bool(prof) and code == prof.get("code")
    result = _result(db, prof)
    loans = home_loans(db, today)
    owed = sum(x["remaining_cents"] for x in loans)
    median = int(result["median_cents"]) if result and result.get("ok") else None
    return {
        "consent": consents.granted(db, CONSENT),
        "profile": prof or None,
        "estimate": result,
        "data": {
            "matches": matches,
            "fetched_at": row.fetched_at.isoformat() if row and row.fetched_at and matches else None,
            "latest_sale": max(s.day for s in sales).isoformat() if sales and matches else None,
            "sales": len(sales) if matches else 0,
            "years": years if matches else [],
            "fresh": bool(matches and row and row.quarter == quarter_of(today)),
            "next_refresh": next_quarter(today).isoformat(),
            "error": row.last_error if row else None,
        },
        "in_wealth": bool(median is not None and prof.get("in_wealth", True)),
        "loans": loans,
        "loans_remaining_cents": owed,
        "net_cents": median - owed if median is not None else None,
        "kinds": [{"key": k, "label": dvf.KIND_LABELS[k]} for k in dvf.KINDS],
        "bounds": {"surface": [dvf.MIN_SURFACE, dvf.MAX_SURFACE], "rooms": [dvf.MIN_ROOMS, dvf.MAX_ROOMS]},
        "source": dvf.SOURCE,
        "source_url": dvf.SOURCE_URL,
        "disclaimer": (
            "Repère statistique tiré des ventes publiées, pas une expertise : l'état, l'étage, la vue ou les travaux "
            "font bouger un prix bien au-delà de la fourchette. DVF paraît avec plusieurs mois de retard."
        ),
    }


def tick(db: Session, *, today: date, now: datetime, fetch: Fetch) -> dict[str, int]:
    """Tâche de fond : rafraîchit au changement de trimestre, seulement avec ton accord et un logement décrit."""
    if not consents.granted(db, CONSENT) or not profile(db):
        return {}
    row = db.get(DvfCache, 1)
    code, _sales, _years = _cached(row)
    if row is not None and code == profile(db).get("code") and row.quarter == quarter_of(today):
        return {}
    result = refresh(db, today=today, now=now, fetch=fetch, actor="system")
    return {} if result["status"] == "capped" else {f"home_value_{result['status']}": 1}
