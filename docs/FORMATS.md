# Files and formats

What the app reads and writes in a session folder. Change this page first when a format must change.

## The session folder

```
my_session/
  project.toml        what the person says about the session (New project writes it, or they do)
  decisions.json      the clock step's own choices, with its evidence, and the person's overrides
  lens/               optional: lens videos in lens/<camera>/, sent with the upload
  work/cameras/       what each camera recorded: cameras.json, and the depth camera's exports
  work/scan/          where the board is seen: board.csv
  work/clock/         all cameras on one clock: clock.json
  transcripts/        transcripts the person edited in the app
  upload/             the uploader's files (docs/UPLOAD.md)
```

Each `work/<step>/` also holds that step's `check.json` and `log.txt`.

Recordings are never copied, moved or changed. Every path inside a results file is absolute.
Files whose names start with a dot, and `Log/` folders, are caches and logs. The depth camera's library writes the
`Log/` folders. A file ending in `.part` is still being written. Readers skip all of these.

## project.toml

Read by `camrig/project.py`, which refuses unknown keys, missing folders and cameras with no files.
It refuses an unknown name inside `[limits]` too, so a misspelt limit stops the run with a message.

```toml
name = "cleft workshop"
lens_folder = "lens"                 # optional (default lens): lens videos in lens/<camera>/, sent with the upload

[board]
squares = [5, 7]                     # columns, rows
square_mm = 20.0                     # measured on the screen or print
marker_mm = 14.0
dictionary = "DICT_4X4_50"           # optional

[[camera]]
name = "cam1"                        # the label on the camera body
kind = "action"                      # action, depth or phone
folder = "/path/to/videos/cam1"
files = "*.MOV"                      # optional pattern inside the folder (default: every video, or every .bag for depth)
lens = "/path/to/lens.json"          # optional; sent with the upload as sync/lens/<camera>.json
station = "doctor"                   # optional; copied into cameras.json
reference = true                     # optional; default: the first depth camera, else the first camera

[camera.exports]                     # optional, depth camera only: exports made before, per recording (.bag stem)
orbbec_main = "/path/with/orbbec_colour.mp4/and/orbbec_frames.csv"

[clock]
existing = "/path/to/sync.json"      # optional: keep a clock made before, unchanged

[limits]                             # optional; the values shown are the defaults
"split gap min s" = -0.05            # clock: the shortest normal gap between two parts of one camera, s
"split gap max s" = 5.0              # clock: the longest normal gap, s
"depth drift max ppm" = 100.0        # clock: the largest believable drift between a sound camera and the depth camera
```

- New project writes `name`, `[board]` (`squares`, `square_mm`, `marker_mm`) and one `[[camera]]` per camera folder
  (`name`, `kind`, `folder`, and `files = "*.bag"` for the depth camera).
- `[limits]` names with spaces need the quotes. Values must be numbers. The cameras and scan steps read no `[limits]`.
- The clock step holds its limits in its settings, so a changed limit reruns it.
- The reader also accepts `[board] thickness_mm`, `[positions]`, `[transcript]`, `[models]` and further `[limits]`
  names. No step of this app reads them.

## check.json, in every work/<step>/

Written by `camrig/record.py`. Schema `camrig.check/1`. An example from an automatic clock:

```json
{"schema": "camrig.check/1", "step": "clock", "status": "flag",
 "items": [{"name": "cam1/part07: two sound matches", "status": "flag", "value": 1, "limit": ">= 2", "unit": "matches",
            "message": "1 other parts heard the same sound", "fix": "open Multi-view at cam1 part07 and check it; slide it by hand if it is off",
            "where": {"camera": "cam1", "part": "part07"}}],
 "outputs": {"clock": "/abs/my_session/work/clock/clock.json", "log": "/abs/my_session/work/clock/clock_log.txt"},
 "numbers": {"sound_matches": 56, "sound_parts": 30, "orbbec_windows": 19, "parts": 32, "flagged": 2, "waited_for_lock_s": 0.0, "step_s": 0.2},
 "version": "", "settings_hash": "adada85826b2db80", "inputs": {"/abs/my_session/work/cameras/cameras.json": "sha256 e24fef73..."},
 "started": "2026-11-02T00:09:30", "seconds": 0.3}
```

- `status` of a step is its worst item. Order from best to worst: `pass`, `not_tested`, `flag`, `fail`. A step with no
  items is `not_tested`.
- `pass`: checked and fine. `flag`: usable, but a person should look at the named place. `fail`: not usable, steps that
  need it do not run. `not_tested`: the check could not be made, for example a recording too short, or a step that
  waits for another.
- Every item a person may act on has `message` (what happened, plain words) and `fix` (what to do).
- `where` names the place with any of `camera`, `part`, `file`, `station`, `from_s`, `to_s` and `clock_s` (clock seconds).
- `outputs` maps a role to the absolute path of a file the step wrote. Each step's roles are listed with its files below.
- `numbers` holds small summary values for pages. Each step chooses its own.
- The runner (`camrig/run.py`) runs the steps in this order: cameras, scan, clock.
- The runner fills `settings_hash`, `inputs`, `started` and `seconds`. `version` stays empty.
- `settings_hash` covers the step's VERSION, its settings and the fingerprints of its code. A change in any of them
  reruns the step.
- `inputs` maps each input file to its fingerprint: size and modification time above 20 MB, else a sha256 of its content.
- `seconds` is working time. The wait for another heavy job (`numbers.waited_for_lock_s`, when a step reports it) is
  taken off.
- A step whose `settings_hash` and `inputs` did not change, and that did not fail, is skipped.
- The runner writes two items of its own. `waits` (`not_tested`): a step it needs has not finished, failed, or itself
  waited. `step ran` (`fail`): the step stopped; its `fix` names `log.txt`.

## decisions.json

Written by `camrig/decisions.py`. Schema `camrig.decisions/1`. One section per step, each
`{"auto": {...}, "evidence": {...}, "user": {...}}`. Only the clock step has a section. An example, shortened, with a
`user` entry added:

```json
{"schema": "camrig.decisions/1",
 "clock": {"auto": {"reference": "orbbec", "depth_link": ["cam1", "cam3"],
                    "rough_offset_s": {"orbbec": {"orbbec_main": 4.0, "orbbec_start": 3.0}},
                    "line": {"orbbec": {"offset_s": 4.203146758637685, "rate": 0.999991027372352}},
                    "parts": {"cam1/part01": {"start_s": -4.203184, "source": "sound"},
                              "orbbec/orbbec_start": {"start_s": 0.0, "source": "reference"}}},
           "evidence": {"depth_link": "19 of 19 windows on one line, drift -9.0 ppm, residual median 17.6 ms, worst 75.9 ms, cameras cam1, cam3",
                        "sound": "56 matches between 30 parts, residual median 0.4 ms, worst 2.4 ms",
                        "flagged": "cam1/part07, cam3/part08"},
           "user": {"manual": {"cam3/part05": {"start_s": 4016.977014, "file": "cam3_part05_20261002_032202.MOV", "note": "moved by hand in Multi-view"}}}}}
```

- A step rewrites its `auto` and `evidence` on every run and never touches `user`.
- A step saves only what it changed, onto the file as it is when it ends, so a person's change made while it ran stays.
  The step and the app both hold `.decisions.json.lock`, in the session folder, while they read and write the file. It
  lasts milliseconds.
- The `user` keys win over what the step finds. A change in them reruns the step.
- A clock kept with `[clock] existing` writes no section and reads no `user` keys.
- The app's Multi-view writes `clock.user.manual` when the person saves a part slid by hand. A person may edit any
  `user` key.

The clock section, automatic route only:
- `auto.reference`: the reference camera.
- `auto.depth_link`: the sound cameras whose picture motion placed the depth camera.
  It is a list when the project has one depth camera, else `{depth camera: [cameras]}`.
  It is `[]` when only the board placed the depth camera, and `{}` when no depth camera got a rough offset.
- `auto.rough_offset_s`: `{depth camera: {part key: s}}`, the offset found from the board seconds alone.
- `auto.line`: `{depth camera: {"offset_s", "rate"}}`. Sound seconds = offset_s + rate × the depth camera's device
  seconds since its first frame.
- `rough_offset_s` and `line` are on the step's own sound clock, not in clock.json's seconds. A reader does not need them.
- `auto.parts`: `{"camera/part": {"start_s", "source"}}`, as in clock.json. A part set by hand has source `manual`.
- `evidence`: `sound`, `flagged` (the flagged parts, or "none") and `depth_link`, all text.
  With several depth cameras, or when only the board placed one, the key is `depth_link <camera>`.
