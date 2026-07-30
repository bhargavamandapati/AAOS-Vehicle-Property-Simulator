"""APK install / push-workflow helpers: `pm list packages` and
`cmd overlay list` parsing. Both are standard AOSP platform commands
(not AAOS/OEM-specific like `cmd car_service`), stable across Android
versions - but parsed tolerantly anyway, same philosophy as the rest of
this app: never assume, always keep the raw text available.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Common system-partition install locations for privileged apps and RRO
# (Runtime Resource Overlay) packages. Presets only - any path is valid,
# these just save typing for the common cases.
PUSH_TARGET_PRESETS = [
    "/system/app/",
    "/system/priv-app/",
    "/system/overlay/",
    "/product/app/",
    "/product/priv-app/",
    "/product/overlay/",
    "/vendor/app/",
    "/vendor/overlay/",
    "/system_ext/app/",
    "/system_ext/priv-app/",
    "/system_ext/overlay/",
]


def suggest_target_path(preset_dir: str, local_apk_path: str) -> str:
    """Best-effort suggestion of a full target path from a preset
    directory + the local file's name - always editable afterward."""
    name = Path(local_apk_path).name if local_apk_path else "app.apk"
    if not preset_dir.endswith("/"):
        preset_dir += "/"
    return f"{preset_dir}{name}"


def parse_package_list(text: str) -> List[str]:
    """Parse `pm list packages` output ("package:com.example.app" per
    line) into a sorted list of package names."""
    packages = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line[len("package:"):].strip())
    return sorted(packages)


@dataclass
class OverlayInfo:
    package: str
    enabled: bool
    target_package: str = ""
    raw_line: str = ""


# `cmd overlay list` groups overlays under their target package, e.g.:
#   com.android.systemui
#   [x] com.example.overlay.one
#   [ ] com.example.overlay.two
# Matched tolerantly - any "[x]"/"[ ]" line is an overlay entry
# regardless of surrounding formatting; the nearest preceding
# non-bracketed line is treated as its target package.
_OVERLAY_LINE_RE = re.compile(r"^\s*\[([ xX])\]\s+(\S+)")


def parse_overlay_list(text: str) -> List[OverlayInfo]:
    overlays: List[OverlayInfo] = []
    current_target = ""
    for raw_line in text.splitlines():
        match = _OVERLAY_LINE_RE.match(raw_line)
        if match:
            enabled = match.group(1).strip().lower() == "x"
            overlays.append(
                OverlayInfo(
                    package=match.group(2).strip(),
                    enabled=enabled,
                    target_package=current_target,
                    raw_line=raw_line.strip(),
                )
            )
        else:
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("-"):
                current_target = stripped
    return overlays


def build_install_flags(replace: bool, grant_permissions: bool, allow_test: bool, allow_downgrade: bool) -> List[str]:
    flags = []
    if replace:
        flags.append("-r")
    if grant_permissions:
        flags.append("-g")
    if allow_test:
        flags.append("-t")
    if allow_downgrade:
        flags.append("-d")
    return flags
