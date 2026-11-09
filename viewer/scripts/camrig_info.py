"""What the app needs from its Python package, as one JSON line: the step order and each project file as the steps read it.

    python -I viewer/scripts/camrig_info.py [PROJECT.toml ...]

Run with the core pixi environment's Python. Uses camrig.project.load, so the app refuses a project file exactly when a
run would. Only the fields the app uses. Standard library only.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from camrig.project import ProjectError, load  # noqa: E402
from camrig.run import STEPS, needs_of  # noqa: E402


def info(path: str) -> dict:
    try:
        p = load(path)
    except (ProjectError, OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "name": p.name, "clock_existing": str(p.clock_existing) if p.clock_existing else None,
            "needs": {s: list(needs_of(p, s)) for s in STEPS},          # as the runner sees them for this project
            "cameras": [{"name": c.name, "kind": c.kind, "folder": str(c.folder), "exports": {k: str(v) for k, v in c.exports.items()},
                         "videos": len(c.videos())} for c in p.cameras]}


print(json.dumps({"steps": list(STEPS), "projects": {a: info(a) for a in sys.argv[1:]}}))