- `user.manual`: `{"camera/part": {"start_s": s, "file": video file name, "note": text}}`, or a bare number for
  `start_s`. The app writes `file`. When a part was added or taken away and the key now holds another file, the entry
  is flagged and not used. It sets that part's start in clock seconds. The part keeps its rate. A name that is no part
  of the session is flagged.

## work/cameras/cameras.json

Written by the cameras step (`camrig/pipeline/cameras.py`). Schema `camrig.cameras/1`. What each camera recorded, in
order. outputs role: `cameras`. An example, one part per camera shown:

```json
{"schema": "camrig.cameras/1",
 "cameras": [{"name": "cam1", "kind": "action", "has_sound": true, "station": "doctor", "lens": "/abs/lens.json",
              "parts": [{"file": "/abs/cam1_part01_20260929_123006.MOV", "frames": 75300, "fps": 60.0, "width": 1920, "height": 1080,
                         "seconds": 1255.0, "created": "2026-09-29T04:30:08Z", "key": "part01", "sound": true}]},
             {"name": "orbbec", "kind": "depth", "has_sound": false, "station": "doctor", "lens": "/abs/lens.json",
              "parts": [{"key": "orbbec_start", "bag": "/abs/orbbec_start.bag", "file": "/abs/orbbec_start/orbbec_colour.mp4",
                         "depth_video": "/abs/orbbec_start/orbbec_depth.mp4", "frames_csv": "/abs/orbbec_start/orbbec_frames.csv",
                         "frames": 8216, "fps": 15.0, "width": 1920, "height": 1080, "seconds": 547.941, "missing": 2}]}]}
```

- Cameras come in project order. A camera is left out, and its item fails, when no file can be read or its parts change
  video mode.
- `lens` is the lens path from project.toml, or null. `station` is null when not given.
- `has_sound` is true when every part has sound. A depth camera has none.
- Action and phone parts come in the camera's own creation-time order. `key` numbers them in that order, `part01` first.
- `created` is UTC, from the file's creation tag, else the file's birth time (or its modification time less its length).
- `created` comes from the camera's own clock, so only its order counts.
- `fps` is the video's own frame rate. For the depth camera it is the colour export's rate, which seeking needs.
- `seconds` is frames / fps for an action part. For a depth part it is the device time from its first frame to its last.
- `sound` says whether that part has a sound track.
- A depth part's `key` is its .bag stem. Depth parts come in the order of their first device time.
- A depth part's `frames` counts every colour row of `frames_csv`, fillers too. `missing` counts the fillers: frames the
  recording lost.
- `bag` is the recording. `file`, `depth_video` and `frames_csv` are its export.
- The export comes from `[camera.exports]`. A .bag with none is exported into `work/cameras/<camera>/<bag stem>/`, with
  `export_log.txt`.

The depth export item in check.json, `<camera>: <bag stem> export`:
- `pass` when the step exported the recording in this run.
- `pass` when an earlier run's export in `work/cameras/` is reused and its colour frames match `orbbec_frames.csv`.
  The item comes after the frames check, so it can fail. Delete that folder to export again.
- `fail` when a `[camera.exports]` folder lacks a file, or when the export stopped.
- An export named in `[camera.exports]` gets no export item. Its frames are still checked (`<camera>: <bag stem> readable`).
- Items `<camera>: file names agree with the camera's times` and `<camera>: one video mode` are `not_tested` for a
  camera with one part.

The frame table `orbbec_frames.csv` has one row per video frame of each stream, with these columns:
- `stream`: `colour` or `depth`. `frame`: the frame's index in that stream's video.
- `real`: 1, or 0 for a filler copied from the frame before.
- `device_us`: the depth camera's own clock. `system_us`: the computer's clock. Both in microseconds.

## work/scan/board.csv

Written by the scan step (`camrig/pipeline/scan.py`). outputs role: `board`. Columns: `camera, part, file, t, corners`.

- One row per keyframe, about one a second, where the board detector found at least one ChArUco corner. Other keyframes
  have no row.
- `part` is the part's key in cameras.json. Files made before scan VERSION 2 numbered action parts by file name, so
  match action rows to parts by `file`, as the clock step does.
- `t` is seconds into that file: the keyframe's number times the video's usual keyframe gap. It is not the frame's own
  time stamp.
- For the depth camera, a row is frame round(t × fps) of its colour export.
- `corners` is the number of corners found. The clock step counts 10 or more as seeing the board.

## work/clock/clock.json

