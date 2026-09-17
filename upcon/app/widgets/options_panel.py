"""처리 방식(자동/내 PC/클라우드) 과 출력 해상도(2×/1080p/4K) 선택 카드."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget,
)

from upcon.core.constants import (
    DEFAULT_OUTPUT_MODE, OUTPUT_MODE_HINTS, OUTPUT_MODE_LABELS, PROCESS_MODE_HINTS, PROCESS_MODE_LABELS,
    OutputMode, ProcessMode,
)


class OptionsPanel(QFrame):
    modeChanged = Signal(object)         # ProcessMode
    outputModeChanged = Signal(object)   # OutputMode

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("class", "card")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 12, 20, 12)
        outer.setSpacing(6)

        # 한 줄: [처리 방식] ○자동 ○내 PC ○클라우드    |    [출력 해상도] ●2× ○1080p ○4K
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
        t2 = QLabel("출력 해상도")
        t2.setProperty("class", "cardTitle")
        row.addWidget(t2)
        self.output_mode_group = QButtonGroup(self)
        self._output_mode_buttons: dict[OutputMode, QRadioButton] = {}
        for m in OutputMode:
            rb = QRadioButton(OUTPUT_MODE_LABELS[m])
            rb.setProperty("output_mode", m.value)
            rb.setToolTip(OUTPUT_MODE_HINTS[m])
            self.output_mode_group.addButton(rb)
            self._output_mode_buttons[m] = rb
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

        # 결과가 어디에 어떤 이름으로 저장되는지 — 처리 전에 미리 알려 준다 (MainWindow 가 설정에 맞춰 채운다)
        self.output_hint = QLabel("")
        self.output_hint.setProperty("class", "hint")
        self.output_hint.setWordWrap(True)
        self.output_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.output_hint)

        self.mode_group.buttonToggled.connect(self._on_mode_toggled)
        self.output_mode_group.buttonToggled.connect(self._on_output_mode_toggled)

        self.set_mode(ProcessMode.AUTO)
        self.set_output_mode(DEFAULT_OUTPUT_MODE)

    # ---- 상태 ----
    def mode(self) -> ProcessMode:
        b = self.mode_group.checkedButton()
        return ProcessMode(b.property("mode")) if b else ProcessMode.AUTO

    def set_mode(self, mode: ProcessMode) -> None:
        self._mode_buttons[mode].setChecked(True)

    def output_mode(self) -> OutputMode:
        b = self.output_mode_group.checkedButton()
        return OutputMode(b.property("output_mode")) if b else DEFAULT_OUTPUT_MODE

    def set_output_mode(self, mode: OutputMode) -> None:
        rb = self._output_mode_buttons.get(mode)
        if rb and rb.isEnabled():
            rb.setChecked(True)

    def set_env_hint(self, text: str) -> None:
        self.env_hint.setText(text.splitlines()[0] if text else "")

    def set_output_hint(self, text: str, tooltip: str = "") -> None:
        self.output_hint.setText(text)
        self.output_hint.setToolTip(tooltip or text)

    def _on_mode_toggled(self, button, checked: bool) -> None:
        if checked:
            mode = ProcessMode(button.property("mode"))
            self.mode_hint.setText(PROCESS_MODE_HINTS[mode])
            self.modeChanged.emit(mode)

    def _on_output_mode_toggled(self, button, checked: bool) -> None:
        if checked:
            self.outputModeChanged.emit(OutputMode(button.property("output_mode")))
