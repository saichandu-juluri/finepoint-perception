"""The cameras step: what each camera recorded, in order, in work/cameras/cameras.json (camrig.cameras/1, docs/FORMATS.md).

Action cameras and phones: the parts of one camera are put in order by the camera's own creation time, and each part must
start about where the one before it ends (creation time plus duration; this put 30 of 30 parts right on 2 Oct). The order
by file name is checked against it. Size, frame rate, frame count and sound come from ffprobe. A camera whose parts change
video mode is refused; an unreadable file fails.
Depth camera: each .bag needs orbbec_colour.mp4, orbbec_depth.mp4 and orbbec_frames.csv. The exports named in
[camera.exports] are used; a bag with none is exported into work/cameras/<camera>/<bag>/ by steps/orbbec.py export.
"""
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from camrig.record import Check, Item

VERSION = "1"
ROOT = Path(__file__).resolve().parent.parent.parent
EXPORT = ("orbbec_colour.mp4", "orbbec_depth.mp4", "orbbec_frames.csv")
GAP_FLAG_S = (-2.0, 60.0)      # creation times are whole seconds; a longer gap means the camera was stopped


def tool(name: str) -> str:
    """ffmpeg / ffprobe of this environment, so a run without pixi activation does not pick up another build"""
    p = Path(sys.executable).parent / name
    return str(p) if p.exists() else name


def inputs(project, decisions):
    out = []
    for c in project.cameras:
        out += c.videos()
        if c.kind == "depth":
            out += [c.exports[b.stem] / "orbbec_frames.csv" for b in c.videos() if b.stem in c.exports]
    return out


def settings(project, decisions):
    return {c.name: [c.kind, c.files, str(c.folder), {k: str(v) for k, v in c.exports.items()}] for c in project.cameras}


def probe(path: Path) -> dict | None:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:format_tags=creation_time:stream=codec_type,width,"
                        "height,r_frame_rate,avg_frame_rate,nb_frames", "-of", "json", str(path)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        v = next(s for s in d["streams"] if s["codec_type"] == "video")
        num, den = v["r_frame_rate"].split("/")
        fps = float(num) / float(den)
        seconds = float(d["format"]["duration"])
        frames = int(v["nb_frames"]) if v.get("nb_frames", "N/A") not in ("N/A", "0") else round(seconds * fps)
        a, b = v.get("avg_frame_rate", "0/1").split("/")
        avg = float(a) / float(b) if float(b) else fps
    except (ValueError, KeyError, StopIteration, ZeroDivisionError):
        return None
    created = d["format"].get("tags", {}).get("creation_time")
    if created:
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    else:                                    # no tag: the file's own birth time, else its last change less its length
        st = path.stat()
        born = getattr(st, "st_birthtime", None)
        created = datetime.fromtimestamp(born if born else st.st_mtime - seconds, timezone.utc)
    return dict(file=str(path), frames=frames, fps=round(fps, 3), width=int(v["width"]), height=int(v["height"]), seconds=round(frames / fps, 3),
                created=created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), _created=created.timestamp(), _avg=avg,
                _sound=any(s["codec_type"] == "audio" for s in d["streams"]))


def chain(parts: list[dict]) -> tuple[list[dict], list[float]]:
    """parts in the camera's own time order, and the gap in seconds between each part's end and the next one's start"""
    parts = sorted(parts, key=lambda p: p["_created"])
    return parts, [b["_created"] - (a["_created"] + a["seconds"]) for a, b in zip(parts, parts[1:])]


def action_camera(c) -> tuple[dict | None, list[Item]]:
    items, parts = [], []
    for f in c.videos():
        p = probe(f)
        if p is None:
            items.append(Item(f"{c.name}: {f.name} readable", "fail", message=f"ffprobe cannot read {f}",
                              fix="copy the file again from the camera, or remove it from the folder", where={"camera": c.name, "file": str(f)}))
        else:
            parts.append(p)
    if not parts:
        return None, items
    by_name = [p["file"] for p in parts]
    parts, gaps = chain(parts)
    if len(parts) == 1:
        items.append(Item(f"{c.name}: file names agree with the camera's times", "not_tested", value=1, unit="parts",
                          message="one part, so there is no order to check", where={"camera": c.name}))
    elif [p["file"] for p in parts] != by_name:
        items.append(Item(f"{c.name}: file names agree with the camera's times", "flag", message="the camera's creation times give another "
                          "order than the file names: " + ", ".join(Path(p["file"]).name for p in parts), fix="the camera's times are used; "
                          "check the order in Multi-view", where={"camera": c.name}))
    else:
        items.append(Item(f"{c.name}: file names agree with the camera's times", "pass", value=len(parts), unit="parts",
                          message="gaps between parts by the camera's times: " + ", ".join(f"{g:.1f}" for g in gaps) + " s"))
    for a, b, g in zip(parts, parts[1:], gaps):
        if not GAP_FLAG_S[0] <= g <= GAP_FLAG_S[1]:
            what = "overlaps the one before it" if g < 0 else "starts long after the one before it ends (the camera was stopped)"
            items.append(Item(f"{c.name}: {Path(b['file']).name} follows {Path(a['file']).name}", "flag", value=round(g, 1),
                              limit=f"{GAP_FLAG_S[0]:g} to {GAP_FLAG_S[1]:g}", unit="s", message=f"{Path(b['file']).name} {what}",
                              fix="check that both files are from this camera and this session", where={"camera": c.name, "file": b["file"]}))
    modes = {(p["width"], p["height"], p["fps"]) for p in parts}
    if len(modes) > 1:
        items.append(Item(f"{c.name}: one video mode", "fail", value=len(modes), limit="1", unit="modes", message="the parts change video "
                          "mode: " + ", ".join(f"{w}x{h} at {f:g} fps" for w, h, f in sorted(modes)), fix="give each video mode its own "
                          "[[camera]] entry", where={"camera": c.name}))
        return None, items
    w, h, fps = modes.pop()
    items.append(Item(f"{c.name}: one video mode", "pass" if len(parts) > 1 else "not_tested", value=f"{w}x{h} at {fps:g} fps",
                      message="" if len(parts) > 1 else "one part, so the mode cannot change", where={"camera": c.name}))
    if any(abs(p["_avg"] - p["fps"]) > 0.01 * p["fps"] for p in parts):
        items.append(Item(f"{c.name}: even frame rate", "flag", message="the frames are not evenly spaced (variable frame rate, as phones "
                          "record)", fix="the clock takes frame i at i / fps; check the sync of this camera in Multi-view", where={"camera": c.name}))
    sound = [p["_sound"] for p in parts]
    if any(sound) and not all(sound):
        items.append(Item(f"{c.name}: sound in every part", "flag", message="some parts have no sound", fix="those parts cannot be placed "
                          "by sound; check them in Multi-view", where={"camera": c.name}))
    out = [dict({k: v for k, v in p.items() if not k.startswith("_")}, key=f"part{i + 1:02d}") for i, p in enumerate(parts)]
    for o, p in zip(out, parts):
        o["sound"] = p["_sound"]
    return dict(name=c.name, kind=c.kind, has_sound=all(sound), station=c.station, lens=str(c.lens) if c.lens else None, parts=out), items


