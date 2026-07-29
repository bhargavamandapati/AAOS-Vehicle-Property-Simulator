"""Small filesystem helpers shared across GUI tabs."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_in_file_manager(path: Path) -> None:
    """Open `path` (a directory) in the OS's file manager, creating it first
    if needed. Best-effort - failures are the caller's problem to surface."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])
