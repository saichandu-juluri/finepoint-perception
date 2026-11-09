"""decisions.json: every choice the tool made by itself, with its evidence, and the person's overrides.

Each step owns one section: {"auto": {...}, "evidence": {...}, "user": {...}}. A step rewrites "auto" and "evidence"
on every run and never touches "user". get() returns the person's value where there is one. The viewer writes
"user" entries too, for example a part moved by hand in Multi-view:

    "clock": {"user": {"manual": {"cam1/part07": {"start_s": 5614.7, "note": "moved by hand"}}}}

A step runs for minutes between reading the file and saving it, and the person may save in the viewer meanwhile. So
save() writes only what this process changed, onto the file as it is then, under .decisions.json.lock, which the
viewer takes too (viewer/src/server/manual.ts).

Standard library only. JSON, so the viewer (TypeScript) reads and writes it without a library.
"""
import contextlib
import copy
import json
import os
import time
from pathlib import Path

SCHEMA = "camrig.decisions/1"
STALE_S = 30.0          # a lock this old was left by a writer that died: a write takes milliseconds


@contextlib.contextmanager
def locked(path: Path):
    lock = Path(path).with_name(".decisions.json.lock")
    t0 = time.time()
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STALE_S:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > 2 * STALE_S:
                raise TimeoutError(f"{lock} stays locked")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    data = json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA}
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {data.get('schema')} is not {SCHEMA}")
    return data


class Decisions:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = _read(self.path)
        self.base = copy.deepcopy(self.data)             # the file as this process read it

    def section(self, step: str) -> dict:
        return self.data.setdefault(step, {"auto": {}, "evidence": {}, "user": {}})

    def get(self, step: str, key: str, default=None):
        s = self.data.get(step, {})
        if key in s.get("user", {}):
            return s["user"][key]
        return s.get("auto", {}).get(key, default)

    def user(self, step: str) -> dict:
        return self.data.get(step, {}).get("user", {})

    def set_auto(self, step: str, values: dict, evidence: dict | None = None):
        """replace what the step chose last time; the person's entries stay"""
        s = self.section(step)
        s["auto"] = dict(values)
        s["evidence"] = dict(evidence or {})

    def save(self):
        """each auto, evidence or user part this process changed replaces that part in the file as it is now"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with locked(self.path):
            disk = _read(self.path)
            for step, sec in self.data.items():
                if step == "schema":
                    continue
                for part, value in sec.items():
                    if value != self.base.get(step, {}).get(part, {}):
                        disk.setdefault(step, {"auto": {}, "evidence": {}, "user": {}})[part] = value
            tmp = self.path.with_suffix(".json.part")
            tmp.write_text(json.dumps(disk, indent=1, sort_keys=True))
            os.replace(tmp, self.path)
        self.data, self.base = disk, copy.deepcopy(disk)
