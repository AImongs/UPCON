# UPCON 제3자 구성요소 및 라이선스

이 문서는 **실제 배포되는 Portable 구성**(`packaging/upcon.spec` 빌드 결과)을 기준으로 한다.
"코드 라이선스"와 "모델 가중치 라이선스"를 분리해 기록한다.

마지막 갱신: STEP 7-3 (배포 구성 확정 시점)
배포 형태: PyInstaller **onedir** Portable. FFmpeg/ncnn 은 **별도 실행파일**로 동봉하며
UPCON 본체(Python)는 `subprocess` + 파이프/명령행 인자로만 통신한다.

---

## 1. 동봉 실행파일 (bin/)

| 구성요소 | 파일 | 버전 | 라이선스 | 근거 |
|---|---|---|---|---|
| **FFmpeg / ffprobe** | `ffmpeg.exe`, `ffprobe.exe` + `av*.dll`, `sw*.dll` (7개) | `n8.1.2-53-g1005b294ff` (BtbN win64 **gpl-shared**) | **GPL** — 빌드가 `--enable-gpl --enable-version3` 이므로 실질 **GPLv3** | `bin/LICENSE-ffmpeg.txt`, `ffmpeg -version` |
| Real-ESRGAN-ncnn-vulkan | `realesrgan-ncnn-vulkan.exe` | 20220424 | **MIT** (© 2021 Xintao Wang) | `bin/LICENSE-realesrgan-ncnn-vulkan.txt` |
| ncnn (위 exe 에 정적 링크) | (exe 내부) | — | **BSD-3-Clause** (Tencent) + 허용적 제3자 구성요소 | `bin/LICENSE-ncnn.txt` |
| Visual C++ OpenMP 런타임 | `vcomp140.dll` | — | Microsoft 재배포 가능 런타임 | Real-ESRGAN 공식 릴리스 zip 에 포함되어 배포됨 |

**`ffplay.exe` 는 동봉하지 않는다** (UPCON 미사용).

### FFmpeg GPL 관련 사실

- 이 빌드는 **`--enable-gpl --enable-version3`** 이며 **`--enable-nonfree` 가 아니다** → 재배포 자체는 허용된다.
- GPL 대상 구성요소가 포함된다: **`libx264`**, libx265, libxvid, libvidstab, frei0r, librubberband, libzvbi, libxavs2.
- UPCON 은 `libx264` 를 **하드웨어 인코더를 못 쓸 때의 소프트웨어 폴백**으로 실제 사용한다.
  (LGPL 빌드에는 libx264 가 없어 폴백 인코더를 교체해야 하므로, GPL 빌드를 유지하기로 결정했다.)

### GPLv3 준수 체크리스트 (배포 전)

법률 자문이 아니며, 배포 전에 확인할 실무 항목이다.

1. `LICENSE-ffmpeg.txt`(GPL 전문) 동봉 — **현재 포함됨**
2. 프로그램 "정보" 화면에 FFmpeg 사용 사실과 라이선스 고지 — **미구현 (installer 단계)**
3. **Corresponding Source 제공** — 동봉한 바이너리와 정확히 대응하는 소스를,
   배포본과 같은 위치에서 내려받게 하거나 3년간 유효한 서면 제안으로 제공 (GPLv3 §6)
   → **소스 패키지는 준비 완료.** `scripts/build_ffmpeg_source_package.py` 가
   `UPCON-FFmpeg-Corresponding-Source-<version>.zip` 을 생성한다 (FFmpeg 소스 + BtbN 빌드
   스크립트 스냅샷 + GPL 구성요소 소스 + 라이선스 + BUILD-INFO + SOURCE-MANIFEST).
   → **실제 배포 URL/호스팅 방식은 아직 미확정.** 배포 시 설치 파일과 같은 위치에 올려야 한다.
4. **빌드 설정 정보 제공** — `ffmpeg -version` 의 configure 라인, BtbN 빌드 스크립트 출처
   (고정 태그: `autobuild-2026-09-15-13-18`, `scripts/fetch_binaries.py` 에 URL·SHA-256 기록)
5. 수강생 대상 유료 강의 배포도 GPL 상 "conveying" 에 해당하므로 위 의무가 동일하게 적용된다.

> UPCON 본체(Python)가 GPL 로 전염되는지: FSF FAQ 는 파이프·명령행 인자를 "보통 별개 프로그램 사이의
> 통신 수단"으로 설명하며, UPCON 은 내부 자료구조를 공유하지 않고 별도 프로세스로만 호출한다.
> 다만 FSF 는 이것이 "궁극적으로 판사가 결정할 법적 문제"라고 명시한다. **단정하지 않는다.**

---

## 2. 동봉 AI 모델 (models/)

