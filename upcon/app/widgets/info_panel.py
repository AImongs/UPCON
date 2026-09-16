"""선택한 영상의 정보(파일명/해상도/FPS/길이/크기)를 보여주는 카드."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from upcon.core.probe import VideoInfo


class InfoPanel(QFrame):
    ROWS = (("해상도", "resolution"), ("FPS", "fps"), ("길이", "duration"), ("파일 크기", "size"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        title = QLabel("영상 정보")
        title.setProperty("class", "cardTitle")
        outer.addWidget(title)

        self.file_name = QLabel("아직 선택한 영상이 없습니다")
        self.file_name.setObjectName("fileName")
        self.file_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.file_name)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.values: dict[str, QLabel] = {}
        for i, (label, key) in enumerate(self.ROWS):
            k = QLabel(label)
            k.setProperty("class", "infoKey")
            v = QLabel("—")
            v.setProperty("class", "infoValue")
            self.values[key] = v
            grid.addWidget(k, i, 0)
            grid.addWidget(v, i, 1)
        grid.setColumnStretch(2, 1)

        # 업스케일 결과 예상 해상도
        k = QLabel("업스케일 후")
        k.setProperty("class", "infoKey")
        self.after = QLabel("—")
        self.after.setProperty("class", "infoValue")
        grid.addWidget(k, len(self.ROWS), 0)
        grid.addWidget(self.after, len(self.ROWS), 1)

        # 클라우드 예상 비용 (항상 "예상" 으로만 표기)
        k2 = QLabel("클라우드 예상 비용")
        k2.setProperty("class", "infoKey")
        self.cost = QLabel("—")
        self.cost.setProperty("class", "infoValue")
        self.cost.setToolTip("")
        grid.addWidget(k2, len(self.ROWS) + 1, 0)
        grid.addWidget(self.cost, len(self.ROWS) + 1, 1)
        outer.addLayout(grid)

        self.extra = QLabel("")
        self.extra.setProperty("class", "hint")
        self.extra.setVisible(False)
        outer.addWidget(self.extra)

    def clear(self) -> None:
        self.file_name.setText("아직 선택한 영상이 없습니다")
        for v in self.values.values():
            v.setText("—")
        self.after.setText("—")
        self.cost.setText("—")
        self.cost.setToolTip("")
        self.extra.setVisible(False)

    def show_loading(self, name: str) -> None:
        self.file_name.setText(name)
        for v in self.values.values():
            v.setText("분석 중...")
        self.after.setText("분석 중...")
        self.cost.setText("분석 중...")

    def set_info(self, info: VideoInfo, scale: int, extra_count: int = 0) -> None:
        self.file_name.setText(info.path.name)
        self.values["resolution"].setText(info.resolution_text)
        self.values["fps"].setText(info.fps_text)
        self.values["duration"].setText(info.duration_text)
        self.values["size"].setText(info.size_text)
        self.after.setText(f"{info.upscaled_resolution_text(scale)}  ({scale}×)")
        if extra_count > 0:
            self.extra.setText(f"외 {extra_count}개 파일 — 여러 파일 일괄 처리는 다음 단계에서 지원됩니다")
            self.extra.setVisible(True)
        else:
            self.extra.setVisible(False)

    def set_cost(self, text: str, basis: str = "") -> None:
        """예: '약 $0.20 (클라우드 GPU 사용 시)'. basis 는 툴팁으로 계산 근거."""
        self.cost.setText(text)
        self.cost.setToolTip(basis)

    def update_scale(self, info: VideoInfo | None, scale: int) -> None:
        if info:
            self.after.setText(f"{info.upscaled_resolution_text(scale)}  ({scale}×)")
