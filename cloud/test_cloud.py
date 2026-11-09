"""Checks for the cloud side: the server (upload calls, sign-in, admin pages, commands), the local stand-in and the
S3 store, with no AWS at all.

    pixi run --frozen -e cloud test-cloud

The stand-in runs as a real server (cloud/dev_server.py) on a free port in a temporary folder, with its request log in
server.log there. A second lab and person are made through users.py on its database and signed in over HTTP. The S3
store is checked offline: its presigned piece link is signed locally with fake credentials, and every other S3 call
goes through botocore's Stubber, which checks each parameter name against the S3 model.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

os.environ.update(AWS_ACCESS_KEY_ID="AKIDTESTONLY", AWS_SECRET_ACCESS_KEY="test-secret-only", AWS_REGION="us-east-1",
                  AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import api
import server
import store_fake
import users

DEV_KEY = "perception-dev-key"
OTHER_EMAIL, OTHER_PASSWORD = "other@example.com", "other-lab-password"
MIB = 1024**2


def sha_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_b64(b: bytes) -> str:
    return base64.b64encode(hashlib.sha256(b).digest()).decode()


class Server:
    def __init__(self, folder, faults: str = ""):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "server.log"
        env = {**os.environ, "CAMRIG_CLOUD_DEV_FAULTS": faults}
        with open(self.log, "a") as log:
            self.p = subprocess.Popen([sys.executable, str(HERE / "dev_server.py"), "--port", "0", "--dir", str(folder)],
                                      stdout=subprocess.PIPE, stderr=log, text=True, env=env)
        line = self.p.stdout.readline()
        assert re.fullmatch(r"ready http://127\.0\.0\.1:\d+ lab dev key perception-dev-key\n", line), f"ready line: {line!r}"
        self.url = line.split()[1]
        self.port = int(self.url.rsplit(":", 1)[1])
        self.person("other", OTHER_EMAIL, OTHER_PASSWORD)
        self.other = self.signin(OTHER_EMAIL, OTHER_PASSWORD, "other-mac")["token"]

    def db(self):
        return users.connect(self.dir / "perception.db")

    def person(self, lab: str, email: str, password: str | None, admin: bool = False) -> int:
        """a lab (when new) and a person in it through users.py, with this password (None: invited only)"""
        db = self.db()
        try:
            if not db.execute("SELECT 1 FROM labs WHERE name = ?", (lab,)).fetchone():
                users.add_lab(db, lab, f"Lab {lab}")
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                return users.user_id(db, email)
            uid = users.add_user(db, email, lab, "", admin)
            if password:
                users.set_password(db, uid, password)
            return uid
        finally:
            db.close()

    def signin(self, email: str, password: str, computer: str = "test", address: str = "10.0.0.1") -> dict:
        status, answer = self.call("POST", "/auth/signin", {"email": email, "password": password, "computer": computer},
                                   key=None, headers={"X-Forwarded-For": address})
        assert status == 200, f"sign-in of {email}: {status} {answer}"
        return answer

    def raw(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        """(status, headers, body) without following redirects"""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.headers, r.read()
        finally:
            c.close()

    def form(self, path: str, fields: dict, cookie: str | None = None, origin: str | None = "own", **headers):
        h = {"Content-Type": "application/x-www-form-urlencoded", **headers}
        if origin:
            h["Origin"] = self.url if origin == "own" else origin
        if cookie:
            h["Cookie"] = f"fpp_admin={cookie}"
        return self.raw("POST", path, urlencode(fields).encode(), h)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.p.terminate()
        self.p.wait(10)

    def call(self, method: str, path: str, body=None, key: str | None = DEV_KEY, headers: dict | None = None) -> tuple[int, dict]:
        data = body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers or {})
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        return send(req)

    def ok(self, method: str, path: str, body=None, key: str = DEV_KEY) -> dict:
        status, answer = self.call(method, path, body, key)
        assert status == 200, f"{method} {path}: {status} {answer}"
        return answer

    def start(self, path: str, data: bytes, session: str = "s1", key: str = DEV_KEY) -> dict:
        return self.ok("POST", "/uploads/start", {"session": session, "path": path, "size": len(data), "sha256": sha_hex(data),
                                                  "kind": api.kind_of(path), "content_type": api.content_type_of(path)}, key)

    def link(self, up: dict, n: int, piece: bytes) -> dict:
        return self.ok("POST", "/uploads/links", {"key": up["key"], "upload_id": up["upload_id"],
                                                  "parts": [{"n": n, "sha256_b64": sha_b64(piece)}]})["links"][0]

    def complete(self, up: dict, pieces: list[bytes], etags: list[str]) -> tuple[int, dict]:
        return self.call("POST", "/uploads/complete", {
            "key": up["key"], "upload_id": up["upload_id"], "size": sum(map(len, pieces)),
            "parts": [{"n": i + 1, "etag": e, "sha256_b64": sha_b64(p)} for i, (p, e) in enumerate(zip(pieces, etags))]})

    def upload(self, path: str, pieces: list[bytes], session: str = "s1") -> tuple[dict, list[str]]:
        """start, a link and a PUT per piece; complete is left to the caller"""
        up = self.start(path, b"".join(pieces), session)
        etags = []
        for n, piece in enumerate(pieces, 1):
            status, headers = put(self.link(up, n, piece), piece)
            assert status == 200, f"piece {n}: {status}"
            etags.append(headers["ETag"])
        return up, etags


def send(req) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw.startswith(b"{") else {"raw": raw.decode()}


def put(link: dict, data: bytes, headers: dict | None = None, url: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url or link["url"], data=data, method="PUT",
                                 headers=link["headers"] if headers is None else headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, dict(e.headers)


# ---- the stand-in, over HTTP ----

def test_keys_and_routes(d):
    with Server(d) as s:
        assert s.ok("GET", "/health") == {"ok": True, "lab": "dev"}
        assert s.ok("GET", "/health", key=s.other) == {"ok": True, "lab": "other"}
        assert s.ok("GET", "/health", key=None) == {"ok": True}, "health without a token: no lab"
        assert s.ok("GET", "/health", key="not-a-key") == {"ok": True}, "health with an unknown token: no lab"
        for key in (None, "not-a-key", "fpp_" + "x" * 43):
            for method, path in (("GET", "/auth/me"), ("GET", "/sessions/s1/files"), ("POST", "/uploads/parts")):
                assert s.call(method, path, {} if method == "POST" else None, key=key) == (401, {"error": "signed out: sign in again"}), f"{method} {path} {key}"
        assert s.call("GET", "/nothing")[0] == 404
        assert s.call("GET", "/uploads/start")[0] == 404, "a POST call answers 404 to GET"
        assert s.call("POST", "/uploads/start", b"{not json")[0] == 400
        assert s.call("POST", "/uploads/start", b"[1]")[0] == 400
        assert s.call("POST", "/uploads/start", b"[" * 100_000 + b"]" * 100_000)[0] == 400, "deep nesting is bad input"
        port = int(s.url.rsplit(":", 1)[1])
        for method in ("POST /health", "PUT /_s3/x"):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                c.sendall(f"{method} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {DEV_KEY}\r\nContent-Length: -1\r\n\r\n".encode())
                head = c.recv(100)
                assert re.match(rb"HTTP/1\.[01] 4\d\d ", head), f"{method} with Content-Length -1: {head!r}"
    print("PASS  tokens and the route table: 401 signed out without a known token, 404 for no such call, 400 for a body that is not JSON,"
          " too deep, or a negative Content-Length")


def test_round_trip(d):
    a, b = os.urandom(5 * MIB), os.urandom(1000)
    with Server(d) as s:
        up, etags = s.upload("originals/cam1/cam1 part01.MOV", [a, b])
        assert up["key"] == "labs/dev/sessions/s1/originals/cam1/cam1 part01.MOV"
        held = s.ok("POST", "/uploads/parts", {"key": up["key"], "upload_id": up["upload_id"]})["parts"]
        assert held == [{"n": 1, "size": len(a), "etag": etags[0], "sha256_b64": sha_b64(a)},
                        {"n": 2, "size": len(b), "etag": etags[1], "sha256_b64": sha_b64(b)}], held
        assert etags[0] == f'"{hashlib.md5(a).hexdigest()}"', "ETag = MD5 hex of the piece, in quotes"
        listing = s.ok("GET", "/sessions/s1/files")
        assert [u["path"] for u in listing["uploading"]] == ["originals/cam1/cam1 part01.MOV"] and not listing["files"]
        assert s.complete(up, [a, b], etags) == (200, {"key": up["key"], "size": len(a) + len(b)})
        listing = s.ok("GET", "/sessions/s1/files")
        f = listing["files"][0]
        assert (f["path"], f["size"], f["sha256"], f["kind"]) == ("originals/cam1/cam1 part01.MOV", len(a) + len(b), sha_hex(a + b), "original")
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", f["modified"]) and not listing["uploading"]
        req = urllib.request.Request(f"{s.url}/_s3/{urllib.parse.quote(up['key'])}", headers={"Authorization": f"Bearer {DEV_KEY}"})
        with urllib.request.urlopen(req) as r:
            assert r.read() == a + b, "a small object keeps its bytes"
        assert s.start("originals/cam1/cam1 part01.MOV", a + b) == {"done": True, "key": up["key"]}, "same sha256: done"
        again = s.start("originals/cam1/cam1 part01.MOV", a)
        assert "upload_id" in again, "a changed file gets a new upload"
        assert s.ok("POST", "/uploads/abort", {"key": again["key"], "upload_id": again["upload_id"]}) == {}
        assert s.call("POST", "/uploads/abort", {"key": again["key"], "upload_id": again["upload_id"]})[0] == 404
        assert s.call("POST", "/uploads/links", {"key": again["key"], "upload_id": again["upload_id"],
                                                 "parts": [{"n": 1, "sha256_b64": sha_b64(a)}]})[0] == 404
        assert not s.ok("GET", "/sessions/s1/files")["uploading"]
        assert s.ok("GET", "/sessions/s1/files", key=s.other) == {"files": [], "uploading": []}, "a lab sees only its own files"
    print("PASS  round trip: start, links, PUT, parts, complete, list, read back, start again (done), abort")


def test_boundary(d):
    good = {"session": "s1", "path": "sync/clock.json", "size": 10, "sha256": "0" * 64, "kind": "sync",
            "content_type": "application/json"}
    bad_starts = [{"session": "S1"}, {"session": "a/b"}, {"path": "../x"}, {"path": "/sync/x"}, {"path": "sync//x"},
                  {"path": "sync/./x"}, {"path": "sync\\x"}, {"path": "sync/a\nb"}, {"path": "other/x"}, {"path": "sync"},
                  {"size": -1}, {"size": "10"}, {"size": True}, {"size": 1.5}, {"sha256": "A" * 64}, {"sha256": "0" * 63},
                  {"kind": "original"}, {"content_type": "text/plain"}]
    with Server(d) as s:
        assert "upload_id" in s.ok("POST", "/uploads/start", good)
        for change in bad_starts:
            status, answer = s.call("POST", "/uploads/start", {**good, **change})
            assert status == 400 and answer["error"], f"{change}: {status} {answer}"
        up = s.ok("POST", "/uploads/start", good)
        one = {"n": 1, "sha256_b64": sha_b64(b"x")}
        for parts in ([], [one] * 2, [{**one, "n": i} for i in range(1, 34)], [{**one, "n": 0}], [{**one, "n": 10_001}],
                      [{**one, "sha256_b64": "abc"}], [{**one, "sha256_b64": base64.b64encode(b"short").decode()}], "x"):
            status, _ = s.call("POST", "/uploads/links", {"key": up["key"], "upload_id": up["upload_id"], "parts": parts})
            assert status == 400, f"links {parts!r:.60}: {status}"
        assert len(s.ok("POST", "/uploads/links", {"key": up["key"], "upload_id": up["upload_id"],
                                                    "parts": [{**one, "n": i} for i in range(1, 33)]})["links"]) == 32
        assert s.call("POST", "/uploads/links", {"key": up["key"], "upload_id": "has space", "parts": [one]})[0] == 400
        gap = [{"n": n, "etag": '"e"', "sha256_b64": sha_b64(b"x")} for n in (1, 3)]
        assert s.call("POST", "/uploads/complete", {"key": up["key"], "upload_id": up["upload_id"], "size": 2, "parts": gap})[0] == 400
        many = [{"n": n, "etag": '"e"', "sha256_b64": sha_b64(b"x")} for n in range(1, 10_002)]
        assert s.call("POST", "/uploads/complete", {"key": up["key"], "upload_id": up["upload_id"], "size": 2, "parts": many})[0] == 400
    print(f"PASS  the boundary refuses {len(bad_starts)} bad starts, bad link lists (0, 33, repeats, part 0 or 10,001, bad checksums), gaps and 10,001 parts")


def test_lab_walls(d):
    with Server(d) as s:
        mine = s.start("sync/clock.json", b"{}")
        theirs = s.start("sync/clock.json", b"{}", key=s.other)
        assert theirs["key"].startswith("labs/other/"), "the lab comes from the key, never from the client"
        for path, body in (("/uploads/links", {"parts": [{"n": 1, "sha256_b64": sha_b64(b"{}")}]}), ("/uploads/parts", {}),
                           ("/uploads/complete", {"size": 2, "parts": [{"n": 1, "etag": '"e"', "sha256_b64": sha_b64(b"{}")}]}),
                           ("/uploads/abort", {})):
            status, answer = s.call("POST", path, {"key": theirs["key"], "upload_id": theirs["upload_id"], **body})
            assert status == 403 and answer["error"], f"{path} with another lab's key: {status}"
            for bad_key in ("labs/dev/../other/sessions/s1/sync/clock.json", "labs/dev/sessions/S1/sync/clock.json", "labs/dev/x"):
                assert s.call("POST", path, {"key": bad_key, "upload_id": mine["upload_id"], **body})[0] == 400, f"{path} {bad_key}"
        assert s.call("POST", "/uploads/parts", {"key": "elsewhere/x", "upload_id": mine["upload_id"]})[0] == 403
        assert s.call("POST", "/uploads/parts", {"key": theirs["key"], "upload_id": theirs["upload_id"]}, key=s.other)[0] == 200
        status, answer = s.call("POST", "/uploads/parts", {"key": mine["key"], "upload_id": theirs["upload_id"]})
        assert status == 404, "an upload ID only works with its own key"
        req = urllib.request.Request(f"{s.url}/_s3/{theirs['key']}", headers={"Authorization": f"Bearer {DEV_KEY}"})
        assert send(req)[0] == 403
    print("PASS  a key outside its lab is 403 on links, parts, complete, abort and reads; a malformed key is 400")


def test_strict_pieces(d):
    a, b = os.urandom(5 * MIB), os.urandom(3000)
    with Server(d) as s:
        up = s.start("derived/orbbec/rec1/orbbec_depth.mp4", a + b)
        link = s.link(up, 1, a)
        assert put(link, a[:-1] + b"!")[0] == 400, "bytes that do not match the signed checksum"
        assert put(link, a, headers={"x-amz-checksum-sha256": sha_b64(b)})[0] == 403, "an altered checksum header"
        assert put(link, a, headers={})[0] == 403, "no checksum header"
        assert put(link, a, url=link["url"].replace("partNumber=1", "partNumber=2"))[0] == 403, "an altered link"
        assert put(link, a, url=re.sub(r"X-Expires=\d+", "X-Expires=9999999999", link["url"]))[0] == 403, "a longer expiry"
        assert put(link, a)[0] == 200
        status, headers = put(s.link(up, 2, b), b)
        etags = [s.ok("POST", "/uploads/parts", {"key": up["key"], "upload_id": up["upload_id"]})["parts"][0]["etag"], headers["ETag"]]
        status, answer = s.complete(up, [a, b], [etags[0], '"0123456789abcdef0123456789abcdef"'])
        assert status == 400 and "ETag" in answer["error"], answer
        status, answer = s.call("POST", "/uploads/complete", {"key": up["key"], "upload_id": up["upload_id"], "size": len(a + b),
                                                              "parts": [{"n": 1, "etag": etags[0], "sha256_b64": sha_b64(a)},
                                                                        {"n": 2, "etag": etags[1], "sha256_b64": sha_b64(a)}]})
        assert status == 400 and "checksum" in answer["error"], answer
        status, answer = s.complete(up, [a, b, b], etags + etags[1:])
        assert status == 400 and "bytes" in answer["error"], f"a part never uploaded: {answer}"
        assert s.complete({**up, "upload_id": "nope"}, [a, b], etags)[0] == 404
        assert s.complete(up, [a, b], etags)[0] == 200

        small = s.start("derived/orbbec/rec1/orbbec_frames.csv", b"x" * 2048)
        e1 = put(s.link(small, 1, b"x" * 1024), b"x" * 1024)[1]["ETag"]
        e2 = put(s.link(small, 2, b"x" * 1024), b"x" * 1024)[1]["ETag"]
        status, answer = s.complete(small, [b"x" * 1024] * 2, [e1, e2])
        assert status == 400 and "5 MiB" in answer["error"], answer
        gone = s.start("sync/board.csv", b"1,2\n")
        link = s.link(gone, 1, b"1,2\n")
        s.ok("POST", "/uploads/abort", {"key": gone["key"], "upload_id": gone["upload_id"]})
        assert put(link, b"1,2\n")[0] == 404, "a piece for a cancelled upload"
    print("PASS  strict as S3: bad piece 400, altered link or header 403, unknown upload 404; complete refuses a wrong ETag or checksum, a missing part and a small middle part")


def test_store_rules(d):
    """the rules S3 itself applies at complete, on the store alone, where the API's own checks cannot hide them"""
    st = store_fake.FakeStore(d, "http://127.0.0.1:1")
    up = st.create("labs/dev/sessions/s1/sync/x.json", "application/json", {"sha256": "0" * 64, "kind": "sync", "session": "s1"})
    data = b"y" * 100
    url, headers = st.presign_part("labs/dev/sessions/s1/sync/x.json", up, 1, sha_b64(data), 60)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    status, h, _ = st.put_part("labs/dev/sessions/s1/sync/x.json", q, headers["x-amz-checksum-sha256"], __import__("io").BytesIO(data), 100)
    assert status == 200
    part = {"n": 1, "etag": h["ETag"], "sha256_b64": sha_b64(data)}
    for parts, word in (([{**part, "n": n} for n in range(1, 10_002)], "10000"), ([part, {**part, "n": 2}], "never uploaded"),
                        ([{**part, "n": 2}, part], "ascending"), ([part, part], "ascending")):
        try:
            st.complete("labs/dev/sessions/s1/sync/x.json", up, parts)
            raise AssertionError(f"complete took {word}")
        except api.BadUpload as e:
            assert word in str(e), e
    try:
        st.list_parts("labs/dev/sessions/s1/sync/other.json", up)
        raise AssertionError("an upload ID with another key")
    except api.NoSuchUpload:
        pass
    assert st.complete("labs/dev/sessions/s1/sync/x.json", up, [part]) == {"size": 100}
    print("PASS  the store alone refuses over 10,000 parts, a part never uploaded, parts out of order or twice")


