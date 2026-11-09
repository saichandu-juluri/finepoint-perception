"""FinePoint Perception's server (docs/SERVER.md): the upload calls of docs/UPLOAD.md, sign-in and the admin pages,
on 127.0.0.1:8080 behind Caddy.

    python server.py serve [--listen 127.0.0.1:8080]
    python server.py migrate
    python server.py add-lab <name> <title>
    python server.py add-user --email E --lab L [--name N] [--admin]      prints the set-password link
    python server.py link --email E                                       prints a new set-password link
    python server.py backup                                               the database to <bucket>/backups/perception.db
    python server.py restore                                              that copy back, only when no database exists

Settings: /etc/perception/config.json {bucket, region, public_url} and optionally db (default
/var/lib/perception/perception.db); PERCEPTION_CONFIG names another file. CAMRIG_STORE=fake uses the local stand-in for
S3 in CAMRIG_CLOUD_DEV_DIR. One line per request goes to stderr: the method, the path without its query string and the
status, never a body, a header or a token. Standard library only, apart from boto3 inside store_s3.
"""
import argparse
import hmac
import json
import os
import re
import sqlite3
import sys
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import admin
import api
import users

MAX_BODY = 2 * 1024**2        # a 10,000-part /uploads/complete is about 1.2 MB
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'",
    "Referrer-Policy": "same-origin",   # no-referrer makes browsers send "Origin: null" on form posts
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
SIGNED_OUT = "signed out: sign in again"
UNPRINTABLE = re.compile(r"[^\x21-\x7e]")


def load_settings() -> dict:
    path = os.environ.get("PERCEPTION_CONFIG", "/etc/perception/config.json")
    settings = json.loads(Path(path).read_text())
    settings.setdefault("db", "/var/lib/perception/perception.db")
    return settings


def open_store(settings: dict):
    if os.environ.get("CAMRIG_STORE", "s3") == "fake":
        import store_fake
        return store_fake.FakeStore(os.environ["CAMRIG_CLOUD_DEV_DIR"], settings.get("public_url", "http://127.0.0.1:1"))
    import store_s3
    return store_s3.S3Store(settings["bucket"], settings["region"])


def origin_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


