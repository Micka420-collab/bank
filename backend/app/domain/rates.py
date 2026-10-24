"""Les barèmes que Bank applique : le fichier daté rates.json, livré avec le code et toujours le repli, recouvert des
valeurs que Bank a relevées lui-même aux sources officielles (services.baremes).

Une valeur relevée ne passe devant le fichier que si elle date au moins du même jour que la section qu'elle
remplace : un rates.json mis à jour plus tard par une nouvelle version de Bank reprend la main tout seul. Module pur :
la couche est posée par le service au démarrage et après chaque relève ; un seul processus sert l'application.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

# Chemin d'une valeur : segments séparés par « / » ; « liste:clé » désigne l'élément de la liste dont l'« id » ou le
# « kind » vaut clé. Exemples : « electricity_trv/options:base_6/yearly_subscription_cents »,
# « products:livret_a/rate_percent », « income_tax/brackets ».
Overlay = Mapping[str, tuple[Any, str]]  # chemin → (valeur, date d'effet AAAA-MM-JJ)

_lock = threading.Lock()
_overlay: dict[str, tuple[Any, str]] = {}


@cache
def _static_text() -> str:
    return resources.files("app.domain").joinpath("rates.json").read_text("utf-8")


def static() -> dict[str, Any]:
    """Le fichier livré, intact (une copie neuve à chaque appel : personne ne le modifie par mégarde)."""
    raw: dict[str, Any] = json.loads(_static_text())
    return raw


def section_of(path: str) -> str:
    """livrets | electricity | income_tax | other : la section dont la date dit si le fichier est plus récent."""
    head = path.split("/", 1)[0].split(":", 1)[0]
    if head in ("products", "as_of", "source"):
        return "livrets"
    if head == "electricity_trv":
        return "electricity"
    if head == "income_tax":
        return "income_tax"
    return "other"


def section_as_of(raw: Mapping[str, Any], section: str) -> str | None:
    """La date (AAAA-MM-JJ) à laquelle le fichier dit être à jour pour cette section."""
    if section == "livrets":
        return str(raw.get("as_of") or "") or None
    if section == "electricity":
        return str((raw.get("electricity_trv") or {}).get("as_of") or "") or None
    if section == "income_tax":
        year = (raw.get("income_tax") or {}).get("incomes_year")
        return f"{int(year):04d}-01-01" if year else None
    return None


def _parent(raw: Any, segments: list[str]) -> Any:
    node = raw
    for seg in segments:
        name, _, key = seg.partition(":")
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
        if key:
            if not isinstance(node, list):
                return None
            node = next((x for x in node if isinstance(x, dict) and key in (x.get("id"), x.get("kind"))), None)
    return node


def get(raw: Mapping[str, Any], path: str) -> Any:
    *segments, leaf = path.split("/")
    parent = _parent(raw, segments)
    return parent.get(leaf) if isinstance(parent, dict) else None


def apply(raw: Mapping[str, Any], overlay: Overlay) -> dict[str, Any]:
    """Le fichier recouvert des valeurs relevées. Une valeur dont la section du fichier est plus récente, ou dont le
    chemin n'existe plus dans le fichier (option retirée), est ignorée : le fichier reste le repli."""
    out: dict[str, Any] = copy.deepcopy(dict(raw))
    for path, (value, effective_from) in overlay.items():
        as_of = section_as_of(raw, section_of(path))
        if as_of and effective_from < as_of:
            continue
        *segments, leaf = path.split("/")
        parent = _parent(out, segments)
        if isinstance(parent, dict) and leaf in parent:
            parent[leaf] = copy.deepcopy(value)
    return out


def install(overlay: Overlay) -> None:
    global _overlay
    with _lock:
        _overlay = dict(overlay)


def installed() -> dict[str, tuple[Any, str]]:
    with _lock:
        return dict(_overlay)


def current() -> dict[str, Any]:
    """Les barèmes en vigueur pour Bank : ce que tous les lecteurs consultent à la place du fichier brut."""
    return apply(static(), installed())
