"""Put the cameras of one recording session on one clock: the sync steps, one command each. The app's clock step
(camrig/pipeline/clock.py) uses only motion() and motion_lag() from here.

Run with the core environment's Python:  python steps/sync.py STEP ...   (STEP -h: that step's notes)
  stopwatch  VIDEO FIRST_FRAME LAST_FRAME OUT.csv [--step N] [--crop X,Y,W,H] [--orient up,down] [--ignore X,Y,W,H ...] [--stamp]
  gaps       JOB_DIR CAMERA [--window 180]
  motion     JOB_DIR [CAMERA ...] [--window 60] [--search 8]
  audio      JOB_DIR TARGET SOURCE [--window 60] [--search 8]
  fit        JOB_DIR                                          (assembles sync.json; not the stopwatch step)
  check      JOB_DIR FROM_S TO_S OUT.mp4
steps/clock.py reads the result.
"""
import argparse
import csv
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PATTERN = re.compile(r"^(\d{2})[:.](\d{2})[. ]?(\d{2})$")   # a stamp like 12:31:47 keeps its second colon and is left out
STAMP = re.compile(r"^(\d{2})[:.](\d{2})[:.](\d{2})$")       # the AKASO's burnt-in clock, HH:MM:SS
STAMP_BOX = (0, 0, 480, 70)
RATE = 8000
SIZE = (160, 90)
REFERENCE = "orbbec"      # its device clock follows the Mac's clock within 0.001 %; the action cameras drift against it
MOTION_OK = dict(peak=0.4, spread_s=0.06)   # two windows 15 min apart agreeing is the real test; the peak is broad, so no runner-up ratio
W, H = 640, 360
FFMPEG = os.path.join(os.path.dirname(sys.executable), "ffmpeg")      # this environment's build, also when pixi did not set PATH
FFMPEG = FFMPEG if os.path.exists(FFMPEG) else "ffmpeg"


def parse(text, stamp=False):
    """seconds shown, or None. stamp=True reads a camera's HH:MM:SS clock stamp instead of the stopwatch."""
    m = (STAMP if stamp else PATTERN).match(text.strip())
    if not m or int(m.group(2)) > 59 or int(m.group(3 if stamp else 2)) > 59:
        return None
    if stamp:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 100


def read(video, first, last, step=1, crop=None, orient="up,down", ignore=(), stamp=False):
    """[(frame, reading_s, text, orientation, confidence, x, y)] for frames first..last, every step-th."""
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    rows, tmp = [], tempfile.mkdtemp(prefix="stopwatch_")
    paths = {}
    n = first
    while n <= last:
        ok, img = cap.read()
        if not ok:
            break
        if (n - first) % step == 0:
            if crop:
                x, y, w, h = crop
                img = img[y:y + h, x:x + w]
            p = os.path.join(tmp, f"{n:08d}.jpg")
            cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            paths[p] = (n, img.shape[1], img.shape[0])
        n += 1
    files = sorted(paths)
    out = subprocess.run(["xargs", "-P", "6", "-n", "40", os.path.join(HERE, "stopwatch_ocr")], input="\n".join(files),
                         capture_output=True, text=True, env={**os.environ, "OCR_ORIENT": orient}).stdout
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) != 8 or f[0] not in paths:
            continue
        s = parse(f[2], stamp)
        if s is None or float(f[3]) < 0.5:
            continue
        frame, w, h = paths[f[0]]
        bx, by, bw, bh = map(float, f[4:8])
        rx, ry = bx + bw / 2, 1 - by - bh / 2                 # Vision's box: normalised, origin bottom-left, in the image as oriented
        if f[1] == "down":
            rx, ry = 1 - rx, 1 - ry
        cx, cy = rx * w, ry * h                                # left and right stay in their rotated frame; only up and down are placed
        if crop:
            cx, cy = cx + crop[0], cy + crop[1]
        if any(x <= cx < x + bw_ and y <= cy < y + bh_ for x, y, bw_, bh_ in ignore) and f[1] in ("up", "down"):
            continue
        rows.append((frame, s, f[2], f[1], float(f[3]), round(cx), round(cy)))
    for p in files:
        os.remove(p)
    os.rmdir(tmp)
    rows.sort()
    return rows


