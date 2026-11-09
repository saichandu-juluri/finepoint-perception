"""The local stand-in for S3: the API's seven store calls and the database backup, plus the piece PUTs and
small-object GETs that S3 itself would serve (cloud/dev_server.py routes them here).

It is as strict as S3, so what passes here passes there: links are HMAC-signed over the key, upload, part number,
piece checksum and expiry with a secret kept in the data folder; a piece must match its signed checksum; complete
refuses a part under 5 MiB that is not the last, more than 10,000 parts, parts out of order, a part never uploaded,
and a wrong ETag or checksum. The ETag of a piece is the MD5 hex of its bytes, in quotes, as S3's.

Bytes are kept only for objects up to 64 MiB; bigger ones keep their size and checksums, because the lab computer's
disk cannot hold a second copy of every session. The index is one JSON file, replaced atomically on every change,
so stopping the server at any moment loses nothing.

    CAMRIG_CLOUD_DEV_FAULTS="put-500-every=3,link-ttl-s=2"     every 3rd piece fails with 500; links last 2 s
"""
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import quote

from api import BadUpload, NoSuchUpload

KEEP_BYTES = 64 * 1024**2
MIN_PART = 5 * 1024**2
MAX_PARTS = 10_000
CHUNK = 1024**2


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_faults(text: str) -> dict:
    faults = {}
    for item in filter(None, (s.strip() for s in text.split(","))):
        name, _, value = item.partition("=")
        if name not in ("put-500-every", "link-ttl-s") or not value.isdigit() or int(value) < 1:
            raise ValueError(f"CAMRIG_CLOUD_DEV_FAULTS: {item!r} is not put-500-every=N or link-ttl-s=S")
        faults[name] = int(value)
    return faults


