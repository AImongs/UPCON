# UPCON 제3자 구성요소 및 라이선스 (STEP 3 기준)

배포본(설치 파일)에 실제로 동봉되는 것과, 개발용으로만 쓰는 것을 구분한다.
"코드 라이선스" 와 "모델 가중치 라이선스" 를 분리해 확인했다.

## 배포본에 동봉되는 구성요소

| 구성요소 | 파일 | 종류 | 라이선스 | 확인 근거 | 판단 |
|---|---|---|---|---|---|
| Real-ESRGAN-ncnn-vulkan (실행파일) | `bin/realesrgan-ncnn-vulkan.exe` | 코드(바이너리) | **MIT** (Copyright 2021 Xintao Wang) | 저장소 LICENSE 파일 (`bin/LICENSE-realesrgan-ncnn-vulkan.txt`) | 문제 없음. 저작권 고지 동봉 |
| ncnn (실행파일에 정적 링크) | (위 exe 내부) | 코드 | **BSD-3-Clause** + 제3자 구성요소(zlib 등, 모두 허용적) | Tencent/ncnn LICENSE.txt (`bin/LICENSE-ncnn.txt`) | 문제 없음. 고지 동봉 |
| Visual C++ OpenMP 런타임 | `bin/vcomp140.dll` | Microsoft 재배포 가능 런타임 | MS Visual Studio 재배포 조건 | 공식 릴리스 zip 에 포함되어 배포됨 | 문제 없음 |
| Real-ESRGAN AnimeVideo v3 모델 | `models/realesr-animevideov3-x*.{param,bin}` | **가중치** | 저장소 LICENSE = BSD-3-Clause. **가중치 전용 라이선스 문서는 없음** | xinntao/Real-ESRGAN LICENSE (`models/LICENSE-Real-ESRGAN.txt`), 릴리스 v0.2.5.0 자산 | 아래 "가중치 라이선스 주의" 참조 |
| Real-ESRGAN General v3 모델 (UPCON 변환본) | `models/realesr-general-*-x4v3-s2.{param,bin}` | **가중치** (공식 `.pth` 를 `scripts/convert_compact_to_ncnn.py` 로 형식만 변환, 값 동일) | 원본과 동일 (BSD-3 저장소 릴리스 자산) | 릴리스 v0.2.5.0 `realesr-general-x4v3.pth`, `realesr-general-wdn-x4v3.pth` | 아래 "가중치 라이선스 주의" 참조 |
| FFmpeg / ffprobe | `bin/ffmpeg.exe`, `bin/ffprobe.exe` (STEP 7 에서 동봉) | 코드(바이너리) | GPL-2.0+ 빌드 또는 LGPL 빌드 | STEP 7 에서 최종 결정 | 별도 프로세스 호출. STEP 7 검토 |
| PySide6 / Qt | Python 패키지 | 코드 | LGPL-3.0 | PyPI 메타데이터 | 동적 링크, 고지 동봉 → 문제 없음 |
| nvidia-ml-py | Python 패키지 | 코드 | BSD-3 | PyPI | 문제 없음 |
| PyInstaller 부트로더 | 실행파일 | 코드 | GPL-2.0 + 예외 조항 | PyInstaller 라이선스 | 생성물에 GPL 비적용 → 문제 없음 |

## 개발용 (배포본 미포함)

| 구성요소 | 용도 | 라이선스 |
|---|---|---|
| PyTorch (CPU) | 공식 .pth → ncnn 변환 (`scripts/convert_compact_to_ncnn.py`) | BSD-3 |
| pytest | 테스트 | MIT |

## 가중치 라이선스 주의 (사용자 판단 필요)

- Real-ESRGAN 저장소의 LICENSE 는 BSD-3-Clause 이며, 모델 가중치(.pth 및 ncnn 변환본)는 그 저장소의 GitHub Release 자산으로 배포된다.
  README 나 릴리스 노트에 **"가중치는 별도 라이선스" 라는 언급은 없다.** 따라서 업계 관행(Upscayl, chaiNNer, Video2X, fal.ai `video-upscaler` 등 다수 상용/오픈소스 서비스가 동일 가중치를 재배포·상업 이용)상 BSD-3 로 취급된다.
- 다만 학습 데이터(DIV2K, Flickr2K, OST)에는 "연구 목적" 문구가 있는 데이터셋이 포함된다. 학습 데이터 조건이 가중치에 미치는 법적 효력은 확립되어 있지 않으며, 이는 사실상 모든 공개 초해상도 모델에 공통된 회색 지대다.
- **결론: "문제없음" 으로 단정하지 않는다.** 코드/실행파일은 명확히 허용적(MIT/BSD). 가중치는 별도 문서가 없어 "BSD-3 저장소 자산" 이라는 근거로 배포하되, 더 확실히 하려면 저자(Xintao Wang)에게 상업/교육 배포 사용 확인 메일을 보내는 것을 권장한다.

## 동봉 시 지켜야 할 것

1. `bin/LICENSE-*.txt`, `models/LICENSE-Real-ESRGAN.txt` 를 설치본에 그대로 포함 (BSD/MIT 의 고지 의무).
2. 프로그램 "정보" 화면에 위 구성요소와 라이선스 목록 표시 (STEP 7).
3. Real-ESRGAN 이름을 UPCON 홍보에 "보증" 형태로 사용하지 않음 (BSD 3항).
