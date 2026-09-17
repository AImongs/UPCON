"""진행 상태 카드: 현재 파일 진행률 + 전체(배치) 진행률 + 마지막 결과 파일 위치.

사용자 화면에서는 프레임 숫자보다 '진행률 / 남은 시간 / 결과가 어디 있는지' 가 중요하다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout

from upcon.core.jobs import BatchSummary


class ElidedLabel(QLabel):
    """긴 경로가 카드 폭을 넘지 않도록 가운데를 '…' 로 줄여 그린다. 전체 텍스트는 툴팁으로."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)

    def full_text(self) -> str:
        return self._full

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))     # QSS 의 color 를 그대로 쓴다
        fm = QFontMetrics(self.font())
        rect = self.contentsRect()
        text = fm.elidedText(self._full, Qt.TextElideMode.ElideMiddle, rect.width())
        p.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class ProgressPanel(QFrame):
    IDLE_TEXT = "대기 중"
    IDLE_SUB = "'전체 업스케일 시작' 버튼을 누르면 처리를 시작합니다."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(4)

        title = QLabel("진행 상태")
        title.setProperty("class", "cardTitle")
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

        # 현재 파일
        k1 = QLabel("현재 파일")
        k1.setProperty("class", "infoKey")
        self.cur_name = QLabel("—")
        self.cur_name.setObjectName("statusText")
        self.cur_bar = QProgressBar()
        self.cur_bar.setRange(0, 100)
        self.cur_bar.setFixedHeight(10)
        self.cur_sub = QLabel("")
        self.cur_sub.setObjectName("statusSub")
        grid.addWidget(k1, 0, 0)
        grid.addWidget(self.cur_name, 0, 1)
        grid.addWidget(self.cur_bar, 1, 1)
        grid.addWidget(self.cur_sub, 2, 1)

        # 전체
        k2 = QLabel("전체 작업")
        k2.setProperty("class", "infoKey")
        self.all_name = QLabel("—")
        self.all_name.setObjectName("statusText")
        self.all_bar = QProgressBar()
        self.all_bar.setRange(0, 100)
        self.all_bar.setFixedHeight(10)
        self.all_sub = QLabel("")
        self.all_sub.setObjectName("statusSub")
        grid.addWidget(k2, 3, 0)
        grid.addWidget(self.all_name, 3, 1)
        grid.addWidget(self.all_bar, 4, 1)
        grid.addWidget(self.all_sub, 5, 1)

        # 마지막 결과 (완료 후 '파일이 어디 있지?' 에 바로 답한다)
        self.result_key = QLabel("결과 파일")
        self.result_key.setProperty("class", "infoKey")
        self.result_name = ElidedLabel("")
        self.result_name.setObjectName("statusText")
        self.result_dir = ElidedLabel("")
        self.result_dir.setObjectName("statusSub")
        grid.addWidget(self.result_key, 6, 0)
        grid.addWidget(self.result_name, 6, 1)
        grid.addWidget(self.result_dir, 7, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.set_result(None)

    # ---- 현재 파일 ----
    def set_current(self, name: str, phase_text: str, percent: int | None, sub: str = "") -> None:
        self.cur_name.setText(f"{name}  ·  {phase_text}" if name else phase_text)
        if percent is None:
            self.cur_bar.setRange(0, 0)
        else:
            self.cur_bar.setRange(0, 100)
            self.cur_bar.setValue(max(0, min(100, percent)))
        self.cur_sub.setText(sub)

    def set_idle(self, text: str = IDLE_TEXT, sub: str = "", percent: int = 0) -> None:
        self.cur_name.setText(text)
        self.cur_bar.setRange(0, 100)
        self.cur_bar.setValue(max(0, min(100, percent)))
        self.cur_sub.setText(sub)

    def set_waiting(self, pending: int) -> None:
        """새 대기 항목이 생겼을 때: 이전 '완료' 문구가 새 항목 상태처럼 보이지 않게 초기화."""
        self.set_idle(self.IDLE_TEXT, f"대기 {pending}개  ·  {self.IDLE_SUB}")

    # ---- 결과 ----
    def set_result(self, output: Path | None) -> None:
        show = output is not None
        for w in (self.result_key, self.result_name, self.result_dir):
            w.setVisible(show)
        if show:
            self.result_name.setText(output.name)
            self.result_dir.setText(f"저장 위치: {output.parent}")
        else:
            self.result_name.setText("")
            self.result_dir.setText("")

    # ---- 전체 ----
    def set_batch(self, s: BatchSummary, running: bool) -> None:
        if s.total == 0:
            self.all_name.setText("—")
            self.all_bar.setValue(0)
            self.all_sub.setText("")
            return
        # 진행률은 '완료한 작업량' 기준 (실패/취소는 진행으로 치지 않는다 → 완료 0개가 100% 로 보이지 않음)
        self.all_bar.setValue(s.percent)
        if running:
            self.all_name.setText(f"완료 {s.done} / {s.total}  ·  전체 진행률 {s.percent}%")
        elif s.pending:
            self.all_name.setText(f"완료 {s.done} / {s.total}  ·  대기 {s.pending}개")
        else:
            self.all_name.setText(f"완료 {s.done} / {s.total}")
        parts = [f"전체 {s.total}개", f"완료 {s.done}"]
        if s.running:
            parts.append(f"처리 중 {s.running}")
        parts.append(f"대기 {s.pending}")
        if s.failed:
            parts.append(f"실패 {s.failed}")
        if s.cancelled:
            parts.append(f"취소 {s.cancelled}")
        if s.interrupted:
            parts.append(f"중단 {s.interrupted}")
        self.all_sub.setText("  ·  ".join(parts))
