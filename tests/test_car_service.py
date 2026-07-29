from app.car_service import guess_category, parse_dumpsys_output, parse_hal_prop_values, pick_value_for_area

# Captured verbatim (property id / structure) from a live AAOS emulator's
# `dumpsys car_service` output - see car_service.py module docstring for
# why the parser is written to be tolerant rather than assume one exact
# format, but this fixture pins down the one format we know is real.
REAL_FORMAT_DUMP = """
  Property:GEAR_SELECTION(0x11400400), group:SYSTEM(0x10000000), areaType:GLOBAL(0x1000000), valueType:INT32(0x400000),
      access:READ(0x1), changeMode:ON_CHANGE(0x1), configArray:[4, 1, 2, 8, 16, 32, 64, 128, 256], minSampleRateHz:0.000000, maxSampleRateHz:0.000000
          areaId:GLOBAL(0x0), access:READ(0x1), f min:0.000000, f max:0.000000, i min:0, i max:0, i64 min:0, i64 max:0
  Property:EV_BRAKE_REGENERATION_LEVEL(0x1140040c), group:SYSTEM(0x10000000), areaType:GLOBAL(0x1000000), valueType:INT32(0x400000),
      access:READ_WRITE(0x3), changeMode:ON_CHANGE(0x1), configArray:[], minSampleRateHz:0.000000, maxSampleRateHz:0.000000
          areaId:GLOBAL(0x0), access:READ_WRITE(0x3), f min:0.000000, f max:0.000000, i min:0, i max:3, i64 min:0, i64 max:0
  Property:HVAC_TEMPERATURE_SET(0x15600503), group:HVAC(0x10000000), areaType:SEAT(0x50000000), valueType:FLOAT(0x600000),
      access:READ_WRITE(0x3), changeMode:ON_CHANGE(0x1), configArray:[], minSampleRateHz:0.000000, maxSampleRateHz:0.000000
          areaId:ROW_1_LEFT(0x1), access:READ_WRITE(0x3), f min:16.000000, f max:32.000000, i min:0, i max:0, i64 min:0, i64 max:0
          areaId:ROW_1_RIGHT(0x4), access:READ_WRITE(0x3), f min:16.000000, f max:32.000000, i min:0, i max:0, i64 min:0, i64 max:0
"""

SAMPLE_DUMP = """
Some unrelated dumpsys section
------------------------------
Property:0x11600207, Property Name: PERF_VEHICLE_SPEED, Access: READ, ChangeMode: CONTINUOUS, AreaType: GLOBAL, ValueType: FLOAT
  AreaId: 0, Value: 12.5
Property:0x11400400, Name: GEAR_SELECTION, access=READ_WRITE, changeMode=ON_CHANGE, areaType=GLOBAL, valueType=INT32
  areaId=0, value=8
Property:0x15600503, Name: HVAC_TEMPERATURE_SET, Access: READ_WRITE, ChangeMode: ON_CHANGE, AreaType: SEAT
  AreaId: 1, Value: 22.0
  AreaId: 4, Value: 21.5
"""


def test_parses_multiple_properties():
    props = parse_dumpsys_output(SAMPLE_DUMP)
    names = {p.name for p in props}
    assert {"PERF_VEHICLE_SPEED", "GEAR_SELECTION", "HVAC_TEMPERATURE_SET"} <= names


def test_extracts_access_and_change_mode_and_value():
    props = {p.name: p for p in parse_dumpsys_output(SAMPLE_DUMP)}
    speed = props["PERF_VEHICLE_SPEED"]
    assert speed.access == "READ"
    assert speed.change_mode == "CONTINUOUS"
    assert speed.area_type == "GLOBAL"
    assert speed.value_type == "FLOAT"
    assert speed.area_values.get("0") == "12.5"
    assert speed.prop_id_hex == "0x11600207"
    assert speed.prop_id_int == 0x11600207


def test_extracts_key_equals_value_style():
    props = {p.name: p for p in parse_dumpsys_output(SAMPLE_DUMP)}
    gear = props["GEAR_SELECTION"]
    assert gear.access == "READ_WRITE"
    assert gear.change_mode == "ON_CHANGE"
    assert gear.area_values.get("0") == "8"


def test_multi_area_property_keeps_each_area_value():
    props = {p.name: p for p in parse_dumpsys_output(SAMPLE_DUMP)}
    hvac = props["HVAC_TEMPERATURE_SET"]
    assert hvac.area_values.get("1") == "22.0"
    assert hvac.area_values.get("4") == "21.5"
    assert len(hvac.area_ids) == 2


def test_raw_block_is_preserved_for_every_property():
    props = parse_dumpsys_output(SAMPLE_DUMP)
    assert all(p.raw_block.strip() for p in props)
    assert all(p.prop_id_hex in p.raw_block.lower() or p.prop_id_hex in p.raw_block for p in props)


def test_guess_category():
    assert guess_category("HVAC_FAN_SPEED") == "HVAC / Climate"
    assert guess_category("GEAR_SELECTION") == "Powertrain"
    assert guess_category("DOOR_LOCK") == "Doors"
    assert guess_category("SOMETHING_UNKNOWN") == "Other"


