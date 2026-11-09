"""Simulator rig: marker detection check, ChArUco intrinsic calibration and extrinsics.
steps/orbbec.py imports it for the board and the lens models.

  python steps/calib.py detect VIDEO
  python steps/calib.py intrinsics VIDEO --name action_cam_1 --settings "1080p 60 fps, stabilisation off"
  python steps/calib.py extrinsics VIDEO --name phone --intrinsics steps/calib/phone_<stamp>/intrinsics.json

`detect` reports what one camera sees (board corners, base markers 40-43) and saves annotated frames.
`extrinsics` gives one still camera's pose relative to the board (steps/calib/<name>_extrinsics_<stamp>/).
`intrinsics` picks one view per held-still pose, fits standard / rational / fisheye lens models, scores
each on held-out time blocks and writes steps/calib/<name>_<stamp>/intrinsics.json plus coverage and
undistortion previews.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
aruco = cv2.aruco
DICT = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)

# Printed targets (the PDF itself was checked at 20.00 mm squares and 35.0 mm markers). If the ruler shows
# the PRINT is uniformly off, enter the measured sizes.
BOARDS = {
    "main": dict(squares=(5, 7), square_mm=20.0, marker_mm=14.0),       # ids 0-16: lens calibration + fixed reference
    "intrinsic": dict(squares=(7, 9), square_mm=20.0, marker_mm=14.0),  # optional; ids 0-30 clash with main!
}
BASE_IDS = (40, 41, 42, 43)  # 35 mm markers on the simulator base

MIN_CORNERS = 10       # ChArUco corners a view needs to be used for intrinsics
STILL_SPEED = 0.017    # "held still" (image diagonals / s): 25 px/s at 720p, under 0.5 px blur per frame
MIN_STILL_S = 0.2      # a pose must be held this long to become a view
SKIP_START_S = 1.0     # a take's first second: a focus-locked lens may still be moving into place
VIDEO_FPS = 10         # frames/s analysed from calibration videos
MAX_VIEWS = 80
FOLDS = 5
HOLDOUT_PX = 1.0       # held-out RMS a usable fit stays under: the gate, and who may vouch at the frame border
POSE_PX = 0.5          # reprojection RMS a still clip stays under with its lens calibration held fixed
MODELS = ("standard", "rational", "fisheye")
BORDER_DEG = 3.0       # a model whose frame-border rays disagree with every other model by more is dropped
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def charuco(name):
    spec = BOARDS[name]
    board = aruco.CharucoBoard(spec["squares"], spec["square_mm"], spec["marker_mm"], DICT)
    params = aruco.DetectorParameters()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    params.minMarkerPerimeterRate = 0.015  # default 0.03 rejects ~20 px markers in 2.7K/4K frames
    # Read each marker bit from its cell's centre only (defaults 4 and 0.13 use the whole cell): a bright
    # screen board blooms, its white bits swell into the neighbouring cells and the ids stop decoding.
    params.perspectiveRemovePixelPerCell = 8
    params.perspectiveRemoveIgnoredMarginPerCell = 0.35
    return board, aruco.CharucoDetector(board, aruco.CharucoParameters(), params)


def is_video(src):
    src = Path(src)
    return not (src.is_dir() or src.suffix.lower() in IMAGE_EXT)


def frames(src, fps=None):
    """Yield (time_s, BGR frame) with pixels as the sensor stored them. Rotation flags (video display
    matrix, photo EXIF) are ignored, so a calibration holds however the camera sits in its mount and
    matches Apple's per-frame intrinsics. Video times are the frames' own timestamps (phone files have
    gaps), optionally subsampled to `fps`; an image or folder yields one frame per file (time = index)."""
    src = Path(src)
    if not is_video(src):
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXT) if src.is_dir() else [src]
        for i, f in enumerate(files):
            img = cv2.imread(str(f), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
            if img is None:
                sys.exit(f"Cannot read image {f}")
            yield float(i), img
        return
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        sys.exit(f"Cannot open {src}")
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
    rate = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(rate / fps)) if fps else 1
    i = 0
    while cap.grab():
        if i % step == 0:
            ok, img = cap.retrieve()
            if ok:
                yield cap.get(cv2.CAP_PROP_POS_MSEC) / 1000, img
        i += 1


def probe(src):
    """Codec, resolution, fps, HDR transfer and rotation as recorded, stored with the calibration."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                            "-show_format", "-of", "json", str(src)], capture_output=True, text=True, timeout=60)
        j = json.loads(r.stdout)
        s = j["streams"][0]
        keep = ("codec_name", "profile", "width", "height", "avg_frame_rate", "pix_fmt", "color_transfer",
                "side_data_list", "tags")
        return {"stream": {k: s[k] for k in keep if k in s}, "format_tags": j.get("format", {}).get("tags", {})}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as e:
        return {"error": str(e)}


