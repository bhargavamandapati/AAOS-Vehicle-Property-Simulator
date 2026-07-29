#!/usr/bin/env python3
"""Entry point for the AAOS Vehicle Property Simulator.

Run via run.bat (Windows) / run.sh (Linux/macOS), which create a venv,
install requirements.txt, and launch this script - or directly with
`python main.py` inside an already-prepared environment.
"""
from __future__ import annotations

import sys


def _check_tkinter() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        print(
            "ERROR: Python's tkinter module is not available.\n"
            "  - Windows: reinstall Python from python.org with the 'tcl/tk' "
            "option enabled.\n"
            "  - Debian/Ubuntu: sudo apt install python3-tk\n"
            "  - Fedora: sudo dnf install python3-tkinter\n"
            "  - macOS (Homebrew python): brew install python-tk",
            file=sys.stderr,
        )
        return False


def main() -> int:
    if not _check_tkinter():
        return 1

    from app.gui.main_window import MainWindow

    window = MainWindow()
    window.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
