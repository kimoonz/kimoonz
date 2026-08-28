@echo off
chcp 65001 >nul
rem 창으로 쓰기 - 이 파일을 더블클릭하세요
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [막힘] 아직 설치가 안 됐습니다. setup.bat 을 먼저 실행해 주세요.
  pause
  exit /b 1
)
rem pythonw 로 띄우면 검은 콘솔 창 없이 프로그램 창만 뜬다
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m paradogo %* gui
) else (
  ".venv\Scripts\python.exe" -m paradogo %* gui
)
