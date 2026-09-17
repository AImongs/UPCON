# UPCON — AI Video Upscaler

AI 영상 2× 업스케일러 (Windows). 내 PC GPU 또는 클라우드 GPU(fal.ai, 사용자 본인 계정)로 처리한다.

## 새 개발 PC 설정 (clone 후 처음 한 번)

```powershell
# 1) 저장소 가져오기  (URL 은 아직 미정 — GitHub Private repo 생성 후 교체)
git clone https://github.com/<계정>/<저장소>.git UpCon
cd UpCon

# 2) Python 3.12 이상인지 확인
python --version

# 3) 가상환경 생성 + 활성화
python -m venv .venv
.\.venv\Scripts\Activate.ps1
#   PowerShell 실행 정책 때문에 막히면:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   활성화 없이 .\.venv\Scripts\python 을 직접 써도 된다.

# 4) 의존성 설치 (개발/테스트 포함)
python -m pip install -r requirements-dev.txt
#   앱 실행만 할 거라면: python -m pip install -r requirements.txt

# 5) 실행파일 준비 — ncnn 업스케일러 + 모델 + FFmpeg/ffprobe
python scripts\fetch_binaries.py --ffmpeg

# 6) 테스트 (테스트용 영상은 자동 생성되므로 따로 준비할 것이 없다)
python -m pytest -q
python tests\ui_smoke.py

# 7) 실행
python -m upcon
```

마지막으로 **fal.ai API Key 를 다시 입력해야 한다.** 키는 Windows 자격 증명 관리자에 저장되므로
git 으로 따라오지 않는다 (의도된 동작). UPCON 실행 → 우측 상단 **⚙ 클라우드 설정** 에서
입력하고 "연결 테스트" 로 확인한다. 클라우드를 쓰지 않으면 이 단계는 건너뛰어도 된다.

로컬(내 PC GPU) 업스케일에는 **Vulkan 을 지원하는 GPU 와 최신 그래픽 드라이버**가 필요하다.
GPU 가 없거나 Vulkan 을 못 쓰면 로컬 처리는 비활성화되고 클라우드만 쓸 수 있다.

## FFmpeg / ffprobe

UPCON 은 **`bin\` 폴더에 있으면 그것을, 없으면 시스템 PATH** 의 것을 쓴다 (`upcon/core/binaries.py`).
둘 중 하나만 있으면 된다.

```powershell
# 방법 A (권장) — 저장소 bin\ 에 동봉본을 받는다. 시스템에 FFmpeg 를 설치하지 않아도 된다.
python scripts\fetch_binaries.py --ffmpeg      # ncnn + 모델 + ffmpeg.exe / ffprobe.exe
python scripts\fetch_binaries.py               # --ffmpeg 를 빼면 ncnn + 모델만 받는다

# 방법 B — 이미 시스템에 FFmpeg 가 설치돼 PATH 에 있으면 그대로 쓴다
ffmpeg -version                                # 확인만 하면 된다
```

`bin\` 은 git 에 포함되지 않으므로(라이선스 txt 제외) **새 PC 마다 방법 A 또는 B 중 하나가 필요하다.**
배포본(설치 파일)에는 `bin\` 이 항상 동봉되므로 최종 사용자는 FFmpeg 를 따로 설치하지 않는다 (STEP 7).

## 개발 환경 실행

```powershell
# 실행
.\.venv\Scripts\python -m upcon

# UI 없이 파이프라인만 (속도/디스크 측정 출력)
.\.venv\Scripts\python -m upcon.cli 영상.mp4 [--out-dir 폴더] [--model realesr-animevideov3]
```

## 배포본 빌드 (Portable)

```powershell
# 1) 동봉 실행파일 준비 (SHA-256 검증 포함)
.\.venv\Scripts\python scripts\fetch_binaries.py --ffmpeg

# 2) Portable 빌드 (onedir)
.\.venv\Scripts\python -m PyInstaller packaging\upcon.spec --noconfirm

