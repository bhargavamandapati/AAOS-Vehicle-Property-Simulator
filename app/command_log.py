"""Records every ADB command the app runs (request + response) - both a
bounded in-memory buffer the GUI can display live, and a continuous
rotating on-disk log (see app/persistent_log.py) so the history survives
beyond the current session.

AdbManager._run() is the single choke point for every subprocess call
(device list, shell commands, get/set/inject-property-value, dumpsys,
server restarts, ...), so hooking it there gives full coverage without
instrumenting every call site individually.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Deque, List, Optional

from app.persistent_log import command_file_logger

MAX_STORED_RESPONSE_CHARS = 4000


@dataclass
class CommandLogEntry:
    timestamp: str
    command: str
    success: bool
    duration_ms: float
    response: str
    truncated: bool = False


class CommandLogger:
    def __init__(self, max_entries: int = 2000) -> None:
        self._entries: Deque[CommandLogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._listeners: List[Callable[[CommandLogEntry], None]] = []

    def add_listener(self, callback: Callable[[CommandLogEntry], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[CommandLogEntry], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def record(self, command: str, success: bool, duration_ms: float, response: str) -> CommandLogEntry:
        response = response or ""
        truncated = len(response) > MAX_STORED_RESPONSE_CHARS
        stored_response = response[:MAX_STORED_RESPONSE_CHARS] + ("\n... [truncated]" if truncated else "")

        entry = CommandLogEntry(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            command=command,
            success=success,
            duration_ms=duration_ms,
            response=stored_response,
            truncated=truncated,
        )
        with self._lock:
            self._entries.append(entry)

        status = "OK" if success else "FAIL"
        first_line = stored_response.splitlines()[0] if stored_response.splitlines() else ""
        command_file_logger.info(
            f"[{entry.timestamp}] {status} ({duration_ms:.0f}ms) $ {command}\n    -> {first_line}"
        )

        for listener in list(self._listeners):
            try:
                listener(entry)
            except Exception:  # noqa: BLE001 - a broken GUI listener must not break logging
                pass
        return entry

    def entries(self) -> List[CommandLogEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


command_logger = CommandLogger()
