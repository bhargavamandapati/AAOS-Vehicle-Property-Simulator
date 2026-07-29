"""Properties tab: full table of every property the device reports, with a
details panel for inspecting raw output and issuing get/set/inject commands
for troubleshooting.

The details panel adapts its input widgets to the property's declared
value type - BOOLEAN gets radio buttons, an enum-like INT with a known
configArray gets a dropdown of exactly the valid values, everything else
gets a free-text field - and always shows the literal `adb` command each
action would run, so nothing here is a black box.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Dict, List, Optional

import ttkbootstrap as ttkb
from ttkbootstrap.dialogs import Messagebox

from app.car_service import VehicleProperty, parse_hal_prop_values, pick_value_for_area
from app.export_utils import EXPORT_FILETYPES, export_rows
from app.gui.context import AppContext
from app.gui.widgets import debounce
from app.property_registry import enum_table
from app.utils.workers import run_async

COLUMNS = ("id", "name", "category", "access", "change_mode", "area", "type", "value")
COLUMN_LABELS = {
    "id": "Property ID",
    "name": "Name",
    "category": "Category",
    "access": "Access",
    "change_mode": "Change Mode",
    "area": "Area Type",
    "type": "Value Type",
    "value": "Current Value",
}
COLUMN_WIDTHS = {"id": 110, "name": 240, "category": 140, "access": 90,
                  "change_mode": 100, "area": 90, "type": 90, "value": 220}

_ACTION_LABELS = {
    "get_property": "Get   ",
    "set_property": "Set   ",
    "inject_event": "Inject",
    "inject_error": "Error ",
}


def _decorate(raw: str, label: Optional[str]) -> str:
    return f"{raw} - {label}" if label else raw


def _undecorate(text: str) -> str:
    """Strip a " - LABEL" decoration, as produced by _decorate() above for
    the value dropdown (e.g. "8 - DRIVE" -> "8")."""
    text = text.strip()
    return text.split(" - ", 1)[0].strip() if " - " in text else text


def _undecorate_area(text: str) -> str:
    """Strip a " (NAME)" decoration, as produced by VehicleProperty.area_label
    for the area dropdown (e.g. "0x10 (ROW_2_LEFT)" -> "0x10"). This is a
    *different* format from _undecorate() above, so using the wrong one
    here would send the literal "(NAME)" text as the area id to the
    device's remote shell, which then chokes on the parentheses."""
    text = text.strip()
    return text.split(" (", 1)[0].strip() if " (" in text else text


class PropertyDetailsPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.current: Optional[VehicleProperty] = None
        self.value_widget_kind = "entry"  # "radio" | "dropdown" | "entry"
        self.value_var: tk.Variable = tk.StringVar()
        self.value_combo: Optional[ttkb.Combobox] = None
        self._building_value_widget = False
        self._build_ui()

    def _build_ui(self) -> None:
        self.title_label = ttkb.Label(self, text="Select a property", font=("Segoe UI", 12, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttkb.Label(self, text="", bootstyle="secondary")
        self.subtitle_label.pack(anchor="w", pady=(0, 8))

        info_grid = ttk.Frame(self)
        info_grid.pack(fill="x", pady=(0, 8))
        self.info_vars: Dict[str, tk.StringVar] = {}
        for row, key in enumerate(["current_value", "access", "change_mode", "area_type", "value_type", "min", "max"]):
            ttkb.Label(info_grid, text=key.replace("_", " ").title() + ":", width=12, anchor="w").grid(
                row=row, column=0, sticky="w", pady=1
            )
            var = tk.StringVar(value="-")
            self.info_vars[key] = var
            style = "warning" if key == "current_value" else "info"
            font = ("Segoe UI", 10, "bold") if key == "current_value" else ("Segoe UI", 9)
            ttkb.Label(info_grid, textvariable=var, bootstyle=style, font=font).grid(row=row, column=1, sticky="w")

        action_frame = ttkb.Labelframe(self, text="Get / Set / Inject", padding=8, bootstyle="primary")
        action_frame.pack(fill="x", pady=(0, 8))

        row_area = ttk.Frame(action_frame)
        row_area.pack(fill="x", pady=2)
        ttkb.Label(row_area, text="Area ID:", width=10).pack(side="left")
        self.area_var = tk.StringVar(value="0")
        self.area_combo = ttkb.Combobox(row_area, textvariable=self.area_var, width=20, values=["0"])
        self.area_combo.pack(side="left", padx=4)
        self.area_combo.bind("<<ComboboxSelected>>", lambda _e: (self._update_command_preview(), self._update_current_value_display()))
        self.area_var.trace_add("write", lambda *_a: self._update_command_preview())
        ttkb.Button(row_area, text="Get Latest", bootstyle="info-outline", command=self._get_value).pack(side="left", padx=4)
        ttkb.Button(row_area, text="Inject Event", bootstyle="warning-outline", command=self._inject_event).pack(side="left", padx=4)

        row_value = ttk.Frame(action_frame)
        row_value.pack(fill="x", pady=(6, 2))
        ttkb.Label(row_value, text="Value:", width=10).pack(side="left")
        self.value_container = ttk.Frame(row_value)
        self.value_container.pack(side="left", fill="x", expand=True)
        ttkb.Button(row_value, text="Set", bootstyle="success-outline", command=self._set_value).pack(side="left", padx=4)
        self._build_value_widget_for(None, "")

        row_error = ttk.Frame(action_frame)
        row_error.pack(fill="x", pady=2)
        ttkb.Label(row_error, text="Error Code:", width=10).pack(side="left")
        self.error_var = tk.StringVar(value="0")
        ttkb.Entry(row_error, textvariable=self.error_var, width=10).pack(side="left", padx=4)
        self.error_var.trace_add("write", lambda *_a: self._update_command_preview())
        ttkb.Button(row_error, text="Inject Error", bootstyle="danger-outline", command=self._inject_error).pack(side="left", padx=4)

        self.busy_bar = ttkb.Progressbar(action_frame, mode="indeterminate", bootstyle="info-striped")

        self.result_label = ttkb.Label(self, text="", bootstyle="secondary", wraplength=380, justify="left")
        self.result_label.pack(fill="x", pady=(0, 4))

        cmd_frame = ttkb.Labelframe(self, text="ADB commands (for reference)", padding=6, bootstyle="secondary")
        cmd_frame.pack(fill="x", pady=(0, 8))
        self.command_preview = tk.Text(cmd_frame, height=5, wrap="none", font=("Consolas", 8))
        self.command_preview.pack(fill="x")
        try:
            colors = ttkb.Style().colors
            self.command_preview.configure(background=colors.bg, foreground=colors.fg, insertbackground=colors.fg)
        except Exception:  # noqa: BLE001 - purely cosmetic, never worth crashing over
            pass
        self.command_preview.configure(state="disabled")

        ttkb.Label(self, text="Raw dump for this property:").pack(anchor="w")
        self.raw_text = ttkb.ScrolledText(self, height=10, auto_hide=True, wrap="word")
        self.raw_text.pack(fill="both", expand=True)
        self.raw_text.text.configure(state="disabled")

        self._update_command_preview()

    # -- populate ------------------------------------------------------
    def show_property(self, prop: VehicleProperty) -> None:
        self.current = prop
        self.title_label.configure(text=prop.display_name)
        self.subtitle_label.configure(text=f"{prop.prop_id_hex}  ({prop.category})")
        self.info_vars["access"].set(prop.access or "unknown")
        self.info_vars["change_mode"].set(prop.change_mode or "unknown")
        self.info_vars["area_type"].set(prop.area_type or "unknown")
        self.info_vars["value_type"].set(prop.value_type or "unknown")
        self.info_vars["min"].set(prop.min_value or "-")
        self.info_vars["max"].set(prop.max_value or "-")

        area_ids = prop.area_ids or ["0"]
        area_labels = [prop.area_label(a) for a in area_ids]
        self.area_combo.configure(values=area_labels)
        first_area = area_ids[0]
        self.area_var.set(prop.area_label(first_area))
        current_value = prop.area_values.get(first_area, "")

        self._build_value_widget_for(prop, current_value)
        self.result_label.configure(text="")
        self._set_raw_text(prop.raw_block)
        self._update_command_preview()
        self._update_current_value_display()

    def clear(self) -> None:
        self.current = None
        self.title_label.configure(text="Select a property")
        self.subtitle_label.configure(text="")
        for var in self.info_vars.values():
            var.set("-")
        self.area_combo.configure(values=["0"])
        self.area_var.set("0")
        self._build_value_widget_for(None, "")
        self._set_raw_text("")
        self._update_command_preview()

    def _update_current_value_display(self) -> None:
        if self.current is None:
            self.info_vars["current_value"].set("-")
            return
        area_id = self._current_area_id()
        raw = self.current.area_values.get(area_id) or pick_value_for_area(self.current.area_values, area_id)
        if not raw:
            self.info_vars["current_value"].set("(unknown - try Get Latest)")
            return
        decoded = enum_table.decode(self.current.name, raw)
        self.info_vars["current_value"].set(f"{raw} ({decoded})" if decoded else raw)

    def _set_raw_text(self, text: str) -> None:
        self.raw_text.text.configure(state="normal")
        self.raw_text.text.delete("1.0", "end")
        self.raw_text.text.insert("end", text or "(no raw data)")
        self.raw_text.text.configure(state="disabled")

    # -- value widget: shape depends on the property's declared type ------
    def _build_value_widget_for(self, prop: Optional[VehicleProperty], current_value: str) -> None:
        self._building_value_widget = True
        for child in self.value_container.winfo_children():
            child.destroy()
        self.value_combo = None

        value_type = (prop.value_type if prop else "").upper()

        if value_type == "BOOLEAN":
            self.value_widget_kind = "radio"
            initial = "true" if current_value.strip().lower() in ("true", "1") else "false"
            self.value_var = tk.StringVar(value=initial)
            ttkb.Radiobutton(self.value_container, text="True", variable=self.value_var, value="true", bootstyle="success").pack(side="left", padx=(0, 10))
            ttkb.Radiobutton(self.value_container, text="False", variable=self.value_var, value="false", bootstyle="danger").pack(side="left")
        elif prop is not None and prop.is_enum_like:
            self.value_widget_kind = "dropdown"
            options = [_decorate(raw, enum_table.decode(prop.name, raw)) for raw in prop.config_array]
            self.value_var = tk.StringVar()
            self.value_combo = ttkb.Combobox(self.value_container, textvariable=self.value_var, values=options, state="readonly")
            self.value_combo.pack(side="left", fill="x", expand=True)
            matched_index = next((i for i, raw in enumerate(prop.config_array) if raw == current_value.strip()), None)
            if matched_index is not None:
                self.value_combo.current(matched_index)
            elif options:
                self.value_combo.current(0)
        else:
            self.value_widget_kind = "entry"
            self.value_var = tk.StringVar(value=current_value)
            ttkb.Entry(self.value_container, textvariable=self.value_var).pack(side="left", fill="x", expand=True)

        self.value_var.trace_add("write", lambda *_a: self._update_command_preview())
        self._building_value_widget = False

    def _get_value_to_send(self) -> str:
        if self.value_widget_kind == "dropdown":
            return _undecorate(self.value_var.get())
        return self.value_var.get()

    def _current_area_id(self) -> str:
        return _undecorate_area(self.area_var.get()) or "0"

    # -- live command preview, purely for user reference -------------------
    def _update_command_preview(self) -> None:
        if not hasattr(self, "command_preview"):
            return
        if self.current is None:
            text = "(select a property to preview its ADB commands)"
        else:
            serial = self.ctx.serial or "<no device selected>"
            prop_id = self.current.prop_id_hex
            area_id = self._current_area_id()
            value = self._get_value_to_send()
            error_code = self.error_var.get() if hasattr(self, "error_var") else "0"
            lines = [
                f"{_ACTION_LABELS['get_property']}  {self.ctx.car.preview_command(serial, 'get_property', prop_id, area_id)}",
                f"{_ACTION_LABELS['set_property']}  {self.ctx.car.preview_command(serial, 'set_property', prop_id, area_id, value=value)}",
                f"{_ACTION_LABELS['inject_event']}  {self.ctx.car.preview_command(serial, 'inject_event', prop_id, area_id, value=value)}",
                f"{_ACTION_LABELS['inject_error']}  {self.ctx.car.preview_command(serial, 'inject_error', prop_id, area_id, error_code=error_code)}",
            ]
            text = "\n".join(lines)
        self.command_preview.configure(state="normal")
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("end", text)
        self.command_preview.configure(state="disabled")

    # -- actions -------------------------------------------------------
    def _require_target(self) -> Optional[tuple]:
        if not self.ctx.serial or self.current is None:
            Messagebox.show_warning(
                "Select a connected device and a property before running this action.",
                title="Nothing to run", parent=self.ctx.root,
            )
            return None
        return self.ctx.serial, self.current.prop_id_hex, self._current_area_id()

    def _run(self, label: str, func, apply_result_as_value: bool = False, refresh_after: bool = False) -> None:
        self.result_label.configure(text=f"{label}…", bootstyle="secondary")
        self.busy_bar.pack(fill="x", pady=(2, 0))
        self.busy_bar.start(12)

        def stop_busy() -> None:
            try:
                self.busy_bar.stop()
                self.busy_bar.pack_forget()
            except tk.TclError:
                pass

        def done(result) -> None:
            stop_busy()
            if not self.winfo_exists():
                return
            style = "success" if result.ok else "danger"
            text = result.combined.strip() or ("OK" if result.ok else "No output")
            self.result_label.configure(text=f"{label}: {text[:400]}", bootstyle=style)
            if result.ok:
                self.ctx.notify_status(f"{label} succeeded for {self.current.display_name}", "success")
                if apply_result_as_value:
                    self._apply_fetched_value(result)
                if refresh_after:
                    self._refresh_value_after_write(label)
            else:
                self.ctx.notify_status(f"{label} failed for {self.current.display_name}", "error")

        def error(exc: BaseException) -> None:
            stop_busy()
            if not self.winfo_exists():
                return
            self.result_label.configure(text=f"{label} error: {exc}", bootstyle="danger")

        run_async(self.ctx.root, func, done, error)

    def _apply_fetched_value(self, result) -> None:
        """Update the in-memory property (and re-broadcast it so the table
        and Dashboard pick it up) from a get-property-value response."""
        if self.current is None:
            return
        values = parse_hal_prop_values(result.combined)
        if not values:
            return
        self.current.area_values.update(values)
        self._update_current_value_display()
        self.ctx.set_properties(self.ctx.properties)

    def _refresh_value_after_write(self, action_label: str) -> None:
        """`set-property-value` / `inject-vhal-event` don't echo the new
        value back, and the property dump has no live values at all - so
        without this, a successful Set looks like nothing happened because
        nothing on screen changes. Issue a follow-up Get and show what the
        device now actually reports.
        """
        if not self.ctx.serial or self.current is None:
            return
        serial = self.ctx.serial
        prop = self.current
        prop_id = prop.prop_id_hex
        area_id = self._current_area_id()

        def task():
            return self.ctx.car.get_property_value(serial, prop_id, area_id)

        def done(result) -> None:
            if not self.winfo_exists() or self.current is not prop or not result.ok:
                return
            values = parse_hal_prop_values(result.combined)
            if values:
                prop.area_values.update(values)
            confirmed = pick_value_for_area(values, area_id) if values else None
            if confirmed:
                self.result_label.configure(
                    text=f"{action_label} applied - device now reports: {confirmed}", bootstyle="success",
                )
            self._update_current_value_display()
            self.ctx.set_properties(self.ctx.properties)

        # Small delay: the VHAL may process the write asynchronously, so an
        # immediate read-back can still race and return the old value.
        self.after(400, lambda: run_async(self.ctx.root, task, done, lambda _exc: None))

    def _get_value(self) -> None:
        target = self._require_target()
        if not target:
            return
        serial, prop_id, area_id = target
        self._run("Get", lambda: self.ctx.car.get_property_value(serial, prop_id, area_id), apply_result_as_value=True)

    def _set_value(self) -> None:
        target = self._require_target()
        if not target:
            return
        serial, prop_id, area_id = target
        value = self._get_value_to_send()
        self._run("Set", lambda: self.ctx.car.set_property_value(serial, prop_id, area_id, value), refresh_after=True)

    def _inject_event(self) -> None:
        target = self._require_target()
        if not target:
            return
        serial, prop_id, area_id = target
        value = self._get_value_to_send()
        self._run("Inject Event", lambda: self.ctx.car.inject_event(serial, prop_id, area_id, value), refresh_after=True)

    def _inject_error(self) -> None:
        target = self._require_target()
        if not target:
            return
        serial, prop_id, area_id = target
        error_code = self.error_var.get()
        self._run("Inject Error", lambda: self.ctx.car.inject_error(serial, prop_id, area_id, error_code))


class PropertiesTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.all_properties: List[VehicleProperty] = []
        self._row_id_to_prop: Dict[str, VehicleProperty] = {}
        self._sort_state = {"col": None, "reverse": False}
        self._build_ui()
        ctx.on_device_changed(self._on_device_changed)
        ctx.on_properties_updated(self._on_properties_updated)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttkb.Label(toolbar, text="All Vehicle Properties", font=("Segoe UI", 14, "bold")).pack(side="left")
        ttkb.Button(toolbar, text="⟳ Refresh", bootstyle="info-outline", command=self.ctx.refresh_properties).pack(side="right")
        self.fetch_values_btn = ttkb.Button(
            toolbar, text="⚡ Fetch Current Values", bootstyle="warning-outline", command=self._fetch_all_values,
        )
        self.fetch_values_btn.pack(side="right", padx=4)
        ttkb.Button(toolbar, text="⬇ Export All Details", bootstyle="secondary-outline", command=self._export).pack(side="right", padx=4)

        self.progress_row = ttk.Frame(self)
        self.progress_label = ttkb.Label(self.progress_row, text="", bootstyle="secondary")
        self.progress_label.pack(side="left")
        self.progress_bar = ttkb.Progressbar(self.progress_row, mode="determinate", bootstyle="warning-striped")
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=8)

        self.filter_bar = ttk.Frame(self)
        filter_bar = self.filter_bar
        filter_bar.pack(fill="x", pady=(0, 6))
        ttkb.Label(filter_bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttkb.Entry(filter_bar, textvariable=self.search_var, width=26).pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", debounce(self, 200, self._apply_filter))

        ttkb.Label(filter_bar, text="Category:").pack(side="left")
        self.category_var = tk.StringVar(value="All")
        self.category_combo = ttkb.Combobox(filter_bar, textvariable=self.category_var, state="readonly", width=18, values=["All"])
        self.category_combo.pack(side="left", padx=(4, 12))
        self.category_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())

        ttkb.Label(filter_bar, text="Access:").pack(side="left")
        self.access_var = tk.StringVar(value="All")
        access_combo = ttkb.Combobox(
            filter_bar, textvariable=self.access_var, state="readonly", width=13,
            values=["All", "READ", "WRITE", "READ_WRITE", "READ_ONLY", "WRITE_ONLY"],
        )
        access_combo.pack(side="left", padx=(4, 12))
        access_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())

        self.count_label = ttkb.Label(filter_bar, text="0 properties", bootstyle="secondary")
        self.count_label.pack(side="right")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        table_frame = ttk.Frame(paned)
        self.tree = ttk.Treeview(table_frame, columns=COLUMNS, show="headings", selectmode="browse")
        for col in COLUMNS:
            self.tree.heading(col, text=COLUMN_LABELS[col], command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=COLUMN_WIDTHS[col], anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("readonly_row", foreground="#8a94a3")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(table_frame, weight=3)

        self.details = PropertyDetailsPanel(paned, self.ctx)
        paned.add(self.details, weight=2)

    # -- data plumbing ----------------------------------------------------
    def _on_device_changed(self, device) -> None:
        if device is None:
            self.all_properties = []
            self._refresh_tree([])
            self.details.clear()

    def _on_properties_updated(self, properties: List[VehicleProperty]) -> None:
        self.all_properties = properties
        categories = sorted({p.category for p in properties}) if properties else []
        self.category_combo.configure(values=["All"] + categories)
        self._apply_filter()

    def _fetch_all_values(self) -> None:
        """`dumpsys car_service` only gives property *definitions*, not live
        values (confirmed against a real AAOS emulator dump) - this backfills
        them with one `get-property-value` call per property. Opt-in and
        run on a background thread since it's O(property count) adb calls.
        Progress is reported incrementally via a thread-safe queue so a
        274-property fetch doesn't look like a frozen button.
        """
        if not self.ctx.serial or not self.all_properties:
            Messagebox.show_warning(
                "Connect a device with properties loaded before fetching values.",
                title="Nothing to fetch", parent=self.ctx.root,
            )
            return

        serial = self.ctx.serial
        properties = list(self.all_properties)
        total = len(properties)
        self.fetch_values_btn.configure(state="disabled")
        self.progress_row.pack(fill="x", pady=(0, 6), before=self.filter_bar)
        self.progress_bar.configure(maximum=total, value=0)
        self.progress_label.configure(text=f"Fetching current values… 0/{total}")
        self.ctx.notify_status(f"Fetching current values for {total} properties…", "info")

        progress_queue: "queue.Queue" = queue.Queue()
        stop_flag = {"done": False, "fetched": 0}

        def worker() -> None:
            fetched = 0
            for index, prop in enumerate(properties, start=1):
                values = self.ctx.car.fetch_current_values(serial, prop.prop_id_hex)
                if values:
                    prop.area_values.update(values)
                    fetched += 1
                progress_queue.put(index)
            stop_flag["done"] = True
            stop_flag["fetched"] = fetched

        threading.Thread(target=worker, daemon=True).start()
        self._poll_fetch_progress(progress_queue, stop_flag, total, properties)

    def _poll_fetch_progress(self, progress_queue, stop_flag, total, properties) -> None:
        latest = None
        try:
            while True:
                latest = progress_queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self.progress_bar.configure(value=latest)
            self.progress_label.configure(text=f"Fetching current values… {latest}/{total}")

        if stop_flag["done"]:
            self.fetch_values_btn.configure(state="normal")
            self.progress_row.pack_forget()
            if self.details.current is not None:
                self.details.show_property(self.details.current)
            self.ctx.set_properties(self.all_properties)
            fetched = stop_flag["fetched"]
            self.ctx.notify_status(f"Fetched current values for {fetched}/{total} properties", "success")
            Messagebox.show_info(
                f"Fetched current values for {fetched} of {total} properties.\n\n"
                f"{total - fetched} had no value available (no listener/unsupported area), "
                "which is normal for some vendor or write-only properties.",
                title="Fetch Current Values complete", parent=self.ctx.root,
            )
            return
        self.after(150, lambda: self._poll_fetch_progress(progress_queue, stop_flag, total, properties))

    def _apply_filter(self) -> None:
        query = self.search_var.get().strip().lower()
        category = self.category_var.get()
        access = self.access_var.get()
        rows = self.all_properties
        if query:
            rows = [p for p in rows if query in p.display_name.lower() or query in p.prop_id_hex.lower()]
        if category != "All":
            rows = [p for p in rows if p.category == category]
        if access != "All":
            rows = [p for p in rows if access in (p.access or "")]
        self._refresh_tree(rows)

    def _refresh_tree(self, rows: List[VehicleProperty]) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_id_to_prop.clear()
        for prop in rows:
            tags = ("readonly_row",) if prop.access and "WRITE" not in prop.access else ()
            item_id = self.tree.insert(
                "", "end",
                values=(
                    prop.prop_id_hex, prop.display_name, prop.category,
                    prop.access or "?", prop.change_mode or "?",
                    prop.area_type or "?", prop.value_type or "?",
                    prop.value_summary(),
                ),
                tags=tags,
            )
            self._row_id_to_prop[item_id] = prop
        self.count_label.configure(text=f"{len(rows)} / {len(self.all_properties)} properties")

    def _sort_by(self, col: str) -> None:
        reverse = self._sort_state["col"] == col and not self._sort_state["reverse"]
        self._sort_state = {"col": col, "reverse": reverse}
        idx = COLUMNS.index(col)
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def sort_key(pair):
            value = pair[0]
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
        prop = self._row_id_to_prop.get(selection[0])
        if prop is not None:
            self.details.show_property(prop)

    # -- export: every field this app knows about, per property ------------
    def _property_to_row(self, p: VehicleProperty) -> Dict[str, object]:
        return {
            "id_hex": p.prop_id_hex,
            "id_int": p.prop_id_int,
            "name": p.display_name,
            "category": p.category,
            "access": p.access,
            "change_mode": p.change_mode,
            "area_type": p.area_type,
            "value_type": p.value_type,
            "min": p.min_value,
            "max": p.max_value,
            "config_array": ", ".join(p.config_array),
            "areas_and_values": "; ".join(f"{p.area_label(a)}={v or '-'}" for a, v in p.area_values.items()),
            "raw_dump": p.raw_block,
        }

    def _export(self) -> None:
        if not self.all_properties:
            Messagebox.show_info("No properties to export yet - refresh first.", title="Nothing to export", parent=self.ctx.root)
            return
        path = filedialog.asksaveasfilename(
            title="Export all property details", defaultextension=".csv", filetypes=EXPORT_FILETYPES,
        )
        if not path:
            return
        try:
            rows = [self._property_to_row(p) for p in self.all_properties]
            export_rows(rows, path, root_tag="properties", item_tag="property", title="Vehicle Properties")
            self.ctx.notify_status(f"Exported {len(self.all_properties)} properties to {path}", "success")
            Messagebox.show_info(f"Exported {len(self.all_properties)} properties to:\n{path}", title="Export complete", parent=self.ctx.root)
        except (OSError, RuntimeError) as exc:
            Messagebox.show_error(f"Could not write file:\n{exc}", title="Export failed", parent=self.ctx.root)
