"""One heavy video job at a time on this computer: the board scan, the clock's sound and motion search, a depth export.

    from camrig.heavy import one_at_a_time
    with one_at_a_time("board scan"):
        ...

The lock is a file holding the owner's process id and job name. A lock whose process is gone is taken over, and so is
one that stays unreadable (a write cut off) for a minute. If a run says it waits for a job that is no longer running,
delete .heavy.lock in the repository folder.
Standard library only; works on macOS and Windows.
"""
import contextlib
import os
import time
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent / ".heavy.lock"
UNREADABLE_S = 60.0


def _alive(pid: int) -> bool:
    if os.name == "nt":                                    # os.kill(pid, 0) would end the process on Windows
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259              # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


@contextlib.contextmanager
def one_at_a_time(job: str, wait_s: float = 6 * 3600):
    t0, said = time.time(), False
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid, other = LOCK.read_text().split(" ", 1)
                if not _alive(int(pid)):
                    LOCK.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            except ValueError:                             # empty or cut off: being written now, or left by a failed write
                other = "an unreadable lock"
                try:
                    stale = time.time() - LOCK.stat().st_mtime > UNREADABLE_S
                except FileNotFoundError:
                    continue
                if stale:
                    LOCK.unlink(missing_ok=True)
                    continue
            if not said:
                print(f"waiting for another heavy job to finish: {other} (its lock is {LOCK})", flush=True)
                said = True
            if time.time() - t0 > wait_s:
                raise TimeoutError(f"{job}: waited {wait_s / 3600:.0f} h for {other}")
            time.sleep(5)
            continue
        try:
            os.write(fd, f"{os.getpid()} {job}".encode())
        except BaseException:
            os.close(fd)
            LOCK.unlink(missing_ok=True)
            raise
        os.close(fd)
        break
    try:
        yield
    finally:
        LOCK.unlink(missing_ok=True)
