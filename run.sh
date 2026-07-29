#!/usr/bin/env bash
# Creates (if needed) a local venv, installs requirements.txt, and launches
# the AAOS Vehicle Property Simulator. Works on Linux and macOS.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo "ERROR: Python 3 was not found on PATH. Install Python 3.9+ and try again." >&2
        exit 1
    fi
fi

if ! "$PYTHON_BIN" -c "import tkinter" >/dev/null 2>&1; then
    echo "ERROR: tkinter is not available for $PYTHON_BIN." >&2
    echo "  Debian/Ubuntu: sudo apt install python3-tk" >&2
    echo "  Fedora:        sudo dnf install python3-tkinter" >&2
    echo "  Arch:          sudo pacman -S tk" >&2
    echo "  macOS (brew python): brew install python-tk" >&2
    exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo "Checking adb..."
if ! command -v adb >/dev/null 2>&1; then
    echo "WARNING: 'adb' was not found on PATH. Install Android platform-tools," \
         "or set a custom path from the app's Settings tab once it starts." >&2
fi

echo "Starting AAOS Vehicle Property Simulator..."
python "$SCRIPT_DIR/main.py" "$@"
