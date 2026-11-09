"""Checks for the uploader (camrig/upload.py) against the local stand-in for the cloud, with no AWS at all.

    pixi run --frozen test-upload                         a made-up session of 33 MiB, in 5 MiB parts
    pixi run --frozen test-upload --big PROJECT.toml      a real session in 250 MiB parts, killed in its depth .bag

The stand-in is cloud/dev_server.py: once as its own process (its ready line read), and otherwise run in this process
with the same request handler, so the test can count every piece PUT and hold pieces back. Its data folder is a
temporary folder; objects over 64 MiB keep no bytes there, so the big run needs no disk for a second copy.
Every check and every process it starts uses camrig/keyring_file.py in a temporary folder as the password safe; only the
macOS check uses the real keyring backend, on a temporary keychain file under a temporary HOME. It is skipped when the
HOME the checks run in has no default keychain (a scratch HOME, or a computer with no keychain).
"""
import base64
import contextlib
import hashlib
import io
import json
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cloud"))
import dev_server
from camrig import record
from camrig.cloud_account import SERVICE, SIGNED_OUT
from camrig.project import load
from camrig.upload import Entry, Uploader, files_to_send, release_lock, take_lock

DEV_KEY = dev_server.DEV_KEY
MIB = 1024**2
TIMEOUT_S = 300
SESSION = "lab_session"
STARTED = []            # every uploader a check started: a failed check must not leave one waiting on a lock


class Cloud:
    """the stand-in in this process: dev_server's handler, with every piece PUT noted in arrival order"""

    def __init__(self, folder: Path, faults: str = "", delay: float = 0.0, slow_first: tuple[str, float] = ("", 0.0)):
        self.puts, self.links, self.delay, self.slow_first = [], 0, delay, slow_first
        self.fail_abort = False                  # answer the next /uploads/abort with a 500
        self.mutex = threading.Lock()
        cloud = self

        class Handler(dev_server.Handler):
            def do_PUT(self):
                url = urlsplit(self.path)
                key, n = unquote(url.path[len("/_s3/"):]), int(dict(parse_qsl(url.query)).get("partNumber", 0))
                with cloud.mutex:
                    first = (key, n) not in cloud.puts
                    cloud.puts.append((key, n))
                suffix, wait_s = cloud.slow_first
                time.sleep(wait_s if first and suffix and key.endswith(suffix) else cloud.delay)
                super().do_PUT()

            def do_POST(self):
                with cloud.mutex:
                    cloud.links += self.path == "/uploads/links"
                    fail = cloud.fail_abort and self.path == "/uploads/abort"
                    cloud.fail_abort = cloud.fail_abort and not fail
                if fail:
                    self.rfile.read(self.length() or 0)
                    return self.reply(500, {"Content-Type": "application/json"}, b'{"error": "a fault the test asked for"}')
                super().do_POST()

        self.server = dev_server.make_server(folder, 0, Handler, faults)
        self.url, self.store = self.server.url, self.server.store
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def held(self, key: str) -> list[int] | None:
        """the part numbers the unfinished upload of `key` holds, None while it has no upload"""
        with self.store.lock:
            ups = [u for u in self.store.index["uploads"].values() if u["key"] == key]
            return sorted(int(n) for n in ups[0]["parts"]) if ups else None

    def count(self, key: str, n: int) -> int:
        with self.mutex:
            return self.puts.count((key, n))


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {DEV_KEY}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def key(path: str, session: str = SESSION) -> str:
    return f"labs/dev/sessions/{session}/{path}"


def make_session(folder: Path) -> Path:
    """two cameras (an action camera with 23 MiB and 1 KiB videos and a lens video, a depth camera with a 9 MiB .bag),
    the depth recording's exports and the sync files, as camrig.project and the cameras step leave them"""
    s = folder / SESSION
    rnd = random.Random(7)

    def put(rel: str, n: int) -> Path:
        f = s / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(rnd.randbytes(n))
        return f

    a1 = put("a/a1.MOV", 23 * MIB)
    put("a/a2.MOV", 1024)
    bag = put("b/rec1.bag", 9 * MIB)
    put("lens/a/lens1.MOV", 2048)
    (s / "lens" / "a.json").write_text('{"made up": true}\n')
    (s / "project.toml").write_text(
        'name = "upload test"\n[board]\nsquares = [5, 7]\nsquare_mm = 20.0\nmarker_mm = 14.0\n'
        '[[camera]]\nname = "a"\nkind = "action"\nfolder = "a"\nfiles = "*.MOV"\nlens = "lens/a.json"\n'
        '[[camera]]\nname = "b"\nkind = "depth"\nfolder = "b"\nfiles = "*.bag"\n')
    (s / "decisions.json").write_text('{"schema": "camrig.decisions/1"}\n')
    colour, depth = put("work/cameras/b/rec1/orbbec_colour.mp4", 300_000), put("work/cameras/b/rec1/orbbec_depth.mp4", 200_000)
    frames = s / "work/cameras/b/rec1/orbbec_frames.csv"
    frames.write_text("frame,t\n0,0.0\n")
    (s / "work/cameras/cameras.json").write_text(json.dumps({"schema": "camrig.cameras/1", "cameras": [
        {"name": "a", "kind": "action", "parts": [{"file": str(a1), "key": "part01", "sound": True}]},
        {"name": "b", "kind": "depth", "parts": [{"key": "rec1", "bag": str(bag), "file": str(colour),
                                                  "depth_video": str(depth), "frames_csv": str(frames)}]}]}))
    record.write(s / "work/cameras", record.Check("cameras", items=[record.Item("made up", "pass")]))
    (s / "work/clock").mkdir(parents=True)
    (s / "work/clock/clock.json").write_text('{"schema": "camrig.clock/1"}\n')
    return s / "project.toml"


