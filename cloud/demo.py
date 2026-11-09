"""The whole lab computer on this one, with the local stand-in for the cloud: no AWS (docs/UPLOAD.md).

    pixi run --frozen demo [--viewer-port 3210] [--slow SECONDS]

Starts cloud/dev_server.py on a free port, then the viewer with that cloud in its own environment only, so the lab key
stays in the viewer's server and is never sent to the browser. Prints the viewer's address. Ctrl+C stops both, and the
uploaders the viewer started (they would otherwise retry against a stand-in that is gone; the next viewer start resumes
them). The sessions are the viewer's usual ones: CAMRIG_SESSIONS, default ../sessions. Standard library only.
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEWER = HERE.parent / "viewer"


def start(args, **kw) -> subprocess.Popen:
    """in its own process group, so one signal stops it and everything it started"""
    if os.name == "nt":
        return subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **kw)
    return subprocess.Popen(args, start_new_session=True, **kw)


def stop(p: subprocess.Popen):
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        return
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)


def uploaders(roots: list[str]) -> list[int]:
    """the uploaders the viewer started for the sessions it lists, the one running and any waiting for it, found by their
    command line as the viewer spawns them (python -m camrig.upload <session>/project.toml). Not upload/lock: it names
    only the one running, may be empty or half written, and a lock left by a killed uploader names a process id that
    may since belong to an unrelated process."""
    tomls = [os.path.join(r, "project.toml") for r in roots] + [os.path.join(r, d, "project.toml") for r in roots
                                                                if os.path.isdir(r) for d in os.listdir(r)]
    text = {"text": True}
    if os.name == "nt":                                   # PowerShell writes to a pipe in the OEM code page unless told
        ps = ["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false; "
              "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"]
        text = {"encoding": "utf-8", "errors": "replace"}
    else:
        ps = ["ps", "-axww", "-o", "pid=,args="]
    pids = []
    for line in subprocess.run(ps, capture_output=True, **text).stdout.splitlines():
        pid, _, args = line.strip().partition(" ")
        args = args.replace('"', "") + " "                # Windows quotes a path with spaces
        if pid.isdigit() and int(pid) > 0 and int(pid) != os.getpid() and any(f"-m camrig.upload {t} " in args for t in tomls):
            pids.append(int(pid))
    return pids


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--viewer-port", type=int, default=3210)
    ap.add_argument("--slow", type=float, default=0, metavar="SECONDS",
                    help="each piece takes this long to arrive, so a person has time to try Pause and Resume")
    a = ap.parse_args()
    npm = shutil.which("npm")
    if not npm:
        sys.exit("npm was not found: run this through pixi (pixi run --frozen demo), which provides node")
    if not (VIEWER / ".next" / "BUILD_ID").exists():
        print("building the app once (pixi run --frozen viewer-build) ...", flush=True)
        subprocess.run([npm, "ci"], cwd=VIEWER, check=True)
        subprocess.run([npm, "run", "build"], cwd=VIEWER, check=True)
    stand_in = [str(VIEWER / "tests" / "slow_cloud.py"), str(a.slow)] if a.slow > 0 else [str(HERE / "dev_server.py")]
    cloud = start([sys.executable, *stand_in, "--port", "0"], stdout=subprocess.PIPE, text=True)
    ready = cloud.stdout.readline().split()
    if ready[:1] != ["ready"]:
        stop(cloud)
        sys.exit("the cloud stand-in did not start")
    url, key = ready[1], ready[-1]
    env = {**os.environ, "CAMRIG_CLOUD_URL": url, "CAMRIG_CLOUD_KEY": key}
    viewer = start([npm, "exec", "--", "next", "start", "-H", "127.0.0.1", "-p", str(a.viewer_port)], cwd=VIEWER, env=env)
    address = f"http://127.0.0.1:{a.viewer_port}"
    roots = [os.path.abspath(os.path.join(VIEWER, p)) for p in os.environ.get("CAMRIG_SESSIONS", str(HERE.parent / "sessions")).split(os.pathsep) if p]
    try:
        for _ in range(300):
            if viewer.poll() is not None or cloud.poll() is not None:
                sys.exit("the app or the cloud stand-in stopped; see the lines above")
            try:
                urllib.request.urlopen(address, timeout=2)
                break
            except OSError:
                time.sleep(0.2)
        print(f"FinePoint Perception demo: open {address}  (cloud stand-in {url}, lab dev). Ctrl+C stops it.", flush=True)
        while viewer.poll() is None and cloud.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop(viewer)
        for pid in uploaders(roots):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        stop(cloud)
        print("FinePoint Perception demo: stopped", flush=True)


if __name__ == "__main__":
    main()
