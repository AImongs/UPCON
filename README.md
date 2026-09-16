# UPCON — AI Video Upscaler

AI 영상 2× 업스케일러 (Windows). 내 PC GPU 또는 클라우드 GPU(fal.ai, 사용자 본인 계정)로 처리한다.

## 개발 환경 실행

```powershell
# 최초 1회
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scripts\fetch_binaries.py      # bin\realesrgan-ncnn-vulkan.exe + 공식 모델 + 라이선스

# 실행
.\.venv\Scripts\python -m upcon

# UI 없이 파이프라인만 (속도/디스크 측정 출력)
.\.venv\Scripts\python -m upcon.cli 영상.mp4 [--out-dir 폴더] [--model realesr-animevideov3]
```

FFmpeg/ffprobe 는 `bin\` 폴더에 있으면 그것을, 없으면 시스템 PATH 의 것을 사용한다 (배포본에는 동봉, STEP 7).

## 테스트

```powershell
.\.venv\Scripts\python -m pip install pytest
.\.venv\Scripts\python -m pytest -q          # 단위 + 실제 GPU 업스케일/취소/디스크/배치 테스트 (약 90초)
.\.venv\Scripts\python tests\ui_smoke.py     # 실제 UI 배치 시나리오 A~N 자동 테스트 + 스크린샷 (tests\out)
.\.venv\Scripts\python -m pytest tests/integration -q --run-cloud   # 실제 fal.ai API (비용 발생, 키 없으면 skip)
```

테스트 영상은 ffmpeg 로 생성한다 (`tests\samples\`, git 제외):

```powershell
ffmpeg -y -f lavfi -i "testsrc2=size=854x480:rate=24" -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 5 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest tests\samples\sample_480p.mp4
ffmpeg -y -f lavfi -i "testsrc2=size=1920x1080:rate=30000/1001" -t 3 -c:v libx264 -pix_fmt yuv420p "tests\samples\한글 테스트_1080p.mp4"
```

## 폴더 구조

```
upcon/
  __main__.py          진입점 / cli.py  개발용 CLI
  app/                 PySide6 UI (main_window, controller, cloud_settings, widgets/queue_panel …)
  core/                constants, config, env(GPU 감지), probe, ffmpeg, tempfs(디스크), jobs(배치 대기열), queue_store(저장/복원),
                       power(절전 방지), router, models(모델 레지스트리), pricing(클라우드 예상 비용), credentials(keyring)
  providers/           base(UpscalerProvider), local_ncnn(Tier 1 Real-ESRGAN), fal_base/fal_flashvsr(클라우드)
  resources/           styles.qss
bin/                   realesrgan-ncnn-vulkan.exe 등 (fetch_binaries.py, git 제외) + LICENSE
models/                ncnn 모델 (.param/.bin) + LICENSE. general-* 는 UPCON 변환본 (git 포함)
scripts/               fetch_binaries.py, convert_compact_to_ncnn.py
docs/                  설계 문서, 라이선스 목록
tests/                 pytest + UI 스모크 테스트
```

사용자 데이터: `%LOCALAPPDATA%\UPCON\` (config.json, queue.json, logs\, tmp\). API Key 는 여기 저장하지 않고 Windows 자격 증명 관리자에 저장한다.
테스트는 `UPCON_DATA_DIR` 환경변수로 데이터 폴더를 격리해 실제 사용자 설정을 건드리지 않는다.

## 배치 대기열

여러 영상을 드래그/선택으로 한꺼번에 등록하면 목록 순서대로 **한 번에 하나씩** 처리한다 (GPU/VRAM/디스크 안정성).
한 파일이 실패해도 다음 파일을 계속 처리하고, 마지막에 "N개 완료 / M개 실패"를 알린다.
"현재 파일 건너뛰기" 는 현재 파일만 취소, "전체 중지" 는 현재 파일을 취소하고 나머지를 대기 상태로 남긴다 (다시 시작 가능).
전체 진행률은 총 프레임 수 기준. 처리 중에는 Windows 자동 절전을 막고(SetThreadExecutionState), 끝나면 원래대로 돌아간다.
대기열은 queue.json 에 저장되어 앱을 다시 켜면 대기/실패/중단 항목이 복원된다 (처리 중이던 항목은 '중단됨').

## 로컬 처리 파이프라인 (Tier 1)

```
원본 ─ffmpeg(rawvideo 파이프)─▶ 청크 BMP ─▶ realesrgan-ncnn-vulkan 2× ─▶ 청크 PNG
     ─▶ ffmpeg 인코더(NVENC/AMF/QSV 자동, 없으면 x264) + 원본 오디오 ─▶ 파일명_2x.mp4
```
디코드 / 업스케일 / 인코드 3단계가 스레드로 겹쳐 돌며, 임시 디스크 사용량은 `temp_budget_mb`(기본 1.5 GB) 로 청크 크기를 역산해 제한한다.
