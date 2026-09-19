@echo off
setlocal enabledelayedexpansion

rem HOTFIX-2 진단 전용: UPCON 파이프라인(feeder/image2pipe/encoder)을 전혀 거치지 않고
rem  1) 영상에서 프레임 한 장을 FFmpeg 로 새로 추출(INPUT.png)
rem  2) 그 PNG 한 장을 realesrgan-ncnn-vulkan.exe 에 직접 입력
rem 만 실행해, "MP4 인코더 이전 단계"(Real-ESRGAN/Vulkan)에서 이미 프레임이 깨지는지를
rem 독립적으로 확인한다. UPCON 설치 폴더에 이미 있는 실행파일/모델만 쓰므로 추가로 받을
rem 파일이 없다.
rem
rem TOOL_BUILD 는 이 파일이 바뀔 때마다 반드시 새 날짜/버전으로 올린다 - 과거에 구버전
rem bat(이미지 가드/공식모델 비교 없음)으로 테스트한 결과를 최신 결과로 착각한 사고가
rem 있었다. diag_log.txt 맨 위에 이 값이 찍히므로, 로그만 봐도 어떤 버전으로 테스트했는지
rem 바로 확인할 수 있다.
set "TOOL_BUILD=2026-09-18-v6"

rem 사용법: 이 파일 위로 "영상 파일"을 끌어다 놓거나(드래그&드롭), 더블클릭하면 경로를
rem 입력하라고 안내한다. 명령어를 입력할 필요는 없다.
rem
rem 중요(재발 방지 1): 이미지 파일(png/jpg 등)을 넣으면 예전에는 경고만 하고 계속 진행할
rem 수 있었는데, 그 경고를 무시하고 진행해 "이미 손상된 이미지를 다시 ncnn 에 넣는" 무의미한
rem 테스트가 실제로 발생했다. 이제는 이미지 파일이면 무조건 즉시 종료한다(계속 진행 옵션 없음).
rem
rem 중요(재발 방지 2): for /f ('명령') 안에 큰따옴표로 감싼 인자가 2개 이상 들어가면
rem cmd.exe 파서가 "The filename, directory name, or volume label syntax is incorrect."
rem 오류를 내며 깨진다(실경로로 재현 확인됨). 그래서 ffprobe 등 외부 명령 결과는 전부
rem 임시 파일로 리다이렉트한 뒤 "set /p VAR=<파일" 로 읽는다(명령 치환 for /f 는 인자가
rem 하나뿐일 때만 사용).
rem
rem 중요(재발 방지 3): for /f %%X in ("파일경로") 처럼 큰따옴표로 감싼 파일 경로를
rem usebackq 없이 쓰면 "파일을 읽는" 게 아니라 그 문자열 자체를 데이터로 취급해 버린다
rem (실측: 파일 내용이 아니라 경로 문자열 그대로가 출력됨). 그래서 파일 내용을 읽을 때는
rem 전부 "set /p VAR=<파일" 또는 "more +N 파일" 조합만 쓰고, 파일 대상 for /f 는 쓰지 않는다.
rem
rem 중요(재발 방지 4): "N/A" 류 기본값 문자열에 괄호를 넣으면, 그 값이 나중에 (...) 블록
rem 안에서 echo 될 때 블록의 괄호 매칭이 깨진다(예: manifest.txt 작성 블록). 그래서 기본값
rem 문자열에는 괄호를 쓰지 않는다("N/A - 파일 없음" 처럼 하이픈만 사용).

set "BIN=%~dp0_internal\bin"
set "MODELS=%~dp0_internal\models"
set "FFMPEG=%BIN%\ffmpeg.exe"
set "FFPROBE=%BIN%\ffprobe.exe"
set "NCNN=%BIN%\realesrgan-ncnn-vulkan.exe"
set "MODEL_NAME=realesr-general-dn05-x4v3-s2"
set "OFFICIAL_MODEL_NAME=realesr-animevideov3-x2"
set "TMP1=%TEMP%\upcon_ncnn_diag_%RANDOM%.tmp"

if not exist "%FFMPEG%" (
    echo [오류] FFmpeg 를 찾을 수 없습니다: %FFMPEG%
    echo UPCON 이 정상 설치된 폴더에서 실행해 주세요.
    pause
    exit /b 1
)
if not exist "%FFPROBE%" (
    echo [오류] FFprobe 를 찾을 수 없습니다: %FFPROBE%
    pause
    exit /b 1
)
if not exist "%NCNN%" (
    echo [오류] realesrgan-ncnn-vulkan.exe 를 찾을 수 없습니다: %NCNN%
    pause
    exit /b 1
)

