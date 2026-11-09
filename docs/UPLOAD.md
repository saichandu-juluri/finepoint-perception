# Upload and sync by hand: the contract

What the app on the lab computer, the server (docs/SERVER.md) and S3 agree on. Every part of the code follows the
shapes below: change them here first, then in code.

## What the lab computer does

1. New project: browse to the session folder, name the cameras, give the board.
2. Start: two lanes run side by side.
   - Upload lane: `python -m camrig.upload <project.toml>` sends every file to S3 in pieces and can stop at any moment.
   - Sync lane: the steps `cameras`, `scan` and `clock` (`python -m camrig.run <project.toml>`).
3. Sync by hand in Multi-view for any video the clock could not place (no sound, or no match), then the clock reruns.
4. The upload lane sends the depth videos, the sync files and the manifest as soon as they exist, and again after any
   change.

No original is ever cut, trimmed, re-encoded or rewritten. Sync lives in JSON beside the videos.

## S3 layout

One private bucket. Every key of a lab starts `labs/<lab>/`.

```
labs/<lab>/sessions/<session>/
  originals/<camera>/<file name>              camera videos and .bag files, byte for byte
  originals/lens/<camera>/<file name>         lens videos from the project's lens folder, if any
  derived/<camera>/<recording>/<file name>    orbbec_colour.mp4, orbbec_depth.mp4, orbbec_frames.csv
  sync/project.toml
  sync/lens/<camera>.json                     each camera's lens file, if the project names one
  sync/board.csv                              work/scan/board.csv
  sync/cameras.json  sync/clock.json          work/cameras/cameras.json, work/clock/clock.json
  sync/decisions.json
  sync/checks/<step>.json                     work/<step>/check.json for cameras, scan, clock
  manifest.json
```

- `<lab>`: lowercase letters, digits and `-`. It comes from the signed-in person's lab, never from the client.
- `<session>`: the session folder's name, lowercase letters, digits, `_` and `-` (the project id).
- `<camera>`, `<recording>`, `<file name>`: as on disk. Keys always use `/`.
- Content type: `.mov .mp4 .m4v` → `video/mp4`; `.json` → `application/json`; `.csv` → `text/csv`;
  `.toml` → `text/plain`; anything else (`.bag`) → `application/octet-stream`. It is a label stored with the object; no
  byte changes.
- Object metadata set at the start of every upload: `sha256` (hex, the whole file), `kind`
  (`original | derived | sync | manifest`), `session`.

## Cloud API

JSON over HTTPS: the FinePoint Perception server (docs/SERVER.md), or the local stand-in on `127.0.0.1`. Every request
carries `Authorization: Bearer <app token>` from a sign-in. The server keeps only SHA-256 hashes of tokens, maps a token
to its person and lab, and refuses any key outside `labs/<lab>/`. Errors are an HTTP status with
`{"error": "<plain sentence>"}`: 400 bad input, 401 unknown token, 403 a key outside the lab, 404 no such upload (it was
cancelled or cleaned up: start the file again).

`path` below is the key below `labs/<lab>/sessions/<session>/`, for example `originals/cam1/cam1_part01.MOV`.

| Call | Body | Answer |
| --- | --- | --- |
| `POST /uploads/start` | `{session, path, size, sha256, kind, content_type}` | `{upload_id, key}`, or `{done: true, key}` when an object with this `sha256` is already there |
| `POST /uploads/links` | `{key, upload_id, parts: [{n, sha256_b64}]}` (at most 32) | `{links: [{n, url, headers, expires_in}]}` |
| `POST /uploads/parts` | `{key, upload_id}` | `{parts: [{n, size, sha256_b64, etag}]}`, every part S3 holds |
| `POST /uploads/complete` | `{key, upload_id, size, parts: [{n, etag, sha256_b64}]}` | `{key, size}` after the server checked the joined object's size |
| `POST /uploads/abort` | `{key, upload_id}` | `{}` |
| `GET /sessions/<session>/files` | | `{files: [{path, size, sha256, kind, modified}], uploading: [{path, upload_id, started}]}` |
| `GET /health` | | `{ok: true, lab}` |

