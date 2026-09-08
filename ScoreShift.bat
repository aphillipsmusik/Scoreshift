@echo off
rem Start ScoreShift.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo ScoreShift has not been set up yet.
    echo Right-click setup.ps1 and choose "Run with PowerShell", then try again.
    echo.
    pause
    exit /b 1
)

start "ScoreShift" ".venv\Scripts\pythonw.exe" -m scoreshift
