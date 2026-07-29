"""Dashboard tab: curated quick controls for the most common vehicle properties.

Every control here is looked up by NAME against whatever the connected
device actually reports (see app/property_registry.py::match_known). A
control that the device doesn't support is shown greyed out with a
"Not supported" badge instead of being wired to a guessed property id.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

import ttkbootstrap as ttkb

from app.car_service import VehicleProperty, parse_hal_prop_values, pick_value_for_area
from app.gui.context import AppContext
from app.gui.widgets import ScrollableFrame, StatusPill, Tooltip
from app.property_registry import (
    SECTION_ORDER,
    WIDGET_DROPDOWN,
    WIDGET_READONLY,
    WIDGET_SLIDER,
    WIDGET_SPINNER,
    WIDGET_TOGGLE,
    DashboardControl,
    enum_table,
    group_by_section,
    match_known,
)
from app.utils.workers import run_async


def _safe_float(raw: object, default: float = 0.0) -> float:
    try:
        text = str(raw).strip()
        if text.lower() == "true":
            return 1.0
        if text.lower() == "false":
            return 0.0
        if text.lower().startswith("0x"):
            return float(int(text, 16))
        return float(text)
    except (ValueError, TypeError):
        return default


class PropertyControlRow(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext, control: DashboardControl, area_id: str) -> None:
        super().__init__(parent, padding=(4, 4))
        self.ctx = ctx
        self.control = control
        self.area_id = area_id
        self.meta = control.meta
        self.live: Optional[VehicleProperty] = control.live
        self.var: Optional[tk.Variable] = None
        self.combo: Optional[ttkb.Combobox] = None
        self._build()

    def _build(self) -> None:
        self.columnconfigure(2, weight=1)
        name_text = self.meta.label
        if self.meta.per_area and self.area_id not in ("global", None):
            name_text += f"  [area {self.area_id}]"
        label = ttkb.Label(self, text=name_text, width=24, anchor="w")
        label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        if self.meta.description:
            Tooltip(label, self.meta.description)

        if not self.control.supported:
            ttkb.Label(self, text="—", width=14, bootstyle="secondary").grid(row=0, column=1, sticky="w")
            StatusPill(self, text="Not supported", bootstyle="secondary").grid(row=0, column=2, sticky="w")
            return

        current_raw = self.live.area_values.get(self.area_id) if self.live else None
        if not current_raw and self.live:
            current_raw = self.live.value_summary()
        decoded = enum_table.decode(self.meta.name, str(current_raw)) if current_raw else None
        value_text = f"{current_raw} ({decoded})" if decoded else (str(current_raw) if current_raw else "-")
        self.value_label = ttkb.Label(self, text=value_text, width=16, bootstyle="info")
        self.value_label.grid(row=0, column=1, sticky="w")

        editor_frame = ttk.Frame(self)
        editor_frame.grid(row=0, column=2, sticky="ew", padx=8)

        writable = self.control.writable
        widget_kind = self.meta.widget

        if widget_kind == WIDGET_READONLY or not writable:
            if writable:
                ttkb.Label(editor_frame, text="(display only)", bootstyle="secondary").pack(side="left")
            else:
                ttkb.Label(editor_frame, text="(read-only on this device)", bootstyle="secondary").pack(side="left")
        elif widget_kind == WIDGET_TOGGLE:
            self.var = tk.BooleanVar(value=str(current_raw).strip().lower() in ("true", "1"))
            ttkb.Checkbutton(editor_frame, variable=self.var, bootstyle="round-toggle").pack(side="left")
        elif widget_kind == WIDGET_SLIDER:
            self.var = tk.DoubleVar(value=_safe_float(current_raw, self.meta.min_hint))
            scale = ttkb.Scale(
                editor_frame, from_=self.meta.min_hint, to=self.meta.max_hint,
                variable=self.var, bootstyle="info",
            )
            scale.pack(side="left", fill="x", expand=True)
            self.readout = ttkb.Label(editor_frame, text=f"{self.var.get():.1f}", width=7)
            self.readout.pack(side="left", padx=4)
            self.var.trace_add("write", lambda *_a: self.readout.config(text=f"{self.var.get():.1f}"))
        elif widget_kind == WIDGET_SPINNER:
            self.var = tk.DoubleVar(value=_safe_float(current_raw, self.meta.min_hint))
            ttkb.Spinbox(
                editor_frame, from_=self.meta.min_hint, to=self.meta.max_hint,
                increment=self.meta.step_hint or 1, textvariable=self.var, width=8,
            ).pack(side="left")
        elif widget_kind == WIDGET_DROPDOWN:
            options = enum_table.options_for(self.meta.name)
            display_values = [f"{val} - {opt_label}" for val, opt_label in options]
            state = "readonly" if display_values else "normal"
            self.combo = ttkb.Combobox(editor_frame, values=display_values, width=22, state=state)
            self.combo.pack(side="left")
            matched = False
            for val, opt_label in options:
                if val == str(current_raw).strip():
                    self.combo.set(f"{val} - {opt_label}")
                    matched = True
                    break
            if not matched:
                self.combo.set(str(current_raw))

        if writable and widget_kind != WIDGET_READONLY:
            ttkb.Button(
                self, text="Apply", bootstyle="success-outline", width=8, command=self._apply,
            ).grid(row=0, column=3, padx=4)

        self.status_label = ttkb.Label(self, text="", width=14)
        self.status_label.grid(row=0, column=4, sticky="w")

    def _get_value_to_send(self) -> Optional[str]:
        kind = self.meta.widget
        if kind == WIDGET_TOGGLE and self.var is not None:
            return "true" if self.var.get() else "false"
        if kind in (WIDGET_SLIDER, WIDGET_SPINNER) and self.var is not None:
            value = self.var.get()
            return str(int(value)) if float(value).is_integer() else f"{value:.2f}"
        if kind == WIDGET_DROPDOWN and self.combo is not None:
            text = self.combo.get().strip()
            return text.split(" - ", 1)[0].strip() if " - " in text else text
        return None

    def _apply(self) -> None:
        if not self.ctx.serial or not self.live:
            return
        value = self._get_value_to_send()
        if value is None:
            return
        serial = self.ctx.serial
        prop_id = self.live.prop_id_hex
        area = self.area_id if self.area_id != "global" else "0"
        self.status_label.configure(text="Sending…", bootstyle="secondary")

        def task():
            return self.ctx.car.set_property_value(serial, prop_id, area, value)

        def done(result) -> None:
            if not self.winfo_exists():
                return
            if result.ok:
                self.status_label.configure(text="Applied ✓", bootstyle="success")
                self.ctx.notify_status(f"Set {self.meta.name} = {value}", "success")
                self._refresh_current_value(serial, prop_id, area)
            else:
                self.status_label.configure(text="Failed ✗", bootstyle="danger")
                self.ctx.notify_status(
                    f"Failed to set {self.meta.name}: {result.combined.strip()[:200]}", "error"
                )

        def error(exc: BaseException) -> None:
            if not self.winfo_exists():
                return
            self.status_label.configure(text="Error ✗", bootstyle="danger")
            self.ctx.notify_status(f"Error setting {self.meta.name}: {exc}", "error")

        run_async(self.ctx.root, task, done, error)

    def _refresh_current_value(self, serial: str, prop_id: str, area: str) -> None:
        """set-property-value doesn't echo the new value back, and the
        property dump this app discovers properties from never carries
        live values at all - so without this, a successful Apply leaves
        the "current value" label showing the stale pre-set value, which
        reads as "nothing happened" even though the write succeeded.
        """

        def task():
            return self.ctx.car.get_property_value(serial, prop_id, area)

        def done(result) -> None:
            if not self.winfo_exists() or not result.ok:
                return
            values = parse_hal_prop_values(result.combined)
            new_value = pick_value_for_area(values, area) if values else None
            if not new_value:
                return
            if self.live is not None:
                self.live.area_values[self.area_id] = new_value
            decoded = enum_table.decode(self.meta.name, new_value)
            self.value_label.configure(text=f"{new_value} ({decoded})" if decoded else new_value)

        # Small delay: the VHAL may process the write asynchronously, so an
        # immediate read-back can still race and return the old value.
        self.after(400, lambda: run_async(self.ctx.root, task, done, lambda _exc: None))


class DashboardTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self._rows: Dict[Tuple[str, str], PropertyControlRow] = {}
        self.sections: Dict[str, ttkb.Labelframe] = {}
        self._build_ui()
        ctx.on_device_changed(self._on_device_changed)
        ctx.on_properties_updated(self._on_properties_updated)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttkb.Label(toolbar, text="Quick Controls", font=("Segoe UI", 14, "bold")).pack(side="left")
        self.hint_label = ttkb.Label(toolbar, text="Connect a device to begin", bootstyle="secondary")
        self.hint_label.pack(side="right", padx=8)
        ttkb.Button(
            toolbar, text="⟳ Refresh", bootstyle="info-outline", command=self._manual_refresh,
        ).pack(side="right")

        self.scroll = ScrollableFrame(self)
        self.scroll.pack(fill="both", expand=True)

        for section in SECTION_ORDER:
            frame = ttkb.Labelframe(self.scroll.inner, text=section, padding=8, bootstyle="primary")
            frame.pack(fill="x", padx=4, pady=6, anchor="n")
            frame.columnconfigure(0, weight=1)
            self.sections[section] = frame
            ttkb.Label(frame, text="No device connected", bootstyle="secondary").pack(anchor="w")

    def _on_device_changed(self, device) -> None:
        if device is None:
            self._clear_rows()
            self.hint_label.configure(text="No device selected")
            for frame in self.sections.values():
                for child in frame.winfo_children():
                    child.destroy()
                ttkb.Label(frame, text="No device connected", bootstyle="secondary").pack(anchor="w")

    def _on_properties_updated(self, properties: List[VehicleProperty]) -> None:
        controls = match_known(properties)
        grouped = group_by_section(controls)
        supported_count = sum(1 for c in controls if c.supported)
        self.hint_label.configure(text=f"{supported_count}/{len(controls)} known properties supported by this device")
        self._render(grouped)

    def _clear_rows(self) -> None:
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()

    def _render(self, grouped: Dict[str, List[DashboardControl]]) -> None:
        self._clear_rows()
        for section in SECTION_ORDER:
            frame = self.sections[section]
            for child in frame.winfo_children():
                child.destroy()
            controls = grouped.get(section, [])
            if not controls:
                ttkb.Label(frame, text="(none of the known properties in this group)", bootstyle="secondary").pack(anchor="w")
                continue
            for control in controls:
                if control.live and control.meta.per_area and control.live.area_ids:
                    areas = control.live.area_ids
                else:
                    areas = ["global"]
                for area in areas:
                    row = PropertyControlRow(frame, self.ctx, control, area_id=area)
                    row.pack(fill="x", pady=1)
                    self._rows[(control.meta.name, area)] = row

    def _manual_refresh(self) -> None:
        self.ctx.refresh_properties()
