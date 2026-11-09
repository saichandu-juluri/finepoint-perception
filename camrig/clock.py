"""The one clock of a session: work/clock/clock.json, and the frame of any camera at any clock second.

    from camrig.clock import Clock
    c = Clock("my_session/work/clock/clock.json")
    c.frame_at("cam1", 2700.0)          -> (file, frame index) or None
    c.clock_of("cam1", file, index)     -> clock seconds
    c.instants("orbbec", 2690, 2712)    -> [(clock seconds, file, frame index)], real frames only
    c.read("cam3", 2700.0)              -> BGR picture, or None when that camera was not recording

clock.json, schema camrig.clock/1:
    {"schema": "camrig.clock/1", "reference": "orbbec", "zero": "what clock second 0 is",
     "cameras": {"cam1": {"kind": "action", "parts": [
                     {"key": "part01", "file": "/abs/cam1_part01.MOV", "frames": 75300, "fps": 60.0,
                      "start_s": -78.035, "rate": 1.0, "source": "sound", "flag": null}]},
                 "orbbec": {"kind": "depth", "parts": [
                     {"key": "main", "file": "/abs/orbbec_colour.mp4", "frames": 89826, "fps": 15.0,
                      "frames_csv": "/abs/orbbec_frames.csv", "start_s": 1012.528, "rate": 1.0, "source": "reference", "flag": null}]}}}

Clock seconds of frame i of a part = start_s + rate * t_i. t_i is i / fps, or, for a part with frames_csv, the device time of
frame i less that of frame 0, because the depth camera's frames are not evenly spaced. frames_csv rows with real=0 are fillers
for missing frames and are never returned. "flag" holds a reason when the clock step is not sure of that part.
"""
import csv
import json
from pathlib import Path

import numpy as np

from camrig.record import replace

SCHEMA = "camrig.clock/1"


class Part:
    def __init__(self, d: dict):
        self.d = d
        self.key, self.file, self.frames, self.fps = d["key"], d["file"], int(d["frames"]), float(d["fps"])
        self.start_s, self.rate = float(d["start_s"]), float(d.get("rate", 1.0))
        self.t, self.real = None, None
        if d.get("frames_csv"):
            rows = [r for r in csv.DictReader(open(d["frames_csv"])) if r.get("stream", "colour") == "colour"]
            dev = np.array([int(r["device_us"]) for r in rows], np.int64)
            self.t = (dev - dev[0]) / 1e6
            self.real = np.array([r.get("real", "1") == "1" for r in rows])
            self.frames = len(rows)

    def clock(self, i):
        t = self.t[i] if self.t is not None else np.asarray(i) / self.fps
        return self.start_s + self.rate * t

    def table(self):
        """clock seconds of every frame of a part with frames_csv, made once (the depth camera's 90,000 frames)"""
        if not hasattr(self, "_tt"):
            self._tt = self.clock(np.arange(self.frames))
        return self._tt

    def first(self) -> float:
        return float(self.clock(0))

    def last(self) -> float:
        return float(self.clock(self.frames - 1))


class Clock:
    def __init__(self, source):
        self.data = source if isinstance(source, dict) else json.loads(Path(source).read_text())
        if self.data.get("schema") != SCHEMA:
            raise ValueError(f"clock schema {self.data.get('schema')} is not {SCHEMA}")
        self.ref = self.data["reference"]
        self.kind = {cam: c["kind"] for cam, c in self.data["cameras"].items()}
        self._parts = {cam: [Part(p) for p in c["parts"]] for cam, c in self.data["cameras"].items()}
        self._caps = {}

    def cameras(self) -> list[str]:
        return list(self._parts)

    def parts(self, cam) -> list[Part]:
        return self._parts[cam]

    def span(self, cam) -> tuple[float, float]:
        ps = self._parts[cam]
        return min(p.first() for p in ps), max(p.last() for p in ps)

    def frame_at(self, cam, clock):
        """(file, frame index) nearest to clock seconds, None if that camera was not recording then"""
        best = None
        for p in self._parts[cam]:
            if p.t is not None:
                tt = p.table()
                j = int(np.searchsorted(tt, clock))
                i = j if j < len(tt) and (j == 0 or tt[j] - clock < clock - tt[j - 1]) else j - 1
                d = abs(tt[i] - clock)
                if p.real[i] and d < 0.1 and (best is None or d < best[0]):
                    best = (d, p.file, i)
            elif p.start_s <= clock < p.start_s + p.rate * p.frames / p.fps:
                return p.file, min(int(round((clock - p.start_s) / p.rate * p.fps)), p.frames - 1)
        return None if best is None else best[1:]

    def clock_of(self, cam, file, index) -> float:
        for p in self._parts[cam]:
            if p.file == file:
                return float(p.clock(index))
        raise KeyError(file)

    def instants(self, cam, c_from, c_to) -> list[tuple[float, str, int]]:
        """every real frame of cam between the two clock seconds: (clock seconds, file, frame index), in time order"""
        out = []
        for p in self._parts[cam]:
            idx = np.arange(p.frames)
            tt = p.table() if p.t is not None else p.clock(idx)
            m = (tt >= c_from) & (tt <= c_to)
            if p.real is not None:
                m &= p.real
            out += [(float(t), p.file, int(i)) for t, i in zip(tt[m], idx[m])]
        return sorted(out)

    def read(self, cam, clock):
        import cv2
        hit = self.frame_at(cam, clock)
        if hit is None:
            return None
        file, n = hit
        cap = self._caps.get(file)
        if cap is None:
            cap = self._caps[file] = [cv2.VideoCapture(file), -1]
        if not (0 <= n - cap[1] - 1 <= 30):
            cap[0].set(cv2.CAP_PROP_POS_FRAMES, n)
            cap[1] = n - 1
        while cap[1] + 1 < n:
            cap[0].grab()
            cap[1] += 1
        ok, img = cap[0].read()
        cap[1] = n
        return img if ok else None


def write(path: Path, data: dict):
    data = dict(data, schema=SCHEMA)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(data, indent=1))
    replace(tmp, path)


def from_sync_json(sync_path, names=None) -> dict:
    """a clock made before camrig (the 2 Oct sync.json) as camrig.clock/1, unchanged: same clock seconds, same frames.
    names maps the old camera keys to project camera names (default: the same)."""
    sync_path = Path(sync_path)
    job = sync_path.parent
    s = json.loads(sync_path.read_text())
    names = names or {}
    clock0 = s["reference"]["clock0"]
    ref = s["reference"]["camera"]
    out = {"reference": names.get(ref, ref), "zero": s["reference"].get("rule", "") + f" (clock0 {clock0})", "cameras": {}}
    for cam, c in s["cameras"].items():
        parts = [dict(key=p["key"], file=str(job / p["file"]), frames=p["frames"], fps=p["fps"], start_s=p["ref_s_at_frame0"] + clock0,
                      rate=1.0, source=p.get("source", "existing"), flag=None)
                 for p in c["parts"] if p.get("ref_s_at_frame0") is not None]
        out["cameras"][names.get(cam, cam)] = {"kind": "action", "parts": parts}
    rc = s["camera_clocks"][ref]
    origin = rc["origin_device_us"]
    parts = []
    for key, rel in rc["bags"].items():
        table = job / rel
        rows = [r for r in csv.DictReader(open(table)) if r["stream"] == "colour"]
        dev = np.array([int(r["device_us"]) for r in rows], np.int64)
        fps = float(round(1 / np.median(np.diff(dev) / 1e6)))
        parts.append(dict(key=key, file=str(table.parent / "orbbec_colour.mp4"), frames=len(rows), fps=fps, frames_csv=str(table),
                          start_s=(int(dev[0]) - origin) / 1e6 + clock0, rate=1.0, source="reference", flag=None))
    parts.sort(key=lambda p: p["start_s"])
    out["cameras"][names.get(ref, ref)] = {"kind": "depth", "parts": parts}
    return dict(out, schema=SCHEMA)