def corner_speed(a, b):
    """Mean corner displacement between two detections, in px per second."""
    _, ia, ib = np.intersect1d(a["ids"], b["ids"], return_indices=True)
    if len(ia) < 4:
        return np.inf
    return np.linalg.norm(a["img"][ia] - b["img"][ib], axis=1).mean() / max(b["t"] - a["t"], 1e-6)


def collect_views(frame_iter, board_name="main", video=True, still_speed=STILL_SPEED):
    """One view per held-still pose (video) or per image. Returns views, stats, image size and the
    frame whose corners reach furthest from the centre (used for previews)."""
    board, det = charuco(board_name)
    obj_all = board.getChessboardCorners()
    views, run, prev, size, sample, reach = [], [], None, None, None, -1.0
    stats = dict(frames=0, board_found=0, duplicate_ids=0, moving=0)

    def close_run():
        if run and (not video or run[-1]["t"] - run[0]["t"] >= MIN_STILL_S):
            views.append(run[len(run) // 2])
        run.clear()

    for t, img in frame_iter:
        if video and t < SKIP_START_S:
            continue
        stats["frames"] += 1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if size is None:
            size = gray.shape[::-1]
        elif gray.shape[::-1] != size:
            sys.exit(f"Frame size changed from {size} to {gray.shape[::-1]}: one calibration per camera mode.")
        cc, ci, _, mi = det.detectBoard(gray)
        ok = ci is not None and len(ci) >= MIN_CORNERS
        if mi is not None and len(np.unique(mi)) < len(mi):  # same id twice: both boards in view (OpenCV then returns no corners)
            stats["duplicate_ids"] += 1
            ok = False
        if not ok or board.checkCharucoCornersCollinear(ci):
            close_run()
            prev = None
            continue
        stats["board_found"] += 1
        ids = ci.ravel()
        view = dict(t=float(t), ids=ids, img=cc.reshape(-1, 2).astype(np.float32), obj=obj_all[ids])
        if video:
            speed = np.inf if prev is None else corner_speed(prev, view)
            prev = view
            if speed > still_speed * np.hypot(*size):
                stats["moving"] += 1
                close_run()
                continue
        run.append(view)
        r = np.linalg.norm(view["img"] - np.array(size) / 2, axis=1).max()
        if r > reach:
            reach, sample = r, img.copy()
        if not video:
            close_run()
    close_run()
    return views, stats, size, sample


def board_detections(frame_iter, board_det, video=True):
    """The board in every analysed frame, read once so that several still speeds can be tried on one decode.
    board_det is (board, detector), e.g. camrig.board.detector(project.board). Returns [(t, view or None)], stats
    (frames, board_found, duplicate_ids) and the image size; still_views() then does what collect_views does."""
    board, det = board_det
    obj_all = board.getChessboardCorners()
    dets, size, stats = [], None, dict(frames=0, board_found=0, duplicate_ids=0)
    for t, img in frame_iter:
        if video and t < SKIP_START_S:
            continue
        stats["frames"] += 1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        if size is None:
            size = gray.shape[::-1]
        elif gray.shape[::-1] != size:
            raise ValueError(f"frame size changed from {size} to {gray.shape[::-1]}: one calibration per camera mode")
        cc, ci, _, mi = det.detectBoard(gray)
        ok = ci is not None and len(ci) >= MIN_CORNERS
        if mi is not None and len(np.unique(mi)) < len(mi):
            stats["duplicate_ids"] += 1
            ok = False
        if not ok or board.checkCharucoCornersCollinear(ci):
            dets.append((float(t), None))
            continue
        stats["board_found"] += 1
        ids = ci.ravel()
        dets.append((float(t), dict(t=float(t), ids=ids, img=cc.reshape(-1, 2).astype(np.float32), obj=obj_all[ids])))
    return dets, stats, size


def still_views(dets, size, still_speed=STILL_SPEED, video=True):
    """collect_views' choice of one view per held-still pose, on detections from board_detections().
    Returns the views and how many detections were moving."""
    views, run, prev, moving = [], [], None, 0

    def close_run():
        if run and (not video or run[-1]["t"] - run[0]["t"] >= MIN_STILL_S):
            views.append(run[len(run) // 2])
        run.clear()

    for _, view in dets:
        if view is None:
            close_run()
            prev = None
            continue
        if video:
            speed = np.inf if prev is None else corner_speed(prev, view)
            prev = view
            if speed > still_speed * np.hypot(*size):
                moving += 1
                close_run()
                continue
        run.append(view)
        if not video:
            close_run()
    close_run()
    return views, moving


def intrinsics_gates(views, cells, gx, gy, r):
    """The pass/fail gates of a lens fit: (text, passed, value shown) for the selected model's result r."""
    cover = len(cells) / (gx * gy)
    return [("still views >= 20", len(views) >= 20, f"{len(views)}"),
            ("coverage >= 80% of grid cells", cover >= 0.8, f"{100 * cover:.0f}%"),
            ("held-out RMS <= 1.0 px (good <= 0.5)", r["holdout_rms_px"] <= HOLDOUT_PX, f"{r['holdout_rms_px']:.3f} px"),
            ("fold spread fx <= 1% (good <= 0.5%)", r["fold_spread"]["fx_pct"] <= 1.0, f"{r['fold_spread']['fx_pct']:.2f}%")]


def project(model, obj, rvec, tvec, K, D):
    if model == "fisheye":
        p, _ = cv2.fisheye.projectPoints(obj.reshape(-1, 1, 3).astype(np.float64), rvec, tvec, K, D)
    else:
        p, _ = cv2.projectPoints(obj, rvec, tvec, K, D)
    return p.reshape(-1, 2)


def solve_pose(model, obj, img, K, D):
    solver = cv2.fisheye.solvePnP if model == "fisheye" else cv2.solvePnP
    _, r, t = solver(obj.reshape(1, -1, 3).astype(np.float64), img.reshape(1, -1, 2).astype(np.float64), K, D)
    if model == "fisheye":  # fisheye.solvePnP fits undistorted points; refine in the image like cv2.solvePnP does
        o, img = obj.reshape(-1, 1, 3).astype(np.float64), img.reshape(-1, 2)
        for _ in range(10):
            p, J = cv2.fisheye.projectPoints(o, r, t, K, D)  # J columns 8:14 = d(image)/d(rvec, tvec)
            e = (img - p.reshape(-1, 2)).ravel()
            if not np.isfinite(J).all():
                break
            step = np.linalg.lstsq(J[:, 8:14], e, rcond=None)[0]
            r2, t2 = r + step[:3].reshape(3, 1), t + step[3:].reshape(3, 1)
            if not ((img - project(model, obj, r2, t2, K, D)) ** 2).sum() < (e ** 2).sum():
                break
            r, t = r2, t2
    return r, t


def calibrate(views, size, model):
    obj = [v["obj"] for v in views]
    img = [v["img"] for v in views]
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-9)
    if model == "fisheye":  # OpenCV 5.0 fisheye needs (1, N, 2) image points; (N, 1, 2) throws
        _, K, D, rv, tv = cv2.fisheye.calibrate(
            [o.reshape(1, -1, 3).astype(np.float64) for o in obj],
            [i.reshape(1, -1, 2).astype(np.float64) for i in img], size, np.zeros((3, 3)), np.zeros((4, 1)),
            flags=cv2.CALIB_RECOMPUTE_EXTRINSIC | cv2.CALIB_FIX_SKEW, criteria=crit)
    else:
        # k3 and k6 stay fixed: with all six free, numerator and denominator cancel into a pole just
        # past the outermost board corner (phone, 30 Sep)
        flags = cv2.CALIB_RATIONAL_MODEL | cv2.CALIB_FIX_K3 | cv2.CALIB_FIX_K6 if model == "rational" else 0
        _, K, D, rv, tv = cv2.calibrateCamera(obj, img, size, None, None, flags=flags, criteria=crit)
    sq = [((project(model, o, r, t, K, D) - i) ** 2).sum(1) for o, i, r, t in zip(obj, img, rv, tv)]
    return dict(K=K, D=D.ravel(), rms=float(np.sqrt(np.concatenate(sq).mean())),
                per_view=np.array([np.sqrt(s.mean()) for s in sq]))


def robust_fit(views, size, model):
    """Calibrate, then drop the worst view while its error is > max(3x median, 1 px) (blur, two boards in
    view) and refit. One at a time: under a fit skewed by real outliers, good views look bad too."""
    dropped = []
    for _ in range(len(views) // 4 + 1):
        res = calibrate(views, size, model)
        worst = int(np.argmax(res["per_view"]))
        if res["per_view"][worst] <= max(3 * np.median(res["per_view"]), 1.0):
            break
        dropped.append(views[worst]["t"])
        views = views[:worst] + views[worst + 1:]
    else:
        res = calibrate(views, size, model)
    return res, views, dropped


def border_rays(model, K, D, size):
    """Unit rays of the frame's corners and edge midpoints, or None if the model cannot invert them."""
    w, h = size
    pts = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1], [w / 2, 0], [w / 2, h - 1], [0, h / 2],
                    [w - 1, h / 2]], np.float64)
    crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 100, 1e-12)
    if model == "fisheye":
        xy = cv2.fisheye.undistortPoints(pts.reshape(1, -1, 2), K, D.reshape(4, 1), criteria=crit)
    else:
        xy = cv2.undistortPoints(pts.reshape(-1, 1, 2), K, D, criteria=crit)
    rays = np.column_stack([xy.reshape(-1, 2), np.ones(len(pts))])
    if not np.isfinite(rays).all():
        return None
    back = project(model, rays.astype(np.float32), np.zeros(3), np.zeros(3), K, D)
    if not np.linalg.norm(back - pts, axis=1).max() < 1.0:
        return None
    return rays / np.linalg.norm(rays, axis=1, keepdims=True)


