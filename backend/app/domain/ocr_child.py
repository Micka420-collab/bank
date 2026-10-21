"""Lance Tesseract (reconnaissance de texte) dans un processus borné : temps CPU, mémoire, aucun fichier écrit.

``python -I ocr_child.py /usr/bin/tesseract fra+eng < image.jpg`` : pose les limites puis remplace le processus par
Tesseract (``exec``), qui lit l'image sur l'entrée standard et écrit ses mots (format TSV, avec la confiance de
chaque mot) sur la sortie standard. L'image a déjà été nettoyée et réencodée par ``image_child`` : Tesseract ne
voit jamais le fichier d'origine. Environnement vide (hérité du parent), réseau inutile.
"""

from __future__ import annotations

import os
import re
import sys

CPU_SECONDS = 60
ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024
EXIT_BAD_ARGS = 2


def main() -> int:  # pragma: no cover - remplacé par Tesseract (testé de bout en bout)
    if len(sys.argv) != 3:
        return EXIT_BAD_ARGS
    binary, langs = sys.argv[1], sys.argv[2]
    if not os.path.isabs(binary) or not os.path.basename(binary).startswith("tesseract"):
        return EXIT_BAD_ARGS
    if not re.fullmatch(r"[a-z_]{3,10}(?:\+[a-z_]{3,10}){0,3}", langs):
        return EXIT_BAD_ARGS
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS + 2))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # sortie standard seulement : aucun fichier écrit
    os.execv(binary, [binary, "stdin", "stdout", "-l", langs, "--psm", "3", "tsv"])  # noqa: S606  # nosec B606
    return EXIT_BAD_ARGS


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
