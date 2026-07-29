"""Talks to Android Automotive's CarService over `adb shell`.

Two important, honest caveats baked into this module's design:

1. The exact text format of `dumpsys car_service` and the exact
   sub-command names under `adb shell cmd car_service ...` are **not**
   perfectly stable across AOSP releases / OEM forks. Rather than assume
   one exact format, the parser below extracts fields with tolerant,
   generic key/value scanning and always keeps the raw text block per
   property so nothing is ever silently lost - see `raw_block` and the
   Raw Dump viewer in the Properties tab.
2. Property IDs are never hard-coded anywhere in this app. They are
   always read live from the connected device's own dump output, and
   "known" properties (for the Dashboard quick controls) are matched by
   *name* only. This avoids ever sending a guessed/incorrect numeric
   property ID to real hardware.

If your build's command syntax differs, adjust the templates in
Settings -> Command Templates (see app/config.py) without touching code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.adb_manager import AdbManager, CommandResult
from app.config import config

# Tokens that show up as *values* of other fields (access, change mode,
# area type, data type) and must never be mistaken for a property name.
_NON_NAME_TOKENS = {
    "READ", "WRITE", "READ_WRITE", "READ_ONLY", "WRITE_ONLY",
    "STATIC", "ON_CHANGE", "CONTINUOUS",
    "GLOBAL", "WINDOW", "DOOR", "SEAT", "MIRROR", "WHEEL", "VENDOR",
    "BOOLEAN", "STRING", "BYTES", "MIXED",
    "INT32", "INT64", "FLOAT", "INT32_VEC", "INT64_VEC", "FLOAT_VEC",
    "TRUE", "FALSE", "NULL", "NONE", "UNKNOWN",
}

_HEX_TOKEN = re.compile(r"0x[0-9A-Fa-f]{4,9}")
# A record "anchor": a line that mentions a property id AND the word
# "property" nearby - this deliberately excludes plain "areaId: 0x..."
# lines from being mistaken for the start of a new property record.
_ANCHOR_RE = re.compile(r"(?im)^.*\bproperty\b.*?(0x[0-9A-Fa-f]{4,9}).*$")
_FALLBACK_ANCHOR_RE = re.compile(r"(?m)^\s*(0x[0-9A-Fa-f]{4,9})\b")

_NAME_NEAR_KEYWORD_RE = re.compile(r"\bname\b\s*[:=]\s*\"?([A-Za-z][A-Za-z0-9_]{2,})\"?", re.IGNORECASE)
_UPPER_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")

_ACCESS_RE = re.compile(r"\b(READ_WRITE|READ_ONLY|WRITE_ONLY|READ|WRITE)\b")
_CHANGE_MODE_RE = re.compile(r"\b(STATIC|ON_CHANGE|CONTINUOUS)\b")
_AREA_TYPE_RE = re.compile(r"\b(GLOBAL|WINDOW|DOOR|SEAT|MIRROR|WHEEL|VENDOR)\b")
_VALUE_TYPE_RE = re.compile(r"\b(BOOLEAN|STRING|BYTES|MIXED|INT32_VEC|INT64_VEC|FLOAT_VEC|INT32|INT64|FLOAT)\b")

_AREA_VALUE_PAIR_RE = re.compile(
    r"[Aa]rea(?:Id)?\s*[:=]\s*(0x[0-9A-Fa-f]+|-?\d+)[^A-Za-z0-9\-]{0,25}?"
    r"[Vv]alue\s*[:=]\s*([^\n,;]+)"
)
_LONE_VALUE_RE = re.compile(r"[Vv]alue\s*[:=]\s*([^\n,;]+)")

# Real CarPropertyConfig dumps declare each supported area as
# "areaId:GLOBAL(0x0)" / "areaId:DOOR_ROW_1_LEFT(0x1)" with no
# accompanying live value. Capturing these (name + hex id) ensures
# multi-area properties (seats, doors, mirrors, ...) still expose every
# area id they actually support - and a human-readable label for the
# Properties tab's area dropdown - even though this part of the dump has
# no current value for any of them (see CarServiceClient.get_property_value
# / fetch_current_values for how live values are fetched instead).
_AREA_DECL_RE = re.compile(r"areaId:([A-Za-z0-9_]*)\((0x[0-9A-Fa-f]+)\)")

# configArray often enumerates the exact set of valid int values for an
# enum-like property (e.g. GEAR_SELECTION's supported gears, or
# HVAC_TEMPERATURE_DISPLAY_UNITS' [49, 48]) - used to build a value
# dropdown instead of a free-text field where possible.
_CONFIG_ARRAY_RE = re.compile(r"configArray\s*:\s*\[([^\]]*)\]")

# Generic fallback min/max (used when the typed f/i/i64 fields below aren't
# present, e.g. other dump formats): first "min"/"max" field seen.
_MIN_RE = re.compile(r"[Mm]in(?:imum)?(?:Value)?\s*[:=]\s*([^\n,;]+)")
_MAX_RE = re.compile(r"[Mm]ax(?:imum)?(?:Value)?\s*[:=]\s*([^\n,;]+)")

# Real CarPropertyConfig dumps (verified against a live AAOS emulator) print
# the valid range three times per area line, once per storage type, e.g.:
#   f min:0.000000, f max:0.000000, i min:0, i max:3, i64 min:0, i64 max:0
# Pick the pair matching the property's actual value type so an INT32
# property doesn't display its (usually meaningless 0/0) float range.
_F_MINMAX_RE = re.compile(r"\bf min\s*[:=]?\s*(-?[\d.]+).*?\bf max\s*[:=]?\s*(-?[\d.]+)")
_I_MINMAX_RE = re.compile(r"\bi min\s*[:=]?\s*(-?\d+).*?\bi max\s*[:=]?\s*(-?\d+)")
_I64_MINMAX_RE = re.compile(r"\bi64 min\s*[:=]?\s*(-?\d+).*?\bi64 max\s*[:=]?\s*(-?\d+)")


def _minmax_patterns_for(value_type: str):
    vt = (value_type or "").upper()
    if "INT64" in vt:
        return (_I64_MINMAX_RE, _I_MINMAX_RE, _F_MINMAX_RE)
    if "INT" in vt:
        return (_I_MINMAX_RE, _F_MINMAX_RE, _I64_MINMAX_RE)
    return (_F_MINMAX_RE, _I_MINMAX_RE, _I64_MINMAX_RE)


def _extract_minmax(block: str, value_type: str):
    for pattern in _minmax_patterns_for(value_type):
        match = pattern.search(block)
        if match:
            return match.group(1), match.group(2)
    min_m = _MIN_RE.search(block)
    max_m = _MAX_RE.search(block)
    return (min_m.group(1).strip() if min_m else "", max_m.group(1).strip() if max_m else "")

# Prefix -> friendly category, purely a display grouping convenience.
_CATEGORY_PREFIXES = [
    ("HVAC_", "HVAC / Climate"),
    ("DOOR_", "Doors"),
    ("WINDOW_", "Windows"),
    ("MIRROR_", "Mirrors"),
    ("SEAT_", "Seats"),
    ("TIRE_", "Tires"),
    ("WHEEL_", "Wheels"),
    ("GEAR_", "Powertrain"),
    ("ENGINE_", "Powertrain"),
    ("EV_", "Electric / Hybrid"),
    ("FUEL_", "Fuel / Energy"),
    ("IGNITION_", "Powertrain"),
    ("PARKING_", "Safety / ADAS"),
    ("ABS_", "Safety / ADAS"),
    ("TRACTION_", "Safety / ADAS"),
    ("PERF_", "Performance"),
    ("INFO_", "Vehicle Info"),
    ("ENV_", "Environment"),
    ("HEADLIGHT", "Lights"),
    ("LIGHT", "Lights"),
    ("TURN_SIGNAL", "Lights"),
    ("HAZARD", "Lights"),
    ("DISTANCE_", "Instrument Cluster"),
    ("EPOCH_", "System"),
    ("HW_", "System"),
    ("VENDOR_", "Vendor Extension"),
    ("CABIN_", "Cabin"),
    ("STEERING_", "Steering"),
    ("SEAT", "Seats"),
]


def guess_category(name: str) -> str:
    upper = (name or "").upper()
    for prefix, category in _CATEGORY_PREFIXES:
        if upper.startswith(prefix):
            return category
    return "Other"


@dataclass
class VehicleProperty:
    prop_id_hex: str
    prop_id_int: Optional[int]
    name: str
    access: str = ""
    change_mode: str = ""
    area_type: str = ""
    value_type: str = ""
    min_value: str = ""
    max_value: str = ""
    area_values: Dict[str, str] = field(default_factory=dict)
    area_names: Dict[str, str] = field(default_factory=dict)
    config_array: List[str] = field(default_factory=list)
    raw_block: str = ""

    @property
    def category(self) -> str:
        return guess_category(self.name)

    @property
    def display_name(self) -> str:
        return self.name if self.name else f"UNKNOWN_{self.prop_id_hex}"

    @property
    def area_ids(self) -> List[str]:
        return list(self.area_values.keys())

    def area_label(self, area_id: str) -> str:
        """Human-friendly area label for dropdowns, e.g. '0x1 (ROW_1_LEFT)'."""
        name = self.area_names.get(area_id)
        return f"{area_id} ({name})" if name else area_id

    @property
    def is_enum_like(self) -> bool:
        """True when configArray gives an exact, exhaustive set of valid
        int values - the case where a dropdown beats a free-text field."""
        vt = (self.value_type or "").upper()
        return bool(self.config_array) and "FLOAT" not in vt and "STRING" not in vt and "BYTES" not in vt

    def value_summary(self) -> str:
        if not self.area_values:
            return "-"
        if len(self.area_values) == 1:
            value = next(iter(self.area_values.values()))
            return value if value else "-"
        return ", ".join(f"[{area}]={val if val else '-'}" for area, val in self.area_values.items())


def _extract_name(block: str) -> str:
    match = _NAME_NEAR_KEYWORD_RE.search(block)
    if match and match.group(1).upper() not in _NON_NAME_TOKENS:
        return match.group(1).upper() if match.group(1).isupper() else match.group(1)
    for candidate in _UPPER_TOKEN_RE.findall(block[:400]):
        if candidate not in _NON_NAME_TOKENS:
            return candidate
    return ""


def _parse_block(hex_id: str, block: str) -> VehicleProperty:
    try:
        prop_id_int = int(hex_id, 16)
    except ValueError:
        prop_id_int = None

    name = _extract_name(block)

    access_m = _ACCESS_RE.search(block)
    change_m = _CHANGE_MODE_RE.search(block)
    area_type_m = _AREA_TYPE_RE.search(block)
    value_type_m = _VALUE_TYPE_RE.search(block)
    value_type = value_type_m.group(1) if value_type_m else ""
    min_value, max_value = _extract_minmax(block, value_type)

    area_values: Dict[str, str] = {}
    for area, value in _AREA_VALUE_PAIR_RE.findall(block):
        area_values[area.strip()] = value.strip()
    if not area_values:
        lone = _LONE_VALUE_RE.search(block)
        if lone:
            area_values["global"] = lone.group(1).strip()

    area_names: Dict[str, str] = {}
    for area_name, area_hex in _AREA_DECL_RE.findall(block):
        area_values.setdefault(area_hex, "")
        if area_name and area_name.upper() != "GLOBAL":
            area_names[area_hex] = area_name

    config_array: List[str] = []
    config_m = _CONFIG_ARRAY_RE.search(block)
    if config_m and config_m.group(1).strip():
        config_array = [token.strip() for token in config_m.group(1).split(",") if token.strip()]

    return VehicleProperty(
        prop_id_hex=hex_id,
        prop_id_int=prop_id_int,
        name=name,
        access=access_m.group(1) if access_m else "",
        change_mode=change_m.group(1) if change_m else "",
        area_type=area_type_m.group(1) if area_type_m else "",
        value_type=value_type,
        min_value=min_value,
        max_value=max_value,
        area_values=area_values,
        area_names=area_names,
        config_array=config_array,
        raw_block=block.strip(),
    )


# `cmd car_service get-property-value <prop> [areaId]` prints one line per
# area as `HalPropValue{... Area ID: NAME(0xN), ..., Value: <text>}`
# (verified against a live AAOS emulator). Used to backfill live values,
# since the `dumpsys car_service` config dump does not include them.
_HAL_PROP_VALUE_RE = re.compile(r"Area ID:\s*[A-Za-z0-9_]*\((0x[0-9A-Fa-f]+)\).*?Value:\s*(.+?)\}")


def parse_hal_prop_values(text: str) -> Dict[str, str]:
    """Parse `get-property-value` output into {area_id_hex: value_text}."""
    return {area.lower(): value.strip() for area, value in _HAL_PROP_VALUE_RE.findall(text)}


def pick_value_for_area(values: Dict[str, str], area_id: str) -> Optional[str]:
    """Look up `area_id` in a parsed get-property-value result, falling
    back to the only/first value present. Needed because the caller's
    area id might be a plain decimal fallback (e.g. "0") while the device
    reports it back as a hex string (e.g. "0x0") for the same area - a
    single-area response should still be usable even when the two don't
    match textually."""
    if area_id in values:
        return values[area_id]
    if values:
        return next(iter(values.values()))
    return None


def parse_dumpsys_output(text: str) -> List[VehicleProperty]:
    """Split raw `dumpsys car_service` text into per-property records."""
    anchors = list(_ANCHOR_RE.finditer(text))
    if not anchors:
        anchors = list(_FALLBACK_ANCHOR_RE.finditer(text))
    if not anchors:
        return []

    properties: List[VehicleProperty] = []
    seen_ids: Dict[str, int] = {}
    for idx, match in enumerate(anchors):
        start = match.start()
        end = anchors[idx + 1].start() if idx + 1 < len(anchors) else len(text)
        block = text[start:end]
        hex_id = match.group(1).lower()
        # De-duplicate: some dumps repeat the same property id inside a
        # single block (e.g. once in a summary line, once in detail) -
        # keep the longer/more detailed occurrence.
        if hex_id in seen_ids:
            existing = properties[seen_ids[hex_id]]
            if len(block) <= len(existing.raw_block):
                continue
            properties[seen_ids[hex_id]] = _parse_block(hex_id, block)
            continue
        seen_ids[hex_id] = len(properties)
        properties.append(_parse_block(hex_id, block))

    return properties


class CarServiceClient:
    """High level operations built on top of AdbManager + the parser above."""

    def __init__(self, adb_manager: AdbManager) -> None:
        self._adb = adb_manager

    def preview_command(
        self, serial: str, action: str, prop_id: str, area_id: str = "", value: str = "", error_code: str = "",
    ) -> str:
        """Build the exact `adb ...` command line an action would run, so
        the GUI can show it to the user for reference before/without
        actually executing it."""
        template = config.get_command_template(action)
        cmd = template.format(prop_id=prop_id, area_id=area_id, value=value, error_code=error_code).strip()
        adb_path = self._adb.adb_path or "adb"
        return f'"{adb_path}" -s {serial or "<device>"} shell {cmd}'

    def dump_raw(self, serial: str, timeout: float = 25) -> CommandResult:
        template = config.get_command_template("dump_full")
        return self._adb.shell_raw(serial, template, timeout=timeout)

    def list_properties(self, serial: str, timeout: float = 25) -> List[VehicleProperty]:
        result = self.dump_raw(serial, timeout=timeout)
        text = result.combined
        return parse_dumpsys_output(text)

    def get_property_value(self, serial: str, prop_id: str, area_id: str = "0", timeout: float = 10) -> CommandResult:
        template = config.get_command_template("get_property")
        cmd = template.format(prop_id=prop_id, area_id=area_id, value="", error_code="")
        return self._adb.shell_raw(serial, cmd, timeout=timeout)

    def fetch_current_values(self, serial: str, prop_id: str, timeout: float = 10) -> Dict[str, str]:
        """Get the live value for every area of one property (omits the
        area argument, which makes CarShellCommand list all of them)."""
        template = config.get_command_template("get_property")
        cmd = template.format(prop_id=prop_id, area_id="", value="", error_code="").strip()
        result = self._adb.shell_raw(serial, cmd, timeout=timeout)
        if not result.ok:
            return {}
        return parse_hal_prop_values(result.combined)

    def set_property_value(self, serial: str, prop_id: str, area_id: str, value: str, timeout: float = 10) -> CommandResult:
        template = config.get_command_template("set_property")
        cmd = template.format(prop_id=prop_id, area_id=area_id, value=value, error_code="")
        return self._adb.shell_raw(serial, cmd, timeout=timeout)

    def inject_event(self, serial: str, prop_id: str, area_id: str, value: str, timeout: float = 10) -> CommandResult:
        template = config.get_command_template("inject_event")
        cmd = template.format(prop_id=prop_id, area_id=area_id, value=value, error_code="")
        return self._adb.shell_raw(serial, cmd, timeout=timeout)

    def inject_error(self, serial: str, prop_id: str, area_id: str, error_code: str, timeout: float = 10) -> CommandResult:
        template = config.get_command_template("inject_error")
        cmd = template.format(prop_id=prop_id, area_id=area_id, value="", error_code=error_code)
        return self._adb.shell_raw(serial, cmd, timeout=timeout)

    def run_custom_shell(self, serial: str, raw_command: str, timeout: float = 15) -> CommandResult:
        return self._adb.shell_raw(serial, raw_command, timeout=timeout)
