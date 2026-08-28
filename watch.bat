@echo off
chcp 65001 >nul
rem 취소표 감시 시작 - 멈춰도 알아서 다시 뜹니다. 끄려면 Ctrl+C.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행해 주세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* track --forever
pause
