"""출력 해상도 계산 (STEP 10).

2× 는 "배율" — 원본 가로/세로를 정확히 2배로 키운다.
1080p/4K 는 "목표 해상도" — 원본 화면비를 유지한 채 FHD/4K 크기에 맞춘다. 강제로 늘이거나
찌그러뜨리지 않는다. 이 둘은 서로 다른 개념이라 여기서만 계산하고, 나머지 코드는 이미 계산된
(width, height) 만 받아 쓴다 (scale 숫자와 목표 해상도 개념을 혼용하지 않는다).

854×480(UPCON 테스트 클립의 480p 기준)처럼 정확히 16:9/9:16 은 아니지만 육안상 '일반 16:9/세로
9:16' 인 소스는 1920×1080/1080×1920 같은 표준 캔버스로 스냅한다 — 그 외 화면비는 원본 비율을
그대로 유지한 채 크기만 맞춘다(임의 stretch 없음).
"""

from __future__ import annotations

from upcon.core.constants import OutputMode

# FHD/4K 의 '짧은 변' 길이. 가로형은 세로 길이, 세로형은 가로 길이가 이 값이 된다.
_SHORT_EDGE = {OutputMode.FHD: 1080, OutputMode.UHD: 2160}

_RATIO_16_9 = 16 / 9
_RATIO_9_16 = 9 / 16
# 854/480 = 1.779166... 와 16/9 = 1.777778... 의 차이(약 0.08%)까지 넉넉히 포함하는 허용 오차.
# 이보다 화면비가 뚜렷이 다르면(4:3, 21:9 등) 표준 캔버스로 스냅하지 않고 원본 비율을 그대로 쓴다.
_STANDARD_RATIO_TOLERANCE = 0.03


def _even(n: int) -> int:
    """인코더(yuv420p, 4:2:0 크로마 서브샘플링) 호환을 위해 짝수로 올림."""
    return n if n % 2 == 0 else n + 1


def target_size(mode: OutputMode, width: int, height: int) -> tuple[int, int]:
    """원본 (width, height) 에 mode 를 적용한 최종 출력 (width, height).

    - TWO_X: (width*2, height*2). 정수 배율이므로 항상 짝수 결과.
    - FHD/UHD: 원본이 16:9/9:16 에 충분히 가까우면 표준 캔버스(1920×1080 등)로 스냅하고,
      그 외에는 원본 화면비를 그대로 유지한 채 짧은 변을 1080/2160 에 맞춘다(임의 stretch 없음).
      계산된 긴 변은 인코더 호환을 위해 짝수로 반올림한다.
    - width/height 가 0 이하이거나 mode 를 알 수 없으면(손상된 설정 등) 입력을 그대로 돌려주거나
      2× 로 안전하게 대체한다 — 여기서 예외를 던지지 않는다(호출부가 크래시하지 않도록).
    """
    if width <= 0 or height <= 0:
        return width, height
    if mode == OutputMode.TWO_X:
        return width * 2, height * 2

    short = _SHORT_EDGE.get(mode)
    if short is None:                      # 알 수 없는 mode 값 -> 2× 로 안전하게 대체
        return width * 2, height * 2

    ratio = width / height
    if abs(ratio - _RATIO_16_9) / _RATIO_16_9 <= _STANDARD_RATIO_TOLERANCE:
        return short * 16 // 9, short                  # 1920×1080 / 3840×2160
    if abs(ratio - _RATIO_9_16) / _RATIO_9_16 <= _STANDARD_RATIO_TOLERANCE:
        return short, short * 16 // 9                   # 1080×1920 / 2160×3840
    if ratio >= 1:                          # 그 외 가로형: 세로를 short 에 맞추고 화면비 유지
        return _even(round(short * ratio)), short
    return short, _even(round(short / ratio))            # 그 외 세로형: 가로를 short 에 맞추고 화면비 유지


def needs_ai_upscale(mode: OutputMode, width: int, height: int) -> bool:
    """AI(Real-ESRGAN) 업스케일이 실제로 필요한지.

    2× 는 정의상 항상 필요하다. 1080p/4K 는 원본이 이미 목표 크기 이상이면(예: 4K 원본에 1080p
    선택) 불필요한 AI 확대를 피하고 FFmpeg 리사이즈만으로 처리한다."""
    if mode == OutputMode.TWO_X:
        return True
    tw, th = target_size(mode, width, height)
    return width < tw or height < th
