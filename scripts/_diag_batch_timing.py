"""[TEMP DIAGNOSTIC — remove after root cause of macOS CI batch timing failure is found]

test_skip_current_continues_with_next 을 pytest 밖에서 그대로 재현하되, 각 단계마다
경과 시간을 출력한다. 목적: 15초 타임아웃 전에 실제로 어디서 시간이 소요되는지
(job0 취소 시점, job1/job2 시작·종료 시점, 'finished' 이벤트 시점) 정확히 측정한다.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upcon.core.jobs import Job, JobManager, JobStatus, Phase, Progress  # noqa: E402
from upcon.core.probe import VideoInfo  # noqa: E402


def _info(path: Path, frames: int) -> VideoInfo:
    return VideoInfo(path=path, width=854, height=480, fps=24, duration_sec=frames / 24, size_bytes=1000,
                     video_codec="h264", has_audio=True, nb_frames=frames)


def _job(tmp_path: Path, name: str, frames: int = 24) -> Job:
    p = tmp_path / name
    p.write_bytes(b"x")
    return Job(input_path=p, scale=2, info=_info(p, frames))


T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.3f}s] {msg}", flush=True)


def _slow_runner(job: Job, progress) -> Path:
    log(f"  runner START {job.input_path.name}")
    for i in range(100):
        job.cancel.raise_if_cancelled()
        progress(Progress(Phase.UPSCALE, i, 100))
        time.sleep(0.02)
    log(f"  runner DONE  {job.input_path.name}")
    return job.input_path


def main() -> int:
    import tempfile
    tmp_path = Path(tempfile.mkdtemp(prefix="upcon_diag_"))

    events: list[str] = []
    updates: list[Job] = []
    m = JobManager(_slow_runner, updates.append, lambda e: (events.append(e), log(f"EVENT {e}")), None)

    jobs = [_job(tmp_path, f"s{i}.mp4") for i in range(3)]
    for j in jobs:
        m.add(j)

    log("start()")
    m.start()

    t0 = time.time()
    while not (jobs[0].status == JobStatus.RUNNING and jobs[0].progress.frames_done >= 5):
        if time.time() - t0 > 10:
            log("TIMEOUT waiting for job0 RUNNING+frames>=5")
            return 1
        time.sleep(0.02)
    log(f"job0 RUNNING frames_done={jobs[0].progress.frames_done} -> calling skip_current()")
    m.skip_current()
    log("skip_current() returned")

    t0 = time.time()
    while "finished" not in events:
        if time.time() - t0 > 30:
            log("TIMEOUT (30s) waiting for 'finished' — giving up")
            log(f"statuses: {[j.status for j in jobs]}")
            log(f"current: {m.current}")
            return 1
        time.sleep(0.02)
    log(f"'finished' observed after {time.time() - t0:.3f}s of this wait")
    log(f"statuses: {[j.status for j in jobs]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
