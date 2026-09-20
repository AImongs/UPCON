"""설정 → 클라우드 업스케일 (fal.ai 계정 연결)."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from upcon.core import credentials, pricing
from upcon.core.config import AppConfig
from upcon.providers.fal_base import ConnectionTestResult, FalApi

log = logging.getLogger(__name__)
FAL_KEYS_URL = "https://fal.ai/dashboard/keys"


class _Bridge(QObject):
    done = Signal(object)  # ConnectionTestResult


class CloudSettingsDialog(QDialog):
    def __init__(self, config: AppConfig, endpoint: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.endpoint = endpoint
        self.setWindowTitle("설정 — 클라우드 업스케일")
        self.setMinimumWidth(520)
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_test_done)
        self._tested_ok_key: str | None = None
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        title = QLabel("fal.ai 계정 연결")
        title.setObjectName("dialogTitle")
        lay.addWidget(title)
        desc = QLabel(
            "내 PC 그래픽카드 대신 인터넷의 GPU(fal.ai · ByteDance PRO)로 처리하는 <b>선택 기능</b>입니다. "
            "AI 영상 고화질 복원 · 처리시간이 오래 걸릴 수 있습니다. 내 PC로 처리할 수 있으면 연결하지 않아도 됩니다.<br>"
            "클라우드 처리는 <b>내 fal.ai 계정</b>으로 실행되며 비용도 내 계정에서 청구됩니다.<br>"
            f"API Key는 <a href='{FAL_KEYS_URL}'>fal.ai 대시보드 → Keys</a> 에서 만들 수 있습니다. "
            "입력한 키는 이 PC에 안전하게 저장됩니다."
        )
        desc.setOpenExternalLinks(True)
        desc.setWordWrap(True)
        desc.setProperty("class", "hint")
        lay.addWidget(desc)

        form = QFormLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._has_existing = credentials.has_fal_key()
        self.key_edit.setPlaceholderText(
            "저장된 키가 있습니다 · 바꾸려면 새 API Key 입력" if self._has_existing else "fal.ai API Key 붙여넣기")
        self.show_btn = QPushButton("표시")
        self.show_btn.setCheckable(True)
        self.show_btn.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        row = QHBoxLayout()
        row.addWidget(self.key_edit, 1)
        row.addWidget(self.show_btn)
        form.addRow("API Key", row)
        lay.addLayout(form)

        btns = QHBoxLayout()
        self.test_btn = QPushButton("연결 테스트")
        self.test_btn.clicked.connect(self._test)
        self.remove_btn = QPushButton("저장된 키 삭제")
        self.remove_btn.setEnabled(self._has_existing)
        self.remove_btn.clicked.connect(self._remove)
        btns.addWidget(self.test_btn)
        btns.addWidget(self.remove_btn)
        btns.addStretch(1)
        lay.addLayout(btns)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.status)
        if self._has_existing:
            self._set_status(True, "저장된 API Key가 있습니다. (연결 테스트로 확인 가능)")
        else:
            self._set_status(None, "아직 연결된 계정이 없습니다.")

        self.price_label = QLabel(self._price_text())
        self.price_label.setProperty("class", "hint")
        self.price_label.setWordWrap(True)
        self.price_label.setToolTip(self._price_tooltip)
        lay.addWidget(self.price_label)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    # ---- 상태 표시 ----
    def _set_status(self, ok: bool | None, text: str) -> None:
        icon = "✓ " if ok else ("✕ " if ok is False else "")
        color = "#15803d" if ok else ("#b91c1c" if ok is False else "#374151")
        self.status.setText(f"<span style='color:{color};font-weight:600'>{icon}{text}</span>")

    def _price_text(self) -> str:
        """기본 화면은 한 줄로: 요금이 어떻게 정해지는지. 계산식/조회 시각은 툴팁.

        ByteDance는 FlashVSR과 가격 구조 자체가 다르다(메가픽셀 정액이 아니라 해상도 tier ×
        fps 구간별 초당 단가 × PRO 10배) — pricing.DOCUMENTED_UNIT_PRICES_USD_PER_MP 에는
        이 endpoint가 없으므로 별도 분기로 처리한다 (KeyError 방지)."""
        if self.endpoint == pricing.BYTEDANCE_ENDPOINT:
            self._price_tooltip = (
                "요금 = (해상도 tier(1080p/2K/4K) × fps 구간별 초당 단가) × PRO 10배 × 영상 길이(초).\n"
                "1080p 기준 standard 요율 $0.0072/초(≤30fps), $0.0144/초(60fps) — PRO는 그 10배.\n"
                f"공식 문서 {pricing.BYTEDANCE_PRICE_DOC_DATE} 기준. 31~59fps·61~120fps 구간은 정확한 "
                "요율이 공개되어 있지 않아, 알려진 가장 가까운 요율로 최소 예상치만 계산합니다.\n"
                "실제 청구액은 fal.ai가 결정하며 예상값과 다를 수 있습니다."
            )
            return ("요금은 출력 해상도·프레임레이트·영상 길이에 따라 계산되며(ByteDance PRO), "
                    "시작 전에 예상 비용을 보여 드립니다. 실제 청구액은 예상과 다를 수 있습니다.")
        p = self.config.cloud_unit_prices.get(self.endpoint)
        if p:
            src = f"fal API 조회 {self.config.cloud_price_checked_at[:16]}"
        else:
            p = pricing.DOCUMENTED_UNIT_PRICES_USD_PER_MP[self.endpoint]
            src = f"공식 문서 {pricing.PRICE_DOC_DATE} 기준"
        self._price_tooltip = (f"단가 ${p:g} / 메가픽셀 ({src}).\n"
                               "예상 비용 = 출력 가로 × 세로 × 프레임 수 ÷ 1,000,000 × 단가.\n"
                               "연결 테스트 시 최신 단가를 조회합니다.")
        return (f"요금은 영상 해상도와 길이에 따라 계산되며(단가 ${p:g} / 메가픽셀), 시작 전에 예상 비용을 보여 드립니다. "
                "실제 청구액은 예상과 다를 수 있습니다.")

    # ---- 동작 ----
    def _test(self) -> None:
        """입력창이 비어 있고 이미 저장된 키가 있으면, 화면에 표시하지 않은 채 그 키로 테스트한다."""
        key = self.key_edit.text().strip() or (credentials.get_fal_key() if self._has_existing else "")
        if not key:
            self._set_status(False, "API Key를 입력해 주세요.")
            return
        self.test_btn.setEnabled(False)
        self._set_status(None, "연결 확인 중...")

        def work():
            res = FalApi(key=key).test_connection(self.endpoint)
            self._bridge.done.emit((key, res))
        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, payload) -> None:
        key, res = payload
        res: ConnectionTestResult
        self.test_btn.setEnabled(True)
        if res.ok:
            self._tested_ok_key = key
            self._set_status(True, "클라우드 업스케일 연결 완료")
            if res.unit_price_usd:
                self.config.cloud_unit_prices[self.endpoint] = res.unit_price_usd
                self.config.cloud_price_checked_at = datetime.now(timezone.utc).isoformat(timespec="minutes")
                self.price_label.setText(self._price_text())
                self.price_label.setToolTip(self._price_tooltip)
        else:
            self._tested_ok_key = None
            self._set_status(False, res.message)

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            if self._has_existing:
                self.accept()   # 입력 없이 저장 → 기존 키 그대로 유지하고 닫기
                return
            self._set_status(False, "API Key를 입력해 주세요.")
            return
        try:
            credentials.set_fal_key(key)
        except credentials.CredentialStoreError as e:
            log.warning("keyring save failed: %s", e.detail)
            self._set_status(False, e.user_message)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("keyring save failed (unexpected): %s: %s", type(e).__name__, str(e)[:160])
            self._set_status(False, f"저장 중 예상하지 못한 오류가 발생했습니다. ({type(e).__name__})")
            return
        self._has_existing = True
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("저장된 키가 있습니다 · 바꾸려면 새 API Key 입력")
        self._set_status(True, "API Key를 안전하게 저장했습니다.")
        self.config.cloud_provider = "fal"
        self.config.save()
        self.remove_btn.setEnabled(True)
        self.accept()

    def _remove(self) -> None:
        credentials.delete_fal_key()
        self._has_existing = False
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("fal.ai API Key 붙여넣기")
        self.remove_btn.setEnabled(False)
        self._set_status(None, "저장된 API Key를 삭제했습니다.")
