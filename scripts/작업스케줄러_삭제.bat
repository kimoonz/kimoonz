@echo off
chcp 65001 >nul
REM 해외선물 신호 감시 - 작업 스케줄러 등록 해제
setlocal
set "TASK_NAME=해외선물_신호감시"

schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not %errorlevel%==0 (
    echo 등록된 작업이 없습니다: %TASK_NAME%
    goto :end
)

schtasks /end /tn "%TASK_NAME%" >nul 2>&1
schtasks /delete /tn "%TASK_NAME%" /f
if %errorlevel%==0 (
    echo 해제 완료: %TASK_NAME%
) else (
    echo 해제 실패. "관리자 권한으로 실행" 했는지 확인하세요.
)

:end
echo.
pause
endlocal
