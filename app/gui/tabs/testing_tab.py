"""Testing / Diagnostics tab: scenario runner, snapshot diff, live property
monitor, and a raw ADB shell console for ad-hoc troubleshooting.
"""
from __future__ import annotations

import csv
import json
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from tkinter import filedialog, ttk
import tkinter as tk
from typing import Dict, List, Optional

import ttkbootstrap as ttkb
from ttkbootstrap.dialogs import Messagebox

from app.command_log import CommandLogEntry, command_logger
from app.gui.context import AppContext
from app.gui.widgets import debounce
from app.persistent_log import LOG_DIR
from app.utils.fs import open_in_file_manager
from app.utils.workers import Poller, run_async

STATUS_STYLES = {
    "pending": ("secondary", "PENDING"),
    "running": ("warning", "RUNNING"),
    "pass": ("success", "PASS"),
    "fail": ("danger", "FAIL"),
    "skipped": ("secondary", "SKIPPED"),
    "stopped": ("secondary", "STOPPED"),
}


@dataclass
class TestStep:
    property_name: str
    area_id: str = "0"
    value: str = ""
    delay_after: float = 1.0
    verify: bool = False


@dataclass
class TestScenario:
    name: str
    steps: List[TestStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "steps": [asdict(s) for s in self.steps]}

    @staticmethod
    def from_dict(data: dict) -> "TestScenario":
        steps = [TestStep(**s) for s in data.get("steps", [])]
        return TestScenario(name=data.get("name", "Untitled"), steps=steps)


def _builtin_scenarios() -> Dict[str, TestScenario]:
    return {
        "HVAC Smoke Test": TestScenario("HVAC Smoke Test", [
            TestStep("HVAC_POWER_ON", "0", "true", 1.0),
            TestStep("HVAC_AC_ON", "0", "true", 1.0),
            TestStep("HVAC_FAN_SPEED", "0", "4", 1.0),
            TestStep("HVAC_TEMPERATURE_SET", "0", "22", 1.0),
            TestStep("HVAC_RECIRC_ON", "0", "true", 1.0),
            TestStep("HVAC_RECIRC_ON", "0", "false", 1.0),
            TestStep("HVAC_POWER_ON", "0", "false", 0.5),
        ]),
        "Gear Cycle Test": TestScenario("Gear Cycle Test", [
            TestStep("GEAR_SELECTION", "0", "1", 1.5),
            TestStep("GEAR_SELECTION", "0", "4", 1.5),
            TestStep("GEAR_SELECTION", "0", "8", 1.5),
            TestStep("GEAR_SELECTION", "0", "2", 1.5),
            TestStep("GEAR_SELECTION", "0", "1", 0.5),
        ]),
        "Speed Ramp Test": TestScenario("Speed Ramp Test", [
            TestStep("PERF_VEHICLE_SPEED", "0", str(v), 0.8)
            for v in (0, 5, 10, 15, 20, 15, 10, 5, 0)
        ]),
        "Lights Check": TestScenario("Lights Check", [
            TestStep("HEADLIGHTS_SWITCH", "0", "1", 1.0),
            TestStep("HIGH_BEAM_LIGHTS_SWITCH", "0", "1", 1.0),
            TestStep("HIGH_BEAM_LIGHTS_SWITCH", "0", "0", 1.0),
            TestStep("HAZARD_LIGHTS_SWITCH", "0", "true", 1.0),
            TestStep("HAZARD_LIGHTS_SWITCH", "0", "false", 0.5),
            TestStep("HEADLIGHTS_SWITCH", "0", "0", 0.5),
        ]),
    }


