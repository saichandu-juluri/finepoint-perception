# FinePoint Perception: the app

The app runs in the browser on this computer. It lists the recording sessions, makes a new one from a folder of
recordings, sends a session to the lab's cloud, lines up the cameras on one clock, shows all cameras side by side, and
plays one video with its transcript. It reads only the files of `docs/FORMATS.md`.

    pixi run --frozen -e core viewer-build             # once: installs node_modules and builds
    pixi run --frozen -e core viewer                   # http://127.0.0.1:3210 (npm run dev while changing the code)
    npm run check                                      # the transcript editor's state machine, without a browser
    npm run e2e                                        # next build, then every page in headless Chrome (see Tests)

Run `npm` from `viewer/` with the core environment on PATH (`pixi shell -e core`). Without it, the app still finds
ffmpeg, ffprobe and Python in `../.pixi/envs/core`.

## Start it

`start-finepoint-perception.command` in the app's folder starts the app and opens it in the browser. It builds the app
first if needed. `install.command` makes `FinePoint Perception.app` in `~/Applications`, which runs it in the background
with its output in `~/Library/Logs/FinePoint Perception.log` (`docs/DEVELOP.md`). On Windows the same is
`start-finepoint-perception.bat`, and `install.ps1` makes the FinePoint Perception shortcut in the Start menu and on the
desktop, which runs it with no window, its output in `%LOCALAPPDATA%\FinePoint Perception\FinePoint Perception.log`.

When the app starts, every upload that a restart cut off carries on. To have that happen at login without a click, add
the app to the Mac's Login Items:

1. Sign in once in the Upload panel.
2. Open System Settings, General, Login Items & Extensions.
3. Under Open at Login, click +, choose `FinePoint Perception.app` in the Applications folder of your home folder, and
   click Open.

To stop it, remove the line from Open at Login. On Windows, copy the FinePoint Perception shortcut from the desktop into
the folder that `shell:startup` opens (type it into the Run box, Windows key and R); delete it there to stop.

## Where the sessions are

A session is a folder holding a `project.toml`. The library lists the session folders inside the folders named in
`CAMRIG_SESSIONS`, separated by `:` (`;` on Windows). A folder named there may also be a session itself. The default is
`../sessions`.

    CAMRIG_SESSIONS=/Volumes/Data/sessions:/Volumes/Backup/sessions npm start

New project writes into the first of these folders. `CAMRIG_REPO` (default `..`) names the app's folder, and
`CAMRIG_PYTHON` its core Python, if they are elsewhere. `CAMRIG_READONLY=1` refuses Run, Save, New project, Upload and
Sign in, so the sessions are only shown.

The app reads each project file with the same Python reader the steps use (`scripts/camrig_info.py`). So it refuses a
project file exactly when a run would, and the session's card says why.

Files are served only from inside a session folder, or from the video folders its `project.toml` names. Anything else
gets 403. Pages and requests answer only to this computer's own address.

## Pages

- `/` the library: every session with its steps' statuses and its upload, and New project.
- `/new` New project: choose the folder that holds one subfolder per camera (Up goes one folder up), check the camera
  names and the board, then Create the project. The project file goes into the first sessions folder; the recordings
  stay where they are and are only read.
- `/jobs/<session>` the session's overview:
  - Steps: Run runs `python -m camrig.run <project.toml>` in the core environment, as a job of the server. It lines up
    the cameras in three steps: cameras, scan and clock. Each row shows the step's status (PASS, FLAG, FAIL, NOT
    TESTED), what it found and the end of its `log.txt`, every 2 s while it runs. One job runs at a time; Cancel stops it.
  - Upload to the cloud (see below).
  - Recordings: the files of `work/cameras/cameras.json`, on the clock once the clock step has placed them.
- `/jobs/<session>/multiview?c=<clock s>&part=<cam/part>` all cameras on `work/clock/clock.json`, with sync by hand
  (see below). Open multi-view on the overview leads here.
- `/jobs/<session>/watch/<camera>/<part>[/depth]?t=` one video and its transcript.

## Sync by hand

Multi-view lists under "To place by hand" every part the clock step could not place: a part left out of `clock.json`,
or a part without sound placed beside the part before. Each starts at a first guess. The parts the clock step flagged
are listed below them.