def clean_env(home: Path) -> dict:
    """no CAMRIG_CLOUD_* and no ~/.camrig/cloud.json of the person running the test"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CAMRIG_CLOUD")}
    env.update(HOME=str(home), USERPROFILE=str(home))
    return env


def start_uploader(project: Path, url: str | None, *args: str, code: str = "", key: str | None = DEV_KEY) -> subprocess.Popen:
    """the uploader as its own process; `code`, when given, runs in place of -m camrig.upload with the same arguments.
    With no url the address and the token come from ~/.camrig/cloud.json and the (file) password safe."""
    env = clean_env(project.parent.parent)
    if url:
        env["CAMRIG_CLOUD_URL"] = url
    if url and key:
        env["CAMRIG_CLOUD_KEY"] = key
    out = open(project.parent.parent / f"out_{time.monotonic_ns()}.txt", "w+")
    p = subprocess.Popen([sys.executable, *(["-c", code] if code else ["-m", "camrig.upload"]), str(project), *args], cwd=ROOT, env=env, stdout=out,
                         stderr=subprocess.STDOUT, text=True)
    p.out = out
    STARTED.append(p)
    return p


def finish(p: subprocess.Popen, code: int = 0) -> str:
    p.wait(TIMEOUT_S)
    p.out.seek(0)
    out = p.out.read()
    p.out.close()
    assert p.returncode == code, f"uploader exit {p.returncode}, expected {code}:\n{out[-3000:]}"
    return out


def upload(project: Path, url: str | None, *args: str, code: int = 0) -> str:
    return finish(start_uploader(project, url, "--part-mb", "5", "--retry-base", "0.05", *args), code)


def account(home: Path, *args: str, stdin: str = "", **env: str) -> tuple[int, dict, str]:
    """python -m camrig.cloud_account; (exit code, its JSON answer, its whole stdout)"""
    r = subprocess.run([sys.executable, "-m", "camrig.cloud_account", *args], cwd=ROOT, env={**clean_env(home), **env},
                       input=stdin, capture_output=True, text=True, timeout=120)
    assert r.stderr == "", f"cloud_account {args} wrote to stderr: {r.stderr[-2000:]}"
    return r.returncode, json.loads(r.stdout), r.stdout


def keyring_dir() -> Path:
    return Path(os.environ["CAMRIG_TEST_KEYRING_DIR"])


def asked() -> list[str]:
    f = keyring_dir() / "asked.txt"
    return f.read_text().split() if f.exists() else []


def start_dev_server(folder: Path) -> tuple[subprocess.Popen, str]:
    server = subprocess.Popen([sys.executable, str(ROOT / "cloud" / "dev_server.py"), "--port", "0", "--dir", str(folder)],
                              stdout=subprocess.PIPE, text=True)
    line = server.stdout.readline()
    assert re.fullmatch(r"ready http://127\.0\.0\.1:\d+ lab dev key perception-dev-key\n", line), f"ready line: {line!r}"
    return server, line.split()[1]


def until(what: str, ok, timeout_s: float = 60.0):
    t0 = time.monotonic()
    while not ok():
        assert time.monotonic() - t0 < timeout_s, f"waited {timeout_s} s for {what}"
        time.sleep(0.02)


def upload_dir(project: Path) -> Path:
    return project.parent / "upload"


def check_arrived(project: Path, url: str):
    """every file of the session is in the cloud byte for byte, read back from the stand-in, and the manifest lists
    each with its own sha256"""
    want = {e.path: e.source.read_bytes() for e in files_to_send(load(project))}
    listed = {f["path"]: f for f in json.loads(get(f"{url}/sessions/{SESSION}/files"))["files"]}
    assert set(listed) == set(want) | {"manifest.json"}, f"in the cloud: {sorted(listed)}"
    for path, data in want.items():
        assert get(f"{url}/_s3/{key(path)}") == data, f"{path}: the stored bytes differ from the file"
        assert listed[path]["size"] == len(data) and listed[path]["sha256"] == sha(data), path
    manifest = json.loads(get(f"{url}/_s3/{key('manifest.json')}"))
    assert manifest["schema"] == "camrig.manifest/1" and manifest["lab"] == "dev" and manifest["session"] == SESSION
    assert {f["path"]: f["sha256"] for f in manifest["files"]} == {p: sha(d) for p, d in want.items()}
    assert manifest == json.loads((upload_dir(project) / "manifest.json").read_text())
    state = json.loads((upload_dir(project) / "state.json").read_text())
    assert {r["status"] for r in state["files"].values()} == {"done"}, state
    progress = json.loads((upload_dir(project) / "progress.json").read_text())
    assert progress["state"] == "done" and progress["bytes_done"] == progress["bytes_total"], progress
    assert not (upload_dir(project) / "lock").exists(), "the lock's process id is removed at the end"
    held = take_lock(upload_dir(project), block=False)
    assert held, "the OS lock is free at the end"
    release_lock(upload_dir(project), held)


# ---- the checks ----

def test_no_cloud(tmp: Path):
    project = make_session(tmp)
    out = finish(start_uploader(project, None))
    assert "Not signed in to the cloud" in out, out
    progress = json.loads((upload_dir(project) / "progress.json").read_text())
    assert progress["state"] == "waiting_for_cloud" and progress["error"] == "Sign in to upload", progress
    assert asked() == [], f"no address, yet the password safe was asked: {asked()}"
    print("PASS  no cloud address: waiting_for_cloud, 'Sign in to upload', exit 0, the password safe never asked")


def test_no_token(tmp: Path):
    project = make_session(tmp)
    out = finish(start_uploader(project, "http://127.0.0.1:9", key=None))
    progress = json.loads((upload_dir(project) / "progress.json").read_text())
    assert progress["state"] == "waiting_for_cloud" and progress["error"] == "Sign in to upload", (progress, out)
    assert asked() == ["get"], f"the password safe is asked once for the token: {asked()}"
    print("PASS  an address but no token: waiting_for_cloud, 'Sign in to upload', exit 0")


def test_account_offline(tmp: Path):
    code, answer, _ = account(tmp, "status")
    assert code == 0 and answer == {"url": None, "signedIn": False, "email": None, "name": None, "lab": None,
                                    "fromEnv": False}, answer
    code, answer, _ = account(tmp, "status", CAMRIG_CLOUD_URL="http://127.0.0.1:9/", CAMRIG_CLOUD_KEY=DEV_KEY)
    assert code == 0 and answer["url"] == "http://127.0.0.1:9" and answer["signedIn"] and answer["fromEnv"], answer
    assert DEV_KEY not in json.dumps(answer), "status never shows the token"
    details = json.dumps({"url": "http://127.0.0.1:9", "email": "lab@example.com", "password": "lab-dev-password"})
    for args, env in ((("signin",), {}), (("signout",), {})):
        code, answer, _ = account(tmp, *args, stdin=details, CAMRIG_READONLY="1")
        assert code == 1 and "read only" in answer["error"], answer
    code, answer, _ = account(tmp, "signin", stdin=details.replace("http://127.0.0.1:9", "http://example.com"))
    assert code == 1 and "https://" in answer["error"], answer
    code, answer, _ = account(tmp, "nonsense")
    assert code == 1 and "usage" in answer["error"], answer
    assert not (tmp / ".camrig").exists() and asked() == [], "nothing was kept and the password safe never asked"
    print("PASS  cloud_account without a server: status, read only refuses signin and signout, http:// only locally")


def test_signin_upload_signout(tmp: Path):
    """the whole sign-in path against cloud/dev_server.py as its own process, with the token only in the (file) safe"""
    project = make_session(tmp)
    server, url = start_dev_server(tmp / "cloud")
    try:
        details = json.dumps({"url": url + "/", "email": "lab@example.com", "password": "lab-dev-password"})
        code, me, out_signin = account(tmp, "signin", stdin=details)
        assert code == 0 and me["email"] == "lab@example.com" and me["lab"] == "dev" and set(me) == {"email", "name", "lab"}, \
            f"signin (needs the server lane's /auth/signin in cloud/dev_server.py): {me}"
        token = json.loads((keyring_dir() / "keyring.json").read_text())[f"{SERVICE}\n{url}"]
        config = tmp / ".camrig" / "cloud.json"
        assert json.loads(config.read_text()) == {"url": url, **me}, config.read_text()
        if os.name != "nt":
            assert config.stat().st_mode & 0o777 == 0o600, oct(config.stat().st_mode)
        code, st, out_status = account(tmp, "status")
        assert code == 0 and st == {"url": url, "signedIn": True, **me, "fromEnv": False}, st
        out = upload(project, None)
        check_arrived(project, url)
        code, listed, out_files = account(tmp, "files", SESSION)
        assert code == 0 and "manifest.json" in {f["path"] for f in listed["files"]}, listed
        log = (upload_dir(project) / "log.txt").read_text()
        for where, text in (("signin", out_signin), ("status", out_status), ("files", out_files), ("uploader", out),
                            ("log.txt", log), ("cloud.json", config.read_text())):
            assert token not in text and "lab-dev-password" not in text, f"the token or the password is in {where}"

        code, answer, _ = account(tmp, "signout")
        assert code == 0 and answer == {}, answer
        assert f"{SERVICE}\n{url}" not in json.loads((keyring_dir() / "keyring.json").read_text()), "signout forgets the token"
        try:
            urllib.request.urlopen(urllib.request.Request(url + "/auth/me", headers={"Authorization": f"Bearer {token}"}),
                                   timeout=30)
            raise AssertionError("the cloud still accepts the token after signout")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code
        code, st, _ = account(tmp, "status")
        assert code == 0 and st["signedIn"] is False, st

        (keyring_dir() / "keyring.json").write_text(json.dumps({f"{SERVICE}\n{url}": token}))   # signed out elsewhere
        upload(project, None, code=1)
        progress = json.loads((upload_dir(project) / "progress.json").read_text())
        assert progress["state"] == "failed" and progress["error"] == SIGNED_OUT, progress
        code, answer, _ = account(tmp, "signout")
        assert code == 0 and answer == {}, f"a 401 at signout still counts as signed out: {answer}"
    finally:
        server.terminate()
        server.wait(10)
    print("PASS  signin keeps the token in the password safe only; the uploader uses it; signout revokes it and the next "
          "run fails 'Signed out of the cloud'")


def test_macos_keychain(tmp: Path):
    """keyring's macOS backend on a temporary keychain file; keyring ignores KEYCHAIN_PATH (its issue #623), so the
    file is made the default keychain of a temporary HOME, which the Security framework follows"""
    if sys.platform != "darwin":
        print(f"SKIP  macOS keychain check: this is {sys.platform}")
        return
    real = {**os.environ}
    if subprocess.run(["security", "default-keychain"], env=real, capture_output=True, timeout=30).returncode != 0:
        print("SKIP  macOS keychain check: this HOME has no default keychain to compare against")
        return
    home, kc = tmp / "home", tmp / "home" / "check.keychain"
    for d in ("Library/Preferences", "Library/Keychains"):
        (home / d).mkdir(parents=True)
    env = {**clean_env(home), "PYTHON_KEYRING_BACKEND": "keyring.backends.macOS.Keyring"}

    def run(*cmd: str, env=env) -> str:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)   # a prompt must never hang it
        assert r.returncode == 0, f"{cmd[:2]}: {r.stdout}{r.stderr}"
        return r.stdout

    def real_keychains() -> str:
        return run("security", "default-keychain", env=real) + run("security", "list-keychains", "-d", "user", env=real)

    before = real_keychains()
    service = f"camrig-check-{os.getpid()}"
    code = ("import keyring, sys\n"
            "from keyring.backends import macOS\n"
            "assert isinstance(keyring.get_keyring(), macOS.Keyring), keyring.get_keyring()\n"
            "s, a = sys.argv[1], 'account'\n"
            "keyring.set_password(s, a, 'secret one')\n"
            "assert keyring.get_password(s, a) == 'secret one'\n"
            "print('set', flush=True)\n"
            "sys.stdin.readline()\n"
            "keyring.delete_password(s, a)\n"
            "assert keyring.get_password(s, a) is None\n"
            "print('deleted')\n")
    try:
        run("security", "create-keychain", "-p", "check", str(kc))
        run("security", "default-keychain", "-d", "user", "-s", str(kc))
        run("security", "unlock-keychain", "-p", "check", str(kc))
        p = subprocess.Popen([sys.executable, "-c", code, service], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        timer = threading.Timer(60, p.kill)
        timer.start()
        try:
            assert p.stdout.readline() == "set\n", p.stdout.read()
            run("security", "find-generic-password", "-s", service, str(kc))
            out, _ = p.communicate("\n", timeout=60)
            assert p.returncode == 0 and out == "deleted\n", out
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
    finally:
        if kc.exists():
            run("security", "delete-keychain", str(kc))
    assert real_keychains() == before, "the person's own default keychain and search list are unchanged"
    print("PASS  keyring's macOS backend: set, get and delete on a temporary keychain file, which is deleted after")


def test_everything_arrives(tmp: Path):
    project = make_session(tmp)
    server = subprocess.Popen([sys.executable, str(ROOT / "cloud" / "dev_server.py"), "--port", "0", "--dir", str(tmp / "cloud")],
                              stdout=subprocess.PIPE, text=True)
    try:
        line = server.stdout.readline()
        assert re.fullmatch(r"ready http://127\.0\.0\.1:\d+ lab dev key perception-dev-key\n", line), f"ready line: {line!r}"
        url = line.split()[1]
        out = upload(project, url)
        assert asked() == [], f"CAMRIG_CLOUD_KEY is set, yet the password safe was asked: {asked()}"
        check_arrived(project, url)
        log = (upload_dir(project) / "log.txt").read_text()
        assert DEV_KEY not in out + log, "the lab key is never printed"
        done = [l.split()[1] for l in log.splitlines() if l.endswith(" bytes") and ": done, " in l]
        assert done[-1] == "manifest.json:" and len(done) == 14, done
        again = upload(project, url)
        assert " sent," not in again and "session lab_session: done" in again, again
    finally:
        server.terminate()
        server.wait(10)
    print("PASS  everything arrives byte for byte, the manifest last; a second run sends nothing (dev_server.py process)")


def test_kill_and_resume(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", delay=0.4)
    try:
        a1 = key("originals/a/a1.MOV")
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05", "--parallel", "1")
        until("a1.MOV to be part way", lambda: 1 <= len(cloud.held(a1) or []) < 5)
        p.send_signal(signal.SIGKILL)
        finish(p, -signal.SIGKILL)
        held = cloud.held(a1)
        assert 1 <= len(held) < 5, held
        cloud.delay = 0.0
        upload(project, cloud.url)
        check_arrived(project, cloud.url)
        counts = {n: cloud.count(a1, n) for n in range(1, 6)}
        assert all(counts[n] == 1 for n in held), f"a part the cloud held at the kill was sent again: {counts}, held {held}"
        assert sum(counts.values()) <= 5 + 1, f"only the missing parts, plus the one in flight at the kill: {counts}"
    finally:
        cloud.close()
    print(f"PASS  SIGKILL part way through a file, then a restart sends only the missing parts (held {held}, PUTs {counts})")


def test_pause_and_changed_file(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", delay=0.4)
    try:
        a1 = key("originals/a/a1.MOV")
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05", "--parallel", "2")
        until("a first piece of a1.MOV", lambda: cloud.count(a1, 1) + cloud.count(a1, 2) >= 1)
        second = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05")
        until("the second uploader to wait", lambda: "already runs" in Path(second.out.name).read_text())
        with cloud.mutex:
            before = len(cloud.puts)
        (upload_dir(project) / "pause.request").touch()
        finish(p)
        assert "it paused, so this one stops too" in finish(second), "a second start waits, then keeps the pause"
        assert len(cloud.puts) <= before + 2, "after the pause request only the pieces in flight go up"
        assert not (upload_dir(project) / "pause.request").exists(), "the uploader deletes the request when paused"
        assert json.loads((upload_dir(project) / "progress.json").read_text())["state"] == "paused"
        rec = json.loads((upload_dir(project) / "state.json").read_text())["files"]["originals/a/a1.MOV"]
        assert rec["status"] == "uploading" and rec["upload_id"], rec
        with open(project.parent / "a/a1.MOV", "ab") as f:
            f.write(b"changed while paused")
        cloud.delay = 0.0
        upload(project, cloud.url)
        log = (upload_dir(project) / "log.txt").read_text()
        assert "originals/a/a1.MOV: its size or time changed, starting it again" in log, log
        assert not cloud.store.index["uploads"], "the old upload of the changed file was cancelled"
        check_arrived(project, cloud.url)
    finally:
        cloud.close()
    print("PASS  pause.request stops after the pieces in flight; a second uploader waits and keeps the pause; a changed file starts again")


def test_changed_while_sending(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", delay=0.4)
    try:
        a1 = key("originals/a/a1.MOV")
        cloud.fail_abort = True
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05", "--parallel", "1")
        until("a1.MOV to hold a part", lambda: len(cloud.held(a1) or []) >= 1)
        with open(project.parent / "a/a1.MOV", "ab") as f:
            f.write(b"changed while it goes up")
        cloud.delay = 0.0
        finish(p)
        log = (upload_dir(project) / "log.txt").read_text()
        assert "originals/a/a1.MOV: it changed on disk, starting it again" in log, log
        assert "/uploads/abort: the cloud answered 500" in log, "the cancel met a server error and was tried again"
        assert not cloud.store.index["uploads"], "the old upload of the changed file was cancelled"
        check_arrived(project, cloud.url)
    finally:
        cloud.close()
    print("PASS  a file changed while its pieces go up is cancelled (through a 500 on the cancel) and sent again")


def test_server_errors(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", faults="put-500-every=3")
    try:
        upload(project, cloud.url)
        check_arrived(project, cloud.url)
        log = (upload_dir(project) / "log.txt").read_text()
        failures = log.count("InternalError")
        assert failures >= 3, log
    finally:
        cloud.close()
    print(f"PASS  put-500-every=3 with --retry-base 0.05 still finishes ({failures} pieces retried)")


def test_expired_links(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", faults="link-ttl-s=1", slow_first=("a1.MOV", 2.2))
    try:
        upload(project, cloud.url)
        check_arrived(project, cloud.url)
        a1 = key("originals/a/a1.MOV")
        assert all(cloud.count(a1, n) == 2 for n in range(1, 6)), "each a1 piece: refused once with its expired link, then sent"
        log = (upload_dir(project) / "log.txt").read_text()
        assert log.count("Request has expired), asking for a new link") == 5, log
        assert cloud.links >= 15 + 5, f"one link per piece, and 5 new ones: {cloud.links}"
    finally:
        cloud.close()
    print(f"PASS  link-ttl-s=1: each expired link was renewed at once and the upload finished ({cloud.links} links)")


def test_sync_change(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud")
    try:
        upload(project, cloud.url)
        first = len(cloud.puts)
        (project.parent / "decisions.json").write_text('{"schema": "camrig.decisions/1", "clock": {"user": {}}}\n')
        upload(project, cloud.url)
        again = [k for k, _ in cloud.puts[first:]]
        assert again == [key("sync/decisions.json"), key("manifest.json")], again
        check_arrived(project, cloud.url)
    finally:
        cloud.close()
    print("PASS  a changed sync file goes up again, then the manifest again, last")


SLOW_FINISH = """
import sys, time
from pathlib import Path
from camrig import upload
real, window, last = upload.Uploader.write_manifest, Path({!r}), {!r}
def slow(self):
    changed = real(self)
    if not changed and not window.exists() and (self.run_state == "done") == last:
        window.touch()
        time.sleep(1.5)
    return changed
