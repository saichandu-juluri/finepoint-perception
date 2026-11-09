"""The project file: what the person says about a session. Read once, checked once, then trusted.

    from camrig.project import load
    p = load("my_session/project.toml")
    p.cameras, p.reference().name, p.work("clock")

Standard library only, so every pixi environment can import it.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("action", "depth", "phone")
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".avi", ".mkv")


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Board:
    squares: tuple[int, int]
    square_mm: float
    marker_mm: float
    dictionary: str = "DICT_4X4_50"
    thickness_mm: float = 0.0


@dataclass(frozen=True)
class Camera:
    name: str
    kind: str
    folder: Path
    files: str
    lens: Path | None
    station: str | None
    reference: bool
    exports: dict[str, Path] = field(default_factory=dict)

    def videos(self) -> list[Path]:
        """the camera's files as found on disk, by name; for a depth camera its .bag recordings"""
        if self.kind == "depth":
            return sorted(p for p in self.folder.glob(self.files if self.files != "*" else "*.bag") if p.is_file())
        return sorted(p for p in self.folder.glob(self.files) if p.is_file() and p.suffix.lower() in VIDEO_EXT)


@dataclass(frozen=True)
class Project:
    path: Path
    name: str
    board: Board
    cameras: tuple[Camera, ...]
    lens_folder: Path
    clock_existing: Path | None
    positions_existing: Path | None
    words: Path | None
    models: dict
    limits: dict

    @property
    def root(self) -> Path:
        return self.path.parent

    def work(self, step: str) -> Path:
        return self.root / "work" / step

    def camera(self, name: str) -> Camera:
        for c in self.cameras:
            if c.name == name:
                return c
        raise KeyError(name)

    def reference(self) -> Camera:
        """the camera whose clock and position the others are given in: the one marked reference, else the first
        depth camera, else the first camera"""
        marked = [c for c in self.cameras if c.reference]
        if marked:
            return marked[0]
        depth = [c for c in self.cameras if c.kind == "depth"]
        return (depth or list(self.cameras))[0]

    def limit(self, key: str, default: float) -> float:
        return float(self.limits.get(key, default))


def _path(root: Path, value, what: str, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value:
        raise ProjectError(f"{what} must be a path")
    p = Path(value).expanduser()
    p = p if p.is_absolute() else (root / p)
    if must_exist and not p.exists():
        raise ProjectError(f"{what}: {p} does not exist")
    return p.resolve() if p.exists() else p


MODELS = {"range_s", "hands", "tools", "tools_every_s"}
# every name a step reads with project.limit (docs/FORMATS.md); test_core checks this list against the steps
LIMITS = {"split gap min s", "split gap max s", "depth drift max ppm", "lens_reprojection_px", "lens_wrong_share", "lens_wrong_gain",
          "match_px", "reprojection_px", "modes_mm", "tips_modes_mm", "speaker agreement"}


def _only(table: dict, allowed: set, where: str):
    extra = set(table) - allowed
    if extra:
        raise ProjectError(f"{where}: unknown {', '.join(sorted(extra))} (allowed: {', '.join(sorted(allowed))})")


def load(path) -> Project:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ProjectError(f"{path} does not exist")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path}: {e}") from e
    root = path.parent
    _only(raw, {"name", "lens_folder", "board", "camera", "clock", "positions", "transcript", "models", "limits"}, "project")
    b = raw.get("board")
    if not isinstance(b, dict):
        raise ProjectError("project: [board] is missing")
    _only(b, {"squares", "square_mm", "marker_mm", "dictionary", "thickness_mm"}, "[board]")
    try:
        board = Board(squares=(int(b["squares"][0]), int(b["squares"][1])), square_mm=float(b["square_mm"]),
                      marker_mm=float(b["marker_mm"]), dictionary=str(b.get("dictionary", "DICT_4X4_50")),
                      thickness_mm=float(b.get("thickness_mm", 0.0)))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ProjectError(f"[board]: needs squares = [columns, rows], square_mm and marker_mm ({e})") from e
    cams = []
    for i, c in enumerate(raw.get("camera", [])):
        where = f"[[camera]] number {i + 1}"
        _only(c, {"name", "kind", "folder", "files", "lens", "station", "reference", "exports"}, where)
        if c.get("kind") not in KINDS:
            raise ProjectError(f"{where}: kind must be one of {', '.join(KINDS)}")
        name = str(c.get("name", "")).strip()
        if not name or "/" in name or "\\" in name:
            raise ProjectError(f"{where}: name must be a short label such as cam1")
        exports = {k: _path(root, v, f"{where} exports.{k}") for k, v in c.get("exports", {}).items()}
        cams.append(Camera(name=name, kind=c["kind"], folder=_path(root, c.get("folder"), f"{where} folder"),
                           files=str(c.get("files", "*")), lens=_path(root, c["lens"], f"{where} lens") if "lens" in c else None,
                           station=c.get("station"), reference=bool(c.get("reference", False)), exports=exports))
    if len(cams) < 2:
        raise ProjectError("project: at least two [[camera]] entries are needed")
    if len({c.name for c in cams}) != len(cams):
        raise ProjectError("project: camera names must differ")
    if sum(c.reference for c in cams) > 1:
        raise ProjectError("project: only one camera can be the reference")
    for c in cams:
        if not c.videos():
            raise ProjectError(f"camera {c.name}: no files match {c.files} in {c.folder}")
    clock = raw.get("clock", {})
    _only(clock, {"existing"}, "[clock]")
    positions = raw.get("positions", {})
    _only(positions, {"existing"}, "[positions]")
    transcript = raw.get("transcript", {})
    _only(transcript, {"words"}, "[transcript]")
    _only(raw.get("models", {}), MODELS, "[models]")
    _only(raw.get("limits", {}), LIMITS, "[limits]")
    return Project(path=path, name=str(raw.get("name", root.name)), board=board, cameras=tuple(cams),
                   lens_folder=_path(root, raw.get("lens_folder", "lens"), "lens_folder", must_exist=False),
                   clock_existing=_path(root, clock["existing"], "[clock] existing") if "existing" in clock else None,
                   positions_existing=_path(root, positions["existing"], "[positions] existing") if "existing" in positions else None,
                   words=_path(root, transcript["words"], "[transcript] words") if "words" in transcript else None,
                   models=dict(raw.get("models", {})), limits=dict(raw.get("limits", {})))
