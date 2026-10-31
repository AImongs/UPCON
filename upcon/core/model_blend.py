"""AI 결과와 부드러운 확대를 섞는 ncnn 모델 변형을 만든다 ('느낌' 설정, core/models.py 의 LOOKS).

원본 그래프의 마지막 출력(output)을 ai 로 바꾸고, 입력을 bicubic 2× 로 늘린 soft 와
    output = ai × w + soft × (1 − w)
로 섞는 레이어 4개를 뒤에 붙인다. 가중치(.bin)는 원본 그대로 복사한다 — 추가 레이어에는 가중치가 없다.
같은 프레임을 그래프 안에서 섞으므로 FFmpeg 에서 두 영상을 맞춰 섞을 때 생기는 프레임 어긋남(잔상)이 없다.

realesrgan-ncnn-vulkan 은 모델 폴더 경로에 'models' 가 들어 있어야 읽는다("unknown model dir type").
그래서 캐시 폴더 이름을 models_blend 로 둔다.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_DIR_NAME = "models_blend"
_TAIL = (
    "Interp                   UpconSoft                1 1 data_soft soft 0=3 1={s:e} 2={s:e}",
    "BinaryOp                 UpconWeightAI            1 1 ai ai_w 0=2 1=1 2={w:e}",
    "BinaryOp                 UpconWeightSoft          1 1 soft soft_w 0=2 1=1 2={v:e}",
    "BinaryOp                 UpconBlend               2 1 ai_w soft_w output 0=0",
)


def blended_param_text(param_text: str, ai_weight: float, scale: int) -> str:
    """원본 .param 텍스트 → 섞기 레이어를 붙인 .param 텍스트."""
    lines = param_text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "7767517":
        raise ValueError("ncnn param 형식이 아닙니다")
    body: list[str] = []
    split_done = output_done = False
    for ln in lines[2:]:
        if not ln.strip():
            continue
        p = ln.split()
        n_in, n_out = int(p[2]), int(p[3])
        ins, outs = p[4:4 + n_in], p[4 + n_in:4 + n_in + n_out]
        if p[0] == "Split" and ins == ["data"]:
            # 입력을 한 갈래 더 나눠 soft 확대에 쓴다
            p[3] = str(n_out + 1)
            p.insert(4 + n_in + n_out, "data_soft")
            ln, split_done = " ".join(p), True
        elif "output" in outs:
            p[4 + n_in + outs.index("output")] = "ai"
            ln, output_done = " ".join(p), True
        body.append(ln)
    if not (split_done and output_done):
        raise ValueError("입력 Split 또는 output 레이어를 찾지 못했습니다")
    body += [t.format(s=float(scale), w=ai_weight, v=1.0 - ai_weight) for t in _TAIL]
    blobs: set[str] = set()
    for ln in body:
        p = ln.split()
        n_in, n_out = int(p[2]), int(p[3])
        blobs.update(p[4:4 + n_in + n_out])
    return f"7767517\n{len(body)} {len(blobs)}\n" + "\n".join(body) + "\n"


def blended_model(models_dir: Path, base_name: str, ai_weight: float, scale: int, cache_root: Path) -> tuple[Path, str]:
    """(모델 폴더, -n 이름). ai_weight 가 1 이면 원본 모델을 그대로 돌려준다."""
    if ai_weight >= 0.999:
        return models_dir, base_name
    name = f"{base_name}-ai{round(ai_weight * 100):02d}"
    out_dir = cache_root / CACHE_DIR_NAME
    param, binf = out_dir / f"{name}.param", out_dir / f"{name}.bin"
    src_param, src_bin = models_dir / f"{base_name}.param", models_dir / f"{base_name}.bin"
    text = blended_param_text(src_param.read_text(encoding="ascii"), ai_weight, scale)
    fresh = (param.exists() and binf.exists() and param.read_text(encoding="ascii") == text
             and binf.stat().st_size == src_bin.stat().st_size)
    if not fresh:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = param.with_suffix(".param.tmp")
        tmp.write_text(text, encoding="ascii")
        shutil.copyfile(src_bin, binf)
        tmp.replace(param)
        log.info("blended model written: %s (ai_weight=%.2f)", name, ai_weight)
    return out_dir, name
