# macOS(Apple Silicon) 동봉 FFmpeg/ffprobe — 출처·라이선스 (STEP MAC-4B, 직접 빌드)

Windows 쪽 FFmpeg compliance 자료(`docs/THIRD_PARTY_NOTICES.md`, `dist-ffmpeg-source/`,
`scripts/build_ffmpeg_source_package.py`)는 이 문서가 **전혀 수정하지 않는다**.

> **이전 버전(STEP MAC-4) 폐기 안내**: `osxexperts.net`의 사전 빌드 바이너리를 조사했으나
> Corresponding Source를 제공할 방법이 없어 production 후보에서 제외했다(`scripts/fetch_binaries_macos.py`
> 삭제함). 이 문서는 STEP MAC-4B에서 "우리가 정확히 아는 소스에서 직접 빌드"하는 방식으로
> 전면 교체됐다 — 아래 내용이 최신이며 유일하게 유효한 production 경로다.

## 1. UPCON이 실제로 쓰는 FFmpeg 기능 (코드 기준 조사)

`upcon/core/ffmpeg.py`, `upcon/providers/fal_bytedance.py`, `upcon/core/probe.py`를 실제로
읽고 명령줄 구성을 하나씩 추적한 결과:

| 기능 | 근거(파일:함수) | 외부 라이브러리 필요? |
|---|---|---|
| H.264 encode(소프트웨어 폴백) | `ffmpeg.py::_video_codec_args` `"-c:v", "libx264"` | **필요 — libx264(GPL)** |
| H.264 encode(하드웨어) | `ffmpeg.py::HW_ENCODERS` (NVENC/AMF/QSV) | Windows 전용, macOS 해당 없음(현재 코드에 macOS HW 인코더 후보 없음) |
| 영상 decode(원본, 임의 코덱) | `ffmpeg.py::start_frame_decoder` — 사용자가 올리는 원본이 H.264만이 아닐 수 있음(폰 촬영 HEVC 등) | **불필요 — FFmpeg 기본 내장 디코더로 충분**(별도 --enable-lib* 없이 h264/hevc/vp9/... 디코드 가능) |
| AAC encode | `ffmpeg.py` 179-186행, `fal_bytedance.py` 351행 `"-c:a", "aac"` | **불필요 — FFmpeg 네이티브 AAC 인코더**(libfdk_aac 아님, GPL/nonfree 아님) |
| audio copy(remux) | 같은 곳 `"-c:a", "copy"` | 불필요(디코드/인코드 자체를 안 함) |
| MP4/MOV mux·demux | 전체 파이프라인의 컨테이너 | 불필요(기본 포함) |
| scale(리사이즈) | `ffmpeg.py::start_encoder`/`run_plain_resize` `-vf scale=...:flags=lanczos` | 불필요(기본 swscale) |
| fps 처리(CFR 고정) | `ffmpeg.py::start_frame_decoder` `-fps_mode cfr -r fps` | 불필요 |
| rawvideo(bgr24)/image2(PNG) 파이프 | 로컬 ncnn 파이프라인(청크 BMP/PNG) — Cloud-only인 macOS 1차 버전에는 로컬 미지원이라 당장은 안 쓰지만, 코드 공유를 위해 확인 | 불필요(기본 포함) |
| decode validation(`-f null -`) | `ffmpeg.py::verify_video_decodable` | 불필요 |
| ffprobe(스트림/오디오/길이 조회) | `probe.py` 전체 | 불필요(ffprobe는 ffmpeg와 같은 빌드에서 나옴) |
| concat/segment | **UPCON 어디에도 없음** — 배치 큐는 파일 단위로 따로 처리하지, 영상을 이어붙이거나 나누지 않는다 | 해당 없음 |

**결론: GPL을 요구하는 요소는 딱 하나, libx264(H.264 소프트웨어 인코드 폴백)뿐이다.** 나머지
전부(광범위한 디코드 포함)는 `--enable-gpl`/`--enable-nonfree` 없는 FFmpeg 기본 configure
만으로 충족된다.

### LGPL만으로 가능한가? (검토 결과 — 이번엔 채택하지 않음)

