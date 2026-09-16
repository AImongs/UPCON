# UPCON — STEP 1: 환경 분석 및 아키텍처 설계

작성일: 2026-09-15
상태: **승인 대기** (코드 작성 전)

---

## 1. 개발 PC 환경 (터미널 실측)

| 항목 | 결과 |
|---|---|
| OS | Windows 11 Pro 10.0.22631 (64bit) |
| CPU / RAM | i7-8700 (6C/12T) / 32 GB |
| GPU | **NVIDIA GeForce RTX 5060, 8 GB VRAM**, Compute Capability **12.0 (Blackwell)** |
| 드라이버 | 581.29 (CUDA 13.0 지원) |
| CUDA Toolkit | 13.1 (nvcc 13.1.115, `CUDA_PATH` 설정됨) |
| Python | 3.12.7 (기본), 3.11 도 설치됨. torch 미설치, onnxruntime 1.28 설치됨 |
| Node.js | v24.16.0 / npm 11.13.0 |
| Git | 2.54.0 |
| FFmpeg | n8.0.1 (`C:\FFmpeg\bin`, **GPL 빌드**: libx264/libx265 포함) |
| Rust / MSVC | **없음** (Tauri 불가, 네이티브 빌드 불가) |
| 설치 도구 | **Inno Setup 6 설치됨**, NSIS 없음 |
| 프로젝트 폴더 | `C:\Projects\UpCon` — 비어 있음, git 미초기화 |
| 디스크 | **C: 여유 17.5 GB** (PyTorch+CUDA 환경 약 6~7 GB 필요 → 주의) |

핵심 시사점
- RTX 5060(sm_120)은 PyTorch **2.14.0 + cu130 정식 Windows 휠**로 지원됨 (확인: download.pytorch.org/whl/cu130 에 torch-2.14.0+cu130 cp312 win_amd64 존재).
- Rust/MSVC 가 없으므로 Tauri 나 CUDA 커스텀 커널 빌드가 필요한 모델(FlashVSR 로컬)은 현실적으로 배제.

---

## 2. fal.ai 공식 문서 확인 결과 (2026-09-15 기준)

### 인증
- API Key 방식. 환경변수 `FAL_KEY`, HTTP 헤더 `Authorization: Key <FAL_KEY>`.
- Python 클라이언트: `pip install fal-client` (`fal_client.subscribe / submit / status / result / upload_file`, `_async` 변형 제공).

### Queue REST API (`https://queue.fal.run`)
| 동작 | 메서드/URL |
|---|---|
| 제출 | `POST /{model_id}` → `request_id, status_url, response_url, cancel_url, queue_position` |
| 상태 | `GET /{model_id}/requests/{id}/status?logs=1` → `IN_QUEUE(queue_position) / IN_PROGRESS(logs[]) / COMPLETED(metrics.inference_time)` |
| 상태 스트림 | `GET .../status/stream` (SSE) |
| 결과 | `GET /{model_id}/requests/{id}` |
| 취소 | `PUT /{model_id}/requests/{id}/cancel` |

→ **실제 진행률(%)은 제공되지 않음.** 큐 순번, 진행 중 로그, 완료만 알 수 있음. UI 는 "대기열 N번째 → 처리 중 (경과 시간) → 다운로드 중 x%" 로 정직하게 표시.

### 모델 비교 (모두 "Commercial use" 표기)
| 엔드포인트 | 방식 | 가격 (공식) | 2× 지원 | 비고 |
|---|---|---|---|---|
| `fal-ai/flashvsr/upscale/video` | 확산 기반 스트리밍 VSR, temporal | **$0.0005 / MP** (출력 W×H×프레임) | `upscale_factor` 기본값 2 | `preserve_audio`, `color_fix`, `acceleration(regular/high/full)`, `output_format` 옵션 |
| `fal-ai/seedvr/upscale/video` | SeedVR2, temporal | $0.001 / MP | 지원 | FlashVSR 대비 2배 가격 |
| `fal-ai/video-upscaler` | Real-ESRGAN 프레임별 | $0.0008 / MP | `scale` | temporal 없음 |

**비용 예시 (FlashVSR, 24fps 10초 = 240프레임)**
- 854×480 → 1708×960: 393.6 MP → **약 $0.20**
- 1920×1080 → 3840×2160: 1,990 MP → **약 $1.00**
→ 업스케일 전 예상 비용을 UI 에 표시할 것.

