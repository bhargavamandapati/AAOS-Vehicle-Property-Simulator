"""Metadata for well-known vehicle properties, used to build the Dashboard.

Design note: entries here are matched against live, device-reported
properties **by name only** (see `match_known`). Numeric property IDs are
never guessed or hard-coded - they always come from the connected
device's own `dumpsys car_service` output (see app/car_service.py). If a
name below doesn't exist on a given device/build, its control is simply
hidden ("not supported"); it can never cause a command to target the
wrong property.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.car_service import VehicleProperty

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENUM_TABLE_PATH = DATA_DIR / "vehicle_property_enums.json"

WIDGET_TOGGLE = "toggle"
WIDGET_SLIDER = "slider"
WIDGET_DROPDOWN = "dropdown"
WIDGET_SPINNER = "spinner"
WIDGET_READONLY = "readonly"


@dataclass
class KnownPropertyMeta:
    name: str
    label: str
    section: str
    widget: str
    unit: str = ""
    min_hint: float = 0
    max_hint: float = 100
    step_hint: float = 1
    per_area: bool = False
    description: str = ""


SECTION_VEHICLE_STATE = "Vehicle State"
SECTION_HVAC = "HVAC / Climate"
SECTION_DOORS_WINDOWS = "Doors, Windows & Mirrors"
SECTION_LIGHTS = "Lights & Signals"
SECTION_ENERGY = "Energy & Fuel"

KNOWN_PROPERTIES: List[KnownPropertyMeta] = [
    # -- Vehicle state -------------------------------------------------
    KnownPropertyMeta("PERF_VEHICLE_SPEED", "Vehicle Speed", SECTION_VEHICLE_STATE, WIDGET_SLIDER,
                       unit="m/s", min_hint=0, max_hint=60, step_hint=1,
                       description="Simulated road speed. Widget writes raw m/s to the property."),
    KnownPropertyMeta("PERF_VEHICLE_SPEED_DISPLAY", "Displayed Speed", SECTION_VEHICLE_STATE, WIDGET_READONLY),
    KnownPropertyMeta("GEAR_SELECTION", "Gear Selection", SECTION_VEHICLE_STATE, WIDGET_DROPDOWN),
    KnownPropertyMeta("CURRENT_GEAR", "Current Gear", SECTION_VEHICLE_STATE, WIDGET_DROPDOWN),
    KnownPropertyMeta("PARKING_BRAKE_ON", "Parking Brake", SECTION_VEHICLE_STATE, WIDGET_TOGGLE),
    KnownPropertyMeta("PARKING_BRAKE_AUTO_APPLY", "Parking Brake Auto-Apply", SECTION_VEHICLE_STATE, WIDGET_TOGGLE),
    KnownPropertyMeta("IGNITION_STATE", "Ignition State", SECTION_VEHICLE_STATE, WIDGET_DROPDOWN),
    KnownPropertyMeta("NIGHT_MODE", "Night Mode", SECTION_VEHICLE_STATE, WIDGET_TOGGLE),
    KnownPropertyMeta("ENV_OUTSIDE_TEMPERATURE", "Outside Temperature", SECTION_VEHICLE_STATE, WIDGET_READONLY, unit="C"),
    KnownPropertyMeta("PERF_ODOMETER", "Odometer", SECTION_VEHICLE_STATE, WIDGET_READONLY, unit="km"),
    KnownPropertyMeta("ABS_ACTIVE", "ABS Active", SECTION_VEHICLE_STATE, WIDGET_TOGGLE),
    KnownPropertyMeta("TRACTION_CONTROL_ACTIVE", "Traction Control Active", SECTION_VEHICLE_STATE, WIDGET_TOGGLE),
    KnownPropertyMeta("SEAT_BELT_BUCKLED", "Seat Belt Buckled", SECTION_VEHICLE_STATE, WIDGET_TOGGLE, per_area=True),

    # -- HVAC ------------------------------------------------------------
    KnownPropertyMeta("HVAC_POWER_ON", "HVAC Power", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_AC_ON", "A/C", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_AUTO_ON", "Auto Mode", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_RECIRC_ON", "Recirculation", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_DUAL_ON", "Dual Zone", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_MAX_AC_ON", "Max A/C", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_MAX_DEFROST_ON", "Max Defrost", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_DEFROSTER", "Defroster", SECTION_HVAC, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("HVAC_FAN_SPEED", "Fan Speed", SECTION_HVAC, WIDGET_SLIDER, min_hint=0, max_hint=7, step_hint=1, per_area=True),
    KnownPropertyMeta("HVAC_FAN_DIRECTION", "Fan Direction", SECTION_HVAC, WIDGET_DROPDOWN, per_area=True),
    KnownPropertyMeta("HVAC_TEMPERATURE_SET", "Set Temperature", SECTION_HVAC, WIDGET_SPINNER,
                       unit="C", min_hint=16, max_hint=32, step_hint=0.5, per_area=True),
    KnownPropertyMeta("HVAC_TEMPERATURE_CURRENT", "Current Temperature", SECTION_HVAC, WIDGET_READONLY, unit="C", per_area=True),
    KnownPropertyMeta("HVAC_TEMPERATURE_DISPLAY_UNITS", "Temperature Units", SECTION_HVAC, WIDGET_DROPDOWN),
    KnownPropertyMeta("HVAC_SEAT_TEMPERATURE", "Seat Heat/Cool", SECTION_HVAC, WIDGET_SLIDER,
                       min_hint=-3, max_hint=3, step_hint=1, per_area=True),
    KnownPropertyMeta("HVAC_SEAT_VENTILATION", "Seat Ventilation", SECTION_HVAC, WIDGET_SLIDER,
                       min_hint=0, max_hint=3, step_hint=1, per_area=True),
    KnownPropertyMeta("HVAC_STEERING_WHEEL_HEAT", "Steering Wheel Heat", SECTION_HVAC, WIDGET_SLIDER,
                       min_hint=-2, max_hint=2, step_hint=1),
    KnownPropertyMeta("HVAC_SIDE_MIRROR_HEAT", "Side Mirror Heat", SECTION_HVAC, WIDGET_SLIDER,
                       min_hint=0, max_hint=2, step_hint=1, per_area=True),

    # -- Doors / windows / mirrors --------------------------------------
    KnownPropertyMeta("DOOR_LOCK", "Door Lock", SECTION_DOORS_WINDOWS, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("DOOR_POS", "Door Position", SECTION_DOORS_WINDOWS, WIDGET_SLIDER,
                       min_hint=0, max_hint=100, step_hint=1, per_area=True),
    KnownPropertyMeta("WINDOW_POS", "Window Position", SECTION_DOORS_WINDOWS, WIDGET_SLIDER,
                       min_hint=0, max_hint=100, step_hint=1, per_area=True),
    KnownPropertyMeta("WINDOW_LOCK", "Window Lock", SECTION_DOORS_WINDOWS, WIDGET_TOGGLE, per_area=True),
    KnownPropertyMeta("MIRROR_LOCK", "Mirror Lock", SECTION_DOORS_WINDOWS, WIDGET_TOGGLE),
    KnownPropertyMeta("MIRROR_FOLD", "Mirror Fold", SECTION_DOORS_WINDOWS, WIDGET_TOGGLE),
    KnownPropertyMeta("MIRROR_Z_POS", "Mirror Vertical Pos", SECTION_DOORS_WINDOWS, WIDGET_SLIDER,
                       min_hint=-10, max_hint=10, step_hint=1, per_area=True),
    KnownPropertyMeta("MIRROR_Y_POS", "Mirror Horizontal Pos", SECTION_DOORS_WINDOWS, WIDGET_SLIDER,
                       min_hint=-10, max_hint=10, step_hint=1, per_area=True),
    KnownPropertyMeta("TRUNK_LOCK", "Trunk Lock", SECTION_DOORS_WINDOWS, WIDGET_TOGGLE),

    # -- Lights ------------------------------------------------------------
    KnownPropertyMeta("HEADLIGHTS_STATE", "Headlights (State)", SECTION_LIGHTS, WIDGET_READONLY),
    KnownPropertyMeta("HEADLIGHTS_SWITCH", "Headlights (Switch)", SECTION_LIGHTS, WIDGET_DROPDOWN),
    KnownPropertyMeta("HIGH_BEAM_LIGHTS_STATE", "High Beams (State)", SECTION_LIGHTS, WIDGET_READONLY),
    KnownPropertyMeta("HIGH_BEAM_LIGHTS_SWITCH", "High Beams (Switch)", SECTION_LIGHTS, WIDGET_DROPDOWN),
    KnownPropertyMeta("FOG_LIGHTS_STATE", "Fog Lights (State)", SECTION_LIGHTS, WIDGET_READONLY),
    KnownPropertyMeta("FOG_LIGHTS_SWITCH", "Fog Lights (Switch)", SECTION_LIGHTS, WIDGET_DROPDOWN),
    KnownPropertyMeta("HAZARD_LIGHTS_STATE", "Hazard Lights (State)", SECTION_LIGHTS, WIDGET_READONLY),
    KnownPropertyMeta("HAZARD_LIGHTS_SWITCH", "Hazard Lights (Switch)", SECTION_LIGHTS, WIDGET_TOGGLE),
    KnownPropertyMeta("TURN_SIGNAL_STATE", "Turn Signal", SECTION_LIGHTS, WIDGET_DROPDOWN),
    KnownPropertyMeta("CABIN_LIGHTS_STATE", "Cabin Lights (State)", SECTION_LIGHTS, WIDGET_READONLY),
    KnownPropertyMeta("CABIN_LIGHTS_SWITCH", "Cabin Lights (Switch)", SECTION_LIGHTS, WIDGET_DROPDOWN),
    KnownPropertyMeta("READING_LIGHTS_STATE", "Reading Lights (State)", SECTION_LIGHTS, WIDGET_READONLY, per_area=True),
    KnownPropertyMeta("READING_LIGHTS_SWITCH", "Reading Lights (Switch)", SECTION_LIGHTS, WIDGET_DROPDOWN, per_area=True),

    # -- Energy / fuel -------------------------------------------------
    KnownPropertyMeta("FUEL_LEVEL", "Fuel Level", SECTION_ENERGY, WIDGET_SLIDER, min_hint=0, max_hint=100, step_hint=1),
    KnownPropertyMeta("FUEL_LEVEL_LOW", "Fuel Level Low", SECTION_ENERGY, WIDGET_TOGGLE),
    KnownPropertyMeta("FUEL_DOOR_OPEN", "Fuel Door Open", SECTION_ENERGY, WIDGET_TOGGLE),
    KnownPropertyMeta("EV_BATTERY_LEVEL", "EV Battery Level", SECTION_ENERGY, WIDGET_SLIDER, min_hint=0, max_hint=100, step_hint=1),
    KnownPropertyMeta("EV_CHARGE_PORT_OPEN", "Charge Port Open", SECTION_ENERGY, WIDGET_TOGGLE),
    KnownPropertyMeta("EV_CHARGE_PORT_CONNECTED", "Charge Port Connected", SECTION_ENERGY, WIDGET_TOGGLE),
    KnownPropertyMeta("EV_BATTERY_INSTANTANEOUS_CHARGE_RATE", "Charge Rate", SECTION_ENERGY, WIDGET_READONLY),
    KnownPropertyMeta("RANGE_REMAINING", "Range Remaining", SECTION_ENERGY, WIDGET_SLIDER, min_hint=0, max_hint=800, step_hint=1),
    KnownPropertyMeta("DISTANCE_DISPLAY_UNITS", "Distance Units", SECTION_ENERGY, WIDGET_DROPDOWN),
]

BY_NAME: Dict[str, KnownPropertyMeta] = {meta.name: meta for meta in KNOWN_PROPERTIES}

SECTION_ORDER = [
    SECTION_VEHICLE_STATE,
    SECTION_HVAC,
    SECTION_DOORS_WINDOWS,
    SECTION_LIGHTS,
    SECTION_ENERGY,
]


class EnumTable:
    """Lazily-loaded, user-editable best-effort enum decode table."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: Optional[Dict[str, Dict[str, str]]] = None

    def _ensure_loaded(self) -> Dict[str, Dict[str, str]]:
        if self._data is None:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, json.JSONDecodeError):
                self._data = {}
        return self._data

    def decode(self, prop_name: str, raw_value: str) -> Optional[str]:
        table = self._ensure_loaded().get(prop_name)
        if not table:
            return None
        key = raw_value.strip()
        return table.get(key)

    def options_for(self, prop_name: str) -> List[Tuple[str, str]]:
        table = self._ensure_loaded().get(prop_name, {})
        return sorted(table.items(), key=lambda kv: int(kv[0]) if kv[0].lstrip("-").isdigit() else 0)


enum_table = EnumTable(ENUM_TABLE_PATH)


@dataclass
class DashboardControl:
    meta: KnownPropertyMeta
    live: Optional[VehicleProperty]

    @property
    def supported(self) -> bool:
        return self.live is not None

    @property
    def writable(self) -> bool:
        if not self.live:
            return False
        access = (self.live.access or "").upper()
        return access in ("", "WRITE", "READ_WRITE") or "WRITE" in access


def match_known(live_properties: List[VehicleProperty]) -> List[DashboardControl]:
    """Pair each known dashboard property with its live device data (if any)."""
    by_name = {prop.name: prop for prop in live_properties if prop.name}
    controls: List[DashboardControl] = []
    for meta in KNOWN_PROPERTIES:
        controls.append(DashboardControl(meta=meta, live=by_name.get(meta.name)))
    return controls


def group_by_section(controls: List[DashboardControl]) -> Dict[str, List[DashboardControl]]:
    grouped: Dict[str, List[DashboardControl]] = {section: [] for section in SECTION_ORDER}
    for control in controls:
        grouped.setdefault(control.meta.section, []).append(control)
    return grouped
