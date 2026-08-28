@echo off
rem paradogo - check the setup when something is wrong
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [STOP] Not installed yet. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* doctor
echo.
echo Copy the text above and paste it back if you need help.
pause
