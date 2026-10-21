"""Documents de test pour le coffre : photos de texte (avec ou sans métadonnées), PDF scannés."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

PAYSLIP = [
    "BULLETIN DE PAIE",
    "ACME SERVICES SAS - SIRET 123 456 789 00012",
    "Période du 01/09/2026 au 30/09/2026",
    "Salaire de base            151,67 h     2 450,00",
    "SALAIRE BRUT                               2 650,00",
    "Total des cotisations et contributions      -585,40",
    "NET A PAYER AVANT IMPOT SUR LE REVENU      2 064,60",
    "Impôt sur le revenu prélevé à la source  Taux 4,5 %  Montant -95,41",
    "NET PAYE EN EUROS                          1 969,19",
    "Montant net social                        2 071,15",
    "Net imposable                             2 120,30",
    "Date de paiement : 30/09/2026",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def text_image(lines: list[str], *, fmt: str = "JPEG", size: int = 34, rotate: float = 0.0, exif: bytes = b"",
               mode: str = "RGB") -> bytes:  # fmt: skip
    font = _font(size)
    img = Image.new(mode, (1700, 80 + int(size * 1.7) * len(lines)), "white" if mode == "RGB" else (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((60, 40 + int(size * 1.7) * i), line, fill="black", font=font)
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white" if mode == "RGB" else (255, 255, 255, 0))
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=88, exif=exif)
    else:
        img.save(buf, fmt)
    return buf.getvalue()


def gps_exif(orientation: int | None = None) -> bytes:
    """EXIF avec une position GPS (Paris), un appareil et, au besoin, une orientation."""
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0110] = "Phone 15"  # Model
    if orientation:
        exif[0x0112] = orientation
    gps = {1: "N", 2: (48.0, 51.0, 24.0), 3: "E", 4: (2.0, 21.0, 3.0)}
    exif[0x8825] = gps
    return exif.tobytes()


def scanned_pdf(lines: list[str]) -> bytes:
    """Un « scan » : une page PDF qui ne contient qu'une image (aucun texte)."""
    img = Image.open(io.BytesIO(text_image(lines))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150)
    return buf.getvalue()
