"""FinePoint Perception's Start-menu and desktop shortcuts (install.ps1) run this file with the app's own pythonw.exe, which
opens no window. It runs start-finepoint-perception.bat with no window either, its output appended to
%LOCALAPPDATA%\\FinePoint Perception\\FinePoint Perception.log, and shows a message if the app did not start."""
import ctypes
import os
import subprocess
from pathlib import Path

here = Path(__file__).resolve().parent
log = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FinePoint Perception" / "FinePoint Perception.log"
log.parent.mkdir(parents=True, exist_ok=True)
with open(log, "ab") as out:
    # cmd /s /c ""<file>"": the quotes around the path stay whatever it holds, such as spaces or brackets
    code = subprocess.call(f'cmd.exe /d /s /c ""{here / "start-finepoint-perception.bat"}""', cwd=here, stdin=subprocess.DEVNULL,
                           stdout=out, stderr=out, creationflags=subprocess.CREATE_NO_WINDOW)
if code:
    ctypes.windll.user32.MessageBoxW(None, "FinePoint Perception did not start. Send the FinePoint team a screenshot of this "
                                     f"message and the file\n\n{log}", "FinePoint Perception", 0x10)