**1차 클라우드 기본 모델: FlashVSR** (가장 저렴 + temporal + 2× 기본값). SeedVR2 는 "고품질" 선택지, video-upscaler 는 폴백.

미확인/리스크: 파일 업로드 용량 제한과 최대 영상 길이는 문서에 명시되지 않음 → STEP 4 에서 실제 API 로 검증.

---

## 3. 로컬 AI 업스케일 모델 조사

| 모델 | 라이선스 | temporal | VRAM | Windows/RTX50 | 속도 | 판정 |
|---|---|---|---|---|---|---|
| **FlashVSR v1.1** (OpenImagingLab) | Apache-2.0 | O | A100 기준, 미명시 | **Block-Sparse-Attention CUDA 빌드 필수**, "RTX 40/50 호환성 미확인" | A100 17fps@768×1408 | ✗ 수강생 PC 배포 불가 (커스텀 커널 빌드) |
| **SeedVR2-3B** (ByteDance) + numz 구현 | Apache-2.0 (모델·코드 모두) | O | fp16 24GB+, fp8 12~16GB, **GGUF Q4 + BlockSwap 8GB 이하 가능** | PyTorch SDPA 폴백 지원, flash-attn 선택 | 느림 (프레임당 수 초) | ◎ **로컬 고화질 엔진 (Tier 2)** |
| **Real-ESRGAN** (`RealESRGAN_x2plus`, `realesr-animevideov3`) via **realesrgan-ncnn-vulkan** | BSD-3 (모델/코드), MIT (ncnn 실행파일), BSD-3 (ncnn) | ✗ (프레임별, animevideov3 는 영상용 튜닝) | 2 GB 이상이면 충분 | Vulkan → NVIDIA/AMD/Intel 모두, CUDA 불필요, 단일 exe ~30 MB | 빠름 | ◎ **로컬 기본 엔진 (Tier 1)** |
| RealBasicVSR / BasicVSR++ (MMagic) | Apache-2.0 | O | 4× 전용, 고VRAM | mmcv Windows 빌드 난이도 높음 | 느림 | △ 보류 |
| Video2X / Upscayl 재활용 | **AGPL-3.0** | - | - | - | - | ✗ 라이선스 부적합 |

### 결정
- **Tier 1 (기본, STEP 3)**: Real-ESRGAN × ncnn-vulkan. 이유: 라이선스 깨끗, 어떤 GPU 에서도 동작, PyTorch/CUDA 의존 없음 → 설치본이 작고 실패 요인이 적음. 수강생 PC 다양성을 고려하면 "무조건 돌아가는" 로컬 엔진이 먼저 필요.
- **Tier 2 (선택, STEP 3 후반 또는 STEP 6 이후)**: SeedVR2-3B (PyTorch cu130). temporal consistency 를 갖춘 유일한 현실적 로컬 후보. VRAM ≥ 12 GB 에서 자동 후보, 8 GB 는 GGUF+BlockSwap 으로 "느린 고화질 모드"로만 제공. 엔진(약 3.5 GB)과 가중치는 **첫 사용 시 다운로드**(설치본에 미포함).
- 개발 PC(RTX 5060 8 GB)에서는 Tier 1 은 쾌적, Tier 2 는 동작하되 느릴 것으로 예상 → 실측 후 임계값 조정.
- 솔직한 한계: Tier 1 은 프레임 간 깜빡임(flicker) 가능성이 있음. 실사 AI 영상은 `RealESRGAN_x2plus`, 애니메이션 스타일은 `realesr-animevideov3` 를 선택하도록 하고, 품질이 중요한 경우 클라우드 FlashVSR / 로컬 SeedVR2 를 안내.

---

## 4. 기술 스택 선정

| 후보 | 장점 | 단점 | 판정 |
|---|---|---|---|
| **Python 3.12 + PySide6** | AI 런타임(PyTorch/ncnn/ffmpeg 제어)과 UI 가 한 언어, 네이티브 드래그앤드롭, QThread, 현대적 QSS 스타일링, PyInstaller+Inno Setup 배포 경로 검증됨 | 실행파일 크기(기본 ~150 MB), 백신 오탐 가능성 | **채택** |
| Electron | 웹 UI 자유도 | Python 사이드카 별도 패키징 필요, +200 MB Chromium, 프로세스 2개 관리 | 보류 |
| Tauri | 가벼움 | **Rust/MSVC 미설치**, 역시 Python 사이드카 필요 | 제외 |
| Flet / Textual 등 | - | 성숙도·DnD·파일 다이얼로그 제약 | 제외 |

