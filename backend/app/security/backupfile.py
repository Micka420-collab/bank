"""Fichier de sauvegarde chiffré (format « BANKBK1 ») : AES-256-GCM par morceaux de 1 Mio, clé dérivée de la clé
maîtresse (HKDF, sel par fichier), en-tête authentifié, dernier morceau marqué — un fichier tronqué, recollé ou
réordonné ne se déchiffre pas. Lecture et écriture en flux : une base de plusieurs centaines de Mo ne passe jamais
entière en mémoire. Se relit sans Bank : ``python -m scripts.restore`` avec la même clé maîtresse.

    en-tête : b"BANKBK1\\n" | len(kid) (1) | kid | sel (16)
    morceau : longueur du chiffré (4, big-endian) | AES-GCM(nonce = sel[:4] + n° (8), AAD = en-tête + n° + drapeau)
    drapeau : 0 = encore des morceaux, 1 = dernier morceau (il peut être vide)."""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.security.crypto import CryptoError, Keyring

MAGIC = b"BANKBK1\n"
CHUNK = 1 << 20  # 1 Mio en clair par morceau
MAX_CHUNK_CIPHER = CHUNK + 16 + 1024  # garde-fou : un morceau annoncé plus gros est un fichier malformé
SALT_LEN = 16


class BackupFileError(ValueError):
    pass


def _chunk_key(file_key: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"bank-backup-chunks").derive(file_key)


def _nonce(salt: bytes, index: int) -> bytes:
    return salt[:4] + index.to_bytes(8, "big")


def _aad(header: bytes, index: int, last: bool) -> bytes:
    return header + index.to_bytes(8, "big") + (b"\x01" if last else b"\x00")


def encrypt_stream(src: BinaryIO, dst: BinaryIO, keyring: Keyring) -> int:
    """Chiffre ``src`` vers ``dst`` ; renvoie le nombre d'octets en clair lus."""
    kid, file_key = keyring.backup_key()
    kid_bytes = kid.encode("ascii")
    if not 0 < len(kid_bytes) < 256:
        raise BackupFileError("Identifiant de clé invalide")
    salt = os.urandom(SALT_LEN)
    header = MAGIC + bytes([len(kid_bytes)]) + kid_bytes + salt
    dst.write(header)
    aead = AESGCM(_chunk_key(file_key, salt))
    total = 0
    index = 0
    pending = src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK)
        last = not nxt
        ct = aead.encrypt(_nonce(salt, index), pending, _aad(header, index, last))
        dst.write(struct.pack(">I", len(ct)))
        dst.write(ct)
        total += len(pending)
        index += 1
        if last:
            break
        pending = nxt
    return total


def _read_exact(src: BinaryIO, n: int) -> bytes:
    data = src.read(n)
    if len(data) != n:
        raise BackupFileError("Fichier de sauvegarde tronqué")
    return data


def decrypt_chunks(src: BinaryIO, keyring: Keyring) -> Iterator[bytes]:
    """Déchiffre un fichier « BANKBK1 » morceau par morceau. Lève BackupFileError si le fichier est altéré, tronqué
    (dernier morceau absent), rallongé, ou chiffré avec une clé que ce Bank ne connaît pas."""
    magic = src.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupFileError("Ce fichier n'est pas une sauvegarde Bank")
    n = _read_exact(src, 1)[0]
    if n == 0:
        raise BackupFileError("En-tête invalide")
    kid_bytes = _read_exact(src, n)
    salt = _read_exact(src, SALT_LEN)
    header = magic + bytes([n]) + kid_bytes + salt
    try:
        _, file_key = keyring.backup_key(kid_bytes.decode("ascii"))
    except (UnicodeDecodeError, CryptoError) as exc:
        raise BackupFileError("Sauvegarde chiffrée avec une clé maîtresse inconnue") from exc
    aead = AESGCM(_chunk_key(file_key, salt))
    index = 0
    while True:
        raw_len = src.read(4)
        if not raw_len:
            raise BackupFileError("Fichier de sauvegarde tronqué : dernier morceau absent")
        if len(raw_len) != 4:
            raise BackupFileError("Fichier de sauvegarde tronqué")
        (length,) = struct.unpack(">I", raw_len)
        if length < 16 or length > MAX_CHUNK_CIPHER:
            raise BackupFileError("Morceau de taille invalide")
        ct = _read_exact(src, length)
        last = False
        try:
            plain = aead.decrypt(_nonce(salt, index), ct, _aad(header, index, False))
        except InvalidTag:
            try:
                plain = aead.decrypt(_nonce(salt, index), ct, _aad(header, index, True))
                last = True
            except InvalidTag as exc:
                raise BackupFileError("Sauvegarde altérée ou mauvaise clé") from exc
        yield plain
        index += 1
        if last:
            if src.read(1):
                raise BackupFileError("Données après le dernier morceau")
            return


def decrypt_stream(src: BinaryIO, dst: BinaryIO, keyring: Keyring) -> int:
    total = 0
    for chunk in decrypt_chunks(src, keyring):
        dst.write(chunk)
        total += len(chunk)
    return total
