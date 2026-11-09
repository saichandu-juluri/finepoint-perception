# Windows: test and fix

FinePoint Perception has only run on Macs. This brief is for an agent on a Windows 10/11 x64 PC: make the doctor's flow
work there, prove it on the real PC, and leave a one-line install like the Mac's.

## Done means

1. One line pasted into PowerShell installs the app, and the same line updates it. Write `install.ps1` for it:
   `powershell -ExecutionPolicy Bypass -c "irm -useb https://raw.githubusercontent.com/saichandu-juluri/finepoint-perception/main/install.ps1 | iex"`
2. A Start-menu and desktop shortcut opens the app (icon: `finepoint-perception.ico`). The doctor never needs a terminal.
3. New project works from a recording folder, also on a USB drive or SD card (`E:\`).
4. Sign in, Upload, Pause, Resume. Close the app and reopen it: the upload carries on.
5. Run, then Open multi-view and a single view: the videos play.

## How

- `install.command` is the reference: what it checks, pins (pixi 0.83.0), stops, builds and opens. A fresh PC has no
  git; `pixi exec git` (conda-forge has git for win-64) avoids a Git install.
- Tests (`docs/DEVELOP.md`): test-core, test-clock, test-upload, test-hand-sync, test-cloud, `npm run check`,
  `npm run e2e`. The e2e assumes a Mac: the Chrome path, `HOME` (Python on Windows uses `USERPROFILE`), `:` in PATH,
  `bin/python`, `node_modules/.bin/next`, `sleep`. Make it run on Windows, and never touch the real
  `%USERPROFILE%\.camrig` or Credential Manager.
- Uploads go to the local stand-in (`cloud/dev_server.py`, or `pixi run --frozen demo`), never the live server.
- Also try a folder and a user name with a space and a non-ASCII letter (`José`).

## Suspects

Found by reading the code on a Mac. Confirm each on Windows and fix the real ones.

- Videos never play. `lib/format.ts` mediaUrl splits the path on `/`, the media route joins it back with `/`, and
  `server/camrig.ts` resolveMedia refuses any `\`. `components/NewProject.tsx` splits on `/` too (session name, labels).
- New project fails. `server/newproject.ts` tool() looks for `ffprobe` and `ffmpeg` without `.exe`. Its fallback
  folder `envs/core/bin` is `Library\bin` on Windows.
- Other drives are out of reach. browse() has no parent at `C:\` and no list of drives.
- Stop does nothing. `server/run.ts` uses `process.kill(-pid)`, which is POSIX only, and spawns without `windowsHide`.
- Text breaks. Python reads and writes cp1252 on Windows unless told otherwise: every `read_text()`, `write_text()`
  and `open()` without `encoding`, and `print` to a pipe. Node writes `project.toml` in UTF-8, and
  `camrig/project.py:114` reads it. `PYTHONUTF8=1` (pixi activation) fixes most, but not the `python -I` calls
  (use `-X utf8` there).
- The start file. `start-finepoint-perception.bat` builds without `-e core` and does not add
  `%USERPROFILE%\.pixi\bin` to PATH. `.gitattributes` now keeps it CRLF.
- Updates. A running server keeps `viewer\.next` and `node_modules` open, and an uploader or a step keeps the
  environment's files open. Stop every process that runs from the app's folder before installing.
- Pause and stop. On Windows, killing the uploader is TerminateProcess, so no SIGTERM handler runs. Check that the
  progress state stays right and that Resume works.

## Rules

- The Mac must keep working. Change shared code only where needed, and keep Windows-only code behind a platform check.
- The repo is public: no server address, emails, people's names or keys in code, docs or commits.
- Doctors read the README, the guide and the app's texts: plain words, one way to do each thing, and "the FinePoint
  team", never "your admin".
- To finish:
  - Give the README a Windows install and update section.
  - Give `docs/setup-guide/guide.html` its Windows parts (requirements, install, open next time, update), with
    screenshots from this PC.
  - Rebuild the PDF. `make_pdf.sh` is for the Mac, so run Chrome or Edge headless the same way.
  - Write in this file what was tested and what is still open.
  - Commit and push to main.

## What was tested (9 November 2026)

On a Windows 11 Home x64 PC (25H2, build 26200; English, ANSI code page 1252; Chrome and Edge; no pixi before).

### Done means

1. The one line. `install.ps1` ran as the line runs it (through `iex`, not as a file) for a new user profile named
   `José Q`, with no pixi and no git on PATH, its clone pointed at the commit under test: pixi 0.83.0 downloaded and
   checked against its SHA-256, git 2.56.0 through `pixi exec`, the clone, every package downloaded fresh into the
   profile's own cache, the build, the shortcut, and the app opened, in 174 s.
2. The shortcut runs `start-finepoint-perception.pyw` with the environment's `pythonw.exe` and shows
   `finepoint-perception.ico`. Clicked, it opens the app with no window; the app's output goes to
   `%LOCALAPPDATA%\FinePoint Perception\FinePoint Perception.log`.
3. New project from `E:\Grabación día 1` (E: was a `subst` drive of a folder): the drives row lists C: and E:, a click
   on E: opens it, the camera folders show with a picture from `toma ñ 1.mp4`, and the session name is the folder's.
4. Sign in to the local stand-in (`viewer/tests/slow_cloud.py`, 5 s a piece), Upload, Pause at 41%, Resume. Then every
   process from the app's folder was stopped at 88%, as a restart does, and progress.json stayed running; the shortcut
   opened the app again, and the upload carried on by itself to "Uploaded. Everything is in the cloud."
5. Run (cameras pass; scan flag, as the clips show no board; clock flag), then Multi-view: both cameras load and Play
   all plays both. The single view of `toma ñ 2.mp4` plays. No console errors.

### Tests

test-core, test-clock, test-upload, test-hand-sync, test-cloud, `npm run check` and `npm run e2e` (98 checks) pass on
this PC, and the Python ones in both environments. The e2e runs in Chrome with a temporary `USERPROFILE`, `TEMP` and
`TMP` and the file keyring: the real `%USERPROFILE%\.camrig` stayed absent and Credential Manager was never asked.

### The suspects

Each was confirmed on this PC against the code before the fixes.

- Videos never play: the media URL of `C:\rec\camA\clip1.mp4` got 400 Bad path. Now `mediaUrl` splits a Windows path
  at `\` and `/`, the media route builds `C:\...` or `\\server\share\...` again, and on Windows a path must lie in the
  session's folders by its names before the disk is asked at all: opening `\\host\share` would send the person's
  Windows sign-in to that host. New project took the whole path as the session name; the names now come from
  `fileName()`.
- New project fails: `tool("ffprobe")` found nothing (no `.exe`), so 503. Now `.exe`, and `Library\bin`.
- Other drives: no way up from `C:\`. Now a row of drives from Windows' own list (Python's `os.listdrives`, with
  `GetDriveTypeW`), which reads no drive. On Windows the folder browser and the picture answer only the app's own
  pages, for the same reason as the media route.
- Stop does nothing: `process.kill(-pid)` threw ESRCH and the runner went on. Now Cancel, a timeout and the server's
  exit end the runner's whole tree with `taskkill /T /F`. The runner runs with `windowsHide`, not detached: it gets a
  console with no window, which its steps and their ffmpeg share. Detached, it would get none, and each step would
  open a window.
- Text breaks: `project.toml` written by Node in UTF-8 and read with `python -I` turned `Grabación` into
  `GrabaciÃ³n`, so the folder "does not exist". Now `PYTHONUTF8=1` in pixi's activation on `win-64` (core and cloud),
  and `-X utf8` for the `-I` runs. `cloud/demo.py` asks PowerShell for UTF-8 when it lists the uploaders.
- The start file: pixi was not on PATH (the install leaves PATH alone), and `timeout` needs a keyboard, so the start
  with no window did not wait for the app. Now PATH has `%USERPROFILE%\.pixi\bin`, `ping` waits, and the app runs in
  the file's own console, which has no window when the shortcut starts it. `-e core` is named; pixi picked core anyway.
- Updates: `install.ps1` stops every process whose program is in the app's folder before it pulls and installs.
- Pause and stop: a stop is TerminateProcess, which runs no cleanup. progress.json keeps the last state it reported
  (running, so the app resumes it), Windows frees `lock.os` and `wait.os`, the stale process id in `upload/lock`
  blocks nothing, and the next start sends the rest (test_upload's Windows check, and point 4 above). Pause works as
  on the Mac.

### Found besides

- Windows replaces no file that another process has open, and Python opens every file so. The e2e's second Save in
  Multi-view failed with EPERM while the restarted uploader read `decisions.json`. Replacing `decisions.json`,
  `check.json`, `clock.json` and the uploader's JSON is now tried again for up to 5 s on Windows
  (`camrig/record.py` `replace`, `viewer/src/server/data.ts` `renameOver`).
- The app showed ⌘S, ⌘Z, ⌥K and Return. On Windows it now shows Ctrl+S, Ctrl+Z, Alt+K and Enter; the Mac is
  unchanged.
- test_core and test_hand_sync wrote or compared Windows paths as if they were the Mac's.

### Still open

- Only Windows 11 was tried, not Windows 10. `install.ps1` refuses Windows on ARM: the lock has `win-64` only.
- E: was a `subst` drive, not a USB stick or memory card (one shows as "E: (USB drive or memory card)"). Network
  shares, `\\server\share` or a mapped drive, were not tried.
- `José Q` was a profile folder set through `USERPROFILE`, not a Windows account of that name.
- A stop of the uploader in its last look, after progress.json said done, leaves done on Windows (no SIGTERM
  cleanup): a change made in that second goes up with the next change.
- Cancel ends a step at once, with no Ctrl-C to tidy up; only `steps/sync.py`'s stopwatch reading makes temporary
  files.
- The e2e ran in Chrome; Edge is only its fallback. A PC whose policy blocks PowerShell scripts or `pythonw.exe` was
  not tried.