Apple `VideoToolbox`(하드웨어 H.264 인코더)를 쓰면 libx264 없이 LGPL만으로도 빌드할 수
있다 — `configure`가 `videotoolbox`를 autodetect 하고, Apple Silicon은 전부 VideoToolbox를
갖고 있어 하드웨어 자체는 문제가 안 된다. **하지만**:
- `upcon/core/ffmpeg.py::HW_ENCODERS`는 아직 `h264_videotoolbox`를 인코더 후보로 갖고 있지
  않다 — 이걸 넣으려면 production 코드를 바꿔야 하고, 실제 Mac에서 화질/호환성을 검증하기
  전에는 "화질/호환성을 희생해 억지로 LGPL로 만들지 말라"는 지시에 어긋난다.
- 그래서 이번 STEP은 **libx264(GPL)를 그대로 쓴다** — 이미 Windows에서 검증된, 확실히 동작하는
  경로다. `--enable-videotoolbox`는 켜 두되(비용 없음, Apple SDK 프레임워크만 필요) 코드에서는
  아직 안 쓴다 — 나중에 실제 Mac에서 `h264_videotoolbox`를 검증하고 코드에 후보로 추가하면,
  재빌드 시 GPL 없이 LGPL만으로 전환할 수 있는 여지를 남겨 둔 것뿐이다.

## 2. 소스 고정 (재현 가능, 추측 없음)

### FFmpeg

| 항목 | 값 |
|---|---|
| 버전 | **8.1.2** (`RELEASE` 파일로 직접 확인) — Windows(`scripts/fetch_binaries.py`)와 **동일 버전**, 플랫폼 간 동작 일관성을 위해 최신(예: n8.1.3)이 아니라 이미 Windows에서 검증된 버전을 그대로 골랐다 |
| git tag | `n8.1.2` |
| commit | `38b88335f99e76ed89ff3c93f877fdefce736c13` (tag가 가리키는 커밋, `git ls-remote --tags`로 역참조 확인) |
| source URL | `https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n8.1.2.tar.gz` |
| tarball SHA-256(직접 다운로드해 계산) | `9fd092511605bbebafe095ea6d38d9e40f34d12f7386e1258372df8be0576eb7` |

### x264 (GPL을 유발하는 유일한 서드파티 라이브러리)

x264는 번호가 매겨진 릴리스를 내지 않고 계속 개발되는 프로젝트라, "버전"이 아니라 **커밋 해시로
고정**한다(브랜치 이름이 아니라 정확한 커밋 — 브랜치는 계속 움직이므로).

| 항목 | 값 |
|---|---|
| 저장소 | `https://code.videolan.org/videolan/x264.git` |
| 고정 커밋 | `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` (2026-09-21, `stable` 브랜치 HEAD 시점) |
| 링크 방식 | 정적(`--enable-static`, `libx264.a`) — ffmpeg/ffprobe 실행 파일 안에 그대로 포함되고, 배포 시 별도 dylib을 동봉할 필요가 없다 |

## 3. Configure 플래그 (최소 구성)

`scripts/build_ffmpeg_macos_arm64.sh`가 실행하는 정확한 구성:

```
./configure \
    --prefix=<로컬 설치 경로> \
    --enable-gpl \
    --enable-libx264 \
    --enable-videotoolbox \
    --disable-ffplay \
    --disable-doc \
    --disable-debug \
    --pkg-config-flags="--static" \
    --extra-cflags="-I<x264 include 경로>" \
    --extra-ldflags="-L<x264 lib 경로>"
```

`vpx/aom/theora/vorbis/opus/webp/ass/freetype/fontconfig/vmaf/svtav1/kvazaar/vvenc/openjpeg`
등은 **UPCON이 전혀 쓰지 않아 켜지 않았다**(1절 표 참고) — osxexperts.net 사전 빌드본이
포함했던 방대한 코덱 목록과 달리, 이번 빌드는 실제 사용처가 확인된 것만 켠다.
`--enable-cross-compile` 계열 플래그는 의도적으로 쓰지 않는다 — `macos-15` arm64 러너
자체가 arm64 하드웨어라 **네이티브 빌드**이지 크로스 컴파일이 아니다.

## 4. 결과 라이선스: **GPL** (GPLv2 이후 계열, GPLv3 강제 아님)

