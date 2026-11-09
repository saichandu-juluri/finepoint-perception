"""Orbbec recordings (.bag from OrbbecViewer / OrbbecSDK v2): read back, look at, export, check against the board.

Run with the core environment's Python:  python steps/orbbec.py COMMAND ...
  check    RECORDING.bag [playback_rate]          what is in the file; exit 0 only if complete
  preview  RECORDING.bag OUT_DIR [LAST_SECONDS]   colour | depth | overlay clip
  export   RECORDING.bag OUT_DIR [RATE]           two videos and every frame's time (the cameras step runs this)
  lens     TAKE.bag [STILL_SPEED]                 stored colour lens numbers against the board
  depth    TAKE.bag [STILL_SPEED]                 depth against the board plane
  face     OUT.png TAKE.bag x0,x1,y0,y1 [TAKE2.bag x0,x1,y0,y1 ...]   the simulator's face
  selftest                                        the self-tests of preview, lens, depth and face
preview, lens, depth and face also take `selftest` alone; COMMAND --help prints that command's notes.
Run it from a folder where the SDK may create its "Log/" directory (it writes one in the cwd).
read_all, calibration, depth_on_colour, colour_image, paint and read_index are the bag-reading library the commands share.
"""
import csv
import datetime
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import av
import cv2
import numpy as np

import pyorbbecsdk as ob

import calib

S, F = ob.OBSensorType, ob.OBFrameType

NEAR_MM, FAR_MM = 150.0, 700.0   # the camera's stated range; used for the colour scale only
FINE_MM = 0.1                    # the depth unit every reader gets, whatever the Viewer's DepthUnit was at Record

RATE = 4.0   # playback speed while reading; every frame is counted against the file's index

EVERY = 3           # colour frames looked at: every third, 10 a second at 30 fps
PAIR_US = 20_000    # a depth frame counts for a colour frame when their device times are this close
MARGIN_MM = 4.0     # rim of the board's square area that is left out
MIN_COVER = 0.3     # share of the board's area that must have depth for a frame to be measured

MM_PER_PX = 0.1        # scale of the drawn rows
TOP_MM = 40.0          # the colours span this far down from the simulator's highest point, the same for every row
NO_DEPTH = (60, 0, 60)
MIN_HEIGHT_MM = 6.0    # a point this far above the support is first taken as simulator, to find its highest point
# ponytail: the face is taken as everything within 33 mm below its highest point (this simulator stands 39 mm above
# the table on a 6 mm base). That keeps the count the same whether it lies on the table or is raised on something
# narrower than itself. Measure another simulator's height before using this on it.
FACE_MM = 33.0
PAD = 60               # width in pixels of the ring of support around the box used for the plane


def _record(f):
    hlen = struct.unpack("<I", f.read(4))[0]
    h, fields, p = f.read(hlen), {}, 0
    while p < hlen:
        n = struct.unpack_from("<I", h, p)[0]
        k, v = h[p + 4 : p + 4 + n].split(b"=", 1)
        fields[k.decode()] = v
        p += 4 + n
    return fields, struct.unpack("<I", f.read(4))[0]


def read_index(path):
    """Message times (microseconds) per topic, from the ROS bag v2.0 index. No SDK, no decompression."""
    u32, u64 = (lambda b: struct.unpack("<I", b)[0]), (lambda b: struct.unpack("<Q", b)[0])
    with open(path, "rb") as f:
        if not f.readline().startswith(b"#ROSBAG V2.0"):
            raise ValueError("not a ROS bag v2.0 file")
        head, dlen = _record(f)
        if u64(head["index_pos"]) == 0:
            raise ValueError("bag has no index: the recording was not stopped cleanly")
        f.seek(u64(head["index_pos"]))
        conns, chunks = {}, []
        for _ in range(u32(head["conn_count"])):
            c, dlen = _record(f)
            f.seek(dlen, 1)
            conns[u32(c["conn"])] = c["topic"].decode()
        for _ in range(u32(head["chunk_count"])):
            c, dlen = _record(f)
            f.seek(dlen, 1)
            chunks.append((u64(c["chunk_pos"]), u32(c["count"])))
        times, comp, stored, raw = {t: [] for t in conns.values()}, set(), 0, 0
        for pos, n_idx in chunks:
            f.seek(pos)
            c, dlen = _record(f)
            comp.add(c["compression"].decode())
            stored, raw = stored + dlen, raw + u32(c["size"])
            f.seek(dlen, 1)
            for _ in range(n_idx):
                c, dlen = _record(f)
                data = f.read(dlen)
                for i in range(u32(c["count"])):
                    sec, nsec = struct.unpack_from("<II", data, i * 12)
                    times[conns[u32(c["conn"])]].append(sec * 1_000_000 + (nsec + 500) // 1000)
    return {t: sorted(v) for t, v in times.items()}, comp, stored, raw


def read_all(path, near=None, colour=True, rate=1.0):
    """Every colour and depth frame of a recording: stream profiles, depth as (device time us, mm per unit,
    uint16 picture), colour as (device time us, format, bytes). Depth always comes in FINE_MM steps.
    A take of minutes does not fit in memory (about 2 MB per depth frame): near=(device times us, tolerance us)
    keeps only the depth frames that close to one of those times, colour=False leaves the colour frames out, and
    rate is the playback speed."""
    prof, depth, color = _play(path, near=near, colour=colour, rate=rate)
    if depth and depth[0][1] > 1.5 * FINE_MM:
        depth = _fine_depth(path, depth, near, rate)
    return prof, depth, color


def fit_rule(sdk_mm, raw, unit):
    """The take's own rule depth_mm = 1 / (a * raw + b), from frames read both ways: sdk_mm as the SDK converts them
    with the recorded unit, raw with the conversion switched off. The SDK rounds to 0.1 mm and then drops the digits
    below the unit, so its values sit unit/2 - 0.05 mm low on average. Returns a, b and the share of pixels where
    the rule gives back the SDK's own value."""
    m = (sdk_mm > 0) & (raw > 0)
    a, b = np.polyfit(raw[m], 1.0 / (sdk_mm[m] + unit / 2 - 0.05), 1)
    back = np.floor(np.round(1.0 / (a * raw[m] + b), 1) / unit + 1e-6) * unit
    return a, b, float((np.abs(back - sdk_mm[m]) < 1e-4).mean())


def _fine_depth(path, coarse, near=None, rate=1.0):
    """Depth in FINE_MM steps for a take recorded with a coarser depth unit. The file holds the camera's raw values,
    which are finer than any unit; the unit is only applied when the SDK converts them on reading. The rule is
    fitted on this take's first frames and has to reproduce the SDK's values before it is used."""
    unit = float(coarse[0][1])
    raw = _play(path, raw=True, near=near, rate=rate)[1]
    by_time = {ts: z for ts, _, z in raw}
    pairs = [(z, by_time[ts]) for ts, _, z in coarse[:8] if ts in by_time]
    if len(raw) == len(coarse) and pairs:
        sdk_mm = np.concatenate([p[0].ravel() for p in pairs]).astype(np.float64) * unit
        raws = np.concatenate([p[1].ravel() for p in pairs]).astype(np.float64)
        a, b, agree = fit_rule(sdk_mm, raws, unit)
        if agree >= 0.99 and ((sdk_mm > 0) == (raws > 0)).mean() >= 0.999:
            print(f"depth was recorded with a {unit:g} mm unit; rebuilt in {FINE_MM} mm steps from the raw values "
                  f"(the rule gives back the SDK's own value in {100 * agree:.2f}% of pixels)")
            fine = []
            for ts, _, r in raw:
                z = np.zeros(r.shape, np.uint16)
                z[r > 0] = np.clip(np.rint(1.0 / (a * r[r > 0] + b) / FINE_MM), 0, 65535)
                fine.append((ts, FINE_MM, z))
            return fine
    print(f"WARNING: fine depth could not be rebuilt from the raw values; the depth stays in {unit:g} mm steps")
    return coarse


def _play(path, raw=False, near=None, colour=True, rate=1.0):
    """One pass through the file. raw=True reads depth only, with the SDK's conversion switched off, so the depth
    frames hold the camera's raw values. near, colour and rate as in read_all."""
    pb = ob.PlaybackDevice(path)
    if raw:
        pb.set_bool_property(ob.OBPropertyID.OB_PROP_SDK_DISPARITY_TO_DEPTH_BOOL, False)   # for this pass only, the file is not changed
    pipe, cfg, prof = ob.Pipeline(pb), ob.Config(), {}
    sensors = pb.get_sensor_list()
    for i in range(len(sensors)):
        stype, sp = sensors[i].get_type(), sensors[i].get_stream_profile_list()[0]
        if sp.is_video_stream_profile():
            prof[stype] = sp.as_video_stream_profile()
            if stype == ob.OBSensorType.DEPTH_SENSOR or (colour and not raw):
                cfg.enable_stream(stype)
    cfg.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.DISABLE)
    depth, color, errors, done, last = [], [], [], threading.Event(), [time.monotonic()]
    times, tol = (np.sort(np.asarray(near[0], np.int64)), near[1]) if near else (None, 0)

    def wanted(ts):
        i = int(np.searchsorted(times, ts))
        return min(abs(int(t) - ts) for t in times[max(i - 1, 0):i + 1]) <= tol

    def on_frames(frameset):   # copies only: the SDK drops frames silently when this is slow
        try:
            for i in range(frameset.get_count()):
                f = frameset.get_frame_by_index(i)
                if f.get_type() == ob.OBFrameType.DEPTH_FRAME:
                    if times is None or wanted(f.get_timestamp_us()):
                        d = f.as_depth_frame()
                        z = np.array(f.get_data()).view(np.uint16).reshape(d.get_height(), d.get_width())
                        depth.append((f.get_timestamp_us(), d.get_depth_scale(), z))
                elif f.get_type() == ob.OBFrameType.COLOR_FRAME:
                    color.append((f.get_timestamp_us(), f.get_format(), np.array(f.get_data())))
            last[0] = time.monotonic()
        except Exception as e:
            errors.append(repr(e))

    pb.set_playback_status_change_callback(lambda s: done.set() if s == ob.OBPlaybackStatus.STOPPED else None)
    pb.set_playback_rate(rate)
    pb.pause()
    pipe.start(cfg, on_frames)
    pb.resume()
    done.wait(pb.get_duration() / 1000 / rate + 30)
    while time.monotonic() - last[0] < 0.5:
        time.sleep(0.05)
    pipe.stop()
    if errors:
        raise RuntimeError(errors[0])
    return prof, sorted(depth, key=lambda r: r[0]), sorted(color, key=lambda r: r[0])


