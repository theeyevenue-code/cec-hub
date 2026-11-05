"""Keep helper programs off the screen.

The Hub starts from the Windows Startup folder under pythonw, on the desktop staff are using.
A helper started from there (python, then sqlcmd) opened its own black window for every database
read (Mark, 5 Nov 2026: "black square box with sqlcmd on top"). So helpers run under python.exe in
one hidden console, and anything they start shares it."""

import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    _console_python = Path(sys.executable).with_name("python.exe")
    # pythonw has no console to share, so its sqlcmd would still open a window
    if Path(sys.executable).name.lower() == "pythonw.exe" and _console_python.exists():
        sys.executable = str(_console_python)

    class _NoWindowPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            if not kwargs.get("creationflags"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen
