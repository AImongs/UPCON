r"""upcon/resources/upcon.png -> upcon/resources/upcon.icns (macOS, STEP MAC-3).

    .\.venv\Scripts\python scripts\make_icns.py

새 디자인을 만들지 않는다 — scripts/make_icon.py(Windows .ico)와 완전히 같은 원본(upcon.png)과
같은 전처리(흰 여백 제거 → 둥근 모서리 투명화 → 작은 크기는 워드마크 없이 심볼만)를 그대로
재사용해, macOS 아이콘도 같은 UPCON 브랜드로 보이게 한다.

이 개발 PC(Windows) 에는 macOS 전용 도구인 `iconutil`이 없어 .iconset -> .icns 변환을 할 수
없다. 대신 ICNS 포맷을 직접 써서 만든다 — 포맷은 공개돼 있고 간단하다:
    header: b'icns' + 파일 전체 길이(4바이트, big-endian)
    반복:   4바이트 OSType 코드 + 4바이트 길이(entry 헤더 8바이트 포함) + PNG 바이트

macOS 10.7+ 는 icp4/ic07/ic08/... 계열 코드에 원시 RGB 대신 PNG를 그대로 넣는 것을 허용한다
(Wikipedia "Apple Icon Image format" 문서 기준) — 즉 `iconutil`이 만드는 것과 바이트 단위로
동일하지는 않아도, macOS 가 읽는 방식으로는 동일한 유효한 .icns 가 나온다. 실제 macOS 에서
Finder/Dock 렌더링을 최종 확인하는 것은 STEP MAC-3 보고서의 "실제 Mac에서 확인할 항목"에
남겨둔다(이 스크립트는 Windows 에서 실행/검증했다).
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import SRC, make_mark_tile, round_corners_transparent, trim_white_border  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "upcon" / "resources" / "upcon.icns"

# (OSType, 픽셀 크기, 48px 이하 심볼 전용 로직을 쓸지 여부는 make_icon.py 와 동일 기준: <=48px)
ENTRIES = [
    ("icp4", 16), ("ic11", 32), ("icp5", 32), ("ic12", 64),
    ("ic07", 128), ("ic13", 256), ("ic08", 256), ("ic14", 512),
    ("ic09", 512), ("ic10", 1024),
]


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_icns(frames: dict[int, Image.Image]) -> bytes:
    body = b""
    for otype, size in ENTRIES:
        data = _png_bytes(frames[size])
        entry_len = 8 + len(data)
        body += otype.encode("ascii") + struct.pack(">I", entry_len) + data
    total_len = 8 + len(body)
    return b"icns" + struct.pack(">I", total_len) + body


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"원본 이미지가 없습니다: {SRC}")

    base = Image.open(SRC)
    print(f"원본        : {base.size[0]}x{base.size[1]} {base.mode}")
    tile = trim_white_border(base)
    print(f"여백 제거 후 : {tile.size[0]}x{tile.size[1]}")
    full = round_corners_transparent(tile)

    sizes = sorted({size for _, size in ENTRIES})
    frames: dict[int, Image.Image] = {}
    for s_ in sizes:
        if s_ <= 48:
            frames[s_] = make_mark_tile(tile, s_, pad_ratio=0.12)
            kind = "심볼만"
        else:
            frames[s_] = full.resize((s_, s_), Image.LANCZOS)
            kind = "전체 로크업"
        print(f"  {s_:4d}px : {kind}")

    data = build_icns(frames)
    DST.write_bytes(data)
    print()
    print(f"생성: {DST}  ({DST.stat().st_size:,} bytes, {len(ENTRIES)} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
