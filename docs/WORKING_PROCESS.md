# Working Process

How the app actually talks to a device, and why it's built the way it
is. This is the "why", not just the "what" - see
[FUNCTIONALITY.md](FUNCTIONALITY.md) for a user-facing feature
walkthrough and [ARCHITECTURE.md](ARCHITECTURE.md) for the module
layout.

## The core problem: there is no frozen spec

Android Automotive OS's `adb shell cmd car_service ...` sub-command
names and `dumpsys car_service` output format are **not** guaranteed
stable across AOSP releases or OEM forks - unlike, say, a REST API with
a version header, there's no single source of truth this app can code
against with certainty. Every design decision below follows from taking
that seriously instead of assuming one exact format.

## Property discovery: parsing `dumpsys car_service`

`CarServiceClient.list_properties()` runs the configured `dump_full`
template (default: `dumpsys car_service`) and hands the raw text to
`parse_dumpsys_output()`. That function:

1. Finds "anchor" lines - ones containing both the word "property" and
   a hex id (`0x...`) - via `_ANCHOR_RE`. If none match (a build that
   phrases things differently), it falls back to `_FALLBACK_ANCHOR_RE`
   (any line starting with a hex id).
2. Splits the text into blocks between consecutive anchors - each block
   is everything printed for one property.
3. Extracts fields from each block with tolerant, generic regexes
   (`key: value` / `key=value` patterns for access, change mode, area
   type, value type, min/max, configArray, and per-area declarations
   like `areaId:ROW_1_LEFT(0x1)`) rather than assuming a fixed column
   order or exact keyword set.
4. **Always keeps the raw block text** on the resulting
   `VehicleProperty.raw_block`, regardless of how much of it got parsed
   into structured fields - visible in the All Properties details panel.
   If a field isn't recognized by the regexes, nothing is lost; it's
   just not broken out into its own column.

This was verified against a real AAOS emulator (`sdk_car_x86_64`,
Android 14) during development - the actual format looks like:

```
Property:GEAR_SELECTION(0x11400400), group:SYSTEM(0x10000000), areaType:GLOBAL(0x1000000), valueType:INT32(0x400000),
    access:READ(0x1), changeMode:ON_CHANGE(0x1), configArray:[4, 1, 2, 8, 16, 32, 64, 128, 256], minSampleRateHz:0.000000, maxSampleRateHz:0.000000
        areaId:GLOBAL(0x0), access:READ(0x1), f min:0.000000, f max:0.000000, i min:0, i max:0, i64 min:0, i64 max:0
```

Two format details this shape revealed, both handled explicitly:

- **Min/max is printed three times per area** (once per storage type: float,
  int, int64). `_extract_minmax()` picks the pair matching the
  property's actual `valueType`, so an `INT32` property doesn't display
  its (usually meaningless `0.000000`/`0.000000`) float range instead of
  its real int range.
- **Property IDs are never hard-coded anywhere in this app.** Every id
  shown or acted on comes from parsing the connected device's own dump.
  The "known properties" table used to build the Dashboard
  (`app/property_registry.py`) only stores **names** (e.g.
  `"GEAR_SELECTION"`) - matching against the live-discovered list is
  purely by name (`property_registry.match_known`). If a name isn't
  present on this device/build, its control is simply hidden. This
  means a wrong guess can never send a command to the wrong property -
  there's nothing to guess.

## Definitions vs. live values

The dump described above gives property *definitions* (id, access,
type, valid range, declared areas) - **it does not include current
values**, confirmed empirically (no `Value:` field anywhere in a real
config dump). Getting an actual value requires a separate call:

```
adb shell cmd car_service get-property-value <prop> [areaId]
```

which (verified against the same emulator) returns one line per area:

```
HalPropValue{Property ID: HVAC_TEMPERATURE_SET(0x15600503), Area ID: ROW_1_LEFT(0x1), ElapsedRealtimeNanos: ..., Status: AVAILABLE(0x0), Value: 17.0 CELSIUS}
```

`parse_hal_prop_values()` extracts `{area_id_hex: value_text}` from
that. This has two consequences in the app:

