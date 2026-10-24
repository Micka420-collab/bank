"""Boîte mail en lecture seule : lecture d'un message (en-têtes décodés, pièces jointes PDF bornées), reconnaissance
du genre de papier depuis l'expéditeur, l'objet et le nom de fichier, liste d'expéditeurs autorisée. Pur : aucun
réseau ici, le service IMAP lui donne des octets."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from app.domain import vault as vault_domain

MAX_ATTACHMENT_BYTES = 15_000_000
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_MESSAGE_BYTES = 25_000_000
PDF_MAGIC = b"%PDF"
# Expéditeurs institutionnels : le domaine suffit à typer le papier avec confiance.
DOMAIN_KINDS: tuple[tuple[str, str], ...] = (
    ("caf.fr", "caf"),
    ("msa.fr", "caf"),
    ("francetravail.fr", "france_travail"),
    ("pole-emploi.fr", "france_travail"),
    ("dgfip.finances.gouv.fr", "tax_notice"),
    ("impots.gouv.fr", "tax_notice"),
    ("ameli.fr", "health"),
    ("assurance-maladie.fr", "health"),
    ("info-retraite.fr", "pension"),
    ("lassuranceretraite.fr", "pension"),
    ("agirc-arrco.fr", "pension"),
)
SUBJECT_HINTS: tuple[tuple[str, str], ...] = (
    (r"\bquittance\b", "rent_receipt"),
    (r"\bbulletin de (paie|salaire)\b|\bfiche de paie\b", "payslip"),
    (r"\bavis d.imp[oô]t\b|\bavis de situation d[ée]clarative\b", "tax_notice"),
    (r"\battestation de (paiement|droits?)\b", "caf"),
    (r"\bfacture\b|\binvoice\b|\bre[cç]u de paiement\b", "invoice"),
    (r"\brelev[ée] de compte\b", "bank_statement"),
    (r"\bassurance\b|\bcontrat habitation\b|\bavis d.[ée]ch[ée]ance\b", "insurance"),
    (r"\bbail\b|\b[ée]tat des lieux\b", "lease"),
)


@dataclass(frozen=True)
class Headers:
    sender: str  # adresse seule, en minuscules
    sender_name: str
    domain: str
    subject: str
    received_at: datetime | None
    message_id: str
    content_type: str


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


def _decode(value: Any) -> str:
    if value is None:
        return ""
    try:
        text = str(make_header(decode_header(str(value))))
    except (UnicodeDecodeError, ValueError, LookupError):
        text = str(value)
    return " ".join(text.split())[:300]


def parse_headers(raw: bytes) -> Headers:
    """Les en-têtes qui comptent, décodés et bornés ; une date illisible devient None, jamais une exception."""
    msg = message_from_bytes(raw, policy=policy.compat32)
    name, addr = parseaddr(_decode(msg.get("From")))
    addr = addr.lower().strip()
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    received: datetime | None = None
    try:
        parsed = parsedate_to_datetime(str(msg.get("Date") or ""))
        received = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        received = None
    return Headers(
        sender=addr[:200],
        sender_name=_decode(name)[:120],
        domain=domain[:120],
        subject=_decode(msg.get("Subject")),
        received_at=received,
        message_id=str(msg.get("Message-ID") or "").strip()[:200],
        content_type=str(msg.get_content_type() or "").lower(),
    )


def _clean_filename(name: str | None, index: int) -> str:
    base = _decode(name) if name else ""
    base = re.sub(r"[^\w .()\-\[\]]+", "_", base, flags=re.UNICODE).strip(" .")[:120]
    if not base.lower().endswith(".pdf"):
        base = (base or f"piece-jointe-{index}") + ".pdf"
    return base


def attachments(raw: bytes, *, max_bytes: int = MAX_ATTACHMENT_BYTES) -> tuple[list[Attachment], list[str]]:
    """Les PDF joints (type application/pdf ou nom en .pdf, et qui commencent bien par %PDF), bornés en nombre et en
    taille ; ce qui est écarté est dit (trop lourd, pas un PDF)."""
    if len(raw) > MAX_MESSAGE_BYTES:
        return [], ["Message trop lourd"]
    msg = message_from_bytes(raw, policy=policy.default)
    out: list[Attachment] = []
    skipped: list[str] = []
    index = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or "").lower()
        name = part.get_filename()
        looks_pdf = ctype == "application/pdf" or (name or "").lower().endswith(".pdf")
        if not looks_pdf and ctype != "application/octet-stream":
            continue
        index += 1
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload.startswith(PDF_MAGIC):
            if looks_pdf:
                skipped.append(f"{_clean_filename(name, index)} : pas un PDF")
            continue
        if len(payload) > max_bytes:
            skipped.append(f"{_clean_filename(name, index)} : trop lourd ({len(payload) // 1_000_000} Mo)")
            continue
        out.append(Attachment(_clean_filename(name, index), payload, hashlib.sha256(payload).hexdigest()))
        if len(out) >= MAX_ATTACHMENTS_PER_MESSAGE:
            skipped.append("Plus de cinq pièces jointes : les suivantes sont ignorées")
            break
    return out, skipped


def guess_kind(headers: Headers, filename: str) -> tuple[str, float]:
    """Le genre de papier probable : domaine institutionnel d'abord, puis l'objet et le nom de fichier, puis les
    signaux du coffre. En dessous du seuil : « other »."""
    for domain, kind in DOMAIN_KINDS:
        if headers.domain == domain or headers.domain.endswith("." + domain):
            return kind, 0.9
    folded = f"{headers.subject} {filename}".lower()
    for pattern, kind in SUBJECT_HINTS:
        if re.search(pattern, folded):
            return kind, 0.7
    kind, confidence = vault_domain.classify(f"{headers.subject}\n{filename}\n{headers.sender_name}")
    return kind, min(confidence, 0.6)


def sender_allowed(headers: Headers, allowlist: tuple[str, ...]) -> bool:
    """Liste vide : tout le monde. Sinon : adresse exacte ou domaine (« caf.fr » accepte ne-pas-repondre@caf.fr)."""
    if not allowlist:
        return True
    for entry in allowlist:
        e = entry.strip().lower().lstrip("@")
        if not e:
            continue
        if "@" in e and headers.sender == e:
            return True
        if "@" not in e and (headers.domain == e or headers.domain.endswith("." + e)):
            return True
    return False


def scrub(text: str, *secrets: str) -> str:
    """Un message d'erreur sans identifiant ni mot de passe, borné."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "•••")
    out = re.sub(r"(?i)(password|pass|pwd|mot de passe)\s*[:=]\s*\S+", r"\1=•••", out)
    return " ".join(out.split())[:300]


def valid_folder(name: str) -> str:
    folder = name.strip()
    if not folder or len(folder) > 120 or any(c in folder for c in '"\r\n\\'):
        raise ValueError("Nom de dossier invalide")
    return folder


_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(\.{_LABEL})*")


def valid_host(host: str) -> str:
    h = host.strip().lower().rstrip(".")
    if not h or len(h) > 253 or not _HOST.fullmatch(h):
        raise ValueError("Serveur IMAP invalide : un nom d'hôte, sans schéma ni chemin")
    return h
