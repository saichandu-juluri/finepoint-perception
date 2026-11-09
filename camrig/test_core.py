"""Checks for the shared core: project file, clock, decisions, results files and the runner.

    pixi run test-core                      the parts that need no recordings
    pixi run test-core SAVED_PROJECT.toml   also: the clock made from the 2 Oct sync.json picks the same frames as the old code
"""
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camrig import record
from camrig.clock import Clock, from_sync_json
from camrig.decisions import Decisions
from camrig.project import ProjectError, load


def fake_project(folder: Path) -> Path:
    for cam in ("a", "b"):
        (folder / cam).mkdir()
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10", "-c:v", "libx264",
                        "-g", "10", str(folder / cam / "part01.MOV")], check=True)
    p = folder / "project.toml"
    p.write_text('name = "test"\n[board]\nsquares = [5, 7]\nsquare_mm = 20.0\nmarker_mm = 14.0\n'
                 '[[camera]]\nname = "a"\nkind = "action"\nfolder = "a"\n[[camera]]\nname = "b"\nkind = "action"\nfolder = "b"\n')
    return p


def test_project_and_runner():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        p = fake_project(d)
        prj = load(p)
        assert prj.reference().name == "a", "with no depth camera and no mark the first camera is the reference"
        assert prj.camera("b").videos() == [d.resolve() / "b" / "part01.MOV"]
        bad = d / "bad.toml"
        bad.write_text(p.read_text() + "colour = 1\n")
        try:
            load(bad)
            raise AssertionError("an unknown key must be refused")
        except ProjectError as e:
            assert "colour" in str(e)
        run = lambda: subprocess.run([sys.executable, "-m", "camrig.run", str(p)], cwd=ROOT, capture_output=True, text=True).stdout
        skipped = lambda out: [l.split()[0] for l in out.splitlines() if "skipped" in l]
        out = run()
        from camrig.run import STEPS
        for step in STEPS:
            c = record.read(prj.work(step))
            assert c is not None, f"{step} left no check.json:\n{out}"
            crashed = [i.message for i in c.items if i.name == "step ran"]
            assert not crashed, f"{step} crashed on two silent test clips: {crashed}"
        assert record.read(prj.work("clock")).status == "flag", "two cameras with no sound: the reference's first part is clock 0, the rest wait for sync by hand: FLAG"
        out = run()
        assert skipped(out) == list(STEPS), "an unchanged step that did not fail is skipped:\n" + out
        (d / "b" / "part01.MOV").write_bytes(b"not a video")
        out = run()
        assert record.read(prj.work("cameras")).status == "fail", "an unreadable video fails the cameras step:\n" + out
        assert all(record.read(prj.work(s)).items[0].name == "waits" for s in ("scan", "clock")), "steps after a failed one wait instead of running:\n" + out
        out = run()
        assert "cameras" not in skipped(out), "a failed step runs again:\n" + out
    print("PASS  project file and runner")


def test_decisions():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "decisions.json"
        dec = Decisions(path)
        dec.set_auto("clock", {"link": ["cam1", "cam3"]}, {"link": "18 of 19 windows"})
        dec.section("clock")["user"]["link"] = ["cam1"]
        dec.save()
        dec2 = Decisions(path)
        assert dec2.get("clock", "link") == ["cam1"], "the person's choice wins"
        dec2.set_auto("clock", {"link": ["cam3"]})
        assert dec2.get("clock", "link") == ["cam1"] and dec2.user("clock") == {"link": ["cam1"]}, "a new run keeps the person's choice"
        running = Decisions(path)
        viewer = json.loads(path.read_text())
        viewer["clock"]["user"]["manual"] = {"cam3/part01": {"start_s": 1.5}}
        path.write_text(json.dumps(viewer))
        running.set_auto("clock", {"link": ["cam4"]})
        running.set_auto("lens", {"cam1": {"lens": None}})
        running.save()
        saved = json.loads(path.read_text())
        assert saved["clock"]["user"]["manual"] == {"cam3/part01": {"start_s": 1.5}}, "a save made while a step ran stays"
        assert saved["clock"]["auto"] == {"link": ["cam4"]} and saved["lens"]["auto"] == {"cam1": {"lens": None}}, "the step's own choices land"
        assert not (Path(d) / ".decisions.json.lock").exists(), "the lock is gone after the save"
    print("PASS  decisions")


