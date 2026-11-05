"""Mark, 5 Nov 2026: helpers must not open black windows on the desktop."""

import subprocess
import sys

import pytest

import hub  # noqa: F401 - importing it is what keeps helpers windowless

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows consoles only")

REPORT = ("import ctypes;h=ctypes.windll.kernel32.GetConsoleWindow();"
          "print(bool(ctypes.windll.user32.IsWindowVisible(h)))")


def test_helper_has_no_visible_window():
    r = subprocess.run([sys.executable, "-c", REPORT], capture_output=True, text=True, timeout=25)
    assert r.stdout.strip() == "False"


def test_helpers_own_child_shares_the_hidden_console():
    # the sqlcmd case: Hub -> python helper -> console program
    outer = ("import subprocess,sys;"
             f"print(subprocess.run([sys.executable,'-c',{REPORT!r}],capture_output=True,text=True).stdout)")
    r = subprocess.run([sys.executable, "-c", outer], capture_output=True, text=True, timeout=25)
    assert r.stdout.strip() == "False"


def test_helpers_never_run_under_pythonw():
    assert not sys.executable.lower().endswith("pythonw.exe")


def test_explicit_creationflags_are_kept():
    r = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, timeout=25)
    assert r.stdout.strip() == "1"