def test_restart(d):
    a, b = os.urandom(5 * MIB), os.urandom(10)
    with Server(d) as s:
        up = s.start("originals/cam2/cam2_part01.MOV", a + b)
        e1 = put(s.link(up, 1, a), a)[1]["ETag"]
    with Server(d) as s:
        assert s.ok("GET", "/sessions/s1/files")["uploading"][0]["upload_id"] == up["upload_id"]
        held = s.ok("POST", "/uploads/parts", {"key": up["key"], "upload_id": up["upload_id"]})["parts"]
        assert [(p["n"], p["etag"]) for p in held] == [(1, e1)], "the piece sent before the restart is still there"
        e2 = put(s.link(up, 2, b), b)[1]["ETag"]
        assert s.complete(up, [a, b], [e1, e2]) == (200, {"key": up["key"], "size": len(a) + 10})
    print("PASS  the server restarted in the middle of an upload: the upload, its first piece and the secret survive")


def test_faults(d):
    with Server(d / "f1", "put-500-every=2") as s:
        up = s.start("sync/decisions.json", b"{}")
        link = s.link(up, 1, b"{}")
        assert [put(link, b"{}")[0] for _ in range(4)] == [200, 500, 200, 500]
    with Server(d / "f2", "link-ttl-s=1") as s:
        up = s.start("sync/decisions.json", b"{}")
        link = s.link(up, 1, b"{}")
        time.sleep(2.2)
        assert put(link, b"{}")[0] == 403, "an expired link"
        assert put(s.link(up, 1, b"{}"), b"{}")[0] == 200, "a fresh link works"
    print("PASS  faults: put-500-every=2 fails every 2nd piece with 500, link-ttl-s=1 makes links expire")


