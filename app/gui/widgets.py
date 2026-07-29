"""Small reusable widgets shared across tabs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

import ttkbootstrap as ttkb


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container.

    Mouse-wheel scrolling is bound only while the pointer is over this
    widget (bind on <Enter>/<Leave>) so it never steals scroll events
    from a different tab or a nested scrollable widget - this is what
    keeps multiple scrollable areas from fighting each other / overlapping
    behaviorally when several tabs are built from this same class.
    """

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_inner_configure(self, _event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_wheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class StatusPill(ttkb.Label):
    """A small colored status label, e.g. connection state or pass/fail."""

    def __init__(self, parent: tk.Widget, text: str = "", bootstyle: str = "secondary", **kwargs) -> None:
        super().__init__(parent, text=text, bootstyle=f"inverse-{bootstyle}", padding=(8, 2), **kwargs)

    def set_status(self, text: str, bootstyle: str = "secondary") -> None:
        self.configure(text=text, bootstyle=f"inverse-{bootstyle}")


class Tooltip:
    """Minimal hover tooltip for widgets that need a short explanation."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None) -> None:
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tip, text=self.text, justify="left", background="#222222",
            foreground="#ffffff", relief="solid", borderwidth=1,
            padx=6, pady=3, font=("Segoe UI", 9),
        )
        label.pack()

    def _hide(self, _event=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def debounce(widget: tk.Widget, delay_ms: int, func: Callable[[], None]) -> Callable[[], None]:
    """Return a wrapper that (re)schedules `func` on `widget` after `delay_ms`.

    Used for search boxes / filters so every keystroke doesn't trigger a
    full re-filter of a large property table.
    """
    state = {"after_id": None}

    def wrapped(*_args) -> None:
        if state["after_id"] is not None:
            try:
                widget.after_cancel(state["after_id"])
            except Exception:
                pass
        state["after_id"] = widget.after(delay_ms, func)

    return wrapped
