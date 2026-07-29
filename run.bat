@echo off
REM Creates (if needed) a local venv, installs requirements.txt, and launches
REM the AAOS Vehicle Property Simulator on Windows.
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PY_LAUNCHER="
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY_LAUNCHER=py -3"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY_LAUNCHER=python"
    )
)

if "%PY_LAUNCHER%"=="" (
    echo ERROR: Python was not found on PATH. Install Python 3.9+ from python.org and try again.
    exit /b 1
)

%PY_LAUNCHER% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo ERROR: tkinter is not available in this Python install.
    echo Reinstall Python from python.org and make sure "tcl/tk and IDLE" is checked.
    exit /b 1
)

set "VENV_DIR=%SCRIPT_DIR%.venv"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment at %VENV_DIR% ...
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo ERROR: Failed to upgrade pip.
    exit /b 1
)

pip install -r "%SCRIPT_DIR%requirements.txt" --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies from requirements.txt.
    exit /b 1
)

where adb >nul 2>nul
if errorlevel 1 (
    echo WARNING: 'adb' was not found on PATH. Install Android platform-tools,
    echo or set a custom path from the app's Settings tab once it starts.
)

echo Starting AAOS Vehicle Property Simulator...
python "%SCRIPT_DIR%main.py" %*

endlocal
