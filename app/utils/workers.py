"""Small helpers to keep blocking ADB calls off the Tk main thread.

Tkinter is not thread-safe: widgets may only be touched from the main
thread. The pattern used throughout the GUI layer is: run the blocking
work in a daemon thread, then hand the result back to the main thread via
`widget.after(0, ...)`, which Tk guarantees runs on the event loop.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Callable, Optional


def _safe_after(widget: Any, callback: Callable[[], None]) -> None:
    """Schedule `callback` on the Tk main thread, swallowing the case where
    the window was closed/destroyed while the background thread was still
    running - that's a normal shutdown race, not an error worth surfacing.
    """
    try:
        if not widget.winfo_exists():
            return
        widget.after(0, callback)
    except (RuntimeError, tk.TclError):
        pass


def run_async(
    widget: Any,
    func: Callable[[], Any],
    on_done: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[BaseException], None]] = None,
) -> threading.Thread:
    """Run `func` in a background thread; deliver the outcome on the UI thread."""

    def worker() -> None:
        try:
            result = func()
        except BaseException as exc:  # noqa: BLE001 - surfaced to caller, not swallowed
            if on_error is not None:
                _safe_after(widget, lambda e=exc: on_error(e))
            return
        if on_done is not None:
            _safe_after(widget, lambda r=result: on_done(r))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class Poller:
    """Runs `task` on a background thread every `interval_ms`.

    Ticks never overlap: if a previous call has not finished when the
    next interval elapses, that tick is skipped instead of stacking up
    threads/adb processes. This bounds both CPU and memory usage when a
    slow or unresponsive device is attached.
    """

    def __init__(
        self,
        widget: Any,
        interval_ms: int,
        task: Callable[[], Any],
        on_result: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        self._widget = widget
        self._interval_ms = max(200, interval_ms)
        self._task = task
        self._on_result = on_result
        self._on_error = on_error
        self._running = False
        self._busy = False
        self._after_id: Optional[str] = None

    def start(self, immediate: bool = True) -> None:
        if self._running:
            return
        self._running = True
        if immediate:
            self._tick()
        else:
            self._schedule_next()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def set_interval(self, interval_ms: int) -> None:
        self._interval_ms = max(200, interval_ms)

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._after_id = self._widget.after(self._interval_ms, self._tick)

    def _tick(self) -> None:
        if not self._running:
            return
        if not self._busy:
            self._busy = True
            run_async(self._widget, self._task, self._handle_result, self._handle_error)
        self._schedule_next()

    def _handle_result(self, result: Any) -> None:
        self._busy = False
        if self._running and self._on_result is not None:
            self._on_result(result)

    def _handle_error(self, exc: BaseException) -> None:
        self._busy = False
        if self._running and self._on_error is not None:
            self._on_error(exc)
