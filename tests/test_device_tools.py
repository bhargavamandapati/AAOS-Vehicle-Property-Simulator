from app.device_tools import parse_meminfo_totals, parse_ps_output

# Captured verbatim from `adb shell ps -A -o PID,PPID,USER,RSS,VSZ,NAME` on
# a live AAOS emulator.
REAL_PS_WITH_O = """  PID  PPID USER            RSS     VSZ NAME
    1     0 root          13472 11536644 init
    2     0 root              0       0 [kthreadd]
  185     1 system         8048 10880564 carwatchdogd
  212     1 vehicle_network 15404 10977184 android.hardware.automotive.vehicle@V3-emulator-service
  397     1 root         184016 14269584 zygote64
"""

# Captured verbatim from plain `adb shell ps -A` (toybox default columns,
# a completely different order/set than the -o request above).
REAL_PS_DEFAULT = """USER           PID  PPID        VSZ    RSS WCHAN            ADDR S NAME
root             1     0   11536644  13472 0                   0 S init
root             2     0          0      0 0                   0 S [kthreadd]
root             3     2          0      0 0                   0 I [pool_workqueue_release]
"""

# Captured verbatim from `adb shell dumpsys meminfo <pid>` for
# system_server (a Java process) on the same emulator.
REAL_MEMINFO_SYSTEM_SERVER = """Applications Memory Usage (in Kilobytes):
Uptime: 5273724 Realtime: 5273724

** MEMINFO in pid 707 [system] **
                   Pss  Private  Private     Swap      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap    21446    21360        0        0    24820    43432    21982    17383
  Dalvik Heap    20990    20788        0        0    28708    38812    19406    19406
        TOTAL   180750    60928   100484        0   337832    82244    41388    36789

 TOTAL PSS:   180750            TOTAL RSS:   337832      TOTAL SWAP (KB):        0
"""

# Captured for a native (non-Java) process (carwatchdogd) - shorter, no
# "** MEMINFO in pid ... **" header line.
REAL_MEMINFO_NATIVE = """Applications Memory Usage (in Kilobytes):
Uptime: 5285867 Realtime: 5285867
                   Pss  Private  Private     Swap      Rss     Heap     Heap     Heap
                 Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
                ------   ------   ------   ------   ------   ------   ------   ------
  Native Heap     1760     1760        0        0     1760        0        0        0
        TOTAL     3792     2644      892        0     8580        0        0        0
"""

REAL_MEMINFO_NOT_FOUND = "No process found for: 999999\n"


def test_parse_ps_with_o_columns():
    processes = parse_ps_output(REAL_PS_WITH_O)
    assert len(processes) == 5
    init_proc = next(p for p in processes if p.pid == "1")
    assert init_proc.ppid == "0"
    assert init_proc.user == "root"
    assert init_proc.rss_kb == 13472
    assert init_proc.vsz_kb == 11536644
    assert init_proc.name == "init"


def test_parse_ps_long_process_name_kept_whole():
    processes = {p.pid: p for p in parse_ps_output(REAL_PS_WITH_O)}
    vhal = processes["212"]
    assert vhal.name == "android.hardware.automotive.vehicle@V3-emulator-service"
    assert vhal.rss_kb == 15404


def test_parse_ps_bracketed_kernel_thread_name():
    processes = {p.pid: p for p in parse_ps_output(REAL_PS_WITH_O)}
    assert processes["2"].name == "[kthreadd]"
    assert processes["2"].rss_kb == 0


def test_parse_ps_default_column_order_different_from_o_request():
    # Plain `ps -A` puts VSZ before RSS and adds WCHAN/ADDR/S columns not
    # present in the -o request above - the parser must not assume a
    # fixed position and instead read the header.
    processes = {p.pid: p for p in parse_ps_output(REAL_PS_DEFAULT)}
    init_proc = processes["1"]
    assert init_proc.vsz_kb == 11536644
    assert init_proc.rss_kb == 13472
    assert init_proc.user == "root"
    assert init_proc.name == "init"


def test_parse_ps_empty_input():
    assert parse_ps_output("") == []
    assert parse_ps_output("   \n  \n") == []


def test_parse_meminfo_totals_java_process():
    totals = parse_meminfo_totals(REAL_MEMINFO_SYSTEM_SERVER)
    assert totals is not None
    assert totals["pss_kb"] == "180750"
    assert totals["rss_kb"] == "337832"
    assert totals["private_dirty_kb"] == "60928"
    assert totals["swap_kb"] == "0"


def test_parse_meminfo_totals_native_process():
    totals = parse_meminfo_totals(REAL_MEMINFO_NATIVE)
    assert totals is not None
    assert totals["pss_kb"] == "3792"
    assert totals["rss_kb"] == "8580"


def test_parse_meminfo_totals_none_when_process_not_found():
    assert parse_meminfo_totals(REAL_MEMINFO_NOT_FOUND) is None
