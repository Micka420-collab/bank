"""Nettoyage d'une image confiée au coffre, exécuté dans un processus enfant borné (CPU, mémoire, sans réseau).

Lancé tel quel : ``python -I image_child.py < photo``. Il ne dépend que de Pillow et de la bibliothèque standard,
n'importe rien de Bank et n'hérite d'aucune variable d'environnement : une image forgée qui exploiterait une faille
d'un décodeur ne trouve ni la clé maîtresse, ni la base, ni le réseau.

Ce qu'il fait : vérifie le format (JPEG, PNG, WebP seulement), refuse les « bombes » de décompression, redresse
selon l'orientation EXIF, puis réencode en JPEG **sans aucune métadonnée** (ni position GPS, ni appareil, ni date),
réduit au besoin à 3 000 px, et produit une vignette de 360 px.

Protocole : image sur l'entrée standard ; sur la sortie standard, ``b"IMG1"`` + longueurs (4 octets gros-boutiste
chacune : image, vignette, largeur, hauteur) + image + vignette ; code de retour 0, ou 2 (illisible, format refusé,
limite atteinte) avec seulement le nom de l'erreur sur la sortie d'erreur.
"""

from __future__ import annotations

import io
import struct
import sys
import warnings

EXIT_OK = 0
EXIT_UNREADABLE = 2
MAGIC = b"IMG1"

ALLOWED_FORMATS = ("JPEG", "PNG", "WEBP")
MAX_INPUT_BYTES = 15_000_000
MAX_PIXELS = 50_000_000  # 50 Mpx : au-delà, c'est une bombe ou un scan inutilement lourd
MAX_SIDE = 3_000
THUMB_SIDE = 360
CPU_SECONDS = 20
ADDRESS_SPACE_BYTES = 1_024 * 1024 * 1024


def _confine() -> None:  # pragma: no cover - exécuté dans l'enfant seulement (testé par sous-processus)
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 2))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def clean(raw: bytes) -> tuple[bytes, bytes, int, int]:
    """Image propre (JPEG sans métadonnées), vignette, largeur, hauteur. Lève une exception si refusée."""
    from PIL import Image, ImageOps  # importé après _confine() : les limites s'appliquent aussi aux décodeurs

    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("trop lourde")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(io.BytesIO(raw)) as probe:
        if probe.format not in ALLOWED_FORMATS:
            raise ValueError("format refusé")
        probe.verify()  # structure complète lue sans décoder les pixels
    with Image.open(io.BytesIO(raw)) as img:
        img.load()
        upright = ImageOps.exif_transpose(img) or img
        if upright.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", upright.size, (255, 255, 255))
            rgba = upright.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            rgb = background
        else:
            rgb = upright.convert("RGB")
        if max(rgb.size) > MAX_SIDE:
            rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=88, optimize=True)  # aucune métadonnée transmise (ni EXIF, ni ICC, ni XMP)
        thumb = rgb.copy()
        thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        small = io.BytesIO()
        thumb.save(small, "JPEG", quality=72, optimize=True)
        return out.getvalue(), small.getvalue(), rgb.size[0], rgb.size[1]


def pack(image: bytes, thumb: bytes, width: int, height: int) -> bytes:
    return MAGIC + struct.pack(">IIII", len(image), len(thumb), width, height) + image + thumb


def unpack(payload: bytes) -> tuple[bytes, bytes, int, int]:
    if not payload.startswith(MAGIC) or len(payload) < 20:
        raise ValueError("réponse invalide")
    n_img, n_thumb, width, height = struct.unpack(">IIII", payload[4:20])
    body = payload[20:]
    if len(body) != n_img + n_thumb:
        raise ValueError("réponse tronquée")
    return body[:n_img], body[n_img:], width, height


def main() -> int:  # pragma: no cover - testé de bout en bout par sous-processus
    _confine()
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        image, thumb, width, height = clean(raw)
    except Exception as exc:
        sys.stderr.write(type(exc).__name__)
        return EXIT_UNREADABLE
    sys.stdout.buffer.write(pack(image, thumb, width, height))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
