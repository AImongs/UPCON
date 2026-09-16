"""배치 대기열 UI 스모크/시나리오 테스트 (수동 실행). 실제 창을 띄우고 자동 조작하며 스크린샷을 저장한다.

    .venv\\Scripts\\python tests\\ui_smoke.py [출력폴더]

시나리오 (사용자 요구 A~N):
 A 3개 드래그앤드롭   B 10개 파일 선택(add)   C MOV+MP4 혼합   D 한글 파일명/경로   E 오디오 있음/없음 혼합
 F 한 파일 실패(깨진 파일) 후 계속   G 현재 파일 건너뛰기   H 전체 중지   I 중지 후 재시작   J 출력 파일명 충돌
 K 여러 폴더 혼합   L 앱 종료 경고   M 대기열 복원(앱 재시작)   N 절전 방지 활성/해제
 + 설정 대화상자, 클라우드 모드 총비용 확인 대화상자(취소 시 요청 없음)
사용자 실제 설정(%LOCALAPPDATA%/UPCON)은 UPCON_DATA_DIR 로 격리한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = tempfile.mkdtemp(prefix="upcon_ui_data_")
os.environ["UPCON_DATA_DIR"] = DATA_DIR

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from upcon.__main__ import create_app  # noqa: E402
from upcon.app.cloud_settings import CloudSettingsDialog  # noqa: E402
from upcon.core import credentials  # noqa: E402
from upcon.core import ffmpeg as ff  # noqa: E402
from upcon.core.constants import ProcessMode  # noqa: E402
from upcon.core.jobs import JobStatus  # noqa: E402
from upcon.core.probe import probe_video  # noqa: E402
from upcon.core.tempfs import default_temp_root  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="upcon_ui_"))
results: list[str] = []


def shot(w, name: str) -> None:
    w.grab().save(str(OUT / f"{name}.png"))


def base_clip() -> Path:
    """테스트용 원본(854×480 / 24fps / 5초 / 오디오 있음)을 FFmpeg 로 생성한다.
    저장소에 영상을 두지 않으므로, 새로 clone 한 환경에서도 준비 과정 없이 실행된다."""
    dst = WORK / "_base_480p.mp4"
    if not dst.exists():
        subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(dst)], check=True)
    return dst


def make(rel: str, args: list[str], src: Path | None = None) -> Path:
    dst = WORK / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(src or base_clip()), *args, str(dst)], check=True)
    return dst


def ok(msg):
    results.append("PASS " + msg)


def fail(msg):
    results.append("FAIL " + msg)


def main() -> int:
    # ---- 테스트 파일: 여러 폴더, MOV/MP4, 한글, 무음, 깨진 파일 ----
    clips = [
        make("폴더A/클립01.mp4", ["-t", "2", "-c", "copy"]),
        make("폴더A/clip02.mov", ["-t", "2", "-c:v", "copy", "-c:a", "pcm_s16le"]),
        make("folderB/clip03_silent.mp4", ["-t", "2", "-an", "-c:v", "copy"]),
    ]
    broken = WORK / "folderB" / "broken04.mp4"
    broken.write_bytes(b"\x00" * 4096)
    ten = [make(f"many/clip{i:02d}.mp4", ["-t", "1", "-c", "copy"]) for i in range(5, 15)]
    (WORK / "many" / "clip05_2x.mp4").write_bytes(b"old")          # J: 이름 충돌 (clip05 는 끝까지 처리됨)

    app, win = create_app()
    win.show()
    captured: list[str] = []

    def auto_close():
        for w in app.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                captured.append(w.text())
                shot(w, "msgbox")
                w.reject()
            elif isinstance(w, CloudSettingsDialog) and w.isVisible():
                shot(w, "12_cloud_settings")
                captured.append("settings-dialog")
                w.reject()
    poll = QTimer()
    poll.timeout.connect(auto_close)
    poll.start(150)

    batch_events: list[str] = []
    win.controller.batchEvent.connect(batch_events.append)
    mid = {"shot": False}

    def on_job(j):
        if j.status == JobStatus.RUNNING and (j.progress.percent or 0) >= 40 and not mid["shot"]:
            mid["shot"] = True
            shot(win, "03_running")
    win.controller.jobUpdated.connect(on_job)

    def jobs():
        return list(win.controller.jobs.jobs)

    def wait_until(cond, cb, timeout_ms=240000):
        elapsed = [0]

        def tick():
            elapsed[0] += 150
            try:
                if cond():
                    cb()
                    return
            except AssertionError as e:
                fail(str(e))
                app.quit()
                return
            if elapsed[0] > timeout_ms:
                fail("TIMEOUT")
                app.quit()
            else:
                QTimer.singleShot(150, tick)
        tick()

    def step(fn):
        """단계 실행; assert 실패 시 기록하고 종료."""
        try:
            fn()
        except AssertionError as e:
            fail(f"{fn.__name__}: {e}")
            app.quit()

    # ---- 1) 초기 + A 드래그앤드롭 + K 여러 폴더 + C 혼합 + D 한글 + E 오디오 혼합 ----
    def s1():
        shot(win, "01_initial")
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(str(p)) for p in clips] + [QUrl.fromLocalFile(str(broken))])
        enter = QDragEnterEvent(QPoint(30, 30), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(win.queue, enter)
        ev = QDropEvent(QPointF(30, 30), Qt.DropAction.CopyAction, md, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        app.sendEvent(win.queue, ev)
        assert len(jobs()) == 4, f"드롭 후 {len(jobs())}개"
        ok("A 드래그앤드롭 4개 등록 (C MOV+MP4 혼합, D 한글, E 무음/오디오 혼합, K 여러 폴더)")
        win.add_files(ten)
        win.add_files(clips)                       # 중복 → 무시
        assert len(jobs()) == 14, f"{len(jobs())}개"
        ok("B 10개 추가 + 중복 등록 방지 (총 14개)")
        wait_until(lambda: all(j.info is not None or j.status == JobStatus.FAILED for j in jobs()), lambda: step(s2))

    # ---- 2) 표 확인, 순서 변경, 삭제, 클라우드 비용 대화상자 ----
    def s2():
        shot(win, "02_queue_loaded")
        assert win.queue.table.rowCount() == 14
        js = jobs()
        assert js[3].status == JobStatus.FAILED and "읽을 수 없습니다" in js[3].error_message, "깨진 파일이 실패로 표시되어야 함"
        last = js[-1]
        win.controller.move_job(last, -1)
        win.controller.move_job(last, -1)
        win._refresh_table()
        assert jobs()[-3] is last
        ok("순서 변경(↑) OK")
        keep = {last.input_path, ten[0]}
        win._remove_jobs([j for j in jobs() if j.input_path in set(ten) and j.input_path not in keep])
        assert len(jobs()) == 6, f"{len(jobs())}개"
        ok("선택 삭제 OK (14 → 6)")
        real_get = credentials.get_fal_key
        credentials.get_fal_key = lambda: "12345678-abcd-4321-abcd-1234567890ab:fakefakefakefakefakefakefakefake"
        win.options.set_mode(ProcessMode.CLOUD)
        t0 = time.time()                       # 환경 감지 완료 후에 안내 문구를 확인한다
        while win.controller.env is None and time.time() - t0 < 30:
            app.processEvents()
            time.sleep(0.05)
        win._refresh_cost()
        hint = win.options.env_hint.text()
        assert "무료" not in hint, f"클라우드 모드인데 로컬 안내 문구: {hint}"
        assert "클라우드" in hint, f"클라우드 안내 문구가 아님: {hint}"
        shot(win, "11_cloud_mode_costs")
        n = len(captured)
        win.start_btn.click()
        assert len(captured) > n and "예상 비용" in captured[-1] and "5개" in captured[-1], captured[-1:]
        assert not win._batch_running
        credentials.get_fal_key = real_get
        ok("클라우드 모드: 파일별/전체 예상 비용 표시 + 확인 대화상자(취소 시 요청 없음)")
        win.options.set_mode(ProcessMode.AUTO)
        win._refresh_cost()
        s3()

    # ---- 3) 전체 시작 → N 절전 방지 → L 종료 경고 → G 건너뛰기 → H 전체 중지 ----
    def s3():
        win.start_btn.click()
        assert win._batch_running and win.stop_btn.isVisible() and win.skip_btn.isVisible()
        wait_until(lambda: win.controller.keep_awake.active, lambda: step(s3b), 20000)

    def s3b():
        ok("N 절전 방지 활성 (배치 시작)")
        wait_until(lambda: any(j.status == JobStatus.RUNNING and (j.progress.percent or 0) >= 20 for j in jobs()), lambda: step(s3c), 60000)

    def s3c():
        ev = QCloseEvent()
        win.closeEvent(ev)
        assert not ev.isAccepted(), f"closeEvent accepted={ev.isAccepted()} captured={captured[-2:]}"
        assert any("종료하면 현재 작업이 취소됩니다" in c for c in captured[-3:]), f"경고 문구 없음: {captured[-3:]}"
        assert win._batch_running and win.controller.jobs.running, "종료 경고 후에도 배치가 계속 실행되어야 함"
        ok("L 처리 중 종료 경고 → '계속 작업' 선택 시 창 유지")
        cur = win.controller.jobs.current
        win.skip_btn.click()
        wait_until(lambda: cur.status == JobStatus.CANCELLED and win.controller.jobs.current not in (None, cur),
                   lambda: step(lambda: s3d(cur)), 60000)

    def s3d(skipped):
        ok(f"G 현재 파일 건너뛰기 → {skipped.input_path.name} 취소됨, 다음 파일 자동 시작")
        win.stop_btn.click()
        wait_until(lambda: "stopped" in batch_events, lambda: step(s4), 60000)

    # ---- 4) 중지 상태, 재시작(I) ----
    def s4():
        shot(win, "04_stopped")
        st = [j.status for j in jobs()]
        assert not win._batch_running and not win.controller.keep_awake.active
        assert JobStatus.PENDING in st, st
        ok(f"H 전체 중지 → 대기 {st.count(JobStatus.PENDING)}개 유지, 절전 방지 해제")
        qf = Path(DATA_DIR) / "queue.json"
        assert qf.exists() and "pending" in qf.read_text(encoding="utf-8")
        win.start_btn.click()
        assert win._batch_running
        ok("I 중지 후 재시작 → 남은 대기 항목부터 계속")
        wait_until(lambda: "finished" in batch_events, lambda: step(s5), 240000)

    # ---- 5) 완료 검증 ----
    def s5():
        shot(win, "05_finished")
        js = jobs()
        done = [j for j in js if j.status == JobStatus.DONE]
        failed = [j for j in js if j.status == JobStatus.FAILED]
        cancelled = [j for j in js if j.status == JobStatus.CANCELLED]
        assert len(failed) == 1 and failed[0].input_path.name == "broken04.mp4", [(j.input_path.name, j.status) for j in js]
        # 취소 2개 = 건너뛰기(G) 1개 + 전체 중지(H) 시 처리 중이던 1개
        assert len(cancelled) == 2 and len(done) == 3, [(j.input_path.name, j.status) for j in js]
        ok(f"F 깨진 파일 1개 실패 후 나머지 계속 처리 (완료 {len(done)} / 실패 1 / 취소 2=건너뛰기+중지)")
        for j in done:
            o = probe_video(j.output_path)
            i = probe_video(j.input_path)
            assert (o.width, o.height) == (i.width * 2, i.height * 2) and abs(o.fps - i.fps) < 0.01
            assert abs(o.duration_sec - i.duration_sec) < 0.15 and o.has_audio == i.has_audio
            assert j.output_path.parent == j.input_path.parent
        ok("완료 파일 검증 OK (해상도 2×, FPS, 길이, 오디오 유무, 원본 옆 저장)")
        k5 = next(j for j in done if j.input_path.name == "clip05.mp4")
        assert k5.output_path.name == "clip05_2x_2.mp4" and (WORK / "many" / "clip05_2x.mp4").read_bytes() == b"old"
        ok("J 출력 파일명 충돌 → clip05_2x_2.mp4 (기존 결과 보존)")
        assert not list(default_temp_root(win.config.temp_dir).glob("job_*"))
        assert not win.controller.keep_awake.active
        ok("임시 폴더 정리, 절전 방지 해제 확인")
        win.queue.retry_btn.click()
        assert any(j.status == JobStatus.PENDING for j in jobs())
        ok("실패 항목 다시 시도 → 대기열 복귀")
        win._open_settings()
        assert "settings-dialog" in captured
        ok("설정 대화상자 OK")
        s6()

    # ---- 6) M: 앱 재시작 복원 ----
    def s6():
        js = jobs()
        pend = [j for j in js if j.status == JobStatus.PENDING]
        pend[0].status = JobStatus.RUNNING            # 비정상 종료 시점 흉내
        win.controller.save_queue()
        pend[0].status = JobStatus.PENDING
        win.controller.jobs.shutdown()
        win.hide()
        from upcon.app.main_window import MainWindow
        from upcon.core.config import AppConfig
        win2 = MainWindow(AppConfig.load())
        win2.show()

        def check():
            js2 = list(win2.controller.jobs.jobs)
            st = {j.input_path.name: j.status for j in js2}
            assert st.get(pend[0].input_path.name) == JobStatus.INTERRUPTED, st
            assert all(s != JobStatus.DONE for s in st.values())
            shot(win2, "06_restored")
            ok(f"M 대기열 복원: {len(js2)}개 (처리 중이던 항목 → 중단됨, 완료 항목 제외)")
            win2.controller.shutdown()
            app.quit()
        wait_until(lambda: all(j.info is not None or j.status != JobStatus.PENDING for j in win2.controller.jobs.jobs),
                   lambda: step(check), 20000)

    QTimer.singleShot(800, lambda: step(s1))
    app.exec()
    print("\n".join(results))
    print(f"screenshots: {OUT}")
    shutil.rmtree(WORK, ignore_errors=True)
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    return 1 if any(r.startswith("FAIL") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
