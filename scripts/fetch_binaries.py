"""동봉 실행파일/모델 다운로드 (개발·빌드용).

    python scripts/fetch_binaries.py            # ncnn 실행파일 + 공식 모델 + 라이선스
    python scripts/fetch_binaries.py --ffmpeg   # + FFmpeg/ffprobe (배포 구성)

결과 (bin/):
    ffmpeg.exe, ffprobe.exe + 공유 DLL 7개        FFmpeg n8.1 GPL shared
    realesrgan-ncnn-vulkan.exe, vcomp140.dll      Real-ESRGAN-ncnn-vulkan
    LICENSE-ffmpeg.txt, LICENSE-ncnn.txt, LICENSE-realesrgan-ncnn-vulkan.txt
결과 (models/):
    realesr-animevideov3-x{2,3,4}.{param,bin}, LICENSE-Real-ESRGAN.txt
    (realesr-general-* 는 scripts/convert_compact_to_ncnn.py 변환본이며 git 에 포함됨)

배포 구성 결정 (STEP 7-2)
- **shared 빌드**를 쓴다. static 빌드는 libav* 전체를 ffmpeg.exe 와 ffprobe.exe 에 각각
  정적 링크해 155MB x 2 가 되지만, shared 는 DLL 을 공유해 같은 기능에 용량만 줄어든다
  (bin 317MB -> 196MB). 기능 동일함을 능력 검사로 확인했다.
- **ffplay.exe 는 받지 않는다** (UPCON 미사용, 18.7MB).
- **GPL 빌드**를 쓴다. LGPL 빌드에는 libx264 가 없어 소프트웨어 폴백 인코더를 교체해야 한다.
  GPLv3 의무(Corresponding Source 제공 등)는 docs/THIRD_PARTY_NOTICES.md 참조.
- **날짜 태그로 고정**한다. `latest` 태그는 자산이 계속 교체되어 재현이 불가능하고,
  master 나이틀리는 NVENC API 요구 버전이 드라이버보다 앞서 하드웨어 인코딩이 조용히 죽는다.
  실측: N-126574 는 "Required: 13.1 Found: 13.0" 로 NVENC 실패 -> x264 폴백 (드라이버 581.29).
  n8.1.2 는 같은 드라이버에서 NVENC 정상.
- 받은 zip 은 SHA-256 으로 검증한다 (전송 오류/자산 교체 감지).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN, MODELS = ROOT / "bin", ROOT / "models"

NCNN_ZIP = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
NCNN_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"

# FFmpeg n8.1.2 GPL shared — 불변(날짜) 태그로 고정. STEP 7-1/7-2 에서 이 빌드로 검증했다.
FFMPEG_VERSION = "n8.1.2-53-g1005b294ff"
FFMPEG_ZIP = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-15-13-18/"
    f"ffmpeg-{FFMPEG_VERSION}-win64-gpl-shared-8.1.zip"
)
FFMPEG_SHA256 = "b5959e30cfe756a10d40ed0323e3d16e02be11379a46320bbe635f0e7787f109"

# UPCON 이 실제로 쓰는 것만 (ffplay 제외). ffmpeg.exe/ffprobe.exe 는 자기 폴더에서 DLL 을 찾는다.
FFMPEG_WANTED = (
    "ffmpeg.exe", "ffprobe.exe",
    "avcodec-62.dll", "avdevice-62.dll", "avfilter-11.dll", "avformat-62.dll",
    "avutil-60.dll", "swresample-6.dll", "swscale-9.dll",
)

NCNN_LICENSE = "https://raw.githubusercontent.com/xinntao/Real-ESRGAN-ncnn-vulkan/master/LICENSE"
ESRGAN_LICENSE = "https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/LICENSE"
NCNN_LIB_LICENSE = "https://raw.githubusercontent.com/Tencent/ncnn/master/LICENSE.txt"


def fetch(url: str, sha256: str | None = None) -> bytes:
    print(f"  down {url}")
    with urllib.request.urlopen(url, timeout=600) as r:
        data = r.read()
    if sha256:
        got = hashlib.sha256(data).hexdigest()
        if got != sha256:
            raise SystemExit(
                "체크섬 불일치" + '\\n' +
                f"  파일: {url}" + '\\n' +
                f"  기대: {sha256}" + '\\n' +
                f"  실제: {got}" + '\\n' +
                "다운로드가 손상됐거나 배포 자산이 교체되었습니다. "
                "확인 후 스크립트의 해시를 갱신하세요."
            )
        print(f"  sha256 OK ({len(data) / 1048576:.1f} MB)")
    return data


def fetch_ncnn() -> None:
    BIN.mkdir(exist_ok=True)
    MODELS.mkdir(exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(fetch(NCNN_ZIP, NCNN_SHA256)))
    wanted = {
        "realesrgan-ncnn-vulkan.exe": BIN, "vcomp140.dll": BIN,
        "models/realesr-animevideov3-x2.param": MODELS, "models/realesr-animevideov3-x2.bin": MODELS,
        "models/realesr-animevideov3-x3.param": MODELS, "models/realesr-animevideov3-x3.bin": MODELS,
        "models/realesr-animevideov3-x4.param": MODELS, "models/realesr-animevideov3-x4.bin": MODELS,
    }
    for name, dest in wanted.items():
        out = dest / Path(name).name
        out.write_bytes(z.read(name))
        print(f"  ok   {out.relative_to(ROOT)}")
    (BIN / "LICENSE-realesrgan-ncnn-vulkan.txt").write_bytes(fetch(NCNN_LICENSE))
    (BIN / "LICENSE-ncnn.txt").write_bytes(fetch(NCNN_LIB_LICENSE))
    (MODELS / "LICENSE-Real-ESRGAN.txt").write_bytes(fetch(ESRGAN_LICENSE))


def fetch_ffmpeg() -> None:
    BIN.mkdir(exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(fetch(FFMPEG_ZIP, FFMPEG_SHA256)))
    found: set[str] = set()
    for info in z.infolist():
        name = Path(info.filename).name
        if name in FFMPEG_WANTED:
            (BIN / name).write_bytes(z.read(info))
            found.add(name)
            print(f"  ok   bin/{name}  ({info.file_size / 1048576:.1f} MB)")
        elif name == "LICENSE.txt":
            (BIN / "LICENSE-ffmpeg.txt").write_bytes(z.read(info))
            print("  ok   bin/LICENSE-ffmpeg.txt")
    missing = set(FFMPEG_WANTED) - found
    if missing:
        raise SystemExit(f"FFmpeg zip 에서 다음 파일을 찾지 못했습니다: {sorted(missing)}")
    # 이전 static 빌드에서 남은 파일 정리 (shared 로 바꾸면 ffplay 는 쓰지 않는다)
    stale = BIN / "ffplay.exe"
    if stale.exists():
        stale.unlink()
        print("  del  bin/ffplay.exe (UPCON 미사용)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", action="store_true", help="FFmpeg/ffprobe 도 받는다 (배포 구성)")
    a = ap.parse_args()
    print("[ncnn] realesrgan-ncnn-vulkan + models")
    fetch_ncnn()
    if a.ffmpeg:
        print(f"[ffmpeg] {FFMPEG_VERSION} GPL shared")
        fetch_ffmpeg()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
