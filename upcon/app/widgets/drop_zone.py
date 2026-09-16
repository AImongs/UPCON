"""영상 드래그 앤 드롭 영역 + 파일 선택 버튼."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QPushButton, QVBoxLayout

from upcon.core.constants import SUPPORTED_EXTENSIONS


def _is_video(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS


class DropZone(QFrame):
    """파일이 드롭되거나 선택되면 filesSelected(list[Path]) 를 보낸다."""

    filesSelected = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(170)
        self.setProperty("hover", False)
        self.setProperty("loaded", False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon = QLabel("🎬")
        self.icon.setObjectName("dropIcon")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = QLabel("영상을 여기에 드래그하세요")
        self.title.setObjectName("dropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.hint = QLabel("MP4, MOV, MKV 등 대부분의 영상 파일을 지원합니다")
        self.hint.setObjectName("dropHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.button = QPushButton("영상 파일 선택")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.open_dialog)

        lay.addWidget(self.icon)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addSpacing(8)
        lay.addWidget(self.button, 0, Qt.AlignmentFlag.AlignCenter)

    # ---- 파일 선택 ----
    def open_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(
            self, "영상 파일 선택", "", f"영상 파일 ({exts});;모든 파일 (*.*)"
        )
        if files:
            self.filesSelected.emit([Path(f) for f in files])

    def set_loaded(self, loaded: bool) -> None:
        self.setProperty("loaded", loaded)
        self.title.setText("다른 영상을 드래그하거나 선택하세요" if loaded else "영상을 여기에 드래그하세요")
        self._repolish()

    # ---- 드래그 앤 드롭 ----
    def _paths_from_event(self, event) -> list[Path]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        paths = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()]
        return [p for p in paths if _is_video(p)]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._paths_from_event(event):
            event.acceptProposedAction()
            self.setProperty("hover", True)
            self._repolish()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.setProperty("hover", False)
        self._repolish()

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("hover", False)
        self._repolish()
        paths = self._paths_from_event(event)
        if paths:
            event.acceptProposedAction()
            self.filesSelected.emit(paths)

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
