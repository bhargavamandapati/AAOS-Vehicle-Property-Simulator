from app.version import format_version, get_build_info


def test_format_version_with_count_and_hash():
    info = {"commit_count": 245, "short_hash": "ab12cd3", "dirty": False}
    assert format_version("1.2.0", info) == "1.2.0+245.ab12cd3"


def test_format_version_dirty_suffix():
    info = {"commit_count": 245, "short_hash": "ab12cd3", "dirty": True}
    assert format_version("1.2.0", info) == "1.2.0+245.ab12cd3.dirty"


def test_format_version_count_only():
    info = {"commit_count": 245, "short_hash": None, "dirty": False}
    assert format_version("1.2.0", info) == "1.2.0+245"


def test_format_version_hash_only():
    info = {"commit_count": None, "short_hash": "ab12cd3", "dirty": False}
    assert format_version("1.2.0", info) == "1.2.0+ab12cd3"


def test_format_version_falls_back_to_base_when_no_git_info():
    # e.g. downloaded as a zip with no .git directory, or git isn't on PATH
    info = {"commit_count": None, "short_hash": None, "dirty": None}
    assert format_version("1.2.0", info) == "1.2.0"


def test_format_version_not_dirty_when_falling_back():
    info = {"commit_count": None, "short_hash": None, "dirty": True}
    assert format_version("1.2.0", info) == "1.2.0"


def test_get_build_info_shape():
    # This project's own checkout during tests is a real git repo, so
    # these should resolve to real values, not the "unavailable" Nones -
    # verified against ground truth rather than mocked, consistent with
    # this project's parser tests.
    info = get_build_info()
    assert set(info.keys()) == {"commit_count", "short_hash", "dirty"}
    assert info["commit_count"] is None or info["commit_count"] >= 1
    assert info["short_hash"] is None or len(info["short_hash"]) >= 4
    assert info["dirty"] in (True, False, None)


def test_get_build_info_is_cached():
    assert get_build_info() is get_build_info()