class ScenarioRunner:
    """Executes a scenario's steps sequentially on a background thread."""

    def __init__(self, ctx: AppContext, scenario: TestScenario, result_queue: "queue.Queue", stop_event: threading.Event) -> None:
        self.ctx = ctx
        self.scenario = scenario
        self.result_queue = result_queue
        self.stop_event = stop_event

    def run(self) -> None:
        by_name = {p.name: p for p in self.ctx.properties if p.name}
        for idx, step in enumerate(self.scenario.steps):
            if self.stop_event.is_set():
                self.result_queue.put(("stopped", idx, "Stopped by user"))
                return
            self.result_queue.put(("running", idx, ""))
            prop = by_name.get(step.property_name)
            if prop is None:
                self.result_queue.put(("skipped", idx, "Not supported by this device"))
                continue
            area = step.area_id or (prop.area_ids[0] if prop.area_ids else "0")
            try:
                result = self.ctx.car.set_property_value(self.ctx.serial, prop.prop_id_hex, area, step.value)
            except Exception as exc:  # noqa: BLE001
                self.result_queue.put(("fail", idx, str(exc)))
                continue
            if not result.ok:
                self.result_queue.put(("fail", idx, result.combined.strip()[:200] or "Command failed"))
                continue
            if self.stop_event.wait(max(0.0, step.delay_after)):
                self.result_queue.put(("stopped", idx, "Stopped by user"))
                return
            if step.verify:
                get_result = self.ctx.car.get_property_value(self.ctx.serial, prop.prop_id_hex, area)
                ok = step.value.strip() and step.value.strip() in get_result.combined
                detail = get_result.combined.strip()[:200]
                self.result_queue.put(("pass" if ok else "fail", idx, detail or "verified"))
            else:
                self.result_queue.put(("pass", idx, "Set OK"))
        self.result_queue.put(("done", -1, ""))


class ScenarioPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.scenarios: Dict[str, TestScenario] = _builtin_scenarios()
        self.current = self.scenarios["HVAC Smoke Test"]
        self._result_queue: "queue.Queue" = queue.Queue()
        self._stop_event = threading.Event()
        self._running = False
        self._build_ui()
        self._load_scenario_into_table()

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        ttkb.Label(top, text="Scenario:").pack(side="left")
        self.scenario_var = tk.StringVar(value=self.current.name)
        self.scenario_combo = ttkb.Combobox(
            top, textvariable=self.scenario_var, values=list(self.scenarios.keys()),
            state="readonly", width=26,
        )
        self.scenario_combo.pack(side="left", padx=(4, 12))
        self.scenario_combo.bind("<<ComboboxSelected>>", self._on_scenario_pick)

        ttkb.Button(top, text="New", bootstyle="secondary-outline", command=self._new_scenario).pack(side="left", padx=2)
        ttkb.Button(top, text="Load…", bootstyle="secondary-outline", command=self._load_from_file).pack(side="left", padx=2)
        ttkb.Button(top, text="Save…", bootstyle="secondary-outline", command=self._save_to_file).pack(side="left", padx=2)

        self.run_btn = ttkb.Button(top, text="▶ Run", bootstyle="success", command=self._run)
        self.run_btn.pack(side="right", padx=2)
        self.stop_btn = ttkb.Button(top, text="■ Stop", bootstyle="danger-outline", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right", padx=2)

        columns = ("idx", "property", "area", "value", "delay", "verify", "status", "detail")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10)
        headers = {"idx": "#", "property": "Property", "area": "Area", "value": "Value",
                   "delay": "Delay (s)", "verify": "Verify", "status": "Status", "detail": "Detail"}
        widths = {"idx": 30, "property": 200, "area": 50, "value": 80,
                  "delay": 70, "verify": 55, "status": 80, "detail": 260}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(0, 6))
        self.tree.tag_configure("pass", background="#173d1a")
        self.tree.tag_configure("fail", background="#4a1414")
        self.tree.tag_configure("running", background="#4a3b12")
        self.tree.tag_configure("skipped", background="#2a2a2a")
        self.tree.tag_configure("stopped", background="#2a2a2a")

        editor = ttkb.Labelframe(self, text="Add Step", padding=8, bootstyle="primary")
        editor.pack(fill="x")
        ttkb.Label(editor, text="Property:").grid(row=0, column=0, sticky="w")
        self.new_prop_var = tk.StringVar()
        self.prop_combo = ttkb.Combobox(editor, textvariable=self.new_prop_var, width=24)
        self.prop_combo.grid(row=0, column=1, padx=4)
        ttkb.Label(editor, text="Area:").grid(row=0, column=2, sticky="w")
        self.new_area_var = tk.StringVar(value="0")
        ttkb.Entry(editor, textvariable=self.new_area_var, width=6).grid(row=0, column=3, padx=4)
        ttkb.Label(editor, text="Value:").grid(row=0, column=4, sticky="w")
        self.new_value_var = tk.StringVar()
        ttkb.Entry(editor, textvariable=self.new_value_var, width=10).grid(row=0, column=5, padx=4)
        ttkb.Label(editor, text="Delay:").grid(row=0, column=6, sticky="w")
        self.new_delay_var = tk.StringVar(value="1.0")
        ttkb.Entry(editor, textvariable=self.new_delay_var, width=6).grid(row=0, column=7, padx=4)
        self.new_verify_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(editor, text="Verify", variable=self.new_verify_var, bootstyle="round-toggle").grid(row=0, column=8, padx=8)
        ttkb.Button(editor, text="+ Add", bootstyle="success-outline", command=self._add_step).grid(row=0, column=9, padx=4)
        ttkb.Button(editor, text="Remove Selected", bootstyle="danger-outline", command=self._remove_selected).grid(row=0, column=10, padx=4)

        self.ctx.on_properties_updated(self._on_properties_updated)

    def _on_properties_updated(self, properties) -> None:
        names = sorted({p.name for p in properties if p.name})
        self.prop_combo.configure(values=names)

    def _load_scenario_into_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, step in enumerate(self.current.steps):
            self.tree.insert("", "end", iid=str(idx), values=(
                idx, step.property_name, step.area_id, step.value,
                step.delay_after, "yes" if step.verify else "no", "pending", "",
            ))

    def _on_scenario_pick(self, _event=None) -> None:
        self.current = self.scenarios[self.scenario_var.get()]
        self._load_scenario_into_table()

    def _new_scenario(self) -> None:
        name = f"Custom {len(self.scenarios) + 1}"
        self.current = TestScenario(name, [])
        self.scenarios[name] = self.current
        self.scenario_combo.configure(values=list(self.scenarios.keys()))
        self.scenario_var.set(name)
        self._load_scenario_into_table()

    def _add_step(self) -> None:
        try:
            delay = float(self.new_delay_var.get())
        except ValueError:
            delay = 1.0
        step = TestStep(
            property_name=self.new_prop_var.get().strip(),
            area_id=self.new_area_var.get().strip() or "0",
            value=self.new_value_var.get().strip(),
            delay_after=delay,
            verify=self.new_verify_var.get(),
        )
        if not step.property_name:
            return
        self.current.steps.append(step)
        self._load_scenario_into_table()

    def _remove_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        indices = sorted((int(i) for i in selection), reverse=True)
        for idx in indices:
            if 0 <= idx < len(self.current.steps):
                del self.current.steps[idx]
        self._load_scenario_into_table()

    def _save_to_file(self) -> None:
        path = filedialog.asksaveasfilename(title="Save scenario", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.current.to_dict(), fh, indent=2)
        self.ctx.notify_status(f"Saved scenario to {path}", "success")

    def _load_from_file(self) -> None:
        path = filedialog.askopenfilename(title="Load scenario", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            scenario = TestScenario.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            self.ctx.notify_status(f"Failed to load scenario: {exc}", "error")
            return
        self.scenarios[scenario.name] = scenario
        self.current = scenario
        self.scenario_combo.configure(values=list(self.scenarios.keys()))
        self.scenario_var.set(scenario.name)
        self._load_scenario_into_table()

    def _run(self) -> None:
        if self._running or not self.ctx.serial or not self.current.steps:
            if not self.ctx.serial:
                self.ctx.notify_status("Select a device before running a scenario.", "warning")
            return
        self._running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._stop_event = threading.Event()
        self._result_queue = queue.Queue()
        for idx in range(len(self.current.steps)):
            self.tree.item(str(idx), values=self._row_values(idx, "pending", ""), tags=())
        runner = ScenarioRunner(self.ctx, self.current, self._result_queue, self._stop_event)
        threading.Thread(target=runner.run, daemon=True).start()
        self._poll_results()

    def _row_values(self, idx: int, status: str, detail: str):
        step = self.current.steps[idx]
        label = STATUS_STYLES.get(status, ("secondary", status.upper()))[1]
        return (idx, step.property_name, step.area_id, step.value, step.delay_after,
                "yes" if step.verify else "no", label, detail)

    def _poll_results(self) -> None:
        try:
            while True:
                status, idx, detail = self._result_queue.get_nowait()
                if status == "done":
                    self._running = False
                    self.run_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.ctx.notify_status(f"Scenario '{self.current.name}' complete", "success")
                    return
                if 0 <= idx < len(self.current.steps):
                    self.tree.item(str(idx), values=self._row_values(idx, status, detail), tags=(status,))
                if status == "stopped":
                    self._running = False
                    self.run_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    return
        except queue.Empty:
            pass
        if self._running:
            self.after(150, self._poll_results)

    def _stop(self) -> None:
        self._stop_event.set()


class SnapshotPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.snapshot_a: Optional[Dict[str, Dict[str, str]]] = None
        self.snapshot_b: Optional[Dict[str, Dict[str, str]]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        ttkb.Button(top, text="Capture Snapshot A", bootstyle="info-outline", command=lambda: self._capture("a")).pack(side="left", padx=4)
        self.label_a = ttkb.Label(top, text="A: (none)", bootstyle="secondary")
        self.label_a.pack(side="left", padx=(0, 16))
        ttkb.Button(top, text="Capture Snapshot B", bootstyle="info-outline", command=lambda: self._capture("b")).pack(side="left", padx=4)
        self.label_b = ttkb.Label(top, text="B: (none)", bootstyle="secondary")
        self.label_b.pack(side="left", padx=(0, 16))
        ttkb.Button(top, text="Compare A → B", bootstyle="success-outline", command=self._compare).pack(side="left", padx=4)

        columns = ("property", "area", "value_a", "value_b")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        for col, text, width in (
            ("property", "Property", 220), ("area", "Area", 60),
            ("value_a", "Snapshot A", 180), ("value_b", "Snapshot B", 180),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=(6, 0))
        self.tree.tag_configure("changed", background="#4a3b12")
        self.tree.tag_configure("added", background="#173d1a")
        self.tree.tag_configure("removed", background="#4a1414")

    def _snapshot_now(self) -> Dict[str, Dict[str, str]]:
        return {p.name: dict(p.area_values) for p in self.ctx.properties if p.name}

    def _capture(self, which: str) -> None:
        snap = self._snapshot_now()
        stamp = datetime.now().strftime("%H:%M:%S")
        if which == "a":
            self.snapshot_a = snap
            self.label_a.configure(text=f"A: {len(snap)} properties @ {stamp}")
        else:
            self.snapshot_b = snap
            self.label_b.configure(text=f"B: {len(snap)} properties @ {stamp}")

    def _compare(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.snapshot_a is None or self.snapshot_b is None:
            self.ctx.notify_status("Capture both snapshots A and B before comparing.", "warning")
            return
        names = sorted(set(self.snapshot_a) | set(self.snapshot_b))
        diff_count = 0
        for name in names:
            areas_a = self.snapshot_a.get(name)
            areas_b = self.snapshot_b.get(name)
            if areas_a is None:
                self.tree.insert("", "end", values=(name, "-", "(absent)", ", ".join(areas_b.values())), tags=("added",))
                diff_count += 1
                continue
            if areas_b is None:
                self.tree.insert("", "end", values=(name, "-", ", ".join(areas_a.values()), "(absent)"), tags=("removed",))
                diff_count += 1
                continue
            all_areas = sorted(set(areas_a) | set(areas_b))
            for area in all_areas:
                va, vb = areas_a.get(area, "-"), areas_b.get(area, "-")
                if va != vb:
                    self.tree.insert("", "end", values=(name, area, va, vb), tags=("changed",))
                    diff_count += 1
        self.ctx.notify_status(f"Snapshot diff: {diff_count} differences found", "info")


class MonitorPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.watched: List[str] = []
        self.poller: Optional[Poller] = None
        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        ttkb.Label(top, text="Property:").pack(side="left")
        self.prop_var = tk.StringVar()
        self.prop_combo = ttkb.Combobox(top, textvariable=self.prop_var, width=26)
        self.prop_combo.pack(side="left", padx=4)
        ttkb.Button(top, text="+ Watch", bootstyle="info-outline", command=self._add_watch).pack(side="left", padx=4)
        ttkb.Button(top, text="Clear Watches", bootstyle="secondary-outline", command=self._clear_watches).pack(side="left", padx=4)

        ttkb.Label(top, text="Interval (s):").pack(side="left", padx=(16, 4))
        self.interval_var = tk.StringVar(value="2")
        ttkb.Spinbox(top, from_=1, to=60, textvariable=self.interval_var, width=5).pack(side="left")

        self.start_btn = ttkb.Button(top, text="▶ Start Monitor", bootstyle="success", command=self._start)
        self.start_btn.pack(side="right", padx=4)
        self.stop_btn = ttkb.Button(top, text="■ Stop", bootstyle="danger-outline", command=self._stop, state="disabled")
        self.stop_btn.pack(side="right", padx=4)

        self.watch_label = ttkb.Label(self, text="Watching: (none)", bootstyle="secondary")
        self.watch_label.pack(anchor="w", pady=(0, 4))

        self.log_widget = ttkb.ScrolledText(self, auto_hide=False, wrap="none", font=("Consolas", 9), height=20)
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.text.configure(state="disabled")
        self._max_lines = 3000

        self.ctx.on_properties_updated(self._on_properties_updated)
        self.ctx.on_device_changed(lambda _d: self._stop())

    def _on_properties_updated(self, properties) -> None:
        names = sorted({p.name for p in properties if p.name})
        self.prop_combo.configure(values=names)

    def _add_watch(self) -> None:
        name = self.prop_var.get().strip()
        if name and name not in self.watched:
            self.watched.append(name)
            self.watch_label.configure(text="Watching: " + ", ".join(self.watched))

    def _clear_watches(self) -> None:
        self.watched.clear()
        self.watch_label.configure(text="Watching: (none)")

    def _start(self) -> None:
        if not self.ctx.serial or not self.watched:
            self.ctx.notify_status("Select a device and add at least one watched property.", "warning")
            return
        try:
            interval = max(1, int(float(self.interval_var.get())))
        except ValueError:
            interval = 2
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        def task():
            serial = self.ctx.serial
            by_name = {p.name: p for p in self.ctx.properties if p.name}
            lines = []
            stamp = datetime.now().strftime("%H:%M:%S")
            for name in self.watched:
                prop = by_name.get(name)
                if prop is None:
                    lines.append(f"[{stamp}] {name}: not supported")
                    continue
                area = prop.area_ids[0] if prop.area_ids else "0"
                result = self.ctx.car.get_property_value(serial, prop.prop_id_hex, area)
                lines.append(f"[{stamp}] {name}[{area}] = {result.combined.strip()[:150]}")
            return lines

        def on_result(lines: List[str]) -> None:
            widget = self.log_widget.text
            widget.configure(state="normal")
            for line in lines:
                widget.insert("end", line + "\n")
            overflow = int(widget.index("end-1c").split(".")[0]) - self._max_lines
            if overflow > 0:
                widget.delete("1.0", f"{overflow + 1}.0")
            widget.configure(state="disabled")
            widget.see("end")

        def on_error(exc: BaseException) -> None:
            self.ctx.notify_status(f"Monitor error: {exc}", "error")

        self.poller = Poller(self, interval * 1000, task, on_result, on_error)
        self.poller.start()

    def _stop(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller = None
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")


class RawShellPanel(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self.history: List[str] = []
        self.history_idx = -1
        self._build_ui()

    def _build_ui(self) -> None:
        ttkb.Label(
            self,
            text="Runs `adb -s <device> shell <command>` directly - useful for troubleshooting "
                 "beyond the built-in property tools (e.g. dumpsys, ps, custom car_service sub-commands).",
            bootstyle="secondary", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        entry_row = ttk.Frame(self)
        entry_row.pack(fill="x", pady=(0, 6))
        self.command_var = tk.StringVar()
        entry = ttkb.Entry(entry_row, textvariable=self.command_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._run())
        entry.bind("<Up>", self._history_up)
        entry.bind("<Down>", self._history_down)
        ttkb.Button(entry_row, text="Run", bootstyle="success", command=self._run).pack(side="left", padx=4)

        quick_row = ttk.Frame(self)
        quick_row.pack(fill="x", pady=(0, 6))
        ttkb.Label(quick_row, text="Quick commands:", bootstyle="secondary").pack(side="left")
        for label, cmd in (
            ("dumpsys car_service", "dumpsys car_service"),
            ("getprop (build info)", "getprop ro.build.fingerprint"),
            ("ps car_service", "ps -A | grep car"),
        ):
            ttkb.Button(
                quick_row, text=label, bootstyle="link",
                command=lambda c=cmd: (self.command_var.set(c), self._run()),
            ).pack(side="left", padx=4)

        self.output = ttkb.ScrolledText(self, auto_hide=False, wrap="word", font=("Consolas", 9))
        self.output.pack(fill="both", expand=True)
        self.output.text.configure(state="disabled")

    def _history_up(self, _event) -> None:
        if not self.history:
            return
        self.history_idx = max(0, self.history_idx - 1)
        self.command_var.set(self.history[self.history_idx])

    def _history_down(self, _event) -> None:
        if not self.history:
            return
        self.history_idx = min(len(self.history), self.history_idx + 1)
        if self.history_idx == len(self.history):
            self.command_var.set("")
        else:
            self.command_var.set(self.history[self.history_idx])

    def _run(self) -> None:
        command = self.command_var.get().strip()
        if not command:
            return
        if not self.ctx.serial:
            self.ctx.notify_status("Select a device first.", "warning")
            return
        self.history.append(command)
        self.history_idx = len(self.history)
        self._write(f"$ adb shell {command}\n")

        def task():
            return self.ctx.car.run_custom_shell(self.ctx.serial, command, timeout=20)

        def done(result) -> None:
            self._write(result.combined or "(no output)")
            self._write("\n")

        def error(exc: BaseException) -> None:
            self._write(f"ERROR: {exc}\n")

        run_async(self.ctx.root, task, done, error)

    def _write(self, text: str) -> None:
        widget = self.output.text
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.configure(state="disabled")
        widget.see("end")


class CommandLogPanel(ttk.Frame):
    """Live view of every ADB request/response the app has made (see
    app/command_log.py), independent of - and in addition to - the
    continuous rotating file log written automatically on disk."""

    MAX_ROWS = 2000

    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        self._row_to_entry: Dict[str, CommandLogEntry] = {}
        self._build_ui()
        command_logger.add_listener(self._on_new_entry)
        self.bind("<Destroy>", self._on_destroy)
        self._reload()

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            command_logger.remove_listener(self._on_new_entry)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 6))
        ttkb.Label(toolbar, text="ADB Request / Response Log", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttkb.Button(toolbar, text="📁 Open Logs Folder", bootstyle="secondary-outline", command=self._open_folder).pack(side="right", padx=2)
        ttkb.Button(toolbar, text="⬇ Export", bootstyle="secondary-outline", command=self._export).pack(side="right", padx=2)
        ttkb.Button(toolbar, text="🗑 Clear View", bootstyle="danger-outline", command=self._clear).pack(side="right", padx=2)

        ttkb.Label(
            self,
            text="Every ADB command this app runs (device scans, get/set/inject, dumps, ...) shows up here live, "
                 f"and is continuously appended to a rotating log file at: {LOG_DIR / 'adb_commands.log'}",
            bootstyle="secondary", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", pady=(0, 6))
        ttkb.Label(filter_bar, text="Status:").pack(side="left")
        self.status_var = tk.StringVar(value="All")
        status_combo = ttkb.Combobox(filter_bar, textvariable=self.status_var, values=["All", "OK", "FAIL"], state="readonly", width=8)
        status_combo.pack(side="left", padx=(4, 12))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self._reload())

        ttkb.Label(filter_bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttkb.Entry(filter_bar, textvariable=self.search_var, width=30).pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", debounce(self, 200, self._reload))

        self.count_label = ttkb.Label(filter_bar, text="0 commands", bootstyle="secondary")
        self.count_label.pack(side="right")

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True)

        table_frame = ttk.Frame(paned)
        columns = ("time", "duration", "status", "command")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        widths = {"time": 130, "duration": 70, "status": 55, "command": 650}
        labels = {"time": "Time", "duration": "ms", "status": "Status", "command": "Command"}
        for col in columns:
            self.tree.heading(col, text=labels[col])
            self.tree.column(col, width=widths[col], anchor="w")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("fail", background="#4a1414")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(table_frame, weight=3)

        detail_frame = ttk.Frame(paned, padding=(0, 6, 0, 0))
        ttkb.Label(detail_frame, text="Response:").pack(anchor="w")
        self.detail_text = ttkb.ScrolledText(detail_frame, height=8, auto_hide=True, wrap="word", font=("Consolas", 9))
        self.detail_text.pack(fill="both", expand=True)
        self.detail_text.text.configure(state="disabled")
        paned.add(detail_frame, weight=2)

    def _matches(self, entry: CommandLogEntry) -> bool:
        status = self.status_var.get()
        if status == "OK" and not entry.success:
            return False
        if status == "FAIL" and entry.success:
            return False
        query = self.search_var.get().strip().lower()
        if query and query not in entry.command.lower() and query not in entry.response.lower():
            return False
        return True

    def _reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_to_entry.clear()
        entries = [e for e in command_logger.entries() if self._matches(e)]
        for entry in entries[-self.MAX_ROWS:]:
            self._insert_row(entry)
        self.count_label.configure(text=f"{len(entries)} commands")

    def _insert_row(self, entry: CommandLogEntry) -> str:
        tags = () if entry.success else ("fail",)
        item_id = self.tree.insert(
            "", "end",
            values=(entry.timestamp, f"{entry.duration_ms:.0f}", "OK" if entry.success else "FAIL", entry.command),
            tags=tags,
        )
        self._row_to_entry[item_id] = entry
        children = self.tree.get_children()
        if len(children) > self.MAX_ROWS:
            oldest = children[0]
            self._row_to_entry.pop(oldest, None)
            self.tree.delete(oldest)
        return item_id

    def _on_new_entry(self, entry: CommandLogEntry) -> None:
        def apply_() -> None:
            if not self.winfo_exists():
                return
            if self._matches(entry):
                self._insert_row(entry)
                self.count_label.configure(text=f"{len(self.tree.get_children())} commands (live)")

        try:
            self.after(0, apply_)
        except (RuntimeError, tk.TclError):
            pass

    def _on_select(self, _event) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        entry = self._row_to_entry.get(selection[0])
        if entry is None:
            return
        widget = self.detail_text.text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", f"$ {entry.command}\n\n{entry.response}")
        widget.configure(state="disabled")

    def _clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_to_entry.clear()
        self.count_label.configure(text="0 commands (view cleared - the file log on disk is untouched)")

    def _open_folder(self) -> None:
        try:
            open_in_file_manager(LOG_DIR)
        except OSError as exc:
            self.ctx.notify_status(f"Could not open logs folder: {exc}", "error")

    def _export(self) -> None:
        entries = command_logger.entries()
        if not entries:
            Messagebox.show_info("No commands logged yet.", title="Nothing to export", parent=self.ctx.root)
            return
        path = filedialog.asksaveasfilename(
            title="Export command log", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("CSV", "*.csv"), ("Text", "*.txt")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(["Time", "Duration(ms)", "Status", "Command", "Response"])
                    for e in entries:
                        writer.writerow([e.timestamp, f"{e.duration_ms:.0f}", "OK" if e.success else "FAIL", e.command, e.response])
            elif path.lower().endswith(".txt"):
                with open(path, "w", encoding="utf-8") as fh:
                    for e in entries:
                        fh.write(
                            f"[{e.timestamp}] {'OK' if e.success else 'FAIL'} ({e.duration_ms:.0f}ms) $ {e.command}\n"
                            f"    -> {e.response}\n\n"
                        )
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump([asdict(e) for e in entries], fh, indent=2)
            self.ctx.notify_status(f"Exported {len(entries)} log entries to {path}", "success")
            Messagebox.show_info(f"Exported {len(entries)} entries to:\n{path}", title="Export complete", parent=self.ctx.root)
        except OSError as exc:
            Messagebox.show_error(f"Could not write file:\n{exc}", title="Export failed", parent=self.ctx.root)


class TestingTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=8)
        self.ctx = ctx
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 6))
        ttkb.Label(header, text="Testing & Diagnostics", font=("Segoe UI", 14, "bold")).pack(side="left")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.scenario_panel = ScenarioPanel(notebook, ctx)
        notebook.add(self.scenario_panel, text="Scenario Runner")

        self.snapshot_panel = SnapshotPanel(notebook, ctx)
        notebook.add(self.snapshot_panel, text="Snapshot Diff")

        self.monitor_panel = MonitorPanel(notebook, ctx)
        notebook.add(self.monitor_panel, text="Live Monitor")

        self.shell_panel = RawShellPanel(notebook, ctx)
        notebook.add(self.shell_panel, text="Raw ADB Shell")

        self.command_log_panel = CommandLogPanel(notebook, ctx)
        notebook.add(self.command_log_panel, text="Command Log")

    def shutdown(self) -> None:
        self.monitor_panel._stop()
        self.scenario_panel._stop()