upload.Uploader.write_manifest = slow
sys.exit(upload.main(sys.argv[1:]))
"""


def test_lock_and_late_change(tmp: Path):
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud")
    up, decisions = upload_dir(project), project.parent / "decisions.json"
    try:
        up.mkdir()
        (up / "lock").write_text(str(os.getpid()))           # a live process, not an uploader: a kill left it behind
        upload(project, cloud.url)
        check_arrived(project, cloud.url)
        held = take_lock(up, block=False)
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05")
        until("the uploader to wait", lambda: "already runs" in Path(p.out.name).read_text())
        third = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05")
        until("a third start to end at once", lambda: third.poll() is not None, 15)
        assert "another waits to follow it" in finish(third), "a third start, while one waits, is not needed"
        decisions.write_text('{"schema": "camrig.decisions/1", "while": "waiting"}\n')
        release_lock(up, held)
        finish(p)
        check_arrived(project, cloud.url)
        waiter = take_lock(up, block=False, name="wait.os")
        assert waiter, "the waiter's lock is free at the end"
        release_lock(up, waiter)
        window = tmp / "in_window"
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05", code=SLOW_FINISH.format(str(window), False))
        until("the uploader's last look at the files", window.exists)
        decisions.write_text('{"schema": "camrig.decisions/1", "while": "finishing"}\n')
        finish(p)
        check_arrived(project, cloud.url)
    finally:
        cloud.close()
    print("PASS  a stale process id blocks nothing; a second start waits, then sends, a third exits at once; a change as it finishes goes up")


def test_moved_session(tmp: Path):
    project = make_session(tmp)
    s = project.parent
    cams = s / "work/cameras/cameras.json"
    cams.write_text(cams.read_text().replace(str(s), "/old/place/lab_session"))
    frames, aside = s / "work/cameras/b/rec1/orbbec_frames.csv", tmp / "frames.csv"
    frames.rename(aside)
    cloud = Cloud(tmp / "cloud")
    try:
        finish(start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05"), 1)
        progress = json.loads((upload_dir(project) / "progress.json").read_text())
        assert progress["state"] == "failed" and "orbbec_frames.csv is missing" in progress["error"], progress
        aside.rename(frames)
        upload(project, cloud.url)
        listed = {f["path"] for f in json.loads(get(f"{cloud.url}/sessions/{SESSION}/files"))["files"]}
        want = {f"derived/b/rec1/{n}" for n in ("orbbec_colour.mp4", "orbbec_depth.mp4", "orbbec_frames.csv")}
        assert want <= listed, sorted(listed)
        check_arrived(project, cloud.url)
    finally:
        cloud.close()
    print("PASS  a moved session sends its depth exports from its own folder; a missing export fails the session")


def test_sigterm(tmp: Path):
    """the demo's Ctrl+C and a shutdown send SIGTERM: the cleanup runs at once, with a piece still in flight, and
    progress stays running so the viewer resumes it; a waiter stopped so leaves the runner's lock alone"""
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud", slow_first=("a1.MOV", 6.0))
    up = upload_dir(project)
    try:
        a1 = key("originals/a/a1.MOV")
        p = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05", "--parallel", "1")
        until("a first piece of a1.MOV in flight", lambda: cloud.count(a1, 1) == 1)
        waiter = start_uploader(project, cloud.url, "--part-mb", "5", "--retry-base", "0.05")
        until("the second uploader to wait", lambda: "already runs" in Path(waiter.out.name).read_text())
        waiter.send_signal(signal.SIGTERM)
        finish(waiter, 128 + signal.SIGTERM)
        assert (up / "lock").read_text() == str(p.pid), "a waiter stopped leaves the runner's process id"
        p.send_signal(signal.SIGTERM)
        until("the runner's cleanup while its piece is in flight", lambda: not (up / "lock").exists(), 3)
        finish(p, 128 + signal.SIGTERM)
        progress = json.loads((up / "progress.json").read_text())
        assert progress["state"] == "running" and progress["pid"] == p.pid, progress
        for name in ("lock.os", "wait.os"):
            held = take_lock(up, block=False, name=name)
            assert held, f"{name} is free after the stop"
            release_lock(up, held)
        upload(project, cloud.url)
        check_arrived(project, cloud.url)
        window = tmp / "in_last_pass"
        p = start_uploader(project, cloud.url, "--part-mb", "5", code=SLOW_FINISH.format(str(window), True))
        until("the last look at the files, after progress said done", window.exists)
        p.send_signal(signal.SIGTERM)
        finish(p, 128 + signal.SIGTERM)
        assert json.loads((up / "progress.json").read_text())["state"] == "running", "a cut-short last look is not done"
    finally:
        cloud.close()
    print("PASS  SIGTERM: the lock is freed with a piece in flight, progress stays running (also in the last look), a stopped waiter leaves the runner's lock")