주요 패키지 (라이선스)
- PySide6 6.11 — LGPL-3.0 (동적 링크, DLL 분리 배포 → 상업 배포 가능, 고지문 포함)
- fal-client 1.0.1 — Apache-2.0 (fal 공식), httpx — BSD-3
- keyring 25.7 — MIT (Windows Credential Manager 백엔드)
- nvidia-ml-py — BSD (GPU/VRAM 감지)
- PyInstaller 6.22 — GPL-2.0 + **예외 조항** (생성된 실행파일에 GPL 비적용) → OK
- Nuitka 4.x — PyPI 분류가 AGPL 로 표기됨 → **사용 안 함**
- (Tier 2) torch 2.14.0+cu130 — BSD-3, spandrel — MIT, safetensors/huggingface_hub — Apache-2.0

---

## 5. 전체 아키텍처

```
┌──────────────────────────── UPCON (PySide6) ────────────────────────────┐
│  MainWindow  ─ DropZone ─ FileList(대기/처리중/완료/실패) ─ ProgressPanel │
│  SettingsDialog ─ 클라우드 연결(fal.ai) ─ 엔진/출력 설정                  │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ Qt Signals (진행률/상태/오류)
┌───────────────▼──────────────────────────────────────────────────────────┐
│  JobManager (QThread)   순차 큐, 취소, 재시도, 상태머신                  │
│     └─ Router (자동 모드)  GPU/VRAM/엔진 설치 여부/클라우드 연결 판단     │
└───────────────┬──────────────────────────────────────────────────────────┘
                │ UpscalerProvider 인터페이스
   ┌────────────┼──────────────┬───────────────────┬─────────────────────┐
   ▼            ▼              ▼                   ▼                     ▼
LocalNcnn    LocalSeedVR2   FalFlashVSR       FalSeedVR            FalRealESRGAN
(Tier 1)     (Tier 2,선택)  (클라우드 기본)    (클라우드 고품질)     (클라우드 폴백)
   │            │              └──── FalBase: 업로드→submit→status 폴링→다운로드→취소
   ▼            ▼
┌──────────────────────────── core ───────────────────────────────────────┐
│ probe(ffprobe)  ffmpeg(프레임 추출·인코딩·오디오 mux)  gpu(NVML/Vulkan)  │
│ secrets(keyring)  tempfs(임시폴더·디스크 검사)  errors(사용자메시지 매핑) │
│ logging(민감정보 마스킹)  cost(fal 비용 추정)                              │
└──────────────────────────────────────────────────────────────────────────┘
```

Provider 인터페이스(요지)
```python
class UpscalerProvider(ABC):
    id: str; name: str; kind: Literal["local", "cloud"]; temporal: bool
    def is_available(self, env: SystemEnv) -> Availability      # 가능/불가 + 사유
    def estimate(self, info: VideoInfo, scale: int) -> Estimate  # 예상 비용/시간/VRAM
    def upscale(self, job: Job, progress: ProgressCallback, cancel: CancelToken) -> Path
```

처리 파이프라인
1. `ffprobe` 분석 → 해상도/FPS/길이/코덱/오디오 유무/프레임 수
2. 출력 파일명 결정 `scene01_2x.mp4` (중복 시 `scene01_2x (2).mp4`), 원본 절대 미변경
3. 디스크 여유 검사 (임시 + 출력 예상 크기)
4. 로컬(Tier 1): ffmpeg 로 **N프레임 단위 청크**(예: 200프레임) PNG 추출 → ncnn-vulkan 업스케일 → ffmpeg 로 청크 인코딩(중간 코덱) → 청크 concat + 원본 오디오 stream copy → MP4(H.264, 원본 FPS). 청크 방식으로 임시 디스크 사용량 상한 관리. 진행률 = 처리 프레임/전체 프레임 (실제값).
5. 로컬(Tier 2): 프레임을 메모리로 스트리밍(ffmpeg rawvideo pipe) → SeedVR2 배치 추론 → 인코더 파이프.
6. 클라우드: `upload_file` → `submit(upscale_factor=2, preserve_audio=true)` → status 폴링(큐 순번/경과시간 표시) → 결과 다운로드(바이트 진행률) → 오디오 검증, 없으면 로컬 mux → 저장.

