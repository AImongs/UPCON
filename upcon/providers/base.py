"""UpscalerProvider 추상 인터페이스.

    UpscalerProvider
    ├── LocalNcnnProvider      (STEP 3, 구현됨)
    ├── LocalSeedVR2Provider   (예정)
    ├── FalFlashVSRProvider    (STEP 4)
    ├── FalSeedVRProvider      (STEP 4)
    └── FalRealESRGANProvider  (STEP 4)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from upcon.core.env import SystemEnv
from upcon.core.jobs import Job, ProgressCallback
from upcon.core.probe import VideoInfo


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str = ""            # 사용자용 문구 (불가 사유 또는 사용 GPU 안내)
    detail: str = ""            # 로그용


@dataclass(frozen=True)
class Estimate:
    cost_usd: float | None      # 로컬은 None(무료)
    seconds: float | None       # 예상 처리 시간 (모르면 None)
    note: str = ""


class UpscalerProvider(ABC):
    id: str = ""
    name: str = ""
    kind: Literal["local", "cloud"] = "local"
    temporal: bool = False

    @abstractmethod
    def check_availability(self, env: SystemEnv) -> Availability:
        """이 Provider 로 지금 처리할 수 있는지. 'GPU 존재' 가 아니라 '모델 실행 가능' 을 판단한다."""

    @abstractmethod
    def estimate(self, info: VideoInfo, scale: int) -> Estimate: ...

    @abstractmethod
    def upscale(self, job: Job, progress: ProgressCallback) -> Path:
        """job.input_path 를 업스케일해 결과 파일 경로를 돌려준다. 취소 시 CancelledError 를 던진다."""
