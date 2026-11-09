"""The clock step: every camera's frames on one clock, in work/clock/clock.json (camrig.clock/1).

[clock] existing: a clock made before camrig is kept as it is.
Automatic (no [clock] existing), the route tested on the recordings of 2 Oct 2026:
1. Sound. Eight 45 s pieces of each part are searched in every part of the other cameras. A pair of parts whose pieces
   agree within 30 ms is one equation (start of B minus start of A), and least squares solves all starts together.
   Sound is judged per part: a part without sound is chained to a placed neighbour of its camera, flagged "no sound" as a
   first guess, or left out; it never places the depth camera.
2. Depth camera (no sound). A rough offset from the board seconds of work/scan/board.csv, pooled over all sound cameras;
   then 60 s windows of picture motion give its exact offset and drift. A frame difference is stamped at the middle of its
   gap. Windows of the cameras whose own line has a believable drift (100 ppm) are pooled into one line. On a short
   recording, where the windows span too little time to measure drift, the rate is held at 1 and only the offset is fitted.
3. Each sound part is checked: at least two sound matches, matches that agree within one frame, a normal gap at its split.
   A part that fails is flagged by name. decisions.json clock.user.manual {"cam/part": start_s} overrides a start, or places
   a part nothing else could: fewer than two cameras with sound is not_tested, not a fail, so a session synced by hand works.
   A depth reference camera that nothing lines up stays on its own clock alone; the others then need manual entries.
   An action reference camera that nothing places fails the step, but clock.json keeps the other parts, so it can be placed
   by hand; until then the earliest placed part stands in for clock 0. When no part of any camera has sound, an action
   reference camera's first part stands at clock 0, its other parts are chained, and every other part waits for a manual entry.
Clock 0 is the reference camera's first frame as the clock placed it (project.reference(): the depth camera, else the first
camera); clock seconds run on the reference camera's own clock. Manual entries never move clock 0.
"""
import json
import time

from camrig.clock import from_sync_json, write
from camrig.record import Check, Item, fingerprint, settings_hash

VERSION = "2"
RATE, PIECE_S, PIECES, MIN_RATIO, AGREE_S = 4000, 45.0, 8, 2.5, 0.03          # sound
WINDOW, SEARCH, GOOD_PEAK, MAX_PPM, BOARD_CORNERS = 60.0, 10.0, 0.4, 100.0, 10  # motion
GAP_S = (-0.05, 5.0)       # a split loses 1.4 to 3 s on the AKASO; a camera that loses nothing gives 0


def needs(project):
    """an imported clock needs no board scan"""
    return ("cameras",) if project.clock_existing else ("cameras", "scan")

def inputs(project, decisions):
    if project.clock_existing:
        return [project.clock_existing]
    return [project.work("cameras") / "cameras.json", project.work("scan") / "board.csv"]


def settings(project, decisions):
    return {"existing": str(project.clock_existing) if project.clock_existing else None, "manual": decisions.user("clock").get("manual", {}),
            "reference": project.reference().name, "limits": {k: project.limit(k, d) for k, d in limits().items()}}


def limits():
    return {"split gap min s": GAP_S[0], "split gap max s": GAP_S[1], "depth drift max ppm": MAX_PPM}


def run(project, decisions, out):
    if project.clock_existing:
        return existing(project, out)
    return automatic(project, decisions, out)


def existing(project, out):
    data = from_sync_json(project.clock_existing)
    names = {c.name for c in project.cameras}
    data["cameras"] = {k: v for k, v in data["cameras"].items() if k in names}      # cameras the project leaves out stay out
    if data["reference"] not in names:
        data["reference"] = project.reference().name                                # same clock seconds, another reference
    items = [Item("kept the existing clock", "pass", message=f"from {project.clock_existing}, unchanged")]
    for c in project.cameras:
        if c.name not in data["cameras"]:
            items.append(Item(f"{c.name} on the clock", "flag", message=f"{c.name} is not on the existing clock",
                              fix="remove [clock] existing to sync every camera automatically", where={"camera": c.name}))
    write(out / "clock.json", data)
    return Check("clock", items=items, outputs={"clock": str(out / "clock.json")},
                 numbers={"cameras": len(data["cameras"]), "parts": sum(len(c["parts"]) for c in data["cameras"].values())})


# ---------------------------------------------------------------- pure parts (camrig/test_clock.py runs them on made-up data)