def test_big_object(d):
    piece = b"\0" * (5 * MIB)
    pieces = [piece] * 13 + [b"end"]
    with Server(d) as s:
        up = s.start("originals/cam1/cam1_part02.MOV", b"".join(pieces))
        with ThreadPoolExecutor(3) as pool:      # the uploader keeps 3 pieces in flight
            answers = list(pool.map(lambda np: put(s.link(up, np[0], np[1]), np[1]), enumerate(pieces, 1)))
        assert all(status == 200 for status, _ in answers)
        assert s.complete(up, pieces, [h["ETag"] for _, h in answers])[0] == 200
        assert s.ok("GET", "/sessions/s1/files")["files"][0]["size"] == 65 * MIB + 3
        req = urllib.request.Request(f"{s.url}/_s3/{up['key']}", headers={"Authorization": f"Bearer {DEV_KEY}"})
        assert send(req)[0] == 404, "an object over 64 MiB keeps no bytes"
    kept = sum(f.stat().st_size for f in (Path(d) / "blobs").iterdir())
    assert kept == 0, f"{kept} bytes left on disk"
    print("PASS  14 pieces, 3 at a time, join into a 65 MiB object that keeps only its size and checksums: 0 bytes left on disk")


# ---- the S3 store, offline ----

def s3_store():
    import store_s3
    return store_s3, store_s3.S3Store("camrig-test-bucket", "us-east-1")


