"""The upload calls of camrig's uploader (docs/UPLOAD.md, "Cloud API"), served by cloud/server.py.

The lab computer never holds AWS credentials. It asks the server for short-lived links that put each piece of a file
straight into S3, and S3 checks every piece against the SHA-256 the link was signed with. The server moves no video
bytes. Each route gets the signed-in person (their lab and the computer's name), the request's JSON body, the store
and a database connection, and records every upload in the uploads table.

Every request is checked here at the boundary; the store behind it trusts what it is given. Standard library only.
"""
import base64
import binascii
import os
import re

import users

LINK_TTL_S = 3600
MAX_LINKS = 32
MAX_PARTS = 10_000
MAX_SIZE = MAX_PARTS * 5 * 1024**3          # S3's largest part times its most parts
KINDS = {"originals": "original", "derived": "derived", "sync": "sync"}
CONTENT_TYPES = {".mov": "video/mp4", ".mp4": "video/mp4", ".m4v": "video/mp4", ".json": "application/json",
                 ".csv": "text/csv", ".toml": "text/plain"}
SESSION = re.compile(r"[a-z0-9_-]{1,64}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
UPLOAD_ID = re.compile(r"[\x21-\x7e]{1,1024}")
BAD_SEGMENT = re.compile(r"[\x00-\x1f\x7f\\]")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NoSuchUpload(Exception):
    """the store has no upload with this ID: it was cancelled, finished or cleaned up"""


class BadUpload(Exception):
    """the store refused to join the parts (a part missing, too small, out of order, or a wrong ETag or checksum)"""


def bad(message: str):
    raise ApiError(400, message)


# ---- the boundary: every value a client sends is checked here ----

def body_field(body: dict, name: str, kind: type):
    v = body.get(name)
    if not isinstance(v, kind) or isinstance(v, bool):
        bad(f"{name} must be a {kind.__name__}")
    return v


def check_int(v, name: str, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        bad(f"{name} must be a whole number from {lo} to {hi}")
    return v


def check_session(s) -> str:
    if not isinstance(s, str) or not SESSION.fullmatch(s):
        bad("session must be lowercase letters, digits, _ and -")
    return s


def check_path(path) -> str:
    """the key below the session: originals/, derived/ or sync/ and at least one more name, or manifest.json"""
    if not isinstance(path, str) or not path or len(path.encode()) > 700:
        bad("path must be a short relative path")
    parts = path.split("/")
    if any(p in ("", ".", "..") or BAD_SEGMENT.search(p) for p in parts):
        bad(f"path {path!r} has an empty, '.', '..' or unsafe part")
    if path != "manifest.json" and (parts[0] not in KINDS or len(parts) < 2):
        bad(f"path {path!r} must start originals/, derived/ or sync/, or be manifest.json")
    return path


def kind_of(path: str) -> str:
    return "manifest" if path == "manifest.json" else KINDS[path.split("/")[0]]


def content_type_of(path: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def session_prefix(lab: str, session: str) -> str:
    return f"labs/{lab}/sessions/{session}/"


def check_key(key, lab: str) -> str:
    """a key the client got from /uploads/start: inside its own lab (else 403) and well formed (else 400)"""
    if not isinstance(key, str):
        bad("key must be a string")
    if not key.startswith(f"labs/{lab}/"):
        raise ApiError(403, "this key belongs to another lab")
    parts = key.split("/", 4)
    if len(parts) != 5 or parts[2] != "sessions":
        bad("key must be labs/<lab>/sessions/<session>/<path>")
    check_session(parts[3])
    check_path(parts[4])
    return key


def check_upload_id(v) -> str:
    if not isinstance(v, str) or not UPLOAD_ID.fullmatch(v):
        bad("upload_id must be the ID /uploads/start gave")
    return v


def check_sha256_b64(v, name: str = "sha256_b64") -> str:
    try:
        if isinstance(v, str) and len(base64.b64decode(v, validate=True)) == 32:
            return v
    except (binascii.Error, ValueError):
        pass
    bad(f"{name} must be a SHA-256 in base64")


def check_parts(parts, most: int, fields: tuple[str, ...]) -> list[dict]:
    if not isinstance(parts, list) or not 1 <= len(parts) <= most:
        bad(f"parts must be a list of 1 to {most}")
    out = []
    for p in parts:
        if not isinstance(p, dict):
            bad("each part must be an object")
        q = {"n": check_int(p.get("n"), "n", 1, MAX_PARTS), "sha256_b64": check_sha256_b64(p.get("sha256_b64"))}
        if "etag" in fields:
            e = p.get("etag")
            if not isinstance(e, str) or not 1 <= len(e) <= 200:
                bad("etag must be the ETag the piece's PUT answered")
            q["etag"] = e
        out.append(q)
    if len({p["n"] for p in out}) != len(out):
        bad("a part number appears twice")
    return out


# ---- the routes: who is the person's row (lab, id, label = the computer), db a connection ----

def start(who, body, store, db):
    session = check_session(body.get("session"))
    path = check_path(body.get("path"))
    size = check_int(body.get("size"), "size", 0, MAX_SIZE)
    sha = body.get("sha256")
    if not isinstance(sha, str) or not SHA256_HEX.fullmatch(sha):
        bad("sha256 must be 64 lowercase hex digits")
    kind = body.get("kind")
    if kind != kind_of(path):
        bad(f"kind must be {kind_of(path)!r} for {path!r}")
    if body.get("content_type") != content_type_of(path):
        bad(f"content_type must be {content_type_of(path)!r} for {path!r}")
    key = session_prefix(who["lab"], session) + path
    have = store.head(key)
    if have and have["sha256"] == sha and have["size"] == size:
        answer, upload_id, status = {"done": True, "key": key}, None, "already_there"
    else:
        upload_id = store.create(key, content_type_of(path), {"sha256": sha, "kind": kind, "session": session})
        answer, status = {"upload_id": upload_id, "key": key}, "uploading"
    db.execute("INSERT INTO uploads (lab, session, path, kind, size, upload_id, user_id, computer, status, started_at)"
               " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (who["lab"], session, path, kind, size, upload_id, who["id"], who["label"], status, users.now()))
    return answer


def finish(db, who, upload_id: str, status: str):
    db.execute("UPDATE uploads SET status = ?, finished_at = ? WHERE upload_id = ? AND lab = ? AND status = 'uploading'",
               (status, users.now(), upload_id, who["lab"]))


def links(who, body, store, db):
    key = check_key(body.get("key"), who["lab"])
    upload_id = check_upload_id(body.get("upload_id"))
    parts = check_parts(body.get("parts"), MAX_LINKS, ())
    store.list_parts(key, upload_id)            # an unknown upload is a 404 here, not on every piece
    out = []
    for p in parts:
        url, headers = store.presign_part(key, upload_id, p["n"], p["sha256_b64"], LINK_TTL_S)
        out.append({"n": p["n"], "url": url, "headers": headers, "expires_in": LINK_TTL_S})
    return {"links": out}


def parts(who, body, store, db):
    key = check_key(body.get("key"), who["lab"])
    return {"parts": store.list_parts(key, check_upload_id(body.get("upload_id")))}


def complete(who, body, store, db):
    key = check_key(body.get("key"), who["lab"])
    upload_id = check_upload_id(body.get("upload_id"))
    size = check_int(body.get("size"), "size", 0, MAX_SIZE)
    wanted = sorted(check_parts(body.get("parts"), MAX_PARTS, ("etag",)), key=lambda p: p["n"])
    if [p["n"] for p in wanted] != list(range(1, len(wanted) + 1)):
        bad("parts must be numbered 1, 2, 3 ... with none left out")
    held = {p["n"]: p["size"] for p in store.list_parts(key, upload_id)}
    joined = sum(held.get(p["n"], 0) for p in wanted)
    if joined != size:
        bad(f"the parts hold {joined} bytes, the file has {size}: send the missing or wrong parts again")
    got = store.complete(key, upload_id, wanted)
    if got["size"] != size:
        raise ApiError(500, f"S3 joined {got['size']} bytes, expected {size}")
    finish(db, who, upload_id, "done")
    return {"key": key, "size": got["size"]}


def abort(who, body, store, db):
    upload_id = check_upload_id(body.get("upload_id"))
    store.abort(check_key(body.get("key"), who["lab"]), upload_id)
    finish(db, who, upload_id, "aborted")
    return {}


def files(who, body, store, db):
    prefix = session_prefix(who["lab"], check_session(body.get("session")))
    objects, uploads = store.list_prefix(prefix)
    return {"files": [{"path": o["key"][len(prefix):], "size": o["size"], "sha256": o["sha256"], "kind": o["kind"],
                       "modified": o["modified"]} for o in objects],
            "uploading": [{"path": u["key"][len(prefix):], "upload_id": u["upload_id"], "started": u["started"]}
                          for u in uploads]}


ROUTES = {
    ("POST", "/uploads/start"): start,
    ("POST", "/uploads/links"): links,
    ("POST", "/uploads/parts"): parts,
    ("POST", "/uploads/complete"): complete,
    ("POST", "/uploads/abort"): abort,
}
FILES = re.compile(r"/sessions/([^/]+)/files")