def solve(n, eqs):
    """eqs: (i, j, off, weight) meaning start_i - start_j = off. Least squares over the largest connected group of parts,
    its first part held at 0. Returns (starts, NaN where not connected; residual of each equation, NaN where not used)."""
    import numpy as np
    root = list(range(n))

    def find(i):
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i
    for i, j, *_ in eqs:
        root[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    main = max(groups.values(), key=lambda g: (len(g), -g[0]))
    inside = set(main)
    use = [e for e in eqs if e[0] in inside]
    starts, resid = np.full(n, np.nan), np.full(len(eqs), np.nan)
    if not use:
        starts[main[0]] = 0.0
        return starts, resid
    col = {p: k for k, p in enumerate(main)}
    A = np.zeros((len(use) + 1, len(main)))
    b, w = np.zeros(len(use) + 1), np.ones(len(use) + 1)
    for r, (i, j, off, wt) in enumerate(use):
        A[r, col[i]], A[r, col[j]], b[r], w[r] = 1, -1, off, wt
    A[-1, 0], w[-1] = 1, 1000                                           # the first part of the group is clock 0 of the sound
    x = np.linalg.lstsq(A * w[:, None], b * w, rcond=None)[0]
    for p, k in col.items():
        starts[p] = x[k]
    for r, e in enumerate(eqs):
        if e[0] in inside:
            resid[r] = starts[e[0]] - starts[e[1]] - e[2]
    return starts, resid


def in_loop(n, eqs):
    """per equation: True when its two parts stay joined without it. Only such an equation can disagree with the others;
    one that alone joins its parts gets a residual of 0 from the solve, whatever its sound said."""
    out = []
    for e in range(len(eqs)):
        root = list(range(n))

        def find(i):
            while root[i] != i:
                root[i] = root[root[i]]
                i = root[i]
            return i
        for k, (i, j, *_) in enumerate(eqs):
            if k != e:
                root[find(i)] = find(j)
        out.append(find(eqs[e][0]) == find(eqs[e][1]))
    return out


def best_lag(piece, target):
    """seconds into target where piece fits best, and how far that peak stands above the next one"""
    import numpy as np
    from scipy.signal import fftconvolve
    corr = fftconvolve(target, piece[::-1], mode="valid")
    i = int(np.argmax(corr))
    guard = int(0.25 * RATE)
    mask = np.ones(len(corr), bool)
    mask[max(0, i - guard): i + guard] = False
    runner = np.abs(corr[mask]).max() if mask.any() else 1e-9
    return i / RATE, float(corr[i] / max(runner, 1e-9))


def match(x, y):
    """(start of x in seconds into y, agreeing pieces) or None: PIECES pieces of x searched in y, at least two within AGREE_S"""
    import numpy as np
    n = int(PIECE_S * RATE)
    if len(x) < n + 20 * RATE or len(y) < n:
        return None
    hits = []
    for s in np.linspace(10 * RATE, len(x) - n - 10 * RATE, PIECES).astype(int):
        lag, ratio = best_lag(x[s:s + n], y)
        if ratio >= MIN_RATIO:
            hits.append(lag - s / RATE)
    if len(hits) >= 2:
        h = np.array(hits)
        good = h[np.abs(h - np.median(h)) <= AGREE_S]
        if len(good) >= 2:
            return float(np.median(good)), len(good)
    return None


def rough_offset(depth, action):
    """offset d with depth + d = action, from board seconds: the plateau of best overlap, its count and the best other place"""
    import numpy as np
    lo = int(min(depth.min(), action.min())) - 10
    hi = int(max(depth.max(), action.max())) + 10

    def hist(s):
        g = np.zeros(hi - lo + 1)
        g[np.clip(np.round(s).astype(int) - lo, 0, hi - lo)] = 1
        return g
    a, d = hist(action), hist(depth)
    corr = np.correlate(a, d, mode="full")
    lags = np.arange(-len(d) + 1, len(a))
    best = int(np.argmax(corr))
    plateau = lags[corr >= 0.9 * corr[best]]
    plateau = plateau[np.abs(plateau - lags[best]) <= 5]
    far = np.abs(lags - lags[best]) > 10
    return float(np.median(plateau)), int(corr[best]), int(corr[far].max()) if far.any() else 0


def line(rows):
    """sound = a + b * depth through the windows, far-off windows dropped"""
    import numpy as np
    x = np.array([r["depth"] for r in rows])
    y = np.array([r["sound"] for r in rows])
    keep = np.ones(len(x), bool)
    a, b = float(np.median(y - x)), 1.0
    for _ in range(5):
        if keep.sum() >= 3:
            b, a = np.polyfit(x[keep], y[keep], 1)
        res = y - (a + b * x)
        keep = np.abs(res) < max(0.05, 3 * 1.48 * np.median(np.abs(res[keep])))
    return float(a), float(b), res, keep


def line_held(rows):
    """sound = a + depth, the rate held at 1: the offset alone, far-off windows dropped as in line()"""
    import numpy as np
    d = np.array([r["sound"] - r["depth"] for r in rows])
    keep = np.ones(len(d), bool)
    for _ in range(5):
        a = float(np.median(d[keep]))
        res = d - a
        keep = np.abs(res) < max(0.05, 3 * 1.48 * np.median(np.abs(res[keep])))
    return a, 1.0, res, keep


def pool_windows(got, cams, max_ppm=MAX_PPM):
    """per camera: its own line (>= 3 good windows, drift within max_ppm); the agreeing cameras' windows on one line.
    A camera with fewer than 5 good windows, or whose windows span too little time to tell max_ppm of drift from their scatter
    (span * max_ppm under 3 times the residual median about the line held at rate 1), has no measurable drift. When no camera's drift is measured, the
    windows of those cameras give the offset alone, the rate held at 1 (held=True)."""
    import numpy as np
    med = lambda res, keep: float(np.median(np.abs(res[keep])))
    fits, pooled, short = {}, [], []
    for cam in cams:
        good = [r for r in got if r["cam"] == cam and r["peak"] >= GOOD_PEAK and not r["edge"]]
        fit = dict(windows=sum(r["cam"] == cam for r in got), good=len(good))
        if len(good) >= 3:
            a, b, res, keep = line(good)
            fit.update(kept=int(keep.sum()), drift_ppm=round((b - 1) * 1e6, 1), median_ms=round(med(res, keep) * 1000, 1))
            span = float(max(r["depth"] for r in good) - min(r["depth"] for r in good))
            _, _, hres, hkeep = line_held(good)
            # the scatter about the held line: a free line through 3 or 4 windows fits them almost exactly, so its own residual
            # says nothing about whether the drift is real (3 windows over 200 s once gave 99 ppm against a true 9 ppm)
            noise = med(hres, hkeep)
            if len(good) < 5 or span * max_ppm * 1e-6 < 3 * noise:    # the drift cannot be measured from so few or so close windows
                fit.update(short=True, span_s=round(span, 1), noise_ms=round(noise * 1000, 1), kept=int(hkeep.sum()))
                fit["used"] = False
                short += [r for r, k in zip(good, hkeep) if k]
            else:
                fit["used"] = abs(b - 1) * 1e6 <= max_ppm
                if fit["used"]:
                    pooled += [r for r, k in zip(good, keep) if k]
        else:
            fit["used"] = False
        fits[cam] = fit
    held = len(pooled) < 3 and len(short) >= 3
    if held:
        pooled = short
        for f in fits.values():
            f["used"] = f.get("short", False)
    if len(pooled) < 3:
        return None, fits
    a, b, res, keep = (line_held if held else line)(pooled)
    r = np.abs(res[keep])
    return dict(a=a, b=b, kept=int(keep.sum()), of=len(pooled), drift_ppm=(b - 1) * 1e6, median_ms=float(np.median(r)) * 1000,
                worst_ms=float(r.max()) * 1000, cams=sorted({x["cam"] for x, k in zip(pooled, keep) if k}), held=held), fits


def place(parts, eqs, anchor=None):
    """starts on the sound clock: least squares over the parts with sound only (a silent part never anchors it), then any
    unplaced part chained after or before a placed neighbour of its camera with the usual split gap. eqs index parts.
    When no part has sound, the part at index anchor is held at 0, so its camera can be chained and the others set by hand.
    Returns (starts, NaN where not placed; residual per equation; source per part; the usual gap)."""
    import numpy as np
    heard = [k for k, p in enumerate(parts) if p["sound"]]
    at = {k: n for n, k in enumerate(heard)}
    starts, resid = np.full(len(parts), np.nan), np.full(len(eqs), np.nan)
    if heard:
        starts[heard], resid = solve(len(heard), [(at[i], at[j], *e) for i, j, *e in eqs])
    elif anchor is not None:
        starts[anchor] = 0.0
    gaps = [starts[k + 1] - starts[k] - parts[k]["seconds"] for k in range(len(parts) - 1)
            if parts[k]["cam"] == parts[k + 1]["cam"] and np.isfinite(starts[k]) and np.isfinite(starts[k + 1])]
    usual = float(np.median(gaps)) if gaps else 0.0
    source = ["sound" if np.isfinite(s) else None for s in starts]
    for _ in range(len(parts)):
        for k in range(len(parts)):
            if np.isfinite(starts[k]):
                continue
            if k > 0 and parts[k - 1]["cam"] == parts[k]["cam"] and np.isfinite(starts[k - 1]):
                starts[k], source[k] = starts[k - 1] + parts[k - 1]["seconds"] + usual, "chained"
            elif k + 1 < len(parts) and parts[k + 1]["cam"] == parts[k]["cam"] and np.isfinite(starts[k + 1]):
                starts[k], source[k] = starts[k + 1] - parts[k]["seconds"] - usual, "chained"
    return starts, resid, source, usual


def manual_start(v):
    return float(v["start_s"] if isinstance(v, dict) else v)


# ---------------------------------------------------------------- heavy parts

def tool(name):
    from camrig.pipeline.cameras import tool as t
    return t(name)


def sound_of(path):
    import subprocess
    import numpy as np
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"], capture_output=True)
    x = np.frombuffer(r.stdout, np.float32)
    return (x - x.mean()) / (x.std() + 1e-9) if len(x) else x


def sound_matches(parts, log):
    """every pair of parts of two different cameras: (i, j, start of i in seconds into j, agreeing pieces)"""
    import os
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        snd = list(ex.map(lambda p: sound_of(p["file"]), parts))
    log(f"sound of {len(parts)} parts read in {time.time() - t0:.0f} s")
    jobs = [(i, j) for i, a in enumerate(parts) for j, b in enumerate(parts) if a["cam"] < b["cam"]]

    def one(job):
        r = match(snd[job[0]], snd[job[1]])
        return None if r is None else (job[0], job[1], r[0], r[1])
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:          # the FFTs let go of Python's lock, so threads run in parallel
        eqs = [r for r in ex.map(one, jobs) if r]
    log(f"{len(jobs)} pairs of parts searched, {len(eqs)} matches, {time.time() - t0:.0f} s")
    return eqs


def depth_windows(specs, sound_parts, tables, log):
    """the motion windows: each sound camera against the depth camera, around the rough offset"""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "steps"))
    import sync as S

    def one(spec):
        cam, bag, t_bag, s0 = spec
        hit = [p for p in sound_parts[cam] if p["start"] + SEARCH <= s0 and s0 + WINDOW + SEARCH <= p["start"] + p["seconds"]]
        dev, real, video, fps = tables[bag]
        t_from, t_to = t_bag - SEARCH - 1, t_bag + WINDOW + SEARCH + 1
        if not hit or not (dev[0] <= t_from and t_to <= dev[-1]):
            return None
        p = hit[0]
        a, b = int(np.searchsorted(dev, t_from)) - 1, int(np.searchsorted(dev, t_to)) + 1
        m = S.motion(video, a / fps, (b - a) / fps, fps)
        k = min(len(m), b - a)
        ok = real[a + 1:a + 1 + k]
        ot, om = dev[a:a + k][ok[:k]], m[:k][ok[:k]]
        if len(ot) < 100:
            return None
        ot = ot + 0.5 * float(np.median(np.diff(ot)))              # a frame difference belongs to the middle of its gap
        cm = S.motion(p["file"], s0 - p["start"], WINDOW, p["fps"])
        if len(cm) < 100:
            return None
        t, peak, ratio = S.motion_lag(cm, p["fps"], ot, om, t_bag, SEARCH)
        return dict(cam=cam, bag=bag, depth=float(t), sound=float(s0), peak=peak, ratio=ratio, edge=bool(abs(t - t_bag) > SEARCH - 0.5))
    t0 = time.time()
    with ThreadPoolExecutor(5) as ex:
        got = [r for r in ex.map(one, specs) if r]
    log(f"{len(specs)} motion windows, {len(got)} usable, {time.time() - t0:.0f} s")
    return got