def export(bag: Path, folder: Path) -> str | None:
    """steps/orbbec.py export into folder, run from there (the depth camera's library writes a Log/ folder where it runs);
    None when it worked, else the last lines it printed"""
    from camrig.heavy import one_at_a_time
    folder.mkdir(parents=True, exist_ok=True)
    with one_at_a_time(f"depth export {bag.name}"):
        r = subprocess.run([sys.executable, str(ROOT / "steps" / "orbbec.py"), "export", str(bag), str(folder)], cwd=folder,
                           capture_output=True, text=True)
    (folder / "export_log.txt").write_text(r.stdout + r.stderr)
    return None if r.returncode == 0 else " | ".join((r.stdout + r.stderr).strip().splitlines()[-3:])


def depth_camera(c, out: Path) -> tuple[dict | None, list[Item]]:
    items, parts = [], []
    for bag in c.videos():
        folder = c.exports.get(bag.stem) or out / c.name / bag.stem
        where = {"camera": c.name, "part": bag.stem}
        if not all((folder / n).exists() for n in EXPORT):
            if bag.stem in c.exports:
                items.append(Item(f"{c.name}: {bag.stem} export", "fail", message=f"{folder} lacks one of {', '.join(EXPORT)}",
                                  fix=f"point [camera.exports] {bag.stem} at the folder of its export, or remove the line to export again", where=where))
                continue
            err = export(bag, folder)
            if err:
                items.append(Item(f"{c.name}: {bag.stem} export", "fail", message=f"the export stopped: {err}",
                                  fix=f"read {folder / 'export_log.txt'}", where=where))
                continue
            items.append(Item(f"{c.name}: {bag.stem} export", "pass", message=f"exported into {folder}"))
            reused = False
        else:
            reused = bag.stem not in c.exports
        rows = [r for r in csv.DictReader(open(folder / "orbbec_frames.csv")) if r["stream"] == "colour"]
        video = probe(folder / "orbbec_colour.mp4")
        if not rows or video is None or video["frames"] != len(rows):
            items.append(Item(f"{c.name}: {bag.stem} readable", "fail", message=f"orbbec_colour.mp4 has {video and video['frames']} frames, "
                              f"orbbec_frames.csv lists {len(rows)}", fix="export the recording again", where=where))
            continue
        if reused:
            items.append(Item(f"{c.name}: {bag.stem} export", "pass", message=f"an earlier run's export in {folder}: its {len(rows)} colour "
                              "frames match orbbec_frames.csv; delete that folder to export again", where=where))
        dev = [int(r["device_us"]) for r in rows]
        parts.append(dict(key=bag.stem, bag=str(bag), file=str(folder / "orbbec_colour.mp4"), depth_video=str(folder / "orbbec_depth.mp4"),
                          frames_csv=str(folder / "orbbec_frames.csv"), frames=len(rows), fps=video["fps"],
                          width=video["width"], height=video["height"], seconds=round((dev[-1] - dev[0]) / 1e6, 3),
                          missing=sum(r["real"] != "1" for r in rows), _first=dev[0]))
    if not parts:
        return None, items
    parts.sort(key=lambda p: p.pop("_first"))
    items.append(Item(f"{c.name}: recordings readable", "pass", value=len(parts), unit="recordings",
                      message=", ".join(f"{p['key']}: {p['frames']} frames at {p['fps']:g} fps, {p['missing']} missing" for p in parts)))
    return dict(name=c.name, kind=c.kind, has_sound=False, station=c.station, lens=str(c.lens) if c.lens else None, parts=parts), items


def run(project, decisions, out):
    cams, items = [], []
    for c in project.cameras:
        cam, its = depth_camera(c, out) if c.kind == "depth" else action_camera(c)
        items += its
        if cam:
            cams.append(cam)
    (out / "cameras.json").write_text(json.dumps({"schema": "camrig.cameras/1", "cameras": cams}, indent=1))
    return Check("cameras", items=items, outputs={"cameras": str(out / "cameras.json")},
                 numbers={"cameras": len(cams), "parts": sum(len(c["parts"]) for c in cams)})