def radial_ok(K, D, size):
    """False if a standard or rational fit breaks before the farthest frame corner: its denominator
    reaches zero, or the distorted radius stops growing. Board corners seldom reach the frame corners,
    so such a fit can pass every reprojection check and still tear the image there."""
    k1, k2, _, _, k3, k4, k5, k6 = np.append(D, np.zeros(8))[:8]
    far = max(np.hypot((x - K[0, 2]) / K[0, 0], (y - K[1, 2]) / K[1, 1]) for x in (0, size[0] - 1) for y in (0, size[1] - 1))
    # ponytail: sampled at 2000 radii (0.4 px apart at 720p); a thinner pole slips through, np.roots on the denominator catches it
    r = np.linspace(0, far, 2000)
    t = r * r
    den = 1 + t * (k4 + t * (k5 + t * k6))
    return bool((den > 0).all() and (np.diff(r * (1 + t * (k1 + t * (k2 + t * k3))) / den) > 0).all())


def border_gaps(rays):
    """Per model, the smallest worst-case angle (deg) between its border rays and another model's. The
    models agree where the board was; a lone one that disagrees with all others at the frame border is
    extrapolating where the board never reached, which the held-out error cannot see."""
    gaps = {}
    for m, r in rays.items():
        gap = [np.degrees(np.arccos(np.clip((r * o).sum(1), -1, 1))).max() for n, o in rays.items() if n != m]
        gaps[m] = min(gap) if len(gap) >= 2 else 0.0
    return gaps