Move a part, or every part of its camera with Whole camera, while the cameras play: 1 frame, 10 ms, 1 s or 10 s earlier
or later. Or type its start as a clock time. Or use "Mark it in a placed camera" and "Mark it in <part>" on the same
moment, such as a clap. Save writes `decisions.json` `clock.user.manual["cam/part"] = {start_s, file, note}` for each
part moved. Rerun the clock then applies it. The parts the clock step placed as the reference camera stay where they
are. A session that keeps an older clock (`[clock] existing`) cannot be moved by hand, and the page says why.

## Upload to the cloud

The overview's Upload panel sends the session to the lab's cloud storage with `python -m camrig.upload <project.toml>`
(`docs/UPLOAD.md`). Upload starts it. Pause stops it after the pieces in flight, and Resume carries on. One bar shows
the session with the time left; the files and what the cloud holds are folded away below it. It ends with "Uploaded.
Everything is in the cloud." Each library card says uploading 52%, paused, or in the cloud.

The uploader runs on its own, beside the steps and outside the one-job lock. Closing the page or the app does not stop
it. After a run of the session ends, and after a Save or Remove in Multi-view, the app starts the uploader again, so the
new files go up too. That happens only for a session whose upload was started before and is not paused. When the app
starts, it resumes every upload whose progress says running, which is what a restart of the computer leaves.

Uploading needs this computer signed in. Until then the panel shows a sign-in form: Server address, Email and Password.
Sign in runs `python -m camrig.cloud_account signin`, which keeps the token in the computer's password safe and writes
`~/.camrig/cloud.json` `{url, email, name, lab}`. The app never sees the token. The server address starts empty, or as
`CAMRIG_CLOUD_URL` when that is set. `CAMRIG_CLOUD_URL` with `CAMRIG_CLOUD_KEY` signs in for a demo, with no Sign out.

    pixi run --frozen -e core demo                     # the app with the local stand-in for the cloud

starts `cloud/dev_server.py` and the app together, prints the address, and stops both on Ctrl+C. Every upload it
starts really reads the files.

## Transcripts

The single view opens the person's own transcript, `transcripts/<camera>/<video stem>.srt` in the session folder, else
`work/transcript/<camera>/<video stem>.srt` when the session has one. Save writes the person's `.srt` and a `.txt`
reading version into `transcripts/<camera>/`. `transcripts/<camera>/.transcript_history/` keeps the version replaced, a
copy of each saved one, and edits thrown away unsaved. Nothing is written beside a video. A file changed on disk while
open is caught: an untouched transcript shows the new version, and edits get a banner to choose.

## Tests

`npm run check` runs the transcript editor's state machine in Node.

`npm run e2e` builds the app, then drives it in headless Chrome (`/Applications/Google Chrome.app`; on Windows Chrome,
else Edge; or `CHROME`). It needs nothing outside the repo. Everything happens in a scratch folder (`E2E_TMP`, default the system temp folder) that
is deleted at the end: short clips made with ffmpeg, the sessions folders, a home folder and a test password safe
(`camrig.keyring_file`), so the real `~/.camrig` and password safe (on Windows Credential Manager) are never touched. Three servers of the app run on
free ports:

- one not signed in: the library, New project from the clips, Run and the steps' statuses, the single view and a
  transcript saved with Cmd+S (Ctrl+S on Windows), Multi-view, and the guards (pages that answer only to this computer, files only from the
  session, no requests from other sites);
- one signed in for a demo, with the stand-in for the cloud slowed to 0.8 s a piece (`tests/slow_cloud.py`): an upload
  a restart cut off goes on when the app starts; Upload, Pause, Resume and done, checked byte for byte in the cloud; the
  silent clips placed by hand in Multi-view, saved, and the clock rerun, with the uploader sending the new files; and
  stopping the running uploader and the one waiting for it, as the demo's Ctrl+C does;
- one not signed in, with `cloud/dev_server.py`: the sign-in form, an upload and Sign out.

It ends by checking that the clips and the real `~/.camrig` are unchanged, and prints PASS or FAIL. `E2E_SHOTS=folder`
saves a screenshot of each page.