def test_presigned_part():
    store_s3, st = s3_store()
    assert store_s3.S3Store("b", "eu-west-1").s3.meta.region_name == "eu-west-1", "the region comes from the settings"
    url, headers = st.presign_part("labs/dev/sessions/s1/originals/cam1/a b.MOV", "UPLOAD-1", 7, sha_b64(b"x"), 600)
    u = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert u.scheme == "https" and u.hostname == "camrig-test-bucket.s3.us-east-1.amazonaws.com", url
    assert q["X-Amz-SignedHeaders"] == "host;x-amz-checksum-sha256", q["X-Amz-SignedHeaders"]
    assert (q["partNumber"], q["uploadId"], q["X-Amz-Expires"]) == ("7", "UPLOAD-1", "600")
    extra = [k for k in q if k.lower().startswith(("x-amz-sdk-checksum", "x-amz-checksum"))]
    assert not extra, f"checksum parameters the client would not send: {extra}"
    assert headers == {"x-amz-checksum-sha256": sha_b64(b"x")}
    import botocore
    print(f"PASS  presigned UploadPart (botocore {botocore.__version__}): signs host and x-amz-checksum-sha256 only, no default CRC32")


def test_s3_calls():
    from botocore.stub import Stubber
    store_s3, st = s3_store()
    key, bucket, t = "labs/dev/sessions/s1/sync/clock.json", "camrig-test-bucket", datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    meta = {"sha256": "a" * 64, "kind": "sync", "session": "s1"}
    with Stubber(st.s3) as stub:
        stub.add_response("list_objects_v2", {"Contents": [{"Key": key + ".old"}]}, {"Bucket": bucket, "Prefix": key, "MaxKeys": 1})
        assert st.head(key) is None, "a longer key is not this key"
        stub.add_response("list_objects_v2", {"Contents": [{"Key": key}]}, {"Bucket": bucket, "Prefix": key, "MaxKeys": 1})
        stub.add_response("head_object", {"ContentLength": 5, "Metadata": meta, "LastModified": t}, {"Bucket": bucket, "Key": key})
        assert st.head(key) == {"size": 5, "sha256": "a" * 64, "kind": "sync", "modified": "2026-11-08T12:00:00Z"}
        stub.add_response("create_multipart_upload", {"UploadId": "U1"},
                          {"Bucket": bucket, "Key": key, "ContentType": "application/json", "Metadata": meta, "ChecksumAlgorithm": "SHA256"})
        assert st.create(key, "application/json", meta) == "U1"
        page = {"Bucket": bucket, "Key": key, "UploadId": "U1"}
        stub.add_response("list_parts", {"Parts": [{"PartNumber": 1, "Size": 9, "ETag": '"e1"', "ChecksumSHA256": "c1"}],
                                         "IsTruncated": True, "NextPartNumberMarker": 1}, {**page, "PartNumberMarker": 0})
        stub.add_response("list_parts", {"Parts": [{"PartNumber": 2, "Size": 3, "ETag": '"e2"', "ChecksumSHA256": "c2"}],
                                         "IsTruncated": False}, {**page, "PartNumberMarker": 1})
        assert [p["n"] for p in st.list_parts(key, "U1")] == [1, 2], "ListParts pages are followed"
        stub.add_client_error("list_parts", "NoSuchUpload", http_status_code=404)
        try:
            st.list_parts(key, "U9")
            raise AssertionError("NoSuchUpload must become api.NoSuchUpload")
        except api.NoSuchUpload:
            pass
        parts = [{"n": 1, "etag": '"e1"', "sha256_b64": "c1"}, {"n": 2, "etag": '"e2"', "sha256_b64": "c2"}]
        stub.add_response("complete_multipart_upload", {}, {**page, "MultipartUpload": {"Parts": [
            {"PartNumber": 1, "ETag": '"e1"', "ChecksumSHA256": "c1"}, {"PartNumber": 2, "ETag": '"e2"', "ChecksumSHA256": "c2"}]}})
        stub.add_response("head_object", {"ContentLength": 12}, {"Bucket": bucket, "Key": key})
        assert st.complete(key, "U1", parts) == {"size": 12}
        stub.add_client_error("complete_multipart_upload", "EntityTooSmall", "Your proposed upload is smaller than the minimum")
        try:
            st.complete(key, "U1", parts)
            raise AssertionError("EntityTooSmall must become api.BadUpload")
        except api.BadUpload as e:
            assert "minimum" in str(e)
        stub.add_response("abort_multipart_upload", {}, page)
        st.abort(key, "U1")
        prefix = "labs/dev/sessions/s1/"
        stub.add_response("list_objects_v2", {"Contents": [{"Key": key, "Size": 5}]}, {"Bucket": bucket, "Prefix": prefix})
        stub.add_response("list_multipart_uploads", {"Uploads": [{"Key": prefix + "manifest.json", "UploadId": "U2", "Initiated": t}]},
                          {"Bucket": bucket, "Prefix": prefix})
        stub.add_response("head_object", {"ContentLength": 5, "Metadata": meta, "LastModified": t}, {"Bucket": bucket, "Key": key})
        objects, uploads = st.list_prefix(prefix)
        assert objects == [{"key": key, "size": 5, "sha256": "a" * 64, "kind": "sync", "modified": "2026-11-08T12:00:00Z"}]
        assert uploads == [{"key": prefix + "manifest.json", "upload_id": "U2", "started": "2026-11-08T12:00:00Z"}]
        stub.assert_no_pending_responses()
    print("PASS  every S3 call (ListObjectsV2, HeadObject, Create/Complete/AbortMultipartUpload, ListParts, ListMultipartUploads) matches the S3 model")


