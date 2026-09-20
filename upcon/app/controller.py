"""UI 와 core 를 잇는 컨트롤러: 환경 감지, 라우팅, 배치 대기열(JobManager) 을 Qt 시그널로 노출.

UI 는 Provider 내부를 모른다. 비용/가용성/실행은 모두 Provider 인터페이스로만 접근한다.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from upcon.core import queue_store
from upcon.core.config import AppConfig
from upcon.core.constants import DEFAULT_OUTPUT_MODE, SUPPORTED_EXTENSIONS, OutputMode, ProcessMode, parse_output_mode
from upcon.core.env import SystemEnv, detect_system_env
from upcon.core.errors import UpconError
from upcon.core.jobs import BatchSummary, Job, JobManager, JobStatus, ProgressCallback
from upcon.core.power import KeepAwake
from upcon.core.probe import VideoInfo, probe_video
from upcon.core.router import Decision, Router
from upcon.core.tempfs import cleanup_stale_jobs, default_temp_root
from upcon.providers.base import UpscalerProvider
from upcon.providers.fal_bytedance import FalByteDanceProvider
from upcon.providers.local_ncnn import LocalNcnnProvider

# FlashVSR provider(upcon.providers.fal_flashvsr)는 삭제하지 않고 보존한다 — 다만 화질 비교
# 테스트(2026-09-20) 결과 ByteDance PRO AIGC가 더 우수해 기본 클라우드 배선에서는 제외했다.
# 코드/테스트는 그대로 남아 있어 필요하면 다시 배선할 수 있다.

log = logging.getLogger(__name__)


class Controller(QObject):
    jobUpdated = Signal(object)          # Job (워커 스레드 → UI 스레드로 큐잉됨)
    batchEvent = Signal(str)             # "started" | "finished" | "stopped"
    envReady = Signal(object, object)    # SystemEnv, Decision(자동 모드 기준)
    _probed = Signal(object, object, str)  # (job, VideoInfo|None, error) — 워커 스레드 → UI 스레드

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self.env: SystemEnv | None = None
        self.local_ncnn = LocalNcnnProvider(config)
        self.cloud_provider = FalByteDanceProvider(config)
        self.providers: dict[str, UpscalerProvider] = {
            self.local_ncnn.id: self.local_ncnn,
            self.cloud_provider.id: self.cloud_provider,
        }
        self.router = Router(config, [self.local_ncnn], [self.cloud_provider])
        self.keep_awake = KeepAwake()
        self.jobs = JobManager(self._run_job, self._emit_job, self._emit_batch, self.keep_awake)
        self._probed.connect(self._on_probed_main)
        self._probe_sem = threading.Semaphore(4)    # 동시에 4개까지만 ffprobe
        self._save_lock = threading.Lock()
        self._last_save = 0.0
        cleanup_stale_jobs(default_temp_root(config.temp_dir))

    # ---- 환경 감지 (백그라운드) ----
    def detect_env_async(self) -> None:
        def work():
            try:
                env = detect_system_env()
                decision = self.router.decide(ProcessMode.AUTO, env, self.config.scale,
                                              parse_output_mode(self.config.output_mode))
            except Exception as e:  # 감지 실패해도 앱은 떠야 함
                log.exception("env detect failed: %s", e)
                env, decision = SystemEnv(), Decision(None, "PC 환경을 확인하지 못했습니다.", str(e))
            self.env = env
            self.envReady.emit(env, decision)
        threading.Thread(target=work, name="upcon-env", daemon=True).start()

    # ---- 라우팅/비용 ----
    def decide(self, mode: ProcessMode, scale: int, output_mode: OutputMode = DEFAULT_OUTPUT_MODE) -> Decision:
        env = self.env or detect_system_env()
        self.env = env
        return self.router.decide(mode, env, scale, output_mode)

    def cloud_cost(self, info: VideoInfo, scale: int):
        return self.cloud_provider.cost(info, scale)

    def cloud_endpoint(self) -> str:
        return self.cloud_provider.endpoint

    def total_cloud_cost(self, scale: int, only_pending: bool = True) -> tuple[float, int]:
        """(전체 예상 비용 USD, 대상 파일 수)."""
        total, n = 0.0, 0
        for j in self.jobs.jobs:
            if j.info is None or (only_pending and j.status != JobStatus.PENDING):
                continue
            total += self.cloud_cost(j.info, scale).usd_display
            n += 1
        return total, n

    # ---- 대기열 ----
    def add_files(self, paths: list[Path], output_mode: OutputMode) -> tuple[int, int]:
        """파일 추가 + 백그라운드 분석. (추가됨, 중복/무시됨) 반환.

        scale(AI/클라우드 실제 배율)은 항상 기본값 — 사용자가 고르는 건 output_mode(2×/1080p/4K)."""
        added = skipped = 0
        for p in paths:
            p = Path(p)
            if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                skipped += 1
                continue
            job = Job(input_path=p, scale=self.config.scale, output_mode=output_mode)
            if not self.jobs.add(job):
                skipped += 1
                continue
            added += 1
            self._probe_async(job)
        self.save_queue()
        return added, skipped

    def _probe_async(self, job: Job) -> None:
        """영상 분석을 일반 스레드에서 실행하고 결과를 Controller 시그널로 UI 스레드에 전달한다.
        (QRunnable + 개별 시그널 객체는 다수 파일 동시 분석 시 수명 문제로 크래시할 수 있어 쓰지 않는다.)"""
        def work():
            with self._probe_sem:
                try:
                    info = probe_video(job.input_path)
                except UpconError as e:
                    log.warning("probe failed: %s", e.detail)
                    self._probed.emit(job, None, e.user_message)
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("probe crashed: %s", e)
                    self._probed.emit(job, None, "영상 정보를 읽는 중 알 수 없는 오류가 발생했습니다.")
                    return
            self._probed.emit(job, info, "")
        threading.Thread(target=work, name="upcon-probe", daemon=True).start()

    def _on_probed_main(self, job: Job, info, message: str) -> None:
        if info is None:
            job.status = JobStatus.FAILED
            job.error_message = message
            job.finished_at = time.time()
            self.jobUpdated.emit(job)
            self.save_queue()
            return
        job.info = info
        est = self.cloud_cost(info, job.scale)
        job.estimated_cost_usd = est.usd_display
        job.cost_uncertain = getattr(est, "uncertain", False)
        job.cost_uncertain_note = getattr(est, "uncertain_note", "")
        self.jobs._wake.set()
        self.jobUpdated.emit(job)

    def restore_queue(self) -> int:
        jobs = queue_store.load_queue()
        for j in jobs:
            if self.jobs.add(j):
                self._probe_async(j)
        return len(jobs)

    def save_queue(self) -> None:
        with self._save_lock:
            queue_store.save_queue(list(self.jobs.jobs))

    def remove_jobs(self, jobs: list[Job]) -> int:
        n = sum(1 for j in jobs if self.jobs.remove(j))
        self.save_queue()
        return n

    def clear_jobs(self) -> int:
        n = self.jobs.clear()
        self.save_queue()
        return n

    def move_job(self, job: Job, delta: int) -> bool:
        ok = self.jobs.move(job, delta)
        if ok:
            self.save_queue()
        return ok

    def retry_failed(self) -> int:
        n = self.jobs.retry_failed()
        self.save_queue()
        return n

    # ---- 실행 ----
    def start_all(self, provider: UpscalerProvider, label: str, scale: int,
                  output_mode: OutputMode = DEFAULT_OUTPUT_MODE) -> int:
        """대기 항목 전부에 provider/출력 방식을 지정 후 순차 처리 시작. 대상 개수 반환."""
        n = 0
        for j in self.jobs.jobs:
            if j.status == JobStatus.PENDING:
                j.provider_id, j.provider_label, j.scale, j.output_mode = provider.id, label, scale, output_mode
                n += 1
        self.jobs.start()
        return n

    def skip_current(self) -> None:
        self.jobs.skip_current()

    def stop_all(self) -> None:
        self.jobs.stop_all()

    def summary(self) -> BatchSummary:
        return self.jobs.summary()

    def _run_job(self, job: Job, progress: ProgressCallback):
        provider = self.providers.get(job.provider_id)
        if provider is None:
            raise UpconError("선택한 처리 방식을 사용할 수 없습니다.", f"unknown provider {job.provider_id}")
        return provider.upscale(job, progress, self.env)

    def _emit_job(self, job: Job) -> None:
        self.jobUpdated.emit(job)
        # 상태 변화(진행률 제외)는 저장. 진행률은 1초에 한 번만.
        now = time.time()
        if job.status != JobStatus.RUNNING or now - self._last_save > 1.0:
            self._last_save = now
            self.save_queue()

    def _emit_batch(self, event: str) -> None:
        self.save_queue()
        self.batchEvent.emit(event)

    def shutdown(self) -> None:
        self.jobs.shutdown()
        self.keep_awake.release()
        self.save_queue()
