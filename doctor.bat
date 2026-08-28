@echo off
chcp 65001 >nul
rem 설정이 제대로 됐는지 점검 - 문제가 생기면 이것부터 실행하세요
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [막힘] 아직 설치가 안 됐습니다. setup.bat 을 먼저 실행해 주세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m paradogo %* doctor
echo.
echo ==========================================================
echo  위 내용을 그대로 복사해서 붙여넣어 주시면 됩니다.
echo  (창 안에서 마우스로 드래그 - 우클릭 하면 복사됩니다)
echo ==========================================================
pause
