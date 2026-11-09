"""The upload lane: every file of a session into S3 in pieces, through the cloud API (docs/UPLOAD.md).

    python -m camrig.upload PROJECT.toml [--part-mb 250] [--parallel 3] [--retry-base 30]

Each file is one record in upload/state.json that moves waiting -> hashing -> uploading -> done, or to failed. The
records are only a cache of what the cloud holds: every start asks /uploads/parts for each unfinished upload and
believes the answer, so a kill at any moment, or upload/pause.request, costs at most the pieces in flight. It exits
once every file, the manifest last, is done; the viewer starts it again after anything in the session changes.

The cloud's address and token follow camrig.cloud_account: CAMRIG_CLOUD_URL, else ~/.camrig/cloud.json url;
CAMRIG_CLOUD_KEY, else the password safe. With no token it waits ("Sign in to upload"); a token the cloud refuses (401)
fails the run with "Signed out of the cloud: sign in again in the app". The token is never printed. One uploader per session: the OS locks upload/lock.os for it. A second start holds upload/wait.os and waits
for the first to end; a start while one already waits exits at once, as the waiting one makes a full pass after the
first. SIGTERM (the demo's Ctrl+C, a shutdown) ends it as Ctrl+C in a terminal does: the cleanup runs and
progress.json stays running, so the viewer resumes it; only the person's pause leaves it paused.
Standard library, plus keyring when the token is not in the environment; works on macOS and Windows.
"""
import argparse
import base64
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from camrig import cloud_account, record
from camrig.project import VIDEO_EXT, Project, ProjectError, load

SCHEMA = "camrig.upload/1"
PROGRESS_SCHEMA = "camrig.upload.progress/1"
MANIFEST_SCHEMA = "camrig.manifest/1"
MIB = 1024**2
HASH_CHUNK = 8 * MIB
MAX_WAIT_S = 600.0
MAX_PARTS = 10_000          # S3's limit per upload
TIMEOUT_S = 120             # a stalled request is given up and tried again, never left hanging
COMPLETE_TIMEOUT_S = 330    # /uploads/complete: the Lambda may take its whole 300 s to join a big file's parts
SESSION = re.compile(r"[a-z0-9_-]{1,64}")
CONTENT_TYPES = {".mov": "video/mp4", ".mp4": "video/mp4", ".m4v": "video/mp4", ".json": "application/json",
                 ".csv": "text/csv", ".toml": "text/plain"}
EXPORTS = {"file": "orbbec_colour.mp4", "depth_video": "orbbec_depth.mp4", "frames_csv": "orbbec_frames.csv"}
# the only moves a file's record makes; anything else is a bug here, not a state to recover from
NEXT = {"waiting": {"hashing", "uploading", "failed"}, "hashing": {"waiting", "uploading", "failed"},
        "uploading": {"waiting", "done", "failed"}, "done": {"waiting"}, "failed": {"waiting"}}


class Paused(Exception):
    """upload/pause.request appeared"""


class Halted(Exception):
    """another piece of the same file already stopped the file; this one stops too"""


class Gone(Exception):
    """the cloud has no such upload (404): it was cancelled or cleaned up, so the file starts again"""


class Changed(Exception):
    """the file changed on disk after its upload started"""


class Refused(Exception):
    """the cloud refused this file (a 4xx answer): it is failed and the others go on"""