def cmd_stopwatch(a):
    """Read the stopwatch a camera is looking at, frame by frame.

    Run:  python steps/sync.py stopwatch VIDEO FIRST_FRAME LAST_FRAME OUT.csv [--step N] [--crop X,Y,W,H] [--ignore X,Y,W,H ...]
    Pulls every N-th frame by index (default every frame), runs Apple's text recogniser on it (./stopwatch_ocr, built from
    stopwatch_ocr.swift) the right way up and upside down, keeps what looks like a stopwatch (MM:SS.hh, also with the dot
    lost or read as a space), and writes OUT.csv: frame, reading_s, text, orientation, confidence, x, y (centre, full-frame px).
    --crop limits the search to one region and is faster. --ignore drops anything read inside a region of the full frame:
    the AKASO cameras burn their clock into the top-left corner (about 0,0,480,70 at 1080p) and it reads like a stopwatch.
    """
    rows = read(a.video, a.first, a.last, a.step, a.crop, a.orient, a.ignore, a.stamp)
    with open(a.out, "w") as fh:
        fh.write("frame,reading_s,text,orientation,confidence,x,y\n")
        for r in rows:
            fh.write(",".join(str(v) for v in r) + "\n")
    frames = sorted({r[0] for r in rows})
    print(f"{len(frames)} frames read out of {(a.last - a.first) // a.step + 1} looked at"
          + (f", {frames[0]} to {frames[-1]}" if frames else "") + f", written to {a.out}")


def clock_at_frame0(video, first, last, fps):
    """(camera clock at frame 0 of this file, half-width of its uncertainty), from the stamp ticks in frames first..last"""
    rows = read(video, first, last, crop=STAMP_BOX, orient="up", stamp=True)
    if not rows:
        return None, None
    f = np.array([r[0] for r in rows], float); v = np.array([r[1] for r in rows])
    lower = v - f / fps                                           # each reading says: clock at frame 0 is in [lower, lower + 1)
    best = None
    for lo in lower:                                              # the interval most readings agree on; a misread digit is 1 s or 10 s off
        inside = (lower <= lo) & (lower + 1 > lo)
        hi = (lower[inside] + 1).min()
        if best is None or inside.sum() > best[0]:
            best = (int(inside.sum()), lo, hi)
    n, lo, hi = best
    if n < 0.6 * len(lower):
        return None, None
    return (lo + hi) / 2, (hi - lo) / 2


def cmd_gaps(a):
    """A first guess of the time an action camera loses at each 4 GB file split, from its own burnt-in clock.

    Run:  python steps/sync.py gaps JOB_DIR CAMERA [--window N]
    For every pair of consecutive parts of CAMERA in JOB_DIR/cameras.json, the clock stamp (HH:MM:SS in the top-left
    corner) is read in the last N frames of the earlier part and the first N frames of the later one (default 180). The
    frame where the stamp ticks fixes the stamp clock to one frame. Written into cameras.json as gap_before_s of the later
    part, which sync.py fit uses only as a prior for parts no stopwatch, sound or motion anchor covers.

    Good to about 0.25 s at best: the AKASO restarts its stamp clock at every new file on a half-second boundary (the clock
    at frame 0 of every part ends in .06 or .56), so the loss comes out as a multiple of 0.5 s. Measured on 2 Oct 2026:
    1.4 to 3.0 s per split from the stamps, 1.66 to 2.59 s from the stopwatch, sound and motion anchors; the stamp-based
    guess was within 0.1 s of the anchored loss at 9 of the 13 splits and 0.27 to 0.45 s off at the other 4.
    """
    path = os.path.join(a.job, "cameras.json")
    cams = json.load(open(path))
    c = cams[a.camera]
    fps = c["fps"]
    for prev, part in zip(c["parts"], c["parts"][1:]):
        end_clock, e_unc = clock_at_frame0(os.path.join(a.job, prev["file"]), prev["frames"] - a.window, prev["frames"] - 1, fps)
        start_clock, s_unc = clock_at_frame0(os.path.join(a.job, part["file"]), 0, a.window - 1, fps)
        if end_clock is None or start_clock is None:
            print(f"{prev['key']} -> {part['key']}: stamp not read")
            continue
        last_frame_clock = end_clock + (prev["frames"] - 1) / fps
        gap = start_clock - last_frame_clock - 1 / fps
        part["gap_before_s"] = round(float(gap), 4)
        print(f"{prev['key']} -> {part['key']}: {gap * 1000:+7.0f} ms lost at the split  (clock known to +-{(e_unc + s_unc) * 1000:.0f} ms)")
    json.dump(cams, open(path, "w"), indent=1)
    print("wrote", path)