1. **"Fetch Current Values"** (All Properties) and the Processes-style
   "current value" display exist specifically to backfill what the
   definitions dump can't provide - one `get-property-value` call per
   property, which is why it's an explicit opt-in action with a
   progress bar rather than something done automatically on every
   refresh (O(property count) adb calls adds up: ~270 properties on the
   reference emulator, at roughly 1 call each).
2. **After a Set or Inject Event succeeds, the app schedules a
   follow-up Get ~400ms later** and updates the displayed value from
   its response. Without this, a successful write looks identical to a
   no-op in the UI, because nothing about the write's own (usually
   empty) response, or the static dump, reflects the new value. The
   400ms delay exists because the VHAL may apply a write asynchronously
   - an immediate read-back can race and still return the old value.

`pick_value_for_area()` (`app/car_service.py`) exists because the area
id you asked with and the area id the device echoes back aren't always
textually identical (e.g. you might ask with the fallback `"0"` while
the device reports `"0x0"` for the same area) - it falls back to "the
only value present" when there's no exact key match, rather than
silently showing nothing for a single-area property.

## Two decoration formats - and why they must not be confused

Two different pieces of UI decorate a raw value with a human-readable
label, and they use **different separators**:

- The **value** dropdown (enum-like properties): `"8 - DRIVE"` (a space,
  hyphen, space).
- The **area** dropdown (`VehicleProperty.area_label`): `"0x1
  (ROW_1_LEFT)"` (a space then a parenthesized name).

Each has its own matching "undecorate" function in
`properties_tab.py` (`_undecorate` for the `" - "` form,
`_undecorate_area` for the `" ("` form). This split exists because a
real bug shipped from conflating them: using the value's undecorate
function on an area label left the literal `"(ROW_2_LEFT)"` text -
parentheses included - in the area id sent to the device, which the
remote shell then rejected with a syntax error. `_undecorate` and
`_undecorate_area` are covered by dedicated regression tests
(`tests/test_properties_tab_helpers.py`) specifically pinning down that
they are *not* interchangeable.

## Command templates are not hardcoded

`app/config.py::DEFAULT_COMMAND_TEMPLATES` defines the four
`cmd car_service ...` strings (get/set/inject-event/inject-error) as
Python format strings with `{prop_id}`, `{area_id}`, `{value}`,
`{error_code}` placeholders. `CarServiceClient` always builds commands
by filling in the *current* template from `config`, not a constant -
so editing them in Settings takes effect immediately, no restart or
code change required. This is the app's answer to "there is no frozen
spec": rather than assume the defaults are right for every AAOS build,
make them visibly editable and put a raw ADB shell console
(Testing → Raw ADB Shell) right next to them for whatever the templates
don't cover.

## Logcat streaming pipeline

`LogcatStream` (`app/adb_manager.py`) is the one long-running process in
the app:

1. `subprocess.Popen(["adb", "-s", serial, "logcat", "-v", "threadtime",
   ...filters], stdin=DEVNULL, stdout=PIPE, stderr=STDOUT)`.
2. A daemon reader thread does `for line in process.stdout: queue.put(line)`
   - the queue is **bounded** (`maxsize=20000`); once full, `put()`
     blocks the reader thread rather than growing memory without limit,
     which safely back-pressures all the way to the OS pipe.
3. `LogcatTab` polls that queue on a Tk `after()` timer (default 120ms,
   Settings-configurable), draining everything currently queued in one
   batch, parsing each line's level/tag/message via a `threadtime`-format
   regex, appending to a bounded `deque` (default 4000 lines), and
   inserting matching lines into the Text widget - trimming the widget's
   own line count to the same bound so the visible buffer can't grow
   unboundedly either.
4. The same batch is also written to `logcat.log`
   (`app/persistent_log.py`) **once per flush tick, not once per line**
   - this is what keeps continuous disk logging cheap even under heavy
   logcat volume (thousands of individual `logging` calls with their own
   flush would be far slower than one joined write).

## Logging pipeline

Two independent logging concerns, both designed around "must never
silently lose data, must never grow disk/memory without bound":

