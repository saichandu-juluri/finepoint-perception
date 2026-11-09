# Working on FinePoint Perception

- `camrig/`: the Python package: the project file, the steps cameras, scan and clock, the uploader and the sign-in.
- `steps/`: code the steps call.
- `viewer/`: the app itself, a Next.js server on `http://127.0.0.1:3210` (`viewer/README.md`).
- `cloud/`: the server the app uploads through, and its local stand-in.
- `install.command`: what the README's one line runs. It makes `FinePoint Perception.app` (in `~/Applications`, or in
  `FPP_APPS_DIR`), which runs `start-finepoint-perception.command` in the background with its output in
  `~/Library/Logs/FinePoint Perception.log`. Where pixi is missing it installs pixi 0.83.0, the version that made
  `pixi.lock`: change both together.
- `install.ps1`: the same on Windows, which the README's PowerShell line downloads and runs. It clones the app into
  `%USERPROFILE%\finepoint-perception`, or pulls there, with git from conda-forge (`pixi exec`), so a PC needs no git.
  Run as a file (`powershell -ExecutionPolicy Bypass -File install.ps1`) it installs its own folder as it is. Where pixi
  is missing it unpacks the pixi 0.83.0 release into `%USERPROFILE%\.pixi\bin` (not on PATH), checked against the
  SHA-256 in the script: change version and checksum together, with `pixi.lock`. It stops every process that runs from
  the app's folder (the app, an uploader, a step: Windows replaces no file that is open), installs, builds, and makes
  the FinePoint Perception shortcut in the Start menu and on the desktop (in `FPP_APPS_DIR` for tests). The shortcut runs
  `start-finepoint-perception.pyw` with the environment's `pythonw.exe`, which opens no window: it runs
  `start-finepoint-perception.bat` with no window either, its output in
  `%LOCALAPPDATA%\FinePoint Perception\FinePoint Perception.log`, and shows a message if the app did not start.

## Environments

`pixi.toml` has two environments; `pixi.lock` pins every version.

- `core`: the app, the steps and the uploader (Python, ffmpeg, Node). `pixi install -e core --locked`
- `cloud`: the server and its local stand-in, with boto3. `pixi install -e cloud --locked`

Run commands as `pixi run --frozen -e core ...` or `pixi run --frozen -e cloud ...`.

    pixi run --frozen -e core viewer-build          # npm ci and next build, in viewer/
    pixi run --frozen -e core viewer                # the app, built
    cd viewer && pixi run --frozen -e core npm run dev    # the app while you change its code

## Tests

    pixi run --frozen -e core test-core
    pixi run --frozen -e core test-clock
    pixi run --frozen -e core test-upload
    pixi run --frozen -e core test-hand-sync
    pixi run --frozen -e cloud test-cloud
    cd viewer && pixi run --frozen -e core npm run check   # the transcript editor, without a browser
    cd viewer && pixi run --frozen -e core npm run e2e     # next build, then every screen in headless Chrome

`viewer/README.md` says what the end-to-end test needs. A test that signs in keeps the token in a file in a temporary
folder (`PYTHON_KEYRING_BACKEND=camrig.keyring_file.FileKeyring`, `CAMRIG_TEST_KEYRING_DIR`) and runs with a temporary
`HOME`, never with the real password safe or `~/.camrig`.

## The demo

    pixi run --frozen demo

starts the local stand-in for the cloud and the app together, already signed in, and prints the app's address.
Ctrl+C stops both. No AWS.

## The server

- `cloud/`: the server (`server.py`), its admin pages, the S3 store and the local stand-in (`dev_server.py`).
  `docs/SERVER.md` is its contract, `docs/UPLOAD.md` the upload's.
- `pixi run --frozen -e cloud cloud-dev`: the local stand-in on `http://127.0.0.1:8787`.
- `cloud/terraform/`: the server in AWS. `cloud/terraform/README.md` has the resources, the cost and the commands.
  Terraform state (`*.tfstate`) and variable files (`*.tfvars`) are never committed; `.gitignore` keeps them out.

## Formats

`docs/FORMATS.md`: every file the app reads and writes in a session folder.

## Windows

`pixi.toml` also locks `win-64`, and sets `PYTHONUTF8=1` there, so Python reads and writes text in UTF-8 as on the Mac
(a `python -I` run, which ignores it, gets `-X utf8`). The commands above work in PowerShell as they are, with pixi as
`%USERPROFILE%\.pixi\bin\pixi.exe` where it is not on PATH; the end-to-end test takes Chrome, else Edge. Only
`camrig/test_upload.py`'s keychain check is for the Mac alone. `docs/WINDOWS.md` says what was tested on Windows and what
is still open.
