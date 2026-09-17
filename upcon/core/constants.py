"""앱 전역 상수. UI 와 core 가 공유하는 열거형/옵션 정의."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 사용자가 선택/드래그할 수 있는 영상 확장자 (ffprobe 가 실제 유효성 검사)
SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".ts",
)

OUTPUT_SUFFIX_TEMPLATE = "_{scale}x"  # scene01.mp4 -> scene01_2x.mp4 (레거시 문서화용, 실제 파일명은 output_suffix() 사용)


class ProcessMode(str, Enum):
    """처리 방식. 값은 설정 파일에 저장되는 문자열."""

    AUTO = "auto"
    LOCAL = "local"
    CLOUD = "cloud"


PROCESS_MODE_LABELS: dict[ProcessMode, str] = {
    ProcessMode.AUTO: "자동",
    ProcessMode.LOCAL: "내 PC GPU",
    ProcessMode.CLOUD: "클라우드 GPU",
}

PROCESS_MODE_HINTS: dict[ProcessMode, str] = {
    ProcessMode.AUTO: "내 PC로 처리할 수 있으면 PC에서, 아니면 클라우드에서 처리합니다.",
    ProcessMode.LOCAL: "내 PC의 그래픽카드로 처리합니다. 비용이 들지 않습니다.",
    ProcessMode.CLOUD: "클라우드 GPU로 처리합니다. 내 fal.ai 계정에서 비용이 청구됩니다.",
}


@dataclass(frozen=True)
class ScaleOption:
    """업스케일 배율 옵션. 향후 4× 등을 추가할 때 이 목록에만 넣으면 UI 에 반영된다."""

    factor: int
    label: str
    enabled: bool = True


SCALE_OPTIONS: tuple[ScaleOption, ...] = (
    ScaleOption(factor=2, label="2×", enabled=True),
    # ScaleOption(factor=4, label="4×", enabled=False),  # STEP 3 이후 검토
)

DEFAULT_SCALE = 2
"""AI(Real-ESRGAN)/클라우드가 실제로 사용하는 업스케일 배율. 현재 유일하게 검증된 값은 2 뿐이며
STEP 10 이후에도 그대로다 — 사용자가 고르는 '출력 해상도'(OutputMode)와는 다른 개념이다.
2× 는 이 배율을 그대로 결과로 쓰고, 1080p/4K 는 이 배율로 AI 업스케일한 뒤 FFmpeg 로 최종 크기를
맞춘다 (upcon/core/resolution.py). scale 숫자와 목표 해상도 개념을 혼용하지 않는다."""


class OutputMode(str, Enum):
    """사용자가 고르는 출력 방식. 값은 설정 파일/queue.json 에 그대로 저장되는 문자열이자
    결과 파일명 접미사([stem]_{value}.mp4) 이기도 하다 — 2× 는 배율, 1080p/4K 는 목표 해상도."""

    TWO_X = "2x"
    FHD = "1080p"
    UHD = "4k"


OUTPUT_MODE_LABELS: dict[OutputMode, str] = {
    OutputMode.TWO_X: "2× 업스케일",
    OutputMode.FHD: "1080p",
    OutputMode.UHD: "4K",
}

OUTPUT_MODE_HINTS: dict[OutputMode, str] = {
    OutputMode.TWO_X: "원본 가로·세로를 정확히 2배로 늘립니다.",
    OutputMode.FHD: "원본 화면비를 유지하며 FHD(1080p) 크기로 만듭니다.",
    OutputMode.UHD: "원본 화면비를 유지하며 4K 크기로 만듭니다.",
}

DEFAULT_OUTPUT_MODE = OutputMode.TWO_X


def output_suffix(mode: OutputMode) -> str:
    """결과 파일명 접미사. 예: 영상.mp4 -> 영상_2x.mp4 / 영상_1080p.mp4 / 영상_4k.mp4."""
    return f"_{mode.value}"


def parse_output_mode(value: str) -> OutputMode:
    """설정/queue.json 에서 읽은 문자열을 OutputMode 로. 없거나 잘못된 값은 항상 2× 로 안전하게 대체한다
    (기존 UPCON 0.3.0 데이터에는 이 값이 아예 없었다 — 그 경우도 여기로 들어와 2× 가 된다)."""
    try:
        return OutputMode(value)
    except ValueError:
        return DEFAULT_OUTPUT_MODE