def cross_validate(views, size, model):
    """Leave-one-time-block-out: calibrate on 4/5 of the recording, test on the unseen 1/5.
    Returns pooled held-out RMS (px) and per-fold (fx, fy, cx, cy)."""
    n, sq, params = len(views), [], []
    for f in range(FOLDS):
        lo, hi = n * f // FOLDS, n * (f + 1) // FOLDS
        res = calibrate(views[:lo] + views[hi:], size, model)
        K, D = res["K"], res["D"]
        params.append([K[0, 0], K[1, 1], K[0, 2], K[1, 2]])
        for v in views[lo:hi]:
            r, t = solve_pose(model, v["obj"], v["img"], K, D)
            sq.append(((project(model, v["obj"], r, t, K, D) - v["img"]) ** 2).sum(1))
    return float(np.sqrt(np.concatenate(sq).mean())), np.array(params)


def fit_models(views, size, models=MODELS):
    """Fit each lens model; select the lowest held-out error among models that explain >= 90% of views,
    whose full fit converged (not worse than its own folds), that map the frame border to rays and
    back, and whose radial map holds out to the frame corners. Rational must win by 5%: it can match
    inside the data yet run off tens of px toward frame corners the board never reached."""
    views = sorted(views, key=lambda v: v["t"])
    if len(views) > MAX_VIEWS:
        print(f"  {len(views)} views, using {MAX_VIEWS} spread evenly over the recording")
        views = [views[i] for i in np.linspace(0, len(views) - 1, MAX_VIEWS).round().astype(int)]
    results = {}
    for m in models:
        try:
            res, kept, dropped = robust_fit(views, size, m)
            holdout, folds = cross_validate(kept, size, m)
            K = res["K"]
            spread = np.ptp(folds, axis=0)
            results[m] = dict(
                K=K.tolist(), dist=res["D"].tolist(), rms_px=res["rms"], holdout_rms_px=holdout,
                fold_spread=dict(fx_pct=100 * spread[0] / K[0, 0], fy_pct=100 * spread[1] / K[1, 1],
                                 cx_px=spread[2], cy_px=spread[3]),
                views_used=len(kept), views_dropped_t=dropped, worst_view_px=float(res["per_view"].max()))
        except cv2.error as e:
            results[m] = dict(error=str(e).strip().splitlines()[-1])
    fitted = {m: r for m, r in results.items() if "error" not in r}
    rays = {m: border_rays(m, np.array(r["K"]), np.array(r["dist"]), size) for m, r in fitted.items()}
    # A fit that fails the held-out gate describes no lens, so it neither vouches for nor vetoes another
    # at the border (action camera, 1 Oct: two pinhole fits at 3.5 and 4.4 px shut out the fisheye fit).
    gaps = border_gaps({m: r for m, r in rays.items() if r is not None and fitted[m]["holdout_rms_px"] <= HOLDOUT_PX})
    for m, r in fitted.items():
        r["border_gap_deg"] = float(gaps.get(m, np.inf))
        r["radial_ok"] = m == "fisheye" or radial_ok(np.array(r["K"]), np.array(r["dist"]), size)
    ok = {m: r for m, r in fitted.items() if r["views_used"] >= 0.9 * len(views)
          and r["rms_px"] <= 1.5 * r["holdout_rms_px"] and r["border_gap_deg"] <= BORDER_DEG and r["radial_ok"]}
    selected = min(ok, key=lambda m: ok[m]["holdout_rms_px"] * (1.05 if m == "rational" else 1)) if ok else None
    return results, selected, views


