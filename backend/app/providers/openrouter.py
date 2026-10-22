"""Client OpenRouter (https://openrouter.ai) : un seul point d'entrée vers des centaines de modèles de langage,
avec ta propre clé et tes propres crédits.

Ce que Bank lui envoie est décidé ailleurs (services/assistant.py : dossier anonymisé, plafonds). Ici, seulement
du transport, traité avec la même méfiance que n'importe quel service extérieur : adresse fixe en HTTPS, aucune
redirection suivie, délai total borné, corps borné (même décompressé), erreurs traduites en français, et la clé
n'apparaît jamais dans une exception ni dans un log.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

OnDelta = Callable[[str], None]

BASE_URL = "https://openrouter.ai/api/v1"
USER_AGENT = "Bank/0.4 (self-hosted personal finance)"  # ASCII only
MAX_BODY_BYTES = 4_000_000  # la liste des modèles pèse ~2 Mo ; une réponse de chat, quelques Ko
MAX_SECONDS_PER_CALL = 90.0  # délai total d'une réponse, lecture comprise
KEY_RE = re.compile(r"^sk-or-[A-Za-z0-9_\-]{16,}$")  # format publié par OpenRouter (sk-or-v1-…)
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,60}/[a-z0-9][a-z0-9._:\-]{0,80}$")


class OpenRouterError(Exception):
    def __init__(
        self, message: str, status: int | None = None, *, permanent: bool = False, tools_unsupported: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.permanent = permanent  # clé refusée, modèle inconnu : inutile de réessayer tel quel
        self.tools_unsupported = tools_unsupported  # le modèle ne sait pas appeler d'outils : réessayer sans


def looks_like_key(key: str) -> bool:
    return bool(KEY_RE.match(key.strip()))


def valid_model_id(model: str) -> bool:
    return bool(MODEL_ID_RE.match(model))


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON tel que produit par le modèle : à valider avant tout usage


@dataclass(frozen=True)
class ChatResult:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float  # tel que facturé par OpenRouter (usage.cost), 0 pour un modèle gratuit
    finish_reason: str
    tool_calls: tuple[ToolCall, ...] = ()

    def assistant_message(self) -> dict[str, Any]:
        """Le message de l'assistant à renvoyer au modèle avant les résultats d'outils (format OpenAI)."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in self.tool_calls
            ]
        return msg


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context_length: int
    prompt_usd_per_million: float
    completion_usd_per_million: float
    free: bool


