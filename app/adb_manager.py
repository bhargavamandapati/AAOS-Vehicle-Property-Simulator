"""Thin, defensive wrapper around the `adb` command line tool.

Everything that shells out to a process lives in this module so the rest
of the app never touches subprocess directly. All blocking calls are
meant to be invoked from worker threads (see app.utils.workers) - none of
this module touches Tk, so it is safe to unit test headlessly.
"""
from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from app.command_log import command_logger
from app.config import config

# Prevents a console window from flashing on top of the GUI for every
# subprocess call on Windows.
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class AdbError(RuntimeError):
    """Raised when adb cannot be located or a command fails to execute."""


@dataclass
class DeviceInfo:
    serial: str
    state: str  # device | offline | unauthorized | no permissions | ...
    model: str = ""
    transport_id: str = ""
    extra: str = ""

    @property
    def is_ready(self) -> bool:
        return self.state == "device"

    def display_name(self) -> str:
        label = self.model or self.serial
        if self.model and self.model != self.serial:
            return f"{self.serial}  ({label})"
        return self.serial


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined(self) -> str:
        if self.stderr and self.stdout:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr


def _candidate_adb_paths() -> List[Path]:
    candidates: List[Path] = []
    exe = "adb.exe" if os.name == "nt" else "adb"

    for env_var in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        root = os.environ.get(env_var)
        if root:
            candidates.append(Path(root) / "platform-tools" / exe)

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / exe)
    else:
        home = Path.home()
        candidates.append(home / "Android" / "Sdk" / "platform-tools" / exe)
        candidates.append(home / "Library" / "Android" / "sdk" / "platform-tools" / exe)
        candidates.append(Path("/usr/bin/adb"))
        candidates.append(Path("/usr/local/bin/adb"))
        candidates.append(Path("/opt/android-sdk/platform-tools/adb"))

    return candidates


def find_adb() -> Optional[str]:
    """Locate an adb executable, preferring a user-configured override."""
    override = config.get("adb_path", "")
    if override and Path(override).exists():
        return override

    on_path = shutil.which("adb")
    if on_path:
        return on_path

    for candidate in _candidate_adb_paths():
        if candidate.exists():
            return str(candidate)

    return None


