"""개발/테스트용 CLI: python -m upcon.cli <video> [--model id] [--out-dir DIR]

UI 없이 로컬 파이프라인을 실행하고 처리 시간·속도·최대 임시 디스크 사용량을 출력한다.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from upcon.core.config import AppConfig
from upcon.core.env import detect_system_env
from upcon.core.jobs import Job, Phase, Progress
from upcon.core.logging_setup import setup_logging
from upcon.core.probe import probe_video
from upcon.core.tempfs import default_temp_root
from upcon.providers.fal_flashvsr import FalFlashVSRProvider
from upcon.providers.local_ncnn import LocalNcnnProvider


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--cancel-after", type=float, default=None, help="N초 후 취소 (테스트용)")
    ap.add_argument("--temp-budget-mb", type=int, default=None)
    ap.add_argument("--cloud", action="store_true", help="fal.ai FlashVSR 로 처리 (비용 발생, 확인 프롬프트 있음)")
    a = ap.parse_args()

    setup_logging(logging.INFO)
    cfg = AppConfig.load()
    if a.model:
        cfg.local_model = a.model
    if a.out_dir:
        cfg.output_dir = str(a.out_dir)
    if a.temp_budget_mb:
        cfg.temp_budget_mb = a.temp_budget_mb

    env = detect_system_env()
    provider = FalFlashVSRProvider(cfg) if a.cloud else LocalNcnnProvider(cfg)
    avail = provider.check_availability(env, a.scale)
    print(f"availability: ok={avail.ok} {avail.reason} [{avail.detail}]")
    if not avail.ok:
        return 2

    info = probe_video(a.video)
    if a.cloud:
        c = provider.cost(info, a.scale)
        print(f"예상 비용: ${c.usd_display:.2f}  ({c.basis_text()})")
        if input("내 fal.ai 계정에 과금됩니다. 계속하려면 y 입력: ").strip().lower() != "y":
            return 3
    job = Job(input_path=a.video, scale=a.scale, info=info)

    peak = {"bytes": 0}
    stop = False
    temp_root = default_temp_root(cfg.temp_dir)

    def watch():
        while not stop:
            peak["bytes"] = max(peak["bytes"], dir_size(temp_root))
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    if a.cancel_after:
        threading.Timer(a.cancel_after, job.cancel.cancel).start()

    last = [""]

    def on_progress(p: Progress) -> None:
        line = f"{p.phase.value} {p.percent if p.percent is not None else '--'}% {p.detail}"
        if line != last[0]:
            last[0] = line
            print(f"\r{line:<70}", end="", flush=True)

    t0 = time.time()
    try:
        out = provider.upscale(job, on_progress, env)
    except Exception as e:
        stop = True
        print(f"\nFAILED: {getattr(e, 'user_message', e)}  [{getattr(e, 'detail', '')}]")
        print(f"peak temp usage: {peak['bytes']/1e6:.0f} MB")
        return 1
    dt = time.time() - t0
    stop = True
    out_info = probe_video(out)
    print(f"\nOK: {out}")
    print(f"src {info.width}x{info.height} {info.fps:.3f}fps {info.duration_sec:.2f}s audio={info.has_audio} "
          f"→ out {out_info.width}x{out_info.height} {out_info.fps:.3f}fps {out_info.duration_sec:.2f}s audio={out_info.has_audio} {out_info.size_text}")
    print(f"time {dt:.1f}s | {info.nb_frames/dt:.2f} fps | {info.duration_sec/dt:.2f}x realtime | peak temp {peak['bytes']/1e6:.0f} MB")
    if a.cloud:
        print(f"예상 ${job.estimated_cost_usd} | 실제 청구 {job.actual_cost_usd} | {job.note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