def test_complete_timeout(tmp: Path):
    """/uploads/complete may take the Lambda's whole 300 s, so its wait is longer; every other call keeps 120 s"""
    project = make_session(tmp)
    cloud = Cloud(tmp / "cloud")
    real, seen = urllib.request.urlopen, []

    def note(req, timeout):
        seen.append((urlsplit(req.full_url).path, timeout))
        return real(req, timeout=timeout)

    urllib.request.urlopen = note
    try:
        u = Uploader(load(project), (cloud.url, DEV_KEY), 5 * MIB, 3, 0.05)
        u.dir.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            assert u.main() == 0
    finally:
        urllib.request.urlopen = real
        cloud.close()
    complete = {t for path, t in seen if path == "/uploads/complete"}
    rest = {t for path, t in seen if path != "/uploads/complete"}
    assert complete and min(complete) >= 330 and rest == {120}, (complete, rest)
    print(f"PASS  /uploads/complete waits {min(complete)} s for an answer, every other call 120 s")


def test_refusals(tmp: Path):
    """a session name the cloud cannot take, or a project camrig refuses, ends with a failed progress.json that says
    why, unless an uploader of that session runs and reports for itself; a path that is no project gets no folder"""
    bad = make_session(tmp).parent.rename(tmp / "Lab Session")
    finish(start_uploader(bad / "project.toml", None), 2)
    progress = json.loads((bad / "upload" / "progress.json").read_text())
    assert progress["state"] == "failed" and "lowercase letters" in progress["error"], progress
    project = bad.rename(tmp / SESSION) / "project.toml"
    project.write_text('name = "broken"\n')
    finish(start_uploader(project, None), 2)
    progress = json.loads((upload_dir(project) / "progress.json").read_text())
    assert progress["state"] == "failed" and progress["error"] == "project: [board] is missing", progress
    held = take_lock(upload_dir(project), block=False)
    (upload_dir(project) / "progress.json").write_text('{"state": "running"}')
    finish(start_uploader(project, None), 2)
    release_lock(upload_dir(project), held)
    assert json.loads((upload_dir(project) / "progress.json").read_text()) == {"state": "running"}, "a runner's progress is its own"
    finish(start_uploader(tmp / "no_project.toml", None), 2)
    assert not (tmp / "upload").exists(), "a mistyped path makes no upload folder"
    print("PASS  a refused session name or project writes a failed progress.json with the reason, not over a runner's")


