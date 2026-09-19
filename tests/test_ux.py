"""UX 개선 1차 회귀 테스트 (첫 사용자 점검에서 나온 C2/M1/M2/M3/M4/M6/M7/M8/M10).

Qt 위젯은 offscreen 플랫폼으로 띄운다. 실제 사용자 설정(%LOCALAPPDATA%/UPCON)은 UPCON_DATA_DIR 로 격리한다
(conftest + 각 테스트의 tmp_path). fal.ai 호출 없음.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from upcon.core.config import AppConfig
from upcon.core.eta import EtaEstimator, format_eta
from upcon.core.jobs import BatchSummary, Job, JobManager, JobStatus, Phase, Progress
from upcon.core.probe import VideoInfo


def _info(path: Path, frames: int = 120) -> VideoInfo:
    return VideoInfo(path=path, width=854, height=480, fps=24, duration_sec=frames / 24, size_bytes=1000,
                     video_codec="h264", has_audio=True, nb_frames=frames)


def _job(tmp_path: Path, name: str, status: JobStatus = JobStatus.PENDING, frames: int = 120) -> Job:
    p = tmp_path / name
    if not p.exists():
        p.write_bytes(b"x")
    j = Job(input_path=p, scale=2, info=_info(p, frames), status=status)
    if status == JobStatus.DONE:
        out = tmp_path / f"{p.stem}_2x{p.suffix}"
        out.write_bytes(b"result")
        j.output_path = out
        j.started_at, j.finished_at = time.time() - 10, time.time()
    return j


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    """격리된 데이터 폴더 + 환경 감지/디스크 정리 없이 뜨는 MainWindow."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("UPCON_DATA_DIR", str(data))
    from upcon.app.controller import Controller
    monkeypatch.setattr(Controller, "detect_env_async", lambda self: None)
    from upcon.app.main_window import MainWindow
    w = MainWindow(AppConfig())
    yield w
    w.controller.shutdown()
    w.close()


def _load(win, jobs: list[Job]) -> None:
    for j in jobs:
        win.controller.jobs.add(j)
    win._refresh_table()


# ---------------------------------------------------------------- C2 / M3: 선택 삭제, 체크박스 없음
def test_remove_with_no_selection_removes_nothing(win, tmp_path):
    jobs = [_job(tmp_path, "a.mp4"), _job(tmp_path, "b.mp4", JobStatus.DONE)]
    _load(win, jobs)
    win.queue.table.clearSelection()
    assert not win.queue.remove_btn.isEnabled(), "선택이 없으면 '선택 항목 삭제' 는 꺼져 있어야 한다"
    win.queue._remove_selected()
    assert len(win.controller.jobs.jobs) == 2


def test_remove_only_selected_row(win, tmp_path):
    a, b, c = _job(tmp_path, "a.mp4"), _job(tmp_path, "b.mp4"), _job(tmp_path, "c.mp4")
    _load(win, [a, b, c])
    win.queue.table.selectRow(1)
    assert win.queue.remove_btn.isEnabled()
    win.queue.remove_btn.click()
    assert [j.input_path.name for j in win.controller.jobs.jobs] == ["a.mp4", "c.mp4"]


def test_removing_done_item_keeps_result_file(win, tmp_path):
    d = _job(tmp_path, "done.mp4", JobStatus.DONE)
    _load(win, [d])
    out = d.output_path
    assert out.exists()
    win.queue.table.selectRow(0)
    win.queue.remove_btn.click()
    assert win.controller.jobs.jobs == []
    assert out.exists() and out.read_bytes() == b"result", "목록에서 지워도 결과 영상은 남아야 한다"
    assert "삭제되지 않았습니다" in win.progress.cur_sub.text()


