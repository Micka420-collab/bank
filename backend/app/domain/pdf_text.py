"""Extraction du texte d'un PDF, exécutée dans un processus enfant borné (CPU, mémoire, temps, sans réseau).

Ce fichier est lancé tel quel : ``python -I pdf_text.py < document.pdf``. Il ne dépend que de pypdf et de la
bibliothèque standard, n'importe rien de Bank et n'hérite d'aucune variable d'environnement : un PDF forgé qui
exploiterait une faille du lecteur ne trouve ni la clé maîtresse, ni la base, ni le réseau, et il est tué
au-delà des limites ci-dessous. Protocole : PDF sur l'entrée standard, texte UTF-8 sur la sortie standard,
code de retour 0 (texte), 3 (PDF chiffré), 2 (illisible ou limite atteinte) ; en cas d'erreur, seul le nom
de la classe d'exception est écrit sur la sortie d'erreur (jamais le contenu du document).

Mode ``images`` (``python -I pdf_text.py images``) : pour un PDF scanné (sans texte), la plus grande image de
chacune des 5 premières pages, à reconnaître ensuite par OCR ; sortie ``b"PIM1"`` + nombre d'images + pour chacune
sa longueur (4 octets gros-boutiste) et ses octets.

Mode ``stamp`` (``python -I pdf_text.py stamp``) : filigrane d'un papier partagé. Entrée ``b"STP1"`` + longueur du
texte (4 octets gros-boutiste) + texte UTF-8 + document (PDF, ou image JPEG/PNG/WebP mise en page d'abord) ; sortie :
le PDF filigrané (texte en diagonale sur chaque page, rappel en pied de page).
"""

from __future__ import annotations

import io
import struct
import sys
from typing import Any

EXIT_OK = 0
EXIT_UNREADABLE = 2
EXIT_ENCRYPTED = 3

MAX_PAGES = 20
MAX_CHARS = 200_000
CPU_SECONDS = 10
ADDRESS_SPACE_BYTES = 200 * 1024 * 1024
IMAGES_MAGIC = b"PIM1"
MAX_IMAGE_PAGES = 5
MAX_IMAGES_BYTES = 40_000_000
IMAGES_ADDRESS_SPACE_BYTES = 768 * 1024 * 1024  # une page scannée décodée pèse ~30 Mo : plus de marge qu'en texte
STAMP_MAGIC = b"STP1"
MAX_STAMP_PAGES = 60
MAX_STAMP_CHARS = 200

# Plafonds pypdf (les valeurs par défaut visent des documents de 75 Mo ; un avis d'impôt pèse quelques centaines
# de Ko). Toute limite dépassée lève LimitReachedError -> « illisible ».
PYPDF_LIMITS: dict[str, Any] = {
    "maximum_declared_stream_length": 6_000_000,
    "array_based_stream_maximum_output_length": 8_000_000,
    "array_maximum_element_count": 20_000,
    "dictionary_maximum_element_count": 20_000,
    "jbig2_maximum_output_length": 1_000_000,
    "lzw_maximum_output_length": 8_000_000,
    "run_length_maximum_output_length": 8_000_000,
    "zlib_maximum_output_length": 8_000_000,
    "zlib_maximum_recovery_input_length": 1_000_000,
    "brotli_maximum_output_length": 8_000_000,
    "flate_maximum_columns": 4_096,
    "flate_maximum_row_length": 100_000,
    "image_maximum_buffer_size": 1_000_000,
    "stream_filters_maximum_length": 4,
    "stream_decoding_work_maximum_length": 64_000_000,
    "stream_parsing_work_maximum_count": 500_000,
    "xmp_maximum_input_length": 100_000,
    "xmp_maximum_element_count": 1_000,
    "outline_maximum_entries": 100,
    "outline_maximum_depth": 5,
    "page_tree_maximum_entries": 200,
    "page_tree_maximum_depth": 16,
    "form_fields_maximum_entries": 100,
    "form_fields_maximum_depth": 5,
    "xform_maximum_invocations_per_extraction": 500,
    "jbig2dec_binary": None,  # jamais de binaire externe
}