def coverage(views, size):
    """Fraction of a coarse image grid (6x4 landscape / 4x6 portrait) that contains used corners."""
    w, h = size
    gx, gy = (6, 4) if w >= h else (4, 6)
    pts = np.concatenate([v["img"] for v in views])
    cells = set(zip((pts[:, 0] * gx // w).astype(int).clip(0, gx - 1), (pts[:, 1] * gy // h).astype(int).clip(0, gy - 1)))
    return cells, gx, gy


def save_previews(out, sample, views, size, model, K, D, cells, gx, gy):
    w, h = size
    vis = (sample * 0.45).astype(np.uint8)
    for x in range(gx):
        for y in range(gy):
            p0, p1 = (x * w // gx, y * h // gy), ((x + 1) * w // gx, (y + 1) * h // gy)
            if (x, y) not in cells:
                vis[p0[1]:p1[1], p0[0]:p1[0], 2] = 160
            cv2.rectangle(vis, p0, p1, (200, 200, 200), max(1, w // 1000))
    for k, v in enumerate(views):
        hue = int(179 * k / max(1, len(views)))
        color = cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
        for p in v["img"]:
            cv2.circle(vis, (int(p[0]), int(p[1])), max(2, w // 500), color, -1)
    cv2.imwrite(str(out / "coverage.jpg"), vis)
    K, D = np.array(K), np.array(D)
    if model == "fisheye":
        P = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(K, D.reshape(4, 1), size, np.eye(3), balance=0.3)
        m1, m2 = cv2.fisheye.initUndistortRectifyMap(K, D.reshape(4, 1), np.eye(3), P, size, cv2.CV_16SC2)
        und = cv2.remap(sample, m1, m2, cv2.INTER_LINEAR)
    else:
        P, _ = cv2.getOptimalNewCameraMatrix(K, D, size, 0)
        und = cv2.undistort(sample, K, D, None, P)
    both = np.hstack([sample, und])
    s = min(1.0, 2400 / both.shape[1])
    cv2.imwrite(str(out / "undistorted.jpg"), cv2.resize(both, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))


def cmd_intrinsics(a):
    """Several takes of one camera mode (same size, lens, focus) pool into one calibration."""
    srcs = [Path(s) for s in a.src]
    views, stats, size, sample = [], {}, None, None
    for k, src in enumerate(srcs):
        video = is_video(src)
        print(f"Detecting the {a.board} board in {src} ...")
        take, st, sz, smp = collect_views(frames(src, VIDEO_FPS if video else None), a.board, video, a.still_speed)
        if size is not None and sz != size:
            sys.exit(f"{src} is {sz[0]}x{sz[1]}, not {size[0]}x{size[1]}: only takes of one camera mode pool.")
        size, sample = sz, smp if sample is None else sample
        for v in take:
            v["t"] += 100_000 * k  # takes stay in order with unique view times
        views += take
        stats = {key: stats.get(key, 0) + n for key, n in st.items()}
        print(f"  {st['frames']} frames analysed, board in {st['board_found']}, moving {st['moving']}, "
              f"two boards (duplicate ids) {st['duplicate_ids']} -> {len(take)} still views")
    if len(views) < 10:
        sys.exit("Fewer than 10 still views: hold each pose ~1-2 s, keep >= 10 corners visible, cover the other board.")
    results, selected, views = fit_models(views, size, [m.strip() for m in a.models.split(",")])
    if selected:  # gates and coverage.jpg only count views the selected model actually used
        views = [v for v in views if v["t"] not in results[selected]["views_dropped_t"]]
    cells, gx, gy = coverage(views, size)

    print(f"\n  image {size[0]}x{size[1]} (as stored), {len(views)} views, corner coverage {len(cells)}/{gx * gy} grid cells")
    print(f"  {'model':9s} {'views':>6s} {'rms px':>7s} {'holdout':>8s} {'fx':>9s} {'fy':>9s} {'cx':>8s} {'cy':>8s} {'fold fx%':>9s} {'border':>7s}")
    for m, r in results.items():
        if "error" in r:
            print(f"  {m:9s} failed: {r['error']}")
            continue
        K = r["K"]
        print(f"  {m:9s} {r['views_used']:6d} {r['rms_px']:7.3f} {r['holdout_rms_px']:8.3f} {K[0][0]:9.1f} {K[1][1]:9.1f} "
              f"{K[0][2]:8.1f} {K[1][2]:8.1f} {r['fold_spread']['fx_pct']:9.2f} {min(r['border_gap_deg'], 999):6.1f}d"
              + ("   <- selected" if m == selected else "") + ("" if r["radial_ok"] else "   breaks inside the frame"))
    if selected is None:
        sys.exit("No usable model (failed, dropped > 10% of views, full fit worse than its folds, or breaks inside "
                 "the frame): check blur, board flatness and that only one board is visible.")

    r = results[selected]
    gates = intrinsics_gates(views, cells, gx, gy, r)
    print("\n  Gates (heuristics until the metric check with known distances):")
    for text, passed, value in gates:
        print(f"    [{'PASS' if passed else 'FAIL'}] {text}: {value}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = HERE / "calib" / f"{a.name}_{stamp}"
    out.mkdir(parents=True)
    report = dict(camera=a.name, created=stamp, sources=[str(s.resolve()) for s in srcs],
                  camera_settings=a.settings, still_speed=a.still_speed, probes=[probe(s) if is_video(s) else None for s in srcs],
                  image_size=list(size), opencv=cv2.__version__,
                  board=dict(name=a.board, dictionary="DICT_4X4_50", **BOARDS[a.board]),
                  frames=stats, views=len(views), coverage=[len(cells), gx * gy], selected=selected,
                  gates={t: bool(p) for t, p, _ in gates}, models=results)
    (out / "intrinsics.json").write_text(json.dumps(report, indent=2, default=float))
    save_previews(out, sample, views, size, selected, r["K"], r["dist"], cells, gx, gy)
    print(f"\n  Saved {out}/intrinsics.json, coverage.jpg (red = no corners), undistorted.jpg")


def board_pose(frame_iter, board_name, model, K, D, start=0.0, end=np.inf):
    """Pose of the board in a camera that does not move: one PnP per frame from `start` to `end`
    seconds, then the median. Returns R and t (board -> camera, mm), the corners used, how far the
    camera position drifts from the first third of the take to the last (mm: a bumped camera or board
    shows up here), how far single frames stray from the median (mm and deg: detection noise, a
    blooming screen board gives 1-2 mm), the image size and one frame for the preview. None if the
    board was never found."""
    board, det = charuco(board_name)
    obj_all = board.getChessboardCorners()
    Rs, ts, used, size, sample = [], [], [], None, None
    for t, img in frame_iter:
        if not start <= t <= end:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        size = gray.shape[::-1]
        cc, ci, _, mi = det.detectBoard(gray)
        if ci is None or len(ci) < MIN_CORNERS or (mi is not None and len(np.unique(mi)) < len(mi)):
            continue
        ids = ci.ravel()
        view = dict(t=float(t), obj=obj_all[ids], img=cc.reshape(-1, 2).astype(np.float32))
        r, tv = solve_pose(model, view["obj"], view["img"], K, D)
        Rs.append(cv2.Rodrigues(r)[0])
        ts.append(np.asarray(tv, np.float64).ravel())
        used.append(view)
        sample = img if sample is None else sample
    if not used:
        return None
    U, _, Vt = np.linalg.svd(np.median(Rs, axis=0))  # the rotation nearest the element-wise median
    R = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    t = np.median(ts, axis=0)
    centres = np.array([-Ri.T @ ti for Ri, ti in zip(Rs, ts)])
    third = max(1, len(centres) // 3)
    drift_mm = np.linalg.norm(np.median(centres[:third], axis=0) - np.median(centres[-third:], axis=0))
    stray_mm = np.linalg.norm(centres + R.T @ t, axis=1).max()
    stray_deg = max(np.degrees(np.arccos(np.clip((np.trace(Ri.T @ R) - 1) / 2, -1, 1))) for Ri in Rs)
    return dict(R=R, t=t, used=used, drift_mm=float(drift_mm), stray_mm=float(stray_mm), stray_deg=float(stray_deg),
                size=size, sample=sample)


def moved(times, per):
    """For a clip that fails the reprojection gate: when the picture moved, and its longest steady
    stretch, as the --start and --end that keep only that stretch."""
    ok = per <= POSE_PX
    if not ok.any():
        return ("  No frame fits the clip's own position: nothing held still, or the lens calibration belongs "
                "to another camera or mode.")
    runs, i = [], 0  # (first frame, last frame, steady?) of each stretch of frames that agree
    while i < len(ok):
        j = i
        while j + 1 < len(ok) and ok[j + 1] == ok[i]:
            j += 1
        runs.append((i, j, bool(ok[i])))
        i = j + 1
    shaky = ", ".join(f"{times[i]:.1f} to {times[j]:.1f} s" for i, j, steady in runs if not steady)
    i, j, _ = max((r for r in runs if r[2]), key=lambda r: times[r[1]] - times[r[0]])
    return (f"  The picture moved in {int((~ok).sum())} of {len(ok)} frames: {shaky}.\n"
            f"  The longest steady stretch runs from {times[i]:.1f} to {times[j]:.1f} s. To use only that, run again with "
            f"--start {np.floor(times[i] * 100) / 100:.2f} --end {np.ceil(times[j] * 100) / 100:.2f}")


def window_frames(ext, n=30):
    """The first `n` frames of an extrinsics result's clip inside the stretch its pose came from."""
    lo, hi = ext.get("window_s") or (SKIP_START_S, None)
    inside = (img for t, img in frames(ext["source"]) if t >= lo and (hi is None or t <= hi))
    return [img for _, img in zip(range(n), inside)]


def cmd_extrinsics(a):
    """Where one camera is: its pose relative to the board, from a recording in which neither moves."""
    cal = json.loads(Path(a.intrinsics).read_text())
    model = cal["selected"]
    K, D = np.array(cal["models"][model]["K"]), np.array(cal["models"][model]["dist"])
    src = Path(a.src)
    video = is_video(src)
    start = a.start if a.start is not None else SKIP_START_S if video else 0.0
    end = a.end if a.end is not None else np.inf
    print(f"Locating the {a.board} board in {src} ...")
    pose = board_pose(frames(src, VIDEO_FPS if video else None), a.board, model, K, D, start, end)
    if pose is None:
        sys.exit("The board was not found in any frame: all of it in view, sharp, and every other board covered?")
    if list(pose["size"]) != cal["image_size"]:
        sys.exit(f"{src} is {pose['size'][0]}x{pose['size'][1]}, the lens calibration is "
                 f"{cal['image_size'][0]}x{cal['image_size'][1]}: one calibration per camera mode.")
    R, t, used = pose["R"], pose["t"], pose["used"]
    rvec = cv2.Rodrigues(R)[0]
    sq = [((project(model, u["obj"], rvec, t, K, D) - u["img"]) ** 2).sum(1) for u in used]
    rms = float(np.sqrt(np.concatenate(sq).mean()))
    position = -R.T @ t
    distance = float(np.linalg.norm(R @ charuco(a.board)[0].getChessboardCorners().mean(0) + t))
    tilt = float(np.degrees(np.arccos(abs(R[2, 2]))))

    print(f"  {len(used)} frames with the board. The camera is {distance:.0f} mm from the board centre and looks "
          f"at it {tilt:.1f} deg off straight-on.")
    print(f"  camera position in board coordinates (mm): x {position[0]:.1f}, y {position[1]:.1f}, z {position[2]:.1f}")
    gates = [("frames with the board >= 5", len(used) >= 5, f"{len(used)}"),
             ("reprojection RMS <= 0.5 px (the lens calibration fits this recording)", rms <= POSE_PX, f"{rms:.3f} px"),
             ("camera and board still: drift from start to end <= 1 mm", pose["drift_mm"] <= 1.0,
              f"{pose['drift_mm']:.2f} mm (single frames stray up to {pose['stray_mm']:.2f} mm, {pose['stray_deg']:.3f} deg)")]
    print("\n  Gates:")
    for text, passed, value in gates:
        print(f"    [{'PASS' if passed else 'FAIL'}] {text}: {value}")
    if rms > POSE_PX:
        print(moved([u["t"] for u in used], np.array([np.sqrt(s.mean()) for s in sq])))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = HERE / "calib" / f"{a.name}_extrinsics_{stamp}"
    out.mkdir(parents=True)
    report = dict(camera=a.name, created=stamp, source=str(src.resolve()), intrinsics=str(Path(a.intrinsics).resolve()),
                  model=model, image_size=list(pose["size"]), opencv=cv2.__version__,
                  board=dict(name=a.board, dictionary="DICT_4X4_50", **BOARDS[a.board]), frames=len(used),
                  window_s=[start, None if end == np.inf else end],
                  convention="X_camera = R @ X_board + t_mm; board x and y lie in the board, mm from its corner",
                  R=R.tolist(), rvec=rvec.ravel().tolist(), t_mm=t.tolist(), camera_position_mm=position.tolist(),
                  distance_mm=distance, tilt_deg=tilt, reproj_rms_px=rms, drift_mm=pose["drift_mm"],
                  stray_mm=pose["stray_mm"], stray_deg=pose["stray_deg"],
                  gates={text: bool(passed) for text, passed, _ in gates})
    (out / "extrinsics.json").write_text(json.dumps(report, indent=2, default=float))
    vis = pose["sample"].copy()
    ends = np.float32([[0, 0, 0], [60, 0, 0], [0, 60, 0], [0, 0, 60 * np.sign(position[2])]])  # z drawn toward the camera
    o, x, y, z = project(model, ends, rvec, t, K, D).astype(int)
    for end, color in ((x, (0, 0, 255)), (y, (0, 255, 0)), (z, (255, 0, 0))):
        cv2.line(vis, tuple(o.tolist()), tuple(end.tolist()), color, max(2, vis.shape[1] // 400))
    cv2.imwrite(str(out / "axes.jpg"), vis)
    print(f"\n  Saved {out}/extrinsics.json, axes.jpg (board x red, y green, up blue)")


def cmd_detect(a):
    board, det = charuco(a.board)
    board_ids = set(board.getIds().ravel().tolist())
    base = set(BASE_IDS) if a.board == "main" else set()
    n_corners = len(board.getChessboardCorners())
    src = Path(a.src)
    out = Path(a.out) if a.out else HERE / "out" / "detect" / f"{src.resolve().parent.name}_{src.stem}"
    out.mkdir(parents=True, exist_ok=True)
    video = is_video(src)
    rows = []
    for t, img in frames(a.src, 1 / a.every if video else None):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        cc, ci, mc, mi = det.detectBoard(gray)
        ids = [] if mi is None else mi.ravel().tolist()
        side = {i: float(np.linalg.norm(c.reshape(4, 2) - np.roll(c.reshape(4, 2), -1, 0), axis=1).min())
                for c, i in zip(mc, ids) if i in base}
        row = dict(t=t, corners=0 if ci is None else len(ci), board_markers=len(set(ids) & board_ids),
                   base=sorted(side), base_px=side, dup=sorted({i for i in ids if ids.count(i) > 1}),
                   unexpected=sorted(set(ids) - board_ids - base))
        rows.append(row)
        line = (f"t={t:6.1f}s  {img.shape[1]}x{img.shape[0]}  board corners {row['corners']:2d}/{n_corners}  "
                f"board markers {row['board_markers']:2d}/{len(board_ids)}")
        if base:
            line += "  base " + (" ".join(f"{i}:{side[i]:.0f}px" for i in sorted(side)) or "none")
        if row["dup"]:
            line += f"  DUPLICATE ids {row['dup']}"
        if row["unexpected"]:
            line += f"  unexpected ids {row['unexpected']}"
        print(line)
        vis = img.copy()
        if ids:
            aruco.drawDetectedMarkers(vis, mc, mi)
        if ci is not None:
            aruco.drawDetectedCornersCharuco(vis, cc.reshape(-1, 1, 2), ci.reshape(-1, 1), (0, 0, 255))
        fs = img.shape[1] / 1600
        cv2.rectangle(vis, (0, 0), (img.shape[1], int(60 * fs)), (0, 0, 0), -1)
        cv2.putText(vis, line, (10, int(42 * fs)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * fs, (255, 255, 255), max(1, int(2 * fs)))
        cv2.imwrite(str(out / f"t{t:07.1f}s.jpg"), vis)
    if not rows:
        sys.exit(f"No frames read from {a.src}")

    n = len(rows)
    corners = [r["corners"] for r in rows]
    print(f"\n{n} frames. Board corners min {min(corners)} / median {int(np.median(corners))} / max {max(corners)} of {n_corners}")
    for i in sorted(base):
        seen = [r["base_px"][i] for r in rows if i in r["base_px"]]
        size_txt = f", smallest side {min(seen):.0f} px" if seen else ""
        print(f"  marker {i}: {len(seen)}/{n} frames ({100 * len(seen) / n:.0f}%){size_txt}")
    if any(r["dup"] for r in rows):
        print("  DUPLICATE ids seen: both ChArUco boards (they share ids 0-16) or a false detection is in view.")
    if any(r["unexpected"] for r in rows):
        print(f"  Unexpected ids {sorted({i for r in rows for i in r['unexpected']})}: other board in view or false positives.")
    print(f"Annotated frames: {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect", help="what does this camera see? (board corners + base markers)")
    d.add_argument("src", help="video, image or folder of images")
    d.add_argument("--board", choices=BOARDS, default="main")
    d.add_argument("--every", type=float, default=1.0, help="seconds between analysed video frames")
    d.add_argument("--out", help="folder for annotated frames (default out/detect/<name>)")
    c = sub.add_parser("intrinsics", help="calibrate one camera mode from a board video or image folder")
    c.add_argument("src", nargs="+", help="one or more videos of the same camera mode, or a folder of images")
    c.add_argument("--name", required=True, help="camera name, e.g. action_cam, phone, orbbec_rgb")
    c.add_argument("--settings", default="", help="free text: resolution, fps, FOV mode, stabilisation, focus")
    c.add_argument("--board", choices=BOARDS, default="main")
    c.add_argument("--models", default=",".join(MODELS), help="comma list of: " + ", ".join(MODELS))
    c.add_argument("--still-speed", type=float, default=STILL_SPEED,
                   help="fastest the corners may move in a view, in image diagonals per second (default %(default)s); "
                        "raise it for a take whose poses were not held still")
    e = sub.add_parser("extrinsics", help="where is this camera? (pose relative to the board, from a still recording)")
    e.add_argument("src", help="video, image or folder of images in which camera and board both stay still")
    e.add_argument("--intrinsics", required=True, help="intrinsics.json of this camera mode")
    e.add_argument("--name", required=True, help="camera name, e.g. action_cam, phone, orbbec_rgb")
    e.add_argument("--board", choices=BOARDS, default="main")
    e.add_argument("--start", type=float, help="first second of the clip to use (default: 1.0 for a video)")
    e.add_argument("--end", type=float, help="last second of the clip to use (default: to its end)")
    a = p.parse_args()
    {"detect": cmd_detect, "intrinsics": cmd_intrinsics, "extrinsics": cmd_extrinsics}[a.cmd](a)


if __name__ == "__main__":
    main()