자동 모드 판단 순서
1. NVIDIA GPU + VRAM ≥ 12 GB + Tier 2 엔진 설치됨 → **로컬 SeedVR2** ("RTX 4080을 사용하여 내 PC에서 고화질 처리합니다")
2. Vulkan 가능한 GPU(NVIDIA ≥ 2 GB, AMD, Intel Arc) → **로컬 Real-ESRGAN** ("RTX 5060을 사용하여 내 PC에서 처리합니다")
3. 그 외 + fal 연결됨 → **클라우드 FlashVSR** ("사용 가능한 GPU가 없어 클라우드 GPU를 사용합니다 · 예상 비용 $0.20")
4. 그 외 → 안내: 설정에서 fal.ai 연결 필요
- 임계값은 STEP 3/5 실측 후 조정. VRAM 부족(OOM) 발생 시 타일 크기 축소 → 재시도 → 그래도 실패 시 클라우드 전환 제안.

보안
- API Key: `keyring` → Windows Credential Manager (`UPCON/fal_api_key`). 파일/레지스트리 평문 저장 금지.
- 로그: `Key ...` 헤더, `FAL_KEY`, 업로드 URL, request_id 는 마스킹 필터 통과 후 기록.
- `.gitignore` 에 `.env`, `*.key`, `secrets/`, 임시·모델 폴더. 코드/리소스 내 키 검색 pre-commit 훅.
- 오류 메시지: 사용자용 한국어 문구(`errors.py` 매핑 테이블) 와 개발자 로그(`%LOCALAPPDATA%\UPCON\logs`) 분리.

---

## 6. 프로젝트 폴더 구조 (예정)

```
UpCon/
├─ upcon/
│  ├─ __main__.py            # 진입점
│  ├─ app/                   # PySide6 UI (main_window, drop_zone, file_list, settings_dialog, styles.qss)
│  ├─ core/                  # probe, ffmpeg, gpu, jobs, router, tempfs, errors, logging, cost
│  ├─ providers/             # base, local_ncnn, local_seedvr2, fal_base, fal_flashvsr, fal_seedvr, fal_realesrgan
│  ├─ secrets/               # keyring wrapper
│  └─ resources/             # 아이콘, QSS
├─ bin/                      # ffmpeg.exe, ffprobe.exe, realesrgan-ncnn-vulkan.exe (+LICENSE)  [git 제외, 스크립트로 다운로드]
├─ models/                   # Real-ESRGAN .param/.bin (+LICENSE)                                 [git 제외]
├─ tests/                    # 단위/통합 테스트, samples/ (짧은 테스트 영상)
├─ scripts/                  # fetch_binaries.py, build.ps1
├─ build/                    # upcon.spec (PyInstaller), upcon.iss (Inno Setup)
├─ docs/                     # 이 문서, THIRD_PARTY_NOTICES.md
├─ pyproject.toml, requirements.txt, requirements-engine.txt (Tier 2)
├─ .gitignore, README.md, LICENSE
```

---

## 7. Windows 배포 방식

1. **PyInstaller (onedir)** → `dist/UPCON/UPCON.exe` + Qt DLL + `bin/` + `models/`
2. **Inno Setup 6** (설치됨) → `UPCON_Setup.exe` (사용자별 설치, 바탕화면 아이콘, 언인스톨러, 약 150~200 MB)
3. FFmpeg/ffprobe/ncnn-vulkan 실행파일은 **별도 프로세스로 호출** — 설치본에 동봉하므로 사용자가 FFmpeg 를 따로 설치할 필요 없음
4. Tier 2 엔진(PyTorch cu130 + SeedVR2 가중치, 수 GB)은 설치본에 넣지 않고 앱 내 "고화질 로컬 엔진 설치" 버튼으로 `%LOCALAPPDATA%\UPCON\engine` 에 다운로드
5. 코드 서명 인증서 미보유 시 SmartScreen 경고 발생 → 수강생 안내문 필요 (또는 인증서 구매)

---

## 8. 라이선스 검토 요약