# 결과: dist\UPCON\  (UPCON.exe + _internal\)
```

FFmpeg 는 **n8.1.2 GPL shared** 빌드를 불변 태그로 고정해 받는다 (`scripts/fetch_binaries.py`).
`ffmpeg.exe`/`ffprobe.exe` 는 작고 `av*.dll`·`sw*.dll` 을 공유하므로, static 빌드 대비
`bin\` 이 317MB → 196MB 로 줄어든다 (기능 동일). `ffplay.exe` 는 UPCON 이 쓰지 않아 받지 않는다.

`onefile` 은 쓰지 않는다 — 실행할 때마다 수백 MB 를 임시 폴더에 풀어야 하고 문제 추적이 어렵다.

빌드본 검증용 콘솔 실행파일(배포본에는 포함되지 않음):

```powershell
.\.venv\Scripts\python -m PyInstaller packaging\selftest.spec --noconfirm --distpath dist-selftest
# dist-selftest\UPCON-selftest\UPCON-selftest.exe 실행 → 경로/keyring/GPU/ncnn/실제 업스케일/
#   폴백 인코더/입력 형식 매트릭스/임시파일 정리까지 점검하고 ALL OK 또는 FAIL 을 출력한다.
```

라이선스: 배포 구성의 제3자 구성요소와 의무사항은 `docs/THIRD_PARTY_NOTICES.md` 참조.
FFmpeg 는 GPL 빌드이며 Corresponding Source 제공 방식은 installer 단계에서 확정한다.
Real-ESRGAN 가중치 라이선스는 **unresolved** 상태다.

### 앱 아이콘

원본 `upcon/resources/upcon.png` 에서 Windows 멀티 해상도 ICO 를 생성한다.

```powershell
.\.venv\Scripts\python -m pip install "Pillow>=10"      # pyproject 의 [icon] extra
.\.venv\Scripts\python scripts\make_icon.py             # -> upcon/resources/upcon.ico
```

`upcon.ico` 하나만 있으면 **UPCON.exe · 앱 창 · 시작 메뉴 바로가기 · Setup.exe · Uninstaller**
가 모두 같은 아이콘을 쓴다 (`paths.app_icon_file()`, `packaging/upcon.spec` 의 `APP_ICON`,
`upcon.iss` 의 `SetupIconFile`). 파일이 없으면 기본 아이콘으로 정상 빌드된다.

포함 해상도: 16 / 32 / 48 / 64 / 128 / 256.
원본 PNG 는 흰 여백이 있는 RGB 이므로, 스크립트가 여백을 잘라내고 둥근 모서리 바깥을
투명으로 만든다 (어두운 작업 표시줄에서 흰 모서리가 남지 않게).

`--mark-small` 옵션을 주면 작은 크기(16/32/48)에 워드마크를 뺀 심볼만 넣는다.
작은 아이콘에서는 "UPCON / AI VIDEO UPSCALER" 글자가 뭉개져 읽히지 않기 때문이다.

## Windows Installer (Inno Setup)

```powershell
# 1) Portable 빌드 먼저
.\.venv\Scripts\python -m PyInstaller packaging\upcon.spec --noconfirm

# 2) 설치 파일 생성 (버전은 upcon/version.py 에서 읽어 ISCC 에 주입)
.\.venv\Scripts\python packaging\build_installer.py
# -> dist-installer\UPCON_Setup_<version>.exe