def test_rows_have_no_checkbox_and_start_targets_all_pending(win, tmp_path):
    from PySide6.QtCore import Qt
    jobs = [_job(tmp_path, "a.mp4"), _job(tmp_path, "b.mp4"), _job(tmp_path, "c.mp4")]
    _load(win, jobs)
    for r in range(win.queue.table.rowCount()):
        it = win.queue.table.item(r, 0)
        assert not (it.flags() & Qt.ItemFlag.ItemIsUserCheckable), "처리와 무관한 체크박스는 없어야 한다"
    win.queue.table.selectRow(0)                       # 행 선택은 처리 대상과 무관
    assert "3개 대기" in win.start_btn.text()
    n = win.controller.start_all(win.controller.local_ncnn, "test", 2)   # 실제 실행은 runner 가 하지만 여기서는 대상 수만 확인
    assert n == 3
    win.controller.stop_all()


# ---------------------------------------------------------------- M2: 파일 대화상자 시작 폴더
def test_start_dir_defaults_and_persistence(qapp, tmp_path, monkeypatch):
    from upcon.app import file_dialogs
    from upcon.core.paths import project_root

    d = file_dialogs.resolve_start_dir("")
    assert d.is_dir()
    assert not d.resolve().is_relative_to(project_root().resolve()), "설치/프로젝트 폴더에서 시작하면 안 된다"
    assert file_dialogs.resolve_start_dir(str(tmp_path / "없는폴더")) == d
    assert file_dialogs.resolve_start_dir(str(tmp_path)) == tmp_path
    assert file_dialogs.remember_dir([tmp_path / "x.mp4"]) == str(tmp_path)
    # 설치 폴더 안의 경로가 저장돼 있어도 거기서 시작하지 않는다
    inside = project_root() / "upcon"
    assert file_dialogs.resolve_start_dir(str(inside)) == d


def test_add_files_remembers_folder_in_isolated_config(win, tmp_path, monkeypatch):
    from upcon.app.controller import Controller
    monkeypatch.setattr(Controller, "_probe_async", lambda self, job: None)   # ffprobe 없이
    folder = tmp_path / "내 영상"
    folder.mkdir()
    clip = folder / "클립.mp4"
    clip.write_bytes(b"x")
    assert win.queue.start_dir == ""
    win.add_files([clip])
    assert win.config.last_open_dir == str(folder)
    assert win.queue.start_dir == str(folder) and win.queue.drop_zone.start_dir == str(folder)
    saved = AppConfig.load()                              # UPCON_DATA_DIR (tmp) 의 config.json
    assert saved.last_open_dir == str(folder)
    cfg_file = Path(os.environ["UPCON_DATA_DIR"]) / "config.json"
    assert cfg_file.exists() and "내 영상" in cfg_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------- M4: 중지 후 안내 = 실제 버튼
def test_stopped_hint_matches_enabled_buttons(win, tmp_path):
    c = _job(tmp_path, "c.mp4", JobStatus.CANCELLED)
    _load(win, [c])
    win._on_batch_event("stopped")
    hint = win.progress.cur_sub.text()
    assert not win.start_btn.isEnabled()
    assert "전체 업스케일 시작" not in hint, f"비활성 버튼을 안내하면 안 된다: {hint}"
    assert win.queue.retry_btn.isEnabled() and win.queue.retry_btn.text() == "취소된 항목 다시 시작"
    assert win.queue.retry_btn.text() in hint
    # 대기 항목이 남아 있으면 시작 버튼 안내가 들어간다
    _load(win, [_job(tmp_path, "p.mp4")])
    win._on_batch_event("stopped")
    assert win.start_btn.isEnabled() and "전체 업스케일 시작" in win.progress.cur_sub.text()


def test_retry_button_label_follows_statuses(tmp_path):
    from upcon.app.widgets.queue_panel import retry_label
    assert retry_label([_job(tmp_path, "a.mp4", JobStatus.FAILED)]) == "실패 항목 다시 시도"
    assert retry_label([_job(tmp_path, "b.mp4", JobStatus.CANCELLED)]) == "취소된 항목 다시 시작"
    assert retry_label([_job(tmp_path, "c.mp4", JobStatus.INTERRUPTED)]) == "중단된 항목 다시 시작"
    assert retry_label([_job(tmp_path, "a.mp4", JobStatus.FAILED), _job(tmp_path, "b.mp4", JobStatus.CANCELLED)]) == "실패/취소 항목 다시 시도"
    assert retry_label([_job(tmp_path, "d.mp4")]) == "다시 시도"