def audio(path, start_s, dur_s):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start_s:.6f}", "-t", f"{dur_s:.6f}", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE),
                        "-f", "f32le", "pipe:1"], capture_output=True)
    return np.frombuffer(r.stdout, np.float32)


def source_at(sync, cam, t_ref):
    """(part, seconds into it) of camera cam at Orbbec second t_ref, or None"""
    for p in sync["cameras"][cam]["parts"]:
        a = p["ref_s_at_frame0"]
        if a is not None and a <= t_ref < a + p["frames"] / p["fps"]:
            return p, t_ref - a
    return None, None


def audio_lag(x, y, search):
    """lag (s) of y relative to x with the highest normalised correlation, the peak, and its ratio to the runner-up"""
    x = (x - x.mean()) / (x.std() + 1e-9); y = (y - y.mean()) / (y.std() + 1e-9)
    n = len(x)
    corr = np.correlate(y, x, mode="valid") / n                 # y is longer by 2*search*RATE samples
    i = int(np.argmax(corr))
    lags = (np.arange(len(corr)) / RATE) - search
    far = np.abs(lags - lags[i]) > 0.05
    runner = corr[far].max() if far.any() else 0.0
    return float(lags[i]), float(corr[i]), float(corr[i] / max(runner, 1e-6))


def cmd_audio(a):
    """Anchor the parts of one action camera to another camera's clock by the sound both recorded.

    Run:  python steps/sync.py audio JOB_DIR TARGET SOURCE [--window 60] [--search 8]
    For every part of TARGET (say cam3) two windows of its sound are correlated with SOURCE's sound (say cam1, whose parts
    are anchored already) around the prior from sync.json. The best lag gives the SOURCE time of the TARGET window, and
    through SOURCE's anchor the Orbbec second of the TARGET part's frame 0. Writes anchor_audio and audio_quality (peak
    correlation, ratio to the runner-up, how far the two windows disagree) into cameras.json; sync.py fit uses it.
    Sound at 8 kHz mono; the two cameras stand about a metre apart, so the sound reaches them within 3 ms of each other.
    """
    cams = json.load(open(os.path.join(a.job, "cameras.json")))
    sync = json.load(open(os.path.join(a.job, "sync.json")))
    for p, ps in zip(cams[a.target]["parts"], sync["cameras"][a.target]["parts"]):
        prior = ps["ref_s_at_frame0"]
        length = p["frames"] / cams[a.target]["fps"]
        results = []
        for w0 in (10.0, length - a.window - 10.0):
            if w0 < 0 or w0 + a.window > length:
                continue
            sp, st = source_at(sync, a.source, prior + w0 - a.search)
            sp2, _ = source_at(sync, a.source, prior + w0 + a.window + a.search)
            if sp is None or sp2 is None or sp["key"] != sp2["key"]:
                print(f"{a.target} {p['key']} window at {w0:.0f} s: {a.source} has no single part covering it")
                continue
            x = audio(os.path.join(a.job, p["file"]), w0, a.window)
            y = audio(os.path.join(a.job, sp["file"]), st, a.window + 2 * a.search)
            if len(x) < a.window * RATE * 0.9 or len(y) < (a.window + 2 * a.search) * RATE * 0.9:
                print(f"{a.target} {p['key']} window at {w0:.0f} s: sound too short")
                continue
            lag, peak, ratio = audio_lag(x, y[: int((a.window + 2 * a.search) * RATE)], a.search)
            t_ref = sp["ref_s_at_frame0"] + st + a.search + lag          # Orbbec second of the target window's start
            results.append(dict(window_s=w0, anchor=t_ref - w0, peak=round(peak, 3), ratio=round(ratio, 1)))
            print(f"{a.target} {p['key']} window at {w0:6.0f} s: frame 0 at Orbbec {t_ref - w0:10.3f} s  (prior {prior:10.3f}, "
                  f"peak {peak:.3f}, {ratio:.1f}x the runner-up, via {a.source} {sp['key']})")
        if results:
            p["anchor_audio"] = float(np.mean([r["anchor"] for r in results]))
            spread = max(r["anchor"] for r in results) - min(r["anchor"] for r in results)
            p["audio_quality"] = dict(peak=min(r["peak"] for r in results), ratio=min(r["ratio"] for r in results),
                                      windows_spread_ms=round(spread * 1000), via=a.source, windows=results)
            print(f"{a.target} {p['key']}: anchor {p['anchor_audio']:.3f} s, windows differ by {spread * 1000:.0f} ms")
    json.dump(cams, open(os.path.join(a.job, "cameras.json"), "w"), indent=1)
    print("wrote cameras.json")


