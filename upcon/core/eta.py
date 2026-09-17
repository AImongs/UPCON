"""남은 시간(ETA) 추정 — 업스케일 단계의 평균 처리 속도(프레임/초)로 계산한다.

- 근사값이다. 처음 몇 초/몇 프레임은 속도가 불안정하므로 None 을 돌려주고 UI 는 '계산 중...' 으로 표시한다.
- 진행이 되돌아가면(새 작업/새 단계) 자동으로 다시 시작한다.
- 표시가 널뛰지 않도록 지수 평활(EMA)을 건다.
"""

from __future__ import annotations

import time


class EtaEstimator:
    MIN_SECONDS = 3.0      # 이 시간 이상 관측해야 계산
    MIN_FRAMES = 12        # 이 프레임 이상 진행해야 계산
    SMOOTHING = 0.3        # 새 추정치 반영 비율

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._t0: float | None = None
        self._f0 = 0
        self._last_done = -1
        self._eta: float | None = None

    def update(self, frames_done: int, frames_total: int, now: float | None = None) -> float | None:
        """진행 상황을 넣고 남은 초를 돌려준다. 아직 믿을 수 없으면 None."""
        now = time.time() if now is None else now
        if frames_total <= 0 or frames_done < 0:
            return None
        if frames_done >= frames_total:
            self._eta = 0.0
            return 0.0
        if self._t0 is None or frames_done < self._last_done:
            self._t0, self._f0, self._last_done, self._eta = now, frames_done, frames_done, None
            return None
        self._last_done = frames_done
        dt, df = now - self._t0, frames_done - self._f0
        if dt < self.MIN_SECONDS or df < self.MIN_FRAMES or dt <= 0:
            return None
        remaining = (frames_total - frames_done) * dt / df
        self._eta = remaining if self._eta is None else (1 - self.SMOOTHING) * self._eta + self.SMOOTHING * remaining
        return self._eta


def format_eta(seconds: float | None) -> str:
    """'약 1분 20초' 형태. None 이면 '계산 중...'. 1분 이상은 10초 단위로 반올림해 표시가 덜 흔들리게 한다."""
    if seconds is None:
        return "계산 중..."
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"약 {max(s, 1)}초" if s > 0 else "곧 완료"
    s = int(round(s / 10.0) * 10)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"약 {h}시간 {m}분"
    return f"약 {m}분 {sec}초" if sec else f"약 {m}분"
