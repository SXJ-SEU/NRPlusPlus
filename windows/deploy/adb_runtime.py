from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from subprocess_utils import hidden_process_kwargs


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdbConfig:
    adb_path: Path
    device_serial: str


class AdbRuntime:
    def __init__(self, config: AdbConfig) -> None:
        self.config = config
        self._root_ready = False
        self._root_via_su = True

    def root_shell(self, command: str, *, timeout: float = 30) -> str:
        adb = str(self.config.adb_path)
        argv = [adb, "-s", self.config.device_serial, "shell", command]
        try:
            result = subprocess.run(argv, check=True, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout,
                                    **hidden_process_kwargs())
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"root shell failed: {exc}") from exc
        self._root_ready = True
        self._root_via_su = False
        return result.stdout