class OpenRouterClient:
    def __init__(self, api_key: str | None, http: httpx.Client | None = None, base_url: str = BASE_URL) -> None:
        self._key = (api_key or "").strip()
        self._http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False)
        self._base = base_url.rstrip("/")

    # ------------------------------------------------------------------ transport
    def _headers(self, *, auth: bool) -> dict[str, str]:
        h = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # pas de « bombe » gzip ; la lecture bornée compte les octets réels
        }
        if auth:
            h["Authorization"] = f"Bearer {self._key}"
            # Attribution facultative, affichée dans le tableau de bord OpenRouter ; rien d'identifiant.
            h["X-Title"] = "Bank (self-hosted)"  # ASCII only : un en-tête HTTP
        return h

    def _read_bounded(self, resp: httpx.Response, started: float) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise OpenRouterError("Réponse trop volumineuse", resp.status_code, permanent=True)
            if time.monotonic() - started > MAX_SECONDS_PER_CALL:
                raise OpenRouterError("OpenRouter trop lent (délai dépassé)", resp.status_code)
            chunks.append(chunk)
        return b"".join(chunks)

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        body: dict[str, Any] | None = None,
        on_delta: OnDelta | None = None,
    ) -> Any:
        """Un appel ; avec ``on_delta``, la réponse est lue en flux SSE et chaque morceau de texte est transmis au
        fur et à mesure — le résultat rendu est alors le même objet qu'en lecture d'un bloc."""
        started = time.monotonic()
        try:
            headers = self._headers(auth=auth)
            if on_delta is not None:
                headers["Accept"] = "text/event-stream"
            with self._http.stream(
                method,
                f"{self._base}{path}",
                headers=headers,
                content=json.dumps(body).encode("utf-8") if body is not None else None,
            ) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    raise OpenRouterError("Redirection refusée", resp.status_code, permanent=True)
                if resp.status_code == 401:
                    raise OpenRouterError(
                        "Clé OpenRouter refusée (401) : vérifie-la ou crée-en une nouvelle", 401, permanent=True
                    )
                if resp.status_code == 402:
                    raise OpenRouterError(
                        "Crédits OpenRouter insuffisants (402) : recharge ton compte", 402, permanent=True
                    )
                if resp.status_code == 403:
                    raise OpenRouterError(
                        "Accès refusé par OpenRouter (403) : modèle ou clé non autorisés", 403, permanent=True
                    )
                if resp.status_code in (400, 404):
                    detail = _error_detail(resp)
                    if "tool" in detail and ("support" in detail or "no endpoints" in detail):
                        raise OpenRouterError(
                            "Ce modèle ne sait pas utiliser d'outils",
                            resp.status_code,
                            permanent=True,
                            tools_unsupported=True,
                        )
                    if resp.status_code == 404:
                        raise OpenRouterError(
                            "Modèle inconnu chez OpenRouter (404) : choisis-en un autre", 404, permanent=True
                        )
                    raise OpenRouterError("Requête refusée par OpenRouter (400)", 400, permanent=True)
                if resp.status_code == 429:
                    raise OpenRouterError("Trop de requêtes vers OpenRouter (429) : réessaie dans une minute", 429)
                if resp.status_code >= 500:
                    raise OpenRouterError(f"OpenRouter indisponible ({resp.status_code})", resp.status_code)
                if resp.status_code != 200:
                    raise OpenRouterError(f"Réponse inattendue ({resp.status_code})", resp.status_code)
                if on_delta is not None and "text/event-stream" in resp.headers.get("content-type", ""):
                    return _assemble_stream(self._sse_events(resp, started), on_delta)
                raw = self._read_bounded(resp, started)
        except httpx.HTTPError as exc:
            # Le message d'httpx peut contenir l'URL : jamais la clé (elle voyage dans un en-tête).
            raise OpenRouterError("OpenRouter injoignable (réseau)") from exc
        try:
            return json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise OpenRouterError("Réponse non JSON", 200, permanent=True) from exc

    def _sse_events(self, resp: httpx.Response, started: float) -> Iterator[Any]:
        """Les objets JSON d'un flux SSE (``data: {...}``), lus ligne à ligne, bornés en taille et en durée ; les
        commentaires (``: OPENROUTER PROCESSING``) et ``[DONE]`` sont ignorés."""
        size = 0
        buffer = b""
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise OpenRouterError("Réponse trop volumineuse", resp.status_code, permanent=True)
            if time.monotonic() - started > MAX_SECONDS_PER_CALL:
                raise OpenRouterError("OpenRouter trop lent (délai dépassé)", resp.status_code)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.strip().decode("utf-8", "replace")
                if not text.startswith("data:"):
                    continue  # commentaire de maintien, ligne vide, champ event
                data = text[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except (ValueError, RecursionError):
                    continue

    # ------------------------------------------------------------------ modèles
    def list_models(self) -> list[ModelInfo]:
        """Modèles texte → texte, avec leur prix (USD par million de jetons). Pas besoin de clé."""
        payload = self._request("GET", "/models", auth=False)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise OpenRouterError("Liste des modèles illisible", 200, permanent=True)
        out: list[ModelInfo] = []
        for row in rows:
            info = _model_info(row)
            if info is not None:
                out.append(info)
        out.sort(key=lambda m: (not m.free, m.prompt_usd_per_million + m.completion_usd_per_million, m.id))
        return out

    # ------------------------------------------------------------------ chat
    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.3,
        tools: list[dict[str, Any]] | None = None,
        on_delta: OnDelta | None = None,
    ) -> ChatResult:
        """Une réponse du modèle. Avec ``on_delta``, elle est demandée en flux : chaque morceau de texte est
        transmis dès qu'il arrive, et le résultat final est identique (texte, appels d'outils, usage, coût)."""
        if not self._key:
            raise OpenRouterError("Aucune clé OpenRouter enregistrée", permanent=True)
        if not valid_model_id(model):
            raise OpenRouterError("Identifiant de modèle invalide", permanent=True)
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "usage": {"include": True},  # coût réel renvoyé avec la réponse
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if on_delta is not None:
            body["stream"] = True
        payload = self._request("POST", "/chat/completions", auth=True, body=body, on_delta=on_delta)
        return _parse_chat(payload, model)


