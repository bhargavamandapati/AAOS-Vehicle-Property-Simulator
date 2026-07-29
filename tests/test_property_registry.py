from app.car_service import VehicleProperty
from app.property_registry import SECTION_ORDER, group_by_section, match_known


def make_prop(name, access="READ_WRITE", areas=None):
    return VehicleProperty(
        prop_id_hex="0x123456",
        prop_id_int=0x123456,
        name=name,
        access=access,
        area_values=areas or {"0": "1"},
    )


def test_supported_property_is_matched():
    live = [make_prop("PERF_VEHICLE_SPEED"), make_prop("GEAR_SELECTION")]
    controls = {c.meta.name: c for c in match_known(live)}
    assert controls["PERF_VEHICLE_SPEED"].supported
    assert controls["GEAR_SELECTION"].supported


def test_unsupported_property_is_not_matched():
    controls = {c.meta.name: c for c in match_known([make_prop("PERF_VEHICLE_SPEED")])}
    assert not controls["HVAC_POWER_ON"].supported
    assert controls["HVAC_POWER_ON"].live is None


def test_read_only_property_is_not_writable():
    controls = {c.meta.name: c for c in match_known([make_prop("PARKING_BRAKE_ON", access="READ")])}
    assert controls["PARKING_BRAKE_ON"].supported
    assert not controls["PARKING_BRAKE_ON"].writable


def test_read_write_property_is_writable():
    controls = {c.meta.name: c for c in match_known([make_prop("PARKING_BRAKE_ON", access="READ_WRITE")])}
    assert controls["PARKING_BRAKE_ON"].writable


def test_never_matches_by_id_only_by_name():
    # A property with no name (parser could not determine it) must never
    # be matched to a known control, even though it has a valid id -
    # matching is name-based only, by design (see property_registry.py).
    unnamed = VehicleProperty(prop_id_hex="0x11600207", prop_id_int=0x11600207, name="", area_values={"0": "10"})
    controls = {c.meta.name: c for c in match_known([unnamed])}
    assert not controls["PERF_VEHICLE_SPEED"].supported


def test_group_by_section_covers_every_declared_section():
    grouped = group_by_section(match_known([]))
    assert set(SECTION_ORDER) <= set(grouped.keys())
