"""Génère un PDF minimal (texte Helvetica, non compressé) pour tester la lecture des documents officiels."""

from __future__ import annotations


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(lines: list[str], *, pages: int = 1) -> bytes:
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    pages_id_placeholder = len(objects) + 1 + 2 * pages  # calculé après les pages et leurs contenus
    for _ in range(pages):
        body = b"BT /F1 11 Tf 40 800 Td 14 TL\n" + b"".join(b"(" + _escape(ln) + b") Tj T*\n" for ln in lines) + b"ET"
        content = add(b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent "
            + str(pages_id_placeholder).encode()
            + b" 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
            + str(font).encode()
            + b" 0 R >> >> /Contents "
            + str(content).encode()
            + b" 0 R >>"
        )
        page_ids.append(page)
    kids = b" ".join(str(p).encode() + b" 0 R" for p in page_ids)
    pages_id = add(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>")
    assert pages_id == pages_id_placeholder
    catalog = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>")

    return _assemble(objects, catalog)


def _assemble(objects: list[bytes], root: int) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root "
        + str(root).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def make_zlib_bomb_pdf(decoded_size: int) -> bytes:
    """Une page dont le flux de contenu, compressé (quelques Ko), se décompresse en `decoded_size` octets."""
    import zlib

    body = zlib.compress(b" " * decoded_size + b"BT /F1 11 Tf (x) Tj ET", 9)
    objects: list[bytes] = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(body)).encode() + b" /Filter /FlateDecode >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Page /Parent 4 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 1 0 R >> >> /Contents 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Catalog /Pages 4 0 R >>",
    ]
    return _assemble(objects, 5)


def make_page_tree_bomb_pdf(pages: int) -> bytes:
    """Un arbre de pages qui déclare `pages` pages (toutes la même) : l'aplatissement coûte autant d'entrées."""
    objects: list[bytes] = [
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length 30 >>\nstream\nBT /F1 11 Tf (bombe) Tj ET\nendstream",
        b"<< /Type /Page /Parent 4 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 1 0 R >> >> /Contents 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join([b"3 0 R"] * pages) + b"] /Count " + str(pages).encode() + b" >>",
        b"<< /Type /Catalog /Pages 4 0 R >>",
    ]
    return _assemble(objects, 5)


def encrypt_pdf(pdf: bytes, user_password: str) -> bytes:
    """Chiffre un PDF avec pypdf (mot de passe utilisateur donné, éventuellement vide)."""
    import io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    writer.encrypt(user_password=user_password, owner_password="owner-secret", algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