def _assemble_stream(events: Iterator[Any], on_delta: OnDelta) -> dict[str, Any]:
    """Recompose, à partir des morceaux d'un flux, la même charge utile qu'une réponse en un bloc : texte,
    appels d'outils (fragments d'arguments concaténés par index), raison d'arrêt, usage, modèle, ou erreur."""
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish: Any = None
    usage: Any = None
    model: Any = None
    error: Any = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if isinstance(ev.get("error"), dict):
            error = ev["error"]
        if ev.get("model"):
            model = ev["model"]
        if isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        choices = ev.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        if choice.get("finish_reason"):
            finish = choice["finish_reason"]
        delta = _dict(choice.get("delta"))
        piece = delta.get("content")
        if isinstance(piece, list):
            piece = "".join(str(p.get("text", "")) for p in piece if isinstance(p, dict))
        if isinstance(piece, str) and piece:
            text.append(piece)
            on_delta(piece)
        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, list):
            for c in raw_calls:
                if not isinstance(c, dict):
                    continue
                idx = c.get("index")
                key = idx if isinstance(idx, int) else len(calls)
                slot = calls.setdefault(key, {"id": "", "function": {"name": "", "arguments": ""}})
                if c.get("id"):
                    slot["id"] = str(c["id"])
                fn = _dict(c.get("function"))
                if fn.get("name"):
                    slot["function"]["name"] = str(fn["name"])
                args = fn.get("arguments")
                if isinstance(args, str):
                    slot["function"]["arguments"] += args
    if error is not None:
        return {"error": error}
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = [calls[k] for k in sorted(calls)]
    return {
        "model": model,
        "usage": usage or {},
        "choices": [{"message": message, "finish_reason": finish or ("tool_calls" if calls else "stop")}],
    }


# ---------------------------------------------------------------------------- lecture tolérante
def _error_detail(resp: httpx.Response) -> str:
    """Message d'erreur d'OpenRouter, lu sur 4 Ko au plus, en minuscules (pour reconnaître un cas, pas l'afficher)."""
    raw = b""
    for chunk in resp.iter_bytes():
        raw += chunk
        if len(raw) > 4_096:
            break
    try:
        payload = json.loads(raw[:4_096])
    except (ValueError, RecursionError):
        return raw[:500].decode("utf-8", "replace").lower()
    err = payload.get("error") if isinstance(payload, dict) else None
    message = err.get("message") if isinstance(err, dict) else None
    return str(message or "").lower()[:500]


def _price_per_million(value: Any) -> float | None:
    try:
        per_token = float(value)
    except (TypeError, ValueError):
        return None
    if per_token < 0 or per_token != per_token:  # NaN
        return None
    return round(per_token * 1_000_000, 4)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _model_info(row: Any) -> ModelInfo | None:
    if not isinstance(row, dict):
        return None
    mid = row.get("id")
    if not isinstance(mid, str) or not valid_model_id(mid):
        return None
    arch = _dict(row.get("architecture"))
    inputs = arch.get("input_modalities") or []
    outputs = arch.get("output_modalities") or []
    if "text" not in inputs or "text" not in outputs:
        return None
    pricing = _dict(row.get("pricing"))
    prompt = _price_per_million(pricing.get("prompt"))
    completion = _price_per_million(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    raw_name = row.get("name")
    name: str = raw_name if isinstance(raw_name, str) else mid
    ctx = row.get("context_length")
    return ModelInfo(
        id=mid,
        name=name[:120],
        context_length=int(ctx) if isinstance(ctx, int | float) and ctx > 0 else 0,
        prompt_usd_per_million=prompt,
        completion_usd_per_million=completion,
        free=prompt == 0 and completion == 0,
    )


def _parse_chat(payload: Any, model: str) -> ChatResult:
    if not isinstance(payload, dict):
        raise OpenRouterError("Réponse illisible", 200, permanent=True)
    err = payload.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "erreur OpenRouter")[:200]
        raise OpenRouterError(f"OpenRouter : {msg}", 200)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise OpenRouterError("Réponse sans contenu", 200)
    message = _dict(choices[0].get("message"))
    content = message.get("content")
    if isinstance(content, list):  # certains modèles renvoient des « parts »
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    if not isinstance(content, str):
        content = ""
    usage = _dict(payload.get("usage"))

    def as_int(v: Any) -> int:
        return int(v) if isinstance(v, int | float) and v >= 0 else 0

    cost = usage.get("cost")
    try:
        cost_usd = max(0.0, float(cost)) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost_usd = 0.0
    raw_model = payload.get("model")
    used: str = raw_model if isinstance(raw_model, str) else model
    finish = choices[0].get("finish_reason")
    calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for i, c in enumerate(raw_calls[:8]):
            fn = _dict(c.get("function")) if isinstance(c, dict) else {}
            name = fn.get("name")
            args = fn.get("arguments")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,40}", name):
                continue
            cid = c.get("id") if isinstance(c, dict) else None
            calls.append(
                ToolCall(
                    id=str(cid)[:80] if cid else f"call_{i}",
                    name=name,
                    arguments=args[:20_000] if isinstance(args, str) else json.dumps(args or {})[:20_000],
                )
            )
    return ChatResult(
        text=content.strip(),
        model=used[:120],
        prompt_tokens=as_int(usage.get("prompt_tokens")),
        completion_tokens=as_int(usage.get("completion_tokens")),
        cost_usd=cost_usd,
        finish_reason=str(finish or "")[:20],
        tool_calls=tuple(calls),
    )
