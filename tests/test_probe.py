from pathlib import Path

import pytest

from upcon.core.errors import UnsupportedFileError
from upcon.core.probe import format_bytes, probe_video

def test_probe_480p_with_audio(sample_480p):
    info = probe_video(sample_480p)
    assert (info.width, info.height) == (854, 480)
    assert abs(info.fps - 24.0) < 0.01
    assert 4.9 <= info.duration_sec <= 5.1
    assert info.has_audio and info.audio_codec == "aac"
    assert info.video_codec == "h264"
    assert info.nb_frames == 120
    assert info.upscaled_resolution_text(2) == "1708 × 960"
    assert info.duration_text == "00:05"


def test_probe_korean_path_1080p_no_audio(sample_1080p_korean):
    info = probe_video(sample_1080p_korean)
    assert (info.width, info.height) == (1920, 1080)
    assert abs(info.fps - 29.97) < 0.01
    assert info.fps_text == "29.97 fps"
    assert not info.has_audio


def test_unsupported_extension(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(UnsupportedFileError):
        probe_video(f)


def test_corrupt_file(tmp_path):
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"\x00" * 1024)
    with pytest.raises(Exception) as ei:
        probe_video(f)
    assert hasattr(ei.value, "user_message")


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert format_bytes(768197) == "750 KB"
    assert format_bytes(2401251) == "2.3 MB"