# ---------------------------------------------------------------- M4: 완료 0개 ≠ 100%
def test_progress_percent_ignores_failed_and_cancelled(tmp_path):
    m = JobManager(lambda job, progress: job.input_path, lambda j: None)
    a = _job(tmp_path, "a.mp4", JobStatus.CANCELLED)
    b = _job(tmp_path, "b.mp4", JobStatus.FAILED)
    m.jobs = [a, b]
    s = m.summary()
    assert s.done == 0 and s.percent == 0, "완료 0개인데 100% 로 보이면 안 된다"
    m.jobs = [_job(tmp_path, "c.mp4", JobStatus.DONE), b]
    assert m.summary().percent == 50
    m.jobs = [_job(tmp_path, "c.mp4", JobStatus.DONE), _job(tmp_path, "d.mp4", JobStatus.DONE)]
    assert m.summary().percent == 100


def test_progress_panel_labels(qapp):
    from upcon.app.widgets.progress_panel import ProgressPanel
    p = ProgressPanel()
    p.set_batch(BatchSummary(total=2, cancelled=2, frames_total=200), running=False)
    assert p.all_name.text() == "완료 0 / 2" and p.all_bar.value() == 0
    p.set_batch(BatchSummary(total=2, done=1, pending=1, frames_total=200, frames_done=100), running=False)
    assert "대기 1개" in p.all_name.text() and "진행률" not in p.all_name.text()
    p.set_batch(BatchSummary(total=2, done=1, running=1, frames_total=200, frames_done=150), running=True)
    assert "전체 진행률 75%" in p.all_name.text()


# ---------------------------------------------------------------- M6: ETA
def test_eta_edge_cases():
    e = EtaEstimator()
    assert e.update(0, 0, now=0.0) is None                    # 총 프레임 모름
    assert e.update(0, 100, now=0.0) is None                  # 첫 관측
    assert e.update(5, 100, now=1.0) is None                  # 너무 이름 (3초/12프레임 미만)
    assert e.update(11, 100, now=4.0) is None                 # 시간은 됐지만 프레임 부족
    eta = e.update(40, 100, now=4.0)                          # 4초에 40프레임 → 10fps → 남은 60프레임 = 6초
    assert eta is not None and abs(eta - 6.0) < 1e-6
    eta2 = e.update(60, 100, now=6.0)                         # 평활: 크게 튀지 않는다
    assert eta2 is not None and 3.9 < eta2 < 6.1
    assert e.update(100, 100, now=10.0) == 0.0                # 완료
    assert e.update(3, 200, now=20.0) is None                 # 진행이 되돌아감(새 작업) → 재시작
    assert e.update(-1, 100, now=21.0) is None


def test_format_eta():
    assert format_eta(None) == "계산 중..."
    assert format_eta(0) == "곧 완료"
    assert format_eta(42) == "약 42초"
    assert format_eta(83) == "약 1분 20초"                    # 10초 단위 반올림
    assert format_eta(120) == "약 2분"
    assert format_eta(3700) == "약 1시간 1분"


def test_running_sub_hides_frames_and_shows_eta(win, tmp_path):
    j = _job(tmp_path, "r.mp4", frames=240)
    j.status = JobStatus.RUNNING
    j.started_at = time.time() - 30
    j.progress = Progress(Phase.UPSCALE, 120, 240, "120 / 240 프레임")
    sub = win._running_sub(j)
    assert "프레임" not in sub and "진행률 50%" in sub and "남은 시간" in sub and "경과" in sub