- **`CommandLogger`** (`app/command_log.py`) - every `AdbManager._run()`
  call (which is nearly every adb invocation in the app - device scans,
  shell commands, get/set/inject, dumpsys, screenshots) records a
  `CommandLogEntry` (timestamp, command, success, duration, response -
  truncated past 4000 chars to bound memory) into a bounded in-memory
  deque (2000 entries) *and* appends a summary line to
  `adb_commands.log` via a `RotatingFileHandler` (5&nbsp;MB &times; 5
  backups). The Testing → Command Log panel subscribes to new entries
  live; anything that happened before the panel existed is still
  available via `command_logger.entries()`.
- **Logcat's continuous file logging** - see above; a second, separate
  `RotatingFileHandler` for `logcat.log`.

Both files live under the OS user config directory (see
[ARCHITECTURE.md](ARCHITECTURE.md#persistence)), not the project
directory, and both are reachable from the GUI (Settings, Logcat, and
Command Log all have an "Open Logs Folder" shortcut).

## Screenshot capture

`AdbManager.capture_screenshot()` runs
`adb -s <serial> exec-out screencap -p` with `text=False` (raw bytes,
not the `text=True` used by every other command in the class) and
`stdin=DEVNULL`. `exec-out` specifically (not
`adb shell screencap -p > file` + `pull`) because the historical
`adb shell` path on Windows would corrupt binary data via
CRLF-translation of the stream; `exec-out` avoids that translation
entirely.

"Live preview" is a `Poller` re-running that same capture at a chosen
interval - it is a fast **snapshot loop**, not true video mirroring.
Real smooth mirroring (à la `scrcpy`) needs an H.264 stream
(`screenrecord --output-format=h264 -`) decoded in real time, which
means either shelling out to an external tool like `scrcpy` or pulling
in a heavy video-decoding dependency (PyAV/OpenCV+ffmpeg) - deliberately
out of scope for this app's dependency footprint; screencap polling
covers "watch a value change on screen" well enough without it.

## Process & memory introspection

`app/device_tools.py::parse_ps_output()` parses `ps -A -o
PID,PPID,USER,RSS,VSZ,NAME` (or any other column selection/order - it
reads the header line to find each column's position rather than
assuming a fixed layout, the same defensive approach as the dumpsys
parser above; verified against both `-o`-customized and toybox's
default `ps -A` column sets on a real emulator, which differ
completely). `parse_meminfo_totals()` pulls just the `TOTAL` row's
Pss/Private-Dirty/Private-Clean/Swap/Rss numbers out of
`dumpsys meminfo <pid>` for the quick-glance summary line - the full
dump is always shown as-is underneath, so a regex miss on the summary
never hides information, only the one summary line.

## Multi-format export

`app/export_utils.py::export_rows()` is the one exporter every "Export"
button in the app calls, format chosen by the file extension: CSV,
JSON (the only format that keeps nested values like `{area: value}`
structured - everything else JSON-encodes nested data into a single
cell), XML (field names sanitized into valid element tags), HTML (a
themed table, values HTML-escaped), or Excel via `openpyxl`. One shared
implementation means every export in the app behaves identically
instead of Properties/Processes/Command-Log each having slightly
different CSV quoting or column ordering.

## Defensive design summary

Recurring principles worth naming explicitly, since they explain *why*
several things above look the way they do:

- **Never hard-code a property ID** - always discovered live, matched
  by name.
- **Parse tolerantly, keep the raw text** - a missed field is a missing
  column, never missing data.
- **Command syntax is user-editable, not baked in** - because it
  genuinely varies across builds.
- **Nothing that can fail should fail silently** - startup failures
  (e.g. Logcat Start) show a popup, not just an easy-to-miss status-bar
  line; the app learned this the hard way (see the Logcat Start
  hardening in the git history) after a report of the UI appearing
  "unresponsive" that turned out to be an uncaught exception with no
  visible feedback.
- **Bound everything that grows** - in-memory buffers (logcat, command
  log) and on-disk logs (rotating handlers) all have explicit caps, so
  "continuous" never means "unbounded".
