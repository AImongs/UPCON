"""pytest 공통 설정.

- 실제 클라우드 API 테스트(tests/integration)는 --run-cloud 를 줄 때만 실행한다.
- 테스트용 영상은 저장소에 두지 않고 FFmpeg 로 임시 디렉터리에 생성한다(세션 1회).
  덕분에 새로 clone 한 환경에서도 준비 과정 없이 pytest 가 바로 돌아간다.
  FFmpeg/ffprobe 가 없는 환경에서는 영상이 필요한 테스트만 skip 된다.
- 사용자의 실제 설정/로그(%LOCALAPPDATA%/UPCON)와 자격 증명은 건드리지 않는다.
"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# 테스트가 실제 사용자 설정/로그(%LOCALAPPDATA%/UPCON)를 건드리지 않도록 격리
os.environ.setdefault("UPCON_DATA_DIR", tempfile.mkdtemp(prefix="upcon_test_data_"))


def pytest_addoption(parser):
    parser.addoption("--run-cloud", action="store_true", default=False, help="실제 fal.ai API 테스트 실행 (비용 발생)")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 실제 외부 API 호출 (비용 발생)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-cloud"):
        return
    skip = pytest.mark.skip(reason="--run-cloud 옵션 없음 (실제 API/비용 발생 테스트)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ------------------------------------------------------------------ 테스트용 영상
@pytest.fixture(scope="session")
def ffmpeg_bin() -> Path:
    """FFmpeg 실행파일. 없으면 영상이 필요한 테스트를 skip 한다."""
    from upcon.core.binaries import ffmpeg_path, ffprobe_path
    from upcon.core.errors import BinaryNotFoundError
    try:
        ffmpeg = ffmpeg_path()
        ffprobe_path()          # 영상 정보 확인에도 필요하므로 함께 검사
    except BinaryNotFoundError:
        pytest.skip("FFmpeg/ffprobe 없음 — `python scripts/fetch_binaries.py --ffmpeg` 또는 PATH 설치 필요")
    return Path(ffmpeg)


@pytest.fixture(scope="session")
def samples_dir(tmp_path_factory) -> Path:
    """세션 동안만 존재하는 테스트 영상 폴더 (pytest 가 종료 시 정리)."""
    return tmp_path_factory.mktemp("upcon_samples")


def _make(ffmpeg: Path, dst: Path, args: list[str]) -> Path:
    if dst.exists():
        return dst
    subprocess.run([str(ffmpeg), "-v", "error", "-y", *args, str(dst)], check=True)
    return dst


@pytest.fixture(scope="session")
def sample_480p(ffmpeg_bin, samples_dir) -> Path:
    """854×480 / 24fps / 5초 / 120프레임 / AAC 오디오 있음."""
    return _make(ffmpeg_bin, samples_dir / "sample_480p.mp4", [
        "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
    ])


@pytest.fixture(scope="session")
def sample_480p_portrait(ffmpeg_bin, samples_dir) -> Path:
    """480×854 / 24fps / 3초 / AAC 오디오 있음 — 세로 영상(9:16 근사) 출력 해상도 테스트용 (STEP 10)."""
    return _make(ffmpeg_bin, samples_dir / "sample_480p_portrait.mp4", [
        "-f", "lavfi", "-i", "testsrc2=size=480x854:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
    ])


@pytest.fixture(scope="session")
def sample_1080p_korean(ffmpeg_bin, samples_dir) -> Path:
    """1920×1080 / 29.97fps / 3초 / 무음. 한글 파일명·경로 처리 검증용."""
    d = samples_dir / "한글 폴더"
    d.mkdir(exist_ok=True)
    return _make(ffmpeg_bin, d / "한글 테스트_1080p.mp4", [
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30000/1001",
        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
    ])
