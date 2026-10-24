"""Lecture d'un document confié au coffre : reconnaître le format, nettoyer une image, extraire le texte d'un PDF ou
les images d'un scan, reconnaître le texte d'une image (OCR).

Chaque étape qui touche au contenu tourne dans un processus enfant confiné (``image_child``, ``pdf_text``,
``ocr_child`` : Python en mode isolé, environnement vide, limites CPU/mémoire, aucun fichier écrit, tué au-delà
d'un délai). Ce module ne fait qu'orchestrer et borner : au plus deux lectures à la fois, des erreurs claires,
jamais le contenu d'un document dans un journal.
"""

from __future__ import annotations

import functools
import logging
import shutil

# Commandes fixes (interpréteur courant, scripts de Bank) ; le contenu ne passe que par l'entrée standard.
import subprocess  # nosec B404
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from app.domain import image_child, ocr_child, pdf_text

log = logging.getLogger("bank")

MAX_PARALLEL = 2
IMAGE_SECONDS = 30.0
PDF_SECONDS = 25.0
OCR_SECONDS = 90.0
_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
_IMAGE_CHILD = Path(image_child.__file__)
_PDF_CHILD = Path(pdf_text.__file__)
_OCR_CHILD = Path(ocr_child.__file__)

PDF, JPEG, PNG, WEBP = "application/pdf", "image/jpeg", "image/png", "image/webp"
EXTENSIONS = {PDF: "pdf", JPEG: "jpg", PNG: "png", WEBP: "webp"}


class ReadingError(ValueError):
    """Document refusé ou illisible ; le message est destiné à l'utilisateur."""


@dataclass(frozen=True)
class CleanImage:
    data: bytes  # JPEG réencodé, sans métadonnées
    thumb: bytes
    width: int
    height: int


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float  # moyenne des confiances de Tesseract, 0 à 1
    words: int


def sniff(raw: bytes) -> str:
    """Format réel d'après les premiers octets (jamais d'après le nom ou le type annoncé par le navigateur)."""
    head = raw[:32]
    if head.startswith(b"%PDF-"):
        return PDF
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        raise ReadingError(
            "Photo HEIC (iPhone) : choisis « Le plus compatible » dans Réglages › Appareil photo › Formats, "
            "ou exporte-la en JPEG"
        )
    raise ReadingError("Format non pris en charge : PDF, JPEG, PNG ou WebP")


def _run(args: list[str], data: bytes, timeout: float, what: str, env: dict[str, str] | None = None) -> bytes:
    if not _SLOTS.acquire(timeout=30):
        raise ReadingError("Trop de documents en cours de lecture : réessaie dans un instant")
    try:
        # Interpréteur courant, script de Bank, arguments fixes ; environnement vide.
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [sys.executable, "-I", *args],
            input=data,
            capture_output=True,
            timeout=timeout,
            env=env or {},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        log.warning("%s interrompu après %.0f s", what, timeout)
        raise ReadingError(f"{what.capitalize()} trop long : document trop lourd ou trop complexe") from exc
    finally:
        _SLOTS.release()
    if proc.returncode == pdf_text.EXIT_ENCRYPTED and args[0] == str(_PDF_CHILD):
        raise ReadingError("PDF protégé par mot de passe : retire-le avant de le déposer")
    if proc.returncode == pdf_text.EXIT_TOO_MANY_PAGES and args[0] == str(_PDF_CHILD):
        pages = proc.stdout.decode("ascii", "replace").strip()
        count = f"{pages} pages" if pages.isdigit() else "plus de pages"
        raise ReadingError(f"Ce papier fait {count} : le partage s'arrête à {pdf_text.MAX_STAMP_PAGES}")
    if proc.returncode != 0:
        log.warning("%s refusé (code %s, %s)", what, proc.returncode, proc.stderr[-80:].decode("ascii", "replace"))
        raise ReadingError(f"{what.capitalize()} impossible : fichier illisible ou trop lourd")
    return proc.stdout


