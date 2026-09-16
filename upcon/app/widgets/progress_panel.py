"""진행 상태 카드: 현재 파일 진행률 + 전체(배치) 진행률."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar, QVBoxLayout

from upcon.core.jobs import BatchSummary


class ProgressPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(6)

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
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

    # ---- 현재 파일 ----
    def set_current(self, name: str, phase_text: str, percent: int | None, sub: str = "") -> None:
        self.cur_name.setText(f"{name}  ·  {phase_text}" if name else phase_text)
        if percent is None:
            self.cur_bar.setRange(0, 0)
        else:
            self.cur_bar.setRange(0, 100)
            self.cur_bar.setValue(max(0, min(100, percent)))
        self.cur_sub.setText(sub)

    def set_idle(self, text: str = "대기 중", sub: str = "") -> None:
        self.cur_name.setText(text)
        self.cur_bar.setRange(0, 100)
        self.cur_bar.setValue(0)
        self.cur_sub.setText(sub)

    # ---- 전체 ----
    def set_batch(self, s: BatchSummary, running: bool) -> None:
        if s.total == 0:
            self.all_name.setText("—")
            self.all_bar.setValue(0)
            self.all_sub.setText("")
            return
        self.all_name.setText(f"{s.finished_count} / {s.total}  ·  전체 진행률 {s.percent}%")
        self.all_bar.setValue(s.percent)
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
        self.all_sub.setText("  ·  ".join(parts) + ("  ·  총 프레임 기준" if s.frames_total else ""))
