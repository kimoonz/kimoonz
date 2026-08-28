@echo off
chcp 65001 >nul
rem 파라다이스 도고 캐빈 예약 도우미 — Windows 설치
rem
rem 이 파일을 더블클릭하면 됩니다.
rem 날짜를 바로 정하려면 명령 프롬프트에서:
rem   setup.bat --date 2026-09-19 --nights 1

setlocal
cd /d "%~dp0"

echo ==================================================
echo  파라다이스 도고 캐빈 예약 도우미 - 설치
echo ==================================================

rem 1) 파이썬 찾기 (py 런처를 우선 쓴다. Windows 표준 설치에 딸려 온다)
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"

if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)

if not defined PY (
  echo.
  echo [막힘] 파이썬 3.10 이상이 필요한데 찾지 못했습니다.
  echo.
  echo   1. https://www.python.org/downloads/ 에서 내려받으세요
  echo   2. 설치 화면 맨 아래 "Add python.exe to PATH" 를 반드시 체크하세요
  echo   3. 설치가 끝나면 이 파일을 다시 실행하세요
  echo.
  pause
  exit /b 1
)
echo [1/4] 파이썬     : 찾았습니다

rem 2) 가상환경 - 시스템 파이썬을 건드리지 않기 위해 이 폴더 안에만 설치한다
if exist ".venv\Scripts\python.exe" (
  echo [2/4] 가상환경   : 이미 있음
) else (
  echo [2/4] 가상환경   : 만드는 중...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo [막힘] 가상환경을 만들지 못했습니다.
    pause
    exit /b 1
  )
)

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo.
  echo [막힘] 가상환경이 망가진 것 같습니다. .venv 폴더를 지우고 다시 실행해 주세요.
  pause
  exit /b 1
)

rem 3) 필요한 것 설치
echo [3/4] 필요한 것  : 내려받는 중... ^(처음 한 번은 몇 분 걸립니다^)
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo.
  echo [막힘] 필요한 패키지를 설치하지 못했습니다. 인터넷 연결을 확인해 주세요.
  pause
  exit /b 1
)

"%VENV_PY%" -c "from paradogo.cli import _browser_dirs; import sys; sys.exit(0 if any(d.is_dir() for d in _browser_dirs()) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [4/4] 브라우저   : 내려받는 중... ^(150MB 정도^)
  "%VENV_PY%" -m playwright install chromium
  if errorlevel 1 (
    echo.
    echo [막힘] 브라우저를 내려받지 못했습니다. 인터넷 연결을 확인해 주세요.
    pause
    exit /b 1
  )
) else (
  echo [4/4] 브라우저   : 이미 있음
)

echo.
echo 설치 끝났습니다.
echo.
echo ==================================================
echo  프로그램을 띄웁니다
echo ==================================================
echo.

"%VENV_PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo 창 화면을 쓸 수 없어 터미널로 진행합니다.
  echo.
  "%VENV_PY%" -m paradogo %* start
) else (
  echo 창이 뜨면 [설정하기] 버튼부터 눌러 주세요.
  echo 다음부터는 gui.bat 을 더블클릭하시면 됩니다.
  "%VENV_PY%" -m paradogo %* gui
)

echo.
echo 창을 닫으려면 아무 키나 누르세요.
pause >nul