class Fatal(Exception):
    """the cloud refuses the token itself: nothing can go up"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data: dict):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def content_type(path: str) -> str:
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def sha256_of(path: Path, offset: int, length: int, seen=None) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        while length > 0:
            b = f.read(min(HASH_CHUNK, length))
            if not b:
                raise Changed(f"{path} became shorter")
            h.update(b)
            length -= len(b)
            if seen:
                seen(len(b))
    return h.digest()


# ---- what goes up, in order ----

@dataclass(frozen=True)
class Entry:
    path: str                   # the key below labs/<lab>/sessions/<session>/
    source: Path
    kind: str
    camera: str | None = None
    recording: str | None = None


def files_to_send(p: Project) -> list[Entry]:
    """the list of docs/UPLOAD.md, "Which files go up", without the manifest; read again on every pass"""
    out = [Entry(f"originals/{c.name}/{v.name}", v, "original", c.name) for c in p.cameras for v in c.videos()]
    for c in p.cameras:
        d = p.lens_folder / c.name
        if d.is_dir():
            out += [Entry(f"originals/lens/{c.name}/{v.name}", v, "original", c.name)
                    for v in sorted(d.iterdir()) if v.is_file() and v.suffix.lower() in VIDEO_EXT]
    out += derived(p)
    sync = [("project.toml", p.path, None)] + [(f"lens/{c.name}.json", c.lens, c.name) for c in p.cameras if c.lens]
    sync += [("board.csv", p.work("scan") / "board.csv", None), ("cameras.json", p.work("cameras") / "cameras.json", None),
             ("clock.json", p.work("clock") / "clock.json", None), ("decisions.json", p.root / "decisions.json", None)]
    sync += [(f"checks/{s}.json", p.work(s) / "check.json", None) for s in ("cameras", "scan", "clock")]
    return out + [Entry(f"sync/{name}", src, "sync", cam) for name, src, cam in sync if src.is_file()]


def derived(p: Project) -> list[Entry]:
    """the depth recordings' exported videos and frame lists, once the cameras step has not failed. An action part's
    file in cameras.json is the original itself, so only depth parts have derived files. Each is found by its place,
    as the cameras step puts it, never by the absolute path in cameras.json: the session folder may have been copied
    or moved since. A listed file that is missing stays in the list, so refresh() fails it."""
    try:
        check = record.read(p.work("cameras"))
        cams = json.loads((p.work("cameras") / "cameras.json").read_text())["cameras"]
    except (OSError, ValueError, KeyError, TypeError):      # not written yet, or being written
        return []
    if check is None or check.status == "fail":
        return []
    exports = {c.name: c.exports for c in p.cameras}
    out = []
    for c in cams:
        if c.get("kind") != "depth":
            continue
        for part in c.get("parts", []):
            folder = exports.get(c["name"], {}).get(part["key"]) or p.work("cameras") / c["name"] / part["key"]
            out += [Entry(f"derived/{c['name']}/{part['key']}/{name}", folder / name, "derived", c["name"], part["key"])
                    for role, name in EXPORTS.items() if part.get(role)]
    return out


# ---- one piece on the wire ----

class Piece:
    """a byte range of a file, read by http.client a block at a time, so a piece is never held in memory whole"""

    def __init__(self, path: Path, offset: int, length: int, seen):
        self.f = open(path, "rb")
        self.f.seek(offset)
        self.left = length
        self.seen = seen

    def read(self, n: int = -1) -> bytes:
        b = self.f.read(self.left if n is None or n < 0 else min(n, self.left))
        self.left -= len(b)
        self.seen(len(b))
        return b

    def close(self):
        self.f.close()


def error_text(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode(errors="replace")
    except OSError:
        return e.reason or ""
    d = None
    try:
        d = json.loads(raw)
    except ValueError:
        pass
    if isinstance(d, dict) and isinstance(d.get("error"), str):
        return d["error"]
    m = re.search(r"<Code>(.*?)</Code>.*?<Message>(.*?)</Message>", raw, re.S)
    return f"{m[1]}: {m[2]}" if m else (raw[:200] or str(e.reason))


# ---- the uploader ----

class Uploader:
    def __init__(self, project: Project, cloud: tuple[str, str], part_size: int, parallel: int, retry_base: float):
        self.p = project
        self.url, self.key = cloud
        self.dir = project.root / "upload"
        self.session = project.root.name
        self.part_size, self.parallel, self.retry_base = part_size, parallel, retry_base
        self.lock = threading.RLock()
        self.halt = threading.Event()
        self.finished = threading.Event()
        self.lab = ""
        self.state = {}
        self.files: dict[str, dict] = {}
        self.entries: dict[str, Entry] = {}
        self.live: dict[str, dict] = {}         # path -> {"held": {n: size}, "sending": {n: bytes}, "hashed": bytes}
        self.run_state, self.error = "running", ""
        self.started, self.sent, self.t_first = now(), 0, None

    # -- small helpers --

    def say(self, line: str):
        print(line, flush=True)
        with open(self.dir / "log.txt", "a") as f:
            f.write(f"{now()} {line}\n")

    def save(self):
        with self.lock:
            write_json(self.dir / "state.json", self.state)

    def move(self, path: str, status: str, error: str = ""):
        with self.lock:
            rec = self.files[path]
            if status != rec["status"] and status not in NEXT[rec["status"]]:
                raise AssertionError(f"{path}: {rec['status']} -> {status} is not a move a file makes")
            rec["status"], rec["error"] = status, error
            self.save()

    def key_of(self, path: str) -> str:
        return f"labs/{self.lab}/sessions/{self.session}/{path}"

    def part_len(self, size: int) -> int:
        """the part size, raised to whole MiB for a file that would need more than 10,000 parts"""
        return max(self.part_size, math.ceil(size / MAX_PARTS / MIB) * MIB)

    def parts_total(self, size: int) -> int:
        return max(1, math.ceil(size / self.part_len(size)))

    def part_range(self, size: int, n: int) -> tuple[int, int]:
        part = self.part_len(size)
        offset = (n - 1) * part
        return offset, min(part, size - offset)

    def check_pause(self):
        if (self.dir / "pause.request").exists():
            raise Paused()

    def wait(self, attempt: int, why: str):
        """30 s, doubling to 10 minutes; pause.request and a stopped file cut the wait short"""
        s = min(self.retry_base * 2 ** min(attempt, 30), MAX_WAIT_S)
        self.error = why
        self.say(f"{why}; trying again in {s:g} s")
        end = time.monotonic() + s
        while (left := end - time.monotonic()) > 0:
            self.check_pause()
            if self.halt.is_set():
                raise Halted()
            time.sleep(min(0.25, left))

    # -- the cloud API --

    def api(self, method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
        """one call; tried again for as long as the cloud cannot be reached or fails (5xx), never given up"""
        attempt = 0
        while True:
            req = urllib.request.Request(self.url + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    answer = json.loads(r.read() or b"{}")
                    self.error = ""
                    return answer
            except urllib.error.HTTPError as e:
                msg = error_text(e)
                if e.code == 401:
                    raise Fatal(cloud_account.SIGNED_OUT)
                if e.code == 404:
                    raise Gone(msg)
                if e.code < 500 and e.code not in (408, 429):
                    raise Refused(f"the cloud refused {path} ({e.code}): {msg}")
                why = f"{path}: the cloud answered {e.code}: {msg}"
            except (OSError, ValueError) as e:           # unreachable, reset, timed out, or an answer that is not JSON
                why = f"{path}: the cloud cannot be reached ({e})"
            self.wait(attempt, why)
            attempt += 1

    def held(self, path: str) -> dict[int, dict]:
        """the parts the cloud holds for this file's upload that are the size this upload's part needs"""
        rec = self.files[path]
        got = self.api("POST", "/uploads/parts", {"key": self.key_of(path), "upload_id": rec["upload_id"]})["parts"]
        held = {p["n"]: p for p in got if p["n"] <= rec["parts_total"]
                and p["size"] == self.part_range(rec["size"], p["n"])[1]}
        with self.lock:
            self.live.setdefault(path, {})["held"] = {n: p["size"] for n, p in held.items()}
        return held

    def forget(self, path: str, why: str, rehash: bool):
        """cancel the file's upload, if any, so it starts again"""
        rec = self.files[path]
        if rec["upload_id"]:
            try:
                self.api("POST", "/uploads/abort", {"key": self.key_of(path), "upload_id": rec["upload_id"]})
            except (Gone, Refused):
                pass
        self.say(f"{path}: {why}, starting it again")
        with self.lock:
            rec["upload_id"] = None
            if rehash:
                rec["sha256"] = None
            self.live.pop(path, None)
            self.move(path, "waiting")

    # -- the state machine over state.json --

    def load_state(self):
        """state.json is a cache: every upload it names is checked against what the cloud holds"""
        s = read_json(self.dir / "state.json")
        if not isinstance(s, dict) or (s.get("schema"), s.get("lab"), s.get("session")) != (SCHEMA, self.lab, self.session):
            s = {"schema": SCHEMA, "lab": self.lab, "session": self.session, "part_size": self.part_size, "files": {}}
        self.state, self.files = s, s["files"]
        resized = s["part_size"] != self.part_size
        for path, rec in self.files.items():
            if rec["status"] in ("hashing", "failed"):
                rec["status"], rec["error"] = "waiting", ""
            if not rec["upload_id"]:
                continue
            if resized:
                self.forget(path, f"the part size changed to {self.part_size // MIB} MiB", rehash=False)
                continue
            try:
                self.held(path)
            except Gone:
                self.forget(path, "the cloud no longer holds its upload", rehash=False)
        s["part_size"] = self.part_size
        for rec in self.files.values():
            rec["parts_total"] = self.parts_total(rec["size"])
        self.save()

    def refresh(self, plan: list[Entry]):
        """bring the records in line with the files on disk: new files wait, changed ones start again"""
        self.entries = {e.path: e for e in plan}
        for path in [p for p in self.files if p not in self.entries]:
            if self.files[path]["upload_id"]:
                self.forget(path, "no longer in the session", rehash=True)
            with self.lock:
                del self.files[path]
                self.live.pop(path, None)
        for e in plan:
            rec = self.files.get(e.path)
            try:
                st = e.source.stat()
            except FileNotFoundError:
                if e.kind == "derived" and (rec or {}).get("status") != "done":     # listed by cameras.json, so it must be there
                    with self.lock:
                        self.files.setdefault(e.path, self.new_record(e, 0, 0))
                    self.move(e.path, "failed", f"{e.source} is missing: run the cameras step again")
                continue
            if rec is None:
                with self.lock:
                    self.files[e.path] = self.new_record(e, st.st_size, st.st_mtime_ns)
                continue
            if e.kind in ("sync", "manifest"):                      # small: compared by content on every pass
                try:
                    data = e.source.read_bytes()                    # its size is what was read: it may be rewritten now
                except FileNotFoundError:
                    continue
                sha = hashlib.sha256(data).hexdigest()
                if sha == rec["sha256"]:
                    continue
                if rec["sha256"] is not None:
                    self.forget(e.path, "its content changed", rehash=True)
                with self.lock:
                    rec.update(source=str(e.source), size=len(data), mtime_ns=st.st_mtime_ns, sha256=sha,
                               parts_total=self.parts_total(len(data)))
            elif (st.st_size, st.st_mtime_ns, str(e.source)) != (rec["size"], rec["mtime_ns"], rec["source"]):
                if rec["status"] != "waiting" or rec["upload_id"]:
                    self.forget(e.path, "its size or time changed", rehash=True)
                with self.lock:
                    rec.update(source=str(e.source), size=st.st_size, mtime_ns=st.st_mtime_ns, sha256=None,
                               parts_total=self.parts_total(st.st_size))
        self.save()

    def new_record(self, e: Entry, size: int, mtime_ns: int) -> dict:
        return {"source": str(e.source), "size": size, "mtime_ns": mtime_ns, "sha256": None, "kind": e.kind,
                "content_type": content_type(e.path), "upload_id": None, "parts_total": self.parts_total(size),
                "status": "waiting", "error": ""}

    def unchanged(self, path: str) -> bool:
        rec = self.files[path]
        try:
            st = Path(rec["source"]).stat()
        except FileNotFoundError:
            return False
        return (st.st_size, st.st_mtime_ns) == (rec["size"], rec["mtime_ns"])

    def run(self) -> str:
        """the passes until every file is done. progress.json says done one pass before the end: a change made before
        that pass is sent by this run, and whoever changes a file after it sees done and starts a new uploader"""
        while True:
            self.refresh(self.plan())
            todo = [p for p, e in self.entries.items() if self.files.get(p, {}).get("status") not in (None, "done", "failed")]
            if not todo or self.entries[todo[0]].kind == "manifest":
                failed = [p for p, r in self.files.items() if r["status"] == "failed"]
                if failed:
                    self.error = f"{len(failed)} file(s) failed, first {failed[0]}: {self.files[failed[0]]['error']}"
                    return "failed"
                if self.write_manifest():
                    continue
                if not todo:
                    if self.run_state == "done":
                        return "done"
                    self.run_state = "done"
                    self.report()
                    continue
            self.run_state = "running"
            self.upload(todo[0])

    def plan(self) -> list[Entry]:
        plan = files_to_send(self.p)
        if (self.dir / "manifest.json").exists():
            plan.append(Entry("manifest.json", self.dir / "manifest.json", "manifest"))
        return plan

    def write_manifest(self) -> bool:
        """write upload/manifest.json when the files it lists changed; its `made` changes only then, so an
        unchanged session reaches done"""
        files = []
        for e in self.entries.values():
            rec = self.files.get(e.path)
            if e.kind == "manifest" or rec is None:
                continue
            f = {"path": e.path, "kind": e.kind, "size": rec["size"], "sha256": rec["sha256"]}
            files += [{**f, **({"camera": e.camera} if e.camera else {}), **({"recording": e.recording} if e.recording else {})}]
        old = read_json(self.dir / "manifest.json")
        if isinstance(old, dict) and (old.get("lab"), old.get("session"), old.get("files")) == (self.lab, self.session, files):
            return False
        write_json(self.dir / "manifest.json",
                   {"schema": MANIFEST_SCHEMA, "lab": self.lab, "session": self.session, "made": now(), "files": files})
        return True

    # -- one file --

    def upload(self, path: str):
        while True:
            try:
                return self.upload_once(path)
            except Gone:
                self.forget(path, "the cloud no longer holds its upload", rehash=False)
            except (Changed, FileNotFoundError):  # the next pass reads its new size and time
                return self.forget(path, "it changed on disk", rehash=True)
            except Refused as e:
                self.say(f"{path}: FAILED: {e}")
                self.move(path, "failed", str(e))
                return

    def upload_once(self, path: str):
        rec = self.files[path]
        if rec["sha256"] is None:
            self.hash_file(path)
        if not rec["upload_id"]:
            self.move(path, "uploading")
            r = self.api("POST", "/uploads/start", {"session": self.session, "path": path, "size": rec["size"],
                                                    "sha256": rec["sha256"], "kind": rec["kind"],
                                                    "content_type": rec["content_type"]})
            if r.get("done"):
                self.say(f"{path}: already in the cloud")
                return self.move(path, "done")
            with self.lock:
                rec["upload_id"] = r["upload_id"]
            self.save()
            self.say(f"{path}: upload started, {rec['parts_total']} part(s)")
        self.move(path, "uploading")
        while True:
            held = self.held(path)
            missing = [n for n in range(1, rec["parts_total"] + 1) if n not in held]
            if not missing:
                break
            self.send(path, missing)
        if not self.unchanged(path):
            raise Changed()
        self.api("POST", "/uploads/complete", {
            "key": self.key_of(path), "upload_id": rec["upload_id"], "size": rec["size"],
            "parts": [{"n": n, "etag": held[n]["etag"], "sha256_b64": held[n]["sha256_b64"]} for n in sorted(held)]},
            timeout=COMPLETE_TIMEOUT_S)
        with self.lock:
            rec["upload_id"] = None
        self.move(path, "done")
        self.say(f"{path}: done, {rec['size']} bytes")

    def hash_file(self, path: str):
        """the whole file's SHA-256, its bytes so far shown in progress.json; pause.request stops it"""
        rec = self.files[path]
        self.move(path, "hashing")
        live = self.live.setdefault(path, {})
        live["hashed"] = 0

        def seen(n: int):
            live["hashed"] += n
            self.check_pause()

        digest = sha256_of(Path(rec["source"]), 0, rec["size"], seen).hex()
        if not self.unchanged(path):
            raise Changed()
        with self.lock:
            rec["sha256"] = digest

    def send(self, path: str, missing: list[int]):
        """the missing parts, `parallel` at a time. A pause, a change or a failed piece stops new pieces; the ones in
        flight finish, then the first reason is raised. A SIGTERM does not wait for them: the cleanup runs at once and
        they end on their own, the halt stopping any retry."""
        pending, running, stop = list(missing), {}, None
        self.halt.clear()
        pool = ThreadPoolExecutor(self.parallel)
        try:
            while pending or running:
                while pending and stop is None and len(running) < self.parallel:
                    if (self.dir / "pause.request").exists():
                        stop = Paused()
                    elif not self.unchanged(path):
                        stop = Changed()
                    else:
                        n = pending.pop(0)
                        running[pool.submit(self.send_part, path, n)] = n
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for f in done:
                    running.pop(f)
                    if f.exception() and not isinstance(f.exception(), Halted):
                        stop = stop or f.exception()
                        self.halt.set()
                if stop:
                    pending.clear()
                    self.halt.set()
                    if isinstance(stop, Paused):
                        self.run_state = "pausing"
        except BaseException:
            self.halt.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown()
        self.halt.clear()
        if stop:
            raise stop

    def send_part(self, path: str, n: int):
        rec = self.files[path]
        key, upload_id, source = self.key_of(path), rec["upload_id"], Path(rec["source"])
        offset, length = self.part_range(rec["size"], n)
        link, renewed, attempt = None, False, 0
        while True:
            if link is None:                     # the link is signed with the piece's SHA-256, so hash it just before
                digest = base64.b64encode(sha256_of(source, offset, length)).decode()
                link = self.api("POST", "/uploads/links", {"key": key, "upload_id": upload_id,
                                                          "parts": [{"n": n, "sha256_b64": digest}]})["links"][0]
            status, why = self.put(path, n, source, offset, length, link)
            if status == 200:
                self.error = ""
                self.say(f"{path}: part {n} of {rec['parts_total']} sent, {length} bytes")
                return
            if status == 404:
                raise Gone(why)
            if not self.unchanged(path):
                raise Changed()
            if status in (400, 403):             # expired or refused link, or the bytes changed under the checksum
                link = None
                if status == 403 and not renewed:
                    renewed = True
                    self.say(f"{path}: part {n}: link refused ({why}), asking for a new link")
                    continue
            self.wait(attempt, f"{path}: part {n}: {why or status}")
            attempt += 1

    def put(self, path: str, n: int, source: Path, offset: int, length: int, link: dict) -> tuple[int, str]:
        live = self.live.setdefault(path, {})
        sending = live.setdefault("sending", {})
        sending[n] = 0

        def seen(k: int):
            with self.lock:
                sending[n] += k
                self.sent += k
                self.t_first = self.t_first or time.monotonic()

        body = Piece(source, offset, length, seen)
        req = urllib.request.Request(link["url"], data=body, method="PUT",
                                     headers={**link["headers"], "Content-Length": str(length)})
        status, why = 0, ""
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status, why = e.code, error_text(e)
        except OSError as e:
            why = f"the piece did not reach the cloud ({e})"
        finally:
            body.close()
            with self.lock:
                got = sending.pop(n, 0)
                if status == 200:
                    live.setdefault("held", {})[n] = length
                else:
                    self.sent -= got
        return status, why

    # -- progress.json --

    def progress(self) -> dict:
        with self.lock:
            files = []
            for path, rec in self.files.items():
                live = self.live.get(path, {})
                held = live.get("held", {})
                if rec["status"] == "done":
                    done, parts = rec["size"], rec["parts_total"]
                elif rec["status"] == "hashing":
                    done, parts = live.get("hashed", 0), 0
                else:
                    done, parts = sum(held.values()) + sum(live.get("sending", {}).values()), len(held)
                files.append({"path": path, "kind": rec["kind"], "size": rec["size"], "bytes_done": done,
                              "parts_done": parts, "parts_total": rec["parts_total"], "status": rec["status"],
                              "error": rec["error"]})
            total = sum(f["size"] for f in files)
            done = sum(f["bytes_done"] for f in files if f["status"] != "hashing")
            rate = self.sent / (time.monotonic() - self.t_first) if self.t_first and self.sent > 0 else 0.0
            return {"schema": PROGRESS_SCHEMA, "pid": os.getpid(), "state": self.run_state, "started": self.started,
                    "updated": now(), "bytes_total": total, "bytes_done": done, "rate_bps": round(rate),
                    "eta_s": round((total - done) / rate) if rate > 0 else None, "error": self.error, "files": files}

    def report(self):
        try:
            write_json(self.dir / "progress.json", self.progress())
        except OSError:                          # the viewer has it open on Windows: next second
            pass

    def report_every_second(self):
        while not self.finished.wait(1.0):
            self.report()

    # -- the whole run --

    def main(self) -> int:
        threading.Thread(target=self.report_every_second, daemon=True).start()
        let_sleep = keep_awake()
        try:
            self.lab = self.api("GET", "/health").get("lab")
            if not self.lab:                             # /health names the lab only for a token it accepts
                raise Fatal(cloud_account.SIGNED_OUT)
            self.load_state()
            self.run_state = self.run()
            self.say(f"session {self.session}: {self.run_state}" + (f": {self.error}" if self.error else ""))
        except Paused:
            with self.lock:
                for path, rec in self.files.items():
                    if rec["status"] == "hashing":
                        self.move(path, "waiting")
                self.run_state = "paused"
            self.report()
            (self.dir / "pause.request").unlink(missing_ok=True)
            self.say(f"session {self.session}: paused")
        except SystemExit:
            if self.run_state == "done":                 # its last look for changes was cut short
                self.run_state = "running"
            self.say(f"session {self.session}: stopped by the system; the next start goes on")
            raise
        except (Fatal, Refused, Gone) as e:
            self.run_state, self.error = "failed", str(e)
            self.say(f"session {self.session}: FAILED: {e}")
        except Exception as e:
            self.run_state, self.error = "failed", f"the uploader broke: {type(e).__name__}: {e}"
            self.say(f"session {self.session}: FAILED: {self.error}")
            raise
        finally:
            let_sleep()
            self.finished.set()
            self.report()
        return 1 if self.run_state == "failed" else 0


