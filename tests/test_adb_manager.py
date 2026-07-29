from app.adb_manager import parse_devices_output

SAMPLE = (
    "List of devices attached\n"
    "emulator-5554          device product:aosp_car_x86_64 model:Automotive_OS transport_id:1\n"
    "0123456789ABCDEF       unauthorized transport_id:2\n"
    "AABBCCDD               offline\n"
    "\n"
)


def test_parses_ready_device_with_metadata():
    devices = parse_devices_output(SAMPLE)
    ready = next(d for d in devices if d.serial == "emulator-5554")
    assert ready.state == "device"
    assert ready.model == "Automotive_OS"
    assert ready.transport_id == "1"
    assert ready.is_ready


def test_unauthorized_and_offline_are_not_ready():
    devices = parse_devices_output(SAMPLE)
    by_serial = {d.serial: d for d in devices}
    assert not by_serial["0123456789ABCDEF"].is_ready
    assert by_serial["0123456789ABCDEF"].state == "unauthorized"
    assert not by_serial["AABBCCDD"].is_ready


def test_parses_exactly_three_devices():
    assert len(parse_devices_output(SAMPLE)) == 3


def test_empty_and_header_only_output():
    assert parse_devices_output("") == []
    assert parse_devices_output("List of devices attached\n") == []


def test_display_name_prefers_model():
    devices = parse_devices_output(SAMPLE)
    ready = next(d for d in devices if d.serial == "emulator-5554")
    assert "Automotive_OS" in ready.display_name()