Written by the clock step (`camrig/pipeline/clock.py`), read by `camrig/clock.py` (`Clock`). Schema `camrig.clock/1`.
outputs roles: `clock`, and `log` on the automatic route. An example from an automatic clock, one part per camera
shown:

```json
{"schema": "camrig.clock/1", "reference": "orbbec", "zero": "clock 0 is the first frame of orbbec; clock seconds run on orbbec's own clock",
 "cameras": {"cam1": {"kind": "action", "parts": [
                 {"key": "part01", "file": "/abs/cam1_part01_20260929_123006.MOV", "frames": 75300, "fps": 60.0,
                  "start_s": -4.203184, "rate": 1.0000089727081567, "source": "sound", "flag": null}]},
             "orbbec": {"kind": "depth", "parts": [
                 {"key": "orbbec_start", "file": "/abs/orbbec_start/orbbec_colour.mp4", "frames": 8216, "fps": 15.0,
                  "frames_csv": "/abs/orbbec_start/orbbec_frames.csv", "start_s": 0.0, "rate": 1.0, "source": "reference", "flag": null}]}}}
```

- Clock seconds of frame i of a part are `start_s + rate * t_i`. `t_i` is `i / fps` for a part without `frames_csv`.
- For a part with `frames_csv`, `t_i` is the `device_us` of colour row i less that of colour row 0, in seconds.
- Always use `rate`. On an automatic clock it need not be 1: a camera's own clock can run a few ppm off the reference's.
- Rows of `frames_csv` with `real` 0 are fillers. They are never shown.
- The frame shown at clock second s: for a part with `frames_csv`, the nearest real frame within 0.1 s.
  For another part, frame round((s - start_s) / rate × fps), while the part runs.
- `flag` is null, or the reason the clock step is not sure of that part.
- A part the step could not place is left out. A camera with no part placed is flagged in check.json: item
  `<camera> on the clock`, or `<camera>/<part>: no sound` for each part, or `reference camera on the clock` for the
  reference camera.
- `kind` is the camera's kind from cameras.json.
- `source` on the automatic route is one of these:
  `sound`; `chained` (placed after the part before it, or before the part after it, with the usual split gap);
  `motion` (a depth camera that is not the reference); `reference`; `manual` (from decisions.json).
- `zero` says in words what clock 0 is. It is free text, not for parsing.

### Sound per part, and sync by hand

- Sound is judged per part (cameras.json `sound`). A camera takes part in sound matching with its parts that have sound.
- A part without sound is `chained` to a placed part of its camera, also on the reference camera. Its `flag` starts
  `no sound`: a first guess for the person to check. Its check.json item `<camera>/<part>: no sound` is `flag`, with
  `where: {camera, part}`.
- With no placed part of its camera beside it, a part without sound is left out. The same item says "left out of
  clock.json".
- A part without sound never places the depth camera: the board seconds and motion windows use parts with sound only.
- decisions.json `clock.user.manual` places any part, also when nothing automatic placed it.
- Fewer than two cameras with sound: item `cameras with sound` is `not_tested`, not `fail`. The step goes on.
  With one sound camera, its first part with sound anchors the sound and its other parts are chained, as when no match
  is found.
- No part of any camera has sound, and the reference camera is an action camera: its first part stands at clock 0
  (`source` `reference`, `flag` null, item `<camera>/<part>: no sound` `not_tested`). Its other parts are `chained`,
  flagged `no sound`. Every other camera's parts are left out and listed, each with its `no sound` item, until manual
  entries place them. The step ends `flag`, never `pass`.
- A depth reference camera that nothing lines up (`<depth> rough offset` `not_tested` when no part was placed by sound)
  is placed alone, on its own device clock, with item `<depth> linked to the other cameras` `not_tested`. Every other
  part needs a manual entry. A camera that had parts placed by sound, now left out, gets item `<camera> on the clock`
  `flag`.
- When the board lines nothing up with a depth reference camera, its `rough offset` is `flag`, not `fail`: it is still
  placed alone.
- So a session placed fully by hand ends `not_tested` (or `flag` as above), never `fail`. The runner only holds back
  the steps after a `fail`.
- A `normal gap at its split` item beside a `chained` part (not set by hand) is `not_tested`: that start is a guess.
- The step still fails when no part of the reference camera is placed, by the clock or by hand (item
  `reference camera on the clock`). When an action reference camera has no part placed, clock.json is still written
  with the other parts, so the reference can be slid into place in Multi-view. Clock 0 is then the first frame of the
  earliest placed part; `zero` names it.