def parse_devices_output(text: str) -> List[DeviceInfo]:
    """Parse `adb devices -l` output. Pure function so it's unit-testable
    without a real adb binary or subprocess."""
    devices: List[DeviceInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        transport_id = ""
        extras = []
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
            elif token.startswith("transport_id:"):
                transport_id = token.split(":", 1)[1]
            else:
                extras.append(token)
        devices.append(
            DeviceInfo(
                serial=serial,
                state=state,
                model=model,
                transport_id=transport_id,
                extra=" ".join(extras),
            )
        )
    return devices


class AdbManager:
    """Owns the resolved adb path and every synchronous/async call to it."""

    def __init__(self) -> None:
        self._adb_path: Optional[str] = find_adb()

    # -- discovery ---------------------------------------------------
    @property
    def adb_path(self) -> Optional[str]:
        return self._adb_path

    def is_available(self) -> bool:
        return self._adb_path is not None

    def refresh_adb_path(self) -> Optional[str]:
        self._adb_path = find_adb()
        return self._adb_path

    def version(self) -> str:
        if not self._adb_path:
            return ""
        try:
            result = self._run([self._adb_path, "version"], timeout=5)
            first_line = result.stdout.splitlines()[0] if result.stdout else ""
            return first_line.strip()
        except (AdbError, IndexError):
            return ""

    # -- low level -----------------------------------------------------
    def _run(self, args: List[str], timeout: Optional[float] = 15) -> CommandResult:
        # stdin=DEVNULL everywhere we spawn adb: without it the child
        # inherits whatever (possibly invalid, e.g. when launched via
        # pythonw with no console) stdin handle this process has, which on
        # Windows can make process creation itself fail unpredictably.
        command_str = " ".join(args)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=_CREATIONFLAGS,
                stdin=subprocess.DEVNULL,
            )
            result = CommandResult(proc.returncode, proc.stdout, proc.stderr)
            duration_ms = (time.monotonic() - start) * 1000
            command_logger.record(command_str, result.ok, duration_ms, result.combined)
            return result
        except FileNotFoundError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            command_logger.record(command_str, False, duration_ms, str(exc))
            raise AdbError(f"adb executable not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.monotonic() - start) * 1000
            command_logger.record(command_str, False, duration_ms, f"Timed out after {timeout}s")
            raise AdbError(f"Command timed out after {timeout}s: {' '.join(args)}") from exc

    def _require_adb(self) -> str:
        if not self._adb_path:
            raise AdbError(
                "adb was not found. Install Android platform-tools or set a "
                "custom path in Settings."
            )
        return self._adb_path

    def start_server(self) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "start-server"], timeout=15)

    def kill_server(self) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "kill-server"], timeout=15)

    # -- devices ---------------------------------------------------------
    def list_devices(self) -> List[DeviceInfo]:
        adb = self._require_adb()
        result = self._run([adb, "devices", "-l"], timeout=10)
        return parse_devices_output(result.stdout)

    def get_prop(self, serial: str, prop_name: str, timeout: float = 8) -> str:
        result = self.shell(serial, f"getprop {prop_name}", timeout=timeout)
        return result.stdout.strip()

    # -- shell -------------------------------------------------------------
    def shell(self, serial: str, command: str, timeout: Optional[float] = 15) -> CommandResult:
        """Run `adb -s <serial> shell <command>` and capture output.

        `command` is split with shlex so simple shell-style quoting for
        arguments (e.g. values with spaces) works as expected.
        """
        adb = self._require_adb()
        try:
            args = shlex.split(command, posix=(os.name != "nt"))
        except ValueError:
            args = command.split()
        return self._run([adb, "-s", serial, "shell", *args], timeout=timeout)

    def shell_raw(self, serial: str, command: str, timeout: Optional[float] = 15) -> CommandResult:
        """Run a shell command without local re-splitting (single argv)."""
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "shell", command], timeout=timeout)

    # -- binary capture ------------------------------------------------------
    def capture_screenshot(self, serial: str, timeout: float = 20) -> bytes:
        """Capture a PNG screenshot of the device's current display.

        Uses `adb exec-out` (not `adb shell ... > file` + `pull`) because
        exec-out streams the raw bytes back over the same connection
        without the historical CRLF-translation corruption that plain
        `adb shell screencap -p` had on Windows - this must run with
        text=False, unlike every other command in this class, or the PNG
        bytes get mangled by universal-newlines decoding.
        """
        adb = self._require_adb()
        args = [adb, "-s", serial, "exec-out", "screencap", "-p"]
        command_str = " ".join(args)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                args, capture_output=True, timeout=timeout, creationflags=_CREATIONFLAGS,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            command_logger.record(command_str, False, (time.monotonic() - start) * 1000, str(exc))
            raise AdbError(f"adb executable not found: {args[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            command_logger.record(command_str, False, (time.monotonic() - start) * 1000, f"Timed out after {timeout}s")
            raise AdbError(f"Command timed out after {timeout}s: {command_str}") from exc

        duration_ms = (time.monotonic() - start) * 1000
        ok = proc.returncode == 0 and len(proc.stdout) > 0
        summary = f"<{len(proc.stdout)} bytes PNG data>" if ok else (proc.stderr.decode(errors="replace") or "empty response")
        command_logger.record(command_str, ok, duration_ms, summary)
        if not ok:
            raise AdbError(proc.stderr.decode(errors="replace").strip() or "screencap returned no data")
        return proc.stdout

    # -- packages / system partition (install, push, root/remount/reboot) --
    def install(self, serial: str, apk_paths: List[str], flags: Optional[List[str]] = None, timeout: float = 120) -> CommandResult:
        """`adb install <flags> <apk>` for one APK, or
        `adb install-multiple <flags> <apks...>` for a split-APK bundle
        (more than one path). Timeout defaults high - installs can be
        slow on first run (dexopt) or over a slow USB connection."""
        adb = self._require_adb()
        flags = flags or []
        if len(apk_paths) > 1:
            return self._run([adb, "-s", serial, "install-multiple", *flags, *apk_paths], timeout=timeout)
        return self._run([adb, "-s", serial, "install", *flags, *apk_paths], timeout=timeout)

    def uninstall(self, serial: str, package: str, keep_data: bool = False, timeout: float = 60) -> CommandResult:
        adb = self._require_adb()
        flags = ["-k"] if keep_data else []
        return self._run([adb, "-s", serial, "uninstall", *flags, package], timeout=timeout)

    def push(self, serial: str, local_path: str, remote_path: str, timeout: float = 120) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "push", local_path, remote_path], timeout=timeout)

    def root(self, serial: str, timeout: float = 20) -> CommandResult:
        """Restarts adbd on the device with root permissions (only works
        on userdebug/eng builds - a "user" build will reject this)."""
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "root"], timeout=timeout)

    def remount(self, serial: str, timeout: float = 30) -> CommandResult:
        """Remounts /system (and /vendor, /product, ...) read-write.
        Requires root first. On a device with dm-verity enabled this
        will fail with a message telling you to `disable-verity` and
        reboot first - that response is surfaced to the caller as-is
        rather than guessed at, since whether verity is even relevant
        depends entirely on the build."""
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "remount"], timeout=timeout)

    def disable_verity(self, serial: str, timeout: float = 30) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "disable-verity"], timeout=timeout)

    def reboot(self, serial: str, timeout: float = 20) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "reboot"], timeout=timeout)

    def wait_for_device(self, serial: str, timeout: float = 120) -> CommandResult:
        """Blocks until the device is back and responsive after a
        reboot. Run this on a background thread - it can legitimately
        take a couple of minutes on real hardware."""
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "wait-for-device"], timeout=timeout)

    # -- logcat ------------------------------------------------------------
    def clear_logcat(self, serial: str) -> CommandResult:
        adb = self._require_adb()
        return self._run([adb, "-s", serial, "logcat", "-c"], timeout=10)

    def start_logcat(
        self,
        serial: str,
        extra_args: Optional[List[str]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        max_queue_size: int = 20000,
    ) -> "LogcatStream":
        adb = self._require_adb()
        return LogcatStream(adb, serial, extra_args or [], on_error=on_error, max_queue_size=max_queue_size)


class LogcatStream:
    """Runs `adb logcat` in a background thread and exposes a Queue of lines.

    Bounded memory: this class does not buffer history itself - it hands
    lines to the caller via a queue as fast as they arrive. The GUI layer
    is responsible for bounding how many lines it keeps on screen.
    """

    def __init__(
        self,
        adb_path: str,
        serial: str,
        extra_args: List[str],
        on_error: Optional[Callable[[str], None]] = None,
        max_queue_size: int = 20000,
    ) -> None:
        self._adb_path = adb_path
        self._serial = serial
        self._extra_args = extra_args
        self._on_error = on_error
        # Bounded so a paused/slow GUI cannot grow memory without limit -
        # the reader thread simply blocks on put() (self-throttling)
        # once the cap is hit, which is safe because the OS pipe/adb
        # process backs up harmlessly rather than losing data or eating
        # unbounded RAM.
        self.line_queue: "queue.Queue[str]" = queue.Queue(maxsize=max_queue_size)
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        args = [self._adb_path, "-s", self._serial, "logcat", *self._extra_args]
        try:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                errors="replace",
                creationflags=_CREATIONFLAGS,
            )
        except OSError as exc:
            # Broader than just FileNotFoundError: process creation itself
            # can fail for other OS-level reasons (e.g. a bad inherited
            # handle) - any of these must reach the caller's error
            # handling, not vanish silently.
            if self._on_error:
                self._on_error(str(exc))
            else:
                raise
            return
        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self._process is not None
        stdout = self._process.stdout
        try:
            if stdout is not None:
                for line in stdout:
                    if self._stop_event.is_set():
                        break
                    self.line_queue.put(line.rstrip("\n"))
        except (ValueError, OSError):
            pass
        finally:
            self.line_queue.put("__LOGCAT_STREAM_ENDED__")

    def stop(self) -> None:
        self._stop_event.set()
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            except OSError:
                pass
        self._process = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


adb_manager = AdbManager()