def test_steps_import_in_core():
    import importlib
    from camrig.run import STEPS
    for step in STEPS:
        m = importlib.import_module(f"camrig.pipeline.{step}")
        assert all(hasattr(m, k) for k in ("VERSION", "inputs", "settings", "run")), step
    print(f"PASS  all {len(STEPS)} step modules import in this environment and have VERSION, inputs, settings, run")


def test_check_status():
    c = record.Check("x", items=[record.Item("a", "pass"), record.Item("b", "flag"), record.Item("c", "not_tested")])
    assert c.status == "flag"
    c.items.append(record.Item("d", "fail"))
    assert c.status == "fail"
    assert record.Check("y").status == "not_tested"
    print("PASS  check status is the worst item")


def test_clock_against_old(saved_project: str):
    prj = load(saved_project)
    clock = Clock(from_sync_json(prj.clock_existing))
    sys.path.insert(0, str(ROOT / "steps"))
    from clock import Session
    old = Session(str(prj.clock_existing.parent))
    rng = random.Random(1)
    lo, hi = -1200.0, 7100.0
    differ = 0
    for cam in ("cam1", "cam3", "orbbec"):
        for _ in range(3000):
            t = rng.uniform(lo, hi)
            a, b = clock.frame_at(cam, t), old.frame_at(cam, t)
            if a is None or b is None:
                differ += (a is None) != (b is None)
                continue
            if Path(a[0]).resolve() != Path(b[0]).resolve() or a[1] != min(b[1], a[1] if b[1] >= _frames(clock, cam, a[0]) else b[1]):
                differ += 1
    assert differ == 0, f"{differ} of 9000 random moments pick another frame than the old code"
    new_i = clock.instants("orbbec", 3600, 3660)
    old_i = old.orbbec_instants(3600, 3660)
    assert len(new_i) == len(old_i) and all(abs(x[0] - y[0]) < 1e-6 and x[2] == y[1] for x, y in zip(new_i, old_i)), "reference frame instants"
    for cam in ("cam1", "cam3"):
        for f, frames, fps, c0 in old.parts(cam):
            assert abs(clock.clock_of(cam, f, 1000) - (c0 + 1000 / fps)) < 1e-9
    print(f"PASS  clock from the 2 Oct sync.json: 9000 random moments, same frames as the old code; {len(new_i)} reference instants identical")


def _frames(clock, cam, file):
    return next(p.frames for p in clock.parts(cam) if p.file == file)


def test_limit_names():
    import re
    from camrig import project
    from camrig.pipeline import clock
    text = "".join(p.read_text() for p in (ROOT / "camrig" / "pipeline").glob("*.py"))
    used = set(re.findall(r'project\.limit\("([^"]+)"', text)) | set(clock.limits())
    assert used <= project.LIMITS, f"[limits] names the steps read but the project file refuses: {sorted(used - project.LIMITS)}"
    print(f"PASS  the project file accepts all {len(used)} [limits] names the steps read")


def test_code_lists():
    """every local file a step imports is in its code list in run.py, so a change to it reruns the step"""
    import ast
    from camrig.run import STEPS
    dirs = [ROOT, ROOT / "steps"]

    def local(module):
        for d in dirs:
            p = d.joinpath(*module.split(".")).with_suffix(".py")
            if p.exists() and not p.name.startswith("test_"):
                return p

    missing = {}
    for step, (_, _, code) in STEPS.items():
        start = ROOT / "camrig" / "pipeline" / f"{step}.py"
        seen, todo = set(), [start]
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            for node in ast.walk(ast.parse(p.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] + [f"{node.module}.{a.name}" for a in node.names] if isinstance(node, ast.ImportFrom) and node.module and not node.level else []
                todo += [q for q in map(local, names) if q and q not in seen]
        own = {p.relative_to(ROOT).as_posix() for p in seen} - {start.relative_to(ROOT).as_posix()}   # run.py's / also on Windows
        own = {f for f in own if not f.startswith("camrig/") or f.startswith("camrig/pipeline/") or f in ("camrig/clock.py", "camrig/board.py")}
        if own - set(code):
            missing[step] = sorted(own - set(code))
    assert not missing, f"files a step imports but whose change would not rerun it (add them to STEPS in run.py): {missing}"
    print(f"PASS  every step's code list holds each local file it imports ({len(STEPS)} steps)")


if __name__ == "__main__":
    test_steps_import_in_core()
    test_check_status()
    test_decisions()
    test_code_lists()
    test_limit_names()
    test_project_and_runner()
    if len(sys.argv) > 1:
        test_clock_against_old(sys.argv[1])
    else:
        print("NOT TESTED  clock against the old code (give the saved 2 Oct project file)")