def motion(path, start_s, dur_s, fps):
    """mean absolute change between consecutive frames, one value per frame interval, from start_s for dur_s"""
    n = int(round(dur_s * fps)) + 1
    # software decode: the same values as -hwaccel videotoolbox to the last bit, 6x faster at 160x90, and not Mac-only (1 Nov 2026)
    r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{start_s:.6f}", "-t", f"{(n + 2) / fps:.6f}", "-i", path,
                        "-vf", f"scale={SIZE[0]}:{SIZE[1]},format=gray", "-f", "rawvideo", "pipe:1"], capture_output=True)
    frames = np.frombuffer(r.stdout, np.uint8)
    frames = frames[: len(frames) // (SIZE[0] * SIZE[1]) * SIZE[0] * SIZE[1]].reshape(-1, SIZE[1], SIZE[0]).astype(np.float32)[:n]
    return np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))


def orbbec_signal(job, clocks, t_from, t_to):
    """(Orbbec seconds of each interval start, motion) covering t_from..t_to, from whichever bag holds that stretch"""
    origin = clocks["origin_device_us"] / 1e6
    for key, rel in clocks["bags"].items():
        rows = [r for r in csv.DictReader(open(os.path.join(job, rel))) if r["stream"] == "colour"]
        dev = np.array([int(r["device_us"]) / 1e6 - origin for r in rows])
        real = np.array([r["real"] == "1" for r in rows])
        if dev[0] <= t_from and t_to <= dev[-1]:
            a = int(np.searchsorted(dev, t_from)) - 1
            b = int(np.searchsorted(dev, t_to)) + 1
            video = os.path.join(job, os.path.dirname(rel), "orbbec_colour.mp4")
            m = motion(video, a / 15, (b - a) / 15, 15)
            k = min(len(m), b - a)
            t, m, ok = dev[a:a + k], m[:k], real[a + 1:a + 1 + k]
            return t[ok], m[ok]
    return None, None