| 파일 | 출처 | 상태 |
|---|---|---|
| `realesr-general-x4v3-s2.{param,bin}` | 공식 `realesr-general-x4v3.pth` 를 `scripts/convert_compact_to_ncnn.py` 로 형식 변환 (값 동일) | 아래 참조 |
| `realesr-general-wdn-x4v3-s2.{param,bin}` | 공식 `realesr-general-wdn-x4v3.pth` 변환 | 아래 참조 |
| `realesr-general-dn05-x4v3-s2.{param,bin}` | 위 둘을 dni 블렌딩(denoise 0.5, 공식 기본값) | 아래 참조 |
| `realesr-animevideov3-x{2,3,4}.{param,bin}` | 공식 릴리스 파일 그대로 | 아래 참조 |

### Real-ESRGAN 가중치 라이선스 — **미해결 (unresolved)**

구분해서 확인한 결과:

| 항목 | 확인 결과 | 상태 |
|---|---|---|
| 저장소(코드) 라이선스 | **BSD-3-Clause** | 확실 |
| 가중치 배포 위치 | 같은 저장소의 GitHub Release 자산 (`v0.2.5.0`) | 확실 |
| **가중치 자체의 명시적 라이선스** | README·릴리스 노트 어디에도 가중치 전용 라이선스 문서가 **없음** | **불명확** |
| **학습 데이터 라이선스** | DIV2K 공식 페이지: *"This dataset is made available for academic research purpose only."* | **불명확 / 위험** |

- **"코드가 BSD-3 이므로 가중치도 BSD-3" 이라고 가정하지 않는다.** 가중치에 저장소 라이선스가
  자동 적용되는지는 법적으로 확립되어 있지 않다.
- 학습 데이터(DIV2K 등)에 **연구 목적 한정** 조항이 있고, 그 조건이 가중치에 미치는 효력도
  확립되어 있지 않다. 이는 공개 초해상도 모델 대부분에 공통된 회색 지대다.
- 업계 관행상 다수 상용/오픈소스 제품이 동일 가중치를 재배포·상업 이용하고 있으나,
  **그 관행이 허가를 만들어 주지는 않는다.**

> **결론: 상업적 사용/재배포가 완전히 허가되었다고 보지 않는다.**
> 유료 강의 수강생에게 배포하기 전에 별도 확인이 필요하며, 확실히 하려면
> 저자(Xintao Wang)에게 상업·교육 목적 배포 사용 확인을 받는 것이 유일한 해소책이다.
> 현재 상태는 **unresolved** 로 유지한다.

---

## 3. 동봉 Python 런타임 및 라이브러리

PyInstaller onedir 번들에 포함되는 실제 배포 의존성.

| 구성요소 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| CPython 런타임 (`python312.dll` 등) | 3.12 | PSF License | PyInstaller 가 동봉 |
| **PySide6 / shiboken6 (Qt 6)** | 6.11.2 | **LGPL-3.0** (또는 GPL-2.0/3.0 선택) | 동적 링크, 고지 동봉 → 요건 충족 |
| keyring | 25.7.0 | MIT | Windows 자격 증명 관리자 접근 |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | keyring Windows 백엔드 |
| httpx / httpcore | 0.28.1 / 1.0.9 | BSD-3-Clause | |
| h11 / anyio / idna | — | MIT / MIT / BSD-3 | httpx 의존 |
| certifi | 2026.7.22 | MPL-2.0 | CA 인증서 번들 |
| fal-client | 1.0.1 | **Apache-2.0** | fal.ai Queue API |
| httpx-sse / asyncstdlib / aiofiles / msgpack / websockets | — | MIT / MIT / Apache-2.0 / Apache-2.0 / BSD-3 | fal-client 의존 |
| nvidia-ml-py (pynvml) | 13.610.43 | BSD | GPU/VRAM 감지 |
| typing_extensions | 4.16.0 | PSF-2.0 | |
| PyInstaller 부트로더 | 6.22.3 | GPL-2.0 + **예외 조항** | 생성된 실행파일에는 GPL 비적용 |

---

## 4. 개발 전용 (배포본 미포함)

| 구성요소 | 용도 | 라이선스 |
|---|---|---|
| PyTorch (CPU) | 공식 `.pth` → ncnn 변환 (`scripts/convert_compact_to_ncnn.py`) | BSD-3 |
| pytest | 테스트 | MIT |
| `packaging/selftest.py` + `selftest.spec` | Portable 검증용 콘솔 빌드 | (UPCON 자체 코드) |

---

## 5. 배포 시 지켜야 할 것

1. `bin/LICENSE-ffmpeg.txt`, `bin/LICENSE-ncnn.txt`, `bin/LICENSE-realesrgan-ncnn-vulkan.txt`,
   `models/LICENSE-Real-ESRGAN.txt`, 이 문서를 배포본에 그대로 포함 — **현재 포함됨**
2. 프로그램 "정보" 화면에 구성요소·라이선스 목록 표시 — **installer 단계 예정**
3. FFmpeg **Corresponding Source 제공 경로 확정** — **installer 단계 예정 (현재 미확정)**
4. Real-ESRGAN 가중치 라이선스 — **unresolved. 배포 전 판단 필요**
5. Real-ESRGAN / ncnn 이름을 UPCON 홍보에 "보증(endorsement)" 형태로 사용하지 않음 (BSD 3항)
