#!/usr/bin/env bash
# macOS(Apple Silicon arm64) FFmpeg/ffprobe 를 "우리가 정확히 아는 소스"에서 직접 빌드한다
# (STEP MAC-4B). osxexperts.net 등 제3자 사전 빌드본은 더 이상 production 후보로 쓰지 않는다 —
# Corresponding Source 를 제공할 수 없기 때문이다(docs/MACOS_FFMPEG_SOURCE.md 참고).
#
# 이 스크립트는 macOS(arm64) 에서만 동작한다 — clang/make 가 필요하고, 이 개발 PC(Windows)에는
# 없다. 실제 실행은 GitHub Actions macos-15(arm64) 러너(.github/workflows/build-macos.yml)가
# 한다. Windows 개발 PC에서는 bash 문법 검사(bash -n)까지만 했다.
#
# 사용법 (macOS 러너에서):
#     bash scripts/build_ffmpeg_macos_arm64.sh
#
# 결과:
#     bin-macos-arm64/ffmpeg, bin-macos-arm64/ffprobe   (arm64, 실행 권한 부여)
#     dist-ffmpeg-source-macos/                          Corresponding Source(FFmpeg + x264 소스)
#
# [기능 조사 결론 — docs/MACOS_FFMPEG_SOURCE.md 2절 전체 근거]
# UPCON 이 실제로 쓰는 것: H.264 encode(하드웨어 없을 때만 libx264 software) / *decode*(광범위,
# 사용자가 올리는 원본은 H.264 만이 아닐 수 있음 — 기본 configure 로 이미 다 됨) / AAC encode
# (FFmpeg 내장 네이티브 인코더 — 외부 라이브러리 불필요, GPL 아님) / audio copy(remux, 디코더도
# 불필요) / MP4·MOV mux/demux(기본 포함) / scale 필터(기본 포함) / rawvideo·image2(PNG) 파이프
# (기본 포함). **GPL 을 요구하는 요소는 딱 하나, libx264(소프트웨어 H.264 인코드 폴백) 뿐이다.**
# 그 외 모든 기능은 --enable-gpl/--enable-nonfree 없는 기본 configure 만으로 충족된다 —
# 그래서 이 스크립트는 "기본 configure + libx264 하나만 추가"로 최소화했다(vpx/aom/theora/
# vorbis/opus/webp/ass/freetype/vmaf/svtav1/kvazaar/vvenc/openjpeg 등은 전부 UPCON 이 쓰지
# 않으므로 켜지 않는다).
#
# VideoToolbox(Apple 하드웨어 인코더/디코더)를 켜면 이론적으로 libx264 없이 LGPL 만으로도
# 빌드가 가능하다 — 단 UPCON 코드(upcon/core/ffmpeg.py::HW_ENCODERS)가 아직 h264_videotoolbox
# 를 인코더 후보로 쓰지 않고(이번 STEP 범위 밖, 실제 Mac에서 검증 전에는 코드를 바꾸지 않는다),
# "화질/호환성을 희생해 억지로 LGPL 로 만들지 말라"는 지시에 따라 이번 빌드는 검증된 libx264
# 경로(GPL)를 그대로 쓴다. --enable-videotoolbox 는 켜 둔다(추가 비용 없음, Apple SDK 표준
# 프레임워크만 필요 — 나중에 하드웨어 인코더를 붙일 때 재빌드 없이 쓸 수 있게 미리 켜 둔다).

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "이 스크립트는 macOS arm64 전용입니다 (현재: $(uname -s) $(uname -m))" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${ROOT}/build-ffmpeg-macos-arm64"
OUT="${ROOT}/bin-macos-arm64"
SRC_OUT="${ROOT}/dist-ffmpeg-source-macos"
JOBS="$(sysctl -n hw.ncpu)"

# ---- 고정된 소스 (docs/MACOS_FFMPEG_SOURCE.md 와 반드시 일치해야 한다) ----
FFMPEG_TAG="n8.1.2"                                                    # = 공식 릴리스 8.1.2, Windows(fetch_binaries.py) 와 같은 버전
FFMPEG_COMMIT="38b88335f99e76ed89ff3c93f877fdefce736c13"               # git tag n8.1.2 가 가리키는 커밋(역참조)
FFMPEG_URL="https://github.com/FFmpeg/FFmpeg/archive/refs/tags/${FFMPEG_TAG}.tar.gz"
FFMPEG_SHA256="9fd092511605bbebafe095ea6d38d9e40f34d12f7386e1258372df8be0576eb7"

X264_REPO="https://code.videolan.org/videolan/x264.git"
X264_COMMIT="b35605ace3ddf7c1a5d67a2eb553f034aef41d55"                 # stable 브랜치 HEAD, 2026-09-21 고정(브랜치가 아니라 커밋 해시로 고정)

verify_sha256() {
    local file="$1" expected="$2" got
    got="$(shasum -a 256 "$file" | awk '{print $1}')"
    if [[ "$got" != "$expected" ]]; then
        echo "체크섬 불일치: $file" >&2
        echo "  기대: $expected" >&2
        echo "  실제: $got" >&2
        exit 1
    fi
    echo "  sha256 OK: $file"
}

rm -rf "$WORK"
mkdir -p "$WORK" "$OUT" "$SRC_OUT"