set "VIDEO=%~1"
if "%VIDEO%"=="" (
    echo 이 창 위로 "영상 파일"을 끌어다 놓고 다시 실행하거나,
    set /p VIDEO=영상 파일 전체 경로를 입력하세요:
)
if not exist "%VIDEO%" (
    echo [오류] 파일을 찾을 수 없습니다: %VIDEO%
    pause
    exit /b 1
)

rem ---- 입력 파일 검사: 영상 확장자만 허용(허용목록), 이미지는 무조건 즉시 종료 ----
rem UPCON 본체가 실제로 지원하는 영상 확장자 목록과 동일(upcon/core/constants.py 의
rem SUPPORTED_EXTENSIONS 와 일치시킨다).
for %%E in ("%VIDEO%") do set "VIDEO_EXT=%%~xE"
set "IS_VIDEO="
if /i "%VIDEO_EXT%"==".mp4" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".mov" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".mkv" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".webm" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".avi" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".m4v" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".mpg" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".mpeg" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".wmv" set "IS_VIDEO=1"
if /i "%VIDEO_EXT%"==".ts" set "IS_VIDEO=1"
if not defined IS_VIDEO (
    echo.
    echo [오류] 이 도구는 "영상 파일"만 입력받습니다. ^(mp4/mov/mkv/webm/avi/m4v/mpg/mpeg/wmv/ts^)
    echo 지금 넣으신 파일: %VIDEO_EXT%
    echo 이미지 파일^(png/jpg/jpeg/bmp/webp/gif 등^)이나 그 외 형식은 사용할 수 없습니다.
    echo UPCON 이 이전에 저장한 진단 PNG^(frame_first.png 등^)를 넣으면 안 됩니다.
    echo 원본 mp4/mov/mkv 등 "영상 파일"로 다시 실행해 주세요.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss" > "%TMP1%"
set /p STAMP=<"%TMP1%"
del "%TMP1%" 2>nul
if not defined STAMP set "STAMP=run"
set "OUTDIR=%USERPROFILE%\Desktop\UPCON_NCNN_DIAG\%STAMP%"
mkdir "%OUTDIR%" 2>nul

set "LOG=%OUTDIR%\diag_log.txt"
set "MANIFEST=%OUTDIR%\manifest.txt"
for %%F in ("%VIDEO%") do set "VIDEO_NAME=%%~nxF"

echo ==================================================== > "%LOG%"
echo UPCON ncnn/Vulkan 독립 진단 (HOTFIX-2)                >> "%LOG%"
echo TOOL_BUILD=%TOOL_BUILD%                               >> "%LOG%"
echo 실행 시각: %date% %time%                              >> "%LOG%"
echo 입력 영상 파일명(경로 제외): %VIDEO_NAME%              >> "%LOG%"
echo 입력 파일 확장자: %VIDEO_EXT%  (영상 확장자 허용목록 통과) >> "%LOG%"
echo ==================================================== >> "%LOG%"

echo. >> "%LOG%"
echo ---- FFmpeg/FFprobe 빌드 정보 ---- >> "%LOG%"
"%FFMPEG%" -version 2>&1 | findstr /b "ffmpeg" >> "%LOG%"
"%FFPROBE%" -version 2>&1 | findstr /b "ffprobe" >> "%LOG%"

echo. >> "%LOG%"
echo ---- realesrgan-ncnn-vulkan.exe 파일 정보(빌드 식별용) ---- >> "%LOG%"
dir "%NCNN%" | findstr /i "realesrgan" >> "%LOG%"

echo.
echo [1/4] 원본 영상 해상도 확인 중...
"%FFPROBE%" -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "%VIDEO%" > "%TMP1%"
set /p SRC_RES=<"%TMP1%"
del "%TMP1%" 2>nul
if not defined SRC_RES (
    echo [오류] 원본 영상의 해상도를 읽지 못했습니다. 손상된 파일이거나 지원하지 않는 형식입니다. >> "%LOG%"
    echo [오류] 원본 영상의 해상도를 읽지 못했습니다.
    pause
    exit /b 1
)
echo 원본 영상 해상도: %SRC_RES% >> "%LOG%"
echo   원본 해상도: %SRC_RES%

echo.
echo [2/4] 영상에서 프레임 한 장을 새로 추출 중... (INPUT.png)
set "INPUT_PNG=%OUTDIR%\INPUT.png"
"%FFMPEG%" -v error -y -ss 1 -i "%VIDEO%" -frames:v 1 "%INPUT_PNG%"
if not exist "%INPUT_PNG%" (
    rem 영상이 1초보다 짧을 수 있으므로 맨 처음 프레임으로 재시도
    "%FFMPEG%" -v error -y -i "%VIDEO%" -frames:v 1 "%INPUT_PNG%"
)
if not exist "%INPUT_PNG%" (
    echo [오류] 입력 프레임을 추출하지 못했습니다. >> "%LOG%"
    echo [오류] 입력 프레임을 추출하지 못했습니다.
    pause
    exit /b 1
)

"%FFPROBE%" -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "%INPUT_PNG%" > "%TMP1%"
set /p INPUT_RES=<"%TMP1%"
del "%TMP1%" 2>nul
echo. >> "%LOG%"
echo ---- INPUT.png ---- >> "%LOG%"
echo INPUT.png 해상도: %INPUT_RES% >> "%LOG%"
call :get_sha256 "%INPUT_PNG%"
set "INPUT_SHA256=%HASH_OUT%"
echo SHA256: %INPUT_SHA256% >> "%LOG%"
for %%S in ("%INPUT_PNG%") do echo 파일 크기: %%~zS bytes >> "%LOG%"

echo   INPUT.png 해상도: %INPUT_RES%
if not "%SRC_RES%"=="%INPUT_RES%" (
    echo. >> "%LOG%"
    echo [오류] INPUT.png 해상도^(%INPUT_RES%^)가 원본 영상 해상도^(%SRC_RES%^)와 다릅니다 - ncnn 테스트를 시작하지 않습니다. >> "%LOG%"
    echo.
    echo [오류] INPUT.png 해상도^(%INPUT_RES%^)가 원본 영상 해상도^(%SRC_RES%^)와 다릅니다.
    echo ncnn 테스트를 시작하지 않고 중단합니다. 이 폴더를 UPCON 개발자에게 전달해 주세요:
    echo   %OUTDIR%
    pause
    exit /b 1
)
echo   OK: INPUT.png 해상도가 원본과 일치합니다.

echo.
echo [3/4] Real-ESRGAN ncnn/Vulkan 직접 실행 (6가지 조건) ...
echo. >> "%LOG%"
echo ==================================================== >> "%LOG%"
echo ncnn 실행 결과 (6가지 조건 비교)                       >> "%LOG%"
echo model=%MODEL_NAME% scale=2                             >> "%LOG%"
echo ==================================================== >> "%LOG%"

call :run_variant "default (tile=auto, gpu=auto)" output_default.png %MODEL_NAME%
set "EC_DEFAULT=%LAST_RC%"
findstr /r /c:"^\[0 " "%LOG%" > "%TMP1%"
set /p GPU0_NAME=<"%TMP1%"
del "%TMP1%" 2>nul
findstr /r /c:"^\[1 " "%LOG%" > "%TMP1%"
set /p GPU1_NAME=<"%TMP1%"
del "%TMP1%" 2>nul
if not defined GPU0_NAME set "GPU0_NAME=- 감지 안 됨"
if not defined GPU1_NAME set "GPU1_NAME=- 감지 안 됨, GPU 1개만 있는 PC일 수 있음"

call :run_variant_tile "tile=32 (small)" output_tile_small.png 32 %MODEL_NAME%
set "EC_TILE_SMALL=%LAST_RC%"

call :run_variant_tile "tile=400 (large)" output_tile_large.png 400 %MODEL_NAME%
set "EC_TILE_LARGE=%LAST_RC%"

call :run_variant_gpu "gpu=0 명시" output_gpu0.png 0 %MODEL_NAME%
set "EC_GPU0=%LAST_RC%"

call :run_variant_gpu "gpu=1 명시" output_gpu1.png 1 %MODEL_NAME%
set "EC_GPU1=%LAST_RC%"

echo   공식(official) 모델 realesr-animevideov3 테스트 실행 중...
call :run_variant "official model realesr-animevideov3 (변환 안 한 원본)" output_official_model.png %OFFICIAL_MODEL_NAME%
set "EC_OFFICIAL=%LAST_RC%"

echo.
echo [4/4] manifest.txt 작성 중...
call :get_sha256 "%OUTDIR%\output_default.png"
set "SHA_DEFAULT=%HASH_OUT%"
call :get_sha256 "%OUTDIR%\output_tile_small.png"
set "SHA_TILE_SMALL=%HASH_OUT%"
call :get_sha256 "%OUTDIR%\output_tile_large.png"
set "SHA_TILE_LARGE=%HASH_OUT%"
call :get_sha256 "%OUTDIR%\output_gpu0.png"
set "SHA_GPU0=%HASH_OUT%"
call :get_sha256 "%OUTDIR%\output_gpu1.png"
set "SHA_GPU1=%HASH_OUT%"
call :get_sha256 "%OUTDIR%\output_official_model.png"
set "SHA_OFFICIAL=%HASH_OUT%"

(
echo tool_build=%TOOL_BUILD%
echo source_video=%VIDEO_NAME%
echo source_resolution=%SRC_RES%
echo input_png_resolution=%INPUT_RES%
echo input_sha256=%INPUT_SHA256%
echo gpu0=%GPU0_NAME%
echo gpu1=%GPU1_NAME%
echo model=%MODEL_NAME%
echo official_model=%OFFICIAL_MODEL_NAME%
echo exit_default=%EC_DEFAULT%
echo exit_tile_small=%EC_TILE_SMALL%
echo exit_tile_large=%EC_TILE_LARGE%
echo exit_gpu0=%EC_GPU0%
echo exit_gpu1=%EC_GPU1%
echo exit_official=%EC_OFFICIAL%
echo sha256_output_default=%SHA_DEFAULT%
echo sha256_output_tile_small=%SHA_TILE_SMALL%
echo sha256_output_tile_large=%SHA_TILE_LARGE%
echo sha256_output_gpu0=%SHA_GPU0%
echo sha256_output_gpu1=%SHA_GPU1%
echo sha256_output_official_model=%SHA_OFFICIAL%
) > "%MANIFEST%"

echo.
echo 완료. 결과 폴더:
echo   %OUTDIR%
echo.
echo 이 폴더 전체^(로그 + manifest.txt + PNG + INPUT.png^)를 그대로 보내 주시면 됩니다.
echo 잠시 후 폴더를 자동으로 엽니다.
ping -n 4 127.0.0.1 >nul
start "" explorer.exe "%OUTDIR%"
exit /b 0

:get_sha256
set "HASH_OUT=N/A - 파일 없음"
if not exist %1 goto :eof
certutil -hashfile %1 SHA256 > "%TMP1%.hash"
more +1 "%TMP1%.hash" > "%TMP1%.hash2"
set /p HASH_OUT=<"%TMP1%.hash2"
del "%TMP1%.hash" 2>nul
del "%TMP1%.hash2" 2>nul
goto :eof

:run_variant
set "LABEL=%~1"
set "OUTNAME=%~2"
set "MODEL=%~3"
echo. >> "%LOG%"
echo ---- %LABEL% -^> %OUTNAME% (model=%MODEL%) ---- >> "%LOG%"
"%NCNN%" -i "%INPUT_PNG%" -o "%OUTDIR%\%OUTNAME%" -n %MODEL% -s 2 -m "%MODELS%" -j 2:2:2 -f png -v >> "%LOG%" 2>&1
set "LAST_RC=%errorlevel%"
echo exit code: %LAST_RC% >> "%LOG%"
goto :eof

:run_variant_tile
set "LABEL=%~1"
set "OUTNAME=%~2"
set "TILE=%~3"
set "MODEL=%~4"
echo. >> "%LOG%"
echo ---- %LABEL% -^> %OUTNAME% (model=%MODEL%) ---- >> "%LOG%"
"%NCNN%" -i "%INPUT_PNG%" -o "%OUTDIR%\%OUTNAME%" -n %MODEL% -s 2 -m "%MODELS%" -t %TILE% -j 2:2:2 -f png -v >> "%LOG%" 2>&1
set "LAST_RC=%errorlevel%"
echo exit code: %LAST_RC% >> "%LOG%"
goto :eof

:run_variant_gpu
set "LABEL=%~1"
set "OUTNAME=%~2"
set "GID=%~3"
set "MODEL=%~4"
echo. >> "%LOG%"
echo ---- %LABEL% -^> %OUTNAME% (model=%MODEL%) ---- >> "%LOG%"
"%NCNN%" -i "%INPUT_PNG%" -o "%OUTDIR%\%OUTNAME%" -n %MODEL% -s 2 -m "%MODELS%" -g %GID% -j 2:2:2 -f png -v >> "%LOG%" 2>&1
set "LAST_RC=%errorlevel%"
echo exit code: %LAST_RC% >> "%LOG%"
goto :eof