# ---------------------------------------------------------------- M1: 결과 위치 안내
def test_output_hint_and_result_row(win, tmp_path):
    hint = win.options.output_hint.text()
    assert "같은 폴더" in hint and "_2x" in hint
    win.config.output_dir = str(tmp_path / "out")
    win._refresh_output_hint()
    assert str(tmp_path / "out") in win.options.output_hint.text()
    d = _job(tmp_path, "done.mp4", JobStatus.DONE)
    _load(win, [d])
    win._on_job_updated(d)
    assert win.progress.result_name.full_text() == "done_2x.mp4"
    assert str(tmp_path) in win.progress.result_dir.full_text()
    assert "done_2x.mp4" in win.queue.table.item(0, 5).text(), "표에도 결과 파일명이 보여야 한다"
    win.queue.table.selectRow(0)
    assert win.open_btn.isVisible() or win.open_btn.isVisibleTo(win)


# ---------------------------------------------------------------- STEP 10: 출력 해상도(2×/1080p/4K)
def test_output_mode_defaults_to_2x(win):
    from upcon.core.constants import OutputMode
    assert win.options.output_mode() == OutputMode.TWO_X
    assert win.config.output_mode == "2x"


def test_selecting_output_mode_updates_config_pending_jobs_and_hint(win, tmp_path):
    from upcon.core.constants import OutputMode
    a = _job(tmp_path, "a.mp4")                  # PENDING
    b = _job(tmp_path, "b.mp4", JobStatus.DONE)  # 완료 항목은 영향받지 않아야 한다
    _load(win, [a, b])
    win.options.set_output_mode(OutputMode.FHD)  # 라디오 클릭과 동일 (buttonToggled 발생)
    assert win.config.output_mode == "1080p"
    assert a.output_mode == OutputMode.FHD, "대기 중인 항목은 즉시 새 출력 모드를 따라가야 한다"
    assert b.output_mode == OutputMode.TWO_X, "이미 완료된 항목은 바뀌면 안 된다"
    assert "_1080p" in win.options.output_hint.text()


def test_queue_output_column_shows_target_resolution_per_mode(win, tmp_path):
    """섹션 8: 원본/출력 해상도를 사용자가 바로 알 수 있어야 한다 — 대기열 표의 '출력' 열."""
    from upcon.core.constants import OutputMode
    j = _job(tmp_path, "landscape.mp4")           # _info() 기본값 854×480
    _load(win, [j])
    win.queue.update_job(j)
    assert win.queue.table.item(0, 1).text() == "854 × 480"      # 원본
    assert win.queue.table.item(0, 3).text() == "1708 × 960"     # 2× 기본값

    j.output_mode = OutputMode.FHD
    win.queue.update_job(j)
    assert win.queue.table.item(0, 3).text() == "1920 × 1080"

    j.output_mode = OutputMode.UHD
    win.queue.update_job(j)
    assert win.queue.table.item(0, 3).text() == "3840 × 2160"


def test_start_all_applies_selected_output_mode_to_all_pending(win, tmp_path):
    from upcon.core.constants import OutputMode
    a, b = _job(tmp_path, "a.mp4"), _job(tmp_path, "b.mp4")
    _load(win, [a, b])
    win.options.set_output_mode(OutputMode.UHD)
    n = win.controller.start_all(win.controller.local_ncnn, "test", win.config.scale, win.options.output_mode())
    assert n == 2 and a.output_mode == OutputMode.UHD and b.output_mode == OutputMode.UHD
    win.controller.stop_all()


def test_corrupted_output_mode_in_config_falls_back_to_2x_without_crash(qapp, tmp_path, monkeypatch):
    """손상된 config.json(output_mode="garbage")을 읽어도 UI 가 죽지 않고 2× 로 뜬다."""
    from upcon.core.constants import OutputMode
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("UPCON_DATA_DIR", str(data))
    from upcon.app.controller import Controller
    monkeypatch.setattr(Controller, "detect_env_async", lambda self: None)
    cfg = AppConfig()
    cfg.output_mode = "garbage-value"
    from upcon.app.main_window import MainWindow
    w = MainWindow(cfg)
    try:
        assert w.options.output_mode() == OutputMode.TWO_X
    finally:
        w.controller.shutdown()
        w.close()