def test_empty_input_returns_empty_list():
    assert parse_dumpsys_output("") == []
    assert parse_dumpsys_output("no properties mentioned here at all") == []


def test_fallback_anchor_used_when_no_property_keyword_present():
    text = "0x11600207 name=PERF_VEHICLE_SPEED access=READ value=1\n0x11400400 name=GEAR_SELECTION value=8\n"
    props = parse_dumpsys_output(text)
    names = {p.name for p in props}
    assert "PERF_VEHICLE_SPEED" in names
    assert "GEAR_SELECTION" in names


# -- real-device format (see REAL_FORMAT_DUMP) --------------------------

def test_real_format_config_array_parsed():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    assert props["GEAR_SELECTION"].config_array == ["4", "1", "2", "8", "16", "32", "64", "128", "256"]


def test_real_format_empty_config_array():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    assert props["EV_BRAKE_REGENERATION_LEVEL"].config_array == []


def test_real_format_typed_minmax_picks_int_not_float():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    prop = props["EV_BRAKE_REGENERATION_LEVEL"]
    assert prop.value_type == "INT32"
    # The float range here is a meaningless 0/0 - the int range (0..3) is
    # the one that actually matters for an INT32 property.
    assert prop.min_value == "0"
    assert prop.max_value == "3"


def test_real_format_typed_minmax_picks_float_for_float_property():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    prop = props["HVAC_TEMPERATURE_SET"]
    assert prop.value_type == "FLOAT"
    assert prop.min_value == "16.000000"
    assert prop.max_value == "32.000000"


def test_real_format_area_names_captured_for_named_areas():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    hvac = props["HVAC_TEMPERATURE_SET"]
    assert hvac.area_names.get("0x1") == "ROW_1_LEFT"
    assert hvac.area_names.get("0x4") == "ROW_1_RIGHT"
    assert set(hvac.area_ids) == {"0x1", "0x4"}


def test_real_format_global_area_name_not_stored():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    gear = props["GEAR_SELECTION"]
    assert "0x0" in gear.area_ids
    assert "0x0" not in gear.area_names
    assert gear.area_label("0x0") == "0x0"


def test_area_label_decorates_named_areas():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    hvac = props["HVAC_TEMPERATURE_SET"]
    assert hvac.area_label("0x1") == "0x1 (ROW_1_LEFT)"


def test_is_enum_like_true_for_int_with_config_array():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    assert props["GEAR_SELECTION"].is_enum_like


def test_is_enum_like_false_for_empty_config_array():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    assert not props["EV_BRAKE_REGENERATION_LEVEL"].is_enum_like


def test_is_enum_like_false_for_float_even_with_config_array():
    props = {p.name: p for p in parse_dumpsys_output(REAL_FORMAT_DUMP)}
    hvac = props["HVAC_TEMPERATURE_SET"]
    hvac.config_array = ["16", "32"]  # hypothetical - FLOAT should still not be enum-like
    assert not hvac.is_enum_like


# -- parse_hal_prop_values (get-property-value output) -------------------

def test_parse_hal_prop_values_single_area():
    text = (
        "HalPropValue{Property ID: PERF_VEHICLE_SPEED(0x11600207), Area ID: GLOBAL(0x0), "
        "ElapsedRealtimeNanos: 742601692816, Status: AVAILABLE(0x0), Value: 0.0 METER_PER_SEC}"
    )
    assert parse_hal_prop_values(text) == {"0x0": "0.0 METER_PER_SEC"}


def test_parse_hal_prop_values_multiple_areas():
    text = (
        "HalPropValue{Property ID: HVAC_TEMPERATURE_SET(0x15600503), Area ID: ROW_1_LEFT(0x1), "
        "ElapsedRealtimeNanos: 6111194794, Status: AVAILABLE(0x0), Value: 17.0 CELSIUS}\n"
        "HalPropValue{Property ID: HVAC_TEMPERATURE_SET(0x15600503), Area ID: ROW_1_RIGHT(0x4), "
        "ElapsedRealtimeNanos: 6111200033, Status: AVAILABLE(0x0), Value: 17.0 CELSIUS}\n"
    )
    assert parse_hal_prop_values(text) == {"0x1": "17.0 CELSIUS", "0x4": "17.0 CELSIUS"}


def test_parse_hal_prop_values_empty_on_no_match():
    assert parse_hal_prop_values("some unrelated error text") == {}


# -- pick_value_for_area --------------------------------------------------

def test_pick_value_for_area_exact_match():
    values = {"0x1": "22.0", "0x4": "21.5"}
    assert pick_value_for_area(values, "0x1") == "22.0"


def test_pick_value_for_area_falls_back_to_only_value_on_key_mismatch():
    # A caller might ask with a plain-decimal fallback area id ("0") while
    # the device reports it back as hex ("0x0") for the same area - a
    # single-area response should still be usable.
    values = {"0x0": "13.4"}
    assert pick_value_for_area(values, "0") == "13.4"


def test_pick_value_for_area_returns_none_when_empty():
    assert pick_value_for_area({}, "0x0") is None
