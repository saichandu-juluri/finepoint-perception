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
