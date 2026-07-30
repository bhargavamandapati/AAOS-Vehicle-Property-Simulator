# Functionality

A detailed walkthrough of what each part of the app does. For setup, see
the [top-level README](../README.md); for *how* these features are
implemented, see [WORKING_PROCESS.md](WORKING_PROCESS.md).

## Top bar - device connection

Always visible, above the tabs:

- **ADB status pill** - shows whether `adb` was found (auto-detected
  from `PATH`, `ANDROID_SDK_ROOT`/`ANDROID_HOME`, or common install
  locations) and its version, or a red "Not Found" you can fix from
  Settings.
- **Device dropdown** - polled every few seconds (Settings → "Device
  list poll interval"). If exactly one ready device is present it's
  auto-selected; if multiple devices/emulators are attached, or the
  device is `unauthorized`/`offline`, you pick manually. Reconnects are
  handled transparently - the same serial reappearing as `device` after
  being `unauthorized` auto-selects it.
- **Device info label** - model and Android version/SDK, fetched via
  `getprop` once a device is selected.

## Dashboard

A curated set of the most common vehicle properties (speed, gear,
parking brake, ignition, HVAC, doors/windows/mirrors, lights, fuel/EV
battery), grouped into sections. Every control is matched **by name**
against what the connected device actually reports - a property the
device doesn't support shows a grey "Not supported" badge instead of a
non-functional control. Widget type depends on the property:

- Boolean properties → a toggle switch
- Slider-appropriate properties (speed, fan speed, seat heat, ...) → a
  slider with the current value shown alongside
- Enum-like properties → a dropdown

Clicking **Apply** sends the value and then automatically re-fetches
and displays what the device now actually reports, a few hundred
milliseconds later (see
[WORKING_PROCESS.md](WORKING_PROCESS.md#definitions-vs-live-values) for
why that follow-up fetch exists).

## All Properties

Every property the device's `dumpsys car_service` reports, with:

- **Search / Category / Access filters** above a sortable table (click
  a column header to sort).
- **A details panel** for the selected property:
  - Access, Change Mode, Area Type, Value Type, Min, Max, and a bold
    **Current Value** line that updates on every Get/Set/Fetch and
    whenever you switch the Area ID dropdown.
  - **Area ID** - a dropdown built from the areas the property actually
    declares, decorated with the symbolic name where the device
    provides one (e.g. `0x1 (ROW_1_LEFT)`).
  - **Value** - adapts to the property's declared type: radio buttons
    for `BOOLEAN`, a dropdown of the exact valid values when the device
    reports a non-empty, non-float `configArray` (e.g. gear positions -
    decoded with a human label where `data/vehicle_property_enums.json`
    has one), or a free-text field otherwise.
  - **Get Latest / Set / Inject Event / Inject Error** buttons, each
    with a busy indicator while in flight.
  - **ADB commands (for reference)** - a live-updating box showing the
    exact `adb ...` command each of those four actions would run, given
    the current Area ID / Value / Error Code fields - useful for seeing
    exactly what will be sent, or for copying into a terminal.
  - The raw `dumpsys` text block for that one property, always
    available even if a field above wasn't parsed.
- **⚡ Fetch Current Values** - `dumpsys car_service` only reports
  property *definitions*, not live values (see
  [WORKING_PROCESS.md](WORKING_PROCESS.md#definitions-vs-live-values)).
  This button backfills them with one `get-property-value` call per
  property, with a determinate progress bar and a completion popup
  (`N of M` - some properties genuinely have no value available, e.g.
  write-only or unsupported-in-this-build ones).
- **⬇ Export All Details** - every field the app knows about (including
  the raw dump and configArray), as **CSV, JSON, XML, HTML, or Excel
  (.xlsx)** - format is chosen by the extension you save as.

## Logcat Console

- **Start / Stop / Pause**, filter **presets** (Car Service, Vehicle
  HAL, Property Changes, Warnings+, Errors only, or All), a custom
  **Tags** field and **Min level** filter, and a client-side **Search**
  box that filters what's currently displayed without re-running the
  device-side filter.
- **Color-coded severity** (V/D/I/W/E/F) with a legend, matching
  Android's own logcat conventions.
- **Clear** clears both the device's logcat buffer and the local view.
  **Save** exports the current buffer to a text file.
- The in-memory buffer is bounded (Settings → "Logcat buffer (lines)",
  default 4000) - both the retained history and the on-screen Text
  widget get trimmed, so a long session doesn't grow memory without
  limit.
- Independent of all of that: **every line received is continuously
  appended to a rotating log file on disk** (`logcat.log`) the moment
  it's captured - regardless of Pause state, search filter, or whether
  you remember to click Save. See
  [WORKING_PROCESS.md](WORKING_PROCESS.md#logging-pipeline).
- If Start fails for any reason (no device, adb error, process
  spawn failure), you get an explicit popup - it never fails silently.

## Testing

Five sub-tabs, all sharing the same device connection:

- **Scenario Runner** - built-in scenarios (HVAC Smoke Test, Gear Cycle
  Test, Speed Ramp Test, Lights Check) plus a step editor to build your
  own (property, area, value, delay, optional verify-after-set), with
  Save/Load as JSON. Running a scenario steps through it sequentially,
  color-coding each row PASS/FAIL/SKIPPED (skipped = the property isn't
  supported on this device) as it goes, and can be stopped mid-run.
- **Snapshot Diff** - capture the full property state twice ("Snapshot
  A", "Snapshot B") and diff them - useful for "what changed" regression
  checks (e.g. before/after a firmware update or a code change).
- **Live Monitor** - pick specific properties to watch, poll them at a
  chosen interval, and see a running timestamped log of their values -
  useful for watching a continuous property (like speed) change over
  time without opening the full property dump repeatedly.
- **Raw ADB Shell** - a free-form `adb shell <command>` console with
  history (Up/Down arrows) and quick-command shortcuts
  (`dumpsys car_service`, `getprop ro.build.fingerprint`, `ps -A`) for
  anything the built-in tools don't cover.
- **Command Log** - a live view of **every** ADB command the app has
  run anywhere (not just from this tab) - timestamp, duration, OK/FAIL,
  full command, full response. Filterable by status and free-text
  search, exportable, with a "Open Logs Folder" shortcut to the
  continuously-written `adb_commands.log` on disk.

## Screenshot

- **Capture** grabs the device's current display via
  `adb exec-out screencap -p` and shows it scaled to fit the preview
  pane.
- **Save As…** writes the *full-resolution* PNG (not the scaled
  preview) to a file you choose.
- **Live preview** repeatedly re-captures at a chosen interval (1-30s)
  - this is a fast-refreshing snapshot loop, not true video mirroring;
  see [WORKING_PROCESS.md](WORKING_PROCESS.md#screenshot-capture) for
  why, and what it would take to get real smooth mirroring instead.

## Processes

- The device's running processes (`ps -A -o PID,PPID,USER,RSS,VSZ,NAME`,
  parsed tolerantly - see
  [WORKING_PROCESS.md](WORKING_PROCESS.md#process--memory-introspection)),
  sortable by any column (click "RSS" to find the heaviest processes),
  filterable by name/PID/user.
- Selecting a process fetches `dumpsys meminfo <pid>` and shows:
  - a quick-glance summary line (PSS / RSS / Private Dirty / Swap, in
    KB), pulled from the dump's `TOTAL` row;
  - the full raw breakdown below (Java/Native heap, code, stack,
    graphics, ...) for anyone who wants the complete picture.
- **Live updates** refreshes both the process list *and* the memory
  detail of whichever process is currently selected, at a chosen
  interval.
- **Export** - same 5 formats as All Properties.

## APK Install

Three sub-tabs, covering the range of ways an APK ends up on a test
device - from a plain user-space install to a system/product overlay
that needs root, a remounted partition, and a reboot to take effect:

- **Quick Install** - browse for one APK (or several, for a split
  install - `install-multiple` is used automatically when more than one
  file is picked), toggle the standard `pm install` flags (`-r` replace
  existing, `-g` grant all runtime permissions, `-t` allow test APKs,
  `-d` allow version downgrade), see the exact `adb install ...` command
  it will run before you run it, and get a scrollable pass/fail result
  with the device's raw output. This covers the common case: installing
  a normal app APK, no root required.
- **Push & System Workflow** - for APKs that must land in a system
  partition instead of being installed normally (RRO overlays,
  priv-app APKs, vendor APKs):
  - Add one or more entries (local file, target path - with presets for
    `/system/app/`, `/system/priv-app/`, `/system/overlay/`,
    `/product/app/`, `/product/priv-app/`, `/product/overlay/`,
    `/vendor/app/`, `/vendor/overlay/`, `/system_ext/{app,priv-app,overlay}/`,
    and an optional package name used only for the overlay-enable step).
  - Choose which workflow steps to run: **Root** (`adb root`),
    **Remount** `/system` read-write (`adb remount`), **Push** each
    entry (`adb push`), **chmod 644** the pushed files, **Reboot**
    (`adb reboot`), **Wait for device** to come back online, and
    optionally **Enable overlay** (`cmd overlay enable <package>`) once
    it's back. A "stop on first failure" toggle decides whether a failed
    step (e.g. `adb root` not supported on a `user` build) aborts the
    rest of the run or the app pushes ahead anyway.
  - **Run** executes the steps in order in the background, updating a
    live Steps table (pass/fail/running, with the device's output for
    each step) as it goes - never one opaque blocking call. **Stop**
    halts before the next step starts.
  - A separate **"⚠ Disable Verity & Reboot…"** button is deliberately
    *not* part of the automatic workflow - see
    [WORKING_PROCESS.md](WORKING_PROCESS.md#push-and-system-workflow-design)
    for why `disable-verity` is kept manual, behind its own
    confirmation dialog.
- **Packages & Overlays** - **Installed Packages**: list packages
  (`pm list packages`, scoped to All / Third-party only (`-3`) / System
  only (`-s`)) with a search filter and an **Uninstall Selected** button
  (confirmation dialog first, with an optional "keep data" checkbox).
  **RRO Overlays**: list overlays (`cmd overlay list`, parsed into
  package / enabled state / target package) and **Enable/Disable
  Selected** - the fast path for iterating on an already-pushed overlay
  without repeating the whole push workflow.

## Settings

- **ADB Connection** - detected path, an override (with a file
  browser), Apply & Re-detect, Restart ADB Server, and shortcuts to the
  config and logs folders.
- **Appearance** - theme picker (a curated set of native ttkbootstrap
  2.x themes, both dark and light).
- **Performance & Buffers** - logcat buffer size, device-list poll
  interval, and the (off-by-default) automatic property-poll interval.
- **ADB / CarService Command Templates** - the actual `cmd car_service
  ...` strings used for Get/Set/Inject Event/Inject Error, editable with
  `{prop_id}`, `{area_id}`, `{value}`, `{error_code}` placeholders. This
  exists because that sub-command syntax genuinely differs across
  AAOS releases/OEM builds - see
  [WORKING_PROCESS.md](WORKING_PROCESS.md#command-templates-are-not-hardcoded).
- **About** - a pointer to the dedicated ℹ️ About tab, below, for the
  full version string and developer info.

## About

- **Version** - the base version (manually maintained), the build
  metadata (commit count, short hash, and a "dirty" flag if there are
  uncommitted changes), and the two combined into one copyable full
  version string (e.g. `1.0.0+245.ab12cd3`) - a **Copy Version Info**
  button puts it straight on the clipboard, ready to paste into a bug
  report so there's never any ambiguity about which build was tested.
  Generated live from git on every launch - see
  [WORKING_PROCESS.md](WORKING_PROCESS.md#git-derived-versioning).
- **Developer** - credited as Bhargava Mandapati, with a clickable link
  to the project's GitHub repository.