# ---------------------------------------------------------------- M10: 완료 후 새 파일 추가 → 대기 상태
def test_adding_file_after_finish_resets_progress_panel(win, tmp_path, monkeypatch):
    from upcon.app.controller import Controller
    monkeypatch.setattr(Controller, "_probe_async", lambda self, job: None)
    d = _job(tmp_path, "done.mp4", JobStatus.DONE)
    _load(win, [d])
    win._on_batch_event("finished")
    assert win.progress.cur_name.text() == "전체 작업 완료"
    new = tmp_path / "new.mp4"
    new.write_bytes(b"x")
    win.add_files([new])
    assert win.progress.cur_name.text() == "대기 중"
    assert "대기 1개" in win.progress.cur_sub.text() and "시작" in win.progress.cur_sub.text()
    assert d.status == JobStatus.DONE                          # 기존 완료 항목은 그대로
    assert "대기 1개" in win.progress.all_name.text() and "진행률" not in win.progress.all_name.text()


def test_restored_cancelled_item_does_not_show_running_message(win, tmp_path):
    win.progress.set_idle("이전 대기열을 복원했습니다", "1개 항목")
    c = _job(tmp_path, "c.mp4", JobStatus.CANCELLED)
    win.controller.jobs.add(c)                                 # add → on_update → _on_job_updated
    assert win.progress.cur_name.text() == "이전 대기열을 복원했습니다"


