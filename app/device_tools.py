"""Device process/memory introspection - `ps` and `dumpsys meminfo`
parsing, verified against a live AAOS emulator.

Tolerant of column-order variance the same way car_service.py's dumpsys
parser is: locate columns by header name rather than assuming a fixed
layout, and always keep the raw line/text for full detail so nothing is
silently lost if a device's `ps` build differs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

# `ps -A -o PID,PPID,USER,RSS,VSZ,NAME` is the field set this app asks
# for (see CommandTemplates-free constant below), but the parser doesn't
# assume that exact set/order - it reads whatever header the device
# actually printed.
PS_COMMAND = "ps -A -o PID,PPID,USER,RSS,VSZ,NAME"


@dataclass
class ProcessInfo:
    pid: str
    ppid: str = ""
    user: str = ""
    rss_kb: Optional[int] = None
    vsz_kb: Optional[int] = None
    name: str = ""
    raw_line: str = ""

    @property
    def rss_display(self) -> str:
        return f"{self.rss_kb:,} KB" if self.rss_kb is not None else "-"

    @property
    def vsz_display(self) -> str:
        return f"{self.vsz_kb:,} KB" if self.vsz_kb is not None else "-"


def _find_column(header_upper: List[str], *names: str) -> Optional[int]:
    for name in names:
        if name in header_upper:
            return header_upper.index(name)
    return None


def parse_ps_output(text: str) -> List[ProcessInfo]:
    """Parse `ps` output (with or without `-o`) into ProcessInfo rows,
    locating columns from the header line so this works whether the
    device returns PID/PPID/USER/RSS/VSZ/NAME or the toybox default
    USER/PID/PPID/VSZ/RSS/WCHAN/ADDR/S/NAME layout."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split()
    header_upper = [h.upper() for h in header]

    pid_idx = _find_column(header_upper, "PID")
    if pid_idx is None:
        return []
    ppid_idx = _find_column(header_upper, "PPID")
    user_idx = _find_column(header_upper, "USER")
    rss_idx = _find_column(header_upper, "RSS")
    vsz_idx = _find_column(header_upper, "VSZ", "VSIZE")
    name_idx = _find_column(header_upper, "NAME", "CMD", "COMMAND")
    if name_idx is None:
        name_idx = len(header_upper) - 1

    processes: List[ProcessInfo] = []
    for line in lines[1:]:
        # Cap the split at the column count so a NAME/CMD field containing
        # whitespace (rare, but e.g. "app_process ... com.foo") stays in
        # one field instead of shifting every later column.
        parts = line.split(None, len(header) - 1)

        def get(idx: Optional[int]) -> str:
            return parts[idx].strip() if idx is not None and idx < len(parts) else ""

        pid = get(pid_idx)
        if not pid.isdigit():
            continue
        rss_raw = get(rss_idx)
        vsz_raw = get(vsz_idx)
        processes.append(
            ProcessInfo(
                pid=pid,
                ppid=get(ppid_idx),
                user=get(user_idx),
                rss_kb=int(rss_raw) if rss_raw.isdigit() else None,
                vsz_kb=int(vsz_raw) if vsz_raw.isdigit() else None,
                name=get(name_idx),
                raw_line=line.strip(),
            )
        )
    return processes


_TOTAL_LINE_RE = re.compile(r"\bTOTAL\b\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")


def parse_meminfo_totals(text: str) -> Optional[Dict[str, str]]:
    """Best-effort pull of the TOTAL row (Pss/Private Dirty/Private
    Clean/Swap/Rss, in that order) from `dumpsys meminfo <pid>` for a
    quick-glance summary. The full raw text is always shown separately in
    the GUI, so a miss here just leaves the summary blank - nothing is
    lost."""
    match = _TOTAL_LINE_RE.search(text)
    if not match:
        return None
    pss, private_dirty, private_clean, swap, rss = match.groups()
    return {
        "pss_kb": pss,
        "private_dirty_kb": private_dirty,
        "private_clean_kb": private_clean,
        "swap_kb": swap,
        "rss_kb": rss,
    }
