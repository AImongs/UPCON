"""처리 방식 결정 (자동 / 내 PC / 클라우드).

STEP 3: 로컬(LocalNcnn) 만 실제 후보. 클라우드 Provider 는 STEP 4 에서 등록된다.
판단 재료: SystemEnv(제조사/모델/VRAM/CUDA/Vulkan) + 각 Provider 의 check_availability
(모델 요구사항 + 실제 self-test) + RoutingConfig. 임계값은 코드가 아니라 설정/모델 선언에서 온다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from upcon.core.config import AppConfig
from upcon.core.constants import DEFAULT_OUTPUT_MODE, OutputMode, ProcessMode
from upcon.core.env import SystemEnv
from upcon.providers.base import UpscalerProvider

log = logging.getLogger(__name__)


@dataclass
class Decision:
    provider: UpscalerProvider | None
    message: str            # 사용자에게 보여줄 문구 (예: "RTX 5060을 사용하여 내 PC에서 처리합니다.")
    detail: str = ""        # 로그용


class Router:
    def __init__(self, config: AppConfig, local_providers: list[UpscalerProvider],
                 cloud_providers: list[UpscalerProvider] | None = None):
        self.config = config
        self.local = local_providers
        self.cloud = cloud_providers or []

    def _order_local(self) -> list[UpscalerProvider]:
        prio = self.config.routing.local_engine_priority
        if not prio:
            return list(self.local)
        rank = {pid: i for i, pid in enumerate(prio)}
        return sorted(self.local, key=lambda p: rank.get(p.id, len(rank)))

    def decide(self, mode: ProcessMode, env: SystemEnv, scale: int,
              output_mode: OutputMode = DEFAULT_OUTPUT_MODE) -> Decision:
        local_reasons: list[str] = []
        if mode in (ProcessMode.AUTO, ProcessMode.LOCAL):
            for p in self._order_local():
                a = p.check_availability(env, scale, output_mode) if hasattr(p, "check_availability") else None
                if a and a.ok:
                    msg = a.reason.replace("에서 처리합니다.", "로 무료 처리합니다.") if mode == ProcessMode.AUTO else a.reason
                    return Decision(p, msg, a.detail)
                if a:
                    local_reasons.append(a.reason)
                    log.info("local provider %s unavailable: %s", p.id, a.detail)

        if mode == ProcessMode.LOCAL:
            why = local_reasons[0] if local_reasons else "내 PC에서 처리할 수 없습니다."
            return Decision(None, f"{why}\n처리 방식을 '자동' 또는 '클라우드 GPU'로 바꿔 보세요.", "local unavailable")

        # 클라우드
        if mode in (ProcessMode.AUTO, ProcessMode.CLOUD):
            if mode == ProcessMode.AUTO and not self.config.routing.allow_cloud_fallback:
                why = local_reasons[0] if local_reasons else "내 PC에서 처리할 수 없습니다."
                return Decision(None, f"{why}\n(설정에서 클라우드 자동 전환이 꺼져 있습니다)", "cloud fallback disabled")
            cloud_reasons: list[str] = []
            for p in self.cloud:
                a = p.check_availability(env, scale, output_mode)
                if a.ok:
                    if mode == ProcessMode.CLOUD:
                        msg = a.reason
                    else:
                        msg = "사용 가능한 GPU가 없어 클라우드 GPU를 사용합니다."
                    return Decision(p, msg, a.detail)
                cloud_reasons.append(a.reason)
            why = ""
            if mode == ProcessMode.AUTO and local_reasons:
                why = local_reasons[0] + "\n"
            why += cloud_reasons[0] if cloud_reasons else "클라우드 처리 방식을 사용할 수 없습니다."
            return Decision(None, why, "cloud unavailable")
        return Decision(None, "사용할 수 있는 처리 방식이 없습니다.", "no provider")
