"""Run a session's steps in order, each in its own pixi environment, skipping steps whose inputs did not change.

    pixi run camrig PROJECT.toml                  every step
    pixi run camrig PROJECT.toml --only clock     one step (comma list for several)
    pixi run camrig PROJECT.toml --force          rerun even when nothing changed

A step is a module camrig/pipeline/<name>.py with VERSION, inputs(project, decisions), settings(project, decisions) and
run(project, decisions, out) -> Check. It writes into work/<name>/ and its check.json last; a step that stops halfway leaves
no check.json, so the next run starts it again (it may reuse its own part files). Standard library only.
"""
import argparse
import importlib
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camrig import record
from camrig.decisions import Decisions
from camrig.project import ProjectError, load

# name: (pixi environment, steps whose results it needs, code besides the step module whose change reruns it).
# Order is the run order. A step module may define needs(project) to replace the list, e.g. when it imports an old result.
STEPS = {
    "cameras": ("core", (), ["steps/orbbec.py"]),
    "scan": ("core", ("cameras",), ["camrig/board.py"]),
    "clock": ("core", ("cameras", "scan"), ["steps/sync.py", "steps/clock.py", "camrig/clock.py", "camrig/pipeline/cameras.py"]),
}


RUNNING: list[subprocess.Popen] = []


def stop(signum, frame):
    """Ctrl-C, a closed terminal or the viewer's Cancel: stop the running step and every process it started, then leave"""
    for proc in RUNNING:
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    sys.exit(128 + signum)


def env_python(env: str) -> list[str]:
    exe = ROOT / ".pixi" / "envs" / env / ("python.exe" if os.name == "nt" else "bin/python")
    return [str(exe)] if exe.exists() else ["pixi", "run", "--frozen", "--manifest-path", str(ROOT / "pixi.toml"), "-e", env, "python"]


def decisions_of(project) -> Decisions:
    return Decisions(project.root / "decisions.json")


def one(step: str, project_path: str):
    """inside the step's own environment: run it and leave work/<step>/check.json"""
    project = load(project_path)
    decisions = decisions_of(project)
    module = importlib.import_module(f"camrig.pipeline.{step}")
    out = project.work(step)
    out.mkdir(parents=True, exist_ok=True)
    check = module.run(project, decisions, out)
    decisions.save()
    record.write(out, check)


def needs_of(project, step) -> tuple[str, ...]:
    module = importlib.import_module(f"camrig.pipeline.{step}")
    return tuple(module.needs(project)) if hasattr(module, "needs") else STEPS[step][1]


def code_prints(step) -> dict[str, str]:
    """the fingerprint of the step module and of every file in its code list"""
    module = importlib.import_module(f"camrig.pipeline.{step}")
    code = [Path(module.__file__)] + [ROOT / f for f in STEPS[step][2]]
    return {str(c.relative_to(ROOT)): record.fingerprint(c) for c in code if c.exists()}


def code_hash(step) -> str:
    """one hash of the step's code, for the caches a step keeps between runs"""
    return record.settings_hash(code_prints(step))


def fingerprints(project, step) -> tuple[str, dict]:
    """the step's settings and code as one hash (so changed code reruns it), and the fingerprint of each input file"""
    module = importlib.import_module(f"camrig.pipeline.{step}")
    decisions = decisions_of(project)
    paths = [Path(p) for p in module.inputs(project, decisions)] + [project.work(n) / "check.json" for n in needs_of(project, step)]
    prints = {str(p): record.fingerprint(p) for p in paths if Path(p).exists()}
    return record.settings_hash({"version": module.VERSION, "settings": module.settings(project, decisions), "code": code_prints(step)}), prints


def run(project_path: str, only=None, force=False) -> int:
    project = load(project_path)
    worst = "pass"
    for step, (env, _, _) in STEPS.items():
        if only and step not in only:
            continue
        out = project.work(step)
        needs = needs_of(project, step)
        waiting = [n for n in needs if (c := record.read(project.work(n))) is None or c.status == "fail"
                   or any(i.name == "waits" for i in c.items)]                    # a step that waited made nothing to build on
        if waiting:
            record.write(out, record.Check(step, items=[record.Item(
                "waits", "not_tested", message=f"needs a finished {', '.join(waiting)} step first",
                fix=f"run {', '.join(waiting)} and fix what it reports")]))
            print(f"{step:11s} NOT TESTED: waits for {', '.join(waiting)}")
            continue
        version_hash, prints = fingerprints(project, step)
        old = record.read(out)
        if not force and old and old.status != "fail" and old.settings_hash == version_hash and old.inputs == prints:
            print(f"{step:11s} {old.status.upper().replace('_', ' '):10s} (unchanged, skipped)")
            worst = max(worst, old.status, key=record.STATUSES.index)
            continue
        out.mkdir(parents=True, exist_ok=True)
        (out / "check.json").unlink(missing_ok=True)
        started, t0 = datetime.now().isoformat(timespec="seconds"), time.time()
        with open(out / "log.txt", "w") as log:
            # its own process group, so stopping the run also stops the step's helper processes (see stop())
            proc = subprocess.Popen(env_python(env) + ["-m", "camrig.run", "--step", step, str(project.path)], cwd=ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            RUNNING.append(proc)
            code = proc.wait()
            RUNNING.remove(proc)
        check = record.read(out)
        if code != 0 or check is None:
            tail = (out / "log.txt").read_text(errors="replace").strip().splitlines()[-3:]
            check = record.Check(step, items=[record.Item("step ran", "fail", message=f"stopped with code {code}: " + " | ".join(tail),
                                                          fix=f"read {out / 'log.txt'}")])
        waited = float(check.numbers.get("waited_for_lock_s", 0) or 0) if isinstance(check.numbers, dict) else 0.0
        check.settings_hash, check.inputs, check.started = version_hash, prints, started
        check.seconds = round(time.time() - t0 - waited, 1)                          # working time, without waiting for another heavy job
        record.write(out, check)
        worst = max(worst, check.status, key=record.STATUSES.index)
        print(f"{step:11s} {check.status.upper().replace('_', ' '):10s} {check.seconds:7.1f} s")
        for i in check.items:
            if i.status in ("fail", "flag"):
                print(f"    {i.status.upper()}: {i.name}: {i.message}" + (f" -> {i.fix}" if i.fix else ""))
    return 1 if worst == "fail" else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project")
    p.add_argument("--only", help="comma list of steps")
    p.add_argument("--force", action="store_true")
    p.add_argument("--step", help=argparse.SUPPRESS)
    a = p.parse_args()
    if not a.step:
        for sig in (signal.SIGINT, signal.SIGTERM) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()):
            signal.signal(sig, stop)
    else:
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))   # a stopped step still releases its locks
    only = set(a.only.split(",")) if a.only else None
    if only and only - set(STEPS):
        sys.exit(f"--only: no step called {', '.join(sorted(only - set(STEPS)))}; the steps are {', '.join(STEPS)}")
    try:
        if a.step:
            return one(a.step, a.project)
        return run(a.project, only=only, force=a.force)
    except ProjectError as e:
        sys.exit(f"project file: {e}")


if __name__ == "__main__":
    sys.exit(main())
