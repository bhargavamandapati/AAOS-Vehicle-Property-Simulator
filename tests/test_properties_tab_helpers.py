from app.gui.tabs.properties_tab import _decorate, _undecorate, _undecorate_area


def test_decorate_with_label():
    assert _decorate("8", "DRIVE") == "8 - DRIVE"


def test_decorate_without_label():
    assert _decorate("8", None) == "8"


def test_undecorate_strips_value_label():
    assert _undecorate("8 - DRIVE") == "8"


def test_undecorate_passthrough_when_no_label():
    assert _undecorate("8") == "8"


def test_undecorate_area_strips_area_name():
    assert _undecorate_area("0x10 (ROW_2_LEFT)") == "0x10"


def test_undecorate_area_passthrough_when_no_name():
    assert _undecorate_area("0x0") == "0x0"


def test_value_and_area_decorations_use_different_separators():
    # Regression test: the value dropdown decorates as "8 - DRIVE" but the
    # area dropdown decorates as "0x10 (ROW_2_LEFT)" (see
    # VehicleProperty.area_label). Using _undecorate() - which only
    # strips " - " - on an area label used to leave the literal "(NAME)"
    # text in the area id sent to the device, breaking the remote shell
    # command with "syntax error: unexpected '('".
    area_label = "0x10 (ROW_2_LEFT)"
    assert _undecorate(area_label) == area_label  # wrong helper: no-op (the bug)
    assert _undecorate_area(area_label) == "0x10"  # correct helper (the fix)
