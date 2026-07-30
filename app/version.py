"""Version info: `app.__version__` is the manually-maintained base
semantic version (bump it by hand for releases); everything else here is
build metadata generated automatically from the local git checkout -
commit count and short hash - so every build/checkout gets a distinct,
traceable version string without needing a separate release/CI pipeline.

Format (SemVer-compatible "build metadata" after `+`):
    1.0.0+245.ab12cd3          - 245 commits, at commit ab12cd3
    1.0.0+245.ab12cd3.dirty    - same, with uncommitted local changes
    1.0.0                      - git info unavailable (e.g. a zip
                                  download with no .git directory, or no
                                  `git` executable on PATH)
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict

from app import __version__ as BASE_VERSION

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class BuildInfo(TypedDict):
    commit_count: Optional[int]
    short_hash: Optional[str]
    dirty: Optional[bool]


def _run_git(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            creationflags=_CREATIONFLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


@lru_cache(maxsize=1)
def get_build_info() -> BuildInfo:
    """Queries the local git checkout once per process (cached - this
    can't change while the app is running). Every field is None if this
    isn't a git checkout or `git` isn't available - never raises."""
    commit_count_raw = _run_git("rev-list", "--count", "HEAD")
    short_hash = _run_git("rev-parse", "--short", "HEAD") or None
    status = _run_git("status", "--porcelain")

    commit_count = int(commit_count_raw) if commit_count_raw and commit_count_raw.isdigit() else None
    dirty = bool(status) if status is not None else None

    return {"commit_count": commit_count, "short_hash": short_hash, "dirty": dirty}


def format_version(base_version: str, info: BuildInfo) -> str:
    """Pure formatting, kept separate from the git-querying I/O above so
    it's trivially unit-testable with hand-built BuildInfo dicts."""
    parts = []
    if info.get("commit_count") is not None:
        parts.append(str(info["commit_count"]))
    if info.get("short_hash"):
        parts.append(info["short_hash"])
    if not parts:
        return base_version
    build = ".".join(parts)
    if info.get("dirty"):
        build += ".dirty"
    return f"{base_version}+{build}"


def get_version_string() -> str:
    return format_version(BASE_VERSION, get_build_info())
