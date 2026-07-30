"""About tab: app identity, developer credit, and an auto-generated
version string (base version + git build metadata - see app/version.py)
so every checkout is distinguishable without a separate release/CI
pipeline.
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

import ttkbootstrap as ttkb

from app import __version__
from app.gui.context import AppContext
from app.gui.theme import APP_ICON_TEXT, APP_TITLE
from app.version import BuildInfo, get_build_info, get_version_string

DEVELOPER_NAME = "Bhargava Mandapati"
REPO_URL = "https://github.com/bhargavamandapati/AAOS-Vehicle-Property-Simulator"


def _build_label(build_info: BuildInfo) -> str:
    count = build_info.get("commit_count")
    short_hash = build_info.get("short_hash")
    if count is None and not short_hash:
        return "unknown (not a git checkout)"
    parts = []
    if count is not None:
        parts.append(f"{count} commit{'s' if count != 1 else ''}")
    if short_hash:
        parts.append(short_hash)
    text = ", ".join(parts)
    if build_info.get("dirty"):
        text += "  (with local changes)"
    return text


class AboutTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, ctx: AppContext) -> None:
        super().__init__(parent, padding=24)
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self)
        container.pack(anchor="n", pady=24)

        ttkb.Label(container, text=APP_ICON_TEXT, font=("Segoe UI", 48)).pack(pady=(0, 4))
        ttkb.Label(container, text=APP_TITLE, font=("Segoe UI", 20, "bold")).pack()
        ttkb.Label(
            container,
            text="A desktop GUI simulator for Android Automotive OS vehicle properties over ADB.",
            bootstyle="secondary", wraplength=520, justify="center",
        ).pack(pady=(4, 20))

        build_info = get_build_info()
        version_string = get_version_string()

        version_frame = ttkb.Labelframe(container, text="Version", padding=16, bootstyle="primary")
        version_frame.pack(fill="x", pady=(0, 16))
        version_frame.columnconfigure(1, weight=1)
        self._info_row(version_frame, 0, "Version:", __version__)
        self._info_row(version_frame, 1, "Build:", _build_label(build_info))
        self._info_row(version_frame, 2, "Full version:", version_string, emphasize=True)

        ttkb.Button(
            container, text="📋 Copy Version Info", bootstyle="info-outline",
            command=lambda: self._copy_version_info(version_string, build_info),
        ).pack(pady=(0, 20))

        dev_frame = ttkb.Labelframe(container, text="Developer", padding=16, bootstyle="primary")
        dev_frame.pack(fill="x", pady=(0, 16))
        ttkb.Label(dev_frame, text=DEVELOPER_NAME, font=("Segoe UI", 12, "bold")).pack(anchor="w")

        repo_label = ttkb.Label(container, text=f"🔗 {REPO_URL}", bootstyle="info", cursor="hand2")
        repo_label.pack(pady=(0, 4))
        repo_label.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))

        ttkb.Label(
            container,
            text="Property IDs are always read live from the connected device - nothing is "
                 "hard-coded - so behavior depends on what your build/VHAL implementation "
                 "actually exposes.",
            bootstyle="secondary", wraplength=520, justify="center",
        ).pack(pady=(16, 0))

    def _info_row(self, parent: tk.Widget, row: int, label: str, value: str, emphasize: bool = False) -> None:
        ttkb.Label(parent, text=label, width=14, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
        font = ("Consolas", 10, "bold") if emphasize else ("Segoe UI", 10)
        ttkb.Label(parent, text=value, bootstyle="warning" if emphasize else "info", font=font).grid(
            row=row, column=1, sticky="w", pady=3
        )

    def _copy_version_info(self, version_string: str, build_info: BuildInfo) -> None:
        text = (
            f"{APP_TITLE}\n"
            f"Version: {__version__}\n"
            f"Build: {_build_label(build_info)}\n"
            f"Full version: {version_string}\n"
        )
        self.clipboard_clear()
        self.clipboard_append(text)
        self.ctx.notify_status("Version info copied to clipboard", "success")