def calibration(prof):
    S = ob.OBSensorType
    d, c = prof[S.DEPTH_SENSOR], prof[S.COLOR_SENSOR]
    di, ci, k, e = d.get_intrinsic(), c.get_intrinsic(), c.get_distortion(), d.get_extrinsic_to(c)
    Kc = np.array([[ci.fx, 0, ci.cx], [0, ci.fy, ci.cy], [0, 0, 1]], np.float64)
    dist = np.array([k.k1, k.k2, k.p1, k.p2, k.k3, k.k4, k.k5, k.k6], np.float64)
    R = np.array(e.rot, np.float64).reshape(3, 3)
    t = np.array(e.transform, np.float64)
    return (di.fx, di.fy, di.cx, di.cy), Kc, dist, R, t, (ci.width, ci.height)


def depth_on_colour(mm, cal):
    """Depth image (mm, 0 = no depth) re-drawn in the colour camera's pixel grid."""
    (fx, fy, cx, cy), Kc, dist, R, t, (w, h) = cal
    out = np.zeros((h, w), np.float32)
    v, u = np.nonzero(mm)
    if not len(u):
        return out
    z = mm[v, u].astype(np.float64)
    pts = np.stack([(u - cx) / fx * z, (v - cy) / fy * z, z], 1)
    px, _ = cv2.projectPoints(pts, cv2.Rodrigues(R)[0], t, Kc, dist)
    px = np.rint(px.reshape(-1, 2)).astype(np.int64)
    zc = (pts @ R.T + t)[:, 2]
    ok = (px[:, 0] >= 0) & (px[:, 0] < w) & (px[:, 1] >= 0) & (px[:, 1] < h) & (zc > 0)
    order = np.argsort(-zc[ok])                       # far first, so the nearest point wins a pixel
    out[px[ok, 1][order], px[ok, 0][order]] = zc[ok][order]
    # ponytail: colour has more pixels than depth, so single points leave a grid of holes; a max fill
    # as wide as one depth pixel (3x3 for 1280x800 depth, 5x5 for 640x400) is enough for looking at it.
    # Use a proper mesh warp if these values are ever measured.
    k = int(np.ceil(Kc[0, 0] / fx)) + 1
    filled = cv2.dilate(out, np.ones((k + 1 - k % 2,) * 2, np.uint8))
    return np.where(out > 0, out, filled)


def paint(mm):
    scaled = np.clip((mm - NEAR_MM) / (FAR_MM - NEAR_MM), 0, 1)
    img = cv2.applyColorMap((255 - scaled * 255).astype(np.uint8), cv2.COLORMAP_TURBO)   # near = red, far = blue
    img[mm <= 0] = 0
    return img