- A piece goes up as `PUT <url>` with exactly the piece's bytes and every header in `headers` (it holds
  `x-amz-checksum-sha256`, the piece's SHA-256 in base64, which S3 checks). The answer is 200 with an `ETag` header.
- A link answers 403 once it has expired: ask `/uploads/links` again.
- Parts are numbered from 1. Every part but the last is the file's part size, at least 5 MiB: the uploader's part size,
  raised to the next whole MiB when the file would otherwise need more than 10,000 parts.
- `/uploads/start` is idempotent per file: same `sha256` and size already stored → `done`. A changed file gets a new
  upload, and the bucket's versioning keeps the old version, billed as storage.
- `/uploads/complete` can take minutes for a big file: the server waits for S3 as long as it takes, and the uploader
  waits 330 s for that call (120 s for every other call and piece).

## Lab-side files, in the session folder

All under `upload/`. Written by the uploader only; the app reads them and creates `pause.request`.

- `upload/state.json`, schema `camrig.upload/1`:
  `{schema, lab, session, part_size, files: {<path>: {source, size, mtime_ns, sha256, kind, content_type, upload_id, parts_total, status, error}}}`.
  `status` is `waiting | hashing | uploading | done | failed`. It is a cache: on every start the uploader asks
  `/uploads/parts` which parts S3 holds and trusts that answer.
- `upload/progress.json`, schema `camrig.upload.progress/1`, rewritten about once a second:
  `{schema, pid, state, started, updated, bytes_total, bytes_done, rate_bps, eta_s, error, files: [{path, kind, size, bytes_done, parts_done, parts_total, status, error}]}`.
  `state` is `running | pausing | paused | done | failed | waiting_for_cloud`.
- `upload/pause.request`: present means "stop after the parts in flight". The uploader deletes it when it has paused.
  Killing the uploader is just as safe; the next start resumes.
- `upload/lock.os`: the running uploader holds an OS lock on it (flock on macOS, msvcrt on Windows) for its whole life;
  the OS frees it however the process ends, and the file is never deleted. `upload/wait.os`: the same for the one
  uploader waiting to follow it. `upload/lock`: the running uploader's pid only, removed on a clean exit.
- `upload/manifest.json`: the manifest as last uploaded.
- `upload/log.txt`: one line per event, appended.
- progress.json says `done` one pass before the uploader exits: that last pass looks once more for changed files.

## The manifest

`manifest.json`, schema `camrig.manifest/1`, uploaded last, again whenever any other file changed:
`{schema, lab, session, made, files: [{path, kind, size, sha256, camera, recording}]}` (`camera` and `recording` where
they apply).

## Which files go up, and when

The uploader works through this list in order and re-reads it on every pass:

1. originals: every camera's files (`Camera.videos()`), then lens videos under the project's lens folder.
2. derived: for each depth recording listed in `work/cameras/cameras.json`, its `orbbec_colour.mp4`, `orbbec_depth.mp4`
   and `orbbec_frames.csv`, found where the cameras step puts them (the camera's `[camera.exports]` folder for that
   recording, else `work/cameras/<camera>/<recording>/`), never through the absolute paths in cameras.json, so a moved
   or copied session sends its own. Only once the cameras step's check.json exists and is not `fail`; a listed export
   that is missing fails the run.
3. sync: `project.toml`, lens files, `work/scan/board.csv`, `work/cameras/cameras.json`, `work/clock/clock.json`,
   `decisions.json`, and the check.json of cameras, scan and clock, each when it exists.
4. the manifest, when everything before it is `done`.

A file whose size or modification time changed since its upload started is cancelled (`/uploads/abort`) and started
again. Sync files are compared by `sha256` on every pass.

Who starts the uploader: the uploader exits once everything on its list is `done`. The app starts it again, when it is
signed in, after any run of the session's steps ends and after every Save in Multi-view, so the depth videos and every
change by hand go up without the person doing anything. When the app starts, it also starts the uploader for every
session whose progress.json says `running` and that has no `pause.request`, without looking at the pid (after a restart
an old pid can belong to another program).

