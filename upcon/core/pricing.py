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


# ---------------------------------------------------------------------------
# ByteDance Video Upscaler (fal-ai/bytedance-upscaler/upscale/video) 전용 가격 모델.
#
# FlashVSR("출력 메가픽셀당 정액")과 근본적으로 다르다 — 이 모델은
# "(출력 해상도 tier × fps 구간)별 초당 단가 × PRO 10배" 구조다. Platform Pricing API
# (FalApi.get_unit_price)는 tier/해상도/fps 를 구분하지 않는 참고값 하나만 주며, 그 값이
# 정확히 "1080p·standard·≤30fps" 기준과 일치한다(2026-09-20 실측 $0.0072/s) — 즉 PRO 배율,
# 2K/4K, 60fps 배율은 API로 조회할 수 없고 아래 문서 상수로만 계산한다.
#
# 출처(원문, 2026-09-20 확인):
#   "Processing in `pro` mode makes the price 10 times of the normal price
#    ($0.072/s, $0.144/s, $0.288/s for 1080p, 2K, 4K at 30fps respectively,
#    at 60fps these prices are further doubled.)"
#   https://fal.ai/models/fal-ai/bytedance-upscaler/upscale/video
#
# 31~59fps, 61~120fps 구간의 정확한 배율은 이 문서에 없다. 임의의 배율(3배/4배 등)을
# 가정하지 않고, 확인된 가장 가까운 하한 요율(각각 ≤30fps, 60fps 요율)로 "확인 가능한
# 최소 예상 비용"만 계산한 뒤 uncertain=True 로 표시한다 — 실제 청구액은 이보다 높을 수 있다.

BYTEDANCE_ENDPOINT = "fal-ai/bytedance-upscaler/upscale/video"
BYTEDANCE_PRO_MULTIPLIER = 10.0
BYTEDANCE_PRICE_DOC_DATE = "2026-09-20"
BYTEDANCE_PRICE_SOURCE_URL = "https://fal.ai/models/fal-ai/bytedance-upscaler/upscale/video"

# standard/fast tier 기준 USD/초. le30 = 30fps 이하(공식 확인), r60 = 정확히 60fps(공식 확인).
BYTEDANCE_TIER_RATES_USD_PER_SEC: dict[str, dict[str, float]] = {
    "1080p": {"le30": 0.0072, "r60": 0.0144},
    "2k":    {"le30": 0.0144, "r60": 0.0288},
    "4k":    {"le30": 0.0288, "r60": 0.0576},
}

_BYTEDANCE_TIER_PIXELS = (
    ("1080p", 1920 * 1080),
    ("2k", 2560 * 1440),
    ("4k", 3840 * 2160),
)


def bytedance_resolution_tier(width: int, height: int) -> str:
    """출력 픽셀 수를 문서상 tier 이름으로 매핑한다.

    정확한 픽셀 컷오프가 공식 문서에 없어, 비용을 과소 추정하지 않도록 다음 tier 로
    올림한다(해당 tier 픽셀수 이하가 아니면 상위 tier 요율 적용). scale_ratio 는 API
    스펙상 4K 를 넘기면 오류이므로(문서: "valid only up to 4k resolution") 4K 가 상한이다."""
    px = width * height
    for name, limit in _BYTEDANCE_TIER_PIXELS:
        if px <= limit:
            return name
    return "4k"


@dataclass(frozen=True)
class ByteDanceCostEstimate:
    endpoint: str
    out_width: int
    out_height: int
    resolution_tier: str        # "1080p" | "2k" | "4k"
    target_fps: float
    fps_bracket: str            # "le30" | "r60" | "31-59(미확인)" | "61-120(미확인)"
    duration_sec: float
    rate_usd_per_sec: float     # 적용된 standard tier 하한 요율
    tier_multiplier: float      # PRO = 10.0 고정 (공식 문서 기준)
    usd: float
    uncertain: bool             # True 면 usd 는 "확인 가능한 최소 예상치"일 뿐 확정값이 아님
    uncertain_note: str = ""

    @property
    def usd_display(self) -> float:
        """표시용: 보수적으로 센트 단위 올림, 최소 $0.01. (uncertain=True 여도 최소치일 뿐이므로
        올림 자체가 '확정 비용'을 뜻하지 않는다 — UI 는 uncertain_note 를 반드시 함께 보여줘야 한다.)"""
        return max(0.01, math.ceil(self.usd * 100 - 1e-9) / 100)

    def krw(self, krw_per_usd: float | None) -> int | None:
        if not krw_per_usd or krw_per_usd <= 0:
            return None
        return int(math.ceil(self.usd_display * krw_per_usd / 10.0) * 10)

    def basis_text(self) -> str:
        base = (f"출력 {self.out_width}×{self.out_height}({self.resolution_tier}) · {self.target_fps:g}fps · "
                f"{self.duration_sec:.1f}초 × ${self.rate_usd_per_sec:g}/초 × PRO {self.tier_multiplier:g}배 "
                f"= ${self.usd:.4f} (공식 문서 {BYTEDANCE_PRICE_DOC_DATE} 기준)")
        if self.uncertain:
            base += f" — {self.uncertain_note}"
        return base


def estimate_bytedance_cost(info: VideoInfo, target_fps: float, scale_ratio: float = 2.0) -> ByteDanceCostEstimate:
    """target_fps 는 이미 24~120 범위로 clamp 된 값이어야 한다
    (upcon.providers.fal_bytedance.resolve_target_fps 로 계산)."""
    out_w = int(round(info.width * scale_ratio))
    out_h = int(round(info.height * scale_ratio))
    tier = bytedance_resolution_tier(out_w, out_h)
    rates = BYTEDANCE_TIER_RATES_USD_PER_SEC[tier]
    uncertain = False
    note = ""
    if target_fps <= 30:
        bracket, rate = "le30", rates["le30"]
    elif target_fps == 60:
        bracket, rate = "r60", rates["r60"]
    elif target_fps < 60:
        bracket, rate, uncertain = "31-59(미확인)", rates["le30"], True
        note = "31~59fps 구간은 공식 요율이 공개되어 있지 않아 30fps 요율로 최소 예상치를 계산했습니다."
    else:
        bracket, rate, uncertain = "61-120(미확인)", rates["r60"], True
        note = "61~120fps 구간은 공식 요율이 공개되어 있지 않아 60fps 요율로 최소 예상치를 계산했습니다."
    usd = rate * BYTEDANCE_PRO_MULTIPLIER * info.duration_sec
    return ByteDanceCostEstimate(BYTEDANCE_ENDPOINT, out_w, out_h, tier, target_fps, bracket,
                                 info.duration_sec, rate, BYTEDANCE_PRO_MULTIPLIER, usd, uncertain, note)
