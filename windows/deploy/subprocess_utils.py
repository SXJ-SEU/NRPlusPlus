from __future__ import annotations

import ctypes
import os
import subprocess
from typing import Any


def hidden_process_kwargs() -> dict[str, Any]:
    """Keep short-lived console helpers invisible under pythonw on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def suppress_child_error_dialogs() -> None:
    """Route child launch failures to the reader status instead of modal popups."""
    if os.name != "nt":
        return
    # Child processes inherit the process error mode.  This prevents a failed
    # adb loader from blocking the reader behind an Application Error dialog.
    sem_fail_critical_errors = 0x0001
    sem_no_gp_fault_error_box = 0x0002
    sem_no_open_file_error_box = 0x8000
    try:
        ctypes.windll.kernel32.SetErrorMode(
            sem_fail_critical_errors | sem_no_gp_fault_error_box | sem_no_open_file_error_box
        )
    except (AttributeError, OSError):
        pass
