"""One clock for a recording session: which frame of which camera shows a given moment.

    from clock import Session
    s = Session("JOB_DIR")
    s.frame_at("cam1", 2700.0)        -> (file path, frame index) or None
    s.clock_of("cam1", file, index)   -> clock seconds
    s.orbbec_instants(2690, 2712)     -> [(clock seconds, frame index in orbbec_colour.mp4, bag key)] real frames only
    s.read("cam3", 2700.0)            -> BGR frame (None when not recording); keeps one reader per file
Clock seconds are sync.json's: the Orbbec device clock shifted so that 0 is the first start of the stopwatch.
"""
import csv
import json
import os

import cv2
import numpy as np


class Session:
    def __init__(self, job):
        self.job = job
        self.sync = json.load(open(os.path.join(job, "sync.json")))
        self.cams = json.load(open(os.path.join(job, "cameras.json")))
        self.clock0 = self.sync["reference"]["clock0"]
        self.ref = self.sync["reference"]["camera"]
        ref = self.sync["camera_clocks"][self.ref]
        origin = ref["origin_device_us"] / 1e6
        self.bags = {}
        for key, rel in ref["bags"].items():
            rows = [r for r in csv.DictReader(open(os.path.join(job, rel))) if r["stream"] == "colour"]
            t = np.array([int(r["device_us"]) / 1e6 - origin + self.clock0 for r in rows])
            real = np.array([r["real"] == "1" for r in rows])
            self.bags[key] = dict(clock=t, real=real, video=os.path.join(job, os.path.dirname(rel), "orbbec_colour.mp4"))
        self._caps = {}

    def cameras(self):
        return list(self.sync["cameras"]) + [self.ref]

    def frame_at(self, cam, clock):
        """(file, frame index) of camera cam nearest to clock seconds, None if it was not recording"""
        if cam == self.ref:
            best = None
            for key, b in self.bags.items():
                i = int(np.abs(b["clock"] - clock).argmin())
                d = abs(b["clock"][i] - clock)
                if b["real"][i] and d < 0.1 and (best is None or d < best[0]):
                    best = (d, b["video"], i)
            return None if best is None else best[1:]
        t_ref = clock - self.clock0
        for p in self.sync["cameras"][cam]["parts"]:
            a = p["ref_s_at_frame0"]
            if a is not None and a <= t_ref < a + p["frames"] / p["fps"]:
                return os.path.join(self.job, p["file"]), int(round((t_ref - a) * p["fps"]))
        return None

    def clock_of(self, cam, file, index):
        if cam == self.ref:
            for b in self.bags.values():
                if b["video"] == file:
                    return float(b["clock"][index])
        for p in self.sync["cameras"][cam]["parts"]:
            if os.path.join(self.job, p["file"]) == file:
                return p["ref_s_at_frame0"] + index / p["fps"] + self.clock0
        raise KeyError(file)

    def parts(self, cam):
        """[(file, frames, fps, clock seconds of frame 0)] in order"""
        return [(os.path.join(self.job, p["file"]), p["frames"], p["fps"], p["ref_s_at_frame0"] + self.clock0)
                for p in self.sync["cameras"][cam]["parts"] if p["ref_s_at_frame0"] is not None]

    def orbbec_instants(self, c_from, c_to):
        """[(clock seconds, frame index, bag key)] of the Orbbec's real colour frames between the two clock seconds"""
        out = []
        for key, b in self.bags.items():
            m = (b["clock"] >= c_from) & (b["clock"] <= c_to) & b["real"]
            out += [(float(t), int(i), key) for i, t in zip(np.nonzero(m)[0], b["clock"][m])]
        return sorted(out)

    def read(self, cam, clock):
        hit = self.frame_at(cam, clock)
        if hit is None:
            return None
        file, n = hit
        cap = self._caps.get(file)
        if cap is None:
            cap = self._caps[file] = [cv2.VideoCapture(file), -1]
        if cap[1] + 1 != n or n < cap[1]:
            if not (0 <= n - cap[1] - 1 <= 30):
                cap[0].set(cv2.CAP_PROP_POS_FRAMES, n)
                cap[1] = n - 1
            while cap[1] + 1 < n:
                cap[0].grab()
                cap[1] += 1
        ok, img = cap[0].read()
        cap[1] = n
        return img if ok else None
