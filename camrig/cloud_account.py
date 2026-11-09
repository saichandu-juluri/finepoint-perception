"""This computer's sign-in to the cloud (docs/SERVER.md, "The lab computer").

    python -m camrig.cloud_account signin          {url, email, password} as JSON on stdin
    python -m camrig.cloud_account signout
    python -m camrig.cloud_account status
    python -m camrig.cloud_account files SESSION

The address comes from CAMRIG_CLOUD_URL, else ~/.camrig/cloud.json {url, email, name, lab}. The token comes from
CAMRIG_CLOUD_KEY, else the OS password safe through keyring (service "FinePoint Perception", account = the address);
it is never printed, logged, written to a file or put on a command line. CAMRIG_READONLY refuses signin and signout.
Every answer is one JSON object on stdout; a failure is {"error": "<plain sentence>"} with exit 1.
Standard library plus keyring, which is imported only when the token is not in the environment.
"""
import json
import os
import platform
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVICE = "FinePoint Perception"
SIGNED_OUT = "Signed out of the cloud: sign in again in the app"
TIMEOUT_S = 30


class CloudError(Exception):
    """a plain sentence for the person"""


def config_path() -> Path:
    return Path.home() / ".camrig" / "cloud.json"


def read_config() -> dict:
    try:
        d = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def address() -> str | None:
    url = os.environ.get("CAMRIG_CLOUD_URL") or read_config().get("url")
    return url.rstrip("/") if isinstance(url, str) and url.strip("/") else None


def keyring_module():
    try:
        import keyring
    except ImportError as e:
        raise CloudError(f"the password safe cannot be used: Python's keyring is not installed ({e})") from e
    return keyring


def stored_token(url: str) -> str | None:
    try:
        return keyring_module().get_password(SERVICE, url)
    except CloudError:
        raise
    except Exception as e:
        raise CloudError(f"the password safe cannot be read: {e}") from e


def token(url: str) -> tuple[str | None, bool]:
    """(token, from the environment); the password safe is asked only when CAMRIG_CLOUD_KEY is unset"""
    if os.environ.get("CAMRIG_CLOUD_KEY"):
        return os.environ["CAMRIG_CLOUD_KEY"], True
    return stored_token(url), False


def settings() -> tuple[str, str] | None:
    """(address, token) for the uploader, None when either is missing; no address, no look in the password safe"""
    url = address()
    if url is None:
        return None
    key, _ = token(url)
    return (url, key) if key else None


def call(url: str, method: str, path: str, key: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            answer = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error") or e.reason
        except (ValueError, AttributeError, OSError):
            msg = e.reason
        if e.code == 401 and key:
            raise CloudError(SIGNED_OUT) from e
        raise CloudError(f"the cloud answered {e.code}: {msg}") from e
    except (OSError, ValueError) as e:
        raise CloudError(f"the cloud at {url} cannot be reached ({e})") from e
    if not isinstance(answer, dict):
        raise CloudError("the cloud's answer was not understood")
    return answer


def check_url(url) -> str:
    if not isinstance(url, str):
        raise CloudError("the server address is missing")
    u = urllib.parse.urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
        raise CloudError("the server address must look like https://perception.example.com")
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise CloudError("the server address must start with https:// (http:// is only for this computer)")
    return url.strip().rstrip("/")


def refuse_readonly(what: str):
    if os.environ.get("CAMRIG_READONLY"):
        raise CloudError(f"the app is read only, so it cannot {what}")


def write_config(data: dict):
    path = config_path()
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def signin() -> dict:
    refuse_readonly("sign in")
    try:
        d = json.loads(sys.stdin.read())
    except ValueError as e:
        raise CloudError("the sign-in details were not JSON") from e
    if not isinstance(d, dict):
        raise CloudError("the sign-in details were not a JSON object")
    url = check_url(d.get("url"))
    email, password = d.get("email"), d.get("password")
    if not (isinstance(email, str) and email.strip() and isinstance(password, str) and password):
        raise CloudError("the email and the password are both needed")
    answer = call(url, "POST", "/auth/signin", body={"email": email.strip(), "password": password,
                                                       "computer": platform.node()})
    key = answer.get("token")
    if not isinstance(key, str) or not key:
        raise CloudError("the cloud's answer to the sign-in had no token")
    try:
        keyring_module().set_password(SERVICE, url, key)
    except CloudError:
        raise
    except Exception as e:
        raise CloudError(f"the password safe cannot keep the sign-in: {e}") from e
    me = {k: answer.get(k) for k in ("email", "name", "lab")}
    write_config({"url": url, **me})
    return me


def signout() -> dict:
    refuse_readonly("sign out")
    if os.environ.get("CAMRIG_CLOUD_KEY"):
        raise CloudError("the token is set in the environment, so there is no sign-in to end here")
    url = address()
    if url is None:
        return {}
    key = stored_token(url)
    if key:
        try:
            call(url, "POST", "/auth/signout", key)
        except CloudError as e:
            if str(e) != SIGNED_OUT:
                raise
        try:
            keyring_module().delete_password(SERVICE, url)
        except Exception as e:
            if stored_token(url):
                raise CloudError(f"the password safe cannot forget the sign-in: {e}") from e
    return {}


def status() -> dict:
    url, cfg = address(), read_config()
    key, from_env = token(url) if url else (None, bool(os.environ.get("CAMRIG_CLOUD_KEY")))
    same = isinstance(cfg.get("url"), str) and cfg["url"].rstrip("/") == url
    return {"url": url, "signedIn": bool(url and key), **{k: cfg.get(k) if same else None for k in ("email", "name", "lab")},
            "fromEnv": from_env}


def files(session: str) -> dict:
    url = address()
    key = token(url)[0] if url else None
    if not key:
        raise CloudError("Sign in to see the cloud's files")
    return call(url, "GET", f"/sessions/{urllib.parse.quote(session, safe='')}/files", key)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"signin": signin, "signout": signout, "status": status}
    try:
        if argv[:1] == ["files"] and len(argv) == 2:
            answer = files(argv[1])
        elif len(argv) == 1 and argv[0] in commands:
            answer = commands[argv[0]]()
        else:
            raise CloudError("usage: python -m camrig.cloud_account signin|signout|status|files SESSION")
    except CloudError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    sys.exit(main())
