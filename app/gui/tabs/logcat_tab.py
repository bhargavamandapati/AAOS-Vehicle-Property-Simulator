"""Logcat console tab: streamed `adb logcat` with filters, colored levels,
clear, search, and save-to-file - all rendered without blocking the UI or
growing memory without bound.
"""
from __future__ import annotations

import queue
import re
import tkinter as tk
from collections import deque
from tkinter import filedialog, ttk
from typing import Deque, List, Optional, Tuple

import ttkbootstrap as ttkb

from app.adb_manager import LogcatStream
from app.config import config
from app.gui.context import AppContext
from app.gui.theme import LOG_LEVEL_COLORS, LOG_LEVEL_LABELS
from app.gui.widgets import debounce
from app.persistent_log import LOG_DIR, logcat_file_logger
from app.utils.fs import open_in_file_manager

_THREADTIME_RE = re.compile(
    r"^\d\d-\d\d\s+\d\d:\d\d:\d\d\.\d+\s+\d+\s+\d+\s+([VDIWEFS])\s+([^:]+?)\s*:\s?(.*)$"
)

LEVEL_ORDER = ["V", "D", "I", "W", "E", "F"]

PRESETS = {
    "All logs": {"tags": "", "min_level": "V"},
    "Car Service": {"tags": "CarService,CarPropertyService,CarPropertyManager", "min_level": "V"},
    "Vehicle HAL (best-effort tags)": {"tags": "VehicleHalService,DefaultVehicleHal,android.hardware.automotive.vehicle", "min_level": "V"},
    "Property Changes": {"tags": "CarPropertyService,CarPropertyManager", "min_level": "V"},
    "Warnings & up": {"tags": "", "min_level": "W"},
    "Errors only": {"tags": "", "min_level": "E"},
}


class ParsedLine:
    __slots__ = ("level", "tag", "message", "raw")

    def __init__(self, level: str, tag: str, message: str, raw: str) -> None:
        self.level = level
        self.tag = tag
        self.message = message
        self.raw = raw


def parse_line(raw: str) -> ParsedLine:
    match = _THREADTIME_RE.match(raw)
    if match:
        return ParsedLine(match.group(1), match.group(2).strip(), match.group(3), raw)
    return ParsedLine("?", "", raw, raw)


def build_logcat_args(tags: List[str], min_level: str) -> List[str]:
    args = ["-v", "threadtime"]
    if tags:
        for tag in tags:
            args.append(f"{tag}:V")
        args.append("*:S")
    else:
        args.append(f"*:{min_level}")
    return args


class LogcatTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.stream: Optional[LogcatStream] = None
        self.buffer_limit = int(config.get("logcat_buffer_lines", 4000))
        self.buffer: Deque[ParsedLine] = deque(maxlen=self.buffer_limit)
        self.paused = False
        self.autoscroll = True
        self.total_received = 0
        self._flush_ms = int(config.get("logcat_flush_ms", 120))
        self._after_id: Optional[str] = None
        self._build_ui()
        ctx.on_device_changed(self._on_device_changed)
        self._update_button_states()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 4))

        self.start_btn = ttkb.Button(toolbar, text="▶ Start", bootstyle="success", command=self._start)
        self.start_btn.pack(side="left", padx=(0, 4))
        self.stop_btn = ttkb.Button(toolbar, text="■ Stop", bootstyle="danger-outline", command=self._stop)
        self.stop_btn.pack(side="left", padx=4)
        self.pause_btn = ttkb.Button(toolbar, text="⏸ Pause", bootstyle="warning-outline", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=4)
        ttkb.Button(toolbar, text="🗑 Clear", bootstyle="secondary-outline", command=self._clear).pack(side="left", padx=4)
        ttkb.Button(toolbar, text="💾 Save", bootstyle="secondary-outline", command=self._save).pack(side="left", padx=4)
        ttkb.Button(toolbar, text="📁 Logs Folder", bootstyle="secondary-outline", command=self._open_logs_folder).pack(side="left", padx=4)

        self.autoscroll_var = tk.BooleanVar(value=True)
        ttkb.Checkbutton(
            toolbar, text="Autoscroll", variable=self.autoscroll_var, bootstyle="round-toggle",
            command=lambda: setattr(self, "autoscroll", self.autoscroll_var.get()),
        ).pack(side="left", padx=12)

        self.stats_label = ttkb.Label(toolbar, text="0 lines", bootstyle="secondary")
        self.stats_label.pack(side="right")

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", pady=(0, 4))
        ttkb.Label(filter_bar, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar(value="All logs")
        preset_combo = ttkb.Combobox(
            filter_bar, textvariable=self.preset_var, values=list(PRESETS.keys()),
            state="readonly", width=26,
        )
        preset_combo.pack(side="left", padx=(4, 12))
        preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)

        ttkb.Label(filter_bar, text="Tags:").pack(side="left")
        self.tags_var = tk.StringVar()
        ttkb.Entry(filter_bar, textvariable=self.tags_var, width=32).pack(side="left", padx=(4, 12))

        ttkb.Label(filter_bar, text="Min level:").pack(side="left")
        self.level_var = tk.StringVar(value="V")
        ttkb.Combobox(
            filter_bar, textvariable=self.level_var, values=LEVEL_ORDER, state="readonly", width=4,
        ).pack(side="left", padx=(4, 12))

        ttkb.Label(filter_bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttkb.Entry(filter_bar, textvariable=self.search_var, width=22).pack(side="left", padx=(4, 4))
        self.search_var.trace_add("write", debounce(self, 250, self._rerender_from_buffer))

        legend = ttk.Frame(self)
        legend.pack(fill="x", pady=(0, 4))
        for level in LEVEL_ORDER:
            fg, bg = LOG_LEVEL_COLORS[level]
            chip = tk.Label(
                legend, text=f" {level} ", fg=fg, bg=(bg or "#20242c"),
                font=("Consolas", 9, "bold"), padx=4,
            )
            chip.pack(side="left", padx=2)
            ttkb.Label(legend, text=LOG_LEVEL_LABELS[level], bootstyle="secondary").pack(side="left", padx=(0, 8))

        ttkb.Label(
            legend, text=f"Continuously saved to: {LOG_DIR / 'logcat.log'}", bootstyle="secondary",
        ).pack(side="right")

        self.text_widget = ttkb.ScrolledText(self, auto_hide=False, wrap="none", font=("Consolas", 9))
        self.text_widget.pack(fill="both", expand=True)
        self.text_widget.text.configure(state="disabled", background="#111318", insertbackground="#eeeeee")
        for level, (fg, bg) in LOG_LEVEL_COLORS.items():
            tag_opts = {"foreground": fg}
            if bg:
                tag_opts["background"] = bg
            self.text_widget.text.tag_configure(level, **tag_opts)
        self.text_widget.text.tag_configure("?", foreground="#dddddd")

    def _update_button_states(self) -> None:
        running = self.stream is not None and self.stream.is_running
        connected = self.ctx.is_connected
        self.start_btn.configure(state="disabled" if (running or not connected) else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self.pause_btn.configure(state="normal" if running else "disabled")

    # -- device lifecycle ---------------------------------------------------
    def _on_device_changed(self, _device) -> None:
        self._stop()
        self._clear()
        self._update_button_states()

    # -- filters ---------------------------------------------------------
    def _apply_preset(self, _event=None) -> None:
        preset = PRESETS.get(self.preset_var.get())
        if preset:
            self.tags_var.set(preset["tags"])
            self.level_var.set(preset["min_level"])

    # -- streaming control -------------------------------------------------
    def _start(self) -> None:
        if not self.ctx.serial:
            self.ctx.notify_status("Select a device before starting logcat.", "warning")
            return
        if self.stream is not None and self.stream.is_running:
            return
        tags = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        args = build_logcat_args(tags, self.level_var.get())

        def on_error(message: str) -> None:
            self.ctx.notify_status(f"logcat error: {message}", "error")

        self.stream = self.ctx.adb.start_logcat(self.ctx.serial, extra_args=args, on_error=on_error)
        self.stream.start()
        self.paused = False
        self.pause_btn.configure(text="⏸ Pause")
        self._schedule_flush()
        self._update_button_states()
        self.ctx.notify_status("Logcat started", "info")

    def _stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream = None
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._update_button_states()

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.configure(text="▶ Resume" if self.paused else "⏸ Pause")

    def _clear(self) -> None:
        self.buffer.clear()
        self.total_received = 0
        self.text_widget.text.configure(state="normal")
        self.text_widget.text.delete("1.0", "end")
        self.text_widget.text.configure(state="disabled")
        self.stats_label.configure(text="0 lines")
        if self.ctx.serial:
            from app.utils.workers import run_async
            run_async(self.ctx.root, lambda: self.ctx.adb.clear_logcat(self.ctx.serial))

    def _save(self) -> None:
        if not self.buffer:
            return
        path = filedialog.asksaveasfilename(
            title="Save logcat output", defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("Log", "*.log")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                for entry in self.buffer:
                    fh.write(entry.raw + "\n")
            self.ctx.notify_status(f"Saved {len(self.buffer)} log lines to {path}", "success")
        except OSError as exc:
            self.ctx.notify_status(f"Failed to save log: {exc}", "error")

    def _open_logs_folder(self) -> None:
        try:
            open_in_file_manager(LOG_DIR)
        except OSError as exc:
            self.ctx.notify_status(f"Could not open logs folder: {exc}", "error")

    # -- queue draining / rendering -----------------------------------------
    def _schedule_flush(self) -> None:
        self._after_id = self.after(self._flush_ms, self._flush)

    def _flush(self) -> None:
        if self.stream is None:
            return
        drained: List[str] = []
        try:
            while True:
                line = self.stream.line_queue.get_nowait()
                drained.append(line)
        except queue.Empty:
            pass

        ended = False
        persisted_lines: List[str] = []
        for raw in drained:
            if raw == "__LOGCAT_STREAM_ENDED__":
                ended = True
                continue
            parsed = parse_line(raw)
            self.total_received += 1
            self.buffer.append(parsed)
            persisted_lines.append(raw)
            if not self.paused and self._matches_search(parsed):
                self._append_line(parsed)

        if persisted_lines:
            # Continuous on-disk storage, independent of the bounded
            # in-memory buffer above and regardless of pause/search state -
            # one write per flush tick (not per line) keeps this cheap even
            # under heavy logcat volume.
            logcat_file_logger.info("\n".join(persisted_lines))

        if drained:
            self.stats_label.configure(text=f"{self.total_received} lines received, {len(self.buffer)} buffered")

        if ended:
            self.ctx.notify_status("Logcat stream ended unexpectedly.", "warning")
            self._update_button_states()
            return

        self._schedule_flush()

    def _matches_search(self, entry: ParsedLine) -> bool:
        query = self.search_var.get().strip().lower()
        if not query:
            return True
        return query in entry.raw.lower()

    def _append_line(self, entry: ParsedLine) -> None:
        widget = self.text_widget.text
        widget.configure(state="normal")
        widget.insert("end", entry.raw + "\n", (entry.level,))
        # Bound the on-screen buffer to match the retained buffer size so
        # memory/CPU cost of the Text widget cannot grow without limit
        # during a long-running session.
        overflow = int(widget.index("end-1c").split(".")[0]) - self.buffer_limit
        if overflow > 0:
            widget.delete("1.0", f"{overflow + 1}.0")
        widget.configure(state="disabled")
        if self.autoscroll:
            widget.see("end")

    def _rerender_from_buffer(self) -> None:
        widget = self.text_widget.text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for entry in self.buffer:
            if self._matches_search(entry):
                widget.insert("end", entry.raw + "\n", (entry.level,))
        widget.configure(state="disabled")
        if self.autoscroll:
            widget.see("end")

    def shutdown(self) -> None:
        self._stop()