def test_s3_backup():
    from botocore.stub import ANY, Stubber
    from botocore.response import StreamingBody
    import io
    store_s3, st = s3_store()
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "a.db", Path(tmp) / "b.db"
        src.write_bytes(b"database bytes")
        with Stubber(st.s3) as stub:
            stub.add_response("put_object", {}, {"Bucket": "camrig-test-bucket", "Key": "backups/perception.db", "Body": ANY})
            st.put_backup(src)
            listing = {"Bucket": "camrig-test-bucket", "Prefix": "backups/perception.db", "MaxKeys": 1}
            stub.add_response("list_objects_v2", {"Contents": [{"Key": "backups/perception.db"}]}, listing)
            stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"database bytes"), 14)},
                              {"Bucket": "camrig-test-bucket", "Key": "backups/perception.db"})
            assert st.get_backup(dst) and dst.read_bytes() == b"database bytes"
            stub.add_response("list_objects_v2", {}, listing)
            assert st.get_backup(dst) is False, "no copy in S3"
            stub.add_client_error("list_objects_v2", "AccessDenied", http_status_code=403)
            try:
                st.get_backup(dst)
                raise AssertionError("AccessDenied must not be taken for no copy")
            except store_s3.ClientError:
                pass
            stub.assert_no_pending_responses()
    print("PASS  S3 backup: PutObject and GetObject of backups/perception.db; an empty listing is no copy, AccessDenied an error")


# ---- sign-in, the admin pages and the commands ----

def signin_status(s, email: str, password: str, address: str = "10.0.0.1") -> tuple[int, dict]:
    return s.call("POST", "/auth/signin", {"email": email, "password": password, "computer": "lab-pc"}, key=None,
                  headers={"X-Forwarded-For": address})


def test_signin(d):
    with Server(d) as s:
        got = s.signin("Lab@Example.com", "lab-dev-password", "lab-mac")
        assert re.fullmatch(r"fpp_[A-Za-z0-9_-]{43}", got["token"]), got["token"]
        assert {k: got[k] for k in ("email", "name", "lab")} == {"email": "lab@example.com", "name": "Lab person", "lab": "dev"}
        token = got["token"]
        assert s.ok("GET", "/auth/me", key=token) == {"email": "lab@example.com", "name": "Lab person", "lab": "dev"}
        assert s.ok("GET", "/health", key=token) == {"ok": True, "lab": "dev"}
        db = s.db()
        row = db.execute("SELECT * FROM tokens WHERE hash = ?", (sha_hex(token.encode()),)).fetchone()
        assert (row["kind"], row["label"], row["expires_at"]) == ("app", "lab-mac", None), dict(row)
        assert not db.execute("SELECT 1 FROM tokens WHERE hash = ? OR hash = ?", (token, token[4:])).fetchone(), "only the hash is kept"
        stored = db.execute("SELECT password FROM users WHERE email = 'lab@example.com'").fetchone()[0]
        assert re.fullmatch(r"scrypt\$15\$8\$3\$[A-Za-z0-9+/]{22}==\$[A-Za-z0-9+/]{43}=", stored), stored
        assert s.ok("POST", "/auth/signout", key=token) == {}
        assert s.call("GET", "/auth/me", key=token) == (401, {"error": "signed out: sign in again"}), "signed out"
        assert s.ok("GET", "/health") == {"ok": True, "lab": "dev"}, "signing one computer out leaves the others"

        s.person("dev", "invited@example.com", None)
        gone = s.person("dev", "gone@example.com", "gone-password-1")
        users.set_disabled(db, gone, True)
        failures = {"unknown email": ("nobody@example.com", "lab-dev-password"), "no password yet": ("invited@example.com", "lab-dev-password"),
                    "disabled": ("gone@example.com", "gone-password-1"), "wrong password": ("lab@example.com", "lab-dev-passwore"),
                    "short password": ("lab@example.com", "short")}
        for why, (email, password) in failures.items():
            assert signin_status(s, email, password) == (401, {"error": "wrong email or password"}), why
        assert s.call("POST", "/auth/signin", {"email": 1, "password": "x"}, key=None)[0] == 400

        dis = s.person("dev", "soon@example.com", "soon-disabled-1")
        t = s.signin("soon@example.com", "soon-disabled-1")["token"]
        assert s.ok("GET", "/auth/me", key=t)["email"] == "soon@example.com"
        users.set_disabled(db, dis, True)
        assert s.call("GET", "/auth/me", key=t)[0] == 401, "a disabled person's token"
        assert s.call("POST", "/uploads/parts", {}, key=t)[0] == 401
        users.set_disabled(db, dis, False)
        assert s.call("GET", "/auth/me", key=t)[0] == 401, "enabling again does not bring the old tokens back"
        db.close()
    print(f"PASS  sign-in, me and sign-out; one 401 'wrong email or password' for {', '.join(failures)}; a disabled person's token is 401; scrypt$15$8$3$ stored")


