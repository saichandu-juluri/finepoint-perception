"""Checks of the cameras step, the automatic clock and the project file New project writes. Prints PASS or FAIL per line.

    .pixi/envs/core/bin/python camrig/test_clock.py                 made-up data, then the 2 Oct targets if FPP_2OCT_SESSION is set
    .pixi/envs/core/bin/python camrig/test_clock.py --rerun         also: a manual entry survives a rerun and moves its part

The 2 Oct targets need FPP_2OCT_SESSION, the folder of a 2oct_auto session (run cameras, scan and clock there first). They
compare it with the saved sync.json of 2 Oct, beside cam1's videos, read only, through camrig.clock.from_sync_json, part by
part by file name. Without FPP_2OCT_SESSION they print one SKIP line. --rerun writes into that session's decisions.json and
takes its entry out again.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camrig.clock import from_sync_json
from camrig.pipeline import clock as C
from camrig.pipeline.cameras import chain
from camrig.project import load

SESSION = Path(os.environ["FPP_2OCT_SESSION"]).expanduser() if os.environ.get("FPP_2OCT_SESSION") else None
FAILED = []


def say(ok, what):
    print(("PASS: " if ok else "FAIL: ") + what, flush=True)
    if not ok:
        FAILED.append(what)


def made_up():
    # chaining: the camera's times win over the names, gaps come out right
    ps = [dict(file="b", _created=1000.0 + 0, seconds=600.0), dict(file="a", _created=1602.0, seconds=500.0), dict(file="c", _created=2104.5, seconds=10.0)]
    order, gaps = chain(ps)
    say([p["file"] for p in order] == ["b", "a", "c"] and np.allclose(gaps, [2.0, 2.5]), f"parts chained by creation time, gaps {gaps}")

    # least squares: three cameras, one part with no match stays out
    rng = np.random.default_rng(1)
    true = np.array([0.0, 1200.0, -30.0, 1170.5, 5.0, 999.0])
    eqs = [(i, j, true[i] - true[j] + rng.normal(0, 0.0005), 3) for i, j in [(0, 2), (0, 3), (1, 3), (1, 2), (2, 4), (0, 4)]]
    starts, resid = C.solve(6, eqs)
    say(np.nanmax(np.abs(starts[:5] - true[:5])) < 0.002 and np.isnan(starts[5]), f"least squares puts 5 parts within 2 ms, the unmatched one stays out")
    eqs.append((1, 4, true[1] - true[4] + 0.5, 3))                   # one wrong match shows in its residual
    _, resid = C.solve(6, eqs)
    say(np.abs(resid[-1]) > 0.1, f"a wrong match shows in its residual ({resid[-1] * 1000:.0f} ms)")

    # sound: a piece of x found in y
    x = rng.normal(size=int(300 * C.RATE)).astype(np.float32)
    y = np.concatenate([rng.normal(size=int(37.25 * C.RATE)), x[: int(250 * C.RATE)]]).astype(np.float32)
    y = y + 0.5 * rng.normal(size=len(y)).astype(np.float32)
    r = C.match(x, y)
    say(r is not None and abs(r[0] - 37.25) < 1 / C.RATE and r[1] >= 2, f"sound match finds x at 37.25 s into y: {r}")
    say(C.match(x, rng.normal(size=len(y)).astype(np.float32)) is None, "two unrelated sounds do not match")

    # rough offset from board seconds
    depth = np.array([100, 101, 102, 103, 104, 2000, 2001, 2002, 4000, 4001], float)
    action = np.concatenate([depth + 1090.0, [50.0, 7000.0]])
    off, n, other = C.rough_offset(depth, action)
    say(off == 1090.0 and n == 10 and other < n, f"rough offset {off} s from board seconds ({n} line up, best other {other})")

    # motion windows: two cameras agree, one has an impossible drift
    got = []
    for cam, ppm, noise in (("a", -8.0, 0.010), ("b", -8.0, 0.012), ("c", -350.0, 0.010)):
        for d in np.linspace(100, 6000, 10):
            got.append(dict(cam=cam, depth=d, sound=1090.5 + d * (1 + ppm * 1e-6) + rng.normal(0, noise), peak=0.6, edge=False))
    got.append(dict(cam="a", depth=3000.0, sound=1090.5 + 3000 + 0.4, peak=0.6, edge=False))     # one far-off window
    fit, fits = C.pool_windows(got, ["a", "b", "c"])
    say(fit is not None and fit["cams"] == ["a", "b"] and abs(fit["drift_ppm"] + 8) < 3 and not fits["c"]["used"],
        f"pooled line from the agreeing cameras: drift {fit and round(fit['drift_ppm'], 1)} ppm, cameras {fit and fit['cams']}, c left out")
    say(fits["a"]["kept"] == 10 and fits["a"]["good"] == 11, f"the far-off window is dropped (camera a: {fits['a']['kept']} of {fits['a']['good']} kept)")
    # a short recording: 190 s of windows with 40 ms scatter cannot tell a drift, so the rate is held at 1
    short = [dict(cam="a", depth=d, sound=4.46 + d + e, peak=0.6, edge=False) for d, e in zip(np.linspace(14, 204, 5), (0, 0.011, 0.48, -0.075, 0.084))]
    fit, fits = C.pool_windows(short, ["a"])
    say(fit is not None and fit["held"] and fit["b"] == 1.0 and abs(fit["a"] - 4.46) < 0.05 and fit["kept"] == 4,
        f"short recording: rate held at 1, offset {fit and round(fit['a'], 4)} s from {fit and fit['kept']} of 5 windows ({fits['a']})")
    long = [dict(r, depth=r["depth"] * 30, sound=4.46 + r["depth"] * 30 * (1 - 8e-6) + (r["sound"] - 4.46 - r["depth"])) for r in short]
    fit, _ = C.pool_windows(long, ["a"])
    say(fit is not None and not fit["held"] and abs(fit["drift_ppm"] + 8) < 30, f"the same windows over 5700 s are not held at rate 1: "
        f"{fit and round(fit['drift_ppm'], 1)} ppm, held {fit and fit['held']}")
    # matches that alone join their parts cannot disagree; one more closes a loop
    say(C.in_loop(3, [(0, 1, 0, 1), (0, 2, 0, 1)]) == [False, False] and C.in_loop(3, [(0, 1, 0, 1), (0, 2, 0, 1), (1, 2, 0, 1)]) == [True] * 3,
        "a match that alone joins its parts is told apart from one in a loop")
    say(C.manual_start(12.5) == 12.5 and C.manual_start({"start_s": 3.0, "note": "x"}) == 3.0, "manual entries as a number or {start_s}")


def new_project():
    """the project file the app's New project writes (viewer/src/server/newproject.ts): the session folder holds only
    project.toml, each camera's folder is named by its absolute path, a depth camera's files are its .bag recordings"""
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for d, f in (("cam1", "a.MOV"), ("cam 2", "b.mp4"), ("depth", "x.bag"), ("notes", "x.txt")):
            (t / "rec" / d).mkdir(parents=True)
            (t / "rec" / d / f).write_bytes(b"")
        text = 'name = "New one"\n\n[board]\nsquares = [5, 7]\nsquare_mm = 20\nmarker_mm = 14\n'
        for name, kind, folder in (("cam_2", "action", "cam 2"), ("cam1", "action", "cam1"), ("depth", "depth", "depth")):
            text += f'\n[[camera]]\nname = "{name}"\nkind = "{kind}"\nfolder = {json.dumps(str(t / "rec" / folder))}\n'
            text += 'files = "*.bag"\n' if kind == "depth" else ""
        (t / "sessions" / "new_one").mkdir(parents=True)
        (t / "sessions" / "new_one" / "project.toml").write_text(text)
        p = load(t / "sessions" / "new_one" / "project.toml")
        kinds = [(c.name, c.kind, len(c.videos())) for c in p.cameras]
        say(kinds == [("cam_2", "action", 1), ("cam1", "action", 1), ("depth", "depth", 1)] and p.reference().name == "depth",
            f"the project file New project writes loads: {kinds}, reference {p.reference().name}")


def cameras_refused():
    """a camera whose parts change mode is refused; an unreadable file fails"""
    from camrig.pipeline.cameras import action_camera, tool
    from camrig.project import Camera
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for name, size in (("p1.MOV", "320x240"), ("p2.MOV", "640x480")):
            subprocess.run([tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=1", str(t / name)], check=True)
        (t / "p3.MOV").write_bytes(b"not a video")
        cam, items = action_camera(Camera("x", "action", t, "*.MOV", None, None, False))
        fails = sorted(i.name for i in items if i.status == "fail")
        say(cam is None and fails == ["x: one video mode", "x: p3.MOV readable"], f"mode change refused and junk file failed: {fails}")


def against_saved():
    path = SESSION / "work" / "clock" / "clock.json"
    if not path.exists():
        print(f"NOT TESTED: the 2 Oct targets, no {path} yet")
        return
    saved_path = load(SESSION / "project.toml").camera("cam1").folder / "sync.json"
    mine = json.loads(path.read_text())
    saved = from_sync_json(saved_path)
    sw = json.loads(saved_path.read_text())
    how = {p["file"]: p.get("source") for c in sw["cameras"].values() for p in c["parts"]}
    old = {Path(p["file"]).name: p for c in saved["cameras"].values() for p in c["parts"]}
    # saved clock 0 is the stopwatch's "start" event, 73.8 s into the depth camera's first recording, which no automatic clock can
    # know. Both clocks hold the depth camera's first frame, the same instant, so it is laid on top: a known frame, not a fitted offset.
    ref = mine["cameras"][mine["reference"]]["parts"][0]
    first_saved = [p for c in saved["cameras"].values() for p in c["parts"] if p.get("frames_csv") == ref.get("frames_csv")]
    shift = ref["start_s"] - first_saved[0]["start_s"]
    print(f"the depth camera's first frame is at {ref['start_s']:.3f} s on mine and {first_saved[0]['start_s']:.3f} s on saved: saved is read {shift:+.3f} s later")
    for p in old.values():
        p["start_s"] += shift
    print("part            mine (s)     saved (s)    difference   how sync.json got it")
    diffs, stop = [], []
    for cam in ("cam1", "cam3"):
        for p in mine["cameras"].get(cam, {}).get("parts", []):
            q = old[Path(p["file"]).name]
            d = (p["start_s"] - q["start_s"]) * 1000
            diffs.append(d)
            src = how[Path(p["file"]).name]
            if src == "stopwatch":
                stop.append((f"{cam} {p['key']}", d))
            print(f"{cam} {p['key']}   {p['start_s']:11.3f}  {q['start_s']:11.3f}   {d:8.1f} ms   {src}")
    say(len(stop) == 6 and all(abs(d) <= 30 for _, d in stop),
        f"the 6 stopwatch parts within 30 ms of saved, nothing subtracted (worst {max(abs(d) for _, d in stop):.1f} ms)")
    say(len(diffs) == 15 and max(abs(d) for d in diffs) <= 50, f"every cam1 and cam3 part within 50 ms of saved "
        f"(median {np.median(np.abs(diffs)):.1f} ms, worst {max(abs(d) for d in diffs):.1f} ms, mean {np.mean(diffs):+.1f} ms, over {len(diffs)} parts)")
    check = json.loads((SESSION / "work" / "clock" / "check.json").read_text())
    few = {i["where"]["camera"] + "/" + i["where"]["part"] for i in check["items"] if i["name"].endswith("two sound matches") and i["value"] < 2}
    flagged = {i["where"]["camera"] + "/" + i["where"]["part"] for i in check["items"] if i["status"] == "flag" and "part" in i["where"]}
    say(flagged == few, f"the flags name exactly the parts with fewer than 2 matches: flagged {sorted(flagged) or 'none'}, "
        f"fewer than 2 {sorted(few) or 'none'}")
    for cam in ("cam2", "cam4"):
        n = len(json.loads((SESSION / "work" / "cameras" / "cameras.json").read_text())["cameras"][[c["name"] for c in json.loads(
            (SESSION / "work" / "cameras" / "cameras.json").read_text())["cameras"]].index(cam)]["parts"])
        got = len(mine["cameras"].get(cam, {}).get("parts", []))
        say(got == n, f"{cam} on the clock: {got} of {n} parts, starts " + ", ".join(f"{p['start_s']:.2f}" for p in mine["cameras"].get(cam, {}).get("parts", [])))
    print(f"clock step on 2 Oct: {check['seconds']} s as the runner timed it; numbers {check['numbers']}")


def rerun(part="cam1/part07", shift=0.5):
    """a manual entry moves its part and survives a rerun; removed again afterwards"""
    dec_path = SESSION / "decisions.json"
    clock = lambda: {f"{c}/{p['key']}": p for c, v in json.loads((SESSION / "work/clock/clock.json").read_text())["cameras"].items() for p in v["parts"]}
    run = lambda: subprocess.run([sys.executable, "-m", "camrig.run", str(SESSION / "project.toml"), "--only", "clock"], cwd=ROOT,
                                 capture_output=True, text=True).stdout
    auto = clock()[part]["start_s"]
    d = json.loads(dec_path.read_text())
    d.setdefault("clock", {}).setdefault("user", {}).setdefault("manual", {})[part] = {"start_s": auto + shift, "note": "test_clock.py"}
    dec_path.write_text(json.dumps(d, indent=1, sort_keys=True))
    t = time.time()
    print(run().strip().splitlines()[-1])
    took = time.time() - t
    p = clock()[part]
    kept = json.loads(dec_path.read_text())["clock"]["user"]["manual"].get(part)
    say(abs(p["start_s"] - (auto + shift)) < 1e-6 and p["source"] == "manual" and kept is not None,
        f"a manual entry survives a rerun and moves {part} by {(p['start_s'] - auto) * 1000:.0f} ms (rerun {took:.0f} s)")
    d = json.loads(dec_path.read_text())
    d["clock"]["user"]["manual"].pop(part)
    dec_path.write_text(json.dumps(d, indent=1, sort_keys=True))
    print(run().strip().splitlines()[-1])
    say(abs(clock()[part]["start_s"] - auto) < 1e-6, f"removing it puts {part} back at its automatic start")


if __name__ == "__main__":
    made_up()
    new_project()
    cameras_refused()
    if SESSION is None:
        print("SKIP: the 2 Oct targets: set FPP_2OCT_SESSION to the folder of a 2oct_auto session to run them")
    else:
        against_saved()
        if "--rerun" in sys.argv:
            rerun()
    print(f"{len(FAILED)} failed" if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
