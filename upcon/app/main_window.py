"""UPCON 메인 창 — 배치 대기열."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from upcon import APP_NAME, APP_TAGLINE, APP_VERSION
from upcon.app.cloud_settings import CloudSettingsDialog
from upcon.app.controller import Controller
from upcon.app.widgets.options_panel import OptionsPanel
from upcon.app.widgets.progress_panel import ProgressPanel
from upcon.app.widgets.queue_panel import QueuePanel
from upcon.core.config import AppConfig
from upcon.core.constants import ProcessMode
from upcon.core.env import SystemEnv
from upcon.core.jobs import Job, JobStatus, Phase
from upcon.core.pricing import format_krw, format_usd
from upcon.core.router import Decision

log = logging.getLogger(__name__)


def _fmt_sec(s: float) -> str:
    s = int(s)
    return f"{s // 60}분 {s % 60}초" if s >= 60 else f"{s}초"


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.controller = Controller(config, self)
        self.controller.jobUpdated.connect(self._on_job_updated)
        self.controller.batchEvent.connect(self._on_batch_event)
        self.controller.envReady.connect(self._on_env_ready)

        self.last_decision: Decision | None = None
        self.last_output: Path | None = None
        self.env_message = "PC 환경 확인 중..."
        self._batch_running = False

        self.setWindowTitle(f"{APP_NAME} — AI Video Upscaler")
        self.resize(1000, 880)
        self.setMinimumSize(820, 680)
        self._build_ui()
        self._apply_config()
        self.controller.detect_env_async()
        # 이전 대기열 복원
        n = self.controller.restore_queue()
        self._refresh_table()
        if n:
            self.progress.set_idle("이전 대기열을 복원했습니다", f"{n}개 항목 (대기/실패/중단됨)")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 20, 28, 22)
        lay.setSpacing(12)

        # 헤더
        header = QHBoxLayout()
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        tagline = QLabel(APP_TAGLINE)
        tagline.setObjectName("tagline")
        brand_box.addWidget(brand)
        brand_box.addWidget(tagline)
        header.addLayout(brand_box)
        header.addStretch(1)
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("settingsButton")
        self.settings_btn.setText("⚙  설정")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(header)

        # 대기열
        self.queue = QueuePanel()
        self.queue.filesDropped.connect(self.add_files)
        self.queue.removeRequested.connect(self._remove_jobs)
        self.queue.clearRequested.connect(self._clear_jobs)
        self.queue.moveRequested.connect(self._move_job)
        self.queue.retryRequested.connect(self._retry_failed)
        self.queue.openResultRequested.connect(self._open_result_of)
        lay.addWidget(self.queue, 1)

        # 옵션
        self.options = OptionsPanel()
        self.options.modeChanged.connect(self._on_mode_changed)
        self.options.scaleChanged.connect(self._on_scale_changed)
        lay.addWidget(self.options)

        # 버튼 줄
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("전체 업스케일 시작")
        self.start_btn.setObjectName("primary")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start_all)
        self.skip_btn = QPushButton("현재 파일 건너뛰기")
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self._on_skip)
        self.stop_btn = QPushButton("전체 중지")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._on_stop_all)
        self.open_btn = QPushButton("결과 폴더 열기")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._open_result_folder)
        btn_row.addWidget(self.start_btn, 1)
        btn_row.addWidget(self.skip_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addWidget(self.open_btn)
        lay.addLayout(btn_row)

        # 진행률
        self.progress = ProgressPanel()
        lay.addWidget(self.progress)

        self.cost_label = QLabel("")
        self.cost_label.setProperty("class", "hint")
        lay.addWidget(self.cost_label)
        self.statusBar().showMessage(f"{APP_NAME} v{APP_VERSION}  ·  {self.env_message}")

    def _apply_config(self) -> None:
        try:
            self.options.set_mode(ProcessMode(self.config.process_mode))
        except ValueError:
            self.options.set_mode(ProcessMode.AUTO)
        self.options.set_scale(self.config.scale)

    def _set_status_bar(self) -> None:
        self.statusBar().showMessage(f"{APP_NAME} v{APP_VERSION}  ·  {self.env_message}")

    # -------------------------------------------------------------- 환경
    def _on_env_ready(self, env: SystemEnv, decision: Decision) -> None:
        gpu = env.primary_gpu
        if decision.provider is not None and decision.provider.kind == "local":
            self.env_message = f"내 PC GPU: {gpu.display_name if gpu else ''} (사용 가능)"
        elif gpu is not None:
            self.env_message = f"내 PC GPU: {gpu.display_name} (AI 처리 불가)"
        else:
            self.env_message = "내 PC GPU: 없음"
        self._set_status_bar()
        self.last_decision = decision
        self._refresh_cost()

    # -------------------------------------------------------------- 대기열
    def add_files(self, paths: list[Path]) -> None:
        added, skipped = self.controller.add_files([Path(p) for p in paths], self.options.scale())
        log.info("queue add: %d added, %d skipped", added, skipped)
        self._refresh_table()
        if skipped and not added:
            self.progress.set_idle("추가된 파일이 없습니다", "이미 대기열에 있거나 지원하지 않는 파일입니다.")

    def _refresh_table(self) -> None:
        self.queue.set_jobs(list(self.controller.jobs.jobs))
        self._refresh_summary()
        self._refresh_cost()

    def _refresh_summary(self) -> None:
        s = self.controller.summary()
        self.progress.set_batch(s, self._batch_running)
        ready = s.pending > 0 and not self._batch_running
        self.start_btn.setEnabled(ready)
        self.start_btn.setText(f"전체 업스케일 시작 ({s.pending}개 대기)" if s.pending else "전체 업스케일 시작")
        self.open_btn.setVisible(s.done > 0)

    def _remove_jobs(self, jobs: list[Job]) -> None:
        self.controller.remove_jobs(jobs)
        self._refresh_table()

    def _clear_jobs(self) -> None:
        if self._batch_running:
            return
        if self.controller.jobs.jobs and QMessageBox.question(
                self, "전체 삭제", "대기열의 모든 항목을 삭제할까요? (결과 파일은 삭제되지 않습니다)") != QMessageBox.StandardButton.Yes:
            return
        self.controller.clear_jobs()
        self._refresh_table()

    def _move_job(self, job: Job, delta: int) -> None:
        if self.controller.move_job(job, delta):
            self._refresh_table()

    def _retry_failed(self) -> None:
        n = self.controller.retry_failed()
        self._refresh_table()
        if n:
            self.progress.set_idle(f"{n}개 항목을 다시 대기열에 넣었습니다", "전체 업스케일 시작을 누르세요.")

    # ------------------------------------------------------------ 옵션/비용
    def _on_mode_changed(self, mode: ProcessMode) -> None:
        self.config.process_mode = mode.value
        self.config.save()
        self._refresh_cost()

    def _on_scale_changed(self, scale: int) -> None:
        self.config.scale = scale
        self.config.save()
        for j in self.controller.jobs.jobs:
            if j.status == JobStatus.PENDING:
                j.scale = scale
        self._refresh_table()

    def _cloud_would_be_used(self) -> bool:
        mode = self.options.mode()
        if mode == ProcessMode.CLOUD:
            return True
        if mode == ProcessMode.LOCAL:
            return False
        d = self.last_decision
        return d is not None and d.provider is not None and d.provider.kind == "cloud"

    def _refresh_cost(self) -> None:
        cloud = self._cloud_would_be_used()
        self.queue.set_show_cost(cloud)
        total, n = self.controller.total_cloud_cost(self.options.scale())
        if cloud and n:
            text = f"클라우드 GPU 사용 시 전체 예상 비용: 약 {format_usd(total)} ({n}개)"
            if self.config.krw_per_usd > 0:
                text += f"  ≈ {format_krw(int(total * self.config.krw_per_usd))}"
            text += "  ·  실제 청구액은 예상과 다를 수 있습니다"
            self.cost_label.setText(text)
        else:
            self.cost_label.setText("")
        d = self._decision_for_selected_mode()
        if d is not None:
            hint = d.message.splitlines()[0]
            if cloud and n:
                hint += f" · 전체 예상 비용 약 {format_usd(total)}"
            self.options.set_env_hint(hint)

    def _decision_for_selected_mode(self) -> Decision | None:
        """안내 문구는 지금 선택한 처리 방식 기준으로 보여준다.
        (last_decision 은 '자동' 기준이라, 클라우드를 골라도 '내 PC GPU로 무료 처리' 로 보이던 문제)
        환경 감지 전에는 UI 를 막지 않기 위해 마지막 결과를 그대로 쓴다."""
        if self.controller.env is None:
            return self.last_decision
        try:
            return self.controller.decide(self.options.mode(), self.options.scale())
        except Exception as e:  # noqa: BLE001 - 안내 문구 때문에 UI 가 죽지 않게
            log.warning("hint decision failed: %s", e)
            return self.last_decision

    # ------------------------------------------------------------ 실행
    def _on_start_all(self) -> None:
        if self._batch_running:
            return
        s = self.controller.summary()
        if s.pending == 0:
            return
        scale = self.options.scale()
        decision = self.controller.decide(self.options.mode(), scale)
        if decision.provider is None:
            QMessageBox.warning(self, "업스케일을 시작할 수 없습니다", decision.message)
            return
        if decision.provider.kind == "cloud":
            total, n = self.controller.total_cloud_cost(scale)
            if not self._confirm_cloud(n, total, decision.message):
                return
        n = self.controller.start_all(decision.provider, decision.message, scale)
        log.info("batch start: %d files via %s", n, decision.provider.id)
        self._set_running_ui(True)
        self.progress.set_current("", "시작 중...", None, decision.message)

    def _confirm_cloud(self, n: int, total: float, why: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("클라우드 업스케일 확인")
        box.setIcon(QMessageBox.Icon.Question)
        krw = f" (약 {format_krw(int(total * self.config.krw_per_usd))})" if self.config.krw_per_usd > 0 else ""
        box.setText(
            f"<b>클라우드 GPU로 업스케일합니다.</b><br>{why}<br><br>"
            f"총 <b>{n}개</b> 영상<br><b>예상 비용: 약 {format_usd(total)}{krw}</b><br>"
            "<span style='color:#6b7280'>내 fal.ai 계정에서 청구됩니다. 실제 청구액은 예상과 다를 수 있습니다.</span>"
        )
        go = box.addButton("계속", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        return box.clickedButton() is go

    def _on_skip(self) -> None:
        self.skip_btn.setEnabled(False)
        self.controller.skip_current()
        QTimer.singleShot(1500, lambda: self.skip_btn.setEnabled(self._batch_running))

    def _on_stop_all(self) -> None:
        self.stop_btn.setEnabled(False)
        self.progress.set_current("", "전체 중지하는 중...", None, "현재 파일을 안전하게 중단하고 임시 파일을 정리합니다.")
        self.controller.stop_all()

    def _set_running_ui(self, running: bool) -> None:
        self._batch_running = running
        self.skip_btn.setVisible(running)
        self.skip_btn.setEnabled(running)
        self.stop_btn.setVisible(running)
        self.stop_btn.setEnabled(running)
        self.options.setEnabled(not running)
        self.queue.set_running(running)
        self._refresh_summary()

    def _on_batch_event(self, event: str) -> None:
        if event == "started":
            self._set_running_ui(True)
            return
        self._set_running_ui(False)
        s = self.controller.summary()
        self._refresh_table()
        if event == "finished":
            msg = f"{s.done}개 완료" + (f" / {s.failed}개 실패" if s.failed else "") + (f" / {s.cancelled}개 취소" if s.cancelled else "")
            self.progress.set_idle("전체 작업 완료", msg)
            if s.failed:
                QMessageBox.warning(self, "전체 작업 완료",
                                    f"{msg}\n실패한 항목은 목록에서 오류 내용을 확인하고 '실패 항목 다시 시도'를 누를 수 있습니다.")
        elif event == "stopped":
            self.progress.set_idle("전체 중지됨", f"대기 {s.pending}개가 남아 있습니다. '전체 업스케일 시작'으로 이어서 처리할 수 있습니다.")

    def _on_job_updated(self, job: Job) -> None:
        self.queue.update_job(job)
        if job.status == JobStatus.RUNNING:
            p = job.progress
            sub = p.detail
            if p.phase == Phase.UPSCALE and p.percent is not None:
                sub = f"{p.detail}  ·  경과 {_fmt_sec(job.elapsed_sec)}"
            elif p.phase in (Phase.UPLOAD, Phase.DOWNLOAD, Phase.QUEUE, Phase.CLOUD):
                sub = f"{p.detail}  ·  경과 {_fmt_sec(job.elapsed_sec)}"
            elif not sub:
                sub = job.provider_label
            self.progress.set_current(job.input_path.name, p.phase.value, p.percent, sub)
        elif job.status == JobStatus.DONE and job.output_path:
            self.last_output = job.output_path
            self.progress.set_current(job.input_path.name, "완료", 100,
                                      f"{job.output_path.name}  ·  {_fmt_sec(job.elapsed_sec)}" + (f"  ·  {job.note}" if job.note else ""))
        elif job.status == JobStatus.FAILED and job.finished_at:
            self.progress.set_current(job.input_path.name, "실패", 0, job.error_message)
        elif job.status == JobStatus.CANCELLED:
            self.progress.set_current(job.input_path.name, "취소됨", 0, job.note or "다음 파일로 넘어갑니다.")
        self._refresh_summary()

    # ------------------------------------------------------------ 결과
    def _open_result_of(self, job: Job) -> None:
        if job.output_path and job.output_path.exists():
            subprocess.Popen(["explorer", "/select,", str(job.output_path)])

    def _open_result_folder(self) -> None:
        sel = [j for j in self.queue.selected_jobs() if j.status == JobStatus.DONE and j.output_path]
        target = sel[0].output_path if sel else self.last_output
        if target and target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif target:
            os.startfile(str(target.parent))

    def _open_settings(self) -> None:
        dlg = CloudSettingsDialog(self.config, self.controller.cloud_endpoint(), self)
        dlg.exec()
        self.config.save()
        self.controller.detect_env_async()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._batch_running:
            box = QMessageBox(self)
            box.setWindowTitle("작업 진행 중")
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("현재 업스케일 작업이 진행 중입니다.\n종료하면 현재 작업이 취소됩니다.")
            keep = box.addButton("계속 작업", QMessageBox.ButtonRole.RejectRole)
            quit_btn = box.addButton("종료", QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() is not quit_btn:      # Esc/닫기 포함 → 안전하게 '계속 작업'
                event.ignore()
                return
        self.controller.shutdown()
        event.accept()