def test_rate_limits(d):
    with Server(d) as s:
        for i in range(10):
            assert signin_status(s, "lab@example.com", "wrong-password-x", f"10.1.0.{i}")[0] == 401
        status, answer = signin_status(s, "lab@example.com", "lab-dev-password", "10.1.0.99")
        assert status == 429, f"the 11th try for one email, with the right password: {status} {answer}"
        for i in range(30):
            assert signin_status(s, f"p{i}@example.com", "short", "10.2.0.1")[0] == 401
        assert signin_status(s, "admin@example.com", "admin-dev-password", "10.2.0.1")[0] == 429, "the 31st try from one address"
        assert signin_status(s, "admin@example.com", "admin-dev-password", "10.2.0.2")[0] == 200, "another address"
        status, _, _ = s.form("/admin/signin", {"email": "lab@example.com", "password": "lab-dev-password"},
                              **{"X-Forwarded-For": "10.3.0.1"})
        assert status == 429, "the admin sign-in shares the limits"
    print("PASS  429 after 10 failed sign-ins for one email (any address) and after 30 from one address, checked before hashing")


def setup_token(link: str) -> str:
    return parse_qs(urlsplit(link).query)["t"][0]


def test_set_password(d):
    with Server(d) as s:
        uid = s.person("dev", "new@example.com", None)
        db = s.db()
        link = users.setup_link(db, uid, s.url)
        assert link.startswith(s.url + "/set-password?t=fpp_")
        t = setup_token(link)
        status, headers, body = s.raw("GET", "/set-password?t=" + t)
        assert status == 200 and b'name="again"' in body and headers["Content-Security-Policy"]
        good = {"t": t, "password": "a-good-new-password", "again": "a-good-new-password"}
        assert s.form("/set-password", {**good, "again": "something-else-1"})[0] == 400, "the two passwords differ"
        assert s.form("/set-password", {**good, "password": "short", "again": "short"})[0] == 400, "too short"
        assert s.form("/set-password", good, origin="https://evil.example")[0] == 403, "a foreign Origin"
        assert s.form("/set-password", good, origin=None)[0] == 403, "no Origin"
        assert s.form("/set-password", good)[0] == 200
        assert s.form("/set-password", good)[0] == 410, "a link works once"
        assert s.raw("GET", "/set-password?t=" + t)[0] == 410
        assert s.signin("new@example.com", "a-good-new-password")["lab"] == "dev"
        t2 = setup_token(users.setup_link(db, uid, s.url))
        t3 = setup_token(users.setup_link(db, uid, s.url))
        assert s.form("/set-password", {**good, "t": t2})[0] == 410, "a newer link replaces the older"
        db.execute("UPDATE tokens SET expires_at = ? WHERE hash = ?", (users.now(-1), sha_hex(t3.encode())))
        assert s.form("/set-password", {**good, "t": t3})[0] == 410, "an expired link"
        db.close()
    print("PASS  a set-password link: the password twice, 12 characters or more, own Origin only, works once, replaced by a newer one, expires")


def admin_signin(s, https: bool = False) -> tuple[str, str]:
    status, headers, _ = s.form("/admin/signin", {"email": "admin@example.com", "password": "admin-dev-password"},
                                **({"X-Forwarded-Proto": "https"} if https else {}))
    assert status == 303 and headers["Location"] == "/admin", status
    cookie = headers["Set-Cookie"]
    return re.match(r"fpp_admin=([^;]+)", cookie).group(1), cookie


