"""FFmpeg 프로세스 헬퍼: 프레임 디코더(rawvideo 파이프), 인코더(image2pipe → MP4), BMP 쓰기, 출력 파일명."""

from __future__ import annotations

import logging
import os
import struct
import subprocess
from pathlib import Path

from upcon.core.binaries import ffmpeg_path
from upcon.core.errors import UpconError
from upcon.core.probe import VideoInfo

log = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000

# MP4 컨테이너에 그대로 복사 가능한 오디오 코덱
_MP4_AUDIO_COPY_OK = {"aac", "mp3", "ac3", "eac3", "alac", "opus"}


def resolve_output_dir(src: Path, output_dir: Path | None) -> Path:
    """설정된 출력 폴더가 없거나 만들 수 없으면 원본 폴더로 되돌린다 (경고 로그)."""
    if output_dir:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir
        except OSError as e:
            log.warning("output_dir %s 사용 불가 (%s) → 원본 폴더에 저장", output_dir, type(e).__name__)
    return src.parent


def ensure_writable_dir(d: Path) -> None:
    """시작 전에 실제로 파일을 하나 만들어 보고 지운다. 실패하면 사용자용 메시지로 즉시 중단."""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".upcon_write_test_{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        raise UpconError(f"결과를 저장할 폴더에 쓸 수 없습니다: {d}\n다른 저장 위치를 선택하거나 폴더 권한을 확인해 주세요.",
                         f"output dir not writable: {d} ({type(e).__name__}: {e})")


def unique_output_path(src: Path, scale: int, output_dir: Path | None = None) -> Path:
    """scene01.mp4 → scene01_2x.mp4. 이미 있으면 scene01_2x_2.mp4, _3 … 기존 결과를 절대 덮어쓰지 않는다."""
    base_dir = resolve_output_dir(src, output_dir)
    stem = f"{src.stem}_{scale}x"
    cand = base_dir / f"{stem}.mp4"
    n = 2
    while cand.exists():
        cand = base_dir / f"{stem}_{n}.mp4"
        n += 1
    return cand


def fps_fraction(info: VideoInfo) -> str:
    """ffmpeg 에 넘길 프레임레이트 문자열 (원본 유지)."""
    f = info.fps
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(f - num / den) < 1e-3:
            return f"{num}/{den}"
    if abs(f - round(f)) < 1e-6:
        return str(int(round(f)))
    return f"{f:.6f}"


def start_frame_decoder(src: Path, fps: str) -> subprocess.Popen:
    """원본 → BGR24 rawvideo 를 stdout 으로 스트리밍 (CFR, 원본 프레임레이트)."""
    cmd = [str(ffmpeg_path()), "-v", "error", "-nostdin", "-i", str(src),
           "-map", "0:v:0", "-an", "-sn", "-dn",
           "-fps_mode", "cfr", "-r", fps,
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    log.debug("decoder: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=0, creationflags=CREATE_NO_WINDOW)


# 하드웨어 H.264 인코더 후보 (순서대로 실제 테스트 인코딩으로 사용 가능 여부 확인)
HW_ENCODERS = ("h264_nvenc", "h264_amf", "h264_qsv")
_hw_encoder_cache: dict[str, bool] = {}


def encoder_usable(name: str) -> bool:
    """해당 인코더로 256×256 1프레임을 실제로 인코딩해 본다 (드라이버/하드웨어 유무 확인).
    (NVENC 는 최소 프레임 크기 제한이 있어 아주 작은 프레임은 실패한다.)"""
    if name == "libx264":
        return True
    if name in _hw_encoder_cache:
        return _hw_encoder_cache[name]
    cmd = [str(ffmpeg_path()), "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=gray:s=256x256:r=1",
           "-frames:v", "1", "-c:v", name, "-pix_fmt", "yuv420p", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _hw_encoder_cache[name] = ok
    log.info("encoder %s usable=%s", name, ok)
    return ok


def pick_encoder(preference: str = "auto") -> str:
    """'auto' 면 사용 가능한 하드웨어 인코더 → 없으면 libx264."""
    if preference and preference != "auto":
        return preference if encoder_usable(preference) else "libx264"
    for name in HW_ENCODERS:
        if encoder_usable(name):
            return name
    return "libx264"


def _video_codec_args(encoder: str, crf: int, preset: str) -> list[str]:
    # crf 18 (x264) 과 대략 비슷한 화질 목표. 하드웨어 인코더는 품질 모드(CQ/QP) 사용.
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(crf + 1),
                "-b:v", "0", "-profile:v", "high"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf + 2)]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "slower", "-global_quality", str(crf + 2)]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    return ["-c:v", encoder]


def start_encoder(dst: Path, src_for_audio: Path, info: VideoInfo, fps: str,
                  crf: int = 18, preset: str = "medium", encoder: str = "libx264") -> subprocess.Popen:
    """stdin 으로 들어오는 PNG 프레임들을 H.264 MP4 로 인코딩하고 원본 오디오를 붙인다."""
    cmd = [str(ffmpeg_path()), "-v", "error", "-y",
           "-f", "image2pipe", "-framerate", fps, "-vcodec", "png", "-i", "pipe:0"]
    if info.has_audio:
        cmd += ["-i", str(src_for_audio), "-map", "0:v:0", "-map", "1:a:0"]
        if info.audio_codec in _MP4_AUDIO_COPY_OK:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-map", "0:v:0"]
    cmd += _video_codec_args(encoder, crf, preset)
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    log.debug("encoder: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)


def bmp_header(width: int, height: int) -> bytes:
    """24bit top-down BMP 헤더 (rawvideo bgr24 프레임을 그대로 뒤에 붙이면 됨)."""
    row = width * 3
    pad = (4 - row % 4) % 4
    data_size = (row + pad) * height
    file_size = 54 + data_size
    return (b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
            + struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 24, 0, data_size, 2835, 2835, 0, 0))


def write_bmp(path: Path, header: bytes, frame: bytes, width: int, height: int) -> None:
    row = width * 3
    pad = (4 - row % 4) % 4
    with open(path, "wb") as f:
        f.write(header)
        if pad == 0:
            f.write(frame)
        else:
            padding = b"\x00" * pad
            for y in range(height):
                f.write(frame[y * row:(y + 1) * row])
                f.write(padding)


def kill_process(p: subprocess.Popen | None) -> None:
    if p is None or p.poll() is not None:
        return
    try:
        p.kill()
        p.wait(timeout=5)
    except Exception:
        pass


def check_encoder_exit(p: subprocess.Popen, dst: Path) -> None:
    stderr = ""
    try:
        _, err = p.communicate(timeout=600)
        stderr = (err or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        kill_process(p)
        raise UpconError("결과 영상을 저장하는 데 시간이 너무 오래 걸립니다.", "encoder timeout")
    if p.returncode != 0 or not dst.exists():
        raise UpconError("결과 영상을 저장하지 못했습니다.", f"encoder rc={p.returncode}: {stderr[-800:]}")
