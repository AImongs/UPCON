"""Real-ESRGAN SRVGGNetCompact (.pth) → ncnn (.param/.bin) 변환기 (개발용 도구).

공식 realesr-general-x4v3.pth / realesr-general-wdn-x4v3.pth (BSD-3-Clause) 를
realesrgan-ncnn-vulkan.exe 가 읽을 수 있는 형식으로 바꾼다.
출력 그래프는 공식 realesr-animevideov3-x2.param 과 동일한 구조
(4× 네트워크 → nearest 4× 잔차 합 → 그래프 내 bicubic ½ 축소) 이므로 `-s 2` 로 실행한다.

사용법 (torch CPU 만 있으면 됨):
    python scripts/convert_compact_to_ncnn.py realesr-general-x4v3.pth out_dir --name realesr-general-x4v3-s2 --scale 2
    python scripts/convert_compact_to_ncnn.py a.pth --blend b.pth --alpha 0.5 ...   # dni 블렌딩 (denoise 강도)
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import torch


def load_params(path: Path) -> dict[str, torch.Tensor]:
    sd = torch.load(path, map_location="cpu", weights_only=True)
    return sd.get("params_ema") or sd.get("params") or sd


def build(params: dict[str, torch.Tensor], name: str, out_scale: int, out_dir: Path) -> None:
    # body.0 conv, body.1 prelu, ..., body.N conv(64→48)
    conv_idx = sorted({int(k.split(".")[1]) for k in params if k.endswith(".weight") and params[k].dim() == 4})
    prelu_idx = sorted({int(k.split(".")[1]) for k in params if params[k].dim() == 1 and k.endswith(".weight")})
    last = conv_idx[-1]
    net_scale = int(round((params[f"body.{last}.weight"].shape[0] / 3) ** 0.5))
    assert net_scale in (2, 3, 4), net_scale
    assert out_scale <= net_scale

    lines: list[str] = []
    bin_parts: list[bytes] = []
    nblob = 0

    lines.append("Input                    input.1                  0 1 data")
    lines.append("Split                    splitncnn_input0         1 2 data data_res data_body")
    nblob += 3
    prev = "data_body"
    for i in range(0, last + 1):
        if i in conv_idx:
            w = params[f"body.{i}.weight"].float().contiguous()
            b = params[f"body.{i}.bias"].float().contiguous()
            out_ch, in_ch, kh, kw = w.shape
            blob = f"c{i}"
            lines.append(f"Convolution              Conv_{i:<18} 1 1 {prev} {blob} 0={out_ch} 1={kh} 4={kh // 2} 5=1 6={w.numel()}")
            bin_parts.append(struct.pack("<i", 0))          # flag: fp32
            bin_parts.append(w.numpy().tobytes())
            bin_parts.append(b.numpy().tobytes())
            prev, nblob = blob, nblob + 1
        elif i in prelu_idx:
            s = params[f"body.{i}.weight"].float().contiguous()
            blob = f"p{i}"
            lines.append(f"PReLU                    PRelu_{i:<17} 1 1 {prev} {blob} 0={s.numel()}")
            bin_parts.append(s.numpy().tobytes())
            prev, nblob = blob, nblob + 1
        else:
            raise RuntimeError(f"unexpected layer index {i}")

    lines.append(f"PixelShuffle             DepthToSpace             1 1 {prev} ps 0={net_scale}")
    lines.append(f"Interp                   ResizeNearest            1 1 data_res up 0=1 1={float(net_scale):e} 2={float(net_scale):e}")
    final = "output" if out_scale == net_scale else "sum"
    lines.append(f"BinaryOp                 Add                      2 1 ps up {final}")
    nblob += 3
    if out_scale != net_scale:
        f = out_scale / net_scale
        lines.append(f"Interp                   ResizeBicubic            1 1 sum output 0=3 1={f:e} 2={f:e}")
        nblob += 1

    header = f"7767517\n{len(lines)} {nblob}\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.param").write_text(header + "\n".join(lines) + "\n", encoding="ascii")
    (out_dir / f"{name}.bin").write_bytes(b"".join(bin_parts))
    print(f"wrote {out_dir / name}.param/.bin  layers={len(lines)} blobs={nblob} net_scale={net_scale} out_scale={out_scale}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pth", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--scale", type=int, default=2, help="출력 배율 (네트워크 배율 이하)")
    ap.add_argument("--blend", type=Path, help="dni 블렌딩할 두 번째 .pth (예: wdn 모델)")
    ap.add_argument("--alpha", type=float, default=0.5, help="첫 모델 가중치 비율 (denoise strength)")
    a = ap.parse_args()

    params = load_params(a.pth)
    if a.blend:
        other = load_params(a.blend)
        params = {k: a.alpha * params[k] + (1 - a.alpha) * other[k] for k in params}
    build(params, a.name, a.scale, a.out_dir)


if __name__ == "__main__":
    main()
