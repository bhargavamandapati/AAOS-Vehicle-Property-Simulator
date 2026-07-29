"""Shared application state passed to every tab.

Keeping this separate from MainWindow avoids circular imports (tabs need
the context's type/shape, MainWindow needs the tabs) and gives every tab
a single, consistent way to reach the current device, the live property
cache, and to push a message to the status bar.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from app.adb_manager import AdbManager, DeviceInfo
from app.car_service import CarServiceClient, VehicleProperty


class AppContext:
    def __init__(self, root, adb: AdbManager, car: CarServiceClient) -> None:
        self.root = root
        self.adb = adb
        self.car = car
        self.current_device: Optional[DeviceInfo] = None
        self.devices: List[DeviceInfo] = []
        self.properties: List[VehicleProperty] = []

        self._device_listeners: List[Callable[[Optional[DeviceInfo]], None]] = []
        self._property_listeners: List[Callable[[List[VehicleProperty]], None]] = []
        self.status_callback: Optional[Callable[[str, str], None]] = None
        self.request_property_refresh: Optional[Callable[[], None]] = None
        self.busy_callback: Optional[Callable[[bool, str], None]] = None

    # -- subscriptions ---------------------------------------------------
    def on_device_changed(self, callback: Callable[[Optional[DeviceInfo]], None]) -> None:
        self._device_listeners.append(callback)

    def on_properties_updated(self, callback: Callable[[List[VehicleProperty]], None]) -> None:
        self._property_listeners.append(callback)

    # -- mutators (call from the Tk main thread only) --------------------
    def set_current_device(self, device: Optional[DeviceInfo]) -> None:
        self.current_device = device
        for callback in list(self._device_listeners):
            callback(device)

    def set_properties(self, properties: List[VehicleProperty]) -> None:
        self.properties = properties
        for callback in list(self._property_listeners):
            callback(properties)

    def notify_status(self, message: str, level: str = "info") -> None:
        if self.status_callback is not None:
            self.status_callback(message, level)

    def begin_busy(self, message: str = "Working…") -> None:
        if self.busy_callback is not None:
            self.busy_callback(True, message)

    def end_busy(self) -> None:
        if self.busy_callback is not None:
            self.busy_callback(False, "")

    def refresh_properties(self) -> None:
        if self.request_property_refresh is not None:
            self.request_property_refresh()

    @property
    def serial(self) -> Optional[str]:
        return self.current_device.serial if self.current_device else None

    @property
    def is_connected(self) -> bool:
        return self.current_device is not None and self.current_device.is_ready
