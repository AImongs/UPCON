r"""upcon/resources/upcon.png -> upcon/resources/upcon.ico (Windows 멀티 해상도).

    .\.venv\Scripts\python -m pip install "Pillow>=10"
    .\.venv\Scripts\python scripts\make_icon.py

원본 PNG 는 흰 여백이 있는 RGB 이미지다. 그대로 ICO 로 만들면 어두운 작업 표시줄에서
모서리가 흰색으로 남으므로, 바깥 여백을 잘라내고 둥근 모서리 바깥을 투명으로 만든다.

--mark-small 을 주면 작은 크기(<=48px)에 워드마크를 뺀 심볼만 넣는다.
작은 아이콘에서 "UPCON / AI VIDEO UPSCALER" 글자는 어차피 뭉개져서 읽히지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "upcon" / "resources" / "upcon.png"
DST = ROOT / "upcon" / "resources" / "upcon.ico"
SIZES = (16, 32, 48, 64, 128, 256)
WHITE_TOL = 12          # 흰 여백 판정 허용치


def trim_white_border(im: Image.Image) -> Image.Image:
    """바깥 흰 여백을 잘라낸다."""
    g = im.convert("L")
    w, h = g.size
    px = g.load()

    def row_white(y):
        return all(px[x, y] >= 255 - WHITE_TOL for x in range(0, w, max(1, w // 64)))

    def col_white(x):
        return all(px[x, y] >= 255 - WHITE_TOL for y in range(0, h, max(1, h // 64)))

    top = 0
    while top < h - 1 and row_white(top):
        top += 1
    bottom = h - 1
    while bottom > top and row_white(bottom):
        bottom -= 1
    left = 0
    while left < w - 1 and col_white(left):
        left += 1
    right = w - 1
    while right > left and col_white(right):
        right -= 1
    return im.crop((left, top, right + 1, bottom + 1))


def round_corners_transparent(im: Image.Image) -> Image.Image:
    """네 모서리의 흰 배경을 투명으로 만든다 (둥근 사각형 바깥)."""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    seen = set()
    stack = []
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        stack.append(corner)
    while stack:
        x, y = stack.pop()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
            continue
        seen.add((x, y))
        r, g, b, a = px[x, y]
        if r < 255 - WHITE_TOL or g < 255 - WHITE_TOL or b < 255 - WHITE_TOL:
            continue            # 아이콘 본체 도달
        px[x, y] = (r, g, b, 0)
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def _symbol_mask(rgb: Image.Image) -> Image.Image:
    """어두운 남색 타일 배경에서 밝은 파란 심볼만 골라낸다.

    실측값:
      배경(남색)  R 4~5    B 22~48
      심볼(하늘색) R 0~87   B 253~254
      타일 바깥    R 255    B 255   <- 둥근 모서리 바깥의 흰 여백

    파랑 채널만 보면 **흰 여백도 B=255 라서 심볼로 잡힌다.** 실제로 그 때문에
    작은 아이콘 위아래에 흰 띠가 생겼다. 그래서 '파랑이 밝고 빨강은 어두운' 픽셀만 고른다.
    """
    r, _, b = rgb.split()
    blue_ok = b.point(lambda v: 255 if v > 110 else 0)
    not_white = r.point(lambda v: 255 if v < 180 else 0)
    return ImageChops.multiply(blue_ok, not_white)


def _row_background(rgb: Image.Image) -> Image.Image:
    """심볼을 지운 깨끗한 타일 배경(세로 그라데이션)을 만든다.

    주의: 타일 모서리가 둥글어서 위/아래 행의 좌우 '끝' 은 타일 바깥의 흰색이다.
    거기서 색을 뽑으면 배경에 흰 띠가 생기므로, 가로로는 타일 안쪽 띠에서만,
    세로로는 모서리 곡선을 벗어난 안전 구간에서만 표본을 뽑아 위아래로 늘린다.
    """
    w, h = rgb.size
    px = rgb.load()
    x0, x1 = int(w * 0.10), int(w * 0.18)      # 심볼 왼쪽, 타일 안쪽
    y0, y1 = int(h * 0.12), int(h * 0.66)      # 둥근 모서리와 워드마크를 모두 피한다

    band = []
    for y in range(y0, y1):
        row = [px[x, y] for x in range(x0, x1)]
        row = [c for c in row if c[2] <= 110]   # 혹시 걸린 심볼 픽셀 제외
        if not row:
            continue
        row.sort(key=lambda c: c[2])
        band.append(row[len(row) // 2])
    if not band:
        raise SystemExit("타일 배경 표본을 뽑지 못했습니다")

    # 표본 띠를 세로로 늘려 전체 타일 배경을 만든다
    strip = Image.new("RGB", (1, len(band)))
    strip.putdata(band)
    return strip.resize((w, h), Image.BILINEAR)


def make_mark_tile(tile: Image.Image, side: int, pad_ratio: float = 0.12) -> Image.Image:
    """작은 크기용 아트워크: 워드마크/태그라인을 완전히 빼고 심볼만,
    사방에 pad_ratio 만큼 시각적 안전 여백을 둔 둥근 타일.
    """
    rgb = tile.convert("RGB")
    w, h = rgb.size

    # 심볼은 타일 위쪽에 있다. 워드마크(아래 ~30%)는 아예 보지 않는다.
    upper = rgb.crop((0, 0, w, int(h * 0.70)))
    mask = _symbol_mask(upper)
    bbox = mask.getbbox()
    if bbox is None:
        raise SystemExit("심볼을 찾지 못했습니다 (임계값 확인 필요)")

    sym = upper.crop(bbox).convert("RGBA")
    sym.putalpha(mask.crop(bbox))

    # 배경: 심볼이 없는 영역의 색으로 재구성한 세로 그라데이션
    bg = _row_background(rgb).resize((side, side), Image.LANCZOS)
    out = bg.convert("RGBA")

    # 심볼을 안전 여백 안에 맞춰 축소 (가로세로 비율 유지)
    inner = int(round(side * (1 - 2 * pad_ratio)))
    sw, sh = sym.size
    scale = inner / max(sw, sh)
    new = (max(1, int(round(sw * scale))), max(1, int(round(sh * scale))))
    sym = sym.resize(new, Image.LANCZOS)
    out.alpha_composite(sym, ((side - new[0]) // 2, (side - new[1]) // 2))

    # 둥근 모서리 (바깥은 투명)
    radius = max(1, int(side * 0.22))
    rmask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(rmask).rounded_rectangle((0, 0, side - 1, side - 1), radius=radius, fill=255)
    rounded = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    rounded.paste(out, (0, 0), rmask)
    return rounded


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-mark-small", action="store_true",
                    help="작은 크기에도 전체 로크업을 쓴다 (기본은 심볼만)")
    ap.add_argument("--pad", type=float, default=0.12,
                    help="작은 크기 심볼의 사방 안전 여백 비율 (기본 0.12 = 12%%)")
    ap.add_argument("--out", default=str(DST))
    a = ap.parse_args()

    if not SRC.is_file():
        raise SystemExit(f"원본 이미지가 없습니다: {SRC}")

    base = Image.open(SRC)
    print(f"원본        : {base.size[0]}x{base.size[1]} {base.mode}")
    tile = trim_white_border(base)
    print(f"여백 제거 후 : {tile.size[0]}x{tile.size[1]}")
    full = round_corners_transparent(tile)

    frames = []
    for s_ in SIZES:
        if s_ <= 48 and not a.no_mark_small:
            img = make_mark_tile(tile, s_, a.pad)
            kind = f"심볼만 (여백 {a.pad:.0%})"
        else:
            img = full.resize((s_, s_), Image.LANCZOS)
            kind = "전체 로크업"
        frames.append(img)
        print(f"  {s_:3d}px : {kind}")

    out = Path(a.out)
    frames[-1].save(out, format="ICO", sizes=[(s_, s_) for s_ in SIZES],
                    append_images=frames[:-1])
    print("")
    print(f"생성: {out}  ({out.stat().st_size:,} bytes)")
    with Image.open(out) as ico:
        print("포함된 해상도:", sorted(ico.info.get("sizes", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
