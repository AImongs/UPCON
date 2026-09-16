"""로컬 파이프라인 테스트. 실제 GPU 로 짧은 영상을 업스케일한다 (RTX 5060 기준 약 15초)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from upcon.core import ffmpeg as ff
from upcon.core import tempfs
from upcon.core.config import AppConfig
from upcon.core.constants import ProcessMode
from upcon.core.env import GpuInfo, GpuVendor, SystemEnv, detect_system_env
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, Job, Phase
from upcon.core.probe import probe_video
from upcon.core.router import Router
from upcon.providers.local_ncnn import LocalNcnnProvider

@pytest.fixture(scope="module")
def env():
    return detect_system_env()


@pytest.fixture
def cfg(tmp_path):
    c = AppConfig()
    c.output_dir = str(tmp_path / "out")
    c.temp_dir = str(tmp_path / "tmp")
    (tmp_path / "out").mkdir()
    return c


def _gpu_required(env):
    if not env.primary_gpu or not env.primary_gpu.vulkan_available:
        pytest.skip("Vulkan GPU 없음")


# ---------------------------------------------------------------- helpers
def test_unique_output_path(tmp_path):
    src = tmp_path / "scene01.mp4"
    src.write_bytes(b"x")
    p1 = ff.unique_output_path(src, 2)
    assert p1.name == "scene01_2x.mp4"
    p1.write_bytes(b"y")
    assert ff.unique_output_path(src, 2).name == "scene01_2x_2.mp4"
    (tmp_path / "scene01_2x_2.mp4").write_bytes(b"z")
    assert ff.unique_output_path(src, 2).name == "scene01_2x_3.mp4"


def test_fps_fraction(sample_480p, sample_1080p_korean):
    info = probe_video(sample_1080p_korean)
    assert ff.fps_fraction(info) == "30000/1001"
    info2 = probe_video(sample_480p)
    assert ff.fps_fraction(info2) == "24"


def test_bmp_roundtrip(tmp_path):
    w, h = 6, 3  # row=18 bytes → 2 byte padding
    frame = bytes(range(w * h * 3))
    p = tmp_path / "t.bmp"
    ff.write_bmp(p, ff.bmp_header(w, h), frame, w, h)
    data = p.read_bytes()
    assert data[:2] == b"BM" and len(data) == 54 + (18 + 2) * h
    assert data[54:54 + 18] == frame[:18]


def test_disk_plan_and_check(tmp_path, sample_480p):
    info = probe_video(sample_480p)
    plan = tempfs.plan_disk(info, 2, tmp_path, tmp_path / "o.mp4", temp_budget_mb=1500, chunk_min=24, chunk_max=600)
    assert 24 <= plan.chunk_frames <= 120
    tempfs.check_disk(plan, tmp_path / "o.mp4")           # 실제 디스크엔 충분
    plan.temp_free_mb = 100                               # 부족 상황 시뮬레이션
    with pytest.raises(UpconError) as ei:
        tempfs.check_disk(plan, tmp_path / "o.mp4")
    assert "저장 공간이 부족" in ei.value.user_message


# ---------------------------------------------------------------- routing / availability
def test_router_no_gpu_message(cfg):
    provider = LocalNcnnProvider(cfg)
    router = Router(cfg, [provider], [])
    d = router.decide(ProcessMode.AUTO, SystemEnv(gpus=[]), 2)
    assert d.provider is None and "그래픽카드" in d.message
    d2 = router.decide(ProcessMode.LOCAL, SystemEnv(gpus=[GpuInfo(GpuVendor.NVIDIA, "X", 8000, vulkan_available=False)]), 2)
    assert d2.provider is None


def test_vram_requirement_override(cfg):
    cfg.routing.model_requirement_overrides = {"realesr-general-x4v3": {"min_vram_mb": 999999}}
    provider = LocalNcnnProvider(cfg)
    fake = SystemEnv(gpus=[GpuInfo(GpuVendor.NVIDIA, "NVIDIA GeForce RTX 5060", 8151, vulkan_available=True, vulkan_device_index=0)])
    a = provider.check_availability(fake, 2)
    assert not a.ok and "메모리가 부족" in a.reason


def test_availability_real_gpu(cfg, env):
    _gpu_required(env)
    a = LocalNcnnProvider(cfg).check_availability(env, 2)
    assert a.ok, a.detail
    assert "내 PC GPU(" in a.reason


# ---------------------------------------------------------------- real upscale
def test_upscale_480p_with_audio(cfg, env, sample_480p):
    _gpu_required(env)
    src = sample_480p
    before = src.stat().st_size
    provider = LocalNcnnProvider(cfg)
    job = Job(input_path=src, scale=2, info=probe_video(src))
    phases: list[Phase] = []
    percents: list[int] = []

    def cb(p):
        if not phases or phases[-1] != p.phase:
            phases.append(p.phase)
        if p.percent is not None:
            percents.append(p.percent)

    out = provider.upscale(job, cb, env)
    assert out.name == "sample_480p_2x.mp4" and out.parent == Path(cfg.output_dir)
    o = probe_video(out)
    assert (o.width, o.height) == (1708, 960)
    assert abs(o.fps - 24) < 0.01 and abs(o.duration_sec - 5.0) < 0.1
    assert o.has_audio and o.audio_codec == "aac"
    assert src.stat().st_size == before                       # 원본 미변경
    assert phases[:3] == [Phase.ANALYZE, Phase.PREPARE, Phase.UPSCALE] and Phase.AUDIO in phases and Phase.SAVE in phases
    assert percents == sorted(percents) and percents[-1] == 100  # 실제 프레임 기반 단조 증가
    assert not list(Path(cfg.temp_dir).glob("job_*"))          # 임시 폴더 정리


def test_upscale_no_audio_korean_path(cfg, env, tmp_path, sample_480p):
    _gpu_required(env)
    src_dir = tmp_path / "한글 폴더"
    src_dir.mkdir()
    src = src_dir / "무음 테스트.mp4"
    import subprocess
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(sample_480p),
                    "-t", "2", "-an", "-c:v", "copy", str(src)], check=True)
    cfg.output_dir = ""                                        # 원본 옆에 저장
    out = LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2), lambda p: None, env)
    assert out == src_dir / "무음 테스트_2x.mp4"
    o = probe_video(out)
    assert (o.width, o.height) == (1708, 960) and not o.has_audio


def test_cancel_cleans_up(cfg, env, sample_480p):
    _gpu_required(env)
    src = sample_480p
    provider = LocalNcnnProvider(cfg)
    job = Job(input_path=src, scale=2, info=probe_video(src))

    def cancel_when_upscaling(p):
        if p.phase == Phase.UPSCALE and p.frames_done >= 5:
            job.cancel.cancel()

    with pytest.raises(CancelledError):
        provider.upscale(job, cancel_when_upscaling, env)
    assert not list(Path(cfg.temp_dir).glob("job_*"))
    assert not list(Path(cfg.output_dir).glob("*.mp4"))
    assert src.exists()
    # 자식 프로세스가 남지 않았는지 (ncnn/ffmpeg)
    import subprocess
    # tasklist 출력은 시스템 로캘 인코딩(한국어 Windows 는 cp949)이라 UTF-8 강제 환경에서 디코딩이 깨진다.
    # 로캘과 무관하게 동작하도록 바이트로 받아서 검사한다.
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq realesrgan-ncnn-vulkan.exe"], capture_output=True)
    assert b"realesrgan-ncnn-vulkan.exe" not in (r.stdout or b"")


def test_disk_full_detected(cfg, env, monkeypatch, sample_480p):
    _gpu_required(env)
    monkeypatch.setattr(tempfs, "free_disk_mb", lambda p: 50)
    src = sample_480p
    with pytest.raises(UpconError) as ei:
        LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert "공간이 부족" in ei.value.user_message


def test_hw_encoder_failure_falls_back_to_x264(cfg, env, monkeypatch, sample_480p):
    """하드웨어 인코더가 바로 죽어도 x264 로 자동 재시도해 성공해야 한다."""
    _gpu_required(env)
    from upcon.core import ffmpeg as ffm
    real_start = ffm.start_encoder
    calls = []

    def fake_start(dst, src, info, fps, crf=18, preset="medium", encoder="libx264"):
        calls.append(encoder)
        if encoder != "libx264":
            # 존재하지 않는 인코더 → ffmpeg 가 즉시 종료 (인코더 사망 상황 재현)
            return real_start(dst, src, info, fps, crf, preset, "h264_nonexistent")
        return real_start(dst, src, info, fps, crf, preset, encoder)

    monkeypatch.setattr(ffm, "start_encoder", fake_start)
    monkeypatch.setattr(ffm, "pick_encoder", lambda pref="auto": "h264_nvenc")
    src = sample_480p
    out = LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert calls[0] == "h264_nvenc" and calls[-1] == "libx264"
    o = probe_video(out)
    assert (o.width, o.height) == (1708, 960) and o.has_audio


def test_missing_output_dir_falls_back_to_source_folder(tmp_path, caplog):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    ghost = tmp_path / "deleted_temp_dir_that_cannot_be_created" / "sub"
    # 만들 수 있는 폴더면 만들어서 쓰고,
    out = ff.unique_output_path(src, 2, ghost)
    assert out.parent == ghost and ghost.exists()
    # 만들 수 없는 경로(파일을 폴더처럼 지정)면 원본 폴더로 되돌린다
    blocker = tmp_path / "file_not_dir"
    blocker.write_bytes(b"x")
    out2 = ff.unique_output_path(src, 2, blocker / "sub")
    assert out2.parent == src.parent


def test_unwritable_output_dir_fails_fast(cfg, env, tmp_path, sample_480p):
    _gpu_required(env)
    blocker = tmp_path / "file_not_dir"
    blocker.write_bytes(b"x")
    src = sample_480p
    job = Job(input_path=src, scale=2, info=probe_video(src), output_path=blocker / "sub" / "out_2x.mp4")
    import time
    t0 = time.time()
    with pytest.raises(UpconError) as ei:
        LocalNcnnProvider(cfg).upscale(job, lambda p: None, env)
    assert "쓸 수 없습니다" in ei.value.user_message and time.time() - t0 < 5
