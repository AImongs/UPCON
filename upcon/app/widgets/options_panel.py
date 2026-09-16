"""처리 방식(자동/내 PC/클라우드) 과 업스케일 배율 선택 카드."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget,
)

from upcon.core.constants import (
    DEFAULT_SCALE, PROCESS_MODE_HINTS, PROCESS_MODE_LABELS, SCALE_OPTIONS, ProcessMode,
)


class OptionsPanel(QFrame):
    modeChanged = Signal(object)   # ProcessMode
    scaleChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(6)

        # 한 줄: [처리 방식] ○자동 ○내 PC ○클라우드    |    [배율] ●2×
        row = QHBoxLayout()
        row.setSpacing(18)
        t1 = QLabel("처리 방식")
        t1.setProperty("class", "cardTitle")
        row.addWidget(t1)
        self.mode_group = QButtonGroup(self)
        self._mode_buttons: dict[ProcessMode, QRadioButton] = {}
        for mode in ProcessMode:
            rb = QRadioButton(PROCESS_MODE_LABELS[mode])
            rb.setProperty("mode", mode.value)
            self.mode_group.addButton(rb)
            self._mode_buttons[mode] = rb
            row.addWidget(rb)
        row.addSpacing(24)
        t2 = QLabel("업스케일 배율")
        t2.setProperty("class", "cardTitle")
        row.addWidget(t2)
        self.scale_group = QButtonGroup(self)
        self._scale_buttons: dict[int, QRadioButton] = {}
        for opt in SCALE_OPTIONS:
            rb = QRadioButton(opt.label)
            rb.setEnabled(opt.enabled)
            rb.setProperty("scale", opt.factor)
            self.scale_group.addButton(rb)
            self._scale_buttons[opt.factor] = rb
            row.addWidget(rb)
        row.addStretch(1)
        outer.addLayout(row)

        self.mode_hint = QLabel(PROCESS_MODE_HINTS[ProcessMode.AUTO])
        self.mode_hint.setProperty("class", "hint")
        self.mode_hint.setWordWrap(True)
        outer.addWidget(self.mode_hint)

        # 환경 감지 결과 (예: "내 PC GPU(RTX 5060)로 무료 처리합니다.")
        self.env_hint = QLabel("PC 환경 확인 중...")
        self.env_hint.setProperty("class", "envHint")
        self.env_hint.setWordWrap(True)
        outer.addWidget(self.env_hint)

        self.mode_group.buttonToggled.connect(self._on_mode_toggled)
        self.scale_group.buttonToggled.connect(self._on_scale_toggled)

        self.set_mode(ProcessMode.AUTO)
        self.set_scale(DEFAULT_SCALE)

    # ---- 상태 ----
    def mode(self) -> ProcessMode:
        b = self.mode_group.checkedButton()
        return ProcessMode(b.property("mode")) if b else ProcessMode.AUTO

    def set_mode(self, mode: ProcessMode) -> None:
        self._mode_buttons[mode].setChecked(True)

    def scale(self) -> int:
        b = self.scale_group.checkedButton()
        return int(b.property("scale")) if b else DEFAULT_SCALE

    def set_scale(self, factor: int) -> None:
        rb = self._scale_buttons.get(factor)
        if rb and rb.isEnabled():
            rb.setChecked(True)

    def set_env_hint(self, text: str) -> None:
        self.env_hint.setText(text.splitlines()[0] if text else "")

    def _on_mode_toggled(self, button, checked: bool) -> None:
        if checked:
            mode = ProcessMode(button.property("mode"))
            self.mode_hint.setText(PROCESS_MODE_HINTS[mode])
            self.modeChanged.emit(mode)

    def _on_scale_toggled(self, button, checked: bool) -> None:
        if checked:
            self.scaleChanged.emit(int(button.property("scale")))