# ============================================================ 1) FFmpeg 소스
echo "[1/5] FFmpeg ${FFMPEG_TAG} 소스 다운로드"
curl -sL -o "$WORK/ffmpeg-src.tar.gz" "$FFMPEG_URL"
verify_sha256 "$WORK/ffmpeg-src.tar.gz" "$FFMPEG_SHA256"
tar xzf "$WORK/ffmpeg-src.tar.gz" -C "$WORK"
FFMPEG_SRC_DIR="$WORK/FFmpeg-${FFMPEG_TAG}"
test -d "$FFMPEG_SRC_DIR" || { echo "예상한 소스 폴더가 없습니다: $FFMPEG_SRC_DIR" >&2; exit 1; }
ACTUAL_RELEASE="$(cat "$FFMPEG_SRC_DIR/RELEASE" 2>/dev/null || echo '?')"
echo "  RELEASE 파일 내용: $ACTUAL_RELEASE (기대: 8.1.2)"
[[ "$ACTUAL_RELEASE" == "8.1.2" ]] || { echo "RELEASE 버전이 예상과 다릅니다: $ACTUAL_RELEASE" >&2; exit 1; }

# ============================================================ 2) x264 (정적, GPL 트리거 유일 요소)
echo "[2/5] x264 (commit ${X264_COMMIT}) 클론 + 정적 빌드"
X264_SRC_DIR="$WORK/x264"
X264_PREFIX="$WORK/x264-prefix"
git clone --quiet "$X264_REPO" "$X264_SRC_DIR"
git -C "$X264_SRC_DIR" checkout --quiet "$X264_COMMIT"
GOT_COMMIT="$(git -C "$X264_SRC_DIR" rev-parse HEAD)"
[[ "$GOT_COMMIT" == "$X264_COMMIT" ]] || { echo "x264 커밋이 예상과 다릅니다: $GOT_COMMIT" >&2; exit 1; }
(
    cd "$X264_SRC_DIR"
    ./configure --prefix="$X264_PREFIX" --enable-static --disable-cli --disable-opencl
    make -j"$JOBS"
    make install
)
test -f "$X264_PREFIX/lib/libx264.a" || { echo "libx264.a 가 만들어지지 않았습니다" >&2; exit 1; }
echo "  OK: $X264_PREFIX/lib/libx264.a"

# ============================================================ 3) FFmpeg configure + build
echo "[3/5] FFmpeg configure (최소 구성: 기본 + libx264 하나만 추가)"
FFMPEG_PREFIX="$WORK/ffmpeg-prefix"
(
    cd "$FFMPEG_SRC_DIR"
    PKG_CONFIG_PATH="$X264_PREFIX/lib/pkgconfig" ./configure \
        --prefix="$FFMPEG_PREFIX" \
        --enable-gpl \
        --enable-libx264 \
        --enable-videotoolbox \
        --disable-ffplay \
        --disable-doc \
        --disable-debug \
        --pkg-config-flags="--static" \
        --extra-cflags="-I${X264_PREFIX}/include" \
        --extra-ldflags="-L${X264_PREFIX}/lib"
    echo "=== configure 요약 (docs/MACOS_FFMPEG_SOURCE.md 대조용) ==="
    grep -E "^  (License|libx264|videotoolbox)" ffbuild/config.log 2>/dev/null || true
    make -j"$JOBS"
)
FFMPEG_BIN="$FFMPEG_SRC_DIR/ffmpeg"
FFPROBE_BIN="$FFMPEG_SRC_DIR/ffprobe"
test -x "$FFMPEG_BIN" && test -x "$FFPROBE_BIN" || { echo "ffmpeg/ffprobe 빌드 결과가 없습니다" >&2; exit 1; }

# ============================================================ 4) 산출물 배치
echo "[4/5] bin-macos-arm64/ 로 복사"
cp "$FFMPEG_BIN" "$OUT/ffmpeg"
cp "$FFPROBE_BIN" "$OUT/ffprobe"
chmod +x "$OUT/ffmpeg" "$OUT/ffprobe"
file "$OUT/ffmpeg" "$OUT/ffprobe"
"$OUT/ffmpeg" -version | head -3
"$OUT/ffprobe" -version | head -3

# ============================================================ 5) Corresponding Source 패키징
echo "[5/5] dist-ffmpeg-source-macos/ 로 Corresponding Source 패키징"
cp "$WORK/ffmpeg-src.tar.gz" "$SRC_OUT/ffmpeg-${FFMPEG_TAG}-source.tar.gz"
( cd "$X264_SRC_DIR" && git archive --format=tar.gz --output="$SRC_OUT/x264-${X264_COMMIT}-source.tar.gz" HEAD )
{
    echo "UPCON macOS(arm64) 동봉 FFmpeg/ffprobe — Corresponding Source 매니페스트"
    echo "생성 시각(UTC): $(date -u +%FT%TZ)"
    echo ""
    echo "ffmpeg-${FFMPEG_TAG}-source.tar.gz"
    echo "  sha256: $(shasum -a 256 "$SRC_OUT/ffmpeg-${FFMPEG_TAG}-source.tar.gz" | awk '{print $1}')"
    echo "  = FFmpeg 공식 저장소 tag ${FFMPEG_TAG} (commit ${FFMPEG_COMMIT}) 그대로, 한 글자도 수정하지 않음"
    echo ""
    echo "x264-${X264_COMMIT}-source.tar.gz"
    echo "  sha256: $(shasum -a 256 "$SRC_OUT/x264-${X264_COMMIT}-source.tar.gz" | awk '{print $1}')"
    echo "  = x264 공식 저장소(videolan.org) commit ${X264_COMMIT} 그대로, 한 글자도 수정하지 않음"
    echo ""
    echo "빌드된 바이너리:"
    echo "  bin-macos-arm64/ffmpeg   sha256: $(shasum -a 256 "$OUT/ffmpeg" | awk '{print $1}')"
    echo "  bin-macos-arm64/ffprobe  sha256: $(shasum -a 256 "$OUT/ffprobe" | awk '{print $1}')"
} > "$SRC_OUT/MANIFEST.txt"
cat "$SRC_OUT/MANIFEST.txt"

echo "done."