def keep_awake():
    """keep the computer from sleeping while this process runs; returns the function that lets it sleep again"""
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])   # -w: ends with us, even on a kill
        except OSError:
            return lambda: None
        return p.terminate
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        k.SetThreadExecutionState(0x80000001)                                    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        return lambda: k.SetThreadExecutionState(0x80000000)
    return lambda: None


def take_lock(folder: Path, block: bool, name: str = "lock.os"):
    """upload/<name>, locked by the OS until release_lock() or the end of this process, however it ends; None when
    another process holds it and block is False. lock.os is the running uploader's, wait.os the one waiting uploader's.
    Neither is ever deleted: a process waiting on one would then hold a lock nobody else sees. upload/lock only tells
    others the running uploader's process id (demo.py stops it by that id), so a waiter never writes it."""
    f = open(folder / name, "a")
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if not block:
                f.close()
                return None
            time.sleep(0.5)
            continue
        if name == "lock.os":
            (folder / "lock").write_text(str(os.getpid()))
        return f


def release_lock(folder: Path, held):
    """on Windows the byte is unlocked before the file closes: Microsoft's _locking says a region should be unlocked
    before closing the file or exiting, and leaves an unlock at close to the system"""
    if Path(held.name).name == "lock.os":
        (folder / "lock").unlink(missing_ok=True)
    if os.name == "nt":
        import msvcrt
        held.seek(0)
        msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
    held.close()


