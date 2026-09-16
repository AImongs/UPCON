r"""Inno Setup 위저드 이미지 생성 (upcon.png 기반, 개발용).

    .\.venv\Scripts\python -m pip install "Pillow>=10"     # pyproject 의 [icon] extra
    .\.venv\Scripts\python scripts\make_installer_images.py

새 로고를 만들지 않는다. 확정된 upcon/resources/upcon.png 에서
  - 심볼(U + 화살표)
  - 워드마크("UPCON" + "AI VIDEO UPSCALER")
  - 타일 배경 그라데이션
을 그대로 떼어내 위저드 이미지 규격에 맞춰 재배치한다. 비율을 바꾸거나 늘리지 않는다.

출력 (Inno Setup 은 BMP 만 받는다. WizardStyle=modern 기준 배율별 크기):
  packaging/installer/wizard-large-*.bmp   WizardImageFile      164x314 기준
  packaging/installer/wizard-small-*.bmp   WizardSmallImageFile  55x55 기준
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from make_icon import (SRC, _row_background, _symbol_mask, make_mark_tile,  # noqa: E402
                       round_corners_transparent, trim_white_border)

OUT = ROOT / "packaging" / "installer"
LARGE_SIZES = [(164, 314), (192, 386), (246, 471), (328, 628)]   # 100/125/150/200%
SMALL_SIZES = [(55, 55), (64, 68), (83, 80), (110, 110)]


def _cut(img: Image.Image, top: float, bottom: float) -> Image.Image:
    w, h = img.size
    return img.crop((0, int(h * top), w, int(h * bottom)))


def _fit(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """비율을 유지한 채 상자 안에 맞춘다 (확대/왜곡 없음)."""
    w, h = img.size
    s = min(box_w / w, box_h / h)
    return img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


def build_large(tile: Image.Image, tile_rgba: Image.Image, size: tuple[int, int]) -> Image.Image:
    W, H = size
    # 배경: 타일의 세로 그라데이션을 위저드 비율로 늘린다 (색만 쓰므로 왜곡 문제 없음)
    bg = _row_background(tile.convert("RGB")).resize((W, H), Image.BILINEAR)
    canvas = bg.convert("RGBA")

    rgb = tile.convert("RGB")
    upper = _cut(rgb, 0.0, 0.70)
    mask = _symbol_mask(upper)
    bbox = mask.getbbox()
    sym = upper.crop(bbox).convert("RGBA")
    sym.putalpha(mask.crop(bbox))

    # 워드마크 + 태그라인 블록 (글자를 새로 그리지 않고 원본을 그대로 쓴다)
    #
    # 주의: _symbol_mask 는 '파랑이 밝고 빨강은 어두운' 픽셀만 고른다. 워드마크 "UPCON" 은
    # 흰 글자(R=G=B=255)라서 그 마스크로는 통째로 빠진다. 그래서 밝기 기준으로 따로 딴다.
    # 타일 바깥(둥근 모서리 밖 흰 여백)도 밝으므로, 모서리를 투명 처리한 이미지를 써서
    # alpha 가 살아 있는 영역만 남긴다 (그렇지 않으면 타일 테두리가 유령처럼 따라온다).
    word_rgba = _cut(tile_rgba, 0.70, 0.98)
    lum = word_rgba.convert("L").point(lambda v: 255 if v > 90 else 0)
    inside = word_rgba.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    wmask = ImageChops.multiply(lum, inside)
    wbox = wmask.getbbox()
    if wbox:
        word = word_rgba.crop(wbox).convert("RGBA")
        word.putalpha(wmask.crop(wbox))
    else:
        word = None

    pad = int(W * 0.12)
    sym_fit = _fit(sym, W - 2 * pad, int(H * 0.34))
    word_fit = _fit(word, W - 2 * pad, int(H * 0.16)) if word else None

    block_h = sym_fit.size[1] + (int(H * 0.05) + word_fit.size[1] if word_fit else 0)
    y = int(H * 0.42) - block_h // 2
    canvas.alpha_composite(sym_fit, ((W - sym_fit.size[0]) // 2, y))
    if word_fit:
        y2 = y + sym_fit.size[1] + int(H * 0.05)
        canvas.alpha_composite(word_fit, ((W - word_fit.size[0]) // 2, y2))
    return canvas.convert("RGB")


def main() -> int:
    if not SRC.is_file():
        raise SystemExit(f"원본 이미지가 없습니다: {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)
    tile = trim_white_border(Image.open(SRC))
    tile_rgba = round_corners_transparent(tile)      # 모서리 바깥 = 투명
    print(f"원본 타일: {tile.size[0]}x{tile.size[1]}")

    large, small = [], []
    for (w, h) in LARGE_SIZES:
        p = OUT / f"wizard-large-{w}x{h}.bmp"
        build_large(tile, tile_rgba, (w, h)).save(p, format="BMP")
        large.append(p)
        print(f"  {p.name:28s} {p.stat().st_size:>8,} bytes")
    for (w, h) in SMALL_SIZES:
        p = OUT / f"wizard-small-{w}x{h}.bmp"
        # 작은 이미지는 아이콘과 같은 심볼 타일을 쓴다 (여백 12%)
        img = make_mark_tile(tile, max(w, h), 0.12).convert("RGB")
        if (w, h) != img.size:
            img = img.resize((w, h), Image.LANCZOS)
        img.save(p, format="BMP")
        small.append(p)
        print(f"  {p.name:28s} {p.stat().st_size:>8,} bytes")

    total = sum(p.stat().st_size for p in large + small)
    print(f"\n총 {len(large) + len(small)}개, {total:,} bytes ({total/1048576:.2f} MB)")
    print("upcon.iss 에서 쓸 값:")
    print("  WizardImageFile=" + ",".join("installer\\" + p.name for p in large))
    print("  WizardSmallImageFile=" + ",".join("installer\\" + p.name for p in small))
    return 0


if __name__ == "__main__":
    sys.exit(main())
