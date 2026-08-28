@echo off
rem paradogo - open the app window  (double-click this file)
rem Keep this file pure ASCII: cmd.exe reads .bat in the system codepage.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [STOP] Not installed yet. Run setup.bat first.
  pause
  exit /b 1
)
rem pythonw shows the app window without a black console window behind it
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m paradogo %* gui
) else (
  ".venv\Scripts\python.exe" -m paradogo %* gui
)
