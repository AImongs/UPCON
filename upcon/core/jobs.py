"""작업(Job) 모델, 취소 토큰, 진행 보고, 배치 대기열 JobManager (UI 프레임워크 비의존).

배치 규칙
- 한 번에 하나만 처리 (GPU/VRAM/임시 디스크/FFmpeg 안정성). 순서: 목록 순서대로.
- 한 파일 실패 → 다음 파일 계속. 마지막에 완료/실패 집계.
- '현재 파일 건너뛰기' = 현재 작업만 취소하고 다음으로. '전체 중지' = 현재 작업 취소 + 대기 항목은 대기 상태로 남김.
- 처리 중에는 절전 방지 (core/power.py), 멈추면 원래대로.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from upcon.core.constants import DEFAULT_OUTPUT_MODE, OutputMode
from upcon.core.errors import UpconError
from upcon.core.probe import VideoInfo

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"          # 대기 (info 가 없으면 '분석 중')
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"      # 사용자가 건너뛰기/중지
    INTERRUPTED = "interrupted"  # 앱 비정상 종료로 중단됨 (복원 시)


STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.PENDING: "대기",
    JobStatus.RUNNING: "처리 중",
    JobStatus.DONE: "완료",
    JobStatus.FAILED: "실패",
    JobStatus.CANCELLED: "취소됨",
    JobStatus.INTERRUPTED: "중단됨",
}

RETRYABLE = (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED)


class Phase(str, Enum):
    ANALYZE = "영상 분석 중"
    PREPARE = "GPU 준비 중"
    UPSCALE = "업스케일 중"
    AUDIO = "오디오 처리 중"
    SAVE = "저장 중"
    DONE = "완료"
    # 클라우드
    UPLOAD = "업로드 중"
    UPLOADED = "업로드 완료"
    QUEUE = "클라우드 업스케일 대기 중"
    CLOUD = "클라우드 업스케일 처리 중"
    DOWNLOAD = "결과 다운로드 중"


class CancelledError(UpconError):
    def __init__(self):
        super().__init__("사용자가 작업을 취소했습니다.", "cancelled")


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def raise_if_cancelled(self) -> None:
        if self._ev.is_set():
            raise CancelledError()


@dataclass
class Progress:
    phase: Phase
    frames_done: int = 0
    frames_total: int = 0
    detail: str = ""
    fraction: float | None = None      # 실측 비율이 있을 때만 (예: 다운로드 바이트). 가짜 진행률 금지.

    @property
    def percent(self) -> int | None:
        if self.phase == Phase.DONE:
            return 100
        if self.fraction is not None:
            return int(max(0.0, min(1.0, self.fraction)) * 100)
        if self.phase == Phase.UPSCALE and self.frames_total > 0:
            return int(self.frames_done * 100 / self.frames_total)
        return None

    @property
    def done_fraction(self) -> float:
        """전체 진행률 계산용 (0~1). 프레임 기반이 있으면 그것, 다운로드 비율은 뒤쪽 10% 로 취급."""
        if self.phase == Phase.DONE:
            return 1.0
        if self.phase == Phase.UPSCALE and self.frames_total > 0:
            return min(1.0, self.frames_done / self.frames_total)
        if self.phase in (Phase.AUDIO, Phase.SAVE):
            return 0.98
        if self.phase == Phase.DOWNLOAD and self.fraction is not None:
            return 0.9 + 0.1 * self.fraction
        if self.phase == Phase.CLOUD:
            return 0.5
        return 0.0


ProgressCallback = Callable[[Progress], None]


@dataclass
class Job:
    input_path: Path
    scale: int                                          # AI/클라우드 실제 배율(항상 2) — output_mode 와 다른 개념
    output_mode: OutputMode = DEFAULT_OUTPUT_MODE         # 사용자가 고른 출력 방식(2×/1080p/4K)
    info: VideoInfo | None = None
    output_path: Path | None = None
    provider_id: str = ""
    provider_label: str = ""           # 예: "내 PC GPU(RTX 5060)에서 처리합니다."
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: JobStatus = JobStatus.PENDING
    progress: Progress = field(default_factory=lambda: Progress(Phase.ANALYZE))
    error_message: str = ""
    error_detail: str = ""             # 개발자용 (로그에도 기록됨)
    note: str = ""
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    cancel: CancelToken = field(default_factory=CancelToken)

    @property
    def elapsed_sec(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.time()
        return end - self.started_at

    @property
    def frames(self) -> int:
        return self.info.nb_frames if self.info else 0

    @property
    def status_label(self) -> str:
        if self.status == JobStatus.PENDING and self.info is None:
            return "분석 중"
        if self.status == JobStatus.RUNNING:
            p = self.progress
            label = "저장 중" if p.phase in (Phase.SAVE, Phase.AUDIO) else "처리 중"
            return f"{label} {p.percent}%" if p.percent is not None else label
        return STATUS_LABELS[self.status]

    def reset_for_retry(self) -> None:
        self.status = JobStatus.PENDING
        self.progress = Progress(Phase.ANALYZE)
        self.error_message = self.error_detail = self.note = ""
        self.output_path = None
        self.started_at = self.finished_at = 0.0
        self.actual_cost_usd = None
        self.cancel = CancelToken()


JobRunner = Callable[[Job, ProgressCallback], Path]
"""Provider 가 구현: (job, progress_cb) → 결과 파일 경로. 취소 시 CancelledError."""


@dataclass
class BatchSummary:
    total: int = 0
    done: int = 0
    running: int = 0
    pending: int = 0
    failed: int = 0
    cancelled: int = 0
    interrupted: int = 0
    frames_total: int = 0
    frames_done: float = 0.0

    @property
    def percent(self) -> int:
        """전체 진행률 = 완료한 작업량 / 전체 작업량 (총 프레임 기준, 프레임 수를 모르면 파일 수 기준).

        실패/취소/중단 항목은 '끝난 것' 이지만 진행으로 세지 않는다 — 완료 0개인데 100% 로 보이면 안 된다.
        """
        if self.frames_total > 0:
            return int(min(100, self.frames_done * 100 / self.frames_total))
        if self.total:
            return int(self.done * 100 / self.total)
        return 0

    @property
    def finished_count(self) -> int:
        return self.done + self.failed + self.cancelled + self.interrupted


class JobManager:
    """배치 대기열. 워커 스레드 하나가 running 상태일 때만 대기 항목을 순서대로 처리한다."""

    def __init__(self, runner: JobRunner, on_update: Callable[[Job], None],
                 on_batch: Callable[[str], None] | None = None, keep_awake=None):
        self._runner = runner
        self._on_update = on_update
        self._on_batch = on_batch or (lambda event: None)   # "started" | "finished" | "stopped"
        self._keep_awake = keep_awake                         # core.power.KeepAwake 호환 객체 (acquire/release)
        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._running = False
        self._stop_requested = False
        self._shutdown = False
        self.current: Job | None = None
        self._thread = threading.Thread(target=self._loop, name="upcon-jobs", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ 대기열 편집
    def add(self, job: Job) -> bool:
        """같은 경로가 이미 (완료 아닌 상태로) 있으면 추가하지 않는다."""
        with self._lock:
            for j in self.jobs:
                if j.input_path == job.input_path and j.status != JobStatus.DONE:
                    return False
            self.jobs.append(job)
        self._on_update(job)
        self._wake.set()
        return True

    def remove(self, job: Job) -> bool:
        with self._lock:
            if job.status == JobStatus.RUNNING or job not in self.jobs:
                return False
            self.jobs.remove(job)
        self._on_update(job)
        return True

    def clear(self) -> int:
        """처리 중이 아닌 항목 전부 삭제. 삭제한 개수 반환."""
        with self._lock:
            keep = [j for j in self.jobs if j.status == JobStatus.RUNNING]
            removed = len(self.jobs) - len(keep)
            self.jobs = keep
        return removed

    def move(self, job: Job, delta: int) -> bool:
        """대기 항목의 순서 변경 (다른 대기 항목과만 자리를 바꾼다)."""
        with self._lock:
            if job.status != JobStatus.PENDING or job not in self.jobs:
                return False
            i = self.jobs.index(job)
            step = 1 if delta > 0 else -1
            k = i + step
            while 0 <= k < len(self.jobs):
                if self.jobs[k].status == JobStatus.PENDING:
                    self.jobs[i], self.jobs[k] = self.jobs[k], self.jobs[i]
                    return True
                k += step
            return False

    def retry_failed(self) -> int:
        n = 0
        with self._lock:
            for j in self.jobs:
                if j.status in RETRYABLE:
                    j.reset_for_retry()
                    n += 1
        for j in list(self.jobs):
            if j.status == JobStatus.PENDING:
                self._on_update(j)
        self._wake.set()
        return n

    def pending(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.status == JobStatus.PENDING and j.info is not None]

    # ------------------------------------------------------------ 실행 제어
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """대기 항목 처리 시작 (이미 실행 중이면 무시)."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_requested = False
        self._wake.set()

    def skip_current(self) -> None:
        """현재 파일만 취소하고 다음 파일로 넘어간다."""
        cur = self.current
        if cur is not None:
            cur.cancel.cancel()

    def stop_all(self) -> None:
        """현재 파일을 안전하게 취소하고 나머지는 '대기' 로 남긴다."""
        with self._lock:
            self._stop_requested = True
            self._running = False
        cur = self.current
        if cur is not None:
            cur.cancel.cancel()
        self._wake.set()

    def shutdown(self) -> None:
        self._shutdown = True
        self.stop_all()

    def summary(self) -> BatchSummary:
        s = BatchSummary()
        with self._lock:
            for j in self.jobs:
                s.total += 1
                f = j.frames
                s.frames_total += f
                if j.status == JobStatus.DONE:
                    s.done += 1
                    s.frames_done += f
                elif j.status == JobStatus.RUNNING:
                    s.running += 1
                    s.frames_done += f * j.progress.done_fraction
                elif j.status == JobStatus.PENDING:
                    s.pending += 1
                elif j.status == JobStatus.FAILED:
                    s.failed += 1               # 진행률에 넣지 않는다 (percent 참고)
                elif j.status == JobStatus.CANCELLED:
                    s.cancelled += 1
                elif j.status == JobStatus.INTERRUPTED:
                    s.interrupted += 1
        return s

    # ------------------------------------------------------------ 워커
    def _next_job(self) -> Job | None:
        with self._lock:
            if not self._running:
                return None
            for j in self.jobs:
                if j.status == JobStatus.PENDING and j.info is not None:
                    return j
            return None

    def _has_unprobed_pending(self) -> bool:
        with self._lock:
            return any(j.status == JobStatus.PENDING and j.info is None for j in self.jobs)

    def _loop(self) -> None:
        batch_active = False
        while not self._shutdown:
            job = self._next_job()
            if job is None:
                if batch_active and (not self._has_unprobed_pending() or not self._running):
                    # 처리할 것이 더 없음 → 배치 종료
                    batch_active = False
                    with self._lock:
                        stopped = self._stop_requested
                        self._running = False
                    self._release_awake()
                    self._on_batch("stopped" if stopped else "finished")
                self._wake.wait(timeout=0.3)
                self._wake.clear()
                continue
            if not batch_active:
                batch_active = True
                self._acquire_awake()
                self._on_batch("started")
            self._run(job)

    def _acquire_awake(self) -> None:
        if self._keep_awake is not None:
            try:
                self._keep_awake.acquire()
            except Exception as e:  # noqa: BLE001
                log.warning("keep-awake acquire failed: %s", e)

    def _release_awake(self) -> None:
        if self._keep_awake is not None:
            try:
                self._keep_awake.release()
            except Exception as e:  # noqa: BLE001
                log.warning("keep-awake release failed: %s", e)

    def _run(self, job: Job) -> None:
        with self._lock:
            self.current = job
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
        self._on_update(job)

        def report(p: Progress) -> None:
            job.progress = p
            self._on_update(job)

        try:
            out = self._runner(job, report)
            job.output_path = out
            job.status = JobStatus.DONE
            job.progress = Progress(Phase.DONE, job.progress.frames_total, job.progress.frames_total)
        except CancelledError:
            job.status = JobStatus.CANCELLED
            job.error_message = ""
        except UpconError as e:
            job.status = JobStatus.FAILED
            job.error_message = e.user_message
            job.error_detail = e.detail
            log.error("job %s (%s) failed: %s", job.id, job.input_path.name, e.detail or e.user_message)
        except Exception as e:  # 예상 못한 오류도 배치를 죽이지 않는다
            job.status = JobStatus.FAILED
            job.error_message = "업스케일 중 알 수 없는 오류가 발생했습니다. 로그를 확인해 주세요."
            job.error_detail = f"{type(e).__name__}: {e}"
            log.exception("job %s crashed: %s", job.id, e)
        finally:
            job.finished_at = time.time()
            with self._lock:
                self.current = None
            self._on_update(job)
