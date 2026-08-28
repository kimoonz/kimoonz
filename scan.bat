@echo off
chcp 65001 >nul
rem 지금 예약 가능한 날짜 보기 (읽기 전용 - 아무것도 예약하지 않습니다)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [막힘] 아직 설치가 안 됐습니다. setup.bat 을 먼저 실행해 주세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* scan
pause