# 3) 안전장치 정적 검증 (설치는 하지 않는다)
.\.venv\Scripts\python packaging\test_installer.py
```

`packaging/upcon.iss` 는 검증된 `dist\UPCON` 을 그대로 설치한다 (내부 구조 재조립 없음).

* **per-user 설치** — `PrivilegesRequired=lowest`, 기본 경로 `%LOCALAPPDATA%\Programs\UPCON`.
  관리자 권한(UAC)을 요구하지 않는다. UPCON 은 서비스·드라이버·시스템 통합이 없고
  사용자 데이터도 `%LOCALAPPDATA%` 에 쓰므로 관리자 권한이 필요할 이유가 없다.
* **AppId 고정** — `{B04C51EA-...}` 를 바꾸면 업그레이드가 아니라 별도 설치가 된다. 절대 변경 금지.
* 사용자 데이터는 설치 폴더가 아니라 `%LOCALAPPDATA%\UPCON`, API Key 는 Windows 자격 증명 관리자.

### 설치 프로그램 외형

브랜드 요소는 확정된 `upcon/resources/upcon.png` 하나에서 파생한다. 새 로고를 만들지 않는다.

```powershell
.\.venv\Scripts\python scripts\make_installer_images.py   # 이미지를 다시 만들 때만
```

* `SetupIconFile` = `upcon/resources/upcon.ico` — Setup.exe · Uninstaller · 위저드 타이틀바
* `WizardImageFile` / `WizardSmallImageFile` = `packaging/installer/wizard-*.bmp`
  원본에서 심볼·워드마크·배경 그라데이션을 떼어내 배율별(100/125/150/200%)로 재배치한다.
  비율을 바꾸거나 늘리지 않는다. 이미지가 없으면 Inno 기본 이미지로 정상 빌드된다.
* 환영 화면(`DisableWelcomePage=no`)에 UPCON 브랜드와 한 줄 설명만 표시한다.

### 제거 정책

| 대상 | 기본 동작 |
|---|---|
| 프로그램 파일 · 바로가기 · 레지스트리 항목 | 제거 |
| `%LOCALAPPDATA%\UPCON` (설정 · 대기열 · 로그) | **보존** |
| fal.ai API Key (자격 증명 관리자) | **보존** |
| 업스케일 결과 영상 | **절대 삭제하지 않음** (원본 옆에 저장되므로 애초에 대상 아님) |

대화형 제거에서 사용자가 **명시적으로 [예]** 를 선택했을 때만 사용자 데이터와 API Key 를 함께 지운다.
기본 버튼은 [아니요] 이고, 무인 제거(`/SUPPRESSMSGBOXES`)에서는 **보존**이 선택된다.

> **Inno 함정**: 평범한 `MsgBox` 는 `/SUPPRESSMSGBOXES` 를 무시하고 `IDYES` 를 반환한다.
> 반드시 `SuppressibleMsgBox(..., IDNO)` 를 써야 무인 제거에서 데이터가 보존된다.
> `tests/test_installer_policy.py` 가 이 규칙을 회귀 테스트로 강제한다.

### ⚠️ 설치/제거 자동 테스트 규칙

**Git Bash 로 Windows 스위치를 전달하지 않는다.** MSYS 경로 변환이
`/VERYSILENT` 를 `C:/Program Files/Git/VERYSILENT` 로 바꿔버려 설치 프로그램이 **대화형으로**
실행된다. 실제로 이 때문에 사용자 데이터와 fal API Key 가 삭제된 적이 있다.

```powershell
# OK - PowerShell
Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -Wait
```
```python
# OK - Python subprocess (리스트 인자)
subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
```
```bash
# 금지 - Git Bash
./UPCON_Setup_0.3.0.exe /VERYSILENT      # -> 경로로 변환되어 대화형 실행됨
```

테스트는 실제 사용자 데이터·자격 증명을 건드리지 않는다:
`UPCON_DATA_DIR` 로 데이터 폴더를 격리하고, keyring 은 `UPCON-test`(pytest) /
`UPCON-selftest`(packaging/selftest.py) 네임스페이스만 쓴다.
실제 fal credential(서비스 이름 `UPCON`)은 **읽지도 삭제하지도 않는다.**

### FFmpeg Corresponding Source (GPLv3 대응)

동봉 FFmpeg 는 `--enable-gpl --enable-version3` 빌드라 **GPLv3** 가 적용되고,
바이너리를 재배포하는 쪽(= UPCON)이 대응 소스를 제공할 의무를 진다 (GPLv3 §6).
BtbN 저장소에는 준수 문구도 패키징된 소스도 없으므로 직접 모은다.

```powershell
.\.venv\Scripts\python scripts\build_ffmpeg_source_package.py
# -> dist-ffmpeg-source\UPCON-FFmpeg-Corresponding-Source-<version>.zip
```

**용량보다 완전성을 우선한다.** 현재 결과는 약 944 MB (해제 951 MB, 264 파일).

#### 분류 기준

BtbN 빌드 시스템의 `ffbuild_enabled()` 를 우리 빌드 조건(`TARGET=win64`,
`VARIANT=gpl-shared`, ffver 801)으로 실제 평가해서 나눈다. 추측하지 않는다.

| | 의미 | 처리 |
|---|---|---|
| **A** | 이 바이너리에 컴파일/정적 링크됨 | **소스 수집** (96개) |
| **B** | System Library | 제외 + `SYSTEM-LIBRARIES.txt` 에 근거 기록 |
| **C** | 빌드 도구/레시피 | BtbN 스냅샷으로 보존 (patch 포함) |
| **D** | 이 빌드와 무관 (다른 타깃/변형) | 제외 + 사유 기록 |

애매하면 보수적으로 A 로 둔다. 확신 없는 것을 임의로 빼지 않는다.

System Libraries 판단은 "Microsoft 것이라서"가 아니라 **실제 PE import 테이블을 읽어서**
한다. 동봉 DLL/EXE 가 import 하는 외부 DLL 을 전부 뽑아 `SYSTEM-LIBRARIES.txt` 에 기록한다
(현재 45개, 모두 Windows 기본 구성요소이며 배포본에 동봉되지 않는다).

#### 수집 방식

| 호스트 | 방법 |
|---|---|
| github / gitlab / googlesource / savannah | 리비전 지정 tarball |
| 그 외 git 호스트 | `git archive` (얕은 clone) |
| **SVN (xvid, lame)** | **WebDAV `!svn/bc/<rev>/` 로 정확한 리비전 export** (svn 클라이언트 불필요) |

xvid 는 `svn.xvid.org` **rev 2204**, lame 은 `svn.code.sf.net` **rev 6761** 로 고정 수집한다.
릴리스 tarball 은 리비전이 달라 이 바이너리에 대응하지 않으므로 쓰지 않는다.

#### 완전성 검사 (경고가 아니라 **실패**)

다음 중 하나라도 걸리면 아카이브를 **만들지 않고** exit 1 로 끝난다.
필수 component 누락 / 리비전 미고정 / 라이선스 누락 / 체크섬 불일치 /
수집 실패 / BtbN 스냅샷에 `patches/`·`scripts.d/` 누락 / 바이너리↔메타데이터 불일치.

별도 라이선스 파일이 upstream 에 아예 없는 component 는 `LICENSE_IN_SOURCE` 에
**근거와 함께** 명시적으로 예외 등록한다 (현재 `ffnvcodec` 1건: MIT 고지가 각 헤더
상단 주석에 들어 있음을 확인).

재실행 속도를 위해 `dist-ffmpeg-source/_cache/` 에 다운로드를 캐시한다 (지워도 무방).
생성물은 `dist-*/` 라 git 에 포함되지 않는다.

## 의존성

| 파일 | 용도 |
|---|---|
| `requirements.txt` | 런타임 — PySide6, keyring, httpx, fal-client, nvidia-ml-py |
| `requirements-dev.txt` | 위 + pytest, pyinstaller |
| `pyproject.toml` 의 `[convert]` | torch — `scripts/convert_compact_to_ncnn.py` 로 모델을 다시 변환할 때만 |

`tests/test_packaging.py` 가 **코드의 실제 import 와 의존성 선언을 자동으로 대조**한다.
새 import 를 추가하고 선언을 빠뜨리면 테스트가 실패하므로, "내 PC 에서만 되는" 상태를 막는다.

## 테스트

```powershell
.\.venv\Scripts\python -m pytest -q          # 단위 + 실제 GPU 업스케일/취소/디스크/배치 테스트 (약 90초)
.\.venv\Scripts\python tests\ui_smoke.py     # 실제 UI 배치 시나리오 A~N 자동 테스트 + 스크린샷 (tests\out)
.\.venv\Scripts\python -m pytest tests/integration -q --run-cloud   # 실제 fal.ai API (비용 발생, 키 없으면 skip)
```

**테스트 영상은 저장소에 두지 않는다.** pytest fixture(`tests/conftest.py`)와 `ui_smoke.py` 가
FFmpeg 로 필요한 합성 영상(854×480 24fps 5초 오디오 있음 / 1920×1080 29.97fps 무음 / 한글 경로)을
임시 폴더에 만들고 세션이 끝나면 정리한다. 따라서 clone 직후 바로 `pytest` 를 돌릴 수 있다.
FFmpeg/ffprobe 가 없으면 영상이 필요한 테스트만 skip 되고 나머지는 실행된다.
GPU(Vulkan)가 없으면 실제 업스케일 테스트는 skip 된다.

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
