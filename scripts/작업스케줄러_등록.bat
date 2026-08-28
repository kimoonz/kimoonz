@echo off
chcp 65001 >nul
REM ===================================================================
REM  해외선물 신호 감시 - Windows 작업 스케줄러 등록
REM
REM  PC 부팅 시 자동으로 감시를 시작합니다. 하루에 몇 번 재시작해도
REM  발송 이력이 state\alerts.json 에 남아 같은 신호를 두 번 보내지
REM  않습니다.
REM
REM  이 파일을 "관리자 권한으로 실행" 하세요.
REM ===================================================================
setlocal
set "TASK_NAME=해외선물_신호감시"
set "PROJECT_DIR=%~dp0.."
pushd "%PROJECT_DIR%"
set "PROJECT_DIR=%CD%"
popd
set "RUNNER=%PROJECT_DIR%\scripts\start_windows.bat"

echo 프로젝트 경로: %PROJECT_DIR%
echo.

if not exist "%RUNNER%" (
    echo [오류] %RUNNER% 를 찾을 수 없습니다.
    goto :end
)

REM 토큰이 사용자 환경변수에 있는지 확인 (없으면 알림이 안 갑니다)
if "%TELEGRAM_BOT_TOKEN%"=="" (
    echo [경고] TELEGRAM_BOT_TOKEN 환경변수가 없습니다.
    echo        PowerShell 에서 한 번만 실행하세요:
    echo          [Environment]::SetEnvironmentVariable^("TELEGRAM_BOT_TOKEN", "^<토큰^>", "User"^)
    echo        설정 후 이 창을 닫고 새로 열어야 반영됩니다.
    echo.
)

schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel%==0 (
    echo 기존 작업을 지우고 다시 등록합니다.
    schtasks /delete /tn "%TASK_NAME%" /f >nul
)

REM /ru %USERNAME% : 사용자 환경변수(TELEGRAM_BOT_TOKEN)를 그대로 물려받게 한다.
REM                  SYSTEM 계정으로 돌리면 그 변수가 안 보여 발송이 조용히 실패한다.
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%RUNNER%\"" ^
    /sc onstart ^
    /ru "%USERNAME%" ^
    /rl highest ^
    /delay 0002:00 ^
    /f

if %errorlevel%==0 (
    echo.
    echo 등록 완료: %TASK_NAME%
    echo   부팅 2분 뒤 자동 시작합니다 ^(네트워크가 올라올 시간^).
    echo   지금 바로 시작: schtasks /run /tn "%TASK_NAME%"
    echo   상태 확인:      schtasks /query /tn "%TASK_NAME%" /v /fo list
    echo   해제:           scripts\작업스케줄러_삭제.bat
) else (
    echo.
    echo 등록 실패. 이 파일을 "관리자 권한으로 실행" 했는지 확인하세요.
)

:end
echo.
pause
endlocal
