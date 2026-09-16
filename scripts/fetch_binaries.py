"""동봉 실행파일/모델 다운로드 (개발·빌드용).

    python scripts/fetch_binaries.py            # ncnn 실행파일 + 공식 모델 + 라이선스
    python scripts/fetch_binaries.py --ffmpeg   # + FFmpeg (BtbN GPL 빌드, STEP 7 에서 최종 결정)

결과:
    bin/realesrgan-ncnn-vulkan.exe, bin/vcomp140.dll, bin/LICENSE-realesrgan-ncnn-vulkan.txt
    models/realesr-animevideov3-x2.{param,bin}, models/LICENSE-Real-ESRGAN.txt
    (models/realesr-general-*  는 scripts/convert_compact_to_ncnn.py 로 변환한 파일이며 git 에 포함됨)
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN, MODELS = ROOT / "bin", ROOT / "models"

NCNN_ZIP = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip"
NCNN_LICENSE = "https://raw.githubusercontent.com/xinntao/Real-ESRGAN-ncnn-vulkan/master/LICENSE"
ESRGAN_LICENSE = "https://raw.githubusercontent.com/xinntao/Real-ESRGAN/master/LICENSE"
NCNN_LIB_LICENSE = "https://raw.githubusercontent.com/Tencent/ncnn/master/LICENSE.txt"
FFMPEG_ZIP = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"


def fetch(url: str) -> bytes:
    print(f"  ↓ {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def fetch_ncnn() -> None:
    BIN.mkdir(exist_ok=True)
    MODELS.mkdir(exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(fetch(NCNN_ZIP)))
    wanted = {
        "realesrgan-ncnn-vulkan.exe": BIN, "vcomp140.dll": BIN,
        "models/realesr-animevideov3-x2.param": MODELS, "models/realesr-animevideov3-x2.bin": MODELS,
        "models/realesr-animevideov3-x3.param": MODELS, "models/realesr-animevideov3-x3.bin": MODELS,
        "models/realesr-animevideov3-x4.param": MODELS, "models/realesr-animevideov3-x4.bin": MODELS,
    }
    for name, dest in wanted.items():
        (dest / Path(name).name).write_bytes(z.read(name))
        print(f"  → {dest / Path(name).name}")
    (BIN / "LICENSE-realesrgan-ncnn-vulkan.txt").write_bytes(fetch(NCNN_LICENSE))
    (BIN / "LICENSE-ncnn.txt").write_bytes(fetch(NCNN_LIB_LICENSE))
    (MODELS / "LICENSE-Real-ESRGAN.txt").write_bytes(fetch(ESRGAN_LICENSE))


def fetch_ffmpeg() -> None:
    BIN.mkdir(exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(fetch(FFMPEG_ZIP)))
    for info in z.infolist():
        n = Path(info.filename).name
        if n in ("ffmpeg.exe", "ffprobe.exe", "LICENSE.txt"):
            out = BIN / ("LICENSE-ffmpeg.txt" if n == "LICENSE.txt" else n)
            out.write_bytes(z.read(info))
            print(f"  → {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", action="store_true")
    a = ap.parse_args()
    print("[ncnn] realesrgan-ncnn-vulkan + models")
    fetch_ncnn()
    if a.ffmpeg:
        print("[ffmpeg]")
        fetch_ffmpeg()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