def motion_lag(cam_sig, cam_fps, orb_t, orb_sig, prior, search):
    """Orbbec second of the camera window's first frame: the lag with the highest correlation, refined between steps"""
    c = np.convolve(cam_sig, np.ones(4) / 4, mode="same")                   # about one Orbbec interval of smoothing
    c = (c - c.mean()) / (c.std() + 1e-9)
    n = len(c)
    step = 1 / cam_fps
    grid = np.arange(prior - search, prior + search + n * step, step)
    o = np.interp(grid, orb_t, orb_sig)
    o = (o - o.mean()) / (o.std() + 1e-9)
    lags = np.arange(-search, search + step / 2, step)
    corr = np.array([np.dot(c, o[k:k + n]) / n for k in range(len(lags))])
    i = int(np.argmax(corr))
    far = np.abs(lags - lags[i]) > 0.5
    runner = corr[far].max() if far.any() else 0.0
    if 0 < i < len(corr) - 1:                                                # parabola through the three top points
        y0, y1, y2 = corr[i - 1:i + 2]
        i_ref = i + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) < 0 else i
    else:
        i_ref = i
    return prior + (i_ref * step - search), float(corr[i]), float(corr[i] / max(runner, 1e-6))


def prior_anchor(sync, cam, part_key):
    """Orbbec second of the part's frame 0 as sync.json has it now (stopwatch, or the previous part plus the split loss)"""
    for p in sync["cameras"][cam]["parts"]:
        if p["key"] == part_key:
            return p["ref_s_at_frame0"]


def cmd_motion(a):
    """Anchor every 4 GB part of an action camera to the Orbbec's clock by the motion both cameras see.

    Run:  python steps/sync.py motion JOB_DIR [CAMERA ...] [--window 60] [--search 8]
    Why: the action cameras lose about 2 s at each file split (1.66 to 2.59 s by the anchors of 2 Oct, 1.4 to 3 s read
    from the burnt-in clock), and that clock restarts every file on a half-second boundary, so the loss read from it is
    known to about 0.25 s at best (on 2 Oct up to 0.45 s off). The stopwatch pins the parts it was shown in;
    the others are pinned here. For each part two windows (after its start and before its end) of mean frame-to-frame
    change, 160x90 grey, are correlated with the same signal from the Orbbec colour video over a search range around the
    prior from sync.json (a stopwatch anchor, or the previous part plus the split loss), and the best lag gives the Orbbec second of the part's
    frame 0. Writes ref_s_at_frame0_motion (mean of the windows), motion_peak (correlation, ratio to the runner-up) and
    motion_windows into cameras.json; sync.py fit prefers a stopwatch anchor where one exists.
    """
    path = os.path.join(a.job, "cameras.json")
    cams = json.load(open(path))
    sync = json.load(open(os.path.join(a.job, "sync.json")))
    for cam in a.cameras or [k for k, v in cams.items() if v["kind"] == "parts"]:
        c = cams[cam]
        for p in c["parts"]:
            prior = prior_anchor(sync, cam, p["key"])
            if prior is None:
                print(f"{cam} {p['key']}: no prior anchor in sync.json, run sync.py fit first")
                continue
            length = p["frames"] / c["fps"]
            results = []
            for w0 in (10.0, length - a.window - 10.0):
                if w0 < 0 or w0 + a.window > length:
                    continue
                t_from, t_to = prior + w0 - a.search - 1, prior + w0 + a.window + a.search + 1
                ot, om = orbbec_signal(a.job, sync["camera_clocks"]["orbbec"], t_from, t_to)
                if ot is None:
                    print(f"{cam} {p['key']} window at {w0:.0f} s: the Orbbec was not recording then")
                    continue
                cm = motion(os.path.join(a.job, p["file"]), w0, a.window, c["fps"])
                anchor_w, peak, ratio = motion_lag(cm, c["fps"], ot, om, prior + w0, a.search)
                results.append(dict(window_s=w0, ref_s_at_frame0=anchor_w - w0, peak=round(peak, 3), ratio=round(ratio, 2)))
                print(f"{cam} {p['key']} window at {w0:6.0f} s: frame 0 at Orbbec {anchor_w - w0:10.3f} s  (prior {prior:10.3f}, "
                      f"peak {peak:.3f}, {ratio:.1f}x the runner-up)")
            if results:
                p["ref_s_at_frame0_motion"] = float(np.mean([r["ref_s_at_frame0"] for r in results]))
                p["motion_windows"] = results
                p["motion_peak"] = min(r["peak"] for r in results)
                spread = max(r["ref_s_at_frame0"] for r in results) - min(r["ref_s_at_frame0"] for r in results)
                print(f"{cam} {p['key']}: anchor {p['ref_s_at_frame0_motion']:.3f} s, windows differ by {spread * 1000:.0f} ms")
    json.dump(cams, open(path, "w"), indent=1)
    print("wrote", path)


