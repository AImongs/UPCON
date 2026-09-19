"""클라우드 업스케일 실행 전 확인 대화상자.

사용자가 fal.ai 구조나 API 개념을 몰라도 이해할 수 있게, 파일별 원본/길이/예상 출력 해상도/
예상 비용을 표로 보여주고 전체 합계와 함께 "실제 청구 금액은 fal.ai 가 결정하며 예상값과 다를
수 있다"는 문구를 명확히 보여준다. '실행'을 눌러야만 실제로 배치가 시작된다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from upcon.core.jobs import Job
from upcon.core.pricing import format_krw, format_usd

_COLS = ("파일명", "원본 해상도", "길이", "예상 출력 해상도", "예상 비용")
_COL_NAME, _COL_SRC, _COL_DUR, _COL_OUT, _COL_COST = range(5)


class CloudConfirmDialog(QDialog):
    def __init__(self, jobs: list[Job], scale: int, total_usd: float, krw_per_usd: float, why: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("클라우드 업스케일 확인")
        self.setMinimumWidth(640)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel(f"<b>클라우드 GPU로 업스케일합니다.</b><br>{why}")
        title.setWordWrap(True)
        lay.addWidget(title)

        table = QTableWidget(len(jobs), len(_COLS))
        table.setHorizontalHeaderLabels(_COLS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        for r, j in enumerate(jobs):
            info = j.info
            cells = (
                j.input_path.name,
                info.resolution_text if info else "…",
                info.duration_text if info else "…",
                info.upscaled_resolution_text(scale) if info else "…",
                format_usd(j.estimated_cost_usd) if j.estimated_cost_usd is not None else "…",
            )
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if c == _COL_NAME:
                    it.setToolTip(str(j.input_path))
                table.setItem(r, c, it)
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        for c in (_COL_SRC, _COL_DUR, _COL_OUT, _COL_COST):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table.setMinimumHeight(64 + min(len(jobs), 6) * 28)
        table.setMaximumHeight(64 + min(len(jobs), 8) * 28)
        lay.addWidget(table)

        krw = f" (약 {format_krw(int(total_usd * krw_per_usd))})" if krw_per_usd > 0 else ""
        self.total_label = QLabel(f"총 <b>{len(jobs)}개</b> 영상 · <b>예상 비용: 약 {format_usd(total_usd)}{krw}</b>")
        lay.addWidget(self.total_label)

        note = QLabel(
            "실제 청구 금액은 fal.ai에서 결정되며 예상값과 다를 수 있습니다. "
            "내 fal.ai 계정에서 청구됩니다."
        )
        note.setWordWrap(True)
        note.setProperty("class", "hint")
        lay.addWidget(note)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("실행")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
