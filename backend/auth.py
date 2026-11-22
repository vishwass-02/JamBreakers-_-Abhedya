"""Who may watch and who may command.

* Accounts live in data/users.json (git-ignored): user ID, role, salt and a PBKDF2-SHA256 hash
  (260,000 rounds). Passwords are never stored or logged.
* Roles: ``commander`` may send commands (load missions, launch jammers, switch the AI);
  ``observer`` may only watch (judges, teammates on their phones).
* A phone joins by scanning the QR code on the laptop. The QR carries a **join code** that changes every
  15 minutes, and the phone must still sign in with a user ID and password: something you can only see in the
  room, plus something only you know. Someone who merely knows the Wi-Fi address cannot get in.
* Sessions are random 256-bit tokens kept in server memory; they expire after 12 hours (or on sign-out, or when the
  server restarts). Five wrong passwords for one user from one address lock that pair out for a minute.
* The demo laptop itself (requests from 127.0.0.1 / ::1) is trusted as the operator's console.

Limit, stated plainly: on plain http the password crosses the Wi-Fi unencrypted. Run with a TLS certificate
(AEGIS_SSL_CERTFILE / AEGIS_SSL_KEYFILE) when the network is not your own hotspot.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROLES = ("commander", "observer")
PBKDF2_ROUNDS = 260_000
SESSION_S = 12 * 3600
JOIN_CODE_S = 15 * 60
MAX_FAILS, FAIL_WINDOW_S, LOCK_S = 5, 300, 60
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
_JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I: easy to read off a screen
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Principal:
    user: str
    role: str
    local: bool = False

    @property
    def can_command(self) -> bool:
        return self.role == "commander"


LAPTOP = Principal("laptop", "commander", local=True)
OPEN = Principal("anyone", "commander")          # only when auth is switched off (AEGIS_AUTH=0)


def hash_password(password: str, salt: Optional[bytes] = None, rounds: int = PBKDF2_ROUNDS) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": rounds, "algo": "pbkdf2_sha256"}


def check_password(password: str, rec: dict) -> bool:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(rec["salt"]), int(rec["rounds"]))
    return hmac.compare_digest(digest.hex(), rec["hash"])


def make_password(n: int = 12) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def is_local(host: Optional[str]) -> bool:
    return (host or "") in LOCAL_HOSTS


class AuthStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.fails: dict[tuple[str, str], list[float]] = {}
        self._codes: list[tuple[str, float]] = []      # (code, issued) newest first; the previous one stays valid
        self._dummy = hash_password("not-a-real-password", rounds=PBKDF2_ROUNDS)
        self._mtime: Optional[float] = None
        self.load()

    # ---- accounts ----
    def load(self) -> None:
        if self.path.exists():
            self._mtime = self.path.stat().st_mtime
            self.users = json.loads(self.path.read_text(encoding="utf-8")).get("users", {})

    def refresh(self) -> None:
        """Pick up accounts added or passwords changed (python -m backend.users) while the server runs:
        without this a running server keeps checking the old passwords until it is restarted."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            try:
                self.load()
            except (OSError, ValueError):
                pass                                  # half-written file: keep the accounts we have

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")        # one temp file per process: two starts never share it
        tmp.write_text(json.dumps({"users": self.users}, indent=2), encoding="utf-8")
        # Windows refuses to replace a file another process has open for a moment (antivirus scanning a new file,
        # a second server starting): retry briefly instead of failing (seen on the demo laptop: WinError 32)
        for attempt in range(40):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.05)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def ensure_first_run(self) -> dict[str, tuple[str, str]]:
        """No accounts yet: create the single shared account (full control) with a random password. Returns {user: (role, pw)}."""
        self.load()                                   # another server start may have created them a moment ago
        if self.users:
            return {}
        made = {"abhedya": ("commander", make_password())}
        for name, (role, pw) in made.items():
            self.add_user(name, pw, role, save=False)
        self.save()
        return made

    def add_user(self, name: str, password: str, role: str, save: bool = True) -> None:
        if not NAME_RE.match(name):
            raise ValueError("User ID: 3-32 letters, digits, dot, dash or underscore.")
        if role not in ROLES:
            raise ValueError(f"Role must be one of {', '.join(ROLES)}.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
        with self._lock:
            self.users[name.lower()] = {"name": name, "role": role, "created": int(time.time()), **hash_password(password)}
            self._drop_sessions(name.lower())
        if save:
            self.save()

    def remove_user(self, name: str) -> bool:
        with self._lock:
            gone = self.users.pop(name.lower(), None) is not None
            self._drop_sessions(name.lower())
        if gone:
            self.save()
        return gone

    def _drop_sessions(self, key: str) -> None:
        for tok in [t for t, s in self.sessions.items() if s["key"] == key]:
            del self.sessions[tok]

    # ---- join code shown in the QR ----
    def join_code(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        with self._lock:
            if not self._codes or now - self._codes[0][1] >= JOIN_CODE_S:
                code = "".join(secrets.choice(_JOIN_ALPHABET) for _ in range(6))
                self._codes = [(code, now)] + self._codes[:1]
            return self._codes[0][0]

    def join_code_ok(self, code: Optional[str], now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        code = (code or "").strip().upper()
        with self._lock:
            return any(hmac.compare_digest(code, c) and now - t < 2 * JOIN_CODE_S for c, t in self._codes)

    # ---- sign in / out ----
    def login(self, name: str, password: str, ip: str, join_code: Optional[str] = None,
              need_join_code: bool = True, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        key = (name or "").strip().lower()
        fk = (key, ip or "?")
        with self._lock:
            recent = [t for t in self.fails.get(fk, []) if now - t < FAIL_WINDOW_S]
            self.fails[fk] = recent
            if len(recent) >= MAX_FAILS and now - recent[-1] < LOCK_S:
                raise AuthError(429, "locked", f"Too many wrong passwords. Try again in {int(LOCK_S - (now - recent[-1])) + 1} s.")
        if need_join_code and not self.join_code_ok(join_code, now):
            raise AuthError(403, "join_code", "Scan the QR code on the laptop again (the join code is missing or has expired).")
        self.refresh()
        rec = self.users.get(key)
        ok = check_password(password or "", rec if rec else self._dummy) and rec is not None   # same work either way
        if not ok:
            with self._lock:
                self.fails.setdefault(fk, []).append(now)
            raise AuthError(401, "bad_login", "Wrong user ID or password.")
        with self._lock:
            self.fails.pop(fk, None)
            tok = secrets.token_urlsafe(32)
            self.sessions[tok] = {"key": key, "user": rec["name"], "role": rec["role"], "exp": now + SESSION_S, "ip": ip}
        return {"token": tok, "user": rec["name"], "role": rec["role"], "expires_in": SESSION_S}

    def check(self, token: Optional[str], now: Optional[float] = None) -> Optional[Principal]:
        if not token:
            return None
        now = time.time() if now is None else now
        with self._lock:
            s = self.sessions.get(token)
            if not s:
                return None
            if s["exp"] < now:
                del self.sessions[token]
                return None
            return Principal(s["user"], s["role"])

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            self.sessions.pop(token or "", None)


def origin_ok(headers) -> bool:
    """A browser always says which page opened a WebSocket (Origin). Only our own pages may: otherwise any web page
    open on the demo laptop could connect to ws://localhost:8000 and command the demo as the trusted laptop
    (cross-site WebSocket hijacking). Non-browser clients send no Origin and are judged by address and token."""
    origin = headers.get("origin")
    if not origin:
        return True
    host = headers.get("host", "")
    return origin.split("://", 1)[-1].rstrip("/").lower() == host.lower()


_TOKEN_RE = re.compile(r"(token=)[^&\s\"']+")


def redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1***", text)


class RedactTokens:
    """logging filter: never print session tokens (the server window is often on the projector)."""

    def filter(self, record) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def token_from(headers, query) -> Optional[str]:
    h = headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return query.get("token") or None
