"""Settings tab: ADB path override, appearance, command templates, and
performance-related buffer/interval knobs - all persisted via app.config.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Dict

import ttkbootstrap as ttkb

from app import __version__
from app.config import CONFIG_DIR, DEFAULT_COMMAND_TEMPLATES, config
from app.gui.context import AppContext
from app.gui.theme import AVAILABLE_THEMES, apply_notebook_theme, apply_treeview_theme
from app.gui.widgets import ScrollableFrame
from app.persistent_log import LOG_DIR
from app.utils.fs import open_in_file_manager
from app.utils.workers import run_async

TEMPLATE_LABELS = {
    "get_property": "Get property value",
    "set_property": "Set property value",
    "inject_event": "Inject VHAL event",
    "inject_error": "Inject error event",
    "dump_properties": "List properties (short)",
    "dump_full": "Full CarService dump (used to discover properties)",
}


class SettingsTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext, style) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.style = style
        self.template_vars: Dict[str, tk.StringVar] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        scroll = ScrollableFrame(self)
        scroll.pack(fill="both", expand=True)
        root = scroll.inner

        # -- ADB ------------------------------------------------------
        adb_frame = ttkb.Labelframe(root, text="ADB Connection", padding=10, bootstyle="primary")
        adb_frame.pack(fill="x", padx=4, pady=6)
        adb_frame.columnconfigure(1, weight=1)

        ttkb.Label(adb_frame, text="Detected adb path:").grid(row=0, column=0, sticky="w")
        self.detected_label = ttkb.Label(adb_frame, text=self.ctx.adb.adb_path or "(not found)", bootstyle="info")
        self.detected_label.grid(row=0, column=1, sticky="w", padx=6)

        ttkb.Label(adb_frame, text="Override path (optional):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.adb_path_var = tk.StringVar(value=config.get("adb_path", ""))
        ttkb.Entry(adb_frame, textvariable=self.adb_path_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttkb.Button(adb_frame, text="Browse…", bootstyle="info-outline", command=self._browse_adb).grid(
            row=1, column=2, padx=4, pady=(6, 0)
        )

        button_row = ttk.Frame(adb_frame)
        button_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttkb.Button(button_row, text="Apply & Re-detect", bootstyle="success-outline", command=self._apply_adb_path).pack(side="left", padx=2)
        ttkb.Button(button_row, text="Restart ADB Server", bootstyle="warning-outline", command=self._restart_server).pack(side="left", padx=2)
        ttkb.Button(button_row, text="Open Config Folder", bootstyle="info-outline", command=self._open_config_folder).pack(side="left", padx=2)
        ttkb.Button(button_row, text="Open Logs Folder", bootstyle="info-outline", command=self._open_logs_folder).pack(side="left", padx=2)
        self.adb_status_label = ttkb.Label(adb_frame, text="", bootstyle="secondary")
        self.adb_status_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttkb.Label(
            adb_frame,
            text=f"ADB command history and raw logcat are continuously written to: {LOG_DIR}",
            bootstyle="secondary", wraplength=760, justify="left",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # -- Appearance -------------------------------------------------
        appearance_frame = ttkb.Labelframe(root, text="Appearance", padding=10, bootstyle="primary")
        appearance_frame.pack(fill="x", padx=4, pady=6)
        ttkb.Label(appearance_frame, text="Theme:").pack(side="left")
        self.theme_var = tk.StringVar(value=config.get("theme", "darkly"))
        theme_combo = ttkb.Combobox(
            appearance_frame, textvariable=self.theme_var, values=AVAILABLE_THEMES, state="readonly", width=20,
        )
        theme_combo.pack(side="left", padx=6)
        ttkb.Button(appearance_frame, text="Apply Theme", bootstyle="success-outline", command=self._apply_theme).pack(side="left", padx=6)

        # -- Performance --------------------------------------------------
        perf_frame = ttkb.Labelframe(root, text="Performance & Buffers", padding=10, bootstyle="primary")
        perf_frame.pack(fill="x", padx=4, pady=6)
        self.buffer_var = tk.StringVar(value=str(config.get("logcat_buffer_lines", 4000)))
        self.device_poll_var = tk.StringVar(value=str(config.get("device_poll_seconds", 4)))
        self.prop_poll_var = tk.StringVar(value=str(config.get("property_poll_seconds", 3)))
        for row, (label, var, hint) in enumerate((
            ("Logcat buffer (lines)", self.buffer_var, "Older lines are trimmed beyond this to bound memory use."),
            ("Device list poll interval (s)", self.device_poll_var, "How often the device dropdown refreshes."),
            ("Auto property poll interval (s)", self.prop_poll_var, "0 disables automatic re-scanning."),
        )):
            ttkb.Label(perf_frame, text=label, width=28, anchor="w").grid(row=row, column=0, sticky="w", pady=2)
            ttkb.Entry(perf_frame, textvariable=var, width=8).grid(row=row, column=1, sticky="w", padx=6)
            ttkb.Label(perf_frame, text=hint, bootstyle="secondary").grid(row=row, column=2, sticky="w")
        ttkb.Button(perf_frame, text="Save Performance Settings", bootstyle="success-outline", command=self._save_perf).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        # -- Command templates -----------------------------------------
        cmd_frame = ttkb.Labelframe(
            root, text="ADB / CarService Command Templates", padding=10, bootstyle="primary",
        )
        cmd_frame.pack(fill="x", padx=4, pady=6)
        ttkb.Label(
            cmd_frame,
            text="`adb shell cmd car_service ...` sub-command names differ across Android Automotive OS "
                 "versions and OEM builds. If get/set/inject calls fail on your device, adjust the templates "
                 "below - placeholders {prop_id}, {area_id}, {value}, {error_code} are substituted at call time.",
            bootstyle="secondary", wraplength=760, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        for key, label in TEMPLATE_LABELS.items():
            row = ttk.Frame(cmd_frame)
            row.pack(fill="x", pady=2)
            ttkb.Label(row, text=label, width=32, anchor="w").pack(side="left")
            var = tk.StringVar(value=config.get_command_template(key))
            self.template_vars[key] = var
            ttkb.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)

        cmd_buttons = ttk.Frame(cmd_frame)
        cmd_buttons.pack(fill="x", pady=(8, 0))
        ttkb.Button(cmd_buttons, text="Save Templates", bootstyle="success-outline", command=self._save_templates).pack(side="left", padx=2)
        ttkb.Button(cmd_buttons, text="Reset to Defaults", bootstyle="danger-outline", command=self._reset_templates).pack(side="left", padx=2)

        # -- About ----------------------------------------------------------
        about_frame = ttkb.Labelframe(root, text="About", padding=10, bootstyle="primary")
        about_frame.pack(fill="x", padx=4, pady=6)
        ttkb.Label(
            about_frame, text=f"AAOS Vehicle Property Simulator v{__version__}",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttkb.Label(
            about_frame,
            text="See the ℹ️ About tab for developer info, the full auto-generated build "
                 "version, and a copyable version string for bug reports.",
            bootstyle="secondary",
        ).pack(anchor="w", pady=(4, 0))

    # -- handlers -----------------------------------------------------------
    def _browse_adb(self) -> None:
        path = filedialog.askopenfilename(title="Select adb executable")
        if path:
            self.adb_path_var.set(path)

    def _apply_adb_path(self) -> None:
        config.set("adb_path", self.adb_path_var.get().strip())
        config.save()
        new_path = self.ctx.adb.refresh_adb_path()
        self.detected_label.configure(text=new_path or "(not found)")
        self.adb_status_label.configure(
            text="adb located." if new_path else "adb still not found - check the path.",
            bootstyle="success" if new_path else "danger",
        )

    def _restart_server(self) -> None:
        self.adb_status_label.configure(text="Restarting adb server…", bootstyle="secondary")

        def task():
            self.ctx.adb.kill_server()
            return self.ctx.adb.start_server()

        def done(result) -> None:
            self.adb_status_label.configure(
                text="ADB server restarted." if result.ok else f"Failed: {result.combined.strip()[:150]}",
                bootstyle="success" if result.ok else "danger",
            )

        def error(exc: BaseException) -> None:
            self.adb_status_label.configure(text=f"Error: {exc}", bootstyle="danger")

        run_async(self.ctx.root, task, done, error)

    def _open_config_folder(self) -> None:
        try:
            open_in_file_manager(CONFIG_DIR)
        except OSError as exc:
            self.ctx.notify_status(f"Could not open config folder: {exc}", "error")

    def _open_logs_folder(self) -> None:
        try:
            open_in_file_manager(LOG_DIR)
        except OSError as exc:
            self.ctx.notify_status(f"Could not open logs folder: {exc}", "error")

    def _apply_theme(self) -> None:
        theme = self.theme_var.get()
        try:
            self.style.theme_use(theme)
        except tk.TclError as exc:
            self.ctx.notify_status(f"Could not apply theme: {exc}", "error")
            return
        apply_treeview_theme(self.style)
        apply_notebook_theme(self.style)
        config.set("theme", theme)
        config.save()
        self.ctx.notify_status(f"Theme changed to {theme}", "success")

    def _save_perf(self) -> None:
        try:
            buffer_lines = max(200, int(self.buffer_var.get()))
            device_poll = max(1, int(self.device_poll_var.get()))
            prop_poll = max(0, int(self.prop_poll_var.get()))
        except ValueError:
            self.ctx.notify_status("Performance settings must be whole numbers.", "error")
            return
        config.set("logcat_buffer_lines", buffer_lines)
        config.set("device_poll_seconds", device_poll)
        config.set("property_poll_seconds", prop_poll)
        config.save()
        self.ctx.notify_status("Performance settings saved. Restart the app for buffer size changes to fully apply.", "success")

    def _save_templates(self) -> None:
        for key, var in self.template_vars.items():
            config.set_command_template(key, var.get())
        config.save()
        self.ctx.notify_status("Command templates saved.", "success")

    def _reset_templates(self) -> None:
        config.reset_command_templates()
        for key, var in self.template_vars.items():
            var.set(DEFAULT_COMMAND_TEMPLATES[key])
        config.save()
        self.ctx.notify_status("Command templates reset to defaults.", "info")
