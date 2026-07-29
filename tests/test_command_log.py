from app.command_log import CommandLogger


def test_record_and_entries():
    logger = CommandLogger(max_entries=10)
    entry = logger.record("adb devices", True, 12.5, "List of devices attached\nemulator-5554 device")
    assert entry.success is True
    assert entry.duration_ms == 12.5
    assert "adb devices" in entry.command
    assert len(logger.entries()) == 1


def test_bounded_by_max_entries():
    logger = CommandLogger(max_entries=3)
    for i in range(5):
        logger.record(f"cmd {i}", True, 1.0, "ok")
    entries = logger.entries()
    assert len(entries) == 3
    assert entries[0].command == "cmd 2"
    assert entries[-1].command == "cmd 4"


def test_long_response_is_truncated():
    logger = CommandLogger()
    long_response = "x" * 10000
    entry = logger.record("cmd", True, 1.0, long_response)
    assert entry.truncated is True
    assert len(entry.response) < 10000


def test_short_response_is_not_truncated():
    logger = CommandLogger()
    entry = logger.record("cmd", True, 1.0, "short response")
    assert entry.truncated is False
    assert entry.response == "short response"


def test_listener_receives_new_entries():
    logger = CommandLogger()
    received = []
    logger.add_listener(received.append)
    logger.record("cmd", False, 5.0, "error")
    assert len(received) == 1
    assert received[0].success is False


def test_remove_listener_stops_notifications():
    logger = CommandLogger()
    received = []
    logger.add_listener(received.append)
    logger.remove_listener(received.append)
    logger.record("cmd", True, 1.0, "ok")
    assert received == []


def test_listener_exception_does_not_break_recording():
    logger = CommandLogger()

    def bad_listener(_entry):
        raise RuntimeError("boom")

    logger.add_listener(bad_listener)
    entry = logger.record("cmd", True, 1.0, "ok")
    assert entry is not None
    assert len(logger.entries()) == 1


def test_clear_empties_entries():
    logger = CommandLogger()
    logger.record("cmd", True, 1.0, "ok")
    logger.clear()
    assert logger.entries() == []