def clean_image(raw: bytes) -> CleanImage:
    out = _run([str(_IMAGE_CHILD)], raw, IMAGE_SECONDS, "nettoyage de l'image")
    try:
        data, thumb, width, height = image_child.unpack(out)
    except ValueError as exc:
        raise ReadingError("Nettoyage de l'image impossible") from exc
    return CleanImage(data, thumb, width, height)


def pdf_text_content(raw: bytes) -> str:
    """Texte d'un PDF (vide pour un scan) ; le PDF chiffré ou corrompu est refusé."""
    out = _run([str(_PDF_CHILD)], raw, PDF_SECONDS, "lecture du PDF")
    return out.decode("utf-8", "replace")[: pdf_text.MAX_CHARS]


def pdf_images(raw: bytes) -> list[bytes]:
    out = _run([str(_PDF_CHILD), "images"], raw, PDF_SECONDS, "lecture des pages scannées")
    try:
        return pdf_text.unpack_images(out)
    except ValueError as exc:
        raise ReadingError("Lecture des pages scannées impossible") from exc


def stamp_pdf(raw: bytes, text: str) -> bytes:
    """PDF filigrané (le document, ou la photo mise en page) : lu et réécrit dans l'enfant confiné."""
    out = _run([str(_PDF_CHILD), "stamp"], pdf_text.pack_stamp(text, raw), PDF_SECONDS, "filigrane du document")
    if not out.startswith(b"%PDF-"):
        raise ReadingError("Filigrane impossible")
    return out


@functools.cache
def tesseract_path() -> str | None:
    path = shutil.which("tesseract")
    return str(Path(path).resolve()) if path else None


@functools.cache
def ocr_languages() -> str | None:
    """« fra+eng » si le français est installé (les documents sont français), sinon ce qui existe ; None : pas d'OCR."""
    binary = tesseract_path()
    if binary is None:
        return None
    try:
        # Binaire résolu au démarrage, argument fixe.
        proc = subprocess.run(  # noqa: S603  # nosec B603
            [binary, "--list-langs"], capture_output=True, timeout=10, check=False, env={}
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    langs = {line.strip() for line in proc.stdout.decode("utf-8", "replace").splitlines()[1:]}
    picked = [lang for lang in ("fra", "eng") if lang in langs]
    return "+".join(picked) or None


def ocr_available() -> bool:
    return ocr_languages() is not None


def parse_tsv(tsv: str) -> OcrResult:
    """Texte reconstitué ligne par ligne (blocs séparés par une ligne vide) et confiance moyenne des mots."""
    lines: dict[tuple[int, ...], list[str]] = {}
    confidences: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        if not word:
            continue
        try:
            key = tuple(int(c) for c in cols[1:5])
            conf = float(cols[10])
        except ValueError:
            continue
        lines.setdefault(key, []).append(word)
        if conf >= 0:
            confidences.append(conf)
    out: list[str] = []
    previous: tuple[int, ...] | None = None
    for key, words in lines.items():
        if previous is not None and key[:2] != previous[:2]:
            out.append("")
        out.append(" ".join(words))
        previous = key
    confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.0
    return OcrResult("\n".join(out), max(0.0, min(1.0, confidence)), len(confidences))


def ocr(jpeg: bytes) -> OcrResult:
    """Texte d'une image déjà nettoyée. Sans Tesseract installé : erreur claire (le document reste au coffre)."""
    binary, langs = tesseract_path(), ocr_languages()
    if binary is None or langs is None:
        raise ReadingError("Lecture des photos indisponible sur ce serveur (Tesseract absent)")
    out = _run(
        [str(_OCR_CHILD), binary, langs], jpeg, OCR_SECONDS, "reconnaissance du texte", {"OMP_THREAD_LIMIT": "1"}
    )
    return parse_tsv(out.decode("utf-8", "replace"))