| 구성요소 | 라이선스 | 배포 판단 |
|---|---|---|
| Real-ESRGAN 코드·가중치 | BSD-3-Clause | OK |
| realesrgan-ncnn-vulkan / ncnn | MIT / BSD-3 | OK |
| SeedVR2 코드·가중치, numz 구현 | Apache-2.0 | OK (NOTICE 포함) |
| FlashVSR | Apache-2.0 | 로컬 미사용 (fal 경유만) |
| fal.ai 모델 | fal 페이지 "Commercial use" | OK (사용자 본인 계정 과금) |
| PySide6 / Qt | LGPL-3.0 | OK — 동적 링크, 고지·라이선스 전문 동봉, 사용자가 Qt DLL 교체 가능 |
| PyTorch, numpy, httpx | BSD-3 | OK |
| keyring, spandrel | MIT | OK |
| PyInstaller | GPL-2.0 + 예외 | OK (부트로더 예외 조항) |
| **FFmpeg (GPL 빌드, libx264)** | GPL-2.0+ | **주의.** 별도 exe 로 동봉·subprocess 호출(업계 표준 관행), GPL 전문 + 소스 링크 동봉. 더 보수적으로 가려면 LGPL 빌드 + NVENC/openh264 사용 (STEP 7 에서 최종 결정) |
| Nuitka, Video2X, Upscayl | AGPL | **사용 안 함** |
| CUDA 런타임 (torch 휠 내 포함) | NVIDIA EULA (재배포 허용) | OK |

---

## 9. 개발 난이도 및 위험 요소

| # | 위험 | 영향 | 대응 |
|---|---|---|---|
| 1 | **개발 PC C: 여유 17.5 GB** | Tier 2 (PyTorch ~7 GB + 가중치 ~3~6 GB) 설치 시 부족 | venv/모델을 다른 드라이브에 두거나 정리 필요 — **사용자 확인 요청** |
| 2 | Real-ESRGAN 프레임별 flicker | 실사 영상 품질 불만 | Tier 2 / 클라우드 안내, 모델 선택 |
| 3 | SeedVR2 8 GB VRAM 에서 매우 느림 | 8 GB 사용자에게 로컬 고화질이 비현실적일 수 있음 | 실측 후 임계값 12 GB, 8 GB 는 "느림" 경고 후 선택 |
| 4 | fal 업로드 용량/길이 제한 미확인 | 긴 영상 실패 | STEP 4 실측, 필요 시 구간 분할 업로드 |
| 5 | fal 진행률 미제공 | 진행률 표시 한계 | 큐 순번·경과시간·다운로드% 만 표시 (가짜 % 금지) |
| 6 | 긴 영상 임시 디스크 | 1080p 5분 = 7,200 프레임 PNG 수십 GB | 청크 처리(200프레임), 사전 디스크 검사 |
| 7 | 한글 경로/파일명 | ffmpeg/ncnn 인자 인코딩 문제 | UTF-8 subprocess, 테스트 케이스 포함 |
| 8 | PyInstaller 백신 오탐 / SmartScreen | 수강생 설치 실패 | 코드 서명 검토, 안내문 |
| 9 | fal API 변경 | 클라우드 중단 | Adapter 구조, 모델 ID 설정화 |
| 10 | RTX 50 + PyTorch 호환 | Tier 2 실행 실패 | cu130 정식 휠 확인됨(2.14.0), sm_120 포함 |

예상 난이도: STEP 2 낮음 · STEP 3(Tier 1) 중 · STEP 3(Tier 2) 높음 · STEP 4 중 · STEP 5 낮음 · STEP 6 중 · STEP 7 중~높음(서명/오탐)

---

## 10. 승인 요청 사항

1. 기술 스택: **Python 3.12 + PySide6 + PyInstaller + Inno Setup**
2. 로컬 엔진: **Tier 1 Real-ESRGAN(ncnn-vulkan) 기본 + Tier 2 SeedVR2(선택 다운로드)**
3. 클라우드 기본 모델: **FlashVSR** (SeedVR2, Real-ESRGAN 은 선택지)
4. FFmpeg: GPL 빌드를 별도 exe 로 동봉 (subprocess) — 이의 없으면 이대로 진행
5. 개발 PC 디스크: Tier 2 개발용 venv/모델 위치를 다른 드라이브로 둘지 결정
6. STEP 2 (기본 UI) 착수 승인
