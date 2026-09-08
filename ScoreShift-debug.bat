@echo off
rem Same as ScoreShift.bat, but keeps a console window open so you can see errors.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ScoreShift has not been set up yet. Run setup.ps1 first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m scoreshift
echo.
echo ScoreShift closed.
pause
