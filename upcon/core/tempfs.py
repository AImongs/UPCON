"""임시 작업 폴더, 디스크 용량 추정/검사."""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from upcon.core.env import free_disk_mb
from upcon.core.errors import UpconError
from upcon.core.paths import user_data_dir
from upcon.core.probe import VideoInfo

log = logging.getLogger(__name__)

# 실측 기반 계수 (RTX 5060, x264 crf18 / ncnn PNG 출력). 보수적으로 잡는다.
PNG_RATIO_PLANNING = 0.35       # 업스케일 PNG 크기 / raw RGB 크기 (실측 0.13~0.16, 노이즈 많으면 상승)
OUTPUT_BITS_PER_PIXEL = 0.30    # 최종 MP4 추정 bpp (crf18, 실측 0.05~0.2)
SAFETY_MARGIN_MB = 500


@dataclass
class DiskPlan:
    chunk_frames: int
    per_frame_temp_bytes: int
    temp_needed_mb: int
    output_estimate_mb: int
    temp_dir: Path
    temp_free_mb: int
    output_free_mb: int

    def describe(self) -> str:
        return (f"chunk={self.chunk_frames}f, temp≈{self.temp_needed_mb}MB (free {self.temp_free_mb}MB), "
                f"output≈{self.output_estimate_mb}MB (free {self.output_free_mb}MB)")


def default_temp_root(custom: str = "") -> Path:
    root = Path(custom) if custom else user_data_dir() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def new_job_dir(root: Path) -> Path:
    d = root / f"job_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def cleanup_dir(d: Path | None) -> None:
    if d and d.exists():
        shutil.rmtree(d, ignore_errors=True)


def cleanup_stale_jobs(root: Path) -> None:
    """이전 실행이 비정상 종료되어 남은 job_* 폴더 정리."""
    try:
        for d in root.glob("job_*"):
            shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass


def plan_disk(info: VideoInfo, scale: int, temp_root: Path, output_path: Path,
              temp_budget_mb: int, chunk_min: int, chunk_max: int) -> DiskPlan:
    in_bytes = info.width * info.height * 3 + 54                       # BMP 입력
    out_raw = info.width * scale * info.height * scale * 3
    out_bytes = int(out_raw * PNG_RATIO_PLANNING)                      # PNG 출력
    per_frame = in_bytes + out_bytes

    budget = temp_budget_mb * 1024 * 1024
    chunk = max(chunk_min, min(chunk_max, budget // per_frame))
    total_frames = max(info.nb_frames, 1)
    chunk = min(chunk, total_frames)

    # 청크 처리 중 다음 청크 입력을 미리 쓸 수 있으므로 2배로 잡는다
    temp_needed = (per_frame * chunk * 2) // (1024 * 1024)
    out_pixels = info.width * scale * info.height * scale
    output_est = int(out_pixels * OUTPUT_BITS_PER_PIXEL * info.fps * info.duration_sec / 8 / (1024 * 1024))
    output_est += int(info.size_bytes / (1024 * 1024))                 # 오디오 등 여유

    return DiskPlan(
        chunk_frames=int(chunk), per_frame_temp_bytes=per_frame,
        temp_needed_mb=int(temp_needed), output_estimate_mb=output_est,
        temp_dir=temp_root, temp_free_mb=free_disk_mb(temp_root), output_free_mb=free_disk_mb(output_path.parent),
    )


def check_disk(plan: DiskPlan, output_path: Path) -> None:
    same_drive = Path(plan.temp_dir).anchor.lower() == Path(output_path).anchor.lower()
    if same_drive:
        need = plan.temp_needed_mb + plan.output_estimate_mb + SAFETY_MARGIN_MB
        if plan.temp_free_mb < need:
            raise UpconError(
                f"저장 공간이 부족합니다. {Path(plan.temp_dir).anchor} 드라이브에 약 {need:,} MB 가 필요하지만 "
                f"{plan.temp_free_mb:,} MB 만 남아 있습니다. 공간을 확보하거나 설정에서 임시 폴더를 다른 드라이브로 바꿔 주세요.",
                f"disk: need {need}MB free {plan.temp_free_mb}MB ({plan.describe()})",
            )
    else:
        need_t = plan.temp_needed_mb + SAFETY_MARGIN_MB
        need_o = plan.output_estimate_mb + SAFETY_MARGIN_MB
        if plan.temp_free_mb < need_t:
            raise UpconError(
                f"임시 작업 공간이 부족합니다. {Path(plan.temp_dir).anchor} 드라이브에 약 {need_t:,} MB 가 필요하지만 "
                f"{plan.temp_free_mb:,} MB 만 남아 있습니다.",
                f"disk(temp): need {need_t}MB free {plan.temp_free_mb}MB",
            )
        if plan.output_free_mb < need_o:
            raise UpconError(
                f"결과를 저장할 공간이 부족합니다. {Path(output_path).anchor} 드라이브에 약 {need_o:,} MB 가 필요하지만 "
                f"{plan.output_free_mb:,} MB 만 남아 있습니다.",
                f"disk(output): need {need_o}MB free {plan.output_free_mb}MB",
            )
