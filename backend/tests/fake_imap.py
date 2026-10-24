"""Un faux serveur IMAP4rev1 minimal (fil + socket, en clair sur 127.0.0.1) pour les tests de la boîte mail de Bank :
LOGIN, EXAMINE/SELECT, UID SEARCH, UID FETCH (BODYSTRUCTURE, BODY.PEEK[HEADER.FIELDS (...)], BODY.PEEK[n]), LOGOUT.
Il enregistre chaque commande reçue : un test vérifie qu'aucun STORE, EXPUNGE, COPY ni SELECT en écriture n'a eu lieu.
Les messages sont des `email.message.EmailMessage` ; les UID commencent à 1 dans l'ordre d'ajout."""

from __future__ import annotations

import contextlib
import email
import re
import socket
import threading
from email.message import EmailMessage
from typing import Any


class FakeImapServer:
    def __init__(self, *, user: str = "micka@example.org", password: str = "app-password-1234") -> None:  # noqa: S107
        self.user, self.password = user, password
        self.messages: dict[int, EmailMessage] = {}
        self.commands: list[str] = []
        self.logins: list[tuple[str, str]] = []
        self.folders = {"INBOX"}
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ contenu
    def add(self, msg: EmailMessage) -> int:
        uid = max(self.messages, default=0) + 1
        self.messages[uid] = msg
        return uid

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()

    # ------------------------------------------------------------ service
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        f = conn.makefile("rwb")
        try:
            f.write(b"* OK fake IMAP4rev1 ready\r\n")
            f.flush()
            logged = False
            while True:
                line = f.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                self.commands.append(text)
                tag, _, rest = text.partition(" ")
                cmd, _, args = rest.partition(" ")
                cmd = cmd.upper()
                if cmd == "CAPABILITY":
                    f.write(b"* CAPABILITY IMAP4rev1 UIDPLUS\r\n" + f"{tag} OK done\r\n".encode())
                elif cmd == "LOGIN":
                    user, pwd = _two_strings(args)
                    self.logins.append((user, pwd))
                    if (user, pwd) == (self.user, self.password):
                        logged = True
                        f.write(f"{tag} OK LOGIN completed\r\n".encode())
                    else:
                        f.write(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n".encode())
                elif not logged:
                    f.write(f"{tag} BAD not logged in\r\n".encode())
                elif cmd in ("EXAMINE", "SELECT"):
                    folder = args.strip().strip('"')
                    if folder not in self.folders:
                        f.write(f"{tag} NO [TRYCREATE] no such mailbox\r\n".encode())
                    else:
                        n = len(self.messages)
                        mode = "READ-ONLY" if cmd == "EXAMINE" else "READ-WRITE"
                        f.write(
                            f"* {n} EXISTS\r\n* OK [UIDVALIDITY 1234] UIDs valid\r\n{tag} OK [{mode}] done\r\n".encode()
                        )
                elif cmd == "UID":
                    sub, _, subargs = args.partition(" ")
                    if sub.upper() == "SEARCH":
                        uids = self._search(subargs)
                        f.write(("* SEARCH " + " ".join(str(u) for u in uids)).rstrip().encode() + b"\r\n")
                        f.write(f"{tag} OK SEARCH completed\r\n".encode())
                    elif sub.upper() == "FETCH":
                        self._fetch(f, tag, subargs)
                    else:
                        f.write(f"{tag} BAD unsupported\r\n".encode())
                elif cmd == "LOGOUT":
                    f.write(b"* BYE\r\n" + f"{tag} OK LOGOUT\r\n".encode())
                    f.flush()
                    return
                elif cmd == "NOOP":
                    f.write(f"{tag} OK\r\n".encode())
                else:
                    f.write(f"{tag} BAD unsupported command {cmd}\r\n".encode())
                f.flush()
        except (OSError, ValueError):
            return
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _search(self, args: str) -> list[int]:
        m = re.search(r"UID (\d+):(\*|\d+)", args)
        lo = int(m.group(1)) if m else 1
        hi = int(m.group(2)) if m and m.group(2) != "*" else 10**9
        return sorted(u for u in self.messages if lo <= u <= hi)

    def _fetch(self, f: Any, tag: str, args: str) -> None:
        m = re.match(r"([\d:,*]+) \((.*)\)$", args.strip()) or re.match(r"([\d:,*]+) (.*)$", args.strip())
        if not m:
            f.write(f"{tag} BAD fetch\r\n".encode())
            return
        rng, items = m.group(1), m.group(2)
        uids = self._range(rng)
        for seq, uid in enumerate(sorted(uids), start=1):
            msg = self.messages[uid]
            parts: list[bytes] = [f"UID {uid}".encode()]
            for item in re.findall(r"BODY\.PEEK\[[^\]]*\]|BODYSTRUCTURE|RFC822\.SIZE|FLAGS|BODY\[[^\]]*\]", items):
                if item == "BODYSTRUCTURE":
                    parts.append(b"BODYSTRUCTURE " + _bodystructure(msg))
                elif item == "RFC822.SIZE":
                    parts.append(f"RFC822.SIZE {len(msg.as_bytes())}".encode())
                elif item == "FLAGS":
                    parts.append(b"FLAGS ()")
                elif item.startswith(("BODY.PEEK[HEADER.FIELDS", "BODY[HEADER.FIELDS")):
                    names = re.search(r"\((.*)\)", item)
                    wanted = (names.group(1) if names else "").split()
                    hdr = "".join(f"{n}: {msg[n]}\r\n" for n in wanted if msg[n]) + "\r\n"
                    raw = hdr.encode()
                    parts.append(item.replace(".PEEK", "").encode() + b" {" + str(len(raw)).encode() + b"}\r\n" + raw)
                else:
                    section = re.search(r"\[([\d.]+)\]", item)
                    data = _section(msg, section.group(1) if section else "")
                    parts.append(item.replace(".PEEK", "").encode() + b" {" + str(len(data)).encode() + b"}\r\n" + data)
            f.write(f"* {seq} FETCH (".encode() + b" ".join(parts) + b")\r\n")
        f.write(f"{tag} OK FETCH completed\r\n".encode())

    def _range(self, rng: str) -> list[int]:
        out: set[int] = set()
        for piece in rng.split(","):
            if ":" in piece:
                a, b = piece.split(":")
                lo = int(a)
                hi = int(b) if b != "*" else max(self.messages, default=0)
                out.update(u for u in self.messages if lo <= u <= hi)
            elif piece.isdigit() and int(piece) in self.messages:
                out.add(int(piece))
        return sorted(out)


def _two_strings(args: str) -> tuple[str, str]:
    toks = re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', args)
    vals = [a if a else b for a, b in toks]
    return (vals[0] if vals else ""), (vals[1] if len(vals) > 1 else "")


def _bodystructure(msg: EmailMessage) -> bytes:
    def one(part: EmailMessage) -> str:
        if part.is_multipart():
            inner = "".join(one(p) for p in part.iter_parts())
            return f'({inner} "{part.get_content_subtype().upper()}")'
        maintype, subtype = part.get_content_maintype().upper(), part.get_content_subtype().upper()
        payload = part.get_payload(decode=True) or b""
        enc = part.get("Content-Transfer-Encoding", "7BIT").upper()
        name = part.get_filename()
        params = f'("NAME" "{name}")' if name else "NIL"
        disp = f'("ATTACHMENT" ("FILENAME" "{name}"))' if name else "NIL"
        return f'("{maintype}" "{subtype}" {params} NIL NIL "{enc}" {len(payload)} NIL {disp} NIL NIL)'

    return one(msg).encode()


def _section(msg: EmailMessage, section: str) -> bytes:
    if not section:
        return msg.as_bytes()
    part: Any = msg
    for idx in section.split("."):
        if part.is_multipart():
            part = list(part.iter_parts())[int(idx) - 1]
        elif idx != "1":
            raise ValueError("section")
    raw = part.get_payload(decode=False)
    return (raw if isinstance(raw, str) else "").encode()


def make_mail(
    *, sender: str, subject: str, pdf: bytes | None, filename: str = "facture.pdf", text: str = "Bonjour"
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "micka@example.org"
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain="example.org")
    msg.set_content(text)
    if pdf is not None:
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return msg