# Pour extraire les images d'un scan, les plafonds de décompression doivent laisser passer une page entière.
IMAGE_LIMITS: dict[str, Any] = {
    **PYPDF_LIMITS,
    "maximum_declared_stream_length": 40_000_000,
    "array_based_stream_maximum_output_length": 64_000_000,
    "lzw_maximum_output_length": 64_000_000,
    "run_length_maximum_output_length": 64_000_000,
    "zlib_maximum_output_length": 64_000_000,
    "brotli_maximum_output_length": 64_000_000,
    "image_maximum_buffer_size": 64_000_000,
    "flate_maximum_row_length": 1_000_000,
    "flate_maximum_columns": 20_000,
}


def _confine(address_space: int = ADDRESS_SPACE_BYTES) -> None:  # pragma: no cover - exécuté dans l'enfant seulement
    """Limites du processus : temps CPU, espace d'adressage, aucun sous-processus, pas de fichier écrit."""
    import resource  # Linux seulement : importé ici pour que le module reste lisible ailleurs

    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 2))
    resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def extract(pdf_bytes: bytes) -> str:
    """Texte des MAX_PAGES premières pages, borné à MAX_CHARS. Lève les exceptions pypdf telles quelles."""
    import pypdf  # importé après _confine() : les limites s'appliquent aussi à l'import

    with pypdf.apply_configuration(pypdf.get_configuration().with_overwrites(**PYPDF_LIMITS)):
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:  # mot de passe utilisateur vide (fréquent : « protégé » mais lisible) sinon refus
            try:
                opened = reader.decrypt("")
            except Exception as exc:  # pypdf lève plusieurs classes selon l'algorithme
                raise PermissionError("PDF chiffré") from exc
            if not opened:
                raise PermissionError("PDF chiffré")
        parts: list[str] = []
        total = 0
        for index in range(min(len(reader.pages), MAX_PAGES)):  # /Count est déclaré : on s'arrête à la vraie fin
            try:
                text = reader.pages[index].extract_text() or ""
            except IndexError:
                break
            parts.append(text)
            total += len(text)
            if total > MAX_CHARS:
                break
    return "\n".join(parts)[:MAX_CHARS]


def _open(pdf_bytes: bytes) -> Any:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:
            raise PermissionError("PDF chiffré") from exc
        if not opened:
            raise PermissionError("PDF chiffré")
    return reader


def page_images(pdf_bytes: bytes) -> list[bytes]:
    """La plus grande image de chacune des premières pages : ce qu'un scanner ou un téléphone a photographié."""
    import pypdf
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 50_000_000
    out: list[bytes] = []
    total = 0
    with pypdf.apply_configuration(pypdf.get_configuration().with_overwrites(**IMAGE_LIMITS)):
        reader = _open(pdf_bytes)
        for index in range(min(len(reader.pages), MAX_IMAGE_PAGES)):
            try:
                page = reader.pages[index]
            except IndexError:
                break
            best = b""
            for image in page.images:
                data = image.data
                if len(data) > len(best):
                    best = data
            if not best:
                continue
            total += len(best)
            if total > MAX_IMAGES_BYTES:
                break
            out.append(best)
    return out


def pack_images(images: list[bytes]) -> bytes:
    return IMAGES_MAGIC + struct.pack(">I", len(images)) + b"".join(struct.pack(">I", len(i)) + i for i in images)


def unpack_images(payload: bytes) -> list[bytes]:
    if not payload.startswith(IMAGES_MAGIC) or len(payload) < 8:
        raise ValueError("réponse invalide")
    (count,) = struct.unpack(">I", payload[4:8])
    pos, out = 8, []
    for _ in range(min(count, MAX_IMAGE_PAGES)):
        (size,) = struct.unpack(">I", payload[pos : pos + 4])
        pos += 4
        out.append(payload[pos : pos + size])
        pos += size
    if pos != len(payload):
        raise ValueError("réponse tronquée")
    return out


def pack_stamp(text: str, document: bytes) -> bytes:
    raw = text.encode("utf-8")
    return STAMP_MAGIC + struct.pack(">I", len(raw)) + raw + document


def unpack_stamp(payload: bytes) -> tuple[str, bytes]:
    if not payload.startswith(STAMP_MAGIC) or len(payload) < 8:
        raise ValueError("demande invalide")
    (size,) = struct.unpack(">I", payload[4:8])
    if size > MAX_STAMP_CHARS * 4:
        raise ValueError("texte trop long")
    return payload[8 : 8 + size].decode("utf-8")[:MAX_STAMP_CHARS], payload[8 + size :]


