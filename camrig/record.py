"""What every step leaves behind: work/<step>/check.json. The runner, the report and the viewer read only this
and the output files it names.

    from camrig.record import Check, Item
    c = Check("clock", items=[Item("parts linked", "pass", value=30, limit="all", unit="parts")])
    c.status            # the worst item: fail > flag > not_tested > pass

Standard library only.
"""
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = "camrig.check/1"
STATUSES = ("pass", "not_tested", "flag", "fail")          # ordered from best to worst


@dataclass
class Item:
    """one check a person can read: what was checked, what came out, the limit, and what to do when it fails"""
    name: str
    status: str
    value: float | int | str | None = None
    limit: str | None = None
    unit: str | None = None
    message: str = ""
    fix: str = ""
    where: dict = field(default_factory=dict)       # e.g. {"camera": "cam1", "part": "part07"}

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"{self.name}: status {self.status} is not one of {STATUSES}")


@dataclass
class Check:
    step: str
    items: list[Item] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)     # role -> absolute path of a file the step wrote
    numbers: dict = field(default_factory=dict)               # small summary values for pages
    version: str = ""
    settings_hash: str = ""
    inputs: dict[str, str] = field(default_factory=dict)      # absolute path -> fingerprint
    started: str = ""
    seconds: float = 0.0

    @property
    def status(self) -> str:
        if not self.items:
            return "not_tested"
        return max((i.status for i in self.items), key=STATUSES.index)

    def to_json(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        d["status"] = self.status
        return d


def write(folder: Path, check: Check):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "check.json.part"
    tmp.write_text(json.dumps(check.to_json(), indent=1))
    os.replace(tmp, folder / "check.json")


def read(folder: Path) -> Check | None:
    p = Path(folder) / "check.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    if d.get("schema") != SCHEMA:
        raise ValueError(f"{p}: schema {d.get('schema')} is not {SCHEMA}")
    items = [Item(**i) for i in d.pop("items")]
    for k in ("schema", "status"):
        d.pop(k)
    return Check(items=items, **d)


def fingerprint(path: Path) -> str:
    """size and modification time for big files (videos), a content hash for small ones (results, lens files)"""
    p = Path(path)
    st = p.stat()
    if st.st_size > 20_000_000:
        return f"size {st.st_size} mtime {st.st_mtime_ns}"
    return "sha256 " + hashlib.sha256(p.read_bytes()).hexdigest()


def settings_hash(settings: dict) -> str:
    return hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()[:16]
