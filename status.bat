@echo off
rem paradogo - is it running right now?
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [STOP] Not installed yet. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* status
pause
