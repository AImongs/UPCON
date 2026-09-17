"""ffprobe 로 영상 정보를 읽는다."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from upcon import platform as _plat
from upcon.core.binaries import ffprobe_path
from upcon.core.constants import SUPPORTED_EXTENSIONS, OutputMode
from upcon.core.errors import ProbeError, UnsupportedFileError
from upcon.core.resolution import target_size

log = logging.getLogger(__name__)

# 콘솔 창이 뜨지 않도록 (Windows 전용, 다른 플랫폼에서는 0 — STEP MAC-1)
_CREATE_NO_WINDOW = _plat.NO_WINDOW_FLAGS


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration_sec: float
    size_bytes: int
    video_codec: str
    has_audio: bool
    audio_codec: str = ""
    nb_frames: int = 0          # 컨테이너가 알려주지 않으면 fps*duration 추정값
    frames_estimated: bool = False

    # ---- 표시용 포맷 ----
    @property
    def resolution_text(self) -> str:
        return f"{self.width} × {self.height}"

    @property
    def fps_text(self) -> str:
        return f"{self.fps:.2f}".rstrip("0").rstrip(".") + " fps"

    @property
    def duration_text(self) -> str:
        total = int(round(self.duration_sec))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @property
    def size_text(self) -> str:
        return format_bytes(self.size_bytes)

    def upscaled_resolution_text(self, scale: int) -> str:
        return f"{self.width * scale} × {self.height * scale}"

    def target_resolution_text(self, mode: OutputMode) -> str:
        """출력 해상도 모드(2×/1080p/4K) 기준 최종 크기. 큐 표의 '출력' 열, 처리 전 안내에 쓴다."""
        w, h = target_size(mode, self.width, self.height)
        return f"{w} × {h}"


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.0f} {u}" if u in ("B", "KB") else f"{v:.1f} {u}"
        v /= 1024
    return f"{n} B"


def _parse_fps(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key)
        if val and val not in ("0/0", "0"):
            try:
                f = float(Fraction(val))
                if f > 0:
                    return f
            except (ValueError, ZeroDivisionError):
                continue
    return 0.0


def probe_video(path: Path) -> VideoInfo:
    path = Path(path)
    if not path.exists():
        raise UnsupportedFileError("파일을 찾을 수 없습니다.", f"missing: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            "지원하지 않는 파일 형식입니다. MP4, MOV, MKV 등 영상 파일을 선택해 주세요.",
            f"unsupported extension: {path.suffix}",
        )

    cmd = [
        str(ffprobe_path()), "-v", "error",
        "-print_format", "json", "-show_format", "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise ProbeError("영상 정보를 읽는 데 시간이 너무 오래 걸립니다.", f"ffprobe timeout: {path}")
    except OSError as e:
        raise ProbeError("영상 분석 도구를 실행할 수 없습니다.", f"ffprobe exec error: {e}")

    if proc.returncode != 0:
        raise ProbeError(
            "영상 파일을 읽을 수 없습니다. 파일이 손상되었거나 지원하지 않는 형식입니다.",
            f"ffprobe rc={proc.returncode}: {proc.stderr.strip()[:500]}",
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError("영상 정보를 해석할 수 없습니다.", f"ffprobe json error: {e}")

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0), None)
    if video is None:
        raise UnsupportedFileError("이 파일에는 영상 트랙이 없습니다.", f"no video stream: {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ProbeError("영상 해상도를 확인할 수 없습니다.", f"invalid size: {width}x{height}")

    fps = _parse_fps(video)
    duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    size_bytes = int(fmt.get("size") or path.stat().st_size)

    nb_frames = int(video.get("nb_frames") or 0)
    estimated = False
    if nb_frames <= 0 and fps > 0 and duration > 0:
        nb_frames = int(round(fps * duration))
        estimated = True

    info = VideoInfo(
        path=path, width=width, height=height, fps=fps, duration_sec=duration,
        size_bytes=size_bytes, video_codec=video.get("codec_name", ""),
        has_audio=audio is not None, audio_codec=(audio or {}).get("codec_name", ""),
        nb_frames=nb_frames, frames_estimated=estimated,
    )
    log.info("probe: %s %sx%s %.3ffps %.2fs codec=%s audio=%s",
             path.name, width, height, fps, duration, info.video_codec, info.has_audio)
    return info