def write_progress(folder: Path, state: str, error: str):
    """progress.json of an uploader that stopped before it had any files"""
    write_json(folder / "progress.json", {"schema": PROGRESS_SCHEMA, "pid": os.getpid(), "state": state, "started": now(),
                                          "updated": now(), "bytes_total": 0, "bytes_done": 0, "rate_bps": 0,
                                          "eta_s": None, "error": error, "files": []})


def refuse(toml: Path, why: str) -> int:
    """the reason in a failed progress.json too, so the viewer and anyone else see it, not only this terminal; not over
    the progress of an uploader of the session that runs, and no upload folder beside a path that is no project file"""
    print(f"The upload cannot start: {why}")
    try:
        if toml.is_file():
            folder = toml.parent / "upload"
            folder.mkdir(exist_ok=True)
            held = take_lock(folder, block=False)
            if held:
                write_progress(folder, "failed", why)
                release_lock(folder, held)
    except OSError:
        pass
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project")
    ap.add_argument("--part-mb", type=int, default=250, help="part size in MiB, 5 to 5120 (default 250)")
    ap.add_argument("--parallel", type=int, default=3, help="parts in flight (default 3)")
    ap.add_argument("--retry-base", type=float, default=30.0, help="first wait after a failure, in seconds (default 30)")
    a = ap.parse_args(argv)
    if not 5 <= a.part_mb <= 5120 or a.parallel < 1 or a.retry_base <= 0:
        ap.error("--part-mb must be 5 to 5120, --parallel at least 1, --retry-base above 0")
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        project = load(a.project)
    except ProjectError as e:
        return refuse(Path(a.project).expanduser().resolve(), str(e))
    folder = project.root / "upload"
    if not SESSION.fullmatch(project.root.name):
        return refuse(project.path, f"the session folder's name {project.root.name!r} must be lowercase letters, digits, "
                                    "_ and - (it names the session in the cloud): rename the folder")
    folder.mkdir(exist_ok=True)
    try:
        cloud = cloud_account.settings()
    except cloud_account.CloudError as e:
        print(f"The sign-in cannot be read: {e}")
        cloud = None
    if cloud is None:
        write_progress(folder, "waiting_for_cloud", "Sign in to upload")
        print("Not signed in to the cloud, so nothing was uploaded. Sign in in the app, then start the upload again.")
        return 0
    lock = take_lock(folder, block=False)
    if lock is None:
        waiter = take_lock(folder, block=False, name="wait.os")
        if waiter is None:
            print("an uploader already runs for this session and another waits to follow it, sending everything after "
                  "it, so this one is not needed")
            return 0
        other = read_json(folder / "lock")
        print(f"an uploader already runs for this session (process {other}); this one starts once it has finished",
              flush=True)
        try:
            lock = take_lock(folder, block=True)
        finally:
            release_lock(folder, waiter)
        if (read_json(folder / "progress.json") or {}).get("state") == "paused":
            print("it paused, so this one stops too: start the upload again to go on")
            release_lock(folder, lock)
            return 0
    try:
        return Uploader(project, cloud, a.part_mb * MIB, a.parallel, a.retry_base).main()
    finally:
        release_lock(folder, lock)


if __name__ == "__main__":
    sys.exit(main())
