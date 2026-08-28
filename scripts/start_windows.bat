@echo off
chcp 65001 >nul
REM ===================================================================
REM  해외선물 신호 감시 - 상시 실행
REM
REM  부팅 시 자동 실행하려면 scripts\작업스케줄러_등록.bat 을
REM  관리자 권한으로 한 번 실행하세요.
REM ===================================================================
cd /d "%~dp0.."

if "%TELEGRAM_BOT_TOKEN%"=="" (
    echo [경고] TELEGRAM_BOT_TOKEN 환경변수가 없습니다. 알림이 화면에만 출력됩니다.
    echo        PowerShell: [Environment]::SetEnvironmentVariable^("TELEGRAM_BOT_TOKEN", "^<토큰^>", "User"^)
    echo.
)

REM 파이썬이 죽더라도 10초 뒤 자동 재시작
:loop
echo [%date% %time%] 감시 시작
python run.py watch
if errorlevel 130 (
    echo [%date% %time%] 사용자 중단 - 종료
    goto :eof
)
echo [%date% %time%] 프로세스 종료 - 10초 후 재시작
timeout /t 10 /nobreak >nul
goto loop
