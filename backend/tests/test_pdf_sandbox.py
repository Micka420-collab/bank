"""Lecture des PDF dans un processus confiné : bombes, chiffrement, délais et mémoire ne touchent pas l'API."""

import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.domain import documents, pdf_text
from app.domain.documents import MAX_CHARS, DocumentError, extract_text, parse_document
from tests.pdfgen import encrypt_pdf, make_page_tree_bomb_pdf, make_pdf, make_zlib_bomb_pdf

AVIS_LINES = ["AVIS D'IMPÔT 2026", "Impôt sur les revenus 2025", "Revenu fiscal de référence : 23 456 €"]


def test_child_process_runs_isolated_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le processus de lecture ne voit ni la clé maîtresse ni le réseau du parent : environnement vide, mode -I."""
    monkeypatch.setenv("BANK_MASTER_KEY", "secret-de-test")
    probe = Path(pdf_text.__file__).parent / "pdf_text.py"
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(probe)], input=make_pdf(AVIS_LINES), capture_output=True, env={}, timeout=30
    )
    assert proc.returncode == 0 and "23 456" in proc.stdout.decode()
    text = extract_text(make_pdf(AVIS_LINES))
    assert "23 456" in text and "secret-de-test" not in text


def test_zlib_bomb_is_refused_quickly() -> None:
    bomb = make_zlib_bomb_pdf(60_000_000)  # 60 Mo décompressés, < 100 Ko sur le disque
    started = time.perf_counter()
    with pytest.raises(DocumentError, match="illisible"):
        extract_text(bomb)
    assert time.perf_counter() - started < 10


def test_page_tree_bomb_is_bounded() -> None:
    with pytest.raises(DocumentError, match="illisible"):
        extract_text(make_page_tree_bomb_pdf(5_000))
    # Juste sous le plafond : on lit, mais seulement les premières pages.
    text = extract_text(make_page_tree_bomb_pdf(150))
    assert text.count("bombe") == pdf_text.MAX_PAGES


def test_encrypted_pdfs() -> None:
    with pytest.raises(DocumentError, match="mot de passe"):
        extract_text(encrypt_pdf(make_pdf(AVIS_LINES), "secret"))
    # Chiffré avec un mot de passe utilisateur vide (« protégé » mais lisible par tous) : accepté.
    assert "23 456" in extract_text(encrypt_pdf(make_pdf(AVIS_LINES), ""))


def test_wall_clock_timeout_kills_the_reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n")
    monkeypatch.setattr(documents, "_CHILD", slow)
    monkeypatch.setattr(documents, "WALL_SECONDS", 0.5)
    started = time.perf_counter()
    with pytest.raises(DocumentError, match="trop longue"):
        extract_text(make_pdf(AVIS_LINES))
    assert time.perf_counter() - started < 5


def test_memory_and_cpu_limits_apply_inside_the_child(tmp_path: Path) -> None:
    """Les limites sont posées par l'enfant lui-même, avant tout travail : un script qui alloue 1 Go échoue."""
    hog = tmp_path / "hog.py"
    hog.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(pdf_text.__file__).parent)!r})\n"
        "import pdf_text\n"
        "pdf_text._confine()\n"
        "try:\n"
        "    x = bytearray(1024 * 1024 * 1024)\n"
        "except MemoryError:\n"
        "    sys.exit(42)\n"
        "sys.exit(0)\n"
    )
    proc = subprocess.run([sys.executable, "-I", str(hog)], capture_output=True, env={}, timeout=30)  # noqa: S603
    assert proc.returncode == 42
    spin = tmp_path / "spin.py"
    spin.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(pdf_text.__file__).parent)!r})\n"
        "import pdf_text, resource\n"
        "pdf_text._confine()\n"
        "soft, hard = resource.getrlimit(resource.RLIMIT_CPU)\n"
        "nproc = resource.getrlimit(resource.RLIMIT_NPROC)\n"
        "print(soft, hard, nproc[0])\n"
    )
    proc = subprocess.run([sys.executable, "-I", str(spin)], capture_output=True, env={}, timeout=30)  # noqa: S603
    assert proc.stdout.decode().split() == [str(pdf_text.CPU_SECONDS), str(pdf_text.CPU_SECONDS + 2), "0"]


def test_reader_failures_are_logged_without_document_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="bank.documents")
    with pytest.raises(DocumentError, match="illisible"):
        extract_text(b"%PDF-1.4 garbage contenu secret")
    assert "secret" not in caplog.text and "lecture PDF refusée" in caplog.text


def test_parallel_readers_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    monkeypatch.setattr(documents, "_SLOTS", threading.BoundedSemaphore(1))
    documents._SLOTS.acquire()  # un lecteur déjà occupé
    try:
        monkeypatch.setattr(documents._SLOTS, "acquire", lambda timeout=None: False)
        with pytest.raises(DocumentError, match="réessaie"):
            extract_text(make_pdf(AVIS_LINES))
    finally:
        documents._SLOTS.release()


def test_text_is_parsed_beyond_the_first_500_characters() -> None:
    """Régression : le normaliseur des libellés bancaires tronquait à 500 caractères, les chiffres d'un vrai avis
    (plusieurs milliers de caractères) n'étaient jamais lus."""
    filler = [f"Ligne d'information numéro {i} sans aucun chiffre utile pour la lecture" for i in range(30)]
    d = parse_document(
        make_pdf(
            [
                "AVIS D'IMPÔT 2026",
                "Impôt sur les revenus 2025",
                *filler,
                "Numéro fiscal : 12 34 567 891 234",
                "Revenu fiscal de référence : 23 456 €",
                "Nombre de parts : 2,5",
            ]
        )
    )
    assert d.fields["rfr_cents"] == 2_345_600 and d.fields["parts"] == "2.5"
    assert "numéro fiscal" in d.discarded  # détecté même loin dans le document
    assert MAX_CHARS >= 100_000


# ------------------------------------------------------------------ logique du lecteur, exécutée ici (couverture)
def test_reader_logic_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from pypdf.errors import PyPdfError

    assert "23 456" in pdf_text.extract(make_pdf(AVIS_LINES))
    with pytest.raises(PyPdfError):  # LimitReachedError : plafond pypdf resserré
        pdf_text.extract(make_zlib_bomb_pdf(60_000_000))
    with pytest.raises(PermissionError):
        pdf_text.extract(encrypt_pdf(make_pdf(AVIS_LINES), "secret"))
    # Un /Count menteur (déclare plus de pages qu'il n'y en a) s'arrête à la vraie fin.
    liar = make_pdf(["Revenu fiscal de référence : 1 €"]).replace(b"/Count 1", b"/Count 9")
    assert pdf_text.extract(liar).count("1 €") == 1

    monkeypatch.setattr(pdf_text, "_confine", lambda: None)

    def run(data: bytes) -> tuple[int, bytes, str]:
        out, err = io.BytesIO(), io.StringIO()
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
        monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out))
        monkeypatch.setattr(sys, "stderr", err)
        code = pdf_text.main()
        sys.stdout.flush()
        return code, out.getvalue(), err.getvalue()

    code, text, err = run(make_pdf(AVIS_LINES))
    assert code == pdf_text.EXIT_OK and "23 456" in text.decode() and err == ""
    code, text, err = run(encrypt_pdf(make_pdf(AVIS_LINES), "secret"))
    assert code == pdf_text.EXIT_ENCRYPTED and text == b""
    code, text, err = run(b"%PDF-1.4 garbage")
    assert code == pdf_text.EXIT_UNREADABLE and text == b"" and err.isidentifier()  # nom de classe seulement