def orbbec_table(c, part, job):
    """video frame -> (Orbbec seconds, real frame?) for one bag's colour stream, counted from the main bag's first frame"""
    table = c.setdefault("_tables", {})
    for key in ({part, "main"} if "main" in c["bags"] else {part}):
        if key not in table:
            rows = [r for r in csv.DictReader(open(os.path.join(job, c["bags"][key]))) if r["stream"] == "colour"]
            table[key] = {int(r["frame"]): (int(r["device_us"]) / 1e6, r["real"] == "1") for r in rows}
    if "_origin" not in c:
        c["_origin"] = table["main" if "main" in c["bags"] else part][0][0]
    return {k: (t - c["_origin"], real) for k, (t, real) in table[part].items()}


def fit(pairs):
    """mean of (reading - time) with far-off readings dropped: offset, spread, n, dropped"""
    t = np.array([p[0] for p in pairs]); sw = np.array([p[1] for p in pairs])
    off = sw - t
    keep = np.abs(off - np.median(off)) < 0.15                 # a misread digit is far off; the display itself is within 0.03
    return float(off[keep].mean()), float(off[keep].std()), int(keep.sum()), int((~keep).sum()), float(t[keep].mean())


def cmd_fit(job):
    """Put the cameras of one recording session on one clock.

    Run:  python steps/sync.py fit JOB_DIR
    Reads JOB_DIR/cameras.json and JOB_DIR/stopwatch/<camera>_<part>_<event>.csv (readings from sync.py stopwatch; part is
    partNN for a split action-camera recording, start or main for an Orbbec bag; event is start, mid or end). Writes
    JOB_DIR/sync.json and prints the table.

    The clock: the Orbbec's device clock, counted from the main bag's first colour frame, shifted so that 0 is the first
    start of the stopwatch. The stopwatch may be restarted between showings; within one showing every camera reads the
    same display, which ties that camera to the Orbbec at that moment.

    An action camera's recording is a chain of 4 GB parts, and each part is its own clock: frame / fps from its first
    frame. Each part needs one anchor, the Orbbec second of its frame 0. Sources, best first: a stopwatch showing inside
    the part; the sound shared with another camera (sync.py audio); the motion shared with the Orbbec (sync.py motion);
    failing all, the previous part's end plus the loss at the split read from the burnt-in clock (sync.py gaps), good to
    about 0.25 s at best by its half-second rounding (on 2 Oct up to 0.45 s off against the other anchors). Where several
    sources exist the table shows how far they are apart.
    """
    cams = json.load(open(os.path.join(job, "cameras.json")))
    readings = {}                                              # (cam, part, event) -> [(part seconds, reading)]
    for path in sorted(glob.glob(os.path.join(job, "stopwatch", "*.csv"))):
        m = re.match(r"^(cam\d+|orbbec)_(.+)_(start|mid|end)\.csv$", os.path.basename(path))
        if not m:
            continue
        cam, part, event = m.groups()
        per_frame = {}
        for r in csv.DictReader(open(path)):
            per_frame.setdefault(int(r["frame"]), []).append(float(r["reading_s"]))
        for frame, vals in per_frame.items():
            if cams[cam]["kind"] == "parts":
                t = frame / cams[cam]["fps"]
            else:
                t, real = orbbec_table(cams[cam], part, job)[frame]
                if not real:
                    continue
            readings.setdefault((cam, part, event), []).append((t, float(np.median(vals))))
    ref_events = {}                                            # event -> (stopwatch offset over Orbbec seconds, spread, n, dropped, Orbbec s)
    for (cam, part, event), pairs in readings.items():
        if cam == REFERENCE:
            ref_events[event] = fit(pairs)
    if not ref_events:
        sys.exit("no stopwatch readings for the Orbbec: nothing to anchor to")
    first = min(ref_events, key=lambda e: ref_events[e][4])
    clock0 = ref_events[first][0]                               # clock = Orbbec seconds + clock0
    out = dict(reference=dict(camera=REFERENCE, clock0=clock0, first_event=first, rule="clock_s = Orbbec seconds + clock0"),
               events={e: dict(orbbec_s=v[4], stopwatch_offset=v[0], spread_ms=round(v[1] * 1000), n=v[2]) for e, v in ref_events.items()},
               cameras={}, camera_clocks={k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in cams.items()})
    out["camera_clocks"][REFERENCE]["origin_device_us"] = int(round(cams[REFERENCE]["_origin"] * 1e6))
    lines = [f"stopwatch showings on the Orbbec: " + ", ".join(f"{e} at Orbbec {v[4]:.1f} s ({v[2]} readings, spread {v[1] * 1000:.0f} ms)" for e, v in ref_events.items())]
    for cam, c in cams.items():
        if c["kind"] != "parts":
            continue
        parts_out, prev_end = [], None
        for p in c["parts"]:
            cand = {}
            for (rc, rp, ev), pairs in readings.items():
                if rc == cam and rp == p["key"] and ev in ref_events:
                    off, spread, n, dropped, _ = fit(pairs)
                    cand["stopwatch"] = dict(anchor=off - ref_events[ev][0], event=ev, n=n, spread_ms=round(spread * 1000), dropped=dropped)
            if "anchor_audio" in p:
                cand["audio"] = dict(anchor=p["anchor_audio"], **p.get("audio_quality", {}))
            if "ref_s_at_frame0_motion" in p:
                w = p["motion_windows"]
                spread = max(x["ref_s_at_frame0"] for x in w) - min(x["ref_s_at_frame0"] for x in w) if len(w) > 1 else 0.0
                good = p["motion_peak"] >= MOTION_OK["peak"] and len(w) > 1 and spread <= MOTION_OK["spread_s"]
                cand["motion"] = dict(anchor=p["ref_s_at_frame0_motion"], peak=p["motion_peak"], ratio=min(x["ratio"] for x in w),
                                      windows_spread_ms=round(spread * 1000), good=good)
            if prev_end is not None:
                cand["prior"] = dict(anchor=prev_end + p.get("gap_before_s", 2.3), note="previous part plus the split loss, about +-0.25 s")
            source = next((s for s in ("stopwatch", "audio", "motion", "prior") if s in cand and cand[s].get("good", True)), None)
            if source is None:
                source = next(iter(cand)) if cand else None
            anchor = cand[source]["anchor"] if source else None
            parts_out.append(dict(key=p["key"], file=p["file"], frames=p["frames"], fps=c["fps"], ref_s_at_frame0=anchor, source=source, candidates=cand))
            others = ", ".join(f"{s} {v['anchor'] - anchor:+.3f} s" for s, v in cand.items() if s != source) if anchor is not None else ""
            lines.append(f"{cam} {p['key']}: frame 0 at Orbbec {anchor if anchor is None else round(anchor, 3)} s  from {source}"
                         + (f"  (others: {others})" if others else ""))
            prev_end = None if anchor is None else anchor + p["frames"] / c["fps"]
        out["cameras"][cam] = dict(kind="parts", fps=c["fps"], parts=parts_out)
    json.dump(out, open(os.path.join(job, "sync.json"), "w"), indent=1)
    print("\n".join(lines))
    print("wrote", os.path.join(job, "sync.json"))


