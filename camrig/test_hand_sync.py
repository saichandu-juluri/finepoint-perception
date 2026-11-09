"""Checks of sound judged per part and sync by hand (docs/UPLOAD.md "Sync by hand"). Prints PASS or FAIL per line.

    pixi run --frozen -e core python camrig/test_hand_sync.py

Made-up placements first, then the clock step on made-up sessions of 2 s clips in a temporary folder: with fewer than
two sound cameras it decodes no sound and takes no lock, so it runs in seconds and never touches a real session.
"""
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camrig.decisions import Decisions
from camrig.pipeline import cameras as K
from camrig.pipeline import clock as C
from camrig.project import load

FAILED = []


def say(ok, what):
    print(("PASS: " if ok else "FAIL: ") + what, flush=True)
    if not ok:
        FAILED.append(what)


def part(cam, sound, seconds=100.0):
    return dict(cam=cam, sound=sound, seconds=seconds)


def made_up():
    # a: sound, silent, sound; b: sound; c: silent only. a1 and a3 matched to b1, 2 s split gaps.
    parts = [part("a", True), part("a", False), part("a", True), part("b", True, 400.0), part("c", False)]
    true = {0: 0.0, 2: 204.0, 3: -50.0}
    eqs = [(i, 3, true[i] - true[3], 3) for i in (0, 2)]
    starts, _, source, usual = C.place(parts, eqs)
    say(abs(starts[2] - 204.0) < 1e-6 and source[2] == "sound", f"the sound parts are solved as before: a3 at {starts[2]:.3f} s")
    say(source[1] == "chained" and abs(starts[1] - (starts[0] + 100.0 + usual)) < 1e-9, f"a silent middle part is chained after its "
        f"neighbour with the usual gap ({usual:.2f} s): {starts[1]:.3f} s")
    say(np.isnan(starts[4]) and source[4] is None, "a camera whose only part is silent stays unplaced")
    parts = [part("c", False), part("a", True), part("a", True)]
    starts, _, source, _ = C.place(parts, [])
    say(np.isnan(starts[0]) and starts[1] == 0.0 and source[1:] == ["sound", "chained"], f"with no match a silent part never anchors "
        f"the sound clock: starts {starts.tolist()}, sources {source}")
    starts, _, source, _ = C.place([part("c", False), part("c", False)], [])
    say(np.isnan(starts).all() and source == [None, None], "with no sound at all nothing is placed automatically")
    starts, _, source, _ = C.place([part("a", False), part("a", False), part("b", False)], [], anchor=0)
    say(starts[0] == 0.0 and source[1] == "chained" and np.isnan(starts[2]), f"with no sound at all the anchor is clock 0 and only "
        f"its camera is chained: starts {starts.tolist()}, sources {source}")
    rng = np.random.default_rng(1)
    parts = [part(c, True, 300.0) for c in "aabbc"]
    eqs = [(i, j, rng.normal(0, 100), 3) for i, j in [(0, 2), (1, 3), (0, 4), (2, 4)]]
    starts, resid, _, _ = C.place(parts, eqs)
    s0, r0 = C.solve(len(parts), eqs)
    both = np.isfinite(s0)
    say(np.array_equal(starts[both], s0[both]) and np.array_equal(resid, r0, equal_nan=True),
        "with every part heard, the solved starts and residuals are exactly solve()'s")


def clip(path, sound, seconds=2, fps=10):
    args = [K.tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate={fps}"]
    if sound:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args + ["-c:v", "libx264", "-g", str(fps), str(path)], check=True)


def depth_recording(folder, bag, first_us):
    """a .bag stand-in and its export: 20 colour frames at 10 fps from first_us on the device clock"""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{bag}.bag").write_bytes(b"")
    exp = folder / "exports" / bag
    clip(exp / "orbbec_colour.mp4", False)
    clip(exp / "orbbec_depth.mp4", False)
    with open(exp / "orbbec_frames.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stream", "device_us", "real"])
        w.writerows(["colour", first_us + i * 100_000, 1] for i in range(20))
    return exp


