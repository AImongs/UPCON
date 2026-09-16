"""배치 대기열(JobManager) 테스트. 대부분 가짜 runner 로 빠르게, 마지막 하나는 실제 GPU 로 3개 연속 처리."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from upcon.core import ffmpeg as ff, queue_store
from upcon.core.config import AppConfig
from upcon.core.env import detect_system_env
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, Job, JobManager, JobStatus, Phase, Progress
from upcon.core.power import KeepAwake
from upcon.core.probe import VideoInfo, probe_video

SAMPLES = Path(__file__).parent / "samples"


def _info(path: Path, frames: int) -> VideoInfo:
    return VideoInfo(path=path, width=854, height=480, fps=24, duration_sec=frames / 24, size_bytes=1000,
                     video_codec="h264", has_audio=True, nb_frames=frames)


class FakeAwake:
    def __init__(self):
        self.calls = []

    def acquire(self):
        self.calls.append("acquire")

    def release(self):
        self.calls.append("release")


def _make(runner, awake=None):
    updates = []
    events = []
    m = JobManager(runner, updates.append, events.append, awake)
    return m, updates, events


def _wait(cond, timeout=10.0):
    t0 = time.time()
    while not cond():
        if time.time() - t0 > timeout:
            raise AssertionError("timeout")
        time.sleep(0.02)


def _job(tmp_path, name, frames=24):
    p = tmp_path / name
    p.write_bytes(b"x")
    return Job(input_path=p, scale=2, info=_info(p, frames))


# ---------------------------------------------------------------- 순차/실패 계속/집계
def test_sequential_and_failure_continues(tmp_path):
    order = []

    def runner(job, progress):
        order.append(job.input_path.name)
        if job.input_path.name == "c3.mp4":
            raise UpconError("깨진 파일", "corrupt")
        for i in range(1, 5):
            progress(Progress(Phase.UPSCALE, i * 6, 24))
            time.sleep(0.01)
        return job.input_path.with_name(job.input_path.stem + "_2x.mp4")

    awake = FakeAwake()
    m, updates, events = _make(runner, awake)
    jobs = [_job(tmp_path, f"c{i}.mp4") for i in range(1, 6)]
    for j in jobs:
        assert m.add(j)
    assert not m.add(Job(input_path=jobs[0].input_path, scale=2))       # 중복 등록 안 됨
    assert not m.running and awake.calls == []
    m.start()
    _wait(lambda: "finished" in events)
    assert order == ["c1.mp4", "c2.mp4", "c3.mp4", "c4.mp4", "c5.mp4"]   # 한 번에 하나, 순서대로
    st = [j.status for j in jobs]
    assert st == [JobStatus.DONE, JobStatus.DONE, JobStatus.FAILED, JobStatus.DONE, JobStatus.DONE]
    assert jobs[2].error_message == "깨진 파일"
    s = m.summary()
    assert (s.total, s.done, s.failed, s.pending) == (5, 4, 1, 0) and s.percent == 100
    assert awake.calls == ["acquire", "release"]                        # 절전 방지 켜짐 → 끝나면 해제
    assert not m.running


def test_total_progress_weighted_by_frames(tmp_path):
    gate = threading.Event()

    def runner(job, progress):
        if job.input_path.name == "long.mp4":
            progress(Progress(Phase.UPSCALE, 720, 1440))   # 60초 영상의 절반
            gate.wait(5)
        return job.input_path

    m, updates, events = _make(runner)
    a = _job(tmp_path, "a.mp4", 240)        # 10초
    b = _job(tmp_path, "long.mp4", 1440)    # 60초
    c = _job(tmp_path, "c.mp4", 240)        # 10초
    for j in (a, b, c):
        m.add(j)
    m.start()
    _wait(lambda: b.status == JobStatus.RUNNING and b.progress.frames_done == 720)
    s = m.summary()
    # 파일 수 기준이면 1/3=33% 이지만, 프레임 기준: (240 + 720) / 1920 = 50%
    assert s.percent == 50 and s.finished_count == 1
    gate.set()
    _wait(lambda: "finished" in events)
    assert m.summary().percent == 100


# ---------------------------------------------------------------- 건너뛰기 / 전체 중지 / 재시작
def _slow_runner(job, progress):
    for i in range(100):
        job.cancel.raise_if_cancelled()
        progress(Progress(Phase.UPSCALE, i, 100))
        time.sleep(0.02)
    return job.input_path


def test_skip_current_continues_with_next(tmp_path):
    m, updates, events = _make(_slow_runner)
    jobs = [_job(tmp_path, f"s{i}.mp4") for i in range(3)]
    for j in jobs:
        m.add(j)
    m.start()
    _wait(lambda: jobs[0].status == JobStatus.RUNNING and jobs[0].progress.frames_done >= 5)
    m.skip_current()
    _wait(lambda: "finished" in events, 15)
    assert [j.status for j in jobs] == [JobStatus.CANCELLED, JobStatus.DONE, JobStatus.DONE]


def test_stop_all_keeps_pending_and_restart_resumes(tmp_path):
    awake = FakeAwake()
    m, updates, events = _make(_slow_runner, awake)
    jobs = [_job(tmp_path, f"t{i}.mp4") for i in range(4)]
    for j in jobs:
        m.add(j)
    m.start()
    _wait(lambda: jobs[1].status == JobStatus.RUNNING and jobs[1].progress.frames_done >= 5, 15)
    m.stop_all()
    _wait(lambda: "stopped" in events, 15)
    assert [j.status for j in jobs] == [JobStatus.DONE, JobStatus.CANCELLED, JobStatus.PENDING, JobStatus.PENDING]
    assert awake.calls == ["acquire", "release"]
    # 다시 시작 → 남은 대기 항목부터
    m.start()
    _wait(lambda: events.count("finished") == 1, 20)
    assert [j.status for j in jobs] == [JobStatus.DONE, JobStatus.CANCELLED, JobStatus.DONE, JobStatus.DONE]
    assert awake.calls == ["acquire", "release", "acquire", "release"]
    # 실패/취소 항목 다시 시도
    assert m.retry_failed() == 1 and jobs[1].status == JobStatus.PENDING
    m.start()
    _wait(lambda: events.count("finished") == 2, 20)
    assert jobs[1].status == JobStatus.DONE


# ---------------------------------------------------------------- 편집: 삭제/순서/러닝 보호
def test_remove_move_and_running_protection(tmp_path):
    gate = threading.Event()

    def runner(job, progress):
        gate.wait(5)
        return job.input_path

    m, updates, events = _make(runner)
    jobs = [_job(tmp_path, f"m{i}.mp4") for i in range(4)]
    for j in jobs:
        m.add(j)
    assert m.move(jobs[3], -1) and [j.input_path.name for j in m.jobs] == ["m0.mp4", "m1.mp4", "m3.mp4", "m2.mp4"]
    assert m.move(jobs[0], +1) and m.jobs[0] is jobs[1]
    assert not m.move(jobs[1], -1)                       # 맨 위는 더 못 올림
    m.start()
    _wait(lambda: m.current is not None)
    cur = m.current
    assert not m.remove(cur) and not m.move(cur, 1)      # 처리 중 항목은 삭제/이동 불가
    assert m.clear() == 3 and m.jobs == [cur]            # 처리 중 항목은 남는다
    gate.set()
    _wait(lambda: "finished" in events)
    assert m.remove(cur) and m.jobs == []


# ---------------------------------------------------------------- 저장/복원
def test_queue_persistence_roundtrip(tmp_path):
    p = tmp_path / "q.json"
    a = _job(tmp_path, "a.mp4")
    b = _job(tmp_path, "b.mp4")
    b.status = JobStatus.RUNNING            # 비정상 종료 시점의 처리 중 항목
    c = _job(tmp_path, "c.mp4")
    c.status = JobStatus.DONE
    d = _job(tmp_path, "d.mp4")
    d.status = JobStatus.FAILED
    d.error_message = "깨짐"
    queue_store.save_queue([a, b, c, d], p)
    restored = queue_store.load_queue(p)
    names = [(j.input_path.name, j.status) for j in restored]
    assert names == [("a.mp4", JobStatus.PENDING), ("b.mp4", JobStatus.INTERRUPTED), ("d.mp4", JobStatus.FAILED)]
    assert "중단" in restored[1].error_message and restored[2].error_message == "깨짐"
    assert all(j.info is None for j in restored)         # 정보는 다시 분석


# ---------------------------------------------------------------- 출력 파일명 충돌
def test_output_name_collision(tmp_path):
    src = tmp_path / "clip01.mp4"
    src.write_bytes(b"x")
    (tmp_path / "clip01_2x.mp4").write_bytes(b"a")
    (tmp_path / "clip01_2x_2.mp4").write_bytes(b"b")
    assert ff.unique_output_path(src, 2).name == "clip01_2x_3.mp4"
    assert (tmp_path / "clip01_2x.mp4").read_bytes() == b"a"       # 기존 결과는 그대로


# ---------------------------------------------------------------- 절전 방지 (실제 API)
def test_keep_awake_real_api():
    ka = KeepAwake()
    ka.acquire()
    assert ka.active
    time.sleep(0.2)
    ka.release()
    assert not ka.active and ka.calls == ["acquire", "release"]
    ka.release()                                          # 중복 해제 무해
    assert ka.calls == ["acquire", "release"]


# ---------------------------------------------------------------- 실제 GPU 3개 연속
def test_real_batch_three_clips(tmp_path):
    env = detect_system_env()
    if not env.primary_gpu or not env.primary_gpu.vulkan_available:
        pytest.skip("Vulkan GPU 없음")
    from upcon.providers.local_ncnn import LocalNcnnProvider
    cfg = AppConfig()
    cfg.temp_dir = str(tmp_path / "tmp")
    provider = LocalNcnnProvider(cfg)
    srcs = []
    base = SAMPLES / "sample_480p.mp4"
    specs = [("한글 폴더/첫 번째.mp4", ["-t", "2", "-c", "copy"]),
             ("b/second.mov", ["-t", "2", "-c:v", "copy", "-c:a", "pcm_s16le"]),
             ("c/third_silent.mp4", ["-t", "2", "-an", "-c:v", "copy"])]
    for rel, args in specs:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(base), *args, str(dst)], check=True)
        srcs.append(dst)
    (tmp_path / "c" / "third_silent_2x.mp4").write_bytes(b"old")      # 이름 충돌 유도

    awake = FakeAwake()
    events = []
    m = JobManager(lambda j, cb: provider.upscale(j, cb, env), lambda j: None, events.append, awake)
    jobs = []
    for s in srcs:
        j = Job(input_path=s, scale=2, info=probe_video(s), provider_id=provider.id)
        assert m.add(j)
        jobs.append(j)
    m.start()
    _wait(lambda: "finished" in events, 180)
    assert [j.status for j in jobs] == [JobStatus.DONE] * 3, [j.error_message for j in jobs]
    outs = [j.output_path for j in jobs]
    assert outs[0].name == "첫 번째_2x.mp4" and outs[0].parent == srcs[0].parent
    assert outs[1].name == "second_2x.mp4"
    assert outs[2].name == "third_silent_2x_2.mp4" and (tmp_path / "c" / "third_silent_2x.mp4").read_bytes() == b"old"
    for j, expect_audio in zip(jobs, (True, True, False)):
        o = probe_video(j.output_path)
        assert (o.width, o.height) == (1708, 960) and o.has_audio == expect_audio
    assert awake.calls == ["acquire", "release"]
    assert not list((tmp_path / "tmp").glob("job_*"))
