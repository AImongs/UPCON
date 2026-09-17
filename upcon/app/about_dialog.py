"""정보(About) 화면 + 제3자 라이선스 읽기 창.

- 버전 표기는 upcon.version 한 곳에서 온다.
- 라이선스 전문은 배포본에 동봉된 docs/THIRD_PARTY_NOTICES.md 를 앱 내부 읽기 전용 창으로 보여준다.
  (외부 브라우저가 없어도 읽을 수 있어야 한다. '파일 위치 열기'는 보조 수단일 뿐이다.)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from upcon import APP_DESCRIPTION, APP_NAME, APP_VERSION, COPYRIGHT
from upcon import platform as plat
from upcon.core.paths import notices_file

log = logging.getLogger(__name__)

# 사용자에게 보여줄 핵심 고지. 전문은 THIRD_PARTY_NOTICES.md.
# 주의: 아직 확정되지 않은 Corresponding Source URL 을 여기에 적지 않는다.
# STEP MAC-1: macOS 빌드에는 FFmpeg 실행파일도 Real-ESRGAN weight 도 동봉하지 않으므로
# (Windows 배포 정책은 그대로, 동봉 여부만 실제와 다르게 말하면 안 된다) 문구를 나눈다.
if plat.IS_MACOS:
    THIRD_PARTY_SUMMARY = (
        "이 macOS 빌드에는 FFmpeg 실행파일과 Real-ESRGAN(ncnn) 모델·실행기가 동봉되어 있지 않습니다. "
        "각 구성요소는 별도의 제3자 소프트웨어이며, 저작권·라이선스 전문은 아래 "
        "'제3자 라이선스 전문'에서 확인할 수 있습니다.\n\n"
        "• FFmpeg — 이 macOS 빌드는 동봉하지 않고 시스템에 설치된 FFmpeg(예: Homebrew)를 사용합니다. "
        "FFmpeg 는 UPCON 과 별개의 제3자 소프트웨어이며 빌드 구성에 따라 GPLv3 등의 라이선스를 따릅니다.\n"
        "• Real-ESRGAN / Real-ESRGAN-ncnn-vulkan(ncnn 기반) — 이 macOS 빌드에는 아직 포함되어 있지 않습니다. "
        "내 PC GPU 업스케일 대신 클라우드 업스케일(fal.ai)만 지원합니다.\n"
        "• Qt / PySide6 — 사용자 인터페이스 (LGPLv3)\n"
        "• 그 밖의 오픈소스 구성요소 — 전문 참조"
    )
else:
    THIRD_PARTY_SUMMARY = (
        "UPCON 은 아래 제3자 소프트웨어를 포함합니다. 각 구성요소는 별도의 제3자 소프트웨어이며, "
        "저작권·라이선스 전문은 아래 '제3자 라이선스 전문'에서 확인할 수 있습니다.\n\n"
        "• FFmpeg — UPCON 에 동봉된 FFmpeg 빌드는 GPLv3 라이선스로 배포됩니다. "
        "FFmpeg 는 UPCON 과 별개의 제3자 구성요소이며, 별도 실행 파일로 포함되어 있습니다.\n"
        "• Real-ESRGAN / Real-ESRGAN-ncnn-vulkan — 업스케일 AI 모델 및 실행기 (BSD-3-Clause / MIT)\n"
        "• ncnn — 신경망 추론 라이브러리 (BSD-3-Clause, Tencent)\n"
        "• Qt / PySide6 — 사용자 인터페이스 (LGPLv3)\n"
        "• 그 밖의 오픈소스 구성요소 — 전문 참조"
    )


class NoticesDialog(QDialog):
    """THIRD_PARTY_NOTICES.md 읽기 전용 뷰어."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — 제3자 라이선스 전문")
        self.resize(820, 640)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setStyleSheet("font-family: Consolas, 'D2Coding', monospace; font-size: 12px;")
        self._path = notices_file()
        view.setPlainText(self._read())
        lay.addWidget(view, 1)

        row = QHBoxLayout()
        self.open_btn = QPushButton("파일 위치 열기")
        self.open_btn.setEnabled(self._path.is_file())
        self.open_btn.clicked.connect(self._open_location)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        box.rejected.connect(self.reject)
        row.addWidget(box)
        lay.addLayout(row)

    def _read(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("notices read failed: %s", e)
            return (f"제3자 라이선스 문서를 열지 못했습니다.\n\n"
                    f"문서 위치: {self._path}\n\n"
                    "프로그램을 다시 설치하면 복구됩니다.")

    def _open_location(self) -> None:
        if self._path.is_file():
            plat.reveal_in_file_manager(self._path)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} 정보")
        self.setMinimumWidth(560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(6)

        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 30px; font-weight: 800; letter-spacing: 2px; color: #111827;")
        lay.addWidget(name)

        desc = QLabel(APP_DESCRIPTION)
        desc.setStyleSheet("font-size: 13px; color: #6b7280; letter-spacing: 1px;")
        lay.addWidget(desc)

        ver = QLabel(f"버전 {APP_VERSION}")
        ver.setStyleSheet("font-size: 13px; color: #374151; margin-top: 8px;")
        lay.addWidget(ver)

        cr = QLabel(COPYRIGHT)
        cr.setStyleSheet("font-size: 12px; color: #6b7280;")
        lay.addWidget(cr)

        lay.addSpacing(12)
        head = QLabel("제3자 소프트웨어")
        head.setStyleSheet("font-size: 12px; font-weight: 700; color: #6b7280; letter-spacing: 1px;")
        lay.addWidget(head)

        body = QLabel(THIRD_PARTY_SUMMARY)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet("font-size: 12px; color: #374151;")
        lay.addWidget(body)

        lay.addSpacing(14)
        row = QHBoxLayout()
        self.licenses_btn = QPushButton("제3자 라이선스 전문")
        self.licenses_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.licenses_btn.clicked.connect(self.show_notices)
        row.addWidget(self.licenses_btn)
        row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        box.rejected.connect(self.reject)
        row.addWidget(box)
        lay.addLayout(row)

    def show_notices(self) -> None:
        NoticesDialog(self).exec()
