"""Continuous, bounded on-disk logging.

Separate from the in-memory buffers the GUI keeps for display (which are
intentionally bounded/trimmed for performance - see logcat_tab.py and
command_log.py), these rotating log files mean ADB command history and
raw logcat output survive a session even if the user never clicks
Export. Rotation keeps disk usage bounded so "continuous" doesn't mean
"unbounded".
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "logs"
COMMAND_LOG_FILE = LOG_DIR / "adb_commands.log"
LOGCAT_LOG_FILE = LOG_DIR / "logcat.log"


def _make_logger(name: str, path: Path, max_bytes: int = 5 * 1024 * 1024, backups: int = 5) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


command_file_logger = _make_logger("aaos_vps.commands", COMMAND_LOG_FILE)
logcat_file_logger = _make_logger("aaos_vps.logcat", LOGCAT_LOG_FILE)
