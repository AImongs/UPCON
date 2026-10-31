"""로컬 AI 모델 레지스트리.

모델은 코드가 아니라 여기 선언으로 관리한다. 각 모델은 자기 요구사항(ModelRequirements)을 선언하고,
Router/Provider 는 그것을 SystemEnv 와 대조한다. 설정(RoutingConfig.model_requirement_overrides)으로
요구사항을 재정의할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from upcon.core.constants import PictureLook, parse_picture_look
from upcon.core.env import ModelRequirements


@dataclass(frozen=True)
class NcnnModelSpec:
    """realesrgan-ncnn-vulkan 용 모델 파일 정의."""

    id: str
    display_name: str
    description: str
    # -n 에 넘길 이름. scale 별 파일명이 다르면 `{scale}` 자리표시자 사용.
    ncnn_name: str
    scales: tuple[int, ...]
    license: str
    requirements: ModelRequirements

    def ncnn_model_name(self, scale: int) -> str:
        return self.ncnn_name.format(scale=scale)

    def file_basename(self, scale: int) -> str:
        """models/ 폴더 안의 .param/.bin 기본 이름."""
        name = self.ncnn_model_name(scale)
        # 실행파일 규칙: realesr-animevideov3 만 '-x{scale}' 접미사, 나머지는 이름 그대로
        return f"{name}-x{scale}" if name == "realesr-animevideov3" else name


_COMPACT_REQ = ModelRequirements(
    model_id="", min_vram_mb=1024, requires_vulkan=True, temporal=False, needs_download=False,
)

LOCAL_MODELS: dict[str, NcnnModelSpec] = {
    "realesr-general-x4v3": NcnnModelSpec(
        id="realesr-general-x4v3",
        display_name="실사 (Real-ESRGAN General v3)",
        description="실사·AI 실사 영상용. 노이즈 제거 강도 0.5 (공식 기본값).",
        ncnn_name="realesr-general-dn05-x4v3-s{scale}",
        scales=(2,),
        license="BSD-3-Clause (Real-ESRGAN, 공식 .pth 에서 변환)",
        requirements=replace(_COMPACT_REQ, model_id="realesr-general-x4v3"),
    ),
    "realesr-general-x4v3-sharp": NcnnModelSpec(
        id="realesr-general-x4v3-sharp",
        display_name="실사 · 노이즈 제거 강하게",
        description="노이즈가 많은 영상용. 피부 질감이 다소 매끈해질 수 있음.",
        ncnn_name="realesr-general-x4v3-s{scale}",
        scales=(2,),
        license="BSD-3-Clause (Real-ESRGAN, 공식 .pth 에서 변환)",
        requirements=replace(_COMPACT_REQ, model_id="realesr-general-x4v3-sharp"),
    ),
    "realesr-general-x4v3-soft": NcnnModelSpec(
        id="realesr-general-x4v3-soft",
        display_name="실사 · 질감 유지",
        description="원본 질감/그레인을 최대한 유지 (약한 노이즈 제거).",
        ncnn_name="realesr-general-wdn-x4v3-s{scale}",
        scales=(2,),
        license="BSD-3-Clause (Real-ESRGAN, 공식 .pth 에서 변환)",
        requirements=replace(_COMPACT_REQ, model_id="realesr-general-x4v3-soft"),
    ),
    "realesr-animevideov3": NcnnModelSpec(
        id="realesr-animevideov3",
        display_name="애니메이션 (Real-ESRGAN AnimeVideo v3)",
        description="애니메이션/일러스트 영상용. 가장 빠름.",
        ncnn_name="realesr-animevideov3",
        scales=(2, 3, 4),
        license="BSD-3-Clause (Real-ESRGAN 공식 배포 파일)",
        requirements=replace(_COMPACT_REQ, model_id="realesr-animevideov3"),
    ),
}

DEFAULT_LOCAL_MODEL = "realesr-general-x4v3"


def get_model(model_id: str) -> NcnnModelSpec:
    return LOCAL_MODELS.get(model_id) or LOCAL_MODELS[DEFAULT_LOCAL_MODEL]


@dataclass(frozen=True)
class LookSpec:
    """'느낌' 하나가 정하는 값.

    model_id    : None 이면 설정의 local_model 을 그대로 쓴다.
    ai_weight   : AI 결과 비중(1.0 = AI 결과 그대로). 나머지는 같은 프레임의 bicubic 2× 확대.
                  섞기는 ncnn 모델 안에서 한다(core/model_blend.py) — 프레임이 어긋날 일이 없고 추가 비용도 거의 없다.
    grain       : 인코더 단계에서 밝기 채널에 얹는 필름 그레인 세기(FFmpeg noise 필터 c0s, 0 = 없음).
                  AI 업스케일 특유의 '너무 깨끗한' 면을 깨서 실제 촬영본처럼 보이게 한다.
    """

    model_id: str | None
    ai_weight: float
    grain: int


# 값은 실제 AI 드라마(Seedance 480p) 얼굴 클로즈업을 나란히 놓고 고른 것 (2026-10-31).
LOOKS: dict[PictureLook, LookSpec] = {
    PictureLook.NATURAL: LookSpec(model_id="realesr-general-x4v3-soft", ai_weight=0.55, grain=3),
    PictureLook.BALANCED: LookSpec(model_id=None, ai_weight=0.75, grain=2),
    PictureLook.SHARP: LookSpec(model_id=None, ai_weight=1.0, grain=0),
}


def get_look(look: str | PictureLook) -> LookSpec:
    return LOOKS[parse_picture_look(look.value if isinstance(look, PictureLook) else look)]


def effective_requirements(spec: NcnnModelSpec, overrides: dict[str, dict[str, Any]]) -> ModelRequirements:
    """설정 파일의 재정의를 적용한 요구사항."""
    ov = overrides.get(spec.id) or {}
    valid = {k: v for k, v in ov.items() if k in ModelRequirements.__dataclass_fields__ and k != "model_id"}
    return replace(spec.requirements, **valid) if valid else spec.requirements
