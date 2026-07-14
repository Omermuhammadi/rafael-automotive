@echo off
REM One-click launcher for the Multi-Vehicle Diagnostic & Calibration Tool (Windows).
REM Double-click this file, or run it from a terminal.
setlocal
cd /d "%~dp0"

python -m src.main
if errorlevel 1 (
    echo.
    echo The app exited with an error. If dependencies are missing, run:
    echo     pip install -r requirements.txt
    echo.
    pause
)
endlocal
