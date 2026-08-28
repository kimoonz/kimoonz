@echo off
rem ---------------------------------------------------------------------------
rem  paradogo setup for Windows  /  double-click this file
rem
rem  IMPORTANT: this file must stay pure ASCII.
rem  cmd.exe reads .bat files using the system ANSI codepage (CP949 on Korean
rem  Windows), so UTF-8 Korean text here gets mangled and can split commands
rem  apart ("'ot' is not recognized ..."). All Korean wording lives in the
rem  Python program instead, which controls its own encoding.
rem ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

echo ==================================================
echo  paradogo - setup
echo ==================================================

rem --- 1) find Python ---------------------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo.
  echo [STOP] Python 3.10+ not found.
  echo.
  echo   1. Download from https://www.python.org/downloads/
  echo   2. On the installer screen, CHECK "Add python.exe to PATH"
  echo   3. Run this file again
  echo.
  pause
  exit /b 1
)
echo [1/4] Python      : found

rem --- 2) virtual environment (keeps your system Python untouched) ------------
if exist ".venv\Scripts\python.exe" (
  echo [2/4] Environment : already there
) else (
  echo [2/4] Environment : creating...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo [STOP] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo.
  echo [STOP] The .venv folder looks broken. Delete it and run this file again.
  pause
  exit /b 1
)

rem --- 3) packages ------------------------------------------------------------
echo [3/4] Packages    : downloading... ^(a few minutes the first time^)
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo.
  echo [STOP] Could not install the packages. Check your internet connection.
  pause
  exit /b 1
)

rem --- 4) browser -------------------------------------------------------------
"%VENV_PY%" -c "from paradogo.cli import _browser_dirs; import sys; sys.exit(0 if any(d.is_dir() for d in _browser_dirs()) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [4/4] Browser     : downloading... ^(about 150MB^)
  "%VENV_PY%" -m playwright install chromium
  if errorlevel 1 (
    echo.
    echo [STOP] Could not download the browser. Check your internet connection.
    pause
    exit /b 1
  )
) else (
  echo [4/4] Browser     : already there
)

echo.
echo Setup done. Starting the app...
echo.

rem --- 5) launch --------------------------------------------------------------
"%VENV_PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  "%VENV_PY%" -m paradogo %* start
) else (
  "%VENV_PY%" -m paradogo %* gui
)

echo.
echo Press any key to close this window.
pause >nul
