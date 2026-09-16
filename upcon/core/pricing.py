"""클라우드 예상 비용 계산.

근거 (fal.ai 공식 모델 페이지, 2026-09-15 확인):
  fal-ai/flashvsr/upscale/video — "Your request will cost $0.0005 per megapixel of video data (width × height × frames)."
  예시: "if your upscaled video is 1920×1080 with 121 frames, the total cost will be $0.125."
  → 1920 × 1080 × 121 = 250,905,600 px = 250.9 MP × $0.0005 = $0.1255 ≈ $0.125  ⇒ **출력(업스케일 후) 해상도 기준**.
  (입력 기준이라면 960×540 기준 $0.031 이 되어 예시와 맞지 않음)

실제 단가는 fal Platform API `GET https://api.fal.ai/v1/models/pricing?endpoint_id=…` 로 조회해
설정에 캐시하고, 조회 불가 시 아래 문서 상수를 쓴다. 결과는 항상 "예상 비용" 으로만 표시한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from upcon.core.probe import VideoInfo

# 문서 기준 단가 (USD / 메가픽셀). 런타임에 API 조회값이 있으면 그것을 우선한다.
DOCUMENTED_UNIT_PRICES_USD_PER_MP: dict[str, float] = {
    "fal-ai/flashvsr/upscale/video": 0.0005,
    "fal-ai/seedvr/upscale/video": 0.001,
    "fal-ai/video-upscaler": 0.0008,
}
PRICE_DOC_DATE = "2026-09-15"
PRICE_SOURCE_URL = "https://fal.ai/models/fal-ai/flashvsr/upscale/video"


@dataclass(frozen=True)
class CostEstimate:
    endpoint: str
    out_width: int
    out_height: int
    frames: int
    megapixels: float          # 출력 W × H × 프레임 / 1e6
    unit_price_usd: float      # USD / MP
    usd: float                 # 계산값 (반올림 전)
    price_source: str          # "api" | "documented"

    @property
    def usd_display(self) -> float:
        """표시용: 보수적으로 센트 단위 올림, 최소 $0.01."""
        return max(0.01, math.ceil(self.usd * 100 - 1e-9) / 100)

    def krw(self, krw_per_usd: float | None) -> int | None:
        if not krw_per_usd or krw_per_usd <= 0:
            return None
        return int(math.ceil(self.usd_display * krw_per_usd / 10.0) * 10)

    def basis_text(self) -> str:
        return (f"출력 {self.out_width}×{self.out_height} × {self.frames:,}프레임 = {self.megapixels:,.1f} MP × "
                f"${self.unit_price_usd:g}/MP = ${self.usd:.4f}"
                f" ({'fal API 조회 단가' if self.price_source == 'api' else f'공식 문서 단가 {PRICE_DOC_DATE} 기준'})")


def frames_of(info: VideoInfo) -> int:
    if info.nb_frames > 0:
        return info.nb_frames
    return int(math.ceil(info.fps * info.duration_sec))


def estimate_cost(info: VideoInfo, scale: int, endpoint: str,
                  unit_price_usd: float | None = None) -> CostEstimate:
    """출력 해상도 기준 메가픽셀 × 단가. unit_price_usd 가 None 이면 문서 상수."""
    if unit_price_usd is None or unit_price_usd <= 0:
        price, source = DOCUMENTED_UNIT_PRICES_USD_PER_MP[endpoint], "documented"
    else:
        price, source = unit_price_usd, "api"
    ow, oh = info.width * scale, info.height * scale
    frames = frames_of(info)
    mp = ow * oh * frames / 1e6
    return CostEstimate(endpoint, ow, oh, frames, mp, price, mp * price, source)


def format_usd(usd: float) -> str:
    return f"${usd:,.2f}"


def format_krw(krw: int) -> str:
    return f"₩{krw:,}"
