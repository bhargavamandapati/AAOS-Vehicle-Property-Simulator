"""APK Install tab: standard `adb install` for regular apps, a
root/remount/push/reboot workflow for privileged system apps and RRO
(Runtime Resource Overlay) APKs that can't be installed normally, and a
packages/overlays browser for verifying the result.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional, Tuple

import ttkbootstrap as ttkb
from ttkbootstrap.dialogs import Messagebox

from app.apk_tools import (
    PUSH_TARGET_PRESETS,
    build_install_flags,
    parse_overlay_list,
    parse_package_list,
    suggest_target_path,
)
from app.gui.context import AppContext
from app.gui.widgets import ScrollableFrame, debounce
from app.utils.workers import run_async

STEP_STYLE = {
    "pending": "PENDING", "running": "RUNNING", "pass": "PASS",
    "fail": "FAIL", "stopped": "STOPPED",
}


# ---------------------------------------------------------------------
# Quick Install
# ---------------------------------------------------------------------
class QuickInstallPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.apk_paths: List[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        ttkb.Label(self, text="Standard Install", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttkb.Label(
            self,
            text="For regular apps - runs `adb install`. Select more than one file for a "
                 "split-APK bundle (uses `adb install-multiple`).",
            bootstyle="secondary", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        file_row = ttk.Frame(self)
        file_row.pack(fill="x", pady=4)
        ttkb.Button(file_row, text="📁 Browse APK(s)…", bootstyle="info-outline", command=self._browse).pack(side="left")
        self.files_label = ttkb.Label(file_row, text="No file selected", bootstyle="secondary")
        self.files_label.pack(side="left", padx=8)

        options_frame = ttkb.Labelframe(self, text="Install Options", padding=10, bootstyle="primary")
        options_frame.pack(fill="x", pady=8)
        self.replace_var = tk.BooleanVar(value=True)
        self.grant_var = tk.BooleanVar(value=False)
        self.test_var = tk.BooleanVar(value=False)
        self.downgrade_var = tk.BooleanVar(value=False)
        for text, var in (
            ("Replace existing app (-r)", self.replace_var),
            ("Grant all runtime permissions (-g)", self.grant_var),
            ("Allow test packages (-t)", self.test_var),
            ("Allow version downgrade (-d)", self.downgrade_var),
        ):
            ttkb.Checkbutton(options_frame, text=text, variable=var, bootstyle="round-toggle").pack(anchor="w", pady=2)
            var.trace_add("write", lambda *_a: self._update_preview())

        ttkb.Label(self, text="ADB command (for reference):").pack(anchor="w", pady=(8, 2))
        self.command_preview = tk.Text(self, height=2, wrap="none", font=("Consolas", 8))
        self.command_preview.pack(fill="x")
        try:
            colors = ttkb.Style().colors
            self.command_preview.configure(background=colors.bg, foreground=colors.fg, insertbackground=colors.fg)
        except Exception:  # noqa: BLE001 - purely cosmetic
            pass
        self.command_preview.configure(state="disabled")

        action_row = ttk.Frame(self)
        action_row.pack(fill="x", pady=(10, 4))
        self.install_btn = ttkb.Button(action_row, text="⬇ Install", bootstyle="success", command=self._install)
        self.install_btn.pack(side="left")
        self.busy_bar = ttkb.Progressbar(action_row, mode="indeterminate", bootstyle="info-striped")

        ttkb.Label(self, text="Result:").pack(anchor="w", pady=(8, 2))
        self.result_text = ttkb.ScrolledText(self, height=12, auto_hide=True, wrap="word", font=("Consolas", 9))
        self.result_text.pack(fill="both", expand=True)
        self.result_text.text.configure(state="disabled")

        self._update_preview()

    def _current_flags(self) -> List[str]:
        return build_install_flags(self.replace_var.get(), self.grant_var.get(), self.test_var.get(), self.downgrade_var.get())

    def _update_preview(self) -> None:
        if not hasattr(self, "command_preview"):
            return
        flags = " ".join(self._current_flags())
        serial = self.ctx.serial or "<no device selected>"
        adb_path = self.ctx.adb.adb_path or "adb"
        if not self.apk_paths:
            cmd = f'"{adb_path}" -s {serial} install {flags} <select an APK first>'
        elif len(self.apk_paths) > 1:
            paths = " ".join(f'"{p}"' for p in self.apk_paths)
            cmd = f'"{adb_path}" -s {serial} install-multiple {flags} {paths}'
        else:
            cmd = f'"{adb_path}" -s {serial} install {flags} "{self.apk_paths[0]}"'
        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("end", cmd)
        self.command_preview.configure(state="disabled")

    def _browse(self) -> None:
        paths = filedialog.askopenfilenames(title="Select APK(s)", filetypes=[("APK files", "*.apk")])
        if not paths:
            return
        self.apk_paths = list(paths)
        if len(self.apk_paths) == 1:
            self.files_label.configure(text=Path(self.apk_paths[0]).name)
        else:
            self.files_label.configure(text=f"{len(self.apk_paths)} files selected (split APK bundle)")
        self._update_preview()

    def _write_result(self, text: str) -> None:
        widget = self.result_text.text
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.configure(state="disabled")
        widget.see("end")

    def _stop_busy(self) -> None:
        try:
            self.busy_bar.stop()
            self.busy_bar.pack_forget()
        except tk.TclError:
            pass

    def _install(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        if not self.apk_paths:
            Messagebox.show_warning("Select at least one APK file first.", title="No file selected", parent=self.ctx.root)
            return
        serial = self.ctx.serial
        paths = list(self.apk_paths)
        flags = self._current_flags()
        self.install_btn.configure(state="disabled")
        self.busy_bar.pack(side="left", padx=8)
        self.busy_bar.start(12)
        self._write_result(f"\nInstalling {len(paths)} file(s)…\n")

        def task():
            return self.ctx.adb.install(serial, paths, flags)

        def done(result) -> None:
            self._stop_busy()
            self.install_btn.configure(state="normal")
            self._write_result(result.combined.strip() or "(no output)")
            if result.ok:
                self._write_result("\n\n✓ Install succeeded.\n")
                self.ctx.notify_status("APK installed", "success")
            else:
                self._write_result("\n\n✗ Install failed.\n")
                self.ctx.notify_status("APK install failed", "error")
                Messagebox.show_error(
                    f"Install failed:\n{result.combined.strip()[:500]}", title="Install failed", parent=self.ctx.root,
                )

        def error(exc: BaseException) -> None:
            self._stop_busy()
            self.install_btn.configure(state="normal")
            self._write_result(f"\nError: {exc}\n")
            Messagebox.show_error(f"Could not run install:\n{exc}", title="Install error", parent=self.ctx.root)

        run_async(self.ctx.root, task, done, error)


# ---------------------------------------------------------------------
# Push & System Workflow
# ---------------------------------------------------------------------
@dataclass
class PushEntry:
    local_path: str
    target_path: str
    package_name: str = ""


@dataclass
class WorkflowOptions:
    do_root: bool = True
    do_remount: bool = True
    do_chmod: bool = True
    do_reboot: bool = True
    do_wait: bool = True
    do_enable_overlay: bool = False
    stop_on_failure: bool = True


class PushWorkflowRunner:
    """Root -> remount -> push each APK (+ optional chmod) -> reboot ->
    wait for device -> optionally enable overlays, as one sequential,
    stoppable, step-by-step job. Mirrors testing_tab.py's ScenarioRunner
    pattern deliberately, for consistency."""

    def __init__(
        self, ctx: AppContext, serial: str, entries: List[PushEntry], options: WorkflowOptions,
        result_queue: "queue.Queue", stop_event: threading.Event,
    ) -> None:
        self.ctx = ctx
        self.serial = serial
        self.entries = entries
        self.options = options
        self.result_queue = result_queue
        self.stop_event = stop_event

    def build_steps(self) -> List[Tuple[str, Callable[[], Tuple[bool, str]]]]:
        steps: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = []
        if self.options.do_root:
            steps.append(("Root device (adb root)", self._step_root))
        if self.options.do_remount:
            steps.append(("Remount /system read-write (adb remount)", self._step_remount))
        for entry in self.entries:
            name = Path(entry.local_path).name
            steps.append((f"Push {name} -> {entry.target_path}", lambda e=entry: self._step_push(e)))
            if self.options.do_chmod:
                steps.append((f"chmod 644 {entry.target_path}", lambda e=entry: self._step_chmod(e)))
        if self.options.do_reboot:
            steps.append(("Reboot device (adb reboot)", self._step_reboot))
        if self.options.do_wait:
            steps.append(("Wait for device to come back online", self._step_wait))
        if self.options.do_enable_overlay:
            for entry in self.entries:
                if entry.package_name:
                    steps.append((f"Enable overlay {entry.package_name}", lambda e=entry: self._step_enable_overlay(e)))
        return steps

    def run(self) -> None:
        steps = self.build_steps()
        for idx, (label, fn) in enumerate(steps):
            if self.stop_event.is_set():
                self.result_queue.put(("stopped", idx, label, "Stopped by user"))
                return
            self.result_queue.put(("running", idx, label, ""))
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001 - surfaced as a failed step, not a crash
                ok, detail = False, str(exc)
            self.result_queue.put(("pass" if ok else "fail", idx, label, detail))
            if not ok and self.options.stop_on_failure:
                self.result_queue.put(("aborted", -1, "", f"Stopped after step {idx + 1} failed"))
                return
        self.result_queue.put(("done", -1, "", ""))

    def _step_root(self) -> Tuple[bool, str]:
        result = self.ctx.adb.root(self.serial)
        return result.ok, result.combined.strip()[:300] or "OK"

    def _step_remount(self) -> Tuple[bool, str]:
        result = self.ctx.adb.remount(self.serial)
        return result.ok, result.combined.strip()[:300] or "OK"

    def _step_push(self, entry: PushEntry) -> Tuple[bool, str]:
        result = self.ctx.adb.push(self.serial, entry.local_path, entry.target_path)
        return result.ok, result.combined.strip()[:300] or "OK"

    def _step_chmod(self, entry: PushEntry) -> Tuple[bool, str]:
        result = self.ctx.car.run_custom_shell(self.serial, f"chmod 644 {entry.target_path}")
        return result.ok, result.combined.strip()[:300] or "OK"

    def _step_reboot(self) -> Tuple[bool, str]:
        result = self.ctx.adb.reboot(self.serial)
        return result.ok, result.combined.strip()[:300] or "Reboot command sent"

    def _step_wait(self) -> Tuple[bool, str]:
        result = self.ctx.adb.wait_for_device(self.serial, timeout=150)
        return result.ok, "Device is back online" if result.ok else (result.combined.strip()[:300] or "Timed out")

    def _step_enable_overlay(self, entry: PushEntry) -> Tuple[bool, str]:
        result = self.ctx.car.run_custom_shell(self.serial, f"cmd overlay enable {entry.package_name}")
        return result.ok, result.combined.strip()[:300] or "Enabled"


class PushWorkflowPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.entries: List[PushEntry] = []
        self._running = False
        self._stop_event: Optional[threading.Event] = None
        self._result_queue: Optional["queue.Queue"] = None
        self._build_ui()

    def _build_ui(self) -> None:
        ttkb.Label(self, text="Push & System Install Workflow", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttkb.Label(
            self,
            text="For privileged/system apps and RRO (Runtime Resource Overlay) APKs that can't be "
                 "installed with `adb install` - pushes straight to a system partition instead. "
                 "Requires a userdebug/eng build. Default steps: root -> remount -> push each APK -> "
                 "reboot -> wait for device.",
            bootstyle="secondary", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 10))

        # This panel has a lot of stacked content (entry form, entries
        # table, step toggles, run controls, step results) - scrollable
        # so nothing gets squeezed into invisibility on a shorter window,
        # same pattern as Dashboard/Settings. Every widget below is
        # parented to `content` (the scroll area), not `self` - but
        # `self.xxx = ...` assignments still correctly target this
        # instance, since `self` itself is never reassigned.
        scroll = ScrollableFrame(self)
        scroll.pack(fill="both", expand=True)
        content = scroll.inner

        add_frame = ttkb.Labelframe(content, text="Add APK to push", padding=10, bootstyle="primary")
        add_frame.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(add_frame)
        row1.pack(fill="x", pady=2)
        ttkb.Button(row1, text="📁 Browse…", bootstyle="info-outline", command=self._browse_local).pack(side="left")
        self.local_var = tk.StringVar()
        ttkb.Entry(row1, textvariable=self.local_var, width=55).pack(side="left", padx=4, fill="x", expand=True)

        row2 = ttk.Frame(add_frame)
        row2.pack(fill="x", pady=2)
        ttkb.Label(row2, text="Target path:", width=12).pack(side="left")
        self.target_var = tk.StringVar()
        self.target_combo = ttkb.Combobox(row2, textvariable=self.target_var, values=PUSH_TARGET_PRESETS, width=32)
        self.target_combo.pack(side="left", padx=4)
        self.target_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttkb.Label(row2, text="Package (optional):", width=16).pack(side="left", padx=(12, 0))
        self.package_var = tk.StringVar()
        ttkb.Entry(row2, textvariable=self.package_var, width=26).pack(side="left", padx=4)
        ttkb.Button(row2, text="+ Add to List", bootstyle="success-outline", command=self._add_entry).pack(side="left", padx=8)

        ttkb.Label(
            add_frame, text="Package is only needed if you'll enable it as an overlay after reboot below.",
            bootstyle="secondary",
        ).pack(anchor="w", pady=(4, 0))

        list_row = ttk.Frame(content)
        list_row.pack(fill="x", pady=(0, 4))
        ttkb.Label(list_row, text="APKs to push:").pack(side="left")
        ttkb.Button(list_row, text="🗑 Remove Selected", bootstyle="danger-outline", command=self._remove_selected).pack(side="right")

        entries_columns = ("local", "target", "package")
        self.entries_tree = ttk.Treeview(content, columns=entries_columns, show="headings", height=4)
        for col, text, width in (("local", "Local File", 320), ("target", "Target Path", 320), ("package", "Package", 220)):
            self.entries_tree.heading(col, text=text)
            self.entries_tree.column(col, width=width, anchor="w")
        self.entries_tree.pack(fill="x", pady=(0, 8))

        options_frame = ttkb.Labelframe(content, text="Workflow Steps", padding=10, bootstyle="primary")
        options_frame.pack(fill="x", pady=(0, 8))
        self.root_var = tk.BooleanVar(value=True)
        self.remount_var = tk.BooleanVar(value=True)
        self.chmod_var = tk.BooleanVar(value=True)
        self.reboot_var = tk.BooleanVar(value=True)
        self.wait_var = tk.BooleanVar(value=True)
        self.enable_overlay_var = tk.BooleanVar(value=False)
        self.stop_on_failure_var = tk.BooleanVar(value=True)
        left = ttk.Frame(options_frame)
        left.pack(side="left", fill="x", expand=True)
        right = ttk.Frame(options_frame)
        right.pack(side="left", fill="x", expand=True)
        for text, var in (
            ("Root device first (adb root)", self.root_var),
            ("chmod 644 after each push", self.chmod_var),
            ("Wait for device after reboot", self.wait_var),
            ("Stop workflow on first failure", self.stop_on_failure_var),
        ):
            ttkb.Checkbutton(left, text=text, variable=var, bootstyle="round-toggle").pack(anchor="w", pady=2)
        for text, var in (
            ("Remount /system read-write", self.remount_var),
            ("Reboot after all pushes", self.reboot_var),
            ("Enable overlay after (uses Package field)", self.enable_overlay_var),
        ):
            ttkb.Checkbutton(right, text=text, variable=var, bootstyle="round-toggle").pack(anchor="w", pady=2)

        run_row = ttk.Frame(content)
        run_row.pack(fill="x", pady=(0, 6))
        self.run_btn = ttkb.Button(run_row, text="▶ Run Workflow", bootstyle="success", command=self._run_workflow)
        self.run_btn.pack(side="left")
        self.stop_btn = ttkb.Button(run_row, text="■ Stop", bootstyle="danger-outline", command=self._stop_workflow, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttkb.Button(
            run_row, text="⚠ Disable Verity & Reboot…", bootstyle="warning-outline", command=self._disable_verity,
        ).pack(side="right")

        ttkb.Label(content, text="Steps:").pack(anchor="w")
        steps_columns = ("idx", "step", "status", "detail")
        self.steps_tree = ttk.Treeview(content, columns=steps_columns, show="headings", height=10)
        for col, text, width in (("idx", "#", 30), ("step", "Step", 320), ("status", "Status", 90), ("detail", "Detail", 380)):
            self.steps_tree.heading(col, text=text)
            self.steps_tree.column(col, width=width, anchor="w")
        self.steps_tree.pack(fill="x", pady=(0, 8))
        self.steps_tree.tag_configure("pass", background="#173d1a")
        self.steps_tree.tag_configure("fail", background="#4a1414")
        self.steps_tree.tag_configure("running", background="#4a3b12")
        self.steps_tree.tag_configure("stopped", background="#2a2a2a")

    # -- add/remove entries ----------------------------------------------
    def _browse_local(self) -> None:
        path = filedialog.askopenfilename(title="Select APK", filetypes=[("APK files", "*.apk")])
        if not path:
            return
        self.local_var.set(path)
        current_target = self.target_var.get().strip()
        if not current_target or current_target.endswith("/"):
            self.target_var.set(suggest_target_path(current_target or PUSH_TARGET_PRESETS[0], path))

    def _on_preset_selected(self, _event=None) -> None:
        preset = self.target_var.get()
        if self.local_var.get():
            self.target_var.set(suggest_target_path(preset, self.local_var.get()))

    def _add_entry(self) -> None:
        local = self.local_var.get().strip()
        target = self.target_var.get().strip()
        if not local or not target:
            Messagebox.show_warning(
                "Choose a local APK file and a target path first.", title="Missing info", parent=self.ctx.root,
            )
            return
        self.entries.append(PushEntry(local_path=local, target_path=target, package_name=self.package_var.get().strip()))
        self._refresh_entries_tree()
        self.local_var.set("")
        self.target_var.set("")
        self.package_var.set("")

    def _remove_selected(self) -> None:
        selection = self.entries_tree.selection()
        if not selection:
            return
        indices = sorted((int(i) for i in selection), reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.entries):
                del self.entries[idx]
        self._refresh_entries_tree()

    def _refresh_entries_tree(self) -> None:
        self.entries_tree.delete(*self.entries_tree.get_children())
        for idx, entry in enumerate(self.entries):
            self.entries_tree.insert(
                "", "end", iid=str(idx),
                values=(Path(entry.local_path).name, entry.target_path, entry.package_name or "-"),
            )

    # -- run workflow ---------------------------------------------------
    def _run_workflow(self) -> None:
        if self._running:
            return
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        if not self.entries:
            Messagebox.show_warning("Add at least one APK to push first.", title="Nothing to push", parent=self.ctx.root)
            return

        options = WorkflowOptions(
            do_root=self.root_var.get(), do_remount=self.remount_var.get(), do_chmod=self.chmod_var.get(),
            do_reboot=self.reboot_var.get(), do_wait=self.wait_var.get(),
            do_enable_overlay=self.enable_overlay_var.get(), stop_on_failure=self.stop_on_failure_var.get(),
        )
        serial = self.ctx.serial
        entries = list(self.entries)
        self._stop_event = threading.Event()
        self._result_queue = queue.Queue()
        runner = PushWorkflowRunner(self.ctx, serial, entries, options, self._result_queue, self._stop_event)

        steps = runner.build_steps()
        if not steps:
            Messagebox.show_warning(
                "No steps enabled - turn on at least one workflow step.", title="Nothing to do", parent=self.ctx.root,
            )
            return
        self.steps_tree.delete(*self.steps_tree.get_children())
        for idx, (label, _fn) in enumerate(steps):
            self.steps_tree.insert("", "end", iid=str(idx), values=(idx + 1, label, "PENDING", ""))

        self._running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.ctx.notify_status("Push workflow started…", "info")
        threading.Thread(target=runner.run, daemon=True).start()
        self._poll_workflow()

    def _poll_workflow(self) -> None:
        try:
            while True:
                status, idx, label, detail = self._result_queue.get_nowait()
                if status == "done":
                    self._finish_workflow(success=True)
                    return
                if status == "aborted":
                    self._finish_workflow(success=False)
                    Messagebox.show_error(f"Workflow stopped: {detail}", title="Workflow failed", parent=self.ctx.root)
                    return
                if status == "stopped":
                    self._finish_workflow(success=False)
                    return
                if str(idx) in self.steps_tree.get_children():
                    self.steps_tree.item(str(idx), values=(idx + 1, label, STEP_STYLE.get(status, status.upper()), detail), tags=(status,))
        except queue.Empty:
            pass
        if self._running:
            self.after(200, self._poll_workflow)

    def _finish_workflow(self, success: bool) -> None:
        self._running = False
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if success:
            self.ctx.notify_status("Push workflow completed", "success")
            Messagebox.show_info("Workflow completed successfully.", title="Workflow complete", parent=self.ctx.root)
        else:
            self.ctx.notify_status("Push workflow stopped", "warning")

    def _stop_workflow(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    # -- disable-verity utility ------------------------------------------
    def _disable_verity(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        answer = Messagebox.yesno(
            "This runs `adb disable-verity` and then reboots the device - only do this on a "
            "test/dev build. Use this only if a plain `adb remount` told you verity needs to "
            "be disabled first. Continue?",
            title="Disable Verity & Reboot", parent=self.ctx.root, localize=False,
        )
        if answer != "Yes":
            return
        serial = self.ctx.serial
        self.ctx.notify_status("Disabling verity and rebooting…", "info")

        def task():
            verity_result = self.ctx.adb.disable_verity(serial)
            reboot_result = self.ctx.adb.reboot(serial) if verity_result.ok else None
            return verity_result, reboot_result

        def done(result) -> None:
            verity_result, reboot_result = result
            if not verity_result.ok:
                Messagebox.show_error(
                    f"disable-verity failed:\n{verity_result.combined.strip()[:400]}",
                    title="Disable Verity failed", parent=self.ctx.root,
                )
                return
            self.ctx.notify_status("Verity disabled, device rebooting - reconnect and remount when it's back.", "success")

        run_async(self.ctx.root, task, done, lambda exc: Messagebox.show_error(str(exc), title="Error", parent=self.ctx.root))

    def shutdown(self) -> None:
        self._stop_workflow()


# ---------------------------------------------------------------------
# Packages & Overlays
# ---------------------------------------------------------------------
class PackagesOverlaysPanel(ttk.Frame):
    SCOPE_FLAGS = {"All": "", "Third-party (-3)": "-3", "System (-s)": "-s"}

    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.packages: List[str] = []
        self.overlays = []
        self._build_ui()

    def _build_ui(self) -> None:
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        pkg_frame = ttk.Frame(paned, padding=(0, 0, 0, 4))
        pkg_toolbar = ttk.Frame(pkg_frame)
        pkg_toolbar.pack(fill="x", pady=(0, 4))
        ttkb.Label(pkg_toolbar, text="Installed Packages", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.scope_var = tk.StringVar(value="Third-party (-3)")
        ttkb.Combobox(
            pkg_toolbar, textvariable=self.scope_var, values=list(self.SCOPE_FLAGS.keys()), state="readonly", width=16,
        ).pack(side="left", padx=8)
        ttkb.Button(pkg_toolbar, text="⟳ List", bootstyle="info-outline", command=self._list_packages).pack(side="left", padx=2)
        ttkb.Button(pkg_toolbar, text="🗑 Uninstall Selected", bootstyle="danger-outline", command=self._uninstall_selected).pack(side="left", padx=2)
        ttkb.Label(pkg_toolbar, text="Search:").pack(side="left", padx=(12, 4))
        self.pkg_search_var = tk.StringVar()
        ttkb.Entry(pkg_toolbar, textvariable=self.pkg_search_var, width=24).pack(side="left")
        self.pkg_search_var.trace_add("write", debounce(self, 200, self._apply_pkg_filter))
        self.pkg_count_label = ttkb.Label(pkg_toolbar, text="0 packages", bootstyle="secondary")
        self.pkg_count_label.pack(side="right")

        pkg_table = ttk.Frame(pkg_frame)
        pkg_table.pack(fill="both", expand=True)
        self.pkg_tree = ttk.Treeview(pkg_table, columns=("package",), show="headings")
        self.pkg_tree.heading("package", text="Package")
        self.pkg_tree.column("package", width=600, anchor="w")
        pkg_vsb = ttk.Scrollbar(pkg_table, orient="vertical", command=self.pkg_tree.yview)
        self.pkg_tree.configure(yscrollcommand=pkg_vsb.set)
        self.pkg_tree.grid(row=0, column=0, sticky="nsew")
        pkg_vsb.grid(row=0, column=1, sticky="ns")
        pkg_table.rowconfigure(0, weight=1)
        pkg_table.columnconfigure(0, weight=1)
        paned.add(pkg_frame, weight=1)

        overlay_frame = ttk.Frame(paned, padding=(0, 4, 0, 0))
        overlay_toolbar = ttk.Frame(overlay_frame)
        overlay_toolbar.pack(fill="x", pady=(0, 4))
        ttkb.Label(overlay_toolbar, text="RRO Overlays", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttkb.Button(overlay_toolbar, text="⟳ List Overlays", bootstyle="info-outline", command=self._list_overlays).pack(side="left", padx=8)
        ttkb.Button(overlay_toolbar, text="✓ Enable Selected", bootstyle="success-outline", command=lambda: self._set_overlay(True)).pack(side="left", padx=2)
        ttkb.Button(overlay_toolbar, text="✗ Disable Selected", bootstyle="warning-outline", command=lambda: self._set_overlay(False)).pack(side="left", padx=2)
        self.overlay_count_label = ttkb.Label(overlay_toolbar, text="0 overlays", bootstyle="secondary")
        self.overlay_count_label.pack(side="right")

        overlay_table = ttk.Frame(overlay_frame)
        overlay_table.pack(fill="both", expand=True)
        overlay_columns = ("enabled", "package", "target")
        self.overlay_tree = ttk.Treeview(overlay_table, columns=overlay_columns, show="headings")
        for col, text, width in (("enabled", "Enabled", 70), ("package", "Overlay Package", 380), ("target", "Target Package", 300)):
            self.overlay_tree.heading(col, text=text)
            self.overlay_tree.column(col, width=width, anchor="w")
        overlay_vsb = ttk.Scrollbar(overlay_table, orient="vertical", command=self.overlay_tree.yview)
        self.overlay_tree.configure(yscrollcommand=overlay_vsb.set)
        self.overlay_tree.grid(row=0, column=0, sticky="nsew")
        overlay_vsb.grid(row=0, column=1, sticky="ns")
        overlay_table.rowconfigure(0, weight=1)
        overlay_table.columnconfigure(0, weight=1)
        self.overlay_tree.tag_configure("enabled", foreground="#66bb6a")
        paned.add(overlay_frame, weight=1)

    # -- packages ------------------------------------------------------
    def _list_packages(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        serial = self.ctx.serial
        flag = self.SCOPE_FLAGS.get(self.scope_var.get(), "")
        cmd = f"pm list packages {flag}".strip()

        def task():
            return self.ctx.car.run_custom_shell(serial, cmd, timeout=20)

        def done(result) -> None:
            if not result.ok:
                Messagebox.show_error(f"Failed to list packages:\n{result.combined.strip()[:400]}", title="List failed", parent=self.ctx.root)
                return
            self.packages = parse_package_list(result.combined)
            self._apply_pkg_filter()
            self.ctx.notify_status(f"Found {len(self.packages)} packages", "success")

        run_async(self.ctx.root, task, done, lambda exc: Messagebox.show_error(str(exc), title="Error", parent=self.ctx.root))

    def _apply_pkg_filter(self) -> None:
        query = self.pkg_search_var.get().strip().lower()
        rows = [p for p in self.packages if query in p.lower()] if query else self.packages
        self.pkg_tree.delete(*self.pkg_tree.get_children())
        for pkg in rows:
            self.pkg_tree.insert("", "end", values=(pkg,))
        self.pkg_count_label.configure(text=f"{len(rows)} / {len(self.packages)} packages")

    def _uninstall_selected(self) -> None:
        selection = self.pkg_tree.selection()
        if not selection:
            Messagebox.show_warning("Select a package in the list first.", title="Nothing selected", parent=self.ctx.root)
            return
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        package = self.pkg_tree.item(selection[0], "values")[0]
        answer = Messagebox.yesno(f"Uninstall {package}?", title="Confirm Uninstall", parent=self.ctx.root, localize=False)
        if answer != "Yes":
            return
        serial = self.ctx.serial

        def task():
            return self.ctx.adb.uninstall(serial, package)

        def done(result) -> None:
            if result.ok:
                self.ctx.notify_status(f"Uninstalled {package}", "success")
                self._list_packages()
            else:
                Messagebox.show_error(f"Uninstall failed:\n{result.combined.strip()[:400]}", title="Uninstall failed", parent=self.ctx.root)

        run_async(self.ctx.root, task, done, lambda exc: Messagebox.show_error(str(exc), title="Error", parent=self.ctx.root))

    # -- overlays --------------------------------------------------------
    def _list_overlays(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        serial = self.ctx.serial

        def task():
            return self.ctx.car.run_custom_shell(serial, "cmd overlay list", timeout=20)

        def done(result) -> None:
            if not result.ok:
                Messagebox.show_error(f"Failed to list overlays:\n{result.combined.strip()[:400]}", title="List failed", parent=self.ctx.root)
                return
            self.overlays = parse_overlay_list(result.combined)
            self._refresh_overlay_tree()
            self.ctx.notify_status(f"Found {len(self.overlays)} overlays", "success")

        run_async(self.ctx.root, task, done, lambda exc: Messagebox.show_error(str(exc), title="Error", parent=self.ctx.root))

    def _refresh_overlay_tree(self) -> None:
        self.overlay_tree.delete(*self.overlay_tree.get_children())
        for overlay in self.overlays:
            tags = ("enabled",) if overlay.enabled else ()
            self.overlay_tree.insert(
                "", "end", values=("✓" if overlay.enabled else "-", overlay.package, overlay.target_package), tags=tags,
            )
        self.overlay_count_label.configure(text=f"{len(self.overlays)} overlays")

    def _set_overlay(self, enable: bool) -> None:
        selection = self.overlay_tree.selection()
        if not selection:
            Messagebox.show_warning("Select an overlay in the list first.", title="Nothing selected", parent=self.ctx.root)
            return
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        package = self.overlay_tree.item(selection[0], "values")[1]
        serial = self.ctx.serial
        verb = "enable" if enable else "disable"

        def task():
            return self.ctx.car.run_custom_shell(serial, f"cmd overlay {verb} {package}")

        def done(result) -> None:
            if result.ok:
                self.ctx.notify_status(f"{'Enabled' if enable else 'Disabled'} {package}", "success")
                self._list_overlays()
            else:
                Messagebox.show_error(f"Failed to {verb} overlay:\n{result.combined.strip()[:400]}", title="Overlay change failed", parent=self.ctx.root)

        run_async(self.ctx.root, task, done, lambda exc: Messagebox.show_error(str(exc), title="Error", parent=self.ctx.root))


# ---------------------------------------------------------------------
# Top-level tab
# ---------------------------------------------------------------------
class ApkInstallTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 6))
        ttkb.Label(header, text="APK Install", font=("Segoe UI", 14, "bold")).pack(side="left")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.quick_install_panel = QuickInstallPanel(notebook, ctx)
        notebook.add(self.quick_install_panel, text="Quick Install")

        self.push_workflow_panel = PushWorkflowPanel(notebook, ctx)
        notebook.add(self.push_workflow_panel, text="Push & System Workflow")

        self.packages_overlays_panel = PackagesOverlaysPanel(notebook, ctx)
        notebook.add(self.packages_overlays_panel, text="Packages & Overlays")

    def shutdown(self) -> None:
        self.push_workflow_panel.shutdown()