def test_admin(d):
    with Server(d) as s:
        assert s.raw("GET", "/admin")[:2][0] == 303, "no cookie: to the sign-in"
        token, cookie = admin_signin(s, https=True)
        assert set(cookie.split("; ")[1:]) == {"HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=43200", "Secure"}, cookie
        token, cookie = admin_signin(s)
        assert "Secure" not in cookie, "plain HTTP from the stand-in: no Secure"
        assert s.form("/admin/signin", {"email": "lab@example.com", "password": "lab-dev-password"})[0] == 401, "not an admin"
        assert s.form("/admin/signin", {"email": "admin@example.com", "password": "admin-dev-password"}, origin="https://evil.example")[0] == 403

        status, headers, body = s.raw("GET", "/admin", headers={"Cookie": f"fpp_admin={token}"})
        page = body.decode()
        assert status == 200 and all(h in page for h in ("Uploads by session", "People", "Labs", "Computers")), page[:300]
        assert "<script" not in page, "no JavaScript"
        for path in ("/admin", "/admin/signin", "/set-password?t=x", "/admin/style.css", "/health"):
            _, h, _ = s.raw("GET", path, headers={"Cookie": f"fpp_admin={token}"})
            assert h["Content-Security-Policy"] == "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'", path
            assert (h["Referrer-Policy"], h["X-Content-Type-Options"], h["Cache-Control"]) == ("same-origin", "nosniff", "no-store"), \
                f"{path}: no-referrer would make every browser send Origin: null, so every form would be refused"
        csrf = re.search(r'name="csrf" value="([0-9a-f]{64})"', page).group(1)

        lab = {"name": "lab-b", "title": "Lab B", "csrf": csrf}
        assert s.form("/admin/add-lab", {k: v for k, v in lab.items() if k != "csrf"}, token)[0] == 403, "no CSRF field"
        assert s.form("/admin/add-lab", {**lab, "csrf": "0" * 64}, token)[0] == 403, "a wrong CSRF field"
        assert s.form("/admin/add-lab", lab, token, origin="https://evil.example")[0] == 403, "a foreign Origin"
        assert s.form("/admin/add-lab", lab, token, origin=None)[0] == 403, "no Origin"
        assert s.form("/admin/add-lab", lab, token, origin="https://\u00e9.example")[0] == 403, "a non-ASCII Origin"
        assert s.form("/admin/add-lab", {**lab, "csrf": "\u00e9" * 64}, token)[0] == 403, "a non-ASCII CSRF field"
        db = s.db()
        assert not db.execute("SELECT 1 FROM labs WHERE name = 'lab-b'").fetchone(), "nothing refused was done"
        status, headers, _ = s.form("/admin/add-lab", lab, token)
        assert status == 303 and db.execute("SELECT title FROM labs WHERE name = 'lab-b'").fetchone()[0] == "Lab B"

        hostile = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
        status, _, body = s.form("/admin/add-user", {"email": "evil@example.com", "name": hostile, "lab": "lab-b", "csrf": csrf}, token)
        page = body.decode()
        link = re.search(r"(http://127\.0\.0\.1:\d+/set-password\?t=fpp_[A-Za-z0-9_-]+)", page)
        assert status == 200 and link, page[-600:]
        page = s.raw("GET", "/admin", headers={"Cookie": f"fpp_admin={token}"})[2].decode()
        assert hostile not in page and "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&gt;&lt;img" in page, "the name is escaped"
        assert link.group(1) not in page, "the link is shown once"
        assert s.form("/set-password", {"t": setup_token(link.group(1)), "password": "evil-password-1", "again": "evil-password-1"})[0] == 200

        evil = db.execute("SELECT id FROM users WHERE email = 'evil@example.com'").fetchone()[0]
        t = s.signin("evil@example.com", "evil-password-1", "evil-pc")["token"]
        computer = db.execute("SELECT hash FROM tokens WHERE kind = 'app' AND label = 'evil-pc'").fetchone()[0]
        assert s.form("/admin/signout-computer", {"computer": computer, "csrf": csrf}, token)[0] == 303
        assert s.call("GET", "/auth/me", key=t)[0] == 401, "an admin signed the computer out"
        t = s.signin("evil@example.com", "evil-password-1", "evil-pc")["token"]
        assert s.form("/admin/disable", {"user": evil, "csrf": csrf}, token)[0] == 303
        assert s.call("GET", "/auth/me", key=t)[0] == 401, "disabled from the admin page"
        assert db.execute("SELECT disabled_at IS NOT NULL FROM users WHERE id = ?", (evil,)).fetchone()[0] == 1
        assert s.form("/admin/enable", {"user": evil, "csrf": csrf}, token)[0] == 303
        status, _, body = s.form("/admin/link", {"user": evil, "csrf": csrf}, token)
        assert status == 200 and b"/set-password?t=fpp_" in body
        assert s.form("/admin/add-lab", {"name": "Bad Name", "title": "x", "csrf": csrf}, token)[0] == 400

        status, headers, _ = s.form("/admin/signout", {"csrf": csrf}, token)
        assert status == 303 and "Max-Age=0" in headers["Set-Cookie"]
        assert s.raw("GET", "/admin", headers={"Cookie": f"fpp_admin={token}"})[0] == 303, "signed out"
        db.execute("UPDATE tokens SET expires_at = ? WHERE kind = 'admin'", (users.now(-1),))
        token2, _ = admin_signin(s)
        db.execute("UPDATE tokens SET expires_at = ? WHERE hash = ?", (users.now(-1), sha_hex(token2.encode())))
        assert s.raw("GET", "/admin", headers={"Cookie": f"fpp_admin={token2}"})[0] == 303, "an admin cookie expires"
        db.close()
    print("PASS  admin: cookie HttpOnly SameSite=Strict Path=/ 12 h (Secure over HTTPS); forms refused without CSRF or with a foreign or no Origin;"
          " a hostile name escaped; link shown once; disable, enable, computer sign-out, sign-out; the four security headers on every page")


def test_upload_rows(d):
    data = b'{"made": 1}'
    with Server(d) as s:
        up = s.start("manifest.json", data)
        db = s.db()
        rows = lambda: [dict(r) for r in db.execute("SELECT * FROM uploads ORDER BY id")]
        r = rows()[0]
        assert (r["lab"], r["session"], r["path"], r["kind"], r["size"], r["upload_id"], r["computer"], r["status"], r["finished_at"]) == \
            ("dev", "s1", "manifest.json", "manifest", len(data), up["upload_id"], "dev", "uploading", None), r
        assert r["user_id"] == users.user_id(db, "lab@example.com")
        status, headers = put(s.link(up, 1, data), data)
        assert s.complete(up, [data], [headers["ETag"]])[0] == 200
        assert (rows()[0]["status"], bool(rows()[0]["finished_at"])) == ("done", True)
        assert s.start("manifest.json", data) == {"done": True, "key": up["key"]}
        assert {k: rows()[1][k] for k in ("status", "upload_id")} == {"status": "already_there", "upload_id": None}
        again = s.start("manifest.json", b"{}", key=s.other)
        s.ok("POST", "/uploads/abort", {"key": again["key"], "upload_id": again["upload_id"]}, key=s.other)
        r = rows()[2]
        assert (r["lab"], r["status"], r["computer"], bool(r["finished_at"])) == ("other", "aborted", "other-mac", True), r
        db.close()
        page = s.raw("GET", "/admin", headers={"Cookie": f"fpp_admin={admin_signin(s)[0]}"})[2].decode()
        assert "<td>s1</td><td>1 of 1</td>" in page and "<td>complete</td>" in page and "<td>in progress</td>" in page, "uploads by session"
    print("PASS  uploads rows: start 'uploading' (person and computer), complete 'done', start again 'already_there', abort 'aborted'; the admin table reads them")


def run_server_command(config: Path, store_dir: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "PERCEPTION_CONFIG": str(config), "CAMRIG_STORE": "fake", "CAMRIG_CLOUD_DEV_DIR": str(store_dir)}
    return subprocess.run([sys.executable, str(HERE / "server.py"), *args], capture_output=True, text=True, env=env, timeout=60)


