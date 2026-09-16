"""실제 fal.ai API 통합 테스트 — 비용이 발생한다. 명시적으로만 실행:

    .venv\\Scripts\\python -m pytest tests/integration -q -m integration --run-cloud

API Key 는 keyring(UPCON/fal_api_key) 또는 환경변수 FAL_KEY 에서 읽으며, 없으면 전부 skip.
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
from upcon.providers.fal_base import FalApi, FalAuthError, explain_fal_error
from upcon.providers.fal_flashvsr import ENDPOINT, FalFlashVSRProvider

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
    provider = FalFlashVSRProvider(cfg)
    info = probe_video(src)
    job = Job(input_path=src, scale=2, info=info)
    phases = []
    t0 = time.time()
    out = provider.upscale(job, lambda p: phases.append(p.phase) if not phases or phases[-1] != p.phase else None, None)
    dt = time.time() - t0
    o = probe_video(out)
    print(f"\n[{tag}] {info.width}x{info.height} {info.duration_sec:.1f}s → {o.width}x{o.height} {o.fps:.2f}fps {o.duration_sec:.2f}s "
          f"audio={o.has_audio} size={o.size_text} | {dt:.0f}s | est ${job.estimated_cost_usd} actual {job.actual_cost_usd} | {job.note}")
    return info, o, out, job, phases, dt


def test_connection_ok(key):
    r = FalApi(key).test_connection(ENDPOINT)
    assert r.ok, r.message
    assert r.unit_price_usd is not None
    print(f"\nunit price from API: ${r.unit_price_usd} per {r.unit}")


def test_invalid_key():
    r = FalApi("00000000-0000-0000-0000-000000000000:0000000000000000000000000000dead").test_connection(ENDPOINT)
    assert not r.ok and "API Key" in r.message


def test_a_480p_10s_with_audio(key, cfg, tmp_path, sample_480p):
    src = tmp_path / "a_480p_10s.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p),
                    "-stream_loop", "1", "-i", str(sample_480p), "-t", "10",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    info, o, out, job, phases, dt = _run(cfg, src, "A 480p 10s audio")
    assert abs(o.width - 1708) <= 32 and abs(o.height - 960) <= 32
    assert o.has_audio
    assert Phase.UPLOAD in phases and Phase.DOWNLOAD in phases and (Phase.QUEUE in phases or Phase.CLOUD in phases)


def test_c_no_audio(key, cfg, tmp_path, sample_480p):
    src = tmp_path / "c_noaudio.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p), "-t", "3",
                    "-an", "-c:v", "copy", str(src)], check=True)
    info, o, out, job, phases, dt = _run(cfg, src, "C no audio")
    assert not o.has_audio and o.duration_sec > 2.5


def test_d_korean_path(key, cfg, tmp_path, sample_480p):
    d = tmp_path / "한글 폴더"
    d.mkdir()
    src = d / "한글 영상 이름.mp4"
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p), "-t", "3",
                    "-c", "copy", str(src)], check=True)
    cfg.output_dir = ""
    info, o, out, job, phases, dt = _run(cfg, src, "D korean path")
    assert out == d / "한글 영상 이름_2x.mp4" and o.has_audio


def test_cancel_during_queue(key, cfg, sample_480p):
    src = sample_480p
    provider = FalFlashVSRProvider(cfg)
    job = Job(input_path=src, scale=2, info=probe_video(src))

    def cb(p):
        if p.phase in (Phase.QUEUE, Phase.CLOUD):
            job.cancel.cancel()

    with pytest.raises(CancelledError):
        provider.upscale(job, cb, None)
    print(f"\n[cancel] note: {job.note}")
    assert job.note
    assert not list(Path(cfg.output_dir).glob("*.mp4"))
