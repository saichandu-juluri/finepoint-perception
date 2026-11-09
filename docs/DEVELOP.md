# Working on FinePoint Perception

- `camrig/`: the Python package: the project file, the steps cameras, scan and clock, the uploader and the sign-in.
- `steps/`: code the steps call.
- `viewer/`: the app itself, a Next.js server on `http://127.0.0.1:3210` (`viewer/README.md`).
- `cloud/`: the server the app uploads through, and its local stand-in.
- `install.command`: what the README's one line runs. It makes `FinePoint Perception.app` (in `~/Applications`, or in
  `FPP_APPS_DIR`), which runs `start-finepoint-perception.command` in the background with its output in
  `~/Library/Logs/FinePoint Perception.log`. Where pixi is missing it installs pixi 0.83.0, the version that made
  `pixi.lock`: change both together.

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

`pixi.toml` also locks `win-64`, and `start-finepoint-perception.bat` starts the app there, but nothing has been tested
on Windows yet. `docs/WINDOWS.md` is the brief for testing and fixing it there.
