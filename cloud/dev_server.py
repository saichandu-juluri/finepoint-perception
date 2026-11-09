"""The cloud on this computer: the real server (cloud/server.py) with the local stand-in for S3 (cloud/store_fake.py)
and a database in the data folder, so the uploader and the app can be built and tested without AWS.

    python cloud/dev_server.py [--port 8787] [--dir <data dir>]       --port 0 picks a free port

The data folder is --dir, else CAMRIG_CLOUD_DEV_DIR, else .cloud_dev in the repository folder. Piece PUTs and
small-object GETs under /_s3/ go to the stand-in, as they would go to S3. Every start seeds the database (safe to
repeat): lab dev; lab@example.com, password lab-dev-password, signed in on computer "dev" with the app token
perception-dev-key; admin@example.com, password admin-dev-password, an admin. The admin page is at <address>/admin.
When it is listening it prints one line on stdout and nothing else (request lines go to stderr):

    ready http://127.0.0.1:8787 lab dev key perception-dev-key

Standard library only.
"""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server
import store_fake
import users

DEV_KEY = "perception-dev-key"
DEFAULT_DIR = HERE.parent / ".cloud_dev"
PEOPLE = (("lab@example.com", "lab-dev-password", "Lab person", False),
          ("admin@example.com", "admin-dev-password", "Admin", True))


class Handler(server.Handler):
    """the server's handler plus what S3 itself would serve"""

    def s3_error(self, status: int, message: str):
        code, _, text = message.partition(": ")
        self.reply(status, {"Content-Type": "application/xml"},
                   f"<Error><Code>{code}</Code><Message>{text}</Message></Error>".encode())

    def do_GET(self):
        url = urlsplit(self.path)
        if not url.path.startswith("/_s3/"):
            return super().do_GET()
        key = unquote(url.path[len("/_s3/"):])
        db = self.db()
        try:
            who = users.person_of(db, self.bearer(), "app")
        finally:
            db.close()
        if who is None:
            return self.s3_error(401, f"AccessDenied: {server.SIGNED_OUT}")
        if not key.startswith(f"labs/{who['lab']}/"):
            return self.s3_error(403, "AccessDenied: this key belongs to another lab")
        data = self.server.store.read_object(key)
        if data is None:
            return self.s3_error(404, "NoSuchKey: no such object, or it is bigger than the stand-in keeps")
        self.reply(200, {"Content-Type": "application/octet-stream"}, data)

    def do_PUT(self):
        url = urlsplit(self.path)
        n = self.length()
        if n is None:
            self.close_connection = True
            return self.s3_error(411, "MissingContentLength: a piece needs Content-Length")
        if not url.path.startswith("/_s3/"):
            self.close_connection = True
            return self.s3_error(404, "NoSuchKey: pieces go to the links /uploads/links gives")
        status, headers, error = self.server.store.put_part(
            unquote(url.path[len("/_s3/"):]), dict(parse_qsl(url.query)), self.headers.get("x-amz-checksum-sha256"),
            self.rfile, n)
        if error:
            return self.s3_error(status, error)
        self.reply(status, headers, b"")


def seed(db_path):
    db = users.connect(db_path)
    try:
        users.migrate(db)
        db.execute("INSERT OR IGNORE INTO labs VALUES ('dev', 'Development lab', ?)", (users.now(),))
        for email, password, name, admin in PEOPLE:
            if not db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                uid = users.add_user(db, email, "dev", name, admin)
                users.set_password(db, uid, password)
        uid = users.user_id(db, PEOPLE[0][0])
        db.execute("UPDATE users SET disabled_at = NULL WHERE id = ?", (uid,))
        db.execute("INSERT INTO tokens (hash, user_id, kind, label, created_at) VALUES (?, ?, 'app', 'dev', ?)"
                   " ON CONFLICT (hash) DO UPDATE SET user_id = excluded.user_id, kind = 'app', revoked_at = NULL,"
                   " expires_at = NULL", (users.token_hash(DEV_KEY), uid, users.now()))
    finally:
        db.close()


def make_server(folder, port: int = 0, handler=Handler, faults: str | None = None):
    """the stand-in, seeded and not yet serving; .url is its address and .store its stand-in for S3"""
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    httpd = server.make_server(("127.0.0.1", port), {}, None, handler)
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.settings.update(db=str(folder / "perception.db"), public_url=httpd.url)
    httpd.store = store_fake.FakeStore(folder, httpd.url, faults)
    seed(httpd.settings["db"])
    return httpd


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--dir", default=os.environ.get("CAMRIG_CLOUD_DEV_DIR", str(DEFAULT_DIR)))
    a = ap.parse_args()
    httpd = make_server(a.dir, a.port, Handler)
    print(f"ready {httpd.url} lab dev key {DEV_KEY}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
