@echo off
REM ===================================================================
REM  확률 기반 선물 알림 - Windows 상시 실행
REM  PC가 재시작돼도 자동으로 다시 뜨게 하려면 README의
REM  "부팅 시 자동 시작" 절을 따라 작업 스케줄러에 등록하세요.
REM ===================================================================
cd /d "%~dp0.."

REM 파이썬이 죽더라도 10초 뒤 자동 재시작
:loop
echo [%date% %time%] 감시 시작
python run.py watch
echo [%date% %time%] 프로세스 종료 - 10초 후 재시작
timeout /t 10 /nobreak >nul
goto loop