def _image_to_pdf(raw: bytes) -> bytes:
    """Une photo devient une page PDF (le filigrane s'y pose comme sur un PDF) ; aucune métadonnée reprise."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 50_000_000
    with Image.open(io.BytesIO(raw)) as img:
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("format refusé")
        rgb = img.convert("RGB")
    out = io.BytesIO()
    rgb.save(out, "PDF", resolution=150.0)
    return out.getvalue()


def _hex(text: str) -> bytes:
    # Chaîne hexadécimale : aucune séquence d'échappement à gérer, quel que soit le nom saisi.
    return b"<" + text.encode("cp1252", errors="replace").hex().encode() + b">"


def _overlay(width: float, height: float, x0: float, y0: float, text: str) -> Any:
    import math

    from pypdf import PageObject
    from pypdf.generic import ContentStream, DictionaryObject, FloatObject, NameObject

    diagonal = math.hypot(width, height)
    size = max(8.0, min(34.0, diagonal * 0.8 / (0.52 * max(len(text), 1))))  # largeur moyenne d'Helvetica ~0,52 em
    cos, sin = width / diagonal, height / diagonal
    half = 0.26 * size * len(text)
    tx, ty = x0 + width / 2 - cos * half, y0 + height / 2 - sin * half
    hexed = _hex(text)
    matrix = f"{cos:.4f} {sin:.4f} {-sin:.4f} {cos:.4f} {tx:.2f} {ty:.2f}"
    ops = f"q /BkG gs 0.75 0.1 0.1 rg BT /BkF {size:.2f} Tf {matrix} Tm ".encode()
    ops += hexed + b" Tj ET Q\n"
    ops += f"q 0.25 g BT /BkF 8 Tf 1 0 0 1 {x0 + 18:.2f} {y0 + 12:.2f} Tm ".encode() + hexed + b" Tj ET Q\n"
    page = PageObject.create_blank_page(width=width, height=height)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    state = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"), NameObject("/ca"): FloatObject(0.35)})
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/BkF"): font}),
            NameObject("/ExtGState"): DictionaryObject({NameObject("/BkG"): state}),
        }
    )
    stream = ContentStream(None, None)
    stream.set_data(ops)
    page.replace_contents(stream)
    return page


def stamp(document: bytes, text: str) -> bytes:
    """Le document en PDF, chaque page marquée du texte : en diagonale, translucide, et en pied de page."""
    import pypdf

    pdf = document if document.startswith(b"%PDF-") else _image_to_pdf(document)
    with pypdf.apply_configuration(pypdf.get_configuration().with_overwrites(**IMAGE_LIMITS)):
        reader = _open(pdf)
        if len(reader.pages) > MAX_STAMP_PAGES:
            raise ValueError("trop de pages")
        writer = pypdf.PdfWriter()
        writer.append(reader, import_outline=False)
        for page in writer.pages:
            page.transfer_rotation_to_content()  # page tournée : le filigrane suit ce que l'on voit
            box = page.mediabox
            page.merge_page(_overlay(float(box.width), float(box.height), float(box.left), float(box.bottom), text))
        writer.add_metadata({"/Producer": "Bank"})
        out = io.BytesIO()
        writer.write(out)
    return out.getvalue()


def main() -> int:
    mode = sys.argv[1:2]
    images_mode = mode == ["images"]
    if images_mode or mode == ["stamp"]:
        _confine(IMAGES_ADDRESS_SPACE_BYTES)
    else:
        _confine()
    data = sys.stdin.buffer.read()
    if mode == ["stamp"]:
        try:
            text, document = unpack_stamp(data)
            payload = stamp(document, text)
        except PermissionError:
            return EXIT_ENCRYPTED
        except Exception as exc:
            sys.stderr.write(type(exc).__name__)
            return EXIT_UNREADABLE
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return EXIT_OK
    if images_mode:
        try:
            payload = pack_images(page_images(data))
        except PermissionError:
            return EXIT_ENCRYPTED
        except Exception as exc:
            sys.stderr.write(type(exc).__name__)
            return EXIT_UNREADABLE
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return EXIT_OK
    try:
        text = extract(data)
    except PermissionError:
        return EXIT_ENCRYPTED
    except Exception as exc:  # LimitReachedError, PdfReadError, MemoryError, RecursionError… -> illisible
        sys.stderr.write(type(exc).__name__)
        return EXIT_UNREADABLE
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    sys.stdout.buffer.flush()
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - point d'entrée de l'enfant
    sys.exit(main())