Starts can therefore overlap, and the uploader sorts them out itself: one runs (holding `lock.os`), at most one waits
(holding `wait.os`, then making a full pass after the runner), and any further start prints one line and exits 0. A
pause the runner ended with also stops the waiter. A stop by the system (SIGTERM: the demo's Ctrl+C, a shutdown) runs
the cleanup, exits 143 and leaves progress.json `running`, so the app resumes it; only the person's Pause leaves
`paused`. The demo and the app's tests find uploaders by their command line, not through `upload/lock`.

A project file that `camrig.project` refuses, or a session folder name outside `[a-z0-9_-]`, exits 2 and writes
progress.json `failed` with the plain reason.

## The uploader's rules

- Part size: 250 MiB by default (`--part-mb`); 3 parts in flight (`--parallel`).
- Each part's SHA-256 is computed just before its link is asked for, because the link is signed with it.
- A failed piece is retried after 30 s, doubling to at most 10 minutes; it never gives up. `--retry-base` shortens the
  first wait in tests. A 403 on a piece asks for a new link at once.
- The computer is kept awake while pieces are in flight: `caffeinate -i -w <pid>` on macOS,
  `SetThreadExecutionState` on Windows.
- Cloud settings: the address from `CAMRIG_CLOUD_URL`, else `~/.camrig/cloud.json` `url`; the token from
  `CAMRIG_CLOUD_KEY`, else the OS password safe (docs/SERVER.md, "The lab computer"). With none, the uploader exits
  with `state: waiting_for_cloud` and a plain message.
- Standard library, plus `keyring` to read the token from the password safe.

## Sync by hand

- cameras.json is unchanged: every action part already has `sound`.
- The clock judges sound per part. A camera takes part in sound matching with its parts that have sound.
- A part without sound is placed `chained` (after or before a placed neighbour of the same camera, with the usual split
  gap) with `flag` starting `no sound`, so the person sees a first guess. Without a placed neighbour it is left out and
  check.json lists it with `where: {camera, part}`.
- Entries in decisions.json `clock.user.manual` place any part (`source: manual`), also when fewer than two cameras have
  sound or the automatic matching found nothing.
- clock.json is written whenever any part is placed. A reference camera that nothing placed keeps the step `fail` until
  it is set by hand, with the earliest placed action part standing in for clock 0 meanwhile.
- No part of any camera has sound, and the reference is an action camera: its first part stands at clock 0
  (`source: reference`), its other parts are chained first guesses flagged `no sound`, every other camera waits for
  sync by hand, and the step ends `flag`, never `pass`. A depth reference stands alone on its own clock.
- Multi-view lists every part that needs the person: left out of clock.json, or flagged `no sound`. Moves: 1 frame,
  10 ms, 1 s, 10 s, a typed time, a whole camera at once, and "mark this moment": the same moment clicked in a placed
  part and in the part being placed.

## The local stand-in for the cloud

`pixi run --frozen -e cloud cloud-dev` serves the API and a stand-in for S3 at `http://127.0.0.1:8787`. It runs the
same server code as the real server (cloud/server.py), with a local store instead of S3.

- Data: `--dir`, else `CAMRIG_CLOUD_DEV_DIR`, else `.cloud_dev` in the repository folder.
- It keeps the bytes of objects up to 64 MiB. Bigger objects keep their size, part checksums and fingerprint only, so
  the disk need not hold a second copy of every session.
- App token `perception-dev-key` belongs to `lab@example.com` of lab `dev` (docs/SERVER.md, "The local stand-in").
- Faults for tests, `CAMRIG_CLOUD_DEV_FAULTS`: `put-500-every=N` fails every Nth piece with 500,
  `link-ttl-s=S` makes links expire after S seconds.
- It is as strict as S3, so what passes here passes there. It refuses: a piece whose bytes do not match its signed
  `x-amz-checksum-sha256`; an expired or altered link (403); an unknown upload ID (404); at complete, any part but the
  last under 5 MiB, more than 10,000 parts, a missing part, or an ETag or checksum that does not match what it stored.
- `pixi run --frozen demo` starts the stand-in and the app together, already signed in. The token stays in the app's
  server and is never sent to the browser.

## Terraform

`cloud/terraform/` builds the server of docs/SERVER.md. `cloud/terraform/README.md` has the resources, the cost and the
commands.