class Handler(BaseHTTPRequestHandler):
    """the server's handler; self.server carries settings (db, public_url) and store"""
    protocol_version = "HTTP/1.1"

    def log_request(self, code="-", size="-"):
        path = UNPRINTABLE.sub("?", urlsplit(getattr(self, "path", "") or "").path)[:300]
        print(f"{self.command or '-'} {path or '-'} {int(code) if str(code).isdigit() else code}", file=sys.stderr,
              flush=True)

    def log_message(self, *args):
        pass

    # ---- what every route uses ----

    def reply(self, status: int, headers: dict, body: bytes):
        self.send_response(status)
        for k, v in {**SECURITY_HEADERS, **headers}.items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def reply_json(self, status: int, data: dict):
        self.reply(status, {"Content-Type": "application/json"}, json.dumps(data).encode())

    def length(self) -> int | None:
        try:
            n = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            return None
        return n if n >= 0 else None

    def read_body(self) -> bytes:
        """the request's body, at most 2 MiB; a refusal closes the connection, so unread bytes are never taken for
        the next request"""
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise api.ApiError(411, "send the body with a Content-Length")
        n = self.length()
        if n is None and self.headers["Content-Length"] is not None:
            self.close_connection = True
            raise api.ApiError(400, "Content-Length must be a whole number of bytes")
        if (n or 0) > MAX_BODY:
            self.close_connection = True
            raise api.ApiError(413, "the body is over 2 MiB")
        return self.rfile.read(n) if n else b""

    def peer_is_caddy(self) -> bool:
        return self.client_address[0] == "127.0.0.1"

    def client(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if self.peer_is_caddy() and forwarded:
            return forwarded.split(",")[-1].strip()
        return self.client_address[0]

    def https(self) -> bool:
        return self.peer_is_caddy() and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def same_origin(self) -> bool:
        got = self.headers.get("Origin")
        return bool(got) and hmac.compare_digest(got.rstrip("/").lower().encode(),
                                                origin_of(self.server.settings["public_url"]).encode())

    def bearer(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth.startswith("Bearer ") else None

    def db(self):
        return users.connect(self.server.settings["db"])

    # ---- dispatch ----

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def dispatch(self):
        path = urlsplit(self.path).path
        db = None
        try:
            db = self.db()
            if path in ("/admin", "/set-password") or path.startswith("/admin/"):
                if self.command == "POST":
                    form = self.read_body()
                else:
                    form = b""
                return admin.handle(self, db, path, form)
            return self.reply_json(200, self.api(db, path))
        except api.ApiError as e:
            if self.command != "GET" and not self.close_connection and e.status in (401, 404):
                self.close_connection = True        # the body was never read
            self.reply_json(e.status, {"error": str(e)})
        except api.NoSuchUpload:
            self.reply_json(404, {"error": "no such upload: it was cancelled or cleaned up, start the file again"})
        except api.BadUpload as e:
            self.reply_json(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self.close_connection = True
            self.reply_json(500, {"error": f"the server failed ({type(e).__name__}); try again later"})
        finally:
            if db is not None:
                db.close()

    def api(self, db, path: str) -> dict:
        method = self.command
        m = api.FILES.fullmatch(path)
        known = (method, path) in api.ROUTES or (method, path) in (
            ("GET", "/health"), ("POST", "/auth/signin"), ("POST", "/auth/signout"), ("GET", "/auth/me")) or (
            method == "GET" and m)
        if not known:
            raise api.ApiError(404, f"there is no call {method} {path}")
        body = self.json_body() if method == "POST" else {}
        if path == "/auth/signin":
            return self.signin(db, body)
        who = users.person_of(db, self.bearer(), "app")
        if path == "/health":
            return {"ok": True, "lab": who["lab"]} if who else {"ok": True}
        if who is None:
            raise api.ApiError(401, SIGNED_OUT)
        if path == "/auth/signout":
            users.revoke(db, who["token"])
            return {}
        if path == "/auth/me":
            return {"email": who["email"], "name": who["name"], "lab": who["lab"]}
        if m:
            return api.files(who, {"session": m.group(1)}, self.server.store, db)
        return api.ROUTES[(method, path)](who, body, self.server.store, db)

    def json_body(self) -> dict:
        raw = self.read_body()
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            api.bad("the body must be JSON")
        if not isinstance(body, dict):
            api.bad("the body must be a JSON object")
        return body

    def signin(self, db, body: dict) -> dict:
        email, password, computer = body.get("email"), body.get("password"), body.get("computer", "")
        if not all(isinstance(v, str) for v in (email, password, computer)) or len(computer) > 200:
            api.bad("send email, password and computer as text")
        try:
            row = users.signin(db, email, password, self.client())
        except users.TooManyTries:
            raise api.ApiError(429, "too many failed sign-ins: wait 15 minutes")
        if row is None:
            raise api.ApiError(401, "wrong email or password")
        token = users.new_token(db, row["id"], "app", UNPRINTABLE.sub(" ", computer).strip())
        return {"token": token, "email": row["email"], "name": row["name"], "lab": row["lab"]}


def make_server(listen: tuple[str, int], settings: dict, store=None, handler=Handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(listen, handler)
    server.daemon_threads = True
    server.settings, server.store = settings, store
    return server


# ---- commands ----

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--listen", default="127.0.0.1:8080")
    sub.add_parser("migrate")
    lab = sub.add_parser("add-lab")
    lab.add_argument("name")
    lab.add_argument("title")
    user = sub.add_parser("add-user")
    user.add_argument("--email", required=True)
    user.add_argument("--lab", required=True)
    user.add_argument("--name", default="")
    user.add_argument("--admin", action="store_true")
    link = sub.add_parser("link")
    link.add_argument("--email", required=True)
    sub.add_parser("backup")
    sub.add_parser("restore")
    a = ap.parse_args(argv)
    settings = load_settings()

    if a.command == "restore":
        return restore(settings)
    if a.command == "backup":
        return backup(settings)
    Path(settings["db"]).parent.mkdir(parents=True, exist_ok=True)
    db = users.connect(settings["db"])
    try:
        users.migrate(db)
        if a.command == "migrate":
            print(f"schema {users.SCHEMA_VERSION}: {settings['db']}")
        elif a.command == "add-lab":
            users.add_lab(db, a.name, a.title)
            print(f"lab {a.name} added")
        elif a.command == "add-user":
            uid = users.add_user(db, a.email, a.lab, a.name, a.admin)
            print(users.setup_link(db, uid, settings["public_url"]))
        elif a.command == "link":
            print(users.setup_link(db, users.user_id(db, a.email), settings["public_url"]))
        elif a.command == "serve":
            db.close()
            host, _, port = a.listen.rpartition(":")
            server = make_server((host, int(port)), settings, open_store(settings))
            print(f"serving on {a.listen}", file=sys.stderr, flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except users.Refused as e:
        sys.exit(str(e))
    finally:
        db.close()


def backup(settings: dict):
    if not Path(settings["db"]).exists():
        sys.exit(f"{settings['db']} does not exist: nothing backed up")    # never an empty copy over the real one
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "perception.db"
        src, dst = users.connect(settings["db"]), sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        open_store(settings).put_backup(copy)
    print("backed up to backups/perception.db")


def restore(settings: dict):
    db = Path(settings["db"])
    if db.exists():
        sys.exit(f"{db} exists: restore only fills an empty place")
    db.parent.mkdir(parents=True, exist_ok=True)
    part = db.with_name(db.name + ".restoring")
    if not open_store(settings).get_backup(part):
        print("no copy in the store: nothing restored")
        return
    os.replace(part, db)
    print(f"restored {db} from backups/perception.db")


if __name__ == "__main__":
    main()