### Clock zero

- An automatic clock's 0 is the reference camera's first frame as the clock placed it: the first frame of its earliest
  recording. Manual entries never move 0. A reference part placed `chained` and then moved by hand can start before or
  after 0. When no part of an action reference camera was placed automatically, 0 is the earliest placed part's first
  frame.
- Its clock seconds run on that camera's own clock: for a depth camera, its device time.
- An imported clock (`[clock] existing`) keeps the old file's 0 and seconds, so one moment can have two clock seconds.
- To compare results made on two clocks, line up a frame both clocks hold, for example the depth camera's first frame.
  Its clock seconds on each clock give the shift. Never fit an offset between two results: it would hide the very error
  being measured.
- Match parts across two clocks by `file`, never by `key`: an imported clock may key its parts differently.
- Clock seconds belong to the clock they were made on. This holds for every `clock_s` and `start_s`, and for the clock
  seconds in decisions.json.

### The imported clock

- `[clock] existing` converts an old sync.json with `from_sync_json`, unchanged: the same clock seconds and the same
  frames.
- It holds only the cameras the old file had. The others are flagged.
- Every camera but the reference has `kind` `action`.
- `source` is the old file's word, or `existing` when it had none. Depth parts say `reference`.
- `flag` is always null. `fps` may be a whole number (60).

### How the depth camera gets its rate

- The clock step fits each sound camera's motion windows against the depth camera with a free line (offset and drift).
- A camera's drift counts as not measurable when it has fewer than 5 good windows, or when they span too little time.
  That is when the limit (`"depth drift max ppm"`, default 100) over the span is under 3 times the windows' scatter.
- The scatter is the residual median of the better of two lines: the free line, or the line with the rate held at 1.
- The pool is the kept windows of every camera whose drift is measured and within the limit.
- When the pool has fewer than 3 windows, and the short cameras give 3 or more, those give the offset alone. The rate is
  held at 1.
- So beside a long camera within the limit, a short camera is left out, not held.
  A long camera outside the limit adds nothing to the pool, so it does not stop the hold.
- When neither gives 3 windows, the board alone places the depth camera (`exact offset` flags).

Its items in check.json:
- `<depth> rough offset`: `not_tested` when the depth camera has one recording, so there are no recordings to agree.
- `<depth> exact offset`: `pass` with the rate held at 1. Its message gives the windows kept, the offset and the
  residuals. `flag` when no camera's motion agreed, so the board alone placed the depth camera.
- `<depth> drift`: written only when the rate is held. Its message says why for each camera, and how far a drift within
  the limit could move the clock over the depth camera's whole recording. `not_tested` when that is under one
  depth-camera frame, else `flag`.
- `<depth> exact offset and drift`: `pass` or `flag` against the drift limit, when the drift was measured.
- `<part>: matches agree within one frame`: `not_tested` when none of the part's matches forms a loop with others.
  Then each match alone joins its two parts, so its residual is always 0. This is decided match by match.
- With the rate held, decisions.json `clock.auto.line` has `rate` 1.0. `evidence.depth_link` ends with "drift held at
  1, not measured".

### Other files in work/clock/

- `clock_log.txt`: what the automatic route did, in plain lines.
- `sound_matches.json` and `windows_<camera>.json`: caches, `{"key", "value"}`. A rerun that changes only a manual
  entry reads them back.

## upload/

Written by the uploader, read by the app. docs/UPLOAD.md, "Lab-side files", lists every file and its schema.

## transcripts/

The single-camera player's transcript editor:
- opens `transcripts/<camera>/<video stem>.srt`, else `work/transcript/<camera>/<video stem>.srt` when the session
  holds one. No step of this app writes `work/transcript/`.
- Save writes `transcripts/<camera>/<video stem>.srt` and a reading version, `<video stem>.txt`, beside it. Nothing is
  written beside a video.
- `transcripts/<camera>/.transcript_history/` keeps the version a Save replaced (`<stem>.<time>.srt`, with its `.txt`),
  a copy of each saved one (`<stem>.<time>.saved.srt`), and edits thrown away unsaved (`<stem>.<time>.unsaved.srt`).
  `<time>` is the computer's local time, `YYYYMMDD-HHMMSS-mmm`.
- The `.srt` is standard SRT, times from the start of that video. The `.txt` joins cues into lines that read
  `[m:ss] text`, or `[m:ss] S1: text` when the cue starts with a speaker label.