class FakeStore:
    def __init__(self, folder, base_url: str, faults: str | None = None):
        self.dir = Path(folder)
        self.blobs = self.dir / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.faults = parse_faults(os.environ.get("CAMRIG_CLOUD_DEV_FAULTS", "") if faults is None else faults)
        self.lock = threading.Lock()
        self.puts = 0
        secret = self.dir / "secret"
        if not secret.exists():
            secret.write_text(secrets.token_hex(32))
            secret.chmod(0o600)
        self.secret = bytes.fromhex(secret.read_text().strip())
        index = self.dir / "index.json"
        self.index = json.loads(index.read_text()) if index.exists() else {"objects": {}, "uploads": {}}

    def _save(self):
        tmp = self.dir / "index.json.tmp"
        tmp.write_text(json.dumps(self.index, indent=1))
        os.replace(tmp, self.dir / "index.json")

    def _drop(self, name: str | None):
        if name:
            (self.blobs / name).unlink(missing_ok=True)

    def _upload(self, key: str, upload_id: str) -> dict:
        u = self.index["uploads"].get(upload_id)
        if u is None or u["key"] != key:
            raise NoSuchUpload()
        return u

    def _signature(self, key: str, upload_id: str, n: int, sha256_b64: str, expires: int) -> str:
        msg = "\n".join(("PUT", key, upload_id, str(n), sha256_b64, str(expires))).encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()

    # ---- the seven store calls ----

    def head(self, key: str) -> dict | None:
        with self.lock:
            o = self.index["objects"].get(key)
            return o and {"size": o["size"], "sha256": o["metadata"]["sha256"], "kind": o["metadata"]["kind"],
                          "modified": o["modified"]}

    def create(self, key: str, content_type: str, metadata: dict) -> str:
        upload_id = secrets.token_urlsafe(24)
        with self.lock:
            self.index["uploads"][upload_id] = {"key": key, "content_type": content_type, "metadata": metadata,
                                                "started": now_iso(), "big": False, "parts": {}}
            self._save()
        return upload_id

    def presign_part(self, key: str, upload_id: str, n: int, sha256_b64: str, expires_s: int) -> tuple[str, dict]:
        expires = math.ceil(time.time() + self.faults.get("link-ttl-s", expires_s))
        sig = self._signature(key, upload_id, n, sha256_b64, expires)
        url = (f"{self.base_url}/_s3/{quote(key)}?uploadId={quote(upload_id, safe='')}&partNumber={n}"
               f"&X-Expires={expires}&X-Signature={sig}")
        return url, {"x-amz-checksum-sha256": sha256_b64}

    def list_parts(self, key: str, upload_id: str) -> list[dict]:
        with self.lock:
            u = self._upload(key, upload_id)
            return [{"n": int(n), "size": p["size"], "etag": p["etag"], "sha256_b64": p["sha256_b64"]}
                    for n, p in sorted(u["parts"].items(), key=lambda kv: int(kv[0]))]

    def complete(self, key: str, upload_id: str, parts: list[dict]) -> dict:
        with self.lock:
            u = self._upload(key, upload_id)
            numbers = [p["n"] for p in parts]
            if len(parts) > MAX_PARTS or not all(1 <= n <= MAX_PARTS for n in numbers):
                raise BadUpload(f"InvalidRequest: at most {MAX_PARTS} parts, numbered 1 to {MAX_PARTS}")
            if numbers != sorted(set(numbers)):
                raise BadUpload("InvalidPartOrder: the parts must be listed in ascending order")
            held = []
            for p in parts:
                h = u["parts"].get(str(p["n"]))
                if h is None:
                    raise BadUpload(f"InvalidPart: part {p['n']} was never uploaded")
                if h["etag"].strip('"') != p["etag"].strip('"') or h["sha256_b64"] != p["sha256_b64"]:
                    raise BadUpload(f"InvalidPart: part {p['n']} has a different ETag or checksum")
                held.append(h)
            if any(h["size"] < MIN_PART for h in held[:-1]):
                raise BadUpload("EntityTooSmall: every part but the last must be at least 5 MiB")
            size = sum(h["size"] for h in held)
            data = None
            if size <= KEEP_BYTES and all(h["data"] for h in held):
                data = secrets.token_hex(16) + ".obj"
                with open(self.blobs / data, "wb") as out:
                    for h in held:
                        out.write((self.blobs / h["data"]).read_bytes())
            md5s = b"".join(bytes.fromhex(h["etag"].strip('"')) for h in held)
            old = self.index["objects"].get(key)
            self._drop(old and old["data"])
            self.index["objects"][key] = {
                "size": size, "content_type": u["content_type"], "metadata": u["metadata"], "modified": now_iso(),
                "etag": f'"{hashlib.md5(md5s).hexdigest()}-{len(held)}"',
                "parts": [{"size": h["size"], "sha256_b64": h["sha256_b64"]} for h in held], "data": data}
            for p in u["parts"].values():
                self._drop(p["data"])
            del self.index["uploads"][upload_id]
            self._save()
            return {"size": size}

    def abort(self, key: str, upload_id: str) -> None:
        with self.lock:
            u = self._upload(key, upload_id)
            for p in u["parts"].values():
                self._drop(p["data"])
            del self.index["uploads"][upload_id]
            self._save()

    def list_prefix(self, prefix: str) -> tuple[list[dict], list[dict]]:
        with self.lock:
            objects = [{"key": k, "size": o["size"], "sha256": o["metadata"]["sha256"], "kind": o["metadata"]["kind"],
                        "modified": o["modified"]} for k, o in sorted(self.index["objects"].items()) if k.startswith(prefix)]
            uploads = [{"key": u["key"], "upload_id": i, "started": u["started"]}
                       for i, u in sorted(self.index["uploads"].items(), key=lambda kv: kv[1]["key"])
                       if u["key"].startswith(prefix)]
            return objects, uploads

    # ---- the database backup, at backups/perception.db in the data folder ----

    def put_backup(self, local_path) -> None:
        (self.dir / "backups").mkdir(exist_ok=True)
        shutil.copyfile(local_path, self.dir / "backups" / "perception.db.tmp")
        os.replace(self.dir / "backups" / "perception.db.tmp", self.dir / "backups" / "perception.db")

    def get_backup(self, local_path) -> bool:
        copy = self.dir / "backups" / "perception.db"
        if not copy.exists():
            return False
        shutil.copyfile(copy, local_path)
        return True

    # ---- what S3 itself serves ----

    def read_object(self, key: str) -> bytes | None:
        with self.lock:
            o = self.index["objects"].get(key)
            return (self.blobs / o["data"]).read_bytes() if o and o["data"] else None

    def put_part(self, key: str, query: dict, checksum: str | None, body, length: int) -> tuple[int, dict, str]:
        """one piece PUT: (status, headers, error message). The body is always read to its end, so the client sees
        the answer rather than a broken connection."""
        refusal = self._refuse_link(key, query, checksum)
        if refusal:
            drain(body, length)
            return refusal
        upload_id, n = query["uploadId"], int(query["partNumber"])
        with self.lock:
            self.puts += 1
            fail = self.faults.get("put-500-every") and self.puts % self.faults["put-500-every"] == 0
            u = self.index["uploads"].get(upload_id)
            keep = bool(u) and not u["big"] and length + sum(
                p["size"] for m, p in u["parts"].items() if m != str(n)) <= KEEP_BYTES
        if fail:
            drain(body, length)
            return 500, {}, "InternalError: a fault the test asked for (put-500-every)"
        if u is None or u["key"] != key:
            drain(body, length)
            return 404, {}, "NoSuchUpload: the upload was cancelled or finished"
        name = secrets.token_hex(16) + ".part" if keep else None
        sha, md5, got = hashlib.sha256(), hashlib.md5(), 0
        with open(self.blobs / name, "wb") if name else open(os.devnull, "wb") as out:
            while got < length:
                chunk = body.read(min(CHUNK, length - got))
                if not chunk:
                    break
                sha.update(chunk)
                md5.update(chunk)
                out.write(chunk)
                got += len(chunk)
        if got != length or base64.b64encode(sha.digest()).decode() != checksum:
            self._drop(name)
            return 400, {}, "BadDigest: the piece's bytes do not match its x-amz-checksum-sha256"
        etag = f'"{md5.hexdigest()}"'
        with self.lock:
            u = self.index["uploads"].get(upload_id)
            if u is None:
                self._drop(name)
                return 404, {}, "NoSuchUpload: the upload was cancelled while the piece came in"
            old = u["parts"].get(str(n))
            self._drop(old and old["data"])
            u["parts"][str(n)] = {"size": got, "etag": etag, "sha256_b64": checksum, "data": name}
            if sum(p["size"] for p in u["parts"].values()) > KEEP_BYTES:
                u["big"] = True
                for p in u["parts"].values():
                    self._drop(p["data"])
                    p["data"] = None
            self._save()
        return 200, {"ETag": etag, "x-amz-checksum-sha256": checksum}, ""

    def _refuse_link(self, key: str, query: dict, checksum: str | None) -> tuple[int, dict, str] | None:
        try:
            upload_id, n = query["uploadId"], int(query["partNumber"])
            expires, sig = int(query["X-Expires"]), query["X-Signature"]
        except (KeyError, ValueError):
            return 403, {}, "AccessDenied: this is not a signed piece link"
        if not checksum or not hmac.compare_digest(sig, self._signature(key, upload_id, n, checksum, expires)):
            return 403, {}, "SignatureDoesNotMatch: the link or its x-amz-checksum-sha256 header was changed"
        if time.time() > expires:
            return 403, {}, "AccessDenied: Request has expired"
        return None


def drain(body, length: int):
    while length > 0:
        chunk = body.read(min(CHUNK, length))
        if not chunk:
            return
        length -= len(chunk)