def test_windows_unlock(tmp: Path):
    """on Windows the byte msvcrt.locking locked is unlocked before its file closes, as Microsoft's _locking asks; a
    stand-in for msvcrt notes each call and where the file stood"""
    calls = []
    sys.modules["msvcrt"] = types.SimpleNamespace(
        LK_NBLCK=2, LK_UNLCK=0, locking=lambda fd, mode, n: calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), n)))
    real = os.name
    os.name = "nt"
    try:
        release_lock(tmp, take_lock(tmp, block=False))
    finally:
        os.name = real
        del sys.modules["msvcrt"]
    assert calls == [(2, 0, 1), (0, 0, 1)], calls
    print("PASS  Windows (msvcrt stood in for): the lock's byte is locked, then unlocked before the file closes")


def test_in_process_rules(tmp: Path):
    project = make_session(tmp)
    u = Uploader(load(project), ("http://127.0.0.1:9", DEV_KEY), 5 * MIB, 1, 0.05)
    u.dir.mkdir()
    u.lab = "dev"
    u.load_state()
    u.refresh(u.plan())
    decisions = project.parent / "decisions.json"

    class Bigger(type(decisions)):                     # as if the file shrank between stat() and the read
        def stat(self, *, follow_symlinks=True):
            st = super().stat()
            return types.SimpleNamespace(st_size=st.st_size + 100, st_mtime_ns=st.st_mtime_ns)

    u.files["sync/decisions.json"]["sha256"] = "an older content"
    u.refresh([Entry("sync/decisions.json", Bigger(decisions), "sync")])
    rec = u.files["sync/decisions.json"]
    assert rec["size"] == decisions.stat().st_size and rec["sha256"] == sha(decisions.read_bytes()), rec
    size = 60 * 1024**3
    n = u.parts_total(size)
    lens = [u.part_range(size, i)[1] for i in range(1, n + 1)]
    assert n <= 10_000 and sum(lens) == size and len(set(lens[:-1])) == 1 and lens[0] >= 5 * MIB and lens[0] % MIB == 0, (n, lens[0])
    assert u.parts_total(23 * MIB) == 5, "a file under 10,000 parts keeps the part size asked for"
    print(f"PASS  a sync file rewritten while read is hashed as read; 60 GiB at --part-mb 5 goes in {n} parts of {lens[0] // MIB} MiB")


