"""UPCON 진입점: `python -m upcon`"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from upcon import APP_NAME, APP_VERSION
from upcon.app.main_window import MainWindow
from upcon.app.single_instance import SingleInstance
from upcon.core.config import AppConfig
from upcon.core.logging_setup import setup_logging
from upcon.core.paths import app_icon_file, resources_dir, user_data_dir


def _load_stylesheet() -> str:
    qss = resources_dir() / "styles.qss"
    try:
        return qss.read_text(encoding="utf-8")
    except OSError:
        return ""


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    setup_logging()
    logging.getLogger(__name__).info("%s v%s starting", APP_NAME, APP_VERSION)

    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(_load_stylesheet())
    icon = app_icon_file()          # 아이콘 파일이 없으면 기본 아이콘으로 동작한다
    if icon is not None:
        app.setWindowIcon(QIcon(str(icon)))

    window = MainWindow(AppConfig.load())
    return app, window


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # 같은 데이터 폴더를 쓰는 UPCON 이 이미 떠 있으면 그 창을 앞으로 보내고 조용히 끝낸다 (queue.json 동시 접근 방지).
    guard = SingleInstance(user_data_dir())
    if guard.already_running:
        return 0
    app, window = create_app()
    guard.activated.connect(window.bring_to_front)
    window.show()
    try:
        return app.exec()
    finally:
        guard.close()


if __name__ == "__main__":
    sys.exit(main())
