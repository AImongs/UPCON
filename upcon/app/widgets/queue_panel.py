"""배치 대기열 패널: 비어 있으면 드롭 영역, 파일이 있으면 파일별 목록(표) + 편집 버튼.

UI 는 Job 데이터만 알고 Provider 구현은 모른다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton, QStackedLayout,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from upcon.app.widgets.drop_zone import DropZone, _is_video
from upcon.core.constants import SUPPORTED_EXTENSIONS
from upcon.core.jobs import Job, JobStatus
from upcon.core.pricing import format_usd

COLS = ("파일명", "원본", "길이", "출력", "상태", "시간 / 예상 비용")
COL_NAME, COL_SRC, COL_DUR, COL_OUT, COL_STATUS, COL_META = range(6)

_STATUS_COLORS = {
    JobStatus.DONE: "#15803d", JobStatus.FAILED: "#b91c1c", JobStatus.RUNNING: "#1d4ed8",
    JobStatus.CANCELLED: "#6b7280", JobStatus.INTERRUPTED: "#b45309", JobStatus.PENDING: "#374151",
}


def _fmt_sec(s: float) -> str:
    s = int(s)
    return f"{s // 60}분 {s % 60}초" if s >= 60 else f"{s}초"


class QueuePanel(QFrame):
    filesDropped = Signal(list)          # list[Path]
    removeRequested = Signal(list)       # list[Job]
    clearRequested = Signal()
    moveRequested = Signal(object, int)  # Job, delta
    retryRequested = Signal()
    openResultRequested = Signal(object) # Job

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setAcceptDrops(True)
        self._rows: dict[str, int] = {}      # job.id → row
        self._jobs: dict[str, Job] = {}
        self._show_cost = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        # 툴바
        bar = QHBoxLayout()
        self.title = QLabel("영상 대기열")
        self.title.setProperty("class", "cardTitle")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.add_btn = QPushButton("+ 영상 추가")
        self.add_btn.clicked.connect(self.open_dialog)
        self.remove_btn = QPushButton("선택 삭제")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("전체 삭제")
        self.clear_btn.clicked.connect(self.clearRequested.emit)
        self.up_btn = QPushButton("↑ 위로")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = QPushButton("↓ 아래로")
        self.down_btn.clicked.connect(lambda: self._move(+1))
        self.retry_btn = QPushButton("실패 항목 다시 시도")
        self.retry_btn.clicked.connect(self.retryRequested.emit)
        for b in (self.add_btn, self.remove_btn, self.clear_btn, self.up_btn, self.down_btn, self.retry_btn):
            b.setProperty("class", "small")
            bar.addWidget(b)
        outer.addLayout(bar)

        # 내용: 드롭존 ↔ 표
        self.stack = QStackedLayout()
        self.drop_zone = DropZone()
        self.drop_zone.filesSelected.connect(self.filesDropped.emit)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for c in (COL_SRC, COL_DUR, COL_OUT, COL_STATUS):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_META, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(COL_META, 230)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setMinimumHeight(300)
        self.table.itemDoubleClicked.connect(self._double_clicked)
        self.table.itemSelectionChanged.connect(self._refresh_buttons)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.table.dragEnterEvent = self.dragEnterEvent      # 표 위에 떨어뜨려도 패널이 처리
        self.table.dragMoveEvent = lambda e: e.acceptProposedAction()
        self.table.dropEvent = self.dropEvent
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self.table)
        self.hint = QLabel("여러 파일을 한꺼번에 드래그해 추가할 수 있습니다. 완료된 항목을 더블클릭하면 결과 파일 위치가 열립니다.")
        self.hint.setProperty("class", "hint")
        wl.addWidget(self.hint)
        self.stack.addWidget(self.drop_zone)
        self.stack.addWidget(wrap)
        outer.addLayout(self.stack)
        self.setMinimumHeight(260)
        self._refresh_buttons()

    # ------------------------------------------------------------ 파일 추가
    def open_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(self, "영상 파일 추가", "", f"영상 파일 ({exts});;모든 파일 (*.*)")
        if files:
            self.filesDropped.emit([Path(f) for f in files])

    def _paths_from_event(self, event) -> list[Path]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile() and _is_video(Path(u.toLocalFile()))]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._paths_from_event(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._paths_from_event(event)
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)

    # ------------------------------------------------------------ 표 갱신
    def set_show_cost(self, show: bool) -> None:
        self._show_cost = show
        self.table.horizontalHeaderItem(COL_META).setText("시간 / 예상 비용" if show else "시간")
        for j in self._jobs.values():
            self.update_job(j)

    def set_jobs(self, jobs: list[Job]) -> None:
        """전체 목록으로 표를 다시 그린다 (순서 변경/삭제 후)."""
        selected = {self._job_at(r).id for r in self._selected_rows() if self._job_at(r)}
        self.table.setRowCount(0)
        self._rows.clear()
        self._jobs.clear()
        for j in jobs:
            self._append_row(j)
        for r in range(self.table.rowCount()):
            j = self._job_at(r)
            if j and j.id in selected:
                self.table.selectRow(r)
        self.stack.setCurrentIndex(1 if jobs else 0)
        self.title.setText(f"영상 대기열  ({len(jobs)}개)" if jobs else "영상 대기열")
        self._refresh_buttons()

    def _append_row(self, job: Job) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c in range(len(COLS)):
            it = QTableWidgetItem("")
            if c == COL_NAME:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, c, it)
        self._rows[job.id] = r
        self._jobs[job.id] = job
        self.update_job(job)

    def update_job(self, job: Job) -> None:
        r = self._rows.get(job.id)
        if r is None:
            return
        self._jobs[job.id] = job
        info = job.info
        self.table.item(r, COL_NAME).setText(job.input_path.name)
        self.table.item(r, COL_NAME).setToolTip(str(job.input_path))
        self.table.item(r, COL_SRC).setText(info.resolution_text if info else "…")
        self.table.item(r, COL_DUR).setText(info.duration_text if info else "…")
        self.table.item(r, COL_OUT).setText(info.upscaled_resolution_text(job.scale) if info else "…")
        st = self.table.item(r, COL_STATUS)
        st.setText(job.status_label)
        st.setForeground(QColor(_STATUS_COLORS.get(job.status, "#374151")))
        st.setToolTip(job.error_message if job.status in (JobStatus.FAILED, JobStatus.INTERRUPTED) else
                      (job.progress.detail if job.status == JobStatus.RUNNING else ""))
        meta = ""
        if job.status == JobStatus.DONE:
            meta = _fmt_sec(job.elapsed_sec) + (f" · {job.note}" if job.note else "")
        elif job.status == JobStatus.RUNNING:
            meta = _fmt_sec(job.elapsed_sec)
        elif job.status == JobStatus.FAILED:
            meta = job.error_message[:60] + ("…" if len(job.error_message) > 60 else "")
        if self._show_cost and job.estimated_cost_usd is not None and job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            meta = (meta + "  " if meta else "") + f"약 {format_usd(job.estimated_cost_usd)}"
        self.table.item(r, COL_META).setText(meta)
        tip = job.error_message if job.status == JobStatus.FAILED else ""
        if job.status == JobStatus.DONE and job.output_path:
            tip = f"결과: {job.output_path}"
            st.setToolTip(tip)
        self.table.item(r, COL_META).setToolTip(tip)
        self._refresh_buttons()

    # ------------------------------------------------------------ 선택/편집
    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _job_at(self, row: int) -> Job | None:
        for jid, r in self._rows.items():
            if r == row:
                return self._jobs.get(jid)
        return None

    def selected_jobs(self) -> list[Job]:
        return [j for j in (self._job_at(r) for r in self._selected_rows()) if j]

    def checked_jobs(self) -> list[Job]:
        out = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_NAME).checkState() == Qt.CheckState.Checked:
                j = self._job_at(r)
                if j:
                    out.append(j)
        return out

    def _remove_selected(self) -> None:
        jobs = self.selected_jobs() or self.checked_jobs()
        jobs = [j for j in jobs if j.status != JobStatus.RUNNING]
        if jobs:
            self.removeRequested.emit(jobs)

    def _move(self, delta: int) -> None:
        jobs = self.selected_jobs()
        if len(jobs) == 1 and jobs[0].status == JobStatus.PENDING:
            self.moveRequested.emit(jobs[0], delta)

    def _double_clicked(self, item: QTableWidgetItem) -> None:
        j = self._job_at(item.row())
        if j and j.status == JobStatus.DONE and j.output_path:
            self.openResultRequested.emit(j)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        running = getattr(self, "_running", False)
        jobs = list(self._jobs.values())
        sel = self.selected_jobs()
        self.remove_btn.setEnabled(any(j.status != JobStatus.RUNNING for j in (sel or jobs)) and bool(jobs))
        self.clear_btn.setEnabled(bool(jobs) and not running)
        movable = len(sel) == 1 and sel[0].status == JobStatus.PENDING
        self.up_btn.setEnabled(movable)
        self.down_btn.setEnabled(movable)
        self.retry_btn.setEnabled(any(j.status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED) for j in jobs))
        self.add_btn.setEnabled(True)
