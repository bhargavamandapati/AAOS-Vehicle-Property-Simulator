"""Processes tab: live list of processes running on the device (from
`ps`), with per-process memory detail (`dumpsys meminfo <pid>`) that
refreshes for whichever process is currently selected.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Dict, List, Optional

import ttkbootstrap as ttkb
from ttkbootstrap.dialogs import Messagebox

from app.device_tools import PS_COMMAND, ProcessInfo, parse_meminfo_totals, parse_ps_output
from app.export_utils import EXPORT_FILETYPES, export_rows
from app.gui.context import AppContext
from app.gui.widgets import debounce
from app.utils.workers import Poller, run_async

COLUMNS = ("pid", "ppid", "user", "rss", "vsz", "name")
COLUMN_LABELS = {"pid": "PID", "ppid": "PPID", "user": "User", "rss": "RSS", "vsz": "VSZ", "name": "Name"}
COLUMN_WIDTHS = {"pid": 70, "ppid": 70, "user": 130, "rss": 100, "vsz": 100, "name": 380}


class ProcessesTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.all_processes: List[ProcessInfo] = []
        self._row_id_to_pid: Dict[str, ProcessInfo] = {}
        self._sort_state = {"col": None, "reverse": False}
        self.selected_pid: Optional[str] = None
        self.selected_name: str = ""
        self.poller: Optional[Poller] = None
        self._build_ui()
        ctx.on_device_changed(self._on_device_changed)
        self._update_button_states()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttkb.Label(toolbar, text="Running Processes", font=("Segoe UI", 14, "bold")).pack(side="left")
        self.export_btn = ttkb.Button(toolbar, text="⬇ Export", bootstyle="secondary-outline", command=self._export)
        self.export_btn.pack(side="right", padx=2)
        self.refresh_btn = ttkb.Button(toolbar, text="⟳ Refresh", bootstyle="info-outline", command=self._manual_refresh)
        self.refresh_btn.pack(side="right", padx=2)

        live_bar = ttk.Frame(self)
        live_bar.pack(fill="x", pady=(0, 6))
        self.live_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(
            live_bar, text="Live updates", variable=self.live_var, bootstyle="round-toggle", command=self._toggle_live,
        ).pack(side="left")
        ttkb.Label(live_bar, text="Interval (s):").pack(side="left", padx=(12, 4))
        self.interval_var = tk.StringVar(value="4")
        ttkb.Spinbox(
            live_bar, from_=2, to=60, textvariable=self.interval_var, width=5, command=self._on_interval_changed,
        ).pack(side="left")
        ttkb.Label(
            live_bar, text="Refreshes the process list, and the memory detail of whichever process is selected below.",
            bootstyle="secondary",
        ).pack(side="left", padx=(8, 0))

        ttkb.Label(live_bar, text="Search:").pack(side="left", padx=(16, 4))
        self.search_var = tk.StringVar()
        ttkb.Entry(live_bar, textvariable=self.search_var, width=22).pack(side="left")
        self.search_var.trace_add("write", debounce(self, 200, self._apply_filter))

        self.count_label = ttkb.Label(live_bar, text="0 processes", bootstyle="secondary")
        self.count_label.pack(side="right")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        table_frame = ttk.Frame(paned)
        self.tree = ttk.Treeview(table_frame, columns=COLUMNS, show="headings", selectmode="browse")
        for col in COLUMNS:
            self.tree.heading(col, text=COLUMN_LABELS[col], command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=COLUMN_WIDTHS[col], anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(table_frame, weight=3)

        detail_frame = ttk.Frame(paned, padding=(8, 0, 0, 0))
        self.detail_title = ttkb.Label(detail_frame, text="Select a process for memory detail", font=("Segoe UI", 12, "bold"))
        self.detail_title.pack(anchor="w")
        self.detail_summary = ttkb.Label(detail_frame, text="", bootstyle="warning", font=("Segoe UI", 10, "bold"))
        self.detail_summary.pack(anchor="w", pady=(2, 6))
        ttkb.Label(detail_frame, text="dumpsys meminfo <pid>:").pack(anchor="w")
        self.detail_text = ttkb.ScrolledText(detail_frame, auto_hide=True, wrap="none", font=("Consolas", 9))
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.text.configure(state="disabled")
        paned.add(detail_frame, weight=2)

    def _update_button_states(self) -> None:
        connected = self.ctx.is_connected
        self.refresh_btn.configure(state="normal" if connected else "disabled")

    def _on_device_changed(self, _device) -> None:
        self._stop_live()
        self.live_var.set(False)
        self.all_processes = []
        self.selected_pid = None
        self._refresh_tree([])
        self._clear_detail()
        self._update_button_states()

    # -- fetch --------------------------------------------------------------
    def _manual_refresh(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            return
        serial = self.ctx.serial
        selected_pid = self.selected_pid

        def task():
            return self._fetch(serial, selected_pid)

        run_async(self.ctx.root, task, self._apply_refresh_result, self._on_refresh_error)

    def _fetch(self, serial: str, selected_pid: Optional[str]):
        ps_result = self.ctx.car.run_custom_shell(serial, PS_COMMAND, timeout=15)
        processes = parse_ps_output(ps_result.combined) if ps_result.ok else []
        meminfo_text = None
        if selected_pid:
            mem_result = self.ctx.car.run_custom_shell(serial, f"dumpsys meminfo {selected_pid}", timeout=15)
            meminfo_text = mem_result.combined
        return processes, meminfo_text

    def _apply_refresh_result(self, result) -> None:
        processes, meminfo_text = result
        self.all_processes = processes
        self._apply_filter()
        if meminfo_text is not None and self.selected_pid:
            self._show_meminfo(meminfo_text)

    def _on_refresh_error(self, exc: BaseException) -> None:
        self.ctx.notify_status(f"Failed to refresh process list: {exc}", "error")

    # -- live updates --------------------------------------------------
    def _toggle_live(self) -> None:
        if self.live_var.get():
            self._start_live()
        else:
            self._stop_live()

    def _on_interval_changed(self) -> None:
        if self.poller is not None:
            self.poller.set_interval(self._interval_ms())

    def _interval_ms(self) -> int:
        try:
            return max(2, int(float(self.interval_var.get()))) * 1000
        except ValueError:
            return 4000

    def _start_live(self) -> None:
        if not self.ctx.serial:
            Messagebox.show_warning("Select a connected device first.", title="No device", parent=self.ctx.root)
            self.live_var.set(False)
            return
        self._stop_live()
        serial = self.ctx.serial
        self.poller = Poller(
            self.ctx.root, self._interval_ms(),
            lambda: self._fetch(serial, self.selected_pid),
            self._apply_refresh_result, self._on_refresh_error,
        )
        self.poller.start()

    def _stop_live(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller = None

    # -- table -----------------------------------------------------------
    def _apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        rows = self.all_processes
        if query:
            rows = [p for p in rows if query in p.name.lower() or query in p.pid or query in p.user.lower()]
        self._refresh_tree(rows)

    def _refresh_tree(self, rows: List[ProcessInfo]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_id_to_pid.clear()
        for proc in rows:
            item_id = self.tree.insert(
                "", "end",
                values=(proc.pid, proc.ppid, proc.user, proc.rss_display, proc.vsz_display, proc.name),
            )
            self._row_id_to_pid[item_id] = proc
        self.count_label.configure(text=f"{len(rows)} / {len(self.all_processes)} processes")

    def _sort_by(self, col: str) -> None:
        reverse = self._sort_state["col"] == col and not self._sort_state["reverse"]
        self._sort_state = {"col": col, "reverse": reverse}
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def sort_key(pair):
            value = pair[0].replace(",", "").replace(" KB", "")
            try:
                return (0, float(value))
            except ValueError:
                return (1, value.lower())

        items.sort(key=sort_key, reverse=reverse)
        for index, (_value, item_id) in enumerate(items):
            self.tree.move(item_id, "", index)

    def _on_select(self, _event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        proc = self._row_id_to_pid.get(selection[0])
        if proc is None:
            return
        self.selected_pid = proc.pid
        self.selected_name = proc.name
        self.detail_title.configure(text=f"{proc.name}  (PID {proc.pid})")
        self.detail_summary.configure(text="Fetching memory info…")
        if not self.ctx.serial:
            return
        serial = self.ctx.serial
        pid = proc.pid

        def task():
            return self.ctx.car.run_custom_shell(serial, f"dumpsys meminfo {pid}", timeout=15)

        def done(result) -> None:
            if self.selected_pid != pid:
                return  # user already selected something else
            self._show_meminfo(result.combined)

        run_async(self.ctx.root, task, done, lambda exc: self.detail_summary.configure(text=f"Error: {exc}", bootstyle="danger"))

    def _show_meminfo(self, text: str) -> None:
        totals = parse_meminfo_totals(text)
        if totals:
            self.detail_summary.configure(
                text=(
                    f"PSS: {totals['pss_kb']} KB   |   RSS: {totals['rss_kb']} KB   |   "
                    f"Private Dirty: {totals['private_dirty_kb']} KB   |   Swap: {totals['swap_kb']} KB"
                ),
                bootstyle="warning",
            )
        else:
            self.detail_summary.configure(text="(no summary line found - see raw output below)", bootstyle="secondary")
        widget = self.detail_text.text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text or "(no output)")
        widget.configure(state="disabled")

    def _clear_detail(self) -> None:
        self.detail_title.configure(text="Select a process for memory detail")
        self.detail_summary.configure(text="")
        widget = self.detail_text.text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.configure(state="disabled")

    # -- export -------------------------------------------------------------
    def _export(self) -> None:
        if not self.all_processes:
            Messagebox.show_info("No processes to export yet - refresh first.", title="Nothing to export", parent=self.ctx.root)
            return
        path = filedialog.asksaveasfilename(
            title="Export process list", defaultextension=".csv", filetypes=EXPORT_FILETYPES,
        )
        if not path:
            return
        try:
            rows = [
                {
                    "pid": p.pid, "ppid": p.ppid, "user": p.user,
                    "rss_kb": p.rss_kb, "vsz_kb": p.vsz_kb, "name": p.name,
                }
                for p in self.all_processes
            ]
            export_rows(rows, path, root_tag="processes", item_tag="process", title="Running Processes")
            self.ctx.notify_status(f"Exported {len(self.all_processes)} processes to {path}", "success")
            Messagebox.show_info(f"Exported {len(self.all_processes)} processes to:\n{path}", title="Export complete", parent=self.ctx.root)
        except (OSError, RuntimeError) as exc:
            Messagebox.show_error(f"Could not write file:\n{exc}", title="Export failed", parent=self.ctx.root)

    def shutdown(self) -> None:
        self._stop_live()
