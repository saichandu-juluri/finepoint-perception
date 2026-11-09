"""The server's database: labs, people, sign-in tokens and the record of every upload (docs/SERVER.md, "The database").

One SQLite file, opened once per request: WAL mode, foreign keys on, a busy timeout of 5 s. Passwords are kept as
scrypt hashes and tokens as SHA-256 hashes only, so a copy of the database signs nobody in. Nobody is ever deleted:
disabling a person revokes every token they hold and keeps their upload history.

The sign-in failure counts live in this module (one process), so every caller shares them. Standard library only.
"""
import base64
import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE labs (
  name TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL DEFAULT '',
  lab TEXT NOT NULL REFERENCES labs(name),
  admin INTEGER NOT NULL DEFAULT 0 CHECK (admin IN (0, 1)),
  password TEXT,
  disabled_at TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE tokens (
  hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('app', 'admin', 'setup')),
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT,
  last_used_at TEXT,
  revoked_at TEXT);
CREATE TABLE uploads (
  id INTEGER PRIMARY KEY,
  lab TEXT NOT NULL,
  session TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  size INTEGER NOT NULL,
  upload_id TEXT UNIQUE,
  user_id INTEGER REFERENCES users(id),
  computer TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('uploading', 'done', 'aborted', 'already_there')),
  started_at TEXT NOT NULL,
  finished_at TEXT);
