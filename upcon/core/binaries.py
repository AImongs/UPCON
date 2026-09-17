"""동봉 실행파일(ffmpeg/ffprobe/ncnn) 경로 탐색.

우선순위: 1) 앱 동봉 bin/ 폴더  2) 시스템 PATH.
배포본에는 bin/ 이 항상 포함되므로 사용자는 FFmpeg 를 따로 설치할 필요가 없다.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from upcon import platform as _plat
from upcon.core.errors import BinaryNotFoundError
from upcon.core.paths import bundled_bin_dir


@lru_cache(maxsize=None)
def find_binary(name: str) -> Path:
    exe = _plat.bundled_binary_name(name)          # Windows 만 .exe (STEP MAC-1)
    bundled = bundled_bin_dir() / exe
    if bundled.exists():
        return bundled
    found = shutil.which(name)
    if found:
        return Path(found)
    raise BinaryNotFoundError(
        user_message=f"영상 처리 구성요소({name})를 찾을 수 없습니다. 프로그램을 다시 설치해 주세요.",
        detail=f"{exe} not found in {bundled_bin_dir()} or PATH",
    )


def ffprobe_path() -> Path:
    return find_binary("ffprobe")


def ffmpeg_path() -> Path:
    return find_binary("ffmpeg")
