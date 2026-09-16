"""앱 전역 상수. UI 와 core 가 공유하는 열거형/옵션 정의."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 사용자가 선택/드래그할 수 있는 영상 확장자 (ffprobe 가 실제 유효성 검사)
SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".ts",
)

OUTPUT_SUFFIX_TEMPLATE = "_{scale}x"  # scene01.mp4 -> scene01_2x.mp4


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