def cached(path, key, make):
    """make() once per key: a rerun that only changes a manual entry reads the sound matches and windows back"""
    if path.exists():
        d = json.loads(path.read_text())
        if d.get("key") == key:
            return d["value"], True
    v = make()
    path.write_text(json.dumps({"key": key, "value": v}))
    return v, False


# ---------------------------------------------------------------- the automatic route

def automatic(project, decisions, out):
    import csv
    import numpy as np
    from camrig.heavy import one_at_a_time
    t_start = time.time()
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)
    cams = json.loads((project.work("cameras") / "cameras.json").read_text())["cameras"]
    items, numbers = [], {}
    gap_lo, gap_hi = project.limit("split gap min s", GAP_S[0]), project.limit("split gap max s", GAP_S[1])
    max_ppm = project.limit("depth drift max ppm", MAX_PPM)
    sound_cams = [c for c in cams if c["kind"] != "depth" and any(p["sound"] for p in c["parts"])]
    depth_cams = [c for c in cams if c["kind"] == "depth"]
    if len(sound_cams) < 2:
        items.append(Item("cameras with sound", "not_tested", value=len(sound_cams), limit=">= 2", unit="cameras",
                          message=f"{len(sound_cams)} camera recorded sound; the automatic clock lines cameras up by the sound two of them share, "
                                  "so the parts it cannot place are set by hand",
                          fix="slide each part flagged or left out into place in Multi-view, or give a clock made another way in [clock] existing"))
    parts = [dict(p, cam=c["name"]) for c in cams if c["kind"] != "depth" for p in c["parts"]]
    of_heard = [k for k, p in enumerate(parts) if p["sound"]]
    heard = [parts[k] for k in of_heard]
    pid = [f"{p['cam']}/{p['key']}" for p in parts]
    waited = 0.0

    # 1. sound
    key = settings_hash({"v": VERSION, "files": [(p["file"], fingerprint(p["file"])) for p in heard],
                         "sound": [RATE, PIECE_S, PIECES, MIN_RATIO, AGREE_S]})

    def heavy_sound():
        nonlocal waited
        t = time.time()
        with one_at_a_time("clock: sound of every part"):
            waited += time.time() - t
            t = time.time()
            r = sound_matches(heard, log)
            numbers["sound_s"] = round(time.time() - t, 1)
            return r
    eqs, hit = cached(out / "sound_matches.json", key, heavy_sound) if len(sound_cams) >= 2 else ([], False)
    if hit:
        log(f"sound matches read back from {out / 'sound_matches.json'}")
    eqs = [(of_heard[i], of_heard[j], *e) for i, j, *e in eqs]
    anchor = None if heard else next((k for k, p in enumerate(parts) if p["cam"] == project.reference().name), None)
    starts, resid, source, usual = place(parts, eqs, anchor)
    count = np.zeros(len(parts), int)
    worst = np.zeros(len(parts))
    looped = np.zeros(len(parts), bool)                                # a match of the part can disagree with the others
    for (i, j, *_), r, loop in zip(eqs, resid, in_loop(len(parts), eqs)):
        for k in (i, j):
            count[k] += 1
            if np.isfinite(r):
                worst[k] = max(worst[k], abs(r))
                looped[k] |= loop
    used = resid[np.isfinite(resid)]
    if len(used):
        log(f"{len(eqs)} sound matches, residual median {np.median(np.abs(used)) * 1000:.1f} ms, worst {np.abs(used).max() * 1000:.1f} ms")
    for c in sound_cams:
        if not any(np.isfinite(starts[k]) for k, p in enumerate(parts) if p["cam"] == c["name"]):
            items.append(Item(f"{c['name']} on the clock", "flag", message=f"no part of {c['name']} shares sound with the other cameras",
                              fix="check that its microphone was on and it stood in the same room; slide its parts into place by hand in Multi-view",
                              where={"camera": c["name"]}))
    for k, p in enumerate(parts):
        p["start"] = float(starts[k])
    linked = [p for p in heard if np.isfinite(p["start"])]           # a silent part's start is a guess: it never places the depth camera
    sound_parts = {c["name"]: [p for p in linked if p["cam"] == c["name"]] for c in sound_cams}
    numbers.update(sound_matches=len(eqs), sound_parts=len(heard))

    # 2. depth cameras: rough offset from the board, then motion windows
    board = list(csv.DictReader(open(project.work("scan") / "board.csv")))
    by_file = {p["file"]: p for p in linked}
    pool = np.array([by_file[r["file"]]["start"] + float(r["t"]) for r in board if r["file"] in by_file and int(r["corners"]) >= BOARD_CORNERS])
    links, evidence, rough_all, line_of, depth_first = {}, {}, {}, {}, {}
    for d in depth_cams:
        name = d["name"]
        tables, first = {}, None
        for p in d["parts"]:
            rows = [r for r in csv.DictReader(open(p["frames_csv"])) if r["stream"] == "colour"]
            dev = np.array([int(r["device_us"]) for r in rows], np.int64)
            first = dev[0] if first is None else min(first, dev[0])
            tables[p["key"]] = [dev, np.array([r["real"] == "1" for r in rows]), p["file"], p["fps"]]
        for t in tables.values():
            t[0] = (t[0] - first) / 1e6                                   # seconds on its device clock from its first frame
        depth_first[name] = {k: float(t[0][0]) for k, t in tables.items()}
        rough = {}
        for p in d["parts"]:
            dev, _, _, fps = tables[p["key"]]
            ts = [dev[min(int(round(float(r["t"]) * fps)), len(dev) - 1)] for r in board
                  if r["camera"] == name and r["part"] == p["key"] and int(r["corners"]) >= BOARD_CORNERS]
            if ts and len(pool):
                rough[p["key"]] = rough_offset(np.array(ts), pool)
                log(f"{name} {p['key']}: rough offset {rough[p['key']][0]:.0f} s, {rough[p['key']][1]} board seconds line up, "
                    f"best other place {rough[p['key']][2]}")
        rough_all[name] = {k: v[0] for k, v in rough.items()}
        if not rough:
            items.append(Item(f"{name} rough offset", "flag" if name == project.reference().name else "fail",    # the reference stands alone
                              message=f"no board second of {name} lines up with a sound camera's",
                              fix="show the board where the depth camera and at least one other camera see it, then run again",
                              where={"camera": name}) if len(pool) else
                         Item(f"{name} rough offset", "not_tested", message=f"no camera was placed by sound, so there are no board seconds "
                              f"to line {name} up with", fix=f"slide {name} by hand in Multi-view unless it is the reference camera",
                              where={"camera": name}))
            continue
        offs = [v[0] for v in rough.values()]
        told = "; ".join(f"{k}: {v[0]:.0f} s from {v[1]} board seconds (best other place {v[2]})" for k, v in rough.items())
        if len(rough) == 1:
            items.append(Item(f"{name} rough offset", "not_tested", message=f"{told}; one recording, so there are no recordings to agree",
                              where={"camera": name}))
        else:
            items.append(Item(f"{name} rough offset", "pass" if max(offs) - min(offs) <= 5 else "flag", value=round(max(offs) - min(offs), 1),
                              limit="<= 5", unit="s between its recordings", message=told, fix="check the board showings of each recording",
                              where={"camera": name}))
        specs = []
        for c in sound_cams:
            for p in d["parts"]:
                if p["key"] not in rough:
                    continue
                dev = tables[p["key"]][0]
                lo, hi = float(dev[0]), float(dev[-1])
                n = max(5, min(10, round((hi - lo) / 600)))
                specs += [(c["name"], p["key"], float(w), float(w + rough[p["key"]][0])) for w in np.linspace(lo + 15, hi - WINDOW - 25, n)]
        wkey = settings_hash({"v": VERSION, "specs": specs, "parts": [(p["file"], round(p["start"], 4)) for p in linked],
                              "depth": [(p["file"], fingerprint(p["frames_csv"])) for p in d["parts"]], "w": [WINDOW, SEARCH]})

        def heavy_windows():
            nonlocal waited
            t = time.time()
            with one_at_a_time(f"clock: motion windows of {name}"):
                waited += time.time() - t
                t = time.time()
                r = depth_windows(specs, sound_parts, tables, log)
                numbers["windows_s"] = round(time.time() - t, 1)
                return r
        got, hit = cached(out / f"windows_{name}.json", wkey, heavy_windows)
        if hit:
            log(f"motion windows read back from {out / f'windows_{name}.json'}")
        fit, fits = pool_windows(got, [c["name"] for c in sound_cams], max_ppm)
        for cam, f in fits.items():
            log(f"{name} by {cam}: {f}")
        if fit is None:
            off = float(np.median(offs))
            line_of[name] = (off, 1.0)
            items.append(Item(f"{name} exact offset", "flag", value=0, limit=">= 3", unit="windows", message=f"no camera's motion agreed with "
                              f"{name}'s; it is placed by the board alone, to about a second",
                              fix=f"check {name} against a clap or a hand movement in Multi-view; " + (
                                  f"{name} is the reference camera, so it cannot slide: slide every part of the other cameras by the same amount"
                                  if name == project.reference().name else f"slide {name} by hand there"),
                              where={"camera": name}))
            evidence[f"depth_link {name}"] = f"rough offset only, {off:.1f} s"
            links[name] = []
            continue
        line_of[name] = (fit["a"], fit["b"])
        links[name] = fit["cams"]
        if fit["held"]:
            say = (f"{fit['kept']} of {fit['of']} windows agree with the rate held at 1, offset {fit['a']:.4f} s, residual median "
                   f"{fit['median_ms']:.1f} ms, worst {fit['worst_ms']:.1f} ms, cameras {', '.join(fit['cams'])}")
            why = "; ".join(f"{c} has only {f['good']} good windows, too few to measure a drift" if f["good"] < 5 else
                            f"{c}'s windows span {f['span_s']:.0f} s, and {max_ppm:g} ppm over that is {f['span_s'] * max_ppm * 1e-3:.0f} ms, "
                            f"under 3 times their scatter of {f['noise_ms']:.1f} ms" for c, f in fits.items() if f["used"])
            log(f"{name}: {say}; drift not measured: {why}")
            evidence["depth_link" if len(depth_cams) == 1 else f"depth_link {name}"] = say + "; drift held at 1, not measured"
            items.append(Item(f"{name} exact offset", "pass", value=fit["kept"], limit=">= 3", unit="windows", message=say, where={"camera": name}))
            length = max(float(t[0][-1]) for t in tables.values())          # its device seconds from its first frame to its last
            reach_ms, frame_ms = length * max_ppm * 1e-3, 1000.0 / max(float(t[3]) for t in tables.values())
            far = reach_ms > frame_ms                                        # holding the rate could matter at the far end
            items.append(Item(f"{name} drift", "flag" if far else "not_tested", value=round(reach_ms), limit=f"<= {frame_ms:.1f}", unit="ms",
                              message=f"drift held at 1, not measured: {why}; over {name}'s {length:.0f} s, a drift within {max_ppm:g} ppm "
                                      f"moves the clock by up to {reach_ms:.0f} ms, {'more' if far else 'less'} than one frame ({frame_ms:.1f} ms)",
                              fix=f"check {name} near the start and the end of the session against a clap or a hand movement in Multi-view"
                                  if far else "nothing to do: over so short a recording a drift within the limit moves the clock by less than one frame",
                              where={"camera": name}))
            numbers[f"{name}_windows"] = fit["kept"]
            continue
        say = (f"{fit['kept']} of {fit['of']} windows on one line, drift {fit['drift_ppm']:+.1f} ppm, residual median {fit['median_ms']:.1f} ms, "
               f"worst {fit['worst_ms']:.1f} ms, cameras {', '.join(fit['cams'])}")
        log(f"{name}: {say}")
        evidence["depth_link" if len(depth_cams) == 1 else f"depth_link {name}"] = say
        items.append(Item(f"{name} exact offset and drift", "pass" if abs(fit["drift_ppm"]) <= max_ppm else "flag", value=round(fit["drift_ppm"], 1),
                          limit=f"within {max_ppm:g}", unit="ppm", message=say + "; left out: " + (", ".join(
                              f"{c} ({f['good']} good windows" + (f", drift {f['drift_ppm']:+.0f} ppm)" if "drift_ppm" in f else ")")
                              for c, f in fits.items() if not f["used"]) or "none"), fix=f"check {name} against a sound camera in Multi-view",
                          where={"camera": name}))
        numbers[f"{name}_windows"] = fit["kept"]

    # 3. the reference camera's own clock, zero at its first frame
    ref = project.reference().name
    in_sound = {}                                                      # (start, rate) of every part on the sound clock
    for k, p in enumerate(parts):
        if np.isfinite(p["start"]):
            in_sound[pid[k]] = (p["start"], 1.0)
    for name, (a, b) in line_of.items():
        for key, d0 in depth_first[name].items():
            in_sound[f"{name}/{key}"] = (a + b * d0, b)
    if ref in depth_first and ref not in line_of:     # on its own device clock it needs no link; the sound clock has none to it
        for cam in sorted({k.split("/")[0] for k in in_sound} - {ref}):
            items.append(Item(f"{cam} on the clock", "flag", message=f"left out of clock.json: the reference camera {ref} could not be "
                              f"lined up with {cam}", fix=f"slide {cam}'s parts into place by hand in Multi-view", where={"camera": cam}))
        in_sound = {f"{ref}/{key}": (d0, 1.0) for key, d0 in depth_first[ref].items()}
        items.append(Item(f"{ref} linked to the other cameras", "not_tested", message=f"the reference camera {ref} could not be lined up "
                          "with any other camera, so it alone is placed automatically", fix="slide the other cameras' parts into place by "
                          "hand in Multi-view", where={"camera": ref}))
    a_r, b_r = line_of.get(ref, (0.0, 1.0))
    ref_parts = [v for k, v in in_sound.items() if k.split("/")[0] == ref]
    on_sound = [k for k in in_sound if k in pid]                       # action parts: rate 1 on the sound clock
    stand_in = min(on_sound, key=lambda k: in_sound[k][0]) if on_sound and not ref_parts and ref not in depth_first else None
    if stand_in:                                                       # an action reference nothing placed: placed by hand later
        ref_parts = [in_sound[stand_in]]
    zero = min((s - a_r) / b_r for s, _ in ref_parts) if ref_parts else None
    final = {k: ((s - a_r) / b_r - zero, r / b_r) for k, (s, r) in in_sound.items()} if ref_parts else {}

    # 4. manual entries win, when they still name the file they were made for
    file_of = {f"{c['name']}/{p['key']}": p["file"].replace("\\", "/").rsplit("/", 1)[-1] for c in cams for p in c["parts"]}
    manual = {}
    for k, v in decisions.user("clock").get("manual", {}).items():
        if k not in file_of:
            items.append(Item(f"manual entry {k}", "flag", message=f"decisions.json names {k}, which is not a part of this session",
                              fix="remove that entry or correct its name"))
            continue
        if isinstance(v, dict) and v.get("file") and v["file"] != file_of[k]:
            items.append(Item(f"manual entry {k}", "flag", message=f"decisions.json set {k} for {v['file']}, but {k} is now {file_of[k]} "
                              "(a part was added or taken away), so the entry was not used", where=dict(zip(("camera", "part"), k.split("/"))),
                              fix="slide that part again in Multi-view, or move the entry to the part that holds that file"))
            continue
        manual[k] = v
        auto = final.get(k, (None, None))[0]
        rate = final.get(k, (None, 1.0 / b_r))[1]
        final[k] = (manual_start(v), rate)
        items.append(Item(f"{k}: set by hand", "pass", value=round(manual_start(v), 3), unit="s", where=dict(zip(("camera", "part"), k.split("/"))),
                          message=f"start {manual_start(v):.3f} s from decisions.json" + (f"; the automatic start was {auto:.3f} s "
                          f"({(manual_start(v) - auto) * 1000:+.0f} ms)" if auto is not None else "; there was no automatic start")))
    if not any(k.split("/")[0] == ref for k in final):
        items.append(Item("reference camera on the clock", "fail", message=f"the reference camera {ref} could not be placed",
                          fix=f"slide {ref} into place by hand in Multi-view, mark another camera reference = true in project.toml, "
                              "or fix what the items above say", where={"camera": ref}))
        if not final:
            return Check("clock", items=items, numbers=numbers)

    # 5. per part checks, and the clock file
    flags = {}
    prev = {}
    guess = {pid[k] for k in range(len(parts)) if source[k] == "chained"} - set(manual)
    for k, p in enumerate(parts):
        name, where = pid[k], {"camera": p["cam"], "part": p["key"]}
        frame = 1.0 / p["fps"]
        fix = f"open Multi-view at {p['cam']} {p['key']} and check it; slide it by hand if it is off"
        reasons = []
        if name not in manual and k == anchor:
            items.append(Item(f"{name}: no sound", "not_tested", message="no part of any camera has sound, so this first part of the "
                              "reference camera stands at clock 0 and the other cameras are placed against it by hand",
                              fix="slide the other cameras' parts into place by hand in Multi-view", where=where))
        elif name not in manual and not p["sound"]:
            msg = (f"no sound: placed {usual:.2f} s from the part of {p['cam']} beside it, the usual split gap, as a first guess"
                   if name in final else f"no sound, and no part of {p['cam']} beside it is placed, so it is left out of clock.json")
            items.append(Item(f"{name}: no sound", "flag", message=msg, fix=f"slide {p['cam']} {p['key']} into place by hand in Multi-view",
                              where=where))
            reasons.append(msg)
        elif name not in manual:
            st = "pass" if count[k] >= 2 else "flag"
            msg = f"{count[k]} other parts heard the same sound" if count[k] else "no other camera's part shares sound with it"
            if source[k] == "chained":
                msg += f"; placed {usual:.2f} s after the part before it, the usual split gap"
            items.append(Item(f"{name}: two sound matches", st, value=int(count[k]), limit=">= 2", unit="matches", message=msg,
                              fix=fix if st == "flag" else "", where=where))
            if st == "flag":
                reasons.append(msg)
            if count[k] >= 2 and not looped[k]:
                items.append(Item(f"{name}: matches agree within one frame", "not_tested", message=f"each of its {count[k]} matches alone joins "
                                  "its parts, so none can disagree with another; a further match between these parts would check them",
                                  where=where))
            elif count[k] >= 2:
                st = "pass" if worst[k] <= frame else "flag"
                items.append(Item(f"{name}: matches agree within one frame", st, value=round(worst[k] * 1000, 1), limit=f"<= {frame * 1000:.1f}",
                                  unit="ms", message=f"its matches disagree by up to {worst[k] * 1000:.1f} ms", fix=fix if st == "flag" else "", where=where))
                if st == "flag":
                    reasons.append(f"its sound matches disagree by up to {worst[k] * 1000:.1f} ms")
        if p["cam"] in prev and prev[p["cam"]][0] in final and name in final:
            q_name, q = prev[p["cam"]]
            s0, r0 = final[q_name]
            gap = final[name][0] - (s0 + r0 * q["seconds"])
            st = "not_tested" if guess & {name, q_name} else "pass" if gap_lo <= gap <= gap_hi else "flag"
            items.append(Item(f"{name}: normal gap at its split", st, value=round(gap, 3), limit=f"{gap_lo:g} to {gap_hi:g}", unit="s",
                              message=f"starts {gap:.3f} s after {q['key']} ends" + ("; one of the two starts is a first guess, so the gap "
                              "says nothing" if st == "not_tested" else ""), fix=fix if st == "flag" else "", where=where))
            if st == "flag":
                reasons.append(f"starts {gap:.3f} s after {q['key']} ends")
        prev[p["cam"]] = (name, p)
        if reasons:
            flags[name] = "; ".join(reasons)
    kind = {c["name"]: c["kind"] for c in cams}
    silent = {pid[k] for k, p in enumerate(parts) if not p["sound"] and k != anchor}   # placed only by chaining, also on the reference camera
    src = {k: "manual" if k in manual else "chained" if k in silent else "reference" if k.split("/")[0] == ref
           else "motion" if kind[k.split("/")[0]] == "depth" else source[pid.index(k)] for k in final}
    clock = {"reference": ref, "zero": f"clock 0 is the first frame of {ref}; clock seconds run on {ref}'s own clock" if not stand_in else
             f"clock 0 is the first frame of {stand_in}, as {ref} was not placed automatically; clock seconds run on "
             f"{stand_in.split('/')[0]}'s own clock", "cameras": {}}
    for c in cams:
        ps = []
        for p in c["parts"]:
            k = f"{c['name']}/{p['key']}"
            if k not in final:
                continue
            ps.append(dict(key=p["key"], file=p["file"], frames=p["frames"], fps=p["fps"], **({"frames_csv": p["frames_csv"]} if "frames_csv" in p else {}),
                           start_s=round(final[k][0], 6), rate=final[k][1], source=src[k], flag=flags.get(k)))
        if ps:
            clock["cameras"][c["name"]] = {"kind": c["kind"], "parts": ps}
    for c in cams:
        if c["name"] in clock["cameras"]:
            if len(clock["cameras"][c["name"]]["parts"]) == len(c["parts"]):    # every part placed, by hand too: its flag is answered
                items = [i for i in items if not (i.name == f"{c['name']} on the clock" and i.status == "flag")]
            items.append(Item(f"{c['name']} on the clock", "pass", value=len(clock["cameras"][c["name"]]["parts"]), unit="parts"))
    write(out / "clock.json", clock)
    decisions.set_auto("clock", {"reference": ref, "depth_link": links[depth_cams[0]["name"]] if len(depth_cams) == 1 and depth_cams[0]["name"] in links
                                 else links, "rough_offset_s": rough_all, "line": {k: {"offset_s": a, "rate": b} for k, (a, b) in line_of.items()},
                                 "parts": {k: {"start_s": round(final[k][0], 6), "source": src[k]} for k in final}},
                       dict(evidence, sound=f"{len(eqs)} matches between {len(heard)} parts" + (
                           f", residual median {np.median(np.abs(used)) * 1000:.1f} ms, worst {np.abs(used).max() * 1000:.1f} ms" if len(used) else ""),
                            flagged=", ".join(sorted(flags)) or "none"))
    (out / "clock_log.txt").write_text("\n".join(lines) + "\n")
    numbers.update(parts=sum(len(c["parts"]) for c in clock["cameras"].values()), flagged=len(flags), waited_for_lock_s=round(waited, 1),
                   step_s=round(time.time() - t_start, 1))
    return Check("clock", items=items, outputs={"clock": str(out / "clock.json"), "log": str(out / "clock_log.txt")}, numbers=numbers)
