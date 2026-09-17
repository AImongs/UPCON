"""출력 해상도 계산(upcon.core.resolution) 단위 테스트. GPU/FFmpeg 불필요, 항상 실행된다."""

from __future__ import annotations

import pytest

from upcon.core.constants import OutputMode, parse_output_mode
from upcon.core.resolution import needs_ai_upscale, target_size

M = OutputMode


# ---------------------------------------------------------------- STEP 10 요구 테스트 행렬 (섹션 10)
@pytest.mark.parametrize("w,h,mode,expected", [
    (854, 480, M.TWO_X, (1708, 960)),
    (854, 480, M.FHD, (1920, 1080)),
    (854, 480, M.UHD, (3840, 2160)),
    (480, 854, M.TWO_X, (960, 1708)),
    (480, 854, M.FHD, (1080, 1920)),
    (480, 854, M.UHD, (2160, 3840)),
    (1920, 1080, M.TWO_X, (3840, 2160)),
])
def test_required_matrix(w, h, mode, expected):
    assert target_size(mode, w, h) == expected


# ---------------------------------------------------------------- 가로/세로 둘 다 (섹션 6)
@pytest.mark.parametrize("w,h", [(854, 480), (480, 854), (1920, 1080), (1080, 1920)])
@pytest.mark.parametrize("mode", [M.TWO_X, M.FHD, M.UHD])
def test_all_orientations_produce_positive_even_dimensions(w, h, mode):
    tw, th = target_size(mode, w, h)
    assert tw > 0 and th > 0
    assert tw % 2 == 0 and th % 2 == 0, f"{mode.value} {w}x{h} -> {tw}x{th} (인코더 호환을 위해 짝수여야 함)"
    # 확대(또는 동일) 방향만 — 절대 원본보다 작아지지 않는다
    assert tw >= w and th >= h


def test_exact_16_9_and_9_16_are_canonical():
    assert target_size(M.FHD, 1920, 1080) == (1920, 1080)          # 정확히 16:9 → 그대로
    assert target_size(M.FHD, 1080, 1920) == (1080, 1920)          # 정확히 9:16 → 그대로
    assert target_size(M.UHD, 1920, 1080) == (3840, 2160)
    assert target_size(M.UHD, 1080, 1920) == (2160, 3840)


def test_already_above_target_snaps_down_without_upscale_flag_change():
    """4K 원본에 1080p 선택 — 화면비 유지한 채 목표 크기로 (다운스케일 방향)."""
    assert target_size(M.FHD, 3840, 2160) == (1920, 1080)
    assert target_size(M.FHD, 2160, 3840) == (1080, 1920)


# ---------------------------------------------------------------- 표준 화면비가 아닌 경우: stretch 금지
def test_non_standard_ratio_keeps_source_aspect_ratio_exactly():
    """4:3, 21:9 등은 1920x1080/1080x1920 으로 강제로 늘이지 않고 원본 비율을 유지한다."""
    for w, h in [(640, 480), (1280, 960), (2560, 1080)]:
        tw, th = target_size(M.FHD, w, h)
        src_ratio = w / h
        out_ratio = tw / th
        assert abs(out_ratio - src_ratio) / src_ratio < 0.01, f"{w}x{h} 비율이 유지되지 않음: {tw}x{th}"
        assert not (tw == 1920 and th == 1080), f"{w}x{h} 가 강제로 16:9 로 찌그러졌다"


def test_square_video_stays_square():
    tw, th = target_size(M.FHD, 500, 500)
    assert tw == th


# ---------------------------------------------------------------- needs_ai_upscale (섹션 4 정책)
def test_needs_ai_upscale_policy():
    assert needs_ai_upscale(M.TWO_X, 3840, 2160) is True, "2× 는 항상 AI 가 필요하다(정의상)"
    assert needs_ai_upscale(M.FHD, 854, 480) is True, "480p 는 1080p 에 못 미치므로 AI 필요"
    assert needs_ai_upscale(M.FHD, 1920, 1080) is False, "이미 정확히 목표 → AI 불필요"
    assert needs_ai_upscale(M.FHD, 3840, 2160) is False, "이미 목표 이상(4K) → AI 불필요"
    assert needs_ai_upscale(M.UHD, 1920, 1080) is True, "1080p 는 4K 에 못 미치므로 AI 필요"
    assert needs_ai_upscale(M.UHD, 3840, 2160) is False, "이미 정확히 4K 목표 → AI 불필요"


# ---------------------------------------------------------------- 안전성: 크래시 금지
def test_zero_or_negative_dimensions_do_not_crash():
    """손상된 probe 결과 등 비정상 입력에도 예외 없이 입력을 그대로 돌려준다(변형하지 않음)."""
    assert target_size(M.FHD, 0, 0) == (0, 0)
    assert target_size(M.TWO_X, 0, 480) == (0, 480)
    assert target_size(M.UHD, -1, 480) == (-1, 480)


def test_parse_output_mode_fallback_never_raises():
    """잘못된 output_mode 문자열이 들어와도 항상 2× 로 안전하게 대체한다 (앱이 죽지 않음)."""
    for bad in ("", "8k", "2X", "garbage", "1080P", None, 123, [], {}):
        assert parse_output_mode(bad) == OutputMode.TWO_X, f"{bad!r} 가 예외 없이 2× 로 대체돼야 한다"
    assert parse_output_mode("2x") == OutputMode.TWO_X
    assert parse_output_mode("1080p") == OutputMode.FHD
    assert parse_output_mode("4k") == OutputMode.UHD