def shown(sync, clock):
    """what the stopwatch in the picture should read at this clock second, if a showing is near"""
    t_ref = clock - sync["reference"]["clock0"]
    near = [e for e, v in sync["events"].items() if abs(v["orbbec_s"] - t_ref) < 120]
    if not near:
        return ""
    s = t_ref + sync["events"][near[0]]["stopwatch_offset"]
    return f"   stopwatch in picture ({near[0]} showing): {int(s // 60):02d}:{int(s % 60):02d}.{int(round((s % 1) * 100)) % 100:02d}"


def cmd_check(job, a, b, out):
    """A side-by-side clip of all cameras on the stopwatch's clock, to check the sync by eye.

    Run:  python steps/sync.py check JOB_DIR FROM_S TO_S OUT.mp4
    FROM_S and TO_S are seconds of the common clock (0 = the first start of the stopwatch). Every output frame (15 per
    second) shows each camera's frame nearest to that instant under sync.json, with the clock written on it and, near a
    stopwatch showing, what the stopwatch in the picture should read. Its digits must agree in every panel.
    Frames come from clock.py (Session): a camera with no frame at that instant (not recording, or a hole the Orbbec filled with a copy, real=0) shows black.
    """
    from clock import Session  # only this step reads frames; the other steps do not need clock.py
    ses = Session(job)
    sync = ses.sync
    cams = sorted(sync["cameras"]) + [sync["reference"]["camera"]]
    sw = np.arange(a, b, 1 / 15)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W * len(cams)}x{H + 40}",
                            "-framerate", "15", "-i", "pipe:0", "-an", "-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p", out],
                           stdin=subprocess.PIPE)
    for s in sw:
        label = f"{int(s // 60):02d}:{int(s % 60):02d}.{int(round((s % 1) * 100)) % 100:02d}"
        row = []
        for cam in cams:
            img = ses.read(cam, s)
            img = np.zeros((H, W, 3), np.uint8) if img is None else cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
            cv2.putText(img, cam, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(img, cam, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
            row.append(img)
        frame = np.vstack([np.hstack(row), np.zeros((40, W * len(cams), 3), np.uint8)])
        cv2.putText(frame, f"clock {label}{shown(sync, s)}", (10, H + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
        enc.stdin.write(frame.tobytes())
    enc.stdin.close(), enc.wait()
    print(f"wrote {out}: {len(sw)} frames, stopwatch {a} to {b} s, cameras {cams}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    def step(name, handler):
        return sub.add_parser(name, help=handler.__doc__.splitlines()[0], description=handler.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    st, ga, au, mo = step("stopwatch", cmd_stopwatch), step("gaps", cmd_gaps), step("audio", cmd_audio), step("motion", cmd_motion)
    st.add_argument("video"), st.add_argument("first", type=int), st.add_argument("last", type=int), st.add_argument("out")
    st.add_argument("--step", type=int, default=1)
    st.add_argument("--crop", type=lambda s: tuple(int(v) for v in s.split(",")))
    st.add_argument("--orient", default="up,down")
    st.add_argument("--ignore", action="append", default=[], type=lambda s: tuple(int(v) for v in s.split(",")))
    st.add_argument("--stamp", action="store_true", help="read the camera's HH:MM:SS clock stamp instead")
    ga.add_argument("job"), ga.add_argument("camera"), ga.add_argument("--window", type=int, default=180)
    au.add_argument("job"), au.add_argument("target"), au.add_argument("source")
    au.add_argument("--window", type=float, default=60), au.add_argument("--search", type=float, default=8)
    mo.add_argument("job"), mo.add_argument("cameras", nargs="*")
    mo.add_argument("--window", type=float, default=60), mo.add_argument("--search", type=float, default=8)
    step("fit", cmd_fit).add_argument("job")
    ck = step("check", cmd_check)
    ck.add_argument("job"), ck.add_argument("from_s", type=float), ck.add_argument("to_s", type=float), ck.add_argument("out")
    a = p.parse_args()
    if a.cmd == "fit":
        cmd_fit(a.job)
    elif a.cmd == "check":
        cmd_check(a.job, a.from_s, a.to_s, a.out)
    else:
        {"stopwatch": cmd_stopwatch, "gaps": cmd_gaps, "audio": cmd_audio, "motion": cmd_motion}[a.cmd](a)


if __name__ == "__main__":
    main()
