"""수강생 확인 창 — 이 PC 에 확인된 등록이 없으면 UPCON 을 켤 때 뜬다 (core/license.py).

이름 + 전화번호를 넣으면 강사의 수강생 명단(구글 시트)과 대조해 바로 쓴다(PC 2대까지, 시트가 센다).
이미 등록된 PC 는 창 없이 켜지고, 뒤에서 시트에 다시 묻는다 — 명단에서 빠졌으면 그때 이 창이 뜨고,
통과하지 못하면 앱을 닫는다.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from upcon import APP_NAME
from upcon.core import license as lic


class LicenseGateDialog(QDialog):
    def __init__(self, manager: lic.LicenseManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(f"{APP_NAME} 수강생 확인")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        intro = QLabel(f"{APP_NAME} 는 수강생만 쓸 수 있습니다.\n"
                       f"수강 신청 때 쓴 이름과 전화번호를 넣고 '확인'을 누르세요. "
                       f"한 사람당 PC {lic.STUDENT_MAX_PCS}대까지 쓸 수 있습니다.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("홍길동")
        form.addRow("이름", self.name)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("010-1234-5678")
        self.phone.returnPressed.connect(self.activate)
        form.addRow("전화번호", self.phone)
        lay.addLayout(form)
        st = manager.state
        self.msg = QLabel(st.message if st.status and st.message else "")
        self.msg.setWordWrap(True)
        self.msg.setProperty("class", "hint")
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        self.ok_btn = QPushButton("확인")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.clicked.connect(self.activate)
        quit_btn = QPushButton("종료")
        quit_btn.clicked.connect(self.reject)
        row.addWidget(self.ok_btn, 1)
        row.addWidget(quit_btn)
        lay.addLayout(row)

    def activate(self) -> None:
        self.msg.setText("확인 중…")
        self.ok_btn.setEnabled(False)
        self.repaint()
        try:
            self.manager.activate(self.name.text(), self.phone.text())
        except lic.LicenseError as e:
            self.msg.setText(str(e))
            return
        except Exception as e:  # noqa: BLE001 — 인터넷 연결 등
            self.msg.setText(f"확인 서버에 연결하지 못했습니다. 인터넷 연결을 확인하세요. ({type(e).__name__})")
            return
        finally:
            self.ok_btn.setEnabled(True)
        self.accept()


def license_gate(manager: lic.LicenseManager, parent=None) -> bool:
    """쓸 수 있으면 True. 이미 확인된 PC 면 창 없이 통과하고 뒤에서 다시 확인한다."""
    if manager.allowed():
        if lic.ENFORCED:
            recheck_in_background(manager, parent)
        return True
    return LicenseGateDialog(manager, parent).exec() == QDialog.DialogCode.Accepted


class _Recheck(QObject):
    done = Signal()


def recheck_in_background(manager: lic.LicenseManager, parent=None, *, block: bool = False) -> None:
    """시트에 다시 묻고(백그라운드), 더 이상 쓸 수 없으면 확인 창 → 통과 못 하면 앱 종료. block=True 는 테스트용."""
    sig = _Recheck(parent)

    def after() -> None:
        if not manager.allowed() and LicenseGateDialog(manager, parent).exec() != QDialog.DialogCode.Accepted:
            QApplication.instance().quit()

    def work() -> None:
        try:
            manager.refresh()
        except Exception:  # noqa: BLE001 — 확인 실패는 유예(refresh 가 처리)
            pass
        sig.done.emit()

    sig.done.connect(after, Qt.ConnectionType.QueuedConnection)
    if block:
        work()
        QApplication.processEvents()
    else:
        threading.Thread(target=work, name="upcon-roster-check", daemon=True).start()
