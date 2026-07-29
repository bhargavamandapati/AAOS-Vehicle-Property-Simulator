"""Screenshot tab: capture the connected device's display via
`adb exec-out screencap`, preview it, save full-resolution PNGs, and
optionally poll for a slow "live" view at a user-chosen interval.
"""
from __future__ import annotations

import io
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk
from typing import Optional

import ttkbootstrap as ttkb
from PIL import Image, ImageTk
from ttkbootstrap.dialogs import Messagebox

from app.gui.context import AppContext
from app.utils.workers import Poller, run_async


class ScreenshotTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self._last_png_bytes: Optional[bytes] = None
        self._last_captured_at: Optional[datetime] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._poller: Optional[Poller] = None
        self._build_ui()
        ctx.on_device_changed(self._on_device_changed)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttkb.Label(toolbar, text="Device Screenshot", font=("Segoe UI", 14, "bold")).pack(side="left")
        self.capture_btn = ttkb.Button(toolbar, text="📷 Capture", bootstyle="success", command=self._capture_once)
        self.capture_btn.pack(side="right", padx=2)
        self.save_btn = ttkb.Button(toolbar, text="💾 Save As…", bootstyle="info-outline", command=self._save, state="disabled")
        self.save_btn.pack(side="right", padx=2)

        live_bar = ttk.Frame(self)
        live_bar.pack(fill="x", pady=(0, 6))
        self.live_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(
            live_bar, text="Live preview", variable=self.live_var, bootstyle="round-toggle",
            command=self._toggle_live,
        ).pack(side="left")
        ttkb.Label(live_bar, text="Interval (s):").pack(side="left", padx=(12, 4))
        self.interval_var = tk.StringVar(value="2")
        ttkb.Spinbox(
            live_bar, from_=1, to=30, textvariable=self.interval_var, width=5, command=self._on_interval_changed,
        ).pack(side="left")
        ttkb.Label(
            live_bar, text="(re-captures the full screen each tick - higher intervals use less USB/CPU)",
            bootstyle="secondary",
        ).pack(side="left", padx=(8, 0))

        self.status_label = ttkb.Label(self, text="No screenshot captured yet.", bootstyle="secondary")
        self.status_label.pack(fill="x", pady=(0, 4))

        self.busy_bar = ttkb.Progressbar(self, mode="indeterminate", bootstyle="info-striped")

        self.canvas_frame = ttkb.Labelframe(self, text="Preview", padding=4, bootstyle="primary")
        self.canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.canvas_frame, background="#0c0d10", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._render_preview())
        self.canvas.create_text(
            10, 10, anchor="nw", fill="#8a94a3", font=("Segoe UI", 10),
            text="Connect a device and click Capture (or enable Live preview).",
            tags=("placeholder",),
        )

        self._update_button_states()

    def _update_button_states(self) -> None:
        connected = self.ctx.is_connected
        self.capture_btn.configure(state="normal" if connected else "disabled")
        self.save_btn.configure(state="normal" if self._last_png_bytes else "disabled")

    def _on_device_changed(self, _device) -> None:
        self._stop_live()
        self.live_var.set(False)
        self._update_button_states()

    # -- one-shot capture ----------------------------------------------
    def _capture_once(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        serial = self.ctx.serial
        self.busy_bar.pack(fill="x", pady=(0, 4), before=self.canvas_frame)
        self.busy_bar.start(12)

        def task():
            return self.ctx.adb.capture_screenshot(serial)

        def done(png_bytes: bytes) -> None:
            self._stop_busy()
            self._apply_frame(png_bytes)

        def error(exc: BaseException) -> None:
            self._stop_busy()
            self.status_label.configure(text=f"Capture failed: {exc}", bootstyle="danger")
            Messagebox.show_error(f"Could not capture screenshot:\n{exc}", title="Capture failed", parent=self.ctx.root)

        run_async(self.ctx.root, task, done, error)

    def _stop_busy(self) -> None:
        try:
            self.busy_bar.stop()
            self.busy_bar.pack_forget()
        except tk.TclError:
            pass

    def _apply_frame(self, png_bytes: bytes) -> None:
        self._last_png_bytes = png_bytes
        self._last_captured_at = datetime.now()
        self.status_label.configure(
            text=f"Captured {len(png_bytes) // 1024} KB at {self._last_captured_at.strftime('%H:%M:%S')}",
            bootstyle="success",
        )
        self._render_preview()
        self._update_button_states()

    def _render_preview(self) -> None:
        if not self._last_png_bytes:
            return
        try:
            image = Image.open(io.BytesIO(self._last_png_bytes))
            image.load()
        except Exception:  # noqa: BLE001 - a corrupt/partial frame just skips this redraw
            return
        canvas_w = max(self.canvas.winfo_width(), 100)
        canvas_h = max(self.canvas.winfo_height(), 100)
        display_image = image.copy()
        display_image.thumbnail((max(canvas_w - 8, 50), max(canvas_h - 8, 50)), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(display_image)
        self.canvas.delete("all")
        self.canvas.create_image(canvas_w // 2, canvas_h // 2, image=self._photo, anchor="center")

    # -- save --------------------------------------------------------------
    def _save(self) -> None:
        if not self._last_png_bytes:
            return
        default_name = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = filedialog.asksaveasfilename(
            title="Save screenshot", defaultextension=".png", initialfile=default_name,
            filetypes=[("PNG image", "*.png")],
        )
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(self._last_png_bytes)
            self.ctx.notify_status(f"Saved screenshot to {path}", "success")
            Messagebox.show_info(f"Saved to:\n{path}", title="Screenshot saved", parent=self.ctx.root)
        except OSError as exc:
            Messagebox.show_error(f"Could not save file:\n{exc}", title="Save failed", parent=self.ctx.root)

    # -- live preview --------------------------------------------------
    def _toggle_live(self) -> None:
        if self.live_var.get():
            self._start_live()
        else:
            self._stop_live()

    def _on_interval_changed(self) -> None:
        if self._poller is not None:
            self._poller.set_interval(self._interval_ms())

    def _interval_ms(self) -> int:
        try:
            return max(1, int(float(self.interval_var.get()))) * 1000
        except ValueError:
            return 2000

    def _start_live(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            self.live_var.set(False)
            return
        self._stop_live()
        serial = self.ctx.serial
        self._poller = Poller(
            self.ctx.root, self._interval_ms(), lambda: self.ctx.adb.capture_screenshot(serial),
            self._on_live_result, self._on_live_error,
        )
        self._poller.start()

    def _on_live_result(self, png_bytes: bytes) -> None:
        self._apply_frame(png_bytes)
        self.status_label.configure(
            text=f"Live - last frame {self._last_captured_at.strftime('%H:%M:%S')} ({len(png_bytes) // 1024} KB)",
            bootstyle="success",
        )

    def _on_live_error(self, exc: BaseException) -> None:
        self.status_label.configure(text=f"Live preview error: {exc}", bootstyle="danger")

    def _stop_live(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller = None

    def shutdown(self) -> None:
        self._stop_live()
