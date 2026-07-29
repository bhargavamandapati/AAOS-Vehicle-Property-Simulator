"""Persistent application settings.

Settings are stored as JSON under the user's platform-appropriate config
directory (%APPDATA% on Windows, ~/.config on Linux/macOS) so the app keeps
user preferences (ADB path override, command templates, theme, buffer
sizes) across runs without touching the project directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

APP_NAME = "AAOSVehiclePropertySimulator"


def _config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


CONFIG_DIR = _config_dir()
CONFIG_FILE = CONFIG_DIR / "settings.json"

# Command templates used to talk to CarService / VHAL over `adb shell`.
# NOTE: The exact `cmd car_service ...` sub-command names have changed
# across AOSP / Android Automotive OS releases and OEM forks. These
# defaults reflect the commonly documented AOSP CarShellCommand syntax,
# but if your build uses different flags, edit them here (Settings tab)
# without touching any code - {prop_id}, {area_id}, {value} and
# {error_code} are substituted at call time.
DEFAULT_COMMAND_TEMPLATES = {
    "get_property": "cmd car_service get-property-value {prop_id} {area_id}",
    "set_property": "cmd car_service set-property-value {prop_id} {area_id} {value}",
    "inject_event": "cmd car_service inject-vhal-event {prop_id} {area_id} {value}",
    "inject_error": "cmd car_service inject-error-event {prop_id} {area_id} {error_code}",
    "dump_properties": "cmd car_service get-carpropertyconfig",
    "dump_full": "dumpsys car_service",
}

DEFAULTS: Dict[str, Any] = {
    "adb_path": "",  # empty = auto-detect from PATH / SDK env vars
    "theme": "darkly",
    "command_templates": dict(DEFAULT_COMMAND_TEMPLATES),
    "logcat_buffer_lines": 4000,
    "logcat_flush_ms": 120,
    "device_poll_seconds": 4,
    # 0 = no automatic re-scanning of the (potentially large/slow) full
    # property dump; refresh happens on device connect + manual refresh.
    "property_poll_seconds": 0,
    "window_geometry": "",
}


class Config:
    """Simple JSON-backed settings store, loaded once at import time."""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                    saved = json.load(fh)
                for key, value in saved.items():
                    if key == "command_templates" and isinstance(value, dict):
                        merged = dict(DEFAULT_COMMAND_TEMPLATES)
                        merged.update(value)
                        self._data[key] = merged
                    else:
                        self._data[key] = value
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get_command_template(self, name: str) -> str:
        templates = self._data.get("command_templates", DEFAULT_COMMAND_TEMPLATES)
        return templates.get(name, DEFAULT_COMMAND_TEMPLATES.get(name, ""))

    def set_command_template(self, name: str, template: str) -> None:
        templates = dict(self._data.get("command_templates", DEFAULT_COMMAND_TEMPLATES))
        templates[name] = template
        self._data["command_templates"] = templates

    def reset_command_templates(self) -> None:
        self._data["command_templates"] = dict(DEFAULT_COMMAND_TEMPLATES)


config = Config()
