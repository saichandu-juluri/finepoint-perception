"""Where each camera sees the board: every keyframe, about one a second, read for ChArUco corners.

Writes work/scan/board.csv: camera, part, file, t (seconds into that file), corners. The clock step lines up a depth
camera by the board seconds it shares with the cameras placed by sound. A depth camera is read from its colour export
(project [camera.exports]); one with no export yet is NOT TESTED until the cameras step exports it.
"""
import csv
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from camrig.record import Check, Item

VERSION = "2"
EVERY_S = 1.0
GOOD = 10          # corners a keyframe needs to count as "board seen"


def sources(project):
    """[(camera, part key, video file)] in camera and recording order: from work/cameras/cameras.json when the cameras step
    has run (its order and keys, the depth camera's colour export), else from the project file"""
    listed = project.work("cameras") / "cameras.json"
    if listed.exists():
        cams = {c["name"]: c for c in json.loads(listed.read_text())["cameras"]}
        return [(name, p["key"], Path(p["file"]) if p.get("file") and Path(p["file"]).exists() else None)
                for name in (c.name for c in project.cameras) if name in cams for p in cams[name]["parts"]]
    out = []
    for c in project.cameras:
        if c.kind == "depth":
            for bag in c.videos():
                folder = c.exports.get(bag.stem)
                video = folder / "orbbec_colour.mp4" if folder else None
                out.append((c.name, bag.stem, video if video and video.exists() else None))
        else:
            out += [(c.name, f"part{i + 1:02d}", v) for i, v in enumerate(c.videos())]
    return out


def inputs(project, decisions):
    listed = project.work("cameras") / "cameras.json"
    return [v for _, _, v in sources(project) if v] + ([listed] if listed.exists() else [])


def settings(project, decisions):
    b = project.board
    return {"board": [b.squares, b.square_mm, b.marker_mm, b.dictionary], "every_s": EVERY_S}


def keyframe_gap(video) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0",
                        "-read_intervals", "%+30", str(video)], capture_output=True, text=True).stdout
    t = sorted(float(l.split(",")[0]) for l in r.splitlines() if ",K" in l and l.split(",")[0] not in ("", "N/A"))
    gaps = [b - a for a, b in zip(t, t[1:]) if b > a]
    return sorted(gaps)[len(gaps) // 2] if gaps else 1.0


def scan_file(job):
    """(rows, keyframes looked at) for one video: every keyframe about EVERY_S apart, read for the board"""
    import numpy as np
    from camrig.board import detector
    from camrig.project import Board
    cam, key, video, board = job
    _, det = detector(Board(**board))
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                         "-of", "json", str(video)], capture_output=True, text=True).stdout or "{}").get("streams", [])
    if not streams:
        return [], -1                                       # not a readable video
    w, h = int(streams[0]["width"]), int(streams[0]["height"])
    gap = keyframe_gap(video)
    take = max(1, round(EVERY_S / gap))
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", str(video), "-an", "-fps_mode", "passthrough",
                           "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE, bufsize=w * h)
    rows, n, looked = [], 0, 0
    while True:
        buf = ff.stdout.read(w * h)
        if len(buf) < w * h:
            break
        if n % take == 0:
            looked += 1
            _, ids, _, _ = det.detectBoard(np.frombuffer(buf, np.uint8).reshape(h, w))
            if ids is not None and len(ids):
                rows.append((cam, key, str(video), round(n * gap, 3), len(ids)))
        n += 1
    ff.wait()
    return rows, looked


def run(project, decisions, out):
    from camrig.heavy import one_at_a_time
    src = sources(project)
    b = project.board
    board = dict(squares=b.squares, square_mm=b.square_mm, marker_mm=b.marker_mm, dictionary=b.dictionary, thickness_mm=b.thickness_mm)
    jobs = [(cam, key, video, board) for cam, key, video in src if video]
    import time
    t0 = time.time()
    with one_at_a_time("board scan"):
        waited = time.time() - t0
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(scan_file, jobs))
    rows = [r for rr, _ in results for r in rr]
    with open(out / "board.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["camera", "part", "file", "t", "corners"])
        w.writerows(rows)
    items, numbers = [], {}
    for c in project.cameras:
        missing = [key for cam, key, video in src if cam == c.name and video is None]
        if missing:
            items.append(Item(f"{c.name} scanned", "not_tested", message=f"no colour export yet for {', '.join(missing)}",
                              fix="run the cameras step, which exports the depth camera", where={"camera": c.name}))
            continue
        bad = [Path(v).name for (cam, _, v, _), (_, n) in zip(jobs, results) if cam == c.name and n < 0]
        if bad:
            items.append(Item(f"{c.name} videos readable", "fail", message=f"cannot read {', '.join(bad)}",
                              fix="check that these files are whole videos, not cut short", where={"camera": c.name}))
            continue
        seen = sum(1 for r in rows if r[0] == c.name and r[4] >= GOOD)
        looked = sum(n for (cam, *_), (_, n) in zip(jobs, results) if cam == c.name)
        numbers[c.name] = {"board_seconds": seen, "looked": looked}
        items.append(Item(f"{c.name} sees the board", "pass" if seen >= 5 else "flag", value=seen, limit=">= 5", unit="seconds",
                          message=f"board seen with {GOOD} or more corners in {seen} of {looked} seconds looked at",
                          fix="" if seen >= 5 else "show the board to this camera, so the clock can line up a depth camera by it",
                          where={"camera": c.name}))
    numbers["waited_for_lock_s"] = round(waited, 1)
    return Check("scan", items=items, outputs={"board": str(out / "board.csv")}, numbers=numbers)
