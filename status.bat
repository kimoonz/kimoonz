@echo off
chcp 65001 >nul
rem 지금 감시가 돌고 있는지 확인
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행해 주세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* status
pause
