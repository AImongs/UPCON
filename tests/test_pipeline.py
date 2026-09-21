"""로컬 파이프라인 테스트. 실제 GPU 로 짧은 영상을 업스케일한다 (RTX 5060 기준 약 15초)."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

from upcon.core import ffmpeg as ff
from upcon.core import tempfs
from upcon.core.config import AppConfig
from upcon.core.constants import OutputMode, ProcessMode
from upcon.core.env import GpuInfo, GpuVendor, SystemEnv, detect_system_env
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, CancelToken, Job, Phase
from upcon.core.probe import probe_video
from upcon.core.router import Router
from upcon.providers import local_ncnn as ln
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
_WIN_ONLY_LOCAL_NCNN = pytest.mark.skipif(
    sys.platform != "win32",
    reason="이 GPU/VRAM 분기는 로컬 Real-ESRGAN ncnn 바이너리+모델이 실제로 동봉된 Windows 에서만 "
           "도달 가능하다(plat.local_upscale_unsupported_reason() 을 우회해도 macOS/Linux 는 바이너리가 "
           "없어 '구성요소를 찾을 수 없음' 분기로 먼저 빠진다 — LocalNcnn macOS 활성화는 이번 범위 밖).",
)


@_WIN_ONLY_LOCAL_NCNN
def test_router_no_gpu_message(cfg):
    provider = LocalNcnnProvider(cfg)
    router = Router(cfg, [provider], [])
    d = router.decide(ProcessMode.AUTO, SystemEnv(gpus=[]), 2)
    assert d.provider is None and "그래픽카드" in d.message
    d2 = router.decide(ProcessMode.LOCAL, SystemEnv(gpus=[GpuInfo(GpuVendor.NVIDIA, "X", 8000, vulkan_available=False)]), 2)
    assert d2.provider is None


@_WIN_ONLY_LOCAL_NCNN
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

    def fake_start(dst, src, info, fps, crf=18, preset="medium", encoder="libx264", resize=None):
        calls.append(encoder)
        if encoder != "libx264":
            # 존재하지 않는 인코더 → ffmpeg 가 즉시 종료 (인코더 사망 상황 재현)
            return real_start(dst, src, info, fps, crf, preset, "h264_nonexistent", resize)
        return real_start(dst, src, info, fps, crf, preset, encoder, resize)

    monkeypatch.setattr(ffm, "start_encoder", fake_start)
    monkeypatch.setattr(ffm, "pick_encoder", lambda pref="auto": "h264_nvenc")
    src = sample_480p
    out = LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert calls[0] == "h264_nvenc" and calls[-1] == "libx264"
    o = probe_video(out)
    assert (o.width, o.height) == (1708, 960) and o.has_audio


# ---------------------------------------------------------------- STEP 10: 출력 해상도(1080p/4K)
def test_upscale_1080p_landscape(cfg, env, sample_480p):
    """854×480(가로) + 1080p 선택 → AI 2× 후 FFmpeg 로 정확히 1920×1080 까지 맞춘다."""
    _gpu_required(env)
    src = sample_480p
    job = Job(input_path=src, scale=2, output_mode=OutputMode.FHD, info=probe_video(src))
    out = LocalNcnnProvider(cfg).upscale(job, lambda p: None, env)
    o = probe_video(out)
    assert out.name == "sample_480p_1080p.mp4"
    assert (o.width, o.height) == (1920, 1080)
    assert o.has_audio and o.audio_codec == "aac"
    assert abs(o.duration_sec - 5.0) < 0.1


def test_upscale_1080p_portrait(cfg, env, sample_480p_portrait):
    """480×854(세로) + 1080p 선택 → 정확히 1080×1920 (가로/세로 반대로 찌그러지지 않는지 확인)."""
    _gpu_required(env)
    src = sample_480p_portrait
    job = Job(input_path=src, scale=2, output_mode=OutputMode.FHD, info=probe_video(src))
    out = LocalNcnnProvider(cfg).upscale(job, lambda p: None, env)
    o = probe_video(out)
    assert out.name == "sample_480p_portrait_1080p.mp4"
    assert (o.width, o.height) == (1080, 1920)
    assert o.has_audio


def test_upscale_4k_landscape(cfg, env, sample_480p):
    """854×480 + 4K 선택 → 정확히 3840×2160. 짧은 5초 클립이라 '장시간 4K 테스트' 가 아니다."""
    _gpu_required(env)
    src = sample_480p
    job = Job(input_path=src, scale=2, output_mode=OutputMode.UHD, info=probe_video(src))
    out = LocalNcnnProvider(cfg).upscale(job, lambda p: None, env)
    o = probe_video(out)
    assert out.name == "sample_480p_4k.mp4"
    assert (o.width, o.height) == (3840, 2160)


def test_plain_resize_skips_ai_when_source_already_above_target(cfg, env, monkeypatch, sample_1080p_korean):
    """1920×1080 원본 + 1080p 선택 = 이미 목표 해상도 → AI(ncnn) 를 아예 돌리지 않아야 한다."""
    _gpu_required(env)
    provider = LocalNcnnProvider(cfg)
    ai_called = []
    monkeypatch.setattr(provider, "_run_pipeline", lambda *a, **k: ai_called.append(1) or 0)
    src = sample_1080p_korean
    job = Job(input_path=src, scale=2, output_mode=OutputMode.FHD, info=probe_video(src))
    out = provider.upscale(job, lambda p: None, env)
    assert not ai_called, "이미 목표 해상도 이상인데 AI 업스케일(_run_pipeline)이 호출됐다"
    o = probe_video(out)
    assert (o.width, o.height) == (1920, 1080)


def test_plain_resize_function_direct(tmp_path, sample_1080p_korean):
    """ff.run_plain_resize() 자체를 GPU 없이 직접 검증한다 (AI 미사용 리사이즈 경로의 핵심 함수).
    '이미 목표 이상' 정책의 실제 UI 에는 1080p/4K 두 모드뿐이지만, 함수 자체는 임의 크기로
    줄이거나 늘릴 수 있어야 하므로 여기서는 축소(1920x1080 → 960x540)로 검증한다."""
    from upcon.core.jobs import CancelToken
    info = probe_video(sample_1080p_korean)
    out = tmp_path / "small_resize.mp4"
    ff.run_plain_resize(sample_1080p_korean, out, info, 960, 540, crf=23, preset="veryfast",
                        encoder="libx264", cancel=CancelToken(), on_frame=lambda n: None)
    o = probe_video(out)
    assert (o.width, o.height) == (960, 540)
    assert not o.has_audio                 # sample_1080p_korean 은 무음
    assert abs(o.duration_sec - info.duration_sec) < 0.2


def test_plain_resize_preserves_aac_audio_without_gpu(tmp_path, sample_480p):
    """GPU 없이도 확인 가능한 AAC 오디오 보존 경로 (STEP MAC-1: macOS CI 에 GPU 가 없어도
    FFmpeg 인코드/오디오 처리가 검증되도록 — sample_480p 는 AAC 오디오가 있다)."""
    from upcon.core.jobs import CancelToken
    info = probe_video(sample_480p)
    out = tmp_path / "aac_resize.mp4"
    ff.run_plain_resize(sample_480p, out, info, 640, 360, crf=23, preset="veryfast",
                        encoder="libx264", cancel=CancelToken(), on_frame=lambda n: None)
    o = probe_video(out)
    assert (o.width, o.height) == (640, 360)
    assert o.has_audio and o.audio_codec == "aac"
    assert abs(o.duration_sec - info.duration_sec) < 0.2


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


# ---------------------------------------------------------------- HOTFIX: 결과 영상 디코드 검증
# (RTX 4060 Ti 리포트 — "작업은 완료된 것처럼 보이지만 재생하면 소리만 나오고 화면이 안 나온다")
# 원인이 코드에서 확정되지 않아 인코더 설정은 바꾸지 않고, ffprobe(컨테이너 메타데이터)만으로는
# 못 잡는 "태그는 정상인데 실제 비트스트림이 깨진" 결과를 실제 디코드로 잡아내는 안전망을 검증한다.

def test_verify_video_decodable_passes_on_valid_output(sample_480p):
    ff.verify_video_decodable(sample_480p)  # 예외 없이 통과해야 한다


def test_verify_video_decodable_fails_on_garbage_bytes(tmp_path):
    """(D) 깨진/의미없는 바이트 → 컨테이너조차 못 읽으므로 decode 검증도 실패해야 한다."""
    bad = tmp_path / "garbage.mp4"
    bad.write_bytes(b"not a real mp4 file" * 200)
    with pytest.raises(UpconError):
        ff.verify_video_decodable(bad)


def test_verify_video_decodable_fails_on_zero_byte_file(tmp_path):
    """(D) 0바이트 결과 파일."""
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(UpconError):
        ff.verify_video_decodable(empty)


def test_verify_output_rejects_audio_only_result(tmp_path, sample_480p, ffmpeg_bin):
    """(C) 비디오 스트림이 없는 결과(음성만 있는 MP4) → _verify_output 이 실패해야 한다.
    (probe_video 가 '영상 트랙 없음'으로 먼저 걸러낸다 — 이 체크는 이번 HOTFIX 이전부터 있었다.)"""
    import subprocess
    audio_only = tmp_path / "audio_only.mp4"
    subprocess.run([str(ffmpeg_bin), "-v", "error", "-y", "-i", str(sample_480p),
                    "-vn", "-c:a", "aac", str(audio_only)], check=True)
    with pytest.raises(UpconError):
        LocalNcnnProvider._verify_output(audio_only, 854, 480, None)


def test_verify_output_catches_header_ok_but_bitstream_corrupt(tmp_path, sample_480p):
    """헤더/메타데이터(ffprobe 기준)는 정상으로 보이지만 실제 프레임 데이터가 잘려 디코드가
    실패하는 결과를 재현한다 — RTX 4060 Ti 리포트와 같은 '겉보기엔 정상' 패턴.
    +faststart 로 moov 를 앞에 둔 뒤 뒷부분(프레임 데이터) 대부분을 잘라내면, ffprobe 는
    스트림 정보를 여전히 읽지만(container 헤더는 앞쪽에 있으므로) 실제 디코드는 중간에 깨진다."""
    good = tmp_path / "good.mp4"
    info = probe_video(sample_480p)
    ff.run_plain_resize(sample_480p, good, info, 854, 480, crf=23, preset="veryfast",
                        encoder="libx264", cancel=CancelToken(), on_frame=lambda n: None)
    data = good.read_bytes()
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(data[: len(data) // 3])
    out = probe_video(corrupt)                          # 컨테이너 메타데이터는 여전히 읽힌다
    assert out.width > 0 and out.height > 0
    with pytest.raises(UpconError):
        ff.verify_video_decodable(corrupt)               # 하지만 실제 디코드는 실패해야 한다
    with pytest.raises(UpconError):
        LocalNcnnProvider._verify_output(corrupt, out.width, out.height, None)


def test_plain_resize_transcodes_pcm_audio_to_aac(tmp_path, ffmpeg_bin):
    """(G) MOV + PCM 오디오 입력 → 최종 MP4 의 video/audio 가 모두 정상이어야 한다
    (PCM 은 MP4 컨테이너에 그대로 못 넣으므로 AAC 트랜스코드 분기를 타는지 확인)."""
    import subprocess as sp
    src = tmp_path / "pcm_src.mov"
    sp.run([str(ffmpeg_bin), "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest",
            str(src)], check=True)
    info = probe_video(src)
    assert info.audio_codec == "pcm_s16le"
    out = tmp_path / "pcm_out.mp4"
    ff.run_plain_resize(src, out, info, 320, 240, crf=23, preset="veryfast",
                        encoder="libx264", cancel=CancelToken(), on_frame=lambda n: None)
    o = probe_video(out)
    assert o.has_audio and o.audio_codec == "aac"
    ff.verify_video_decodable(out)


# ---------------------------------------------------------------- HOTFIX-2: 진단 PNG 저장
# "진단 launcher 실행 시 UPCON_DIAG_FRAMES 폴더는 생기는데 PNG 가 0장" 리포트 대응.
# info.nb_frames(추정치) 기반 절대 인덱스 계산 대신, 실제 ncnn 청크 결과만 근거로 저장하도록
# local_ncnn._DebugFrameSaver 로 다시 설계했다 — first/last 는 항상 보장되고, middle 은
# 근사 임계값으로 저장된다.

def test_no_debug_env_saves_nothing(cfg, env, monkeypatch, tmp_path, sample_480p):
    """진단 env 없음 → PNG 저장 안 함."""
    _gpu_required(env)
    monkeypatch.delenv("UPCON_DEBUG_SAVE_FRAMES_DIR", raising=False)
    src = sample_480p
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert not list(tmp_path.glob("**/*.png"))


def test_debug_env_saves_at_least_one_frame(cfg, env, monkeypatch, tmp_path, sample_480p):
    """진단 env 있음 → 최소 1장(first) 저장, 최대 3장."""
    _gpu_required(env)
    debug_dir = tmp_path / "diag"
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))
    src = sample_480p
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    saved = sorted(debug_dir.glob("frame_*.png"))
    assert 1 <= len(saved) <= 3
    assert (debug_dir / "frame_first.png").is_file()
    import subprocess
    for p in saved:
        r = subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-i", str(p), "-f", "null", "-"],
                           capture_output=True)
        assert r.returncode == 0 and not r.stderr, f"{p} not a valid decodable PNG: {r.stderr}"


def test_debug_frames_capped_at_three_with_many_chunks(cfg, env, monkeypatch, tmp_path, sample_480p):
    """여러 chunk 로 나뉘어도 최대 3장, first/middle/last 라벨이 각각 한 번씩만 존재."""
    _gpu_required(env)
    debug_dir = tmp_path / "diag_multi"
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))
    cfg.chunk_frames_min = 8
    cfg.chunk_frames_max = 8   # 854x480 24fps 5초 = 120프레임 → 최소 15개 청크로 강제 분할
    src = sample_480p
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    saved = sorted(p.name for p in debug_dir.glob("frame_*.png"))
    assert len(saved) <= 3
    assert saved == sorted(set(saved)), "같은 라벨 파일이 중복 생성되면 안 된다(고정 파일명으로 덮어써야 함)"
    assert "frame_first.png" in saved and "frame_last.png" in saved


def test_debug_frames_short_clip_no_duplicate_crash(cfg, env, monkeypatch, tmp_path, sample_480p_portrait):
    """아주 짧은 영상(총 프레임이 작아 first/middle/last 대상이 겹쳐도) 정상 동작해야 한다."""
    _gpu_required(env)
    debug_dir = tmp_path / "diag_short"
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))
    src = sample_480p_portrait   # 3초 클립, 상대적으로 프레임 수 적음
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    saved = list(debug_dir.glob("frame_*.png"))
    assert 1 <= len(saved) <= 3


def test_debug_frames_korean_path(cfg, env, monkeypatch, tmp_path, sample_480p):
    """진단 폴더 경로 자체가 한글이어도 정상 저장돼야 한다."""
    _gpu_required(env)
    debug_dir = tmp_path / "한글 진단 폴더" / "프레임"
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))
    src = sample_480p
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    saved = list(debug_dir.glob("frame_*.png"))
    assert len(saved) >= 1


def test_debug_dir_preexisting_with_unrelated_file(cfg, env, monkeypatch, tmp_path, sample_480p):
    """진단 폴더가 이미 존재하고(다른 실행의 잔재 등) 무관한 파일이 있어도 정상 동작."""
    _gpu_required(env)
    debug_dir = tmp_path / "diag_existing"
    debug_dir.mkdir(parents=True)
    (debug_dir / "leftover.txt").write_text("previous run", encoding="utf-8")
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))
    src = sample_480p
    LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert (debug_dir / "leftover.txt").is_file(), "기존 무관한 파일을 지우면 안 된다"
    assert list(debug_dir.glob("frame_*.png"))


def test_debug_copy_failure_logs_warning_but_job_still_succeeds(cfg, env, monkeypatch, tmp_path, sample_480p,
                                                                  caplog):
    """진단 PNG copy 가 실패해도(권한/디스크 등) 본 업스케일 작업은 계속 성공해야 하고,
    실패 원인과 '저장된 게 0장'이라는 사실 둘 다 로그에 남아야 한다."""
    _gpu_required(env)
    debug_dir = tmp_path / "diag_fail"
    monkeypatch.setenv("UPCON_DEBUG_SAVE_FRAMES_DIR", str(debug_dir))

    def _boom(*a, **k):
        raise OSError("simulated copy failure (test)")
    monkeypatch.setattr(ln.shutil, "copy2", _boom)

    src = sample_480p
    with caplog.at_level(logging.WARNING, logger="upcon.providers.local_ncnn"):
        out = LocalNcnnProvider(cfg).upscale(Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env)
    assert out.exists()
    o = probe_video(out)
    assert (o.width, o.height) == (1708, 960)
    assert not list(debug_dir.glob("frame_*.png")), "copy 가 매번 실패했으므로 파일이 없어야 한다"
    messages = [r.message for r in caplog.records]
    assert any("diagnostic frame copy failed" in m for m in messages)
    assert any("Diagnostic mode was enabled but no diagnostic frames were saved" in m for m in messages)