def fit(img, w, h):
    s = min(w / img.shape[1], h / img.shape[0])
    small = cv2.resize(img, (int(img.shape[1] * s) // 2 * 2, int(img.shape[0] * s) // 2 * 2), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((h, w, 3), np.uint8)
    y, x = (h - small.shape[0]) // 2, (w - small.shape[1]) // 2
    canvas[y : y + small.shape[0], x : x + small.shape[1]] = small
    return canvas


def colour_image(fmt, data, size):
    """One recorded colour frame as a BGR picture. size is (width, height)."""
    if fmt == ob.OBFormat.MJPG:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    if fmt == ob.OBFormat.RGB and data.size == size[0] * size[1] * 3:
        return np.ascontiguousarray(data.reshape(size[1], size[0], 3)[..., ::-1])
    return None


def run_check(path, rate=1.0):
    """Read back an OrbbecViewer / OrbbecSDK v2 recording (.bag) and print what is really in it.

    Run:  python steps/orbbec.py check RECORDING.bag [playback_rate]
    Exit code 0 only if colour and depth are both present and every frame in the file was read back.
    """
    # ---- 1. what the file itself says (ground truth for the frame counts)
    index, comp, stored, raw = read_index(path)
    print(f"file: {path}\nchunk compression: {sorted(comp)}  stored {stored / 1e6:.1f} MB, uncompressed {raw / 1e6:.1f} MB")
    for topic, t in sorted(index.items()):
        print(f"  {topic:30s} {len(t):6d} messages")
    if index.get("/cam/property"):
        start = datetime.datetime.fromtimestamp(index["/cam/property"][0] / 1e6, datetime.timezone.utc)
        print(f"recording started (host clock): {start.isoformat()}  = {start.astimezone().isoformat()} local")

    # ---- 2. open with the SDK
    pb = ob.PlaybackDevice(path)
    info = pb.get_device_info()
    print(f"\ndevice: {info.get_name()}  pid=0x{info.get_pid():04x}  sn={info.get_serial_number()}  fw={info.get_firmware_version()}"
          f"  connection={info.get_connection_type()}  min_sdk={info.get_supported_min_sdk_version()}")
    print(f"SDK {ob.get_version()}  duration {pb.get_duration()} ms")

    pipe, cfg, profiles = ob.Pipeline(pb), ob.Config(), {}
    sensors = pb.get_sensor_list()
    for i in range(len(sensors)):
        stype, sp = sensors[i].get_type(), sensors[i].get_stream_profile_list()[0]
        if not sp.is_video_stream_profile():     # IMU streams are not checked here
            print(f"stream {stype.name}: not a video stream, skipped")
            continue
        vp = profiles[stype] = sp.as_video_stream_profile()
        cfg.enable_stream(stype)
        print(f"stream {stype.name}: {vp.get_format().name} {vp.get_width()}x{vp.get_height()} @ {vp.get_fps()} fps")
    # DISABLE = every frame is delivered on its own; FULL_FRAME_REQUIRE would silently drop unpaired frames
    cfg.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.DISABLE)

    # ---- 3. play the whole file once. Keep the callback light: the SDK drops frames silently when it is slow
    S, F = ob.OBSensorType, ob.OBFrameType
    kinds = {int(F.DEPTH_FRAME): ("depth", f"/cam/sensor_{int(S.DEPTH_SENSOR)}/frameType_{int(F.DEPTH_FRAME)}"),
             int(F.COLOR_FRAME): ("color", f"/cam/sensor_{int(S.COLOR_SENSOR)}/frameType_{int(F.COLOR_FRAME)}")}
    rows = {k: [] for k in kinds}       # (index, device_ts_us, system_ts_us, global_ts_us)
    middle, errors, last = {}, [], [time.monotonic()]
    done = threading.Event()

    def on_frames(frameset):
        try:
            for i in range(frameset.get_count()):
                f = frameset.get_frame_by_index(i)
                k = int(f.get_type())
                if k not in rows:
                    continue
                rows[k].append((f.get_index(), f.get_timestamp_us(), f.get_system_timestamp_us(), f.get_global_timestamp_us()))
                if len(rows[k]) == len(index.get(kinds[k][1], [])) // 2 + 1:      # the middle frame of this stream
                    v = f.as_video_frame()
                    middle[k] = dict(data=np.array(f.get_data()), w=v.get_width(), h=v.get_height(), fmt=f.get_format(), pix=v.get_pixel_type(),
                                     scale=f.as_depth_frame().get_depth_scale() if k == int(F.DEPTH_FRAME) else None)
            last[0] = time.monotonic()
        except Exception as e:  # the SDK swallows callback exceptions, so keep them
            errors.append(repr(e))

    pb.set_playback_status_change_callback(lambda s: done.set() if s == ob.OBPlaybackStatus.STOPPED else None)
    pb.set_playback_rate(rate)
    pb.pause()                           # so that no frame is read before every sensor is started
    pipe.start(cfg, on_frames)
    pb.resume()
    if not done.wait(pb.get_duration() / 1000 / rate + 30):
        print("WARNING: playback did not reach STOPPED")
    while time.monotonic() - last[0] < 0.5:   # let the queues drain
        time.sleep(0.05)
    props = {}
    P = ob.OBPropertyID
    for name, pid, get in [("hardware disparity-to-depth", P.OB_PROP_DISPARITY_TO_DEPTH_BOOL, pb.get_bool_property),
                           ("software disparity-to-depth", P.OB_PROP_SDK_DISPARITY_TO_DEPTH_BOOL, pb.get_bool_property),
                           ("depth precision level", P.OB_PROP_DEPTH_PRECISION_LEVEL_INT, pb.get_int_property),
                           ("hardware depth-to-colour align", P.OB_PROP_DEPTH_ALIGN_HARDWARE_BOOL, pb.get_bool_property),
                           ("depth auto exposure", P.OB_PROP_DEPTH_AUTO_EXPOSURE_BOOL, pb.get_bool_property),
                           ("colour auto exposure", P.OB_PROP_COLOR_AUTO_EXPOSURE_BOOL, pb.get_bool_property)]:
        try:
            props[name] = get(pid) if pb.is_property_supported(pid, ob.OBPermissionType.PERMISSION_READ) else "not in file"
        except Exception as e:
            props[name] = f"unreadable ({e})"
    if isinstance(props["depth precision level"], int):
        props["depth precision level"] = ob.OBDepthPrecisionLevel(props["depth precision level"]).name
    try:
        props["depth work mode"] = pb.get_depth_work_mode().name
    except Exception as e:
        props["depth work mode"] = f"unreadable ({e})"
    pipe.stop()

    # ---- 4. report
    ok, gaps = not errors, {}
    for e in errors[:3]:
        print("callback error:", e)
    for k, (name, topic) in kinds.items():
        r, want = sorted(rows[k]), index.get(topic, [])
        print(f"\n[{name}] frames read {len(r)}, frames in file {len(want)}")
        if not r:
            ok = False
            print(f"  NO {name.upper()} FRAMES")
            continue
        idx, dev, sys_ts, glob = (np.array(c, dtype=np.int64) for c in zip(*r))
        same = len(r) == len(want) and np.abs(np.sort(dev) - np.array(want)).max() <= 1
        ok &= bool(same)
        print(f"  every recorded frame read back with its device timestamp: {'yes' if same else 'NO'}")
        print(f"  device ts first {dev.min()} us, last {dev.max()} us, span {(dev.max() - dev.min()) / 1e6:.3f} s")
        if len(r) > 1:
            gap = np.diff(np.sort(dev))
            med = float(np.median(gap))
            gaps[name] = int((gap > 1.5 * med).sum())
            print(f"  median frame interval {med / 1000:.3f} ms ({1e6 / med:.2f} fps), max {gap.max() / 1000:.1f} ms,"
                  f" intervals > 1.5 x median: {gaps[name]}")
        print(f"  frame index {idx.min()}..{idx.max()}, numbers missing in between: {int(idx.max() - idx.min() + 1 - len(set(idx.tolist())))}")
        print(f"  system (host) ts first {sys_ts.min()} us, last {sys_ts.max()} us; median(system - device) {np.median(sys_ts - dev) / 1000:.1f} ms;"
              f" global ts {'all zero' if not glob.any() else 'present'}")

    d = middle.get(int(F.DEPTH_FRAME))
    if d is not None:
        print(f"\n[depth, middle frame] format {d['fmt'].name}, pixel type {d['pix'].name}, depth scale {d['scale']:.4f} mm per unit")
        if d["pix"] == ob.OBPixelType.OB_PIXEL_DISPARITY:   # the SDK did not convert the stored disparity to depth
            ok = False
            print("  NO depth values: the frames are still DISPARITY, not millimetres")
        elif d["fmt"] == ob.OBFormat.Y16 and d["data"].size == d["w"] * d["h"] * 2:
            z = d["data"].view(np.uint16).reshape(d["h"], d["w"])
            mm = z[z > 0].astype(np.float32) * d["scale"]
            print(f"  array dtype {z.dtype}, shape {z.shape}; valid pixels {100 * mm.size / z.size:.1f} %")
            if mm.size:
                print(f"  valid depth min / median / max: {mm.min():.1f} / {np.median(mm):.1f} / {mm.max():.1f} mm")
        else:
            ok = False
            print(f"  NOT 16-bit depth: {d['data'].size} bytes for {d['w']}x{d['h']}")
    c = middle.get(int(F.COLOR_FRAME))
    if c is not None:
        if c["fmt"] == ob.OBFormat.MJPG:
            img = cv2.imdecode(c["data"], cv2.IMREAD_COLOR)
        elif c["fmt"] in (ob.OBFormat.RGB, ob.OBFormat.BGR) and c["data"].size == c["w"] * c["h"] * 3:
            img = c["data"].reshape(c["h"], c["w"], 3)
        else:
            img = None
        print(f"\n[color, middle frame] format {c['fmt'].name}, {c['data'].size} bytes, "
              + (f"decoded to {img.shape} {img.dtype}, mean level {img.mean():.1f}" if img is not None else "NOT decoded here"))

    print("\n[calibration stored in the file]")
    for stype, vp in profiles.items():
        i, k = vp.get_intrinsic(), vp.get_distortion()
        print(f"  {stype.name}: fx {i.fx:.3f} fy {i.fy:.3f} cx {i.cx:.3f} cy {i.cy:.3f} for {i.width}x{i.height}")
        print(f"    distortion {k.model.name}: k1 {k.k1:.6f} k2 {k.k2:.6f} k3 {k.k3:.6f} k4 {k.k4:.6f} k5 {k.k5:.6f} k6 {k.k6:.6f} p1 {k.p1:.6f} p2 {k.p2:.6f}")
    if S.DEPTH_SENSOR in profiles and S.COLOR_SENSOR in profiles:
        e = profiles[S.DEPTH_SENSOR].get_extrinsic_to(profiles[S.COLOR_SENSOR])
        print("  depth -> colour rotation (row-major):\n   ", str(np.array(e.rot, dtype=np.float64).reshape(3, 3)).replace("\n", "\n    "))
        print("  depth -> colour translation (mm):", np.array(e.transform, dtype=np.float64))

    print("\n[settings stored in the file]")
    for name, value in props.items():
        print(f"  {name}: {value}")
    ok &= all(rows[k] for k in kinds)
    print("\nRESULT:", "OK - colour and depth present, every frame in the file read back" if ok else "PROBLEM - see lines marked NO above")
    print("gaps at capture time (frame intervals > 1.5 x median):", gaps)
    return 0 if ok else 1


def run_preview(path, out_dir, last_s=None):
    """Turn an OrbbecViewer recording (.bag) into something a person can look at.

    Run:  python steps/orbbec.py preview RECORDING.bag OUT_DIR [LAST_SECONDS]
          python steps/orbbec.py preview selftest
    Writes OUT_DIR/preview.mp4 (colour | depth | depth laid over colour) and, from the middle frame, OUT_DIR/overlay_mid.jpg,
    colour_mid.jpg and depth_mid.png.
    With LAST_SECONDS only the end of the take is used.
    Depth is laid over colour with the calibration stored in the file (depth intrinsics, depth-to-colour
    rotation and translation, colour intrinsics and distortion).
    """
    prof, depth, color = read_all(path)
    if not depth or not color:
        sys.exit(f"nothing to lay over: {len(depth)} depth frames and {len(color)} colour frames in {path}")
    if last_s:
        cut = max(depth[-1][0], color[-1][0]) - float(last_s) * 1e6
        depth, color = [d for d in depth if d[0] >= cut], [c for c in color if c[0] >= cut]
    os.makedirs(out_dir, exist_ok=True)
    cal = calibration(prof)
    fps = prof[ob.OBSensorType.COLOR_SENSOR].get_fps()
    d_ts = np.array([r[0] for r in depth], np.int64)
    print(f"{len(color)} colour frames, {len(depth)} depth frames")

    container = av.open(os.path.join(out_dir, "preview.mp4"), "w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width, stream.height, stream.pix_fmt = 1920, 360, "yuv420p"
    offsets, valid = [], []
    for n, (ts, fmt, data) in enumerate(color):
        img = colour_image(fmt, data, cal[5])
        if img is None:
            raise RuntimeError(f"colour frame {n} is {fmt}, which this tool cannot read")
        i = int(np.abs(d_ts - ts).argmin())                # the depth frame closest in device time
        offsets.append((d_ts[i] - ts) / 1000)
        mm = depth[i][2].astype(np.float32) * depth[i][1]
        valid.append(100 * (mm > 0).mean())
        on_colour = depth_on_colour(mm, cal)
        tint = paint(on_colour)
        over = np.where((on_colour > 0)[..., None], (0.45 * img + 0.55 * tint).astype(np.uint8), img)
        if n == len(color) // 2:
            cv2.imwrite(os.path.join(out_dir, "overlay_mid.jpg"), over, [cv2.IMWRITE_JPEG_QUALITY, 92])
            cv2.imwrite(os.path.join(out_dir, "colour_mid.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            cv2.imwrite(os.path.join(out_dir, "depth_mid.png"), paint(mm))
        row = np.hstack([fit(img, 640, 360), fit(paint(mm), 640, 360), fit(over, 640, 360)])
        for packet in stream.encode(av.VideoFrame.from_ndarray(row, format="bgr24")):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
    offsets = np.array(offsets)
    print(f"depth frame used for each colour frame: {np.median(offsets):+.1f} ms median offset, worst {np.abs(offsets).max():.1f} ms")
    print(f"valid depth pixels per frame: min {min(valid):.1f} %, median {np.median(valid):.1f} %, max {max(valid):.1f} %")
    print("wrote", os.path.join(out_dir, "preview.mp4"))


def selftest_preview():
    """Raw values made from a known rule and cut to 1 mm the way the SDK does: the fitted rule gives the depth back."""
    rng = np.random.default_rng(0)
    truth = rng.uniform(320.0, 520.0, 400_000)                       # mm
    raw = np.rint(4570930.0 / truth - 6398.5)                        # the camera's raw value for that depth
    truth = 4570930.0 / (raw + 6398.5)
    sdk = np.floor(np.round(truth, 1) + 1e-6)                        # what the SDK hands out at the 1 mm unit
    a, b, agree = fit_rule(sdk, raw, 1.0)
    worst = np.abs(1.0 / (a * raw + b) - truth).max()
    assert agree > 0.99 and worst < 0.02, (agree, worst)
    print(f"PASS: rule fitted on 1 mm values gives the depth back within {worst:.4f} mm (SDK value reproduced in {100 * agree:.2f}%)")


FFMPEG = os.path.join(os.path.dirname(sys.executable), "ffmpeg")      # this environment's build (the gpl one has libx264)
FFMPEG = FFMPEG if os.path.exists(FFMPEG) else "ffmpeg"


def encoder(in_args, bitrate, path, fps):
    """libx264 on every computer, one keyframe a second exactly: the board scan counts keyframes to know the time"""
    gop = str(max(1, round(fps)))
    return subprocess.Popen([FFMPEG, "-v", "error", "-y", *in_args, "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast",
                             "-b:v", bitrate, "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", path], stdin=subprocess.PIPE)


class Writer(threading.Thread):
    """Takes (device_us, system_us, payload) in arrival order, writes one video frame per nominal interval."""

    def __init__(self, name, fps, proc, convert):
        super().__init__(daemon=True, name=name)
        self.q, self.proc, self.convert, self.interval = queue.Queue(), proc, convert, 1e6 / fps
        self.rows, self.real, self.fill, self.backwards, self.error = [], 0, 0, 0, None
        self.mid, self.mid_at = None, None

    def run(self):
        last_ts, last_bytes, n = None, None, 0
        try:
            while (item := self.q.get()) is not None:
                ts, sys_ts, payload = item
                if last_ts is not None and ts < last_ts:
                    self.backwards += 1
                    continue
                b = self.convert(payload)
                if last_ts is not None:
                    for _ in range(max(int(round((ts - last_ts) / self.interval)) - 1, 0)):
                        self.proc.stdin.write(last_bytes)
                        self.rows.append((n, 0, int(last_ts + self.interval * (self.fill + 1)), ""))
                        n, self.fill = n + 1, self.fill + 1
                self.proc.stdin.write(b)
                self.rows.append((n, 1, ts, sys_ts))
                if self.mid_at is not None and self.real == self.mid_at:
                    self.mid = payload
                n, self.real, last_ts, last_bytes = n + 1, self.real + 1, ts, b
                self.fill = 0
        except Exception as e:
            self.error = repr(e)
        self.proc.stdin.close()


def run_export(path, out_dir, rate=4.0):
    """Write an OrbbecViewer recording (.bag) out as two videos a person can scrub through, plus the time of every frame.

    Run:  python steps/orbbec.py export RECORDING.bag OUT_DIR [RATE]
    Writes OUT_DIR/orbbec_colour.mp4 (the colour stream as recorded), OUT_DIR/orbbec_depth.mp4 (depth painted red = near,
    blue = far, black = no reading, 150 to 700 mm), OUT_DIR/orbbec_frames.csv (one line per video frame of each stream with
    its device and host clock in microseconds; real=0 marks a filler copied from the previous frame where the recording has
    a hole, so that frame number / fps stays true time) and OUT_DIR/orbbec_info.txt.
    RATE is the playback speed (default 4). The run fails if fewer frames arrive than the file's index lists.
    The depth values themselves are not copied: the .bag stays the store for them; read_all(near=...) above
    pulls frames by time.
    """
    index = read_index(path)[0]
    topic = lambda s, f: f"/cam/sensor_{int(s)}/frameType_{int(f)}"
    expected = {"colour": len(index.get(topic(S.COLOR_SENSOR, F.COLOR_FRAME), [])),
                "depth": len(index.get(topic(S.DEPTH_SENSOR, F.DEPTH_FRAME), []))}
    host_start = min(v[0] for v in index.values() if v)
    os.makedirs(out_dir, exist_ok=True)

    pb = ob.PlaybackDevice(path)
    pipe, cfg, prof = ob.Pipeline(pb), ob.Config(), {}
    sensors = pb.get_sensor_list()
    for i in range(len(sensors)):
        stype, sp = sensors[i].get_type(), sensors[i].get_stream_profile_list()[0]
        if sp.is_video_stream_profile() and stype in (S.COLOR_SENSOR, S.DEPTH_SENSOR):
            prof[stype] = sp.as_video_stream_profile()
            cfg.enable_stream(stype)
    if S.COLOR_SENSOR not in prof or S.DEPTH_SENSOR not in prof:
        sys.exit(f"the file has no {'colour' if S.COLOR_SENSOR not in prof else 'depth'} stream")
    cfg.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.DISABLE)
    c, d = prof[S.COLOR_SENSOR], prof[S.DEPTH_SENSOR]
    cw, ch, cfps, cfmt = c.get_width(), c.get_height(), c.get_fps(), c.get_format()
    dw, dh, dfps = d.get_width(), d.get_height(), d.get_fps()
    if cfmt == ob.OBFormat.MJPG:
        colour_in, colour_convert = ["-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", str(cfps)], lambda a: a.tobytes()
    elif cfmt == ob.OBFormat.RGB:
        colour_in, colour_convert = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cw}x{ch}", "-framerate", str(cfps)], lambda a: a.tobytes()
    else:
        sys.exit(f"colour is {cfmt.name}, which this tool cannot write")
    depth_in = ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{dw}x{dh}", "-framerate", str(dfps)]

    def depth_convert(payload):
        z, scale = payload
        return paint(z.astype(np.float32) * scale).tobytes()

    colour = Writer("colour", cfps, encoder(colour_in, "8M", os.path.join(out_dir, "orbbec_colour.mp4"), cfps), colour_convert)
    depth = Writer("depth", dfps, encoder(depth_in, "3M", os.path.join(out_dir, "orbbec_depth.mp4"), dfps), depth_convert)
    colour.mid_at, depth.mid_at = expected["colour"] // 2, expected["depth"] // 2
    scale, errors, done, last = [None], [], threading.Event(), [time.monotonic()]

    def on_frames(frameset):   # copies only: the SDK drops frames silently when this is slow
        try:
            for i in range(frameset.get_count()):
                f = frameset.get_frame_by_index(i)
                t = f.get_type()
                if t == F.COLOR_FRAME:
                    colour.q.put((f.get_timestamp_us(), f.get_system_timestamp_us(), np.array(f.get_data())))
                elif t == F.DEPTH_FRAME:
                    df = f.as_depth_frame()
                    scale[0] = df.get_depth_scale()
                    z = np.array(f.get_data()).view(np.uint16).reshape(df.get_height(), df.get_width())
                    depth.q.put((f.get_timestamp_us(), f.get_system_timestamp_us(), (z, scale[0])))
            last[0] = time.monotonic()
        except Exception as e:
            errors.append(repr(e))

    colour.start(), depth.start()
    pb.set_playback_status_change_callback(lambda s: done.set() if s == ob.OBPlaybackStatus.STOPPED else None)
    pb.set_playback_rate(rate)
    pb.pause()
    pipe.start(cfg, on_frames)
    pb.resume()
    t0, paused, deadline = time.monotonic(), False, pb.get_duration() / 1000 / rate + 60
    while not done.is_set() and time.monotonic() - t0 < deadline:
        queued = colour.q.qsize() + depth.q.qsize()
        if not paused and queued > 1500:          # the encoders are behind: hold the file, do not let the SDK drop
            pb.pause(), print(f"  encoders {queued} frames behind, playback held", flush=True)
            paused = True
        elif paused and queued < 300:
            pb.resume()
            paused = False
        done.wait(0.2)
    while time.monotonic() - last[0] < 1.0:
        time.sleep(0.05)
    pipe.stop()
    for w in (colour, depth):
        w.q.put(None)
        w.join()
        w.proc.wait()
    if errors or colour.error or depth.error:
        sys.exit(f"ERROR while reading: {errors[:1] or colour.error or depth.error}")

    with open(os.path.join(out_dir, "orbbec_frames.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["stream", "frame", "real", "device_us", "system_us"])
        for name, w in (("colour", colour), ("depth", depth)):
            wr.writerows([name, *r] for r in w.rows)
    if colour.mid is not None:
        img = cv2.imdecode(colour.mid, cv2.IMREAD_COLOR) if cfmt == ob.OBFormat.MJPG else cv2.cvtColor(colour.mid.reshape(ch, cw, 3), cv2.COLOR_RGB2BGR)
        cv2.imwrite(os.path.join(out_dir, "colour_mid.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if depth.mid is not None:
        cv2.imwrite(os.path.join(out_dir, "depth_mid.png"), paint(depth.mid[0].astype(np.float32) * depth.mid[1]))

    start = datetime.datetime.fromtimestamp(host_start / 1e6, datetime.timezone.utc)
    lines = [f"source: {path}",
             f"recording started (host clock): {start.isoformat()} = {start.astimezone().isoformat()} on this Mac now",
             f"colour: {cfmt.name} {cw}x{ch} @ {cfps} fps, {colour.real} frames in the file's index: {expected['colour']}, "
             f"{sum(1 for r in colour.rows if not r[1])} filler frames added for holes, {colour.backwards} out-of-order frames skipped",
             f"depth: {dw}x{dh} @ {dfps} fps, unit {scale[0]} mm, {depth.real} frames in the file's index: {expected['depth']}, "
             f"{sum(1 for r in depth.rows if not r[1])} filler frames added for holes, {depth.backwards} out-of-order frames skipped",
             f"video frames written: colour {len(colour.rows)}, depth {len(depth.rows)}",
             f"device clock of first colour frame: {colour.rows[0][2] if colour.rows else None} us, "
             f"host clock of first colour frame: {colour.rows[0][3] if colour.rows else None} us",
             f"playback rate {rate}, {time.monotonic() - t0:.0f} s wall"]
    for name in ("orbbec_colour.mp4", "orbbec_depth.mp4"):
        lines.append(f"{name}: {os.path.getsize(os.path.join(out_dir, name)) / 1e6:.0f} MB")
    open(os.path.join(out_dir, "orbbec_info.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    short = {k: expected[k] - got for k, got in (("colour", colour.real), ("depth", depth.real))}
    if any(v > 0 for v in short.values()):
        sys.exit(f"FAIL: frames lost during playback: {short}. Rerun with a lower RATE.")
    print("PASS: every indexed frame was written")


def colour_frames(path, rate=RATE):
    """The stored colour lens numbers (K, D in OpenCV order, picture size), the frame rate, and every
    colour frame of the recording as (device time in us, JPEG bytes)."""
    S, F = ob.OBSensorType, ob.OBFrameType
    pb = ob.PlaybackDevice(path)
    sensors = pb.get_sensor_list()
    prof = next((sensors[i].get_stream_profile_list()[0].as_video_stream_profile()
                 for i in range(len(sensors)) if sensors[i].get_type() == S.COLOR_SENSOR), None)
    if prof is None:
        sys.exit(f"no colour stream in {path}")
    i, k = prof.get_intrinsic(), prof.get_distortion()
    if k.model not in (ob.OBCameraDistortionModel.BROWN_CONRADY, ob.OBCameraDistortionModel.BROWN_CONRADY_K6):
        sys.exit(f"the stored distortion model is {k.model.name}, which OpenCV's projectPoints does not implement")
    K = np.array([[i.fx, 0, i.cx], [0, i.fy, i.cy], [0, 0, 1]], np.float64)
    D = np.array([k.k1, k.k2, k.p1, k.p2, k.k3, k.k4, k.k5, k.k6], np.float64)

    pipe, cfg = ob.Pipeline(pb), ob.Config()
    cfg.enable_stream(S.COLOR_SENSOR)
    cfg.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.DISABLE)
    frames, errors, done, last = [], [], threading.Event(), [time.monotonic()]

    def on_frames(frameset):   # copies only: the SDK drops frames silently when this is slow
        try:
            for n in range(frameset.get_count()):
                f = frameset.get_frame_by_index(n)
                if f.get_type() == F.COLOR_FRAME:
                    frames.append((f.get_timestamp_us(), f.get_format(), np.array(f.get_data())))
            last[0] = time.monotonic()
        except Exception as e:
            errors.append(repr(e))

    pb.set_playback_status_change_callback(lambda s: done.set() if s == ob.OBPlaybackStatus.STOPPED else None)
    pb.set_playback_rate(rate)
    pb.pause()
    pipe.start(cfg, on_frames)
    pb.resume()
    done.wait(pb.get_duration() / 1000 / rate + 30)
    while time.monotonic() - last[0] < 0.5:
        time.sleep(0.05)
    pipe.stop()

    want = len(read_index(path)[0].get(f"/cam/sensor_{int(S.COLOR_SENSOR)}/frameType_{int(F.COLOR_FRAME)}", []))
    if errors or len(frames) != want:
        sys.exit(f"read {len(frames)} of the {want} colour frames in the file {errors[:1]}: lower RATE and run again")
    if any(fmt != ob.OBFormat.MJPG for _, fmt, _ in frames):
        sys.exit("the colour stream is not MJPG, so there are no JPEG frames to copy")
    frames = sorted(((ts, jpg) for ts, _, jpg in frames), key=lambda r: r[0])
    fps = 1e6 / float(np.median(np.diff([ts for ts, _ in frames])))
    return K, D, (i.width, i.height), fps, frames


def export(frames, fps, out):
    """The JPEG frames as one video file, byte for byte."""
    # ponytail: frames are spaced evenly at the measured rate, so a lost frame shortens the video's clock
    # by one frame. Enough for lens work; mux with each frame's own time if this video is ever used for timing.
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "mjpeg", "-framerate", f"{fps:.3f}", "-i", "pipe:0",
                           "-c", "copy", str(out)], stdin=subprocess.PIPE)
    for _, jpg in frames:
        ff.stdin.write(jpg)
    ff.stdin.close()
    if ff.wait():
        sys.exit(f"ffmpeg could not write {out}")
    n = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    if int(n) != len(frames):
        sys.exit(f"{out} holds {n.strip()} frames, {len(frames)} were written")


def lens_error(views, K, D):
    """Per view, the squared distances (px^2) between the board corners as found and as K, D place them
    once the board's pose is fitted to that view alone."""
    sq = []
    for v in views:
        r, t = calib.solve_pose("standard", v["obj"], v["img"], K, D)
        sq.append(((calib.project("standard", v["obj"], r, t, K, D) - v["img"]) ** 2).sum(1))
    return sq


def rms(sq):
    return float(np.sqrt(np.concatenate(sq).mean()))


def run_lens(bag, still_speed=calib.STILL_SPEED):
    """Check the colour lens numbers stored in an Orbbec recording against the 5x7 board it shows.

    Run:  python steps/orbbec.py lens TAKE.bag [STILL_SPEED]
          python steps/orbbec.py lens selftest

    Writes TAKE_colour.mov next to the recording: the colour frames as the camera compressed them, copied
    and not re-encoded, so `calib.py` reads them like any other camera's video. Writes
    TAKE_stored_intrinsics.json: the lens numbers stored in the camera, in the format `calib.py extrinsics
    --intrinsics` reads. Then it takes the held-still views `calib.py intrinsics` uses (STILL_SPEED is its
    `--still-speed`) and reports how far the stored lens numbers put the board corners from where they are
    found.
    """
    bag = Path(bag)
    K, D, size, fps, frames = colour_frames(str(bag))
    video = bag.with_name(bag.stem + "_colour.mov")
    export(frames, fps, video)
    print(f"{video}: {len(frames)} colour frames, {size[0]}x{size[1]} at {fps:.2f} fps, copied as recorded")
    stored = bag.with_name(bag.stem + "_stored_intrinsics.json")
    stored.write_text(json.dumps(dict(camera="Orbbec colour camera, lens numbers as stored in the camera", source=str(bag.resolve()),
                                      image_size=list(size), selected="standard",
                                      models=dict(standard=dict(K=K.tolist(), dist=D.tolist()))), indent=2))
    print(f"{stored}: the stored lens numbers, for `calib.py extrinsics --intrinsics`")

    views, stats, seen, _ = calib.collect_views(calib.frames(video, calib.VIDEO_FPS), still_speed=float(still_speed))
    print(f"board found in {stats['board_found']} of {stats['frames']} analysed frames, moving in {stats['moving']}"
          f" -> {len(views)} still views")
    if not views:
        sys.exit("no still view of the board")
    if seen != size:
        sys.exit(f"the video is {seen[0]}x{seen[1]}, the stored lens numbers are for {size[0]}x{size[1]}")
    sq = lens_error(views, K, D)
    cells, gx, gy = calib.coverage(views, size)
    print(f"stored lens numbers: fx {K[0, 0]:.1f} fy {K[1, 1]:.1f} cx {K[0, 2]:.1f} cy {K[1, 2]:.1f}")
    print(f"  against the board: RMS {rms(sq):.3f} px over {len(views)} views, worst view {max(np.sqrt(s.mean()) for s in sq):.3f} px,"
          f" corners in {len(cells)}/{gx * gy} cells of the picture")


def selftest_lens():
    """Corners projected through known lens numbers come back with no error; a 1% longer focal length shows."""
    rng = np.random.default_rng(0)
    K = np.array([[1270.0, 0, 964], [0, 1270, 542], [0, 0, 1]])
    D = np.array([0.114, -0.361, -0.0003, -0.0002, 0.288, 0, 0, 0])
    obj = calib.charuco("main")[0].getChessboardCorners()
    views = []
    for _ in range(20):   # the board 22 to 32 cm away, tilted up to about 35 degrees
        rvec, tvec = rng.uniform(-0.6, 0.6, 3), np.array([rng.uniform(-90, 10), rng.uniform(-100, -20), rng.uniform(220, 320)])
        views.append(dict(obj=obj, img=calib.project("standard", obj, rvec, tvec, K, D).astype(np.float32)))
    exact, longer = rms(lens_error(views, K, D)), rms(lens_error(views, K * [[1.01], [1.01], [1]], D))
    print(f"true numbers {exact:.4f} px, focal length 1% long {longer:.3f} px")
    assert exact < 0.01 and longer > 10 * max(exact, 0.01), "lens_error does not separate right from wrong lens numbers"
    print("PASS")


def measure(obj, img, zmm, cal):
    """One colour view of the board (corners: obj in board mm, img in px) against one depth picture (mm, 0 = no
    depth). Returns the share of the board's area that has depth and, when that is enough: how far the depth
    surface lies behind the plane the colour camera sees (mm at the board's centre, + = farther from the
    camera), the angle between the two (deg), the depth points' scatter around their own plane (mm rms), the
    board's distance from the camera (mm) and how far it is turned away from facing the camera (deg)."""
    (fx, fy, cx, cy), K, D, R, t, _ = cal                           # X_colour = R @ X_depth + t
    rvec, tvec = calib.solve_pose("standard", obj, img, K, D)
    Rb, tb = cv2.Rodrigues(rvec)[0], np.asarray(tvec, np.float64).ravel()   # X_colour = Rb @ X_board + tb
    spec = calib.BOARDS["main"]
    w, h = (n * spec["square_mm"] for n in spec["squares"])
    m = MARGIN_MM
    rim = np.array([[m, m, 0], [w - m, m, 0], [w - m, h - m, 0], [m, h - m, 0]])
    rim = (rim @ Rb.T + tb - t) @ R                                 # the board's outline, seen from the depth camera
    outline = np.stack([rim[:, 0] / rim[:, 2] * fx + cx, rim[:, 1] / rim[:, 2] * fy + cy], 1)
    mask = np.zeros(zmm.shape, np.uint8)
    cv2.fillConvexPoly(mask, np.rint(outline).astype(np.int32), 1)
    v, u = np.nonzero(mask)
    z = zmm[v, u].astype(np.float64)
    ok = z > 0
    cover = float(ok.mean()) if len(z) else 0.0
    if cover < MIN_COVER or ok.sum() < 500:
        return dict(cover=cover)
    u, v, z = u[ok], v[ok], z[ok]
    p = (np.stack([(u - cx) / fx * z, (v - cy) / fy * z, z], 1) @ R.T + t - tb) @ Rb   # depth points in board mm
    A = np.column_stack([np.ones(len(p)), p[:, 0] - w / 2, p[:, 1] - h / 2])
    keep = np.ones(len(p), bool)
    for _ in range(4):                                              # glare spots and fingers fall out here
        coef = np.linalg.lstsq(A[keep], p[keep, 2], rcond=None)[0]
        res = p[:, 2] - A @ coef
        keep = np.abs(res) < max(3 * 1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))), 0.3)
    side = np.sign((Rb.T @ tb)[2])                                  # +1 when the camera is on the board's -z side
    return dict(cover=cover, behind=float(side * coef[0]), angle=float(np.degrees(np.arctan(np.hypot(coef[1], coef[2])))),
                flat=float(np.sqrt((res[keep] ** 2).mean())), used=float(keep.mean()),
                dist=float(np.linalg.norm(Rb @ [w / 2, h / 2, 0] + tb)), turn=float(np.degrees(np.arccos(abs(Rb[2, 2])))))


def still_views(frames, size, still_speed):
    """The colour frames (every EVERY-th) in which the board is found and moves slower than the limit."""
    board, det = calib.charuco("main")
    obj_all = board.getChessboardCorners()
    views, prev, found = [], None, 0
    for ts, jpg in frames[::EVERY]:
        cc, ci, _, mi = det.detectBoard(cv2.imdecode(jpg, cv2.IMREAD_GRAYSCALE))
        if (ci is None or len(ci) < calib.MIN_CORNERS or (mi is not None and len(np.unique(mi)) < len(mi))
                or board.checkCharucoCornersCollinear(ci)):
            prev = None
            continue
        found += 1
        ids = ci.ravel()
        view = dict(t=ts / 1e6, ts=ts, ids=ids, img=cc.reshape(-1, 2).astype(np.float32), obj=obj_all[ids])
        speed = np.inf if prev is None else calib.corner_speed(prev, view)
        prev = view
        if speed <= still_speed * np.hypot(*size):
            views.append(view)
    return views, found


def spread(x):
    return f"{np.median(x):+.2f} (middle 80%: {np.percentile(x, 10):+.2f} to {np.percentile(x, 90):+.2f})"


def run_depth(bag, still_speed=calib.STILL_SPEED):
    """Check an Orbbec recording's depth against the 5x7 board it shows.

    Run:  python steps/orbbec.py depth TAKE.bag [STILL_SPEED]
          python steps/orbbec.py depth selftest

    In every colour frame where the board is nearly still (STILL_SPEED as in `calib.py intrinsics`), the stored
    colour lens numbers say where the board's plane is. The depth frame closest in time is then measured on the
    board's area: how far the depth surface lies behind that plane, how much it is tilted against it, and how
    flat it is.
    """
    _, _, size, _, frames = colour_frames(bag)
    views, found = still_views(frames, size, float(still_speed))
    print(f"{len(frames)} colour frames, every {EVERY}rd looked at: board in {found}, nearly still in {len(views)}")
    if not views:
        sys.exit("no nearly still view of the board")
    prof, depth, _ = read_all(bag, near=([v["ts"] for v in views], PAIR_US), colour=False, rate=RATE)
    cal = calibration(prof)
    d_ts = np.array([d[0] for d in depth], np.int64)
    rows = []
    for v in views:
        i = int(np.abs(d_ts - v["ts"]).argmin()) if len(d_ts) else -1
        if i >= 0 and abs(d_ts[i] - v["ts"]) <= PAIR_US:
            rows.append(measure(v["obj"], v["img"], depth[i][2].astype(np.float32) * depth[i][1], cal))
    cover = np.array([r["cover"] for r in rows])
    good = [r for r in rows if "behind" in r]
    print(f"{len(rows)} of them have a depth frame within {PAIR_US / 1000:.0f} ms; depth unit {depth[0][1]:g} mm")
    print(f"share of the board's area with depth: median {100 * np.median(cover):.0f}%, "
          f"frames with at least {100 * MIN_COVER:.0f}%: {len(good)}")
    if len(good) < 5:
        sys.exit("too few frames with depth on the board to judge")
    col = {k: np.array([r[k] for r in good]) for k in good[0]}
    print(f"board {col['dist'].min():.0f} to {col['dist'].max():.0f} mm from the camera, turned {col['turn'].min():.0f} to {col['turn'].max():.0f} deg")
    print(f"depth surface behind the colour plane, mm: {spread(col['behind'])}")
    print(f"angle between the two, deg:               {spread(col['angle'])}")
    print(f"depth scatter around its own plane, mm:   {spread(col['flat'])}")
    if np.ptp(col["dist"]) >= 50:                                   # a trend needs a range of distances
        slope, at0 = np.polyfit(col["dist"], col["behind"], 1)
        print(f"behind = {at0:+.2f} mm {slope * 100:+.2f} mm per 100 mm of distance (a pure scale difference of {slope * 100:+.2f}%)")
    print("by distance (mm):   frames  cover  behind   angle   flat")
    edges = np.arange(np.floor(col["dist"].min() / 50) * 50, col["dist"].max() + 50, 50)
    for lo, hi in zip(edges[:-1], edges[1:]):
        s = (col["dist"] >= lo) & (col["dist"] < hi)
        if s.sum():
            print(f"  {lo:4.0f} - {hi:4.0f}       {s.sum():5d}  {100 * np.median(col['cover'][s]):4.0f}%  {np.median(col['behind'][s]):+6.2f}  "
                  f"{np.median(col['angle'][s]):6.2f}  {np.median(col['flat'][s]):5.2f}")
    print("by turn (deg):      frames  cover  behind   angle   flat")
    for lo, hi in ((0, 15), (15, 30), (30, 45), (45, 90)):
        s = (col["turn"] >= lo) & (col["turn"] < hi)
        if s.sum():
            print(f"  {lo:4.0f} - {hi:4.0f}       {s.sum():5d}  {100 * np.median(col['cover'][s]):4.0f}%  {np.median(col['behind'][s]):+6.2f}  "
                  f"{np.median(col['angle'][s]):6.2f}  {np.median(col['flat'][s]):5.2f}")


def selftest_depth():
    """A depth picture made from a plane 1.5 mm behind the board and tilted 0.2 deg against it comes back as that."""
    cal = ((952.7, 952.7, 634.9, 410.3), np.array([[1269.8, 0, 963.7], [0, 1269.8, 542.1], [0, 0, 1]]),
           np.array([0.114, -0.361, -0.0003, -0.0002, 0.288, 0, 0, 0]),
           cv2.Rodrigues(np.array([0.001, -0.004, 0.0035]))[0], np.array([-19.6, 0.02, -0.88]), (1920, 1080))
    (fx, fy, cx, cy), K, D, R, t, _ = cal
    obj = calib.charuco("main")[0].getChessboardCorners()
    for rvec, tvec in ((np.array([0.35, -0.25, 0.1]), np.array([-40.0, -60.0, 330.0])),
                       (np.array([3.0, 0.3, -0.2]), np.array([30.0, 50.0, 280.0]))):      # the board seen from both sides
        Rb = cv2.Rodrigues(rvec)[0]
        img = calib.project("standard", obj, rvec, tvec, K, D).astype(np.float32)
        away = Rb[:, 2] * np.sign(Rb[:, 2] @ tvec)                                        # board normal, pointing away from the camera
        n = cv2.Rodrigues(Rb[:, 0] * np.radians(0.2))[0] @ away                            # tilted 0.2 deg about the board's x axis
        p0 = Rb @ [50, 70, 0] + tvec + 1.5 * away                                         # 1.5 mm behind the board's centre
        n_d, p0_d = R.T @ n, R.T @ (p0 - t)
        u, v = np.meshgrid(np.arange(1280), np.arange(800))
        z = (n_d @ p0_d) / (n_d[0] * (u - cx) / fx + n_d[1] * (v - cy) / fy + n_d[2])
        r = measure(obj, img, np.round(z, 1).astype(np.float32), cal)
        print({k: round(x, 3) for k, x in r.items()})
        assert r["cover"] > 0.99 and abs(r["behind"] - 1.5) < 0.03 and abs(r["angle"] - 0.2) < 0.02 and r["flat"] < 0.05, r
    print("PASS")


def heights(mean, solid, K, box):
    """Height of every depth pixel above the plane of the support around the box, mm (toward the camera = up).
    Returns the height picture, the plane's rms and its tilt from straight-down, and the plane itself."""
    fx, fy, cx, cy = K
    x0, x1, y0, y1 = box
    v, u = np.mgrid[0:mean.shape[0], 0:mean.shape[1]]
    P = np.dstack([(u - cx) / fx * mean, (v - cy) / fy * mean, mean])
    ring = np.zeros(mean.shape, bool)
    ring[max(0, y0 - PAD):y1 + PAD, max(0, x0 - PAD):x1 + PAD] = True
    ring[y0 - 8:y1 + 8, x0 - 8:x1 + 8] = False
    pts = P[ring & solid]
    if len(pts) < 500:
        sys.exit(f"only {len(pts)} depth points on the support around the box: is there flat support all around the simulator?")
    for _ in range(4):                       # drop points more than 1.5 mm off the plane, refit
        c = pts.mean(0)
        nrm = np.linalg.svd(pts - c, full_matrices=False)[2][2]
        nrm = nrm if nrm[2] < 0 else -nrm    # pointing toward the camera
        d = (pts - c) @ nrm
        pts = pts[np.abs(d) < 1.5]
    rms = float(np.sqrt((d[np.abs(d) < 1.5] ** 2).mean()))
    return (P - c) @ nrm, rms, float(np.degrees(np.arccos(abs(nrm[2])))), (c, nrm)


def relief(h, valid, foot):
    """The surface lit from the upper left. foot = millimetres per pixel."""
    h = cv2.GaussianBlur(np.where(valid, h, 0).astype(np.float32), (0, 0), 0.8)
    gy, gx = np.gradient(h)
    gx, gy = gx / foot, gy / foot
    nz = 1.0 / np.sqrt(1 + gx * gx + gy * gy)
    light = np.array([-0.5, -0.6, 0.62]) / np.linalg.norm([-0.5, -0.6, 0.62])
    img = np.clip(40 + 215 * ((-gx * nz) * light[0] + (-gy * nz) * light[1] + nz * light[2]), 0, 255).astype(np.uint8)
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img[~valid] = NO_DEPTH
    return img


def heat(h, valid, top):
    """Height as colour: the hottest colour at the simulator's highest point, the coldest TOP_MM below it."""
    img = cv2.applyColorMap((np.clip((h - (top - TOP_MM)) / TOP_MM, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    img[~valid] = NO_DEPTH
    return img


def face(Hb, ok):
    """The simulator's face in a height picture: the points within FACE_MM below its highest point, and that
    highest point. (None, None) if nothing stands above the surroundings."""
    raised = ok & (Hb > MIN_HEIGHT_MM)
    if raised.sum() < 500:
        return None, None
    top = float(np.percentile(Hb[raised], 99.5))
    return ok & (Hb > top - FACE_MM), top


def look(path, box):
    """One take: the three pictures of the box and the numbers."""
    prof, depth, color = read_all(path)
    if not depth or not color:
        sys.exit(f"{path}: {len(depth)} depth and {len(color)} colour frames, both are needed")
    (fx, fy, cx, cy), Kc, dist, R, t, size = calibration(prof)
    Z = np.stack([d[2] for d in depth]).astype(np.float32) * depth[0][1]
    n = len(Z)
    cnt = (Z > 0).sum(0)
    mean = Z.sum(0) / np.maximum(cnt, 1)
    std = np.sqrt((np.where(Z > 0, Z - mean, 0) ** 2).sum(0) / np.maximum(cnt - 1, 1))
    solid = cnt >= 0.9 * n
    x0, x1, y0, y1 = box
    sl = (slice(y0, y1), slice(x0, x1))
    H, rms, tilt, (c, nrm) = heights(mean, solid, (fx, fy, cx, cy), box)
    one = Z[n // 2]
    v, u = np.mgrid[0:one.shape[0], 0:one.shape[1]]
    H1 = (np.dstack([(u - cx) / fx * one, (v - cy) / fy * one, one]) - c) @ nrm     # the middle frame alone
    Hb, ok = H[sl], solid[sl]
    sim, top = face(Hb, ok)
    if sim is None:
        sys.exit(f"{path}: nothing in the box stands {MIN_HEIGHT_MM:.0f} mm above its surroundings; check the box")
    zsim = mean[sl][sim]
    foot = float(np.median(zsim) / fx)
    outline = cv2.dilate(sim.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    stats = dict(frames=n, distance_mm=float(np.median(zsim)), nearest_mm=float(zsim.min()), points=int(sim.sum()),
                 mm_per_point=foot, wobble_mm=float(np.median(std[sl][sim])), top_mm=top,
                 no_depth_pct=float(100 * (1 - ok[outline].mean())), support_rms_mm=rms, tilt_deg=tilt)
    # the colour picture on the depth grid
    zb = mean[sl]
    vv, uu = np.mgrid[y0:y1, x0:x1]
    pts = np.stack([(uu - cx) / fx * zb, (vv - cy) / fy * zb, zb], -1).reshape(-1, 3).astype(np.float64)
    px = np.full((len(pts), 2), -1, np.float32)
    good = pts[:, 2] > 0
    px[good] = cv2.projectPoints(pts[good], cv2.Rodrigues(R)[0], t, Kc, dist)[0].reshape(-1, 2)
    c_ts = np.array([r[0] for r in color], np.int64)
    _, fmt, data = color[int(np.abs(c_ts - depth[n // 2][0]).argmin())]
    col = cv2.remap(colour_image(fmt, data, size), px[:, 0].reshape(zb.shape), px[:, 1].reshape(zb.shape),
                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=NO_DEPTH)
    ok1 = one[sl] > 0
    return [col, heat(H1[sl], ok1, top), relief(H1[sl], ok1, foot)], stats


def sheet(rows):
    """rows: (pictures, stats) per take. Every row at MM_PER_PX, the distance written on its left."""
    drawn = []
    for pics, st in rows:
        k = st["mm_per_point"] / MM_PER_PX
        pics = [cv2.resize(p, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC) for p in pics]
        strip = np.hstack(pics)
        label = np.full((strip.shape[0], 230, 3), 255, np.uint8)
        cv2.putText(label, f"{st['distance_mm'] / 10:.0f} cm", (14, strip.shape[0] // 2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
        drawn.append(np.hstack([label, strip]))
    w = max(d.shape[1] for d in drawn)
    drawn = [cv2.copyMakeBorder(d, 0, 12, 0, w - d.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for d in drawn]
    return np.vstack(drawn)


def selftest_face():
    """A tilted flat support with a 20 mm dome on it, seen from 350 mm: the height comes back within 0.05 mm."""
    K = (952.7, 952.7, 640.0, 400.0)
    v, u = np.mgrid[0:800, 0:1280].astype(np.float64)
    nrm = np.array([0.15, 0.25, -1.0]) / np.linalg.norm([0.15, 0.25, -1.0])     # the support's normal
    z = 350 * nrm[2] / (nrm[0] * (u - 640) / 952.7 + nrm[1] * (v - 400) / 952.7 + nrm[2])
    r2 = ((u - 640) / 60) ** 2 + ((v - 400) / 60) ** 2
    bump = np.where(r2 < 1, 20 * (1 - r2), 0.0)                        # mm toward the camera along the depth axis
    mean = (z - bump).astype(np.float32)
    H, rms, tilt, _ = heights(mean, np.ones(mean.shape, bool), K, (560, 720, 320, 480))
    top = H[400, 640]                                                  # the dome top sits on the optical axis
    want = 20 * abs(nrm[2])
    assert abs(tilt - np.degrees(np.arccos(abs(nrm[2])))) < 0.01, tilt
    assert rms < 0.01, rms
    assert abs(top - want) < 0.05, (top, want)
    assert abs(H[300, 500]) < 0.01, H[300, 500]                       # the support itself is at height zero
    img = relief(H[320:480, 560:720], np.ones((160, 160), bool), 350 / 952.7)
    assert img.shape == (160, 160, 3) and img.std() > 5               # the dome is shaded, not flat grey

    def scene(pedestal):   # a 45 mm dome, on the support or raised on a pedestal narrower than the box
        dome = np.where(r2 < 1, 45 * (1 - r2), 0.0)
        ped = np.where((np.abs(u - 640) < 70) & (np.abs(v - 400) < 70), pedestal, 0.0)
        Hs = heights((z - dome - ped).astype(np.float32), np.ones(z.shape, bool), K, (560, 720, 320, 480))[0][320:480, 560:720]
        return face(Hs, np.ones(Hs.shape, bool))
    (low, top_low), (high, top_high) = scene(0.0), scene(30.0)
    assert abs((top_high - top_low) - 30 * abs(nrm[2])) < 0.5, (top_low, top_high)
    assert low.sum() > 1000 and abs(int(high.sum()) - int(low.sum())) <= 0.02 * low.sum(), (low.sum(), high.sum())
    print(f"PASS: dome top {top:.2f} mm (expected {want:.2f}), support rms {rms:.4f} mm, tilt {tilt:.1f} deg; "
          f"face points on the support {int(low.sum())}, raised 30 mm {int(high.sum())}")


def run_face(argv):
    """Close look at the simulator in still Orbbec takes: what the depth shows on the face, takes stacked for comparison.

    Run:  python steps/orbbec.py face OUT.png TAKE.bag x0,x1,y0,y1 [TAKE2.bag x0,x1,y0,y1 ...]
          python steps/orbbec.py face selftest
    The box is in depth-picture pixels (1280x800) and must hold the simulator with flat support all around it.
    Each take becomes one row: colour, height above the support, the surface as a relief. Purple is no depth.
    Rows are drawn at the same millimetres per pixel, so a nearer take shows its extra detail and not a bigger picture.
    """
    if argv == ["selftest"]:
        return selftest_face()
    if len(argv) < 3 or len(argv) % 2 == 0:
        sys.exit(__doc__)
    rows = []
    for path, box in zip(argv[1::2], argv[2::2]):
        pics, st = look(path, tuple(int(a) for a in box.split(",")))
        rows.append((pics, st))
        print(f"{path.split('/')[-1]}: {st['frames']} frames, simulator {st['distance_mm']:.0f} mm from the camera (nearest point "
              f"{st['nearest_mm']:.0f}), {st['points']} depth points, one every {st['mm_per_point']:.2f} mm, wobble {st['wobble_mm']:.3f} mm, "
              f"top {st['top_mm']:.1f} mm above the support, no depth on {st['no_depth_pct']:.1f}% of its outline; "
              f"support plane rms {st['support_rms_mm']:.2f} mm, camera {st['tilt_deg']:.0f} deg off straight-down")
    cv2.imwrite(argv[0], sheet(rows))
    print("wrote", argv[0])


def cli(argv):
    cmd, a = (argv[0], argv[1:]) if argv else ('', [])
    if {'-h', '--help'} & set(argv):
        run = {'check': run_check, 'preview': run_preview, 'export': run_export, 'lens': run_lens, 'depth': run_depth, 'face': run_face}.get(cmd)
        print(run.__doc__ if run else __doc__)
    elif cmd == 'selftest' and not a:
        selftest_preview(); selftest_lens(); selftest_depth(); selftest_face()
    elif cmd == 'check' and a:
        try:
            sys.exit(run_check(a[0], float(a[1]) if len(a) > 1 else 1.0))
        except Exception as e:  # PlaybackDevice raises RuntimeError for unreadable files
            traceback.print_exc()
            sys.exit(f'CANNOT READ {a[0]}: {e}')
    elif cmd == 'preview' and a == ['selftest']:
        selftest_preview()
    elif cmd == 'preview' and len(a) in (2, 3):
        run_preview(*a)
    elif cmd == 'export' and len(a) in (2, 3):
        run_export(a[0], a[1], float(a[2]) if len(a) == 3 else 4.0)
    elif cmd in ('lens', 'depth') and len(a) in (1, 2):
        if a[0] == 'selftest':
            (selftest_lens if cmd == 'lens' else selftest_depth)()
        else:
            (run_lens if cmd == 'lens' else run_depth)(*a)
    elif cmd == 'face' and a:
        run_face(a)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    cli(sys.argv[1:])
