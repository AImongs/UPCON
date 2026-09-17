"""FFmpeg 프로세스 헬퍼: 프레임 디코더(rawvideo 파이프), 인코더(image2pipe → MP4), BMP 쓰기, 출력 파일명."""

from __future__ import annotations

import logging
import os
import struct
import subprocess
from pathlib import Path

from upcon import platform as _plat
from upcon.core.binaries import ffmpeg_path
from upcon.core.constants import OutputMode, output_suffix
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, CancelToken
from upcon.core.probe import VideoInfo

log = logging.getLogger(__name__)
# Windows 전용 콘솔창 숨김 플래그. 다른 플랫폼에서 0 이 아닌 creationflags 를 넘기면
# subprocess 가 곧바로 ValueError 를 던진다 — 반드시 upcon.platform.NO_WINDOW_FLAGS 를 거쳐야 한다
# (STEP MAC-1). 이름은 기존 호출부(local_ncnn.py 등)와의 호환을 위해 그대로 둔다.
CREATE_NO_WINDOW = _plat.NO_WINDOW_FLAGS

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


def _unique_with_stem(base_dir: Path, stem: str) -> Path:
    """stem.mp4 이 이미 있으면 stem_2.mp4, _3 … 기존 결과를 절대 덮어쓰지 않는다."""
    cand = base_dir / f"{stem}.mp4"
    n = 2
    while cand.exists():
        cand = base_dir / f"{stem}_{n}.mp4"
        n += 1
    return cand


def unique_output_path(src: Path, scale: int, output_dir: Path | None = None) -> Path:
    """scene01.mp4 → scene01_2x.mp4. (클라우드 Provider 전용 — 배율만 다루므로 그대로 유지한다.
    로컬 Provider 의 출력 해상도 모드 기반 이름은 unique_output_path_for_mode 를 쓴다.)"""
    base_dir = resolve_output_dir(src, output_dir)
    return _unique_with_stem(base_dir, f"{src.stem}_{scale}x")


def unique_output_path_for_mode(src: Path, mode: OutputMode, output_dir: Path | None = None) -> Path:
    """scene01.mp4 → scene01_2x.mp4 / scene01_1080p.mp4 / scene01_4k.mp4. (로컬 Provider 전용)"""
    base_dir = resolve_output_dir(src, output_dir)
    return _unique_with_stem(base_dir, f"{src.stem}{output_suffix(mode)}")


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
                  crf: int = 18, preset: str = "medium", encoder: str = "libx264",
                  resize: tuple[int, int] | None = None) -> subprocess.Popen:
    """stdin 으로 들어오는 PNG 프레임들을 H.264 MP4 로 인코딩하고 원본 오디오를 붙인다.

    resize: (width, height) 가 주어지고 들어오는 프레임 크기와 다르면(1080p/4K 출력 모드)
    Lanczos 로 최종 크기를 맞춘다. None 이면 들어오는 프레임 크기를 그대로 쓴다(기존 2× 동작).
    새 외부 라이브러리 없이 FFmpeg 에 이미 있는 표준 swscale 필터만 쓴다."""
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
    if resize:
        cmd += ["-vf", f"scale={resize[0]}:{resize[1]}:flags=lanczos"]
    cmd += _video_codec_args(encoder, crf, preset)
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)]
    log.debug("encoder: %s", " ".join(cmd))
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)


def run_plain_resize(src: Path, dst: Path, info: VideoInfo, target_w: int, target_h: int,
                     crf: int, preset: str, encoder: str, cancel: CancelToken,
                     on_frame) -> None:
    """AI 업스케일 없이 FFmpeg 만으로 원본을 target_w×target_h 로 리사이즈/재인코딩한다.

    1080p/4K 출력 모드인데 원본이 이미 목표 크기 이상이면(예: 4K 원본에 1080p 선택) 불필요한
    AI 확대를 피하려고 쓴다(resolution.needs_ai_upscale). 디코드→(AI 없이)→인코드를 FFmpeg
    한 프로세스가 전부 처리하므로 청크/파이프 구조가 필요 없다 — 가장 단순한 경로.
    on_frame(done_frames) 로 진행률을 보고한다(ffmpeg 자체 -progress 출력 기반)."""
    cmd = [str(ffmpeg_path()), "-v", "error", "-y", "-nostdin", "-i", str(src),
           "-map", "0:v:0"]
    if info.has_audio:
        cmd += ["-map", "0:a:0"]
        cmd += ["-c:a", "copy"] if info.audio_codec in _MP4_AUDIO_COPY_OK else ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-vf", f"scale={target_w}:{target_h}:flags=lanczos"]
    cmd += _video_codec_args(encoder, crf, preset)
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(dst)]
    log.debug("plain resize: %s", " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    try:
        for line in p.stdout:
            if cancel.cancelled:
                kill_process(p)
                raise CancelledError()
            if line.startswith("frame="):
                try:
                    on_frame(int(line.strip().split("=", 1)[1]))
                except (ValueError, IndexError):
                    pass
        p.wait(timeout=600)
    except subprocess.TimeoutExpired:
        kill_process(p)
        raise UpconError("결과 영상을 저장하는 데 시간이 너무 오래 걸립니다.", "plain resize timeout")
    if p.returncode != 0 or not dst.exists():
        stderr = p.stderr.read() if p.stderr else ""
        raise UpconError("결과 영상을 저장하지 못했습니다.", f"plain resize rc={p.returncode}: {stderr[-800:]}")


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
