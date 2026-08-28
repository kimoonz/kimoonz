@echo off
rem paradogo - start watching for cancellations (Ctrl+C to stop)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [STOP] Not installed yet. Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* track --forever
pause
