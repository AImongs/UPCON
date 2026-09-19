@echo off
setlocal

rem HOTFIX-2 진단 전용 launcher. 이 창에서 실행한 UPCON.exe 프로세스에만 적용되는
rem 환경변수이며 (set 만 사용, setx 아님), Windows 사용자 환경변수는 건드리지 않는다.
set "UPCON_FORCE_SOFTWARE_ENCODER=1"

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "DIAG_STAMP=%%T"
if not defined DIAG_STAMP set "DIAG_STAMP=run"

set "UPCON_DEBUG_SAVE_FRAMES_DIR=%USERPROFILE%\Desktop\UPCON_DIAG_FRAMES\%DIAG_STAMP%"
if not exist "%UPCON_DEBUG_SAVE_FRAMES_DIR%" mkdir "%UPCON_DEBUG_SAVE_FRAMES_DIR%"

echo ===================================================
echo  UPCON 소프트웨어 인코더(libx264) 진단 테스트
echo ===================================================
echo  이 창에서 실행한 UPCON 은 이번 실행에서만 libx264 를
echo  강제로 사용합니다 (NVENC 사용 안 함).
echo  Windows 환경변수는 영구적으로 바뀌지 않습니다 - 이 창을
echo  닫으면 설정이 사라집니다.
echo.
echo  결과 파일명 끝에 _x264diag 가 붙습니다 (일반 실행 결과와
echo  겹쳐 쓰지 않습니다).
echo.
echo  진단 프레임(PNG, 최대 3장) 저장 위치:
echo    %UPCON_DEBUG_SAVE_FRAMES_DIR%
echo ===================================================
echo.

start "" "%~dp0UPCON.exe"

echo UPCON 을 실행했습니다. 이 창은 5초 후 자동으로 닫힙니다.
timeout /t 5 >nul

endlocal