- `--enable-gpl` 있음 → GPL.
- `--enable-version3`을 켜지 않았으므로 **GPLv3 강제가 아니다**(FFmpeg의 GPL 컴포넌트는
  기본적으로 "GPL version 2 or later" — Windows(BtbN 빌드, GPLv3로 `THIRD_PARTY_NOTICES.md`에
  기재)와 **플랫폼별로 정확한 GPL 버전이 다르다**는 점을 macOS 쪽 고지 문구도 반영한다.
  `upcon/app/about_dialog.py`는 "GPL"로만 표기한다).
- `--enable-nonfree`를 켜지 않았으므로 nonfree 성분(예: `--enable-libfdk-aac`) 없음.
- GPL을 유발하는 컴포넌트는 x264 하나뿐이므로, GPL 조건(Corresponding Source 제공 등)의
  적용 범위도 이 빌드 전체(ffmpeg+x264 조합)로 명확하다 — 불필요하게 큰 서드파티 조합이
  아니라서 라이선스 검토 범위 자체가 작다.

## 5. Corresponding Source — 재현 가능한 확보 방법

`scripts/build_ffmpeg_macos_arm64.sh`의 5단계("Corresponding Source 패키징")가 빌드와
**같은 실행에서 자동으로** 만든다 — 나중에 따로 준비하는 게 아니라 바이너리를 만든 바로 그
소스를 그 자리에서 그대로 묶는다:

```
dist-ffmpeg-source-macos/
    ffmpeg-n8.1.2-source.tar.gz     GitHub 공식 태그 아카이브 그대로(한 글자도 수정 안 함)
    x264-<commit>-source.tar.gz     x264 공식 저장소 해당 커밋 그대로(git archive, 한 글자도 수정 안 함)
    MANIFEST.txt                    각 파일 SHA-256 + 대응하는 바이너리(bin-macos-arm64/ffmpeg,
                                     ffprobe)의 SHA-256을 함께 기록 — "이 소스가 이 바이너리를
                                     만들었다"를 체크섬으로 연결
```

`.app`/DMG 내부에는 넣지 않는다(지시대로 — 앱 용량에 포함시키지 않고, Windows와 마찬가지로
필요한 사람이 별도로 받을 수 있는 구조로만 남긴다). `dist-ffmpeg-source-macos/`는
`.gitignore`의 `dist-*/` 규칙에 이미 걸려 git에 커밋되지 않는다 — 배포 시 이 폴더를 GitHub
Release 자산 등으로 **별도 첨부**하는 방식을 Windows의 `dist-ffmpeg-source/` zip 첨부와
동일하게 따른다(실제 배포 채널/릴리스 프로세스는 아직 미정 — STEP MAC-5A의 GitHub remote
문제와 함께 나중에 정한다).

## 6. 재현성

- FFmpeg: GitHub 공식 태그(`refs/tags/n8.1.2`)는 태그가 가리키는 커밋이 고정돼 있어
  Windows(BtbN 날짜 태그)와 동등한 수준으로 재현 가능하다.
- x264: 브랜치가 아니라 정확한 커밋 해시로 고정했으므로, `stable` 브랜치가 나중에 앞으로
  움직여도 이 문서에 적힌 커밋을 다시 체크아웃하면 항상 같은 소스가 나온다.
- 두 소스 모두 다운로드 직후 SHA-256을 검증한다(`build_ffmpeg_macos_arm64.sh::verify_sha256`,
  x264는 `git rev-parse HEAD`로 커밋 일치 확인) — osxexperts.net 방식보다 재현성이 명확히
  높다.

## 7. 아직 실제 macOS에서 확인 못한 것 (다음 CI 실행에서 확정)

이 문서와 `scripts/build_ffmpeg_macos_arm64.sh`는 Windows 개발 PC에서 작성·문법 검사만
했다(`bash -n` 통과, 다운로드/체크섬 로직은 실제 파일로 별도 검증). **실제 컴파일 성공 여부,
빌드 소요 시간, 최종 바이너리 크기, `ffmpeg -version` 실제 출력은 macOS 러너가 한 번 돌아야
확정된다** — `.github/workflows/build-macos.yml`이 이 스크립트를 호출하도록 이미 연결해
두었다.
