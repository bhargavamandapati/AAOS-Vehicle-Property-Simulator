from app.apk_tools import (
    build_install_flags,
    parse_overlay_list,
    parse_package_list,
    suggest_target_path,
)

# `pm list packages` format ("package:<name>" per line) has been stable
# since very early Android versions.
SAMPLE_PACKAGE_LIST = """package:com.android.settings
package:com.google.android.car.kitchensink
package:com.example.myapp
"""


def test_parse_package_list_strips_prefix():
    packages = parse_package_list(SAMPLE_PACKAGE_LIST)
    assert "com.android.settings" in packages
    assert "com.example.myapp" in packages


def test_parse_package_list_sorted():
    packages = parse_package_list(SAMPLE_PACKAGE_LIST)
    assert packages == sorted(packages)


def test_parse_package_list_ignores_unrelated_lines():
    text = "Some unrelated header\npackage:com.foo\nnot a package line\n"
    assert parse_package_list(text) == ["com.foo"]


def test_parse_package_list_empty():
    assert parse_package_list("") == []


# `cmd overlay list` groups overlays under a target-package header line,
# each overlay shown as "[x]" (enabled) or "[ ]" (disabled) + package.
SAMPLE_OVERLAY_LIST = """com.android.systemui
[x] com.example.overlay.one
[ ] com.example.overlay.two
--------------------------------------------------------------------------
com.android.settings
[x] com.example.settings.overlay
"""


def test_parse_overlay_list_extracts_enabled_state():
    overlays = parse_overlay_list(SAMPLE_OVERLAY_LIST)
    by_package = {o.package: o for o in overlays}
    assert by_package["com.example.overlay.one"].enabled is True
    assert by_package["com.example.overlay.two"].enabled is False


def test_parse_overlay_list_tracks_target_package():
    overlays = parse_overlay_list(SAMPLE_OVERLAY_LIST)
    by_package = {o.package: o for o in overlays}
    assert by_package["com.example.overlay.one"].target_package == "com.android.systemui"
    assert by_package["com.example.settings.overlay"].target_package == "com.android.settings"


def test_parse_overlay_list_count():
    assert len(parse_overlay_list(SAMPLE_OVERLAY_LIST)) == 3


def test_parse_overlay_list_empty():
    assert parse_overlay_list("") == []
    assert parse_overlay_list("no overlays here") == []


def test_suggest_target_path_appends_filename():
    assert suggest_target_path("/product/overlay/", "/home/dev/MyOverlay.apk") == "/product/overlay/MyOverlay.apk"


def test_suggest_target_path_adds_missing_trailing_slash():
    assert suggest_target_path("/product/overlay", "/home/dev/MyOverlay.apk") == "/product/overlay/MyOverlay.apk"


def test_build_install_flags_all_off():
    assert build_install_flags(False, False, False, False) == []


def test_build_install_flags_replace_only():
    assert build_install_flags(True, False, False, False) == ["-r"]


def test_build_install_flags_all_on():
    assert build_install_flags(True, True, True, True) == ["-r", "-g", "-t", "-d"]
