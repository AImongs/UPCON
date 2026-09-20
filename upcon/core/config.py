"""사용자 설정 (JSON). 민감정보(API Key)는 여기 저장하지 않는다 → secrets 모듈(keyring).

라우팅 임계값·모델 요구사항 재정의 등은 코드에 하드코딩하지 않고 이 설정으로 조정한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from upcon.core.constants import DEFAULT_OUTPUT_MODE, DEFAULT_SCALE, ProcessMode
from upcon.core.paths import config_file

log = logging.getLogger(__name__)


@dataclass
class RoutingConfig:
    """자동 모드 판단에 쓰이는 조정 가능한 값.

    실제 판단은 Router(STEP 5) 가 GPU 제조사/모델/VRAM/CUDA/Vulkan/모델 요구사항/설치 여부를
    종합해서 내리며, 여기 값은 그 판단의 '여유분'과 '재정의'만 담당한다.
    """

    prefer_local: bool = True                    # 로컬 처리 가능하면 로컬 우선
    allow_cloud_fallback: bool = True            # 로컬 불가 시 클라우드로 전환 허용
    vram_safety_margin_mb: int = 512             # 모델 요구 VRAM + 여유분
    # 모델별 요구사항 재정의: {"seedvr2_3b": {"min_vram_mb": 10240}} 처럼 부분 덮어쓰기
    model_requirement_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 로컬 엔진 우선순위 (앞일수록 우선). 비어 있으면 Provider 기본 순서.
    local_engine_priority: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    process_mode: str = ProcessMode.AUTO.value
    scale: int = DEFAULT_SCALE                    # AI/클라우드 실제 배율(항상 2). output_mode 와 다른 개념
    output_mode: str = DEFAULT_OUTPUT_MODE.value   # 사용자가 고른 출력 방식: "2x" | "1080p" | "4k"
    output_dir: str = ""                         # 비어 있으면 원본 옆에 저장
    # '영상 추가' 대화상자가 시작할 폴더 = 마지막으로 영상을 추가한 폴더. 비어 있거나 없으면 동영상 폴더.
    last_open_dir: str = ""
    # 대용량 모델(SeedVR2 등) 다운로드 위치. 사용자가 직접 선택. 비어 있으면 미설치 상태.
    engine_dir: str = ""
    cloud_provider: str = "fal"                      # credential 자체는 keyring 에만 저장
    cloud_model: str = "fal-ai/bytedance-upscaler/upscale/video"
    # 연결 테스트 때 fal Pricing API 로 조회한 단가 캐시 {endpoint: usd_per_unit}. 없으면 문서 상수 사용.
    cloud_unit_prices: dict[str, float] = field(default_factory=dict)
    cloud_price_checked_at: str = ""
    # FlashVSR 추가 인자 (고급): 예 {"acceleration": "high"}
    cloud_extra_args: dict[str, Any] = field(default_factory=dict)
    # 원화 환산 (0 이면 표시 안 함). 하드코딩 금지 — 향후 환율 조회 기능으로 채움.
    krw_per_usd: float = 0.0
    # 로컬 Tier 1 모델 id (upcon/core/models.py 의 LOCAL_MODELS 키)
    local_model: str = "realesr-general-x4v3"
    # 임시 작업 폴더. 비어 있으면 %LOCALAPPDATA%\UPCON\tmp
    temp_dir: str = ""
    # 청크 하나가 차지할 수 있는 임시 디스크 상한(MB). 청크 프레임 수는 여기서 역산한다.
    temp_budget_mb: int = 1500
    chunk_frames_min: int = 24
    chunk_frames_max: int = 240
    # 출력 인코딩. output_encoder: auto(하드웨어 인코더 우선) | libx264 | h264_nvenc | h264_amf | h264_qsv
    output_encoder: str = "auto"
    output_crf: int = 18
    output_preset: str = "medium"
    routing: RoutingConfig = field(default_factory=RoutingConfig)

    # ---- 저장/로드 ----
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = path or config_file()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("설정 파일을 읽을 수 없어 기본값을 사용합니다: %s", e)
            return cls()
        cfg = cls()
        for k, v in raw.items():
            if k == "routing" and isinstance(v, dict):
                cfg.routing = RoutingConfig(**{kk: vv for kk, vv in v.items() if kk in RoutingConfig.__dataclass_fields__})
            elif k in cls.__dataclass_fields__:
                setattr(cfg, k, v)
        if cfg._migrate_cloud_model_to_bytedance():
            cfg.save(path)
        return cfg

    _LEGACY_FLASHVSR_ENDPOINT = "fal-ai/flashvsr/upscale/video"
    _DEFAULT_BYTEDANCE_ENDPOINT = "fal-ai/bytedance-upscaler/upscale/video"

    def _migrate_cloud_model_to_bytedance(self) -> bool:
        """기존 사용자의 config.json 에 저장돼 있던 cloud_model=FlashVSR endpoint 를
        새 기본 클라우드 엔진(ByteDance PRO)으로 옮긴다. fal API Key(keyring)는 이 함수가
        절대 건드리지 않는다 — cloud_model 문자열 하나만 바꾼다. cloud_unit_prices 에 캐시된
        FlashVSR 단가는 그대로 둔다(더는 쓰이지 않지만 삭제할 이유도 없다 — 데이터 손실 방지)."""
        if self.cloud_model == self._LEGACY_FLASHVSR_ENDPOINT:
            log.info("cloud_model 마이그레이션: FlashVSR endpoint → ByteDance PRO endpoint (fal API Key는 유지됨)")
            self.cloud_model = self._DEFAULT_BYTEDANCE_ENDPOINT
            return True
        return False

    def save(self, path: Path | None = None) -> None:
        path = path or config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