# ---------------------------------------------------------------- M7: 설정 명칭 / 내부 용어 비노출
def test_settings_button_and_dialog_texts(win, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from upcon.app import cloud_settings
    assert "클라우드" in win.settings_btn.text()
    monkeypatch.setattr(cloud_settings.credentials, "get_fal_key", lambda: None)
    dlg = cloud_settings.CloudSettingsDialog(win.config, win.controller.cloud_endpoint(), win)
    text = " ".join(l.text() for l in dlg.findChildren(QLabel))
    assert "WinVault" not in text and "Keyring" not in text and "÷" not in text
    assert "선택 기능" in text and "안전하게 저장" in text
    dlg.close()


def test_cloud_settings_never_prefills_stored_key_in_field(win, monkeypatch):
    """저장된 키가 있어도 입력창에 실제 값을 채우지 않는다 — '표시' 토글로도 저장된 값이 그대로 보이면 안 된다."""
    from upcon.app import cloud_settings
    real_key = "12345678-abcd-4321-abcd-1234567890ab:0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(cloud_settings.credentials, "get_fal_key", lambda: real_key)
    monkeypatch.setattr(cloud_settings.credentials, "has_fal_key", lambda: True)
    dlg = cloud_settings.CloudSettingsDialog(win.config, win.controller.cloud_endpoint(), win)
    assert dlg.key_edit.text() == ""
    assert real_key not in dlg.key_edit.text()
    assert dlg.remove_btn.isEnabled()
    dlg.close()


# ---------------------------------------------------------------- Cloud 실행 전 확인 대화상자: 취소/승인
def test_cloud_confirm_dialog_cancel_keeps_batch_stopped(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from upcon.app.widgets.cloud_confirm_dialog import CloudConfirmDialog
    from upcon.core import credentials
    from upcon.core.constants import ProcessMode

    monkeypatch.setattr(credentials, "get_fal_key", lambda: "fake-key")
    monkeypatch.setattr(CloudConfirmDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    job = _job(tmp_path, "a.mp4")
    _load(win, [job])
    win.options.set_mode(ProcessMode.CLOUD)
    win._on_start_all()
    assert not win._batch_running
    assert job.status == JobStatus.PENDING, "취소하면 요청 자체가 나가지 않아야 한다"


def test_cloud_confirm_dialog_approve_starts_batch(win, qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from upcon.app.widgets.cloud_confirm_dialog import CloudConfirmDialog
    from upcon.core import credentials
    from upcon.core.constants import ProcessMode
    from upcon.providers.fal_flashvsr import FalFlashVSRProvider

    monkeypatch.setattr(credentials, "get_fal_key", lambda: "fake-key")
    monkeypatch.setattr(CloudConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    calls = []

    def fake_upscale(self, job, progress, env=None):
        calls.append(job.id)
        out = job.input_path.with_name(job.input_path.stem + "_2x.mp4")
        out.write_bytes(b"result")
        return out
    monkeypatch.setattr(FalFlashVSRProvider, "upscale", fake_upscale)

    job = _job(tmp_path, "a.mp4")
    _load(win, [job])
    win.options.set_mode(ProcessMode.CLOUD)
    win._on_start_all()
    t0 = time.time()
    # 배치 완료는 워커 스레드 → GUI 스레드로 큐잉된 시그널(batchEvent)로 반영된다 — 이벤트 루프를 돌려야
    # win._batch_running 이 제때 False 로 내려가고, 그렇지 않으면 fixture teardown 의 closeEvent 가
    # "작업 진행 중" 확인창을 실제로 띄워 테스트가 멈춘다.
    while (job.status not in (JobStatus.DONE, JobStatus.FAILED) or win._batch_running) and time.time() - t0 < 5:
        qapp.processEvents()
        time.sleep(0.02)
    assert job.status == JobStatus.DONE and calls == [job.id], job.error_message
    assert not win._batch_running


def test_cloud_mode_without_key_prompts_to_open_settings(win, tmp_path, monkeypatch):
    """클라우드를 골랐는데 fal.ai 키가 없으면: 요청을 보내지 않고 설정을 열 수 있는 안내를 띄운다."""
    from PySide6.QtWidgets import QMessageBox

    from upcon.core import credentials
    from upcon.core.constants import ProcessMode

    monkeypatch.setattr(credentials, "get_fal_key", lambda: None)

    def auto_accept(self):
        for b in self.buttons():
            if self.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole:
                b.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", auto_accept)
    opened = []
    monkeypatch.setattr(type(win), "_open_settings", lambda self: opened.append(1))

    job = _job(tmp_path, "a.mp4")
    _load(win, [job])
    win.options.set_mode(ProcessMode.CLOUD)
    win._on_start_all()
    assert opened == [1]
    assert not win._batch_running


# ---------------------------------------------------------------- M8: 중복 실행 방지
def _pump(qapp, ms=300):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        qapp.processEvents()
        time.sleep(0.01)


def test_single_instance_same_data_dir_is_blocked(qapp, tmp_path):
    from upcon.app.single_instance import SingleInstance
    got = []
    first = SingleInstance(tmp_path / "data")
    try:
        assert first.listening and not first.already_running
        first.activated.connect(lambda: got.append(1))
        second = SingleInstance(tmp_path / "data")
        assert second.already_running and not second.listening
        _pump(qapp)
        assert got == [1], "두 번째 실행은 첫 창을 앞으로 보내야 한다"
    finally:
        first.close()


def test_single_instance_different_data_dirs_are_isolated(qapp, tmp_path):
    from upcon.app.single_instance import SingleInstance
    a = SingleInstance(tmp_path / "A")
    b = SingleInstance(tmp_path / "B")
    try:
        assert a.listening and b.listening
        assert not a.already_running and not b.already_running
        assert a.key != b.key
    finally:
        a.close()
        b.close()
    # 닫은 뒤에는 같은 폴더로 다시 시작할 수 있다
    c = SingleInstance(tmp_path / "A")
    assert c.listening and not c.already_running
    c.close()


def test_single_instance_key_is_path_normalized(tmp_path):
    from upcon.app.single_instance import instance_key
    d = tmp_path / "Data"
    d.mkdir()
    assert instance_key(d) == instance_key(Path(str(d).upper())) == instance_key(Path(str(d).replace("\\", "/")))