def test_commands(d):
    db_path = d / "var" / "perception.db"
    config = d / "config.json"
    config.write_text(json.dumps({"bucket": "b", "region": "us-east-1", "public_url": "https://perception.example", "db": str(db_path)}))
    run = lambda *a: run_server_command(config, d / "store", *a)
    assert run("migrate").returncode == 0
    db = users.connect(db_path)
    secret = users.setting(db, "csrf_secret")
    assert run("migrate").returncode == 0, "migrate twice"
    assert [tuple(r) for r in db.execute("SELECT version FROM schema_version")] == [(1,)] and users.setting(db, "csrf_secret") == secret
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert run("add-lab", "lab-c", "Lab C").returncode == 0
    assert run("add-lab", "lab-c", "Lab C").returncode != 0, "a lab twice"
    r = run("add-user", "--email", "c@example.com", "--lab", "lab-c", "--name", "C", "--admin")
    assert r.returncode == 0 and re.fullmatch(r"https://perception\.example/set-password\?t=fpp_[A-Za-z0-9_-]{43}\n", r.stdout), r
    r2 = run("link", "--email", "c@example.com")
    assert r2.returncode == 0 and r2.stdout != r.stdout
    assert run("link", "--email", "nobody@example.com").returncode != 0
    db.close()

    r = run("backup")
    assert r.returncode == 0, r.stderr
    assert (d / "store" / "backups" / "perception.db").exists()
    r = run("restore")
    assert r.returncode != 0 and "exists" in r.stderr, "restore refuses when a database is there"
    for f in db_path.parent.iterdir():
        f.unlink()
    r = run("restore")
    assert r.returncode == 0 and "restored" in r.stdout, r
    db = users.connect(db_path)
    assert tuple(db.execute("SELECT u.lab, u.admin, l.title FROM users u JOIN labs l ON l.name = u.lab WHERE u.email = 'c@example.com'").fetchone()) == ("lab-c", 1, "Lab C"), "the restored database holds the rows made before the backup"
    db.close()
    for f in db_path.parent.iterdir():
        f.unlink()
    r = run_server_command(config, d / "empty-store", "restore")
    assert r.returncode == 0 and "no copy" in r.stdout and not db_path.exists(), r
    r = run("backup")
    assert r.returncode != 0 and not db_path.exists(), "backup with no database makes none and copies nothing"
    print("PASS  commands: migrate twice (one schema row, csrf_secret kept, WAL), add-lab, add-user and link print a set-password link;"
          " backup, then restore only into an empty place; restore with no copy says so and exits 0")


def test_log_and_body_limit(d):
    with Server(d) as s:
        secret = "fpp_secret-in-the-query"
        s.raw("GET", f"/set-password?t={secret}")
        s.call("GET", f"/health?key={secret}")
        s.call("GET", "/auth/me", key=secret)
        s.signin("lab@example.com", "lab-dev-password")
        with socket.create_connection(("127.0.0.1", s.port), timeout=10) as c:
            c.sendall(f"POST /uploads/start HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {DEV_KEY}\r\n"
                      f"Content-Type: application/json\r\nContent-Length: {3 * MIB}\r\n\r\n".encode())
            head = c.recv(200)
        assert re.match(rb"HTTP/1\.[01] 413 ", head), f"a 3 MiB body, unsent: {head!r}"
        assert s.call("POST", "/auth/signin", b'{"email": "lab@example.com", "password": "lab-dev-password", "pad": "' + b"a" * (2 * MIB - 200) + b'"}', key=None)[0] == 200, "just under 2 MiB"
    time.sleep(0.2)
    lines = s.log.read_text().splitlines()
    assert lines and all(re.fullmatch(r"(GET|POST|PUT) /[\x21-\x7e]* \d{3}", line) for line in lines), lines
    text = s.log.read_text()
    for word in (secret, "?", DEV_KEY, "lab-dev-password", "Bearer", s.other):
        assert word not in text, f"{word!r} in the log"
    assert "GET /set-password 410" in lines and "POST /uploads/start 413" in lines, lines
    print(f"PASS  {len(lines)} log lines, each only method, path and status (no query string, token or password); a body over 2 MiB is refused unread with 413")


def test_server_on_s3(d):
    """the server's own wiring on the S3 store: CAMRIG_STORE unset, bucket and region from the settings"""
    from botocore.stub import Stubber
    os.environ.pop("CAMRIG_STORE", None)
    st = server.open_store({"bucket": "camrig-test-bucket", "region": "eu-central-1"})
    assert st.s3.meta.region_name == "eu-central-1"
    db = users.connect(d / "perception.db")
    users.migrate(db)
    users.add_lab(db, "lab-a", "Lab A")
    uid = users.add_user(db, "a@example.com", "lab-a")
    key = "labs/lab-a/sessions/s1/manifest.json"
    with Stubber(st.s3) as stub:
        stub.add_response("list_objects_v2", {}, {"Bucket": "camrig-test-bucket", "Prefix": key, "MaxKeys": 1})
        stub.add_response("create_multipart_upload", {"UploadId": "U7"})
        who = {"id": uid, "lab": "lab-a", "label": "pc-a"}
        body = {"session": "s1", "path": "manifest.json", "size": 2, "sha256": "b" * 64, "kind": "manifest", "content_type": "application/json"}
        assert api.start(who, body, st, db) == {"upload_id": "U7", "key": key}
        stub.assert_no_pending_responses()
    assert db.execute("SELECT status, upload_id, computer FROM uploads").fetchone()[:] == ("uploading", "U7", "pc-a")
    db.close()
    print("PASS  the server on the S3 store: region from the settings, a start goes to S3 and is recorded")


def main():
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, test in enumerate([test_keys_and_routes, test_round_trip, test_boundary, test_lab_walls, test_strict_pieces,
                                  test_store_rules, test_restart, test_faults, test_big_object, test_signin,
                                  test_rate_limits, test_set_password, test_admin, test_upload_rows, test_commands,
                                  test_log_and_body_limit, test_server_on_s3]):
            folder = Path(tmp) / str(i)
            folder.mkdir()
            failed += run(test, folder)
    for test in (test_presigned_part, test_s3_calls, test_s3_backup):
        failed += run(test)
    print("FAIL" if failed else "PASS  all cloud checks", f"({failed} failed)" if failed else "")
    sys.exit(1 if failed else 0)


def run(test, *args) -> int:
    try:
        test(*args)
        return 0
    except Exception:
        print(f"FAIL  {test.__name__}\n{traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    main()