def session(t, cams, depth=False, manual=None, board=False):
    """cams: {name: [has sound per part]}; writes project.toml, runs the cameras step, a board.csv (header only, or with one
    showing in the first action part when board), then the clock"""
    toml = 'name = "hand"\n[board]\nsquares = [5, 7]\nsquare_mm = 20.0\nmarker_mm = 14.0\n'
    if depth:
        exps = {b: depth_recording(t / "d", b, us) for b, us in (("rec1", 5_000_000), ("rec2", 15_000_000))}
        # a JSON string is a TOML basic string, so a Windows path's \ is escaped
        toml += '[[camera]]\nname = "d"\nkind = "depth"\nfolder = "d"\n[camera.exports]\n' + "".join(f'{b} = {json.dumps(str(e))}\n' for b, e in exps.items())
    for name, sounds in cams.items():
        for i, s in enumerate(sounds):
            clip(t / name / f"part{i + 1:02d}.MOV", s)
        toml += f'[[camera]]\nname = "{name}"\nkind = "action"\nfolder = "{name}"\n'
    (t / "project.toml").write_text(toml)
    if manual:
        (t / "decisions.json").write_text(json.dumps({"schema": "camrig.decisions/1", "clock": {"auto": {}, "evidence": {}, "user": {"manual": manual}}}))
    project = load(t / "project.toml")
    dec = Decisions(t / "decisions.json")
    for step in ("cameras", "scan", "clock"):
        project.work(step).mkdir(parents=True, exist_ok=True)
    K.run(project, dec, project.work("cameras"))
    first = next(c for c in json.loads((project.work("cameras") / "cameras.json").read_text())["cameras"] if c["kind"] != "depth")
    row = f'{first["name"]},part01,{first["parts"][0]["file"]},0.5,12\n' if board else ""
    (project.work("scan") / "board.csv").write_text("camera,part,file,t,corners\n" + row)
    check = C.run(project, dec, project.work("clock"))
    path = project.work("clock") / "clock.json"
    clock = json.loads(path.read_text()) if path.exists() else {"cameras": {}}
    return check, {f"{c}/{p['key']}": p for c, v in clock["cameras"].items() for p in v["parts"]}


def silent_parts():
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [True, False, True], "b": [False]})
        a2 = parts.get("a/part02", {})
        say(a2.get("source") == "chained" and (a2.get("flag") or "").startswith("no sound"),
            f"a silent middle part is placed chained with a 'no sound' flag: {a2.get('source')}, {a2.get('flag')!r}")
        say(parts["a/part01"]["start_s"] == 0.0 and "b/part01" not in parts, f"the reference's sound part is clock 0; the silent "
            f"camera is left out: {sorted(parts)}")
        left = [i for i in check.items if i.where == {"camera": "b", "part": "part01"}]
        say(len(left) == 1 and left[0].status == "flag" and "left out" in left[0].message, f"the left-out part is listed with where "
            f"{{camera, part}}: {[(i.name, i.status) for i in left]}")
        say(check.status == "flag", f"one sound camera is not a fail: step status {check.status}")
        gaps = {i.name: i.status for i in check.items if i.name.endswith("normal gap at its split")}
        say(gaps == {"a/part02: normal gap at its split": "not_tested", "a/part03: normal gap at its split": "not_tested"},
            f"a gap beside a guessed start is not tested: {gaps}")


def action_reference_silent():
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [False], "b": [True, True]})
        ref = [i for i in check.items if i.name == "reference camera on the clock"]
        say(check.status == "fail" and sorted(parts) == ["b/part01", "b/part02"] and ref and ref[0].where == {"camera": "a"},
            f"a silent action reference fails but clock.json keeps the parts sound placed: {check.status} {sorted(parts)}")
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [False], "b": [True, True]}, manual={"a/part01": {"start_s": 0.0}})
        got = {k: p["source"] for k, p in parts.items()}
        say(check.status != "fail" and got == {"a/part01": "manual", "b/part01": "sound", "b/part02": "chained"},
            f"a hand entry for the silent reference keeps the other parts: {check.status} {got}")