# ---- the real session ----

def big_proof(project: Path):
    """a real session in 250 MiB parts: SIGKILL in the depth .bag, restart, then every object checked against the
    file: bytes for small objects, each part's SHA-256 as the stand-in computed it from the bytes it received for big
    ones"""
    prj = load(project)
    session = prj.root.name
    plan = files_to_send(prj)
    bag = next(e for e in plan if e.source.suffix == ".bag")
    bag_key, bag_parts = key(bag.path, session), -(-bag.source.stat().st_size // (250 * MIB))
    with tempfile.TemporaryDirectory(dir=prj.root.parent) as tmp:
        cloud = Cloud(Path(tmp) / "cloud")
        try:
            t0 = time.monotonic()
            p = start_uploader(project, cloud.url, "--retry-base", "1")
            until("the .bag to be part way", lambda: 2 <= len(cloud.held(bag_key) or []) < bag_parts, 3600)
            p.send_signal(signal.SIGKILL)
            finish(p, -signal.SIGKILL)
            t_kill = time.monotonic()
            held = cloud.held(bag_key)
            print(f"killed after {t_kill - t0:.1f} s with {held} of {bag_parts} .bag parts held")
            finish(start_uploader(project, cloud.url, "--retry-base", "1"))
            t_end = time.monotonic()
            print(f"restart finished in {t_end - t_kill:.1f} s; whole run {t_end - t0:.1f} s")
            counts = {n: cloud.count(bag_key, n) for n in range(1, bag_parts + 1)}
            assert all(counts[n] == 1 for n in held), f"a held .bag part was sent again: {counts}"
            assert sum(counts.values()) <= bag_parts + 3, counts
            for e in plan:
                o = cloud.store.index["objects"][key(e.path, session)]
                size, parts = e.source.stat().st_size, []
                with open(e.source, "rb") as f:
                    whole = hashlib.sha256()
                    while chunk := f.read(250 * MIB):
                        whole.update(chunk)
                        parts.append({"size": len(chunk), "sha256_b64": base64.b64encode(hashlib.sha256(chunk).digest()).decode()})
                assert o["size"] == size and o["metadata"]["sha256"] == whole.hexdigest(), e.path
                assert o["parts"] == parts, e.path
                if o["data"]:
                    assert cloud.store.read_object(key(e.path, session)) == e.source.read_bytes(), e.path
            print(f"each of {len(plan)} objects matches its file part by part; .bag PUTs per part {counts}")
            print(f"stand-in data folder: {sum(f.stat().st_size for f in Path(tmp).rglob('*') if f.is_file())} bytes")
        finally:
            cloud.close()
    print("PASS  big proof")


def main():
    if sys.argv[1:2] == ["--big"]:
        return big_proof(Path(sys.argv[2]).resolve())
    failed = 0
    os.environ["PYTHON_KEYRING_BACKEND"] = "camrig.keyring_file.FileKeyring"     # every process this starts inherits it
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))
    with tempfile.TemporaryDirectory() as tmp:
        for i, test in enumerate([test_no_cloud, test_no_token, test_account_offline, test_signin_upload_signout,
                                  test_macos_keychain, test_everything_arrives, test_kill_and_resume, test_pause_and_changed_file,
                                  test_changed_while_sending, test_server_errors, test_expired_links, test_sync_change,
                                  test_lock_and_late_change, test_sigterm, test_complete_timeout, test_refusals,
                                  test_windows_unlock, test_moved_session, test_in_process_rules]):
            folder = Path(tmp) / str(i)
            folder.mkdir()
            os.environ["CAMRIG_TEST_KEYRING_DIR"] = str(folder / "keyring")
            (folder / "keyring").mkdir()
            try:
                test(folder)
            except Exception:
                failed += 1
                print(f"FAIL  {test.__name__}\n{traceback.format_exc()}")
            for p in STARTED:
                if p.poll() is None:
                    p.kill()
                    p.wait()
    print("FAIL" if failed else "PASS  all upload checks", f"({failed} failed)" if failed else "")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
