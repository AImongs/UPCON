"""'화질 느낌'(덜 쨍한 결과) — 섞기 모델 생성, 설정, 인코더 그레인. 실제 GPU 실행은 맨 아래 한 건(없으면 skip)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from upcon.core import ffmpeg as ff
from upcon.core.config import AppConfig
from upcon.core.constants import DEFAULT_PICTURE_LOOK, PictureLook, parse_picture_look
from upcon.core.model_blend import CACHE_DIR_NAME, blended_model, blended_param_text
from upcon.core.models import LOOKS, get_look, get_model
from upcon.core.paths import bundled_models_dir

MODELS = bundled_models_dir()
BASE = "realesr-general-wdn-x4v3-s2"


def _layers(text: str) -> list[list[str]]:
    return [ln.split() for ln in text.splitlines()[2:] if ln.strip()]


def test_default_look_is_natural_and_parses_safely():
    assert DEFAULT_PICTURE_LOOK is PictureLook.NATURAL
    assert AppConfig().picture_look == "natural"
    assert parse_picture_look("없는값") is PictureLook.NATURAL
    assert get_look("sharp").ai_weight == 1.0 and get_look("sharp").grain == 0


def test_every_look_points_to_a_shipped_model():
    for look, spec in LOOKS.items():
        m = get_model(spec.model_id or AppConfig().local_model)
        base = MODELS / m.file_basename(2)
        assert base.with_suffix(".param").exists(), look
        assert 0 < spec.ai_weight <= 1


@pytest.mark.skipif(not (MODELS / f"{BASE}.param").exists(), reason="모델 파일 없음")
def test_blended_param_is_well_formed():
    src = (MODELS / f"{BASE}.param").read_text(encoding="ascii")
    out = blended_param_text(src, 0.55, 2)
    layers = _layers(out)
    head = out.splitlines()[1].split()
    assert int(head[0]) == len(layers) == len(_layers(src)) + 4
    blobs = {b for p in layers for b in p[4:4 + int(p[2]) + int(p[3])]}
    assert int(head[1]) == len(blobs)
    # 모든 입력 blob 은 앞에서 만들어진 것이어야 한다 + output 은 정확히 한 번 만들어진다
    made: set[str] = set()
    outputs = []
    for p in layers:
        n_in, n_out = int(p[2]), int(p[3])
        assert set(p[4:4 + n_in]) <= made, p
        made.update(p[4 + n_in:4 + n_in + n_out])
        outputs += p[4 + n_in:4 + n_in + n_out]
    assert outputs.count("output") == 1
    assert "2=5.500000e-01" in out and "2=4.500000e-01" in out


def test_blended_param_rejects_unknown_graph():
    with pytest.raises(ValueError):
        blended_param_text("7767517\n1 1\nInput a 0 1 data\n", 0.5, 2)


@pytest.mark.skipif(not (MODELS / f"{BASE}.param").exists(), reason="모델 파일 없음")
def test_blended_model_cache(tmp_path):
    d, name = blended_model(MODELS, BASE, 1.0, 2, tmp_path)
    assert (d, name) == (MODELS, BASE)                       # AI 그대로면 원본
    d, name = blended_model(MODELS, BASE, 0.55, 2, tmp_path)
    assert d == tmp_path / CACHE_DIR_NAME and name == f"{BASE}-ai55"
    assert "models" in str(d)                                # ncnn 실행기가 요구하는 폴더 이름 규칙
    assert (d / f"{name}.bin").stat().st_size == (MODELS / f"{BASE}.bin").stat().st_size
    mtime = (d / f"{name}.param").stat().st_mtime_ns
    blended_model(MODELS, BASE, 0.55, 2, tmp_path)
    assert (d / f"{name}.param").stat().st_mtime_ns == mtime  # 같으면 다시 쓰지 않는다


def test_encoder_grain_filter(monkeypatch, tmp_path):
    seen = {}

    class P:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd

    monkeypatch.setattr(ff.subprocess, "Popen", P)
    monkeypatch.setattr(ff, "ffmpeg_path", lambda: Path("ffmpeg"))
    from upcon.core.probe import VideoInfo
    info = VideoInfo(path=tmp_path / "a.mp4", width=854, height=480, fps=30.0, duration_sec=1.0, nb_frames=30,
                     video_codec="h264", has_audio=False, audio_codec="", size_bytes=1)
    ff.start_encoder(tmp_path / "o.mp4", info.path, info, "30", grain=3)
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert vf == "format=yuv420p,noise=c0s=3:c0f=t+u"
    ff.start_encoder(tmp_path / "o.mp4", info.path, info, "30", resize=(1920, 1080), grain=2)
    vf = seen["cmd"][seen["cmd"].index("-vf") + 1]
    assert vf.startswith("scale=1920:1080:flags=lanczos,") and vf.endswith("noise=c0s=2:c0f=t+u")
    ff.start_encoder(tmp_path / "o.mp4", info.path, info, "30")
    assert "-vf" not in seen["cmd"]                          # 예전 방식은 필터 없음 그대로


def test_real_blended_model_runs_on_gpu(tmp_path, ffmpeg_bin):
    """실제 ncnn 실행기로 섞기 모델을 돌려 2× 결과가 나오는지 (GPU/Vulkan 없으면 skip)."""
    from upcon.core.binaries import find_binary
    from upcon.core.errors import BinaryNotFoundError
    try:
        exe = find_binary("realesrgan-ncnn-vulkan")
    except BinaryNotFoundError:
        pytest.skip("ncnn 실행기 없음")
    src = tmp_path / "in.png"
    subprocess.run([str(ffmpeg_bin), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64", "-frames:v", "1",
                    str(src)], check=True)
    d, name = blended_model(MODELS, BASE, 0.55, 2, tmp_path)
    out = tmp_path / "out.png"
    r = subprocess.run([str(exe), "-i", str(src), "-o", str(out), "-n", name, "-s", "2", "-m", str(d)],
                       capture_output=True, text=True, errors="replace", timeout=120)
    if r.returncode != 0 and "vulkan" in (r.stderr or "").lower():
        pytest.skip("Vulkan GPU 없음")
    assert r.returncode == 0 and out.exists(), r.stderr[-400:]
    import struct
    assert struct.unpack(">II", out.read_bytes()[16:24]) == (192, 128)       # PNG IHDR 가로·세로