"""
LAB = re.compile(r"[a-z0-9-]{1,40}")
EMAIL = re.compile(r"[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]{1,190}")
HOURS = {"app": None, "admin": 12, "setup": 72}
MIN_PASSWORD, MAX_PASSWORD = 12, 256
SCRYPT = {"n": 2**15, "r": 8, "p": 3, "maxmem": 64 * 1024**2, "dklen": 32}   # OpenSSL's 32 MiB default is too small
FAIL_WINDOW_S, MAX_EMAIL_FAILS, MAX_ADDRESS_FAILS = 15 * 60, 10, 30

_checks = threading.BoundedSemaphore(2)
_fails_lock = threading.Lock()
_fails: dict[tuple[str, str], list[float]] = {}


class Refused(Exception):
    """a plain sentence for the person: bad input to an admin command or form"""


class TooManyTries(Exception):
    pass


def now(offset_s: float = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset_s))


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextlib.contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn) -> int:
    """creates or upgrades the schema; safe to run again"""
    with transaction(conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'schema_version'").fetchone():
            for statement in filter(str.strip, SCHEMA.split(";")):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise Refused(f"the database is schema {version}, newer than this server's {SCHEMA_VERSION}")
        conn.execute("INSERT OR IGNORE INTO settings VALUES ('csrf_secret', ?)", (secrets.token_hex(32),))
    return version


def setting(conn, name: str) -> str:
    return conn.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()[0]


# ---- passwords ----

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt$15$8$3${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def _password_matches(stored: str, password: str) -> bool:
    _, log_n, r, p, salt, digest = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** int(log_n), r=int(r), p=int(p),
                         maxmem=SCRYPT["maxmem"], dklen=len(base64.b64decode(digest)))
    return hmac.compare_digest(got, base64.b64decode(digest))


_NOBODY = hash_password(secrets.token_urlsafe(16))      # hashed against for an unknown email, so timing tells nothing


def check_new_password(password, again) -> str:
    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise Refused(f"the password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    if password != again:
        raise Refused("the two passwords differ")
    return password


def signin(conn, email: str, password: str, client: str):
    """the person's row, or None for any failure (unknown email, no password yet, disabled, wrong password).
    TooManyTries before any hashing once the email or the client address has too many recent failures."""
    keys = (("email", email.strip().lower()), ("address", client))
    cut = time.time() - FAIL_WINDOW_S
    with _fails_lock:
        for k in list(_fails):         # ponytail: a full sweep per sign-in, fine for a few labs' worth of addresses
            _fails[k] = [t for t in _fails[k] if t > cut]
            if not _fails[k]:
                del _fails[k]
        if len(_fails.get(keys[0], ())) >= MAX_EMAIL_FAILS or len(_fails.get(keys[1], ())) >= MAX_ADDRESS_FAILS:
            raise TooManyTries()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip(),)).fetchone()
    ok = False
    if MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        with _checks:
            ok = _password_matches(row["password"] if row and row["password"] else _NOBODY, password)
    ok = ok and row is not None and row["password"] is not None and row["disabled_at"] is None
    with _fails_lock:
        if ok:
            _fails.pop(keys[0], None)
        else:
            for k in keys:
                _fails.setdefault(k, []).append(time.time())
    return row if ok else None


def set_password(conn, user_id: int, password: str):
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user_id))


# ---- tokens ----

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(conn, user_id: int, kind: str, label: str = "") -> str:
    token = "fpp_" + secrets.token_urlsafe(32)
    hours = HOURS[kind]
    conn.execute("INSERT INTO tokens (hash, user_id, kind, label, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (token_hash(token), user_id, kind, label, now(), hours and now(hours * 3600)))
    return token


def person_of(conn, token: str | None, kind: str):
    """the person a live token of this kind belongs to (with the token's hash and label), or None"""
    if not token:
        return None
    h = token_hash(token)
    row = conn.execute(
        "SELECT u.*, t.hash AS token, t.label AS label FROM tokens t JOIN users u ON u.id = t.user_id"
        " WHERE t.hash = ? AND t.kind = ? AND t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > ?)"
        " AND u.disabled_at IS NULL", (h, kind, now())).fetchone()
    if row:
        conn.execute("UPDATE tokens SET last_used_at = ? WHERE hash = ?", (now(), h))
    return row


def revoke(conn, token_hash_hex: str, user_id: int | None = None):
    conn.execute("UPDATE tokens SET revoked_at = ? WHERE hash = ? AND revoked_at IS NULL"
                 + (" AND user_id = ?" if user_id is not None else ""),
                 (now(), token_hash_hex) + ((user_id,) if user_id is not None else ()))


def use_setup_token(conn, token: str, password: str) -> bool:
    """sets the password and revokes the link, once: two requests with the same link cannot both win"""
    row = conn.execute(
        "UPDATE tokens SET revoked_at = ? WHERE hash = ? AND kind = 'setup' AND revoked_at IS NULL AND expires_at > ?"
        " AND user_id IN (SELECT id FROM users WHERE disabled_at IS NULL) RETURNING user_id",
        (now(), token_hash(token), now())).fetchone()
    if row:
        set_password(conn, row[0], password)
    return row is not None


# ---- labs and people ----

def add_lab(conn, name: str, title: str):
    if not LAB.fullmatch(name or ""):
        raise Refused("a lab name is 1 to 40 lowercase letters, digits and -")
    if not title or len(title) > 200:
        raise Refused("a lab needs a title of at most 200 characters")
    if conn.execute("SELECT 1 FROM labs WHERE name = ?", (name,)).fetchone():
        raise Refused(f"lab {name} is already there")
    conn.execute("INSERT INTO labs VALUES (?, ?, ?)", (name, title, now()))


def add_user(conn, email: str, lab: str, name: str = "", admin: bool = False) -> int:
    email = (email or "").strip()
    if not EMAIL.fullmatch(email):
        raise Refused("that is not an email address")
    if len(name) > 200:
        raise Refused("a name is at most 200 characters")
    if not conn.execute("SELECT 1 FROM labs WHERE name = ?", (lab,)).fetchone():
        raise Refused(f"there is no lab {lab}")
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise Refused(f"{email} is already there")
    return conn.execute("INSERT INTO users (email, name, lab, admin, created_at) VALUES (?, ?, ?, ?, ?)",
                        (email, name, lab, int(admin), now())).lastrowid


def user_id(conn, email: str) -> int:
    row = conn.execute("SELECT id FROM users WHERE email = ?", ((email or "").strip(),)).fetchone()
    if not row:
        raise Refused(f"there is nobody with the email {email}")
    return row[0]


def setup_link(conn, uid: int, public_url: str) -> str:
    """a new set-password link; any older unused link of the person stops working"""
    with transaction(conn):
        conn.execute("UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND kind = 'setup' AND revoked_at IS NULL",
                     (now(), uid))
        token = new_token(conn, uid, "setup")
    return f"{public_url.rstrip('/')}/set-password?t={token}"


def set_disabled(conn, uid: int, disabled: bool):
    with transaction(conn):
        conn.execute("UPDATE users SET disabled_at = ? WHERE id = ?", (now() if disabled else None, uid))
        if disabled:
            conn.execute("UPDATE tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now(), uid))
