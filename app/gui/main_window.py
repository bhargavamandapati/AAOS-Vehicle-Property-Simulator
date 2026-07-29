"""Top-level window: device selection bar, tabbed notebook, status bar."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

import ttkbootstrap as ttkb

from app.adb_manager import DeviceInfo, adb_manager
from app.car_service import CarServiceClient
from app.config import config
from app.gui.context import AppContext
from app.gui.tabs.dashboard_tab import DashboardTab
from app.gui.tabs.logcat_tab import LogcatTab
from app.gui.tabs.processes_tab import ProcessesTab
from app.gui.tabs.properties_tab import PropertiesTab
from app.gui.tabs.screenshot_tab import ScreenshotTab
from app.gui.tabs.settings_tab import SettingsTab
from app.gui.tabs.testing_tab import TestingTab
from app.gui.theme import APP_ICON_TEXT, APP_TITLE, TAB_ICONS, apply_notebook_theme, apply_treeview_theme
from app.gui.widgets import StatusPill
from app.utils.workers import Poller, run_async

_STATUS_STYLE = {"info": "secondary", "success": "success", "warning": "warning", "error": "danger"}


class MainWindow:
    def __init__(self) -> None:
        self.root = ttkb.Window(
            title=APP_TITLE,
            themename=config.get("theme", "darkly"),
            size=(1560, 900),
            minsize=(1150, 700),
        )
        self.style = ttkb.Style()
        apply_treeview_theme(self.style)
        apply_notebook_theme(self.style)
        self.car = CarServiceClient(adb_manager)
        self.ctx = AppContext(self.root, adb_manager, self.car)
        self.ctx.status_callback = self._set_status
        self.ctx.request_property_refresh = self._refresh_properties

        self._device_by_display: Dict[str, DeviceInfo] = {}
        self._adb_version: str = ""

        self._build_ui()
        self._refresh_adb_status(revalidate_version=True)

        device_interval = max(1, int(config.get("device_poll_seconds", 4))) * 1000
        self._device_poller = Poller(self.root, device_interval, adb_manager.list_devices, self._on_devices_result, self._on_devices_error)
        self._device_poller.start()

        self._property_poller: Optional[Poller] = None
        prop_interval = int(config.get("property_poll_seconds", 0))
        if prop_interval > 0:
            self._property_poller = Poller(
                self.root, prop_interval * 1000, self._fetch_properties_task,
                self._on_properties_result, self._on_properties_error,
            )
            self._property_poller.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI construction ------------------------------------------------
    def _build_ui(self) -> None:
        topbar = ttkb.Frame(self.root, padding=(12, 8))
        topbar.pack(fill="x")

        # Right-side items are packed first so they always claim their
        # space from the window's right edge - if the window is narrow
        # or a label on the left grows long, it is the flexible label
        # that gets truncated, never the Settings button.
        ttkb.Button(
            topbar, text="⚙ Settings", bootstyle="info-outline",
            command=lambda: self.notebook.select(self.settings_tab),
        ).pack(side="right")

        ttkb.Label(topbar, text=f"{APP_ICON_TEXT} {APP_TITLE}", font=("Segoe UI", 15, "bold")).pack(side="left")

        self.adb_pill = StatusPill(topbar, text="ADB: checking…", bootstyle="secondary")
        self.adb_pill.pack(side="left", padx=(16, 4))

        ttkb.Label(topbar, text="Device:").pack(side="left", padx=(16, 4))
        self.device_var = tk.StringVar()
        self.device_combo = ttkb.Combobox(topbar, textvariable=self.device_var, width=40, state="readonly")
        self.device_combo.pack(side="left")
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        ttkb.Button(topbar, text="⟳", width=3, bootstyle="info-outline", command=self._manual_device_refresh).pack(side="left", padx=4)

        self.device_info_label = ttkb.Label(topbar, text="No device selected", bootstyle="secondary", width=42, anchor="w")
        self.device_info_label.pack(side="left", padx=14, fill="x", expand=True)

        ttk.Separator(self.root, orient="horizontal").pack(fill="x")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)

        self.dashboard_tab = DashboardTab(self.notebook, self.ctx)
        self.notebook.add(self.dashboard_tab, text=f"{TAB_ICONS['dashboard']}Dashboard")

        self.properties_tab = PropertiesTab(self.notebook, self.ctx)
        self.notebook.add(self.properties_tab, text=f"{TAB_ICONS['properties']}All Properties")

        self.logcat_tab = LogcatTab(self.notebook, self.ctx)
        self.notebook.add(self.logcat_tab, text=f"{TAB_ICONS['logcat']}Logcat Console")

        self.testing_tab = TestingTab(self.notebook, self.ctx)
        self.notebook.add(self.testing_tab, text=f"{TAB_ICONS['testing']}Testing")

        self.screenshot_tab = ScreenshotTab(self.notebook, self.ctx)
        self.notebook.add(self.screenshot_tab, text=f"{TAB_ICONS['screenshot']}Screenshot")

        self.processes_tab = ProcessesTab(self.notebook, self.ctx)
        self.notebook.add(self.processes_tab, text=f"{TAB_ICONS['processes']}Processes")

        self.settings_tab = SettingsTab(self.notebook, self.ctx, self.style)
        self.notebook.add(self.settings_tab, text=f"{TAB_ICONS['settings']}Settings")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", side="bottom")
        statusbar = ttkb.Frame(self.root, padding=(12, 4))
        statusbar.pack(fill="x", side="bottom")
        self.status_label = ttkb.Label(statusbar, text="Ready", bootstyle="secondary")
        self.status_label.pack(side="left")
        self.busy_bar = ttkb.Progressbar(statusbar, mode="indeterminate", bootstyle="info-striped", length=140)
        self.adb_path_label = ttkb.Label(statusbar, text="", bootstyle="secondary")
        self.adb_path_label.pack(side="right")
        self.ctx.busy_callback = self._set_busy

    # -- ADB status ----------------------------------------------------
    def _refresh_adb_status(self, revalidate_version: bool = False) -> None:
        available = adb_manager.is_available()
        if available:
            self.adb_pill.set_status(f"ADB: {self._adb_version or 'Found'}", "success")
            self.adb_path_label.configure(text=adb_manager.adb_path or "")
        else:
            self.adb_pill.set_status("ADB: Not Found", "danger")
            self.adb_path_label.configure(text="Configure adb path in Settings")
        if revalidate_version and available:
            run_async(self.root, adb_manager.version, self._on_version_result, lambda _exc: None)

    def _on_version_result(self, version: str) -> None:
        self._adb_version = version
        self._refresh_adb_status()

    # -- device discovery -------------------------------------------------
    def _manual_device_refresh(self) -> None:
        run_async(self.root, adb_manager.list_devices, self._on_devices_result, self._on_devices_error)

    def _on_devices_error(self, exc: BaseException) -> None:
        self._set_status(f"Device scan failed: {exc}", "error")

    def _on_devices_result(self, devices: List[DeviceInfo]) -> None:
        self.ctx.devices = devices
        self._refresh_adb_status()

        display_names = []
        self._device_by_display.clear()
        for device in devices:
            label = f"{device.display_name()} [{device.state}]"
            display_names.append(label)
            self._device_by_display[label] = device
        self.device_combo.configure(values=display_names)

        if self.ctx.current_device is not None:
            self._sync_current_device(devices)
            return

        ready_devices = [d for d in devices if d.is_ready]
        if len(ready_devices) == 1:
            only = ready_devices[0]
            self.device_var.set(f"{only.display_name()} [{only.state}]")
            self._select_device(only)

    def _sync_current_device(self, devices: List[DeviceInfo]) -> None:
        current = self.ctx.current_device
        if current is None:
            return
        match = next((d for d in devices if d.serial == current.serial), None)
        if match is None:
            self.ctx.set_current_device(None)
            self.device_var.set("")
            self.device_info_label.configure(text="Device disconnected")
            return
        became_ready = match.is_ready and not current.is_ready
        if became_ready:
            self._select_device(match)
        else:
            self.ctx.current_device = match
        label = f"{match.display_name()} [{match.state}]"
        if self.device_var.get() != label:
            self.device_var.set(label)

    def _on_device_selected(self, _event) -> None:
        device = self._device_by_display.get(self.device_var.get())
        if device is not None:
            self._select_device(device)

    def _select_device(self, device: DeviceInfo) -> None:
        self.ctx.set_current_device(device)
        if not device.is_ready:
            self.device_info_label.configure(text=f"{device.serial}: {device.state}")
            self.ctx.set_properties([])
            return

        self.device_info_label.configure(text="Loading device info…")

        def task():
            model = adb_manager.get_prop(device.serial, "ro.product.model")
            version = adb_manager.get_prop(device.serial, "ro.build.version.release")
            sdk = adb_manager.get_prop(device.serial, "ro.build.version.sdk")
            return model, version, sdk

        def done(result) -> None:
            model, version, sdk = result
            text = f"{model or device.serial}  •  Android {version or '?'} (SDK {sdk or '?'})"
            self.device_info_label.configure(text=text)

        def error(_exc: BaseException) -> None:
            self.device_info_label.configure(text=device.display_name())

        run_async(self.root, task, done, error)
        self._refresh_properties()

    # -- properties -----------------------------------------------------
    def _fetch_properties_task(self):
        serial = self.ctx.serial
        if not serial:
            return []
        return self.car.list_properties(serial)

    def _refresh_properties(self) -> None:
        if not self.ctx.serial:
            return
        self._set_status("Scanning vehicle properties…", "info")
        self.ctx.begin_busy("Scanning vehicle properties…")
        run_async(self.root, self._fetch_properties_task, self._on_properties_result, self._on_properties_error)

    def _on_properties_result(self, properties) -> None:
        self.ctx.end_busy()
        self.ctx.set_properties(properties)
        if properties:
            self._set_status(f"Loaded {len(properties)} vehicle properties", "success")
        else:
            self._set_status(
                "No properties parsed - check the Raw Dump in All Properties, or adjust "
                "command templates in Settings if your build uses different syntax.", "warning",
            )

    def _on_properties_error(self, exc: BaseException) -> None:
        self.ctx.end_busy()
        self._set_status(f"Failed to load properties: {exc}", "error")

    # -- status bar ---------------------------------------------------------
    def _set_status(self, message: str, level: str = "info") -> None:
        self.status_label.configure(text=message, bootstyle=_STATUS_STYLE.get(level, "secondary"))

    def _set_busy(self, busy: bool, message: str) -> None:
        if busy:
            self._set_status(message, "info")
            self.busy_bar.pack(side="left", padx=10)
            self.busy_bar.start(12)
        else:
            try:
                self.busy_bar.stop()
                self.busy_bar.pack_forget()
            except tk.TclError:
                pass

    # -- lifecycle -----------------------------------------------------
    def _on_close(self) -> None:
        try:
            self._device_poller.stop()
            if self._property_poller is not None:
                self._property_poller.stop()
            self.logcat_tab.shutdown()
            self.testing_tab.shutdown()
            self.screenshot_tab.shutdown()
            self.processes_tab.shutdown()
            config.save()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
