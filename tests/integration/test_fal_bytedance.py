"""실제 fal.ai API 통합 테스트(ByteDance PRO) — 비용이 발생한다. 명시적으로만 실행:

    .venv\\Scripts\\python -m pytest tests/integration/test_fal_bytedance.py -q -m integration --run-cloud

API Key 는 keyring(UPCON/fal_api_key) 또는 환경변수 FAL_KEY 에서 읽으며, 없으면 전부 skip.

주의: 이 파일은 구현 완료 시점(2026-09-20)에 유료 호출 없이 작성만 됐다 — 아직 실행되지 않았다.
fps 클램프가 실제 서버에서도 강제되는지(스키마 minimum=24 가 서버 측에서도 적용되는지)는
이 통합 테스트를 실제로 --run-cloud 로 실행해야만 확인된다.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from upcon.core import credentials, ffmpeg as ff
from upcon.core.config import AppConfig
from upcon.core.jobs import CancelledError, Job, Phase
from upcon.core.probe import probe_video
from upcon.providers.fal_base import FalApi, explain_fal_error
from upcon.providers.fal_bytedance import ENDPOINT, FalByteDanceProvider

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def key():
    k = credentials.get_fal_key()
    if not k:
        pytest.skip("fal API key 없음 (keyring/FAL_KEY)")
    return k


@pytest.fixture
def cfg(tmp_path):
    c = AppConfig.load()
    c.output_dir = str(tmp_path / "out")
    c.temp_dir = str(tmp_path / "tmp")
    (tmp_path / "out").mkdir()
    return c


def _run(cfg, src: Path, tag: str):
    provider = FalByteDanceProvider(cfg)
    info = probe_video(src)
    job = Job(input_path=src, scale=2, info=info)
    phases = []
    t0 = time.time()
    out = provider.upscale(job, lambda p: phases.append(p.phase) if not phases or phases[-1] != p.phase else None, None)
    dt = time.time() - t0
    o = probe_video(out)
    print(f"\n[{tag}] {info.width}x{info.height} {info.fps:.3f}fps {info.duration_sec:.1f}s → "
          f"{o.width}x{o.height} {o.fps:.2f}fps {o.duration_sec:.2f}s audio={o.has_audio} size={o.size_text} "
          f"| {dt:.0f}s | est ${job.estimated_cost_usd} actual {job.actual_cost_usd} | uncertain={job.cost_uncertain} | {job.note}")
    return info, o, out, job, phases, dt


def test_connection_ok(key):
    r = FalApi(key).test_connection(ENDPOINT)
    assert r.ok, r.message
    print(f"\nunit price from API (참고용, tier/fps 미반영): ${r.unit_price_usd} per {r.unit}")


def test_a_default_24fps_source_keeps_24fps_output(key, cfg, tmp_path, sample_480p):
    """가장 흔한 케이스: 24fps 원본 → target_fps=24 그대로 전달, 길이/fps 보존 확인."""
    info, o, out, job, phases, dt = _run(cfg, sample_480p, "A 24fps default")
    assert abs(o.fps - 24.0) < 0.5
    assert abs(o.duration_sec - info.duration_sec) < 0.5
    assert o.has_audio
    assert not job.cost_uncertain
    assert Phase.UPLOAD in phases and Phase.DOWNLOAD in phases and (Phase.QUEUE in phases or Phase.CLOUD in phases)


def test_b_60fps_source(key, cfg, tmp_path, sample_480p):
    """60fps 실측 — 공식 60fps 요율이 실제로도 적용되는지, target_fps=60이 그대로 반영되는지 확인."""
    src = tmp_path / "b_60fps.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p),
                    "-r", "60", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src)], check=True)
    info, o, out, job, phases, dt = _run(cfg, src, "B 60fps")
    assert abs(o.fps - 60.0) < 1.0


def test_c_below_min_fps_clamped_to_24_by_server(key, cfg, tmp_path, sample_480p):
    """15fps 원본 → resolve_target_fps 가 24로 clamp 해 보낸다. 서버가 실제로 24를 강제하는지
    (스키마 minimum=24) 이 테스트로만 확인 가능 — 아직 실행 안 됨."""
    src = tmp_path / "c_15fps.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p),
                    "-r", "15", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src)], check=True)
    info, o, out, job, phases, dt = _run(cfg, src, "C 15fps→24 clamp")
    assert abs(o.fps - 24.0) < 1.0
    assert "24fps로 보정" in job.note


def test_d_no_audio_source(key, cfg, tmp_path, sample_480p):
    src = tmp_path / "d_noaudio.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p), "-t", "3",
                    "-an", "-c:v", "copy", str(src)], check=True)
    info, o, out, job, phases, dt = _run(cfg, src, "D no audio")
    assert not o.has_audio and o.duration_sec > 2.5


def test_e_korean_path(key, cfg, tmp_path, sample_480p):
    d = tmp_path / "한글 폴더"
    d.mkdir()
    src = d / "한글 영상 이름.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p), "-t", "3",
                    "-c", "copy", str(src)], check=True)
    cfg.output_dir = ""
    info, o, out, job, phases, dt = _run(cfg, src, "E korean path")
    assert out == d / "한글 영상 이름_2x.mp4" and o.has_audio


def test_cancel_during_queue(key, cfg, sample_480p):
    src = sample_480p
    provider = FalByteDanceProvider(cfg)
    job = Job(input_path=src, scale=2, info=probe_video(src))

    def cb(p):
        if p.phase in (Phase.QUEUE, Phase.CLOUD):
            job.cancel.cancel()

    with pytest.raises(CancelledError):
        provider.upscale(job, cb, None)
    print(f"\n[cancel] note: {job.note}")
    assert not list(Path(cfg.output_dir).glob("*.mp4"))