def no_sound_anywhere():
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [False, False], "b": [False]})
        a1, a2 = parts.get("a/part01", {}), parts.get("a/part02", {})
        say(a1.get("start_s") == 0.0 and a1.get("source") == "reference" and a1.get("flag") is None, f"no part of any camera has "
            f"sound: the reference's first part stands at clock 0: {a1.get('start_s')}, {a1.get('source')}, {a1.get('flag')!r}")
        say(a2.get("source") == "chained" and (a2.get("flag") or "").startswith("no sound"), f"the reference's other parts are "
            f"chained first guesses: {a2.get('source')}, {a2.get('flag')!r}")
        left = [i for i in check.items if i.where == {"camera": "b", "part": "part01"}]
        say("b/part01" not in parts and len(left) == 1 and left[0].status == "flag" and "left out" in left[0].message,
            f"the other cameras are left out and listed: {sorted(parts)} {[(i.name, i.status) for i in left]}")
        say(check.status == "flag", f"such a session does not pass: step status {check.status}")
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [False, False], "b": [False]}, manual={"b/part01": 2.0})
        got = {k: (p["start_s"], p["source"]) for k, p in parts.items()}
        say(got.get("b/part01") == (2.0, "manual") and got.get("a/part01") == (0.0, "reference") and check.status != "fail",
            f"a manual entry places the rest against it: {got}, step {check.status}")


def left_out_camera():
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [True, True], "b": [False]}, depth=True, board=True)
        a = [(i.name, i.status) for i in check.items if i.where == {"camera": "a"}]
        say(("a on the clock", "flag") in a, f"a camera dropped because the depth reference stands alone is flagged: {a}")


def by_hand():
    manual = {"a/part01": 1.0, "a/part02": {"start_s": 4.0, "note": "test"}, "b/part01": {"start_s": 2.5}}
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [True, True], "b": [False]}, depth=True)
        say(sorted(parts) == ["d/rec1", "d/rec2"] and parts["d/rec2"]["start_s"] == 10.0 and parts["d/rec1"]["source"] == "reference",
            f"nothing lines the depth reference up: it alone is placed, on its own device clock ({ {k: p['start_s'] for k, p in parts.items()} })")
        say(check.status != "fail", f"not a fail before the parts are set by hand: step status {check.status}")
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [True, True], "b": [False]}, depth=True, board=True)
        st = {i.name: i.status for i in check.items}
        say(st.get("d rough offset") == "flag" and st.get("d linked to the other cameras") == "not_tested" and check.status != "fail"
            and sorted(parts) == ["d/rec1", "d/rec2"], f"a depth reference the board lines up with nothing is flagged, placed alone, "
            f"not a fail: rough offset {st.get('d rough offset')}, step {check.status}")
    with tempfile.TemporaryDirectory() as t:
        check, parts = session(Path(t), {"a": [True, True], "b": [False]}, depth=True, manual=manual)
        got = {k: (p["start_s"], p["source"]) for k, p in parts.items() if not k.startswith("d/")}
        say(got == {k: (C.manual_start(v), "manual") for k, v in manual.items()}, f"manual entries place every part with fewer than two "
            f"sound cameras: {got}")
        bad = [(i.name, i.status) for i in check.items if i.status in ("flag", "fail")]
        say(check.status == "not_tested" and not bad, f"a session placed fully by hand is not_tested, nothing flagged: {check.status} {bad}")


if __name__ == "__main__":
    made_up()
    silent_parts()
    action_reference_silent()
    no_sound_anywhere()
    left_out_camera()
    by_hand()
    print(f"{len(FAILED)} failed" if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
