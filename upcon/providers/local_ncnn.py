"""Tier 1 로컬 엔진: Real-ESRGAN (realesrgan-ncnn-vulkan.exe, Vulkan).

파이프라인 (청크 단위, 임시 디스크 사용량 상한 관리):

    원본 ─ffmpeg(rawvideo bgr24 파이프)─▶ 청크 BMP ─▶ ncnn-vulkan 2× ─▶ 청크 PNG
        ─▶ (피더 스레드) ffmpeg 인코더 stdin ─▶ H.264 MP4 + 원본 오디오 ─▶ 파일명_2x.mp4

- 원본은 읽기만 한다.
- 진행률은 ncnn 이 실제로 완료한 프레임 수 / 전체 프레임 수.
- 취소 시 모든 자식 프로세스를 종료하고 임시 폴더와 미완성 결과 파일을 지운다.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from upcon.core import ffmpeg as ff
from upcon.core import tempfs
from upcon.core.binaries import find_binary
from upcon.core.config import AppConfig
from upcon.core.env import GpuInfo, SystemEnv
from upcon.core.errors import BinaryNotFoundError, UpconError
from upcon.core.jobs import CancelToken, CancelledError, Job, Phase, Progress, ProgressCallback
from upcon.core.models import NcnnModelSpec, effective_requirements, get_model
from upcon.core.paths import bundled_models_dir
from upcon.core.probe import VideoInfo, probe_video
from upcon.providers.base import Availability, Estimate, UpscalerProvider

log = logging.getLogger(__name__)
_DONE_RE = re.compile(r" done\s*$")


def _read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


class LocalNcnnProvider(UpscalerProvider):
    id = "local_ncnn"
    name = "내 PC GPU (Real-ESRGAN)"
    kind = "local"
    temporal = False

    # 실측 처리량 (RTX 5060, general-x4v3, 2×) — estimate() 에서 대략적인 예상 시간 계산용
    _MPIX_PER_SEC = 5.0  # 입력 메가픽셀/초 (480p 0.41MP × 12fps ≈ 4.9)

    def __init__(self, config: AppConfig):
        self.config = config
        self._selftest_cache: dict[tuple[str, int], Availability] = {}

    # ------------------------------------------------------------------ 구성
    @property
    def spec(self) -> NcnnModelSpec:
        return get_model(self.config.local_model)

    def _exe(self) -> Path:
        return find_binary("realesrgan-ncnn-vulkan")

    def _models_dir(self) -> Path:
        return bundled_models_dir()

    def _model_files_ok(self, scale: int) -> bool:
        base = self._models_dir() / self.spec.file_basename(scale)
        return base.with_suffix(".param").exists() and base.with_suffix(".bin").exists()

    # ------------------------------------------------------------------ 가용성
    def check_availability(self, env: SystemEnv, scale: int = 2) -> Availability:
        try:
            self._exe()
        except BinaryNotFoundError as e:
            return Availability(False, e.user_message, e.detail)
        if scale not in self.spec.scales or not self._model_files_ok(scale):
            return Availability(False, "AI 모델 파일이 없습니다. 프로그램을 다시 설치해 주세요.",
                                f"model files missing: {self.spec.file_basename(scale)} in {self._models_dir()}")

        gpu = env.primary_gpu
        if gpu is None or not gpu.vulkan_available:
            return Availability(False, "내 PC에서 AI 처리를 할 수 있는 그래픽카드를 찾지 못했습니다.",
                                f"no vulkan gpu: {env.summary()}")

        req = effective_requirements(self.spec, self.config.routing.model_requirement_overrides)
        if gpu.vendor not in req.supported_vendors:
            return Availability(False, f"{gpu.display_name}은(는) 이 AI 모델을 지원하지 않습니다.", f"vendor {gpu.vendor}")
        need = req.min_vram_mb + self.config.routing.vram_safety_margin_mb
        if gpu.vram_mb and gpu.vram_mb < need:
            return Availability(False, f"{gpu.display_name}의 그래픽 메모리가 부족합니다 ({gpu.vram_mb} MB, 최소 {need} MB).",
                                f"vram {gpu.vram_mb} < {need}")

        # 실제 모델 실행 self-test (세션 캐시)
        key = (self.spec.id, gpu.vulkan_device_index)
        if key not in self._selftest_cache:
            self._selftest_cache[key] = self._self_test(gpu, scale)
        st = self._selftest_cache[key]
        if not st.ok:
            return st
        return Availability(True, f"내 PC GPU({gpu.display_name})에서 처리합니다.", f"gpu={gpu.name} model={self.spec.id}")

    def _self_test(self, gpu: GpuInfo, scale: int) -> Availability:
        """64×64 이미지를 실제로 업스케일해 본다. '모델 로드 + Vulkan 실행' 이 실제로 되는지 확인."""
        root = tempfs.default_temp_root(self.config.temp_dir)
        d = tempfs.new_job_dir(root)
        try:
            w = h = 64
            src = d / "t.bmp"
            ff.write_bmp(src, ff.bmp_header(w, h), bytes([90, 120, 160]) * (w * h), w, h)
            out = d / "t.png"
            cmd = self._ncnn_cmd(src, out, gpu, scale)
            t0 = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=120, creationflags=ff.CREATE_NO_WINDOW)
            dt = time.time() - t0
            if r.returncode != 0 or not out.exists():
                return Availability(False, self._explain_ncnn_failure(r.stderr),
                                    f"selftest rc={r.returncode}: {r.stderr[-500:]}")
            log.info("ncnn self-test ok on %s (%.1fs) model=%s", gpu.name, dt, self.spec.id)
            return Availability(True, "", "")
        except subprocess.TimeoutExpired:
            return Availability(False, "그래픽카드 초기화가 너무 오래 걸립니다. 드라이버를 업데이트해 주세요.", "selftest timeout")
        except OSError as e:
            return Availability(False, "AI 엔진을 실행할 수 없습니다. 프로그램을 다시 설치해 주세요.", f"selftest exec: {e}")
        finally:
            tempfs.cleanup_dir(d)

    @staticmethod
    def _explain_ncnn_failure(stderr: str) -> str:
        s = (stderr or "").lower()
        if "no vulkan device" in s or "vkcreateinstance" in s or "vulkan" in s and "fail" in s:
            return "그래픽카드 드라이버가 AI 처리를 지원하지 않습니다. 그래픽 드라이버를 최신으로 업데이트해 주세요."
        if "out of memory" in s or "vk_error_out_of_device_memory" in s or "vkallocatememory" in s:
            return "그래픽카드 메모리가 부족하여 AI 처리에 실패했습니다. 다른 프로그램을 종료한 뒤 다시 시도해 주세요."
        if "load model" in s or "param" in s and "fail" in s:
            return "AI 모델 파일을 불러오지 못했습니다. 프로그램을 다시 설치해 주세요."
        return "내 PC 그래픽카드로 AI 처리를 시작하지 못했습니다. 그래픽 드라이버를 업데이트하거나 클라우드 처리를 이용해 주세요."

    # ------------------------------------------------------------------ 예상
    def estimate(self, info: VideoInfo, scale: int) -> Estimate:
        mpix = info.width * info.height / 1e6 * max(info.nb_frames, 1)
        return Estimate(cost_usd=None, seconds=mpix / self._MPIX_PER_SEC, note="내 PC 처리 (비용 없음)")

    # ------------------------------------------------------------------ 실행
    def _ncnn_cmd(self, src: Path, dst: Path, gpu: GpuInfo, scale: int) -> list[str]:
        cmd = [str(self._exe()), "-i", str(src), "-o", str(dst),
               "-n", self.spec.ncnn_model_name(scale), "-s", str(scale),
               "-m", str(self._models_dir()), "-f", "png", "-j", "2:2:2", "-v"]
        if gpu.vulkan_device_index >= 0:
            cmd += ["-g", str(gpu.vulkan_device_index)]
        return cmd

    def upscale(self, job: Job, progress: ProgressCallback, env: SystemEnv | None = None) -> Path:
        cancel = job.cancel
        scale = job.scale
        progress(Progress(Phase.ANALYZE))
        info = job.info or probe_video(job.input_path)
        job.info = info
        cancel.raise_if_cancelled()

        gpu = (env.primary_gpu if env else None)
        if gpu is None:
            from upcon.core.env import detect_system_env
            gpu = detect_system_env().primary_gpu
        if gpu is None:
            raise UpconError("내 PC에서 AI 처리를 할 수 있는 그래픽카드를 찾지 못했습니다.", "no gpu at upscale()")

        out_dir = Path(self.config.output_dir) if self.config.output_dir else None
        output = job.output_path or ff.unique_output_path(info.path, scale, out_dir)
        job.output_path = output
        ff.ensure_writable_dir(output.parent)      # 30초 뒤가 아니라 지금 실패하도록

        # 디스크 계획/검사
        temp_root = tempfs.default_temp_root(self.config.temp_dir)
        plan = tempfs.plan_disk(info, scale, temp_root, output, self.config.temp_budget_mb,
                                self.config.chunk_frames_min, self.config.chunk_frames_max)
        log.info("disk plan: %s", plan.describe())
        tempfs.check_disk(plan, output)

        progress(Progress(Phase.PREPARE, 0, info.nb_frames))
        avail = self.check_availability(env or SystemEnv(gpus=[gpu]), scale)
        if not avail.ok:
            raise UpconError(avail.reason, avail.detail)
        cancel.raise_if_cancelled()

        job_dir = tempfs.new_job_dir(temp_root)
        t0 = time.time()
        frames_done = 0
        try:
            enc_name = ff.pick_encoder(self.config.output_encoder)
            try:
                frames_done = self._run_pipeline(info, output, gpu, scale, plan.chunk_frames, job_dir, cancel, progress, enc_name)
            except UpconError as e:
                if enc_name == "libx264" or "encoder" not in (e.detail or "").lower():
                    raise
                # 하드웨어 인코더(NVENC/AMF/QSV)가 죽은 경우 → CPU 인코더로 자동 재시도
                log.warning("hardware encoder %s failed (%s) → retrying with libx264", enc_name, e.detail[:200])
                tempfs.cleanup_dir(job_dir)
                job_dir = tempfs.new_job_dir(temp_root)
                if output.exists():
                    output.unlink()
                progress(Progress(Phase.PREPARE, 0, info.nb_frames, "인코더를 바꿔 다시 시작합니다"))
                frames_done = self._run_pipeline(info, output, gpu, scale, plan.chunk_frames, job_dir, cancel, progress, "libx264")
            progress(Progress(Phase.SAVE, frames_done, frames_done))
            self._verify_output(output, info, frames_done)
        except BaseException:
            ff_out = output
            if ff_out.exists():
                try:
                    ff_out.unlink()
                except OSError:
                    pass
            raise
        finally:
            tempfs.cleanup_dir(job_dir)

        dt = time.time() - t0
        log.info("upscale done: gpu=%s model=%s src=%s %dx%d %.3ffps %.1fs frames=%d → %s | %.1fs (%.2f fps, %.2fx realtime)",
                 gpu.name, self.spec.id, info.path.name, info.width, info.height, info.fps, info.duration_sec,
                 frames_done, output.name, dt, frames_done / dt if dt else 0,
                 (info.duration_sec / dt) if dt else 0)
        return output

    def _run_pipeline(self, info: VideoInfo, output: Path, gpu: GpuInfo, scale: int, chunk_frames: int,
                      job_dir: Path, cancel: CancelToken, progress: ProgressCallback, enc_name: str = "libx264") -> int:
        w, h = info.width, info.height
        frame_bytes = w * h * 3
        header = ff.bmp_header(w, h)
        fps = ff.fps_fraction(info)
        total = info.nb_frames

        dec_err = open(job_dir / "decoder.log", "wb")
        enc_err = open(job_dir / "encoder.log", "wb")
        decoder = encoder = None
        feeder = _Feeder(cancel)
        frames_done = 0
        last_report = 0.0

        def report(done: int, detail: str = "") -> None:
            nonlocal last_report
            now = time.time()
            if now - last_report >= 0.2 or done == total:
                last_report = now
                progress(Progress(Phase.UPSCALE, done, max(total, done), detail))

        producer: _Producer | None = None
        try:
            decoder = ff.start_frame_decoder(info.path, fps)
            decoder_stderr_thread = threading.Thread(target=_pump, args=(decoder.stderr, dec_err), daemon=True)
            decoder_stderr_thread.start()
            log.info("encoder: %s", enc_name)
            encoder = ff.start_encoder(output, info.path, info, fps, self.config.output_crf,
                                       self.config.output_preset, enc_name)
            encoder_stderr_thread = threading.Thread(target=_pump, args=(encoder.stderr, enc_err), daemon=True)
            encoder_stderr_thread.start()
            feeder.start(encoder.stdin)

            # 1) 디코더 → 청크 BMP 쓰기는 별도 스레드 (ncnn 처리와 겹침, 최대 1청크 선행)
            producer = _Producer(decoder.stdout, job_dir, chunk_frames, frame_bytes, header, w, h, cancel)
            producer.start()

            chunk_idx = 0
            report(0)
            while True:
                cancel.raise_if_cancelled()
                if feeder.error:
                    raise self._encoder_died(encoder, job_dir, feeder.error)
                item = producer.next_chunk()          # None = 디코딩 끝
                if item is None:
                    break
                in_dir, out_dir, n_in_chunk = item

                # 2) ncnn 업스케일 (stderr 의 'done' 라인으로 실제 진행률)
                self._run_ncnn_chunk(in_dir, out_dir, gpu, scale, cancel,
                                     lambda d: report(frames_done + d, f"{frames_done + d} / {max(total, frames_done + d)} 프레임"))
                produced = sorted(out_dir.glob("*.png"))
                if len(produced) != n_in_chunk:
                    raise UpconError("AI 업스케일 결과 프레임 수가 맞지 않습니다.",
                                     f"chunk {chunk_idx}: expected {n_in_chunk}, got {len(produced)}")
                frames_done += n_in_chunk
                report(frames_done, f"{frames_done} / {max(total, frames_done)} 프레임")

                # 3) 인코더로 전달 (백그라운드), 입력 청크는 즉시 삭제
                tempfs.cleanup_dir(in_dir)
                producer.release()
                feeder.enqueue(produced, out_dir)
                chunk_idx += 1

            producer.raise_if_failed()
            if decoder.wait(timeout=30) != 0:
                dec_err.flush()
                raise UpconError("영상을 읽는 중 오류가 발생했습니다. 파일이 손상되었을 수 있습니다.",
                                 f"decoder rc={decoder.returncode}: {_tail(job_dir / 'decoder.log')}")
            if frames_done == 0:
                raise UpconError("영상에서 프레임을 읽지 못했습니다.", "0 frames decoded")

            # 4) 남은 청크 전송 완료 대기 → 인코더 종료 (오디오 mux 는 인코더가 함께 처리)
            feeder.finish(raise_error=False)
            if feeder.error:
                raise self._encoder_died(encoder, job_dir, feeder.error)
            progress(Progress(Phase.AUDIO, frames_done, frames_done))
            cancel.raise_if_cancelled()
            try:
                encoder.stdin.close()
            except OSError:
                pass
            ff.check_encoder_exit(encoder, output)
            encoder_stderr_thread.join(timeout=5)
            return frames_done
        except BaseException:
            feeder.abort()
            if producer:
                producer.abort()
            ff.kill_process(decoder)
            ff.kill_process(encoder)
            raise
        finally:
            try:
                dec_err.close(); enc_err.close()
            except OSError:
                pass

    @staticmethod
    def _encoder_died(encoder, job_dir: Path, err: BaseException) -> UpconError:
        """인코더 프로세스가 먼저 종료된 경우: stderr 로그를 상세에 담아 원인을 남긴다."""
        rc = encoder.poll() if encoder else None
        try:
            if encoder and encoder.poll() is None:
                encoder.wait(timeout=5)
                rc = encoder.returncode
        except Exception:
            pass
        tail = _tail(job_dir / "encoder.log", 1200)
        cmd = " ".join(str(a) for a in getattr(encoder, "args", [])) if encoder else ""
        # 임시 폴더는 정리되지만, 원인 분석에 필요한 명령/종료코드/stderr 는 UPCON 로그에 남는다
        log.error("encoder died: rc=%s cmd=%s stderr=%s", rc, cmd, tail)
        return UpconError("결과 영상을 저장하는 중 인코더 오류가 발생했습니다.",
                          f"encoder exited rc={rc} ({type(err).__name__}: {err}) stderr: {tail[-400:]}")

    def _run_ncnn_chunk(self, in_dir: Path, out_dir: Path, gpu: GpuInfo, scale: int,
                        cancel: CancelToken, on_frame) -> None:
        cmd = self._ncnn_cmd(in_dir, out_dir, gpu, scale)
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             creationflags=ff.CREATE_NO_WINDOW)
        done = 0
        tail: list[str] = []

        def reader():
            nonlocal done
            for raw in iter(p.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if _DONE_RE.search(line):
                    done += 1
                    on_frame(done)
                else:
                    tail.append(line)
                    if len(tail) > 40:
                        tail.pop(0)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        while p.poll() is None:
            if cancel.cancelled:
                ff.kill_process(p)
                raise CancelledError()
            time.sleep(0.1)
        t.join(timeout=5)
        if p.returncode != 0:
            err = "\n".join(tail)
            raise UpconError(self._explain_ncnn_failure(err), f"ncnn rc={p.returncode}: {err[-600:]}")

    @staticmethod
    def _verify_output(output: Path, info: VideoInfo, frames: int) -> None:
        if not output.exists() or output.stat().st_size == 0:
            raise UpconError("결과 파일이 만들어지지 않았습니다.", "output missing")
        out = probe_video(output)
        if out.width != info.width * 2 and out.width % info.width != 0:
            raise UpconError("결과 영상의 해상도가 예상과 다릅니다.", f"{out.width}x{out.height}")
        if info.has_audio and not out.has_audio:
            raise UpconError("결과 영상에 오디오가 들어가지 않았습니다.", "audio missing in output")
        if abs(out.duration_sec - info.duration_sec) > max(1.0, info.duration_sec * 0.02):
            log.warning("duration differs: src %.2fs out %.2fs", info.duration_sec, out.duration_sec)
        log.info("output verified: %s %dx%d %.3ffps %.2fs audio=%s size=%s",
                 output.name, out.width, out.height, out.fps, out.duration_sec, out.has_audio, out.size_text)


# ---------------------------------------------------------------------- 보조
def _pump(src, dst) -> None:
    """자식 프로세스 stderr → 파일. 오류 시 바로 읽을 수 있도록 매번 flush."""
    try:
        for chunk in iter(lambda: src.read(4096), b""):
            dst.write(chunk)
            dst.flush()
    except (OSError, ValueError):
        pass


def _tail(p: Path, n: int = 600) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


class _Producer:
    """디코더 파이프에서 프레임을 읽어 청크 폴더에 BMP 로 쓰는 스레드. 최대 1청크만 앞서간다."""

    def __init__(self, stdout, job_dir: Path, chunk_frames: int, frame_bytes: int, header: bytes,
                 w: int, h: int, cancel: CancelToken):
        self._stdout, self._job_dir, self._chunk = stdout, job_dir, chunk_frames
        self._frame_bytes, self._header, self._w, self._h = frame_bytes, header, w, h
        self._cancel = cancel
        self._q: queue.Queue = queue.Queue()
        self._slots = threading.Semaphore(2)   # 쓰는 중 1개 + ncnn 처리 중 1개
        self._abort = False
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._loop, name="upcon-producer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def next_chunk(self):
        while True:
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                self.raise_if_failed()
                self._cancel.raise_if_cancelled()
                continue
            return item

    def release(self) -> None:
        self._slots.release()

    def raise_if_failed(self) -> None:
        if self.error:
            raise UpconError("영상을 읽는 중 오류가 발생했습니다.", f"producer: {self.error}")

    def abort(self) -> None:
        self._abort = True
        self._slots.release()

    def _loop(self) -> None:
        try:
            frames_read = 0
            chunk_idx = 0
            eof = False
            while not eof and not self._abort:
                self._slots.acquire()
                if self._abort:
                    return
                in_dir = self._job_dir / f"c{chunk_idx:04d}_in"
                out_dir = self._job_dir / f"c{chunk_idx:04d}_out"
                in_dir.mkdir(); out_dir.mkdir()
                n = 0
                while n < self._chunk:
                    if self._abort or self._cancel.cancelled:
                        return
                    data = _read_exact(self._stdout, self._frame_bytes)
                    if len(data) < self._frame_bytes:
                        eof = True
                        break
                    ff.write_bmp(in_dir / f"f{frames_read:07d}.bmp", self._header, data, self._w, self._h)
                    frames_read += 1
                    n += 1
                if n == 0:
                    in_dir.rmdir(); out_dir.rmdir()
                    break
                self._q.put((in_dir, out_dir, n))
                chunk_idx += 1
            self._q.put(None)
        except BaseException as e:
            self.error = e
            self._q.put(None)


class _Feeder:
    """업스케일된 PNG 청크를 순서대로 인코더 stdin 에 써 넣고 지우는 스레드."""

    def __init__(self, cancel: CancelToken):
        self._q: queue.Queue = queue.Queue()
        self._cancel = cancel
        self._stdin = None
        self._thread: threading.Thread | None = None
        self.error: BaseException | None = None

    def start(self, stdin) -> None:
        self._stdin = stdin
        self._thread = threading.Thread(target=self._loop, name="upcon-feeder", daemon=True)
        self._thread.start()

    def enqueue(self, files: list[Path], out_dir: Path) -> None:
        self._q.put((files, out_dir))

    def raise_if_failed(self) -> None:
        if self.error:
            raise UpconError("결과 영상을 저장하는 중 오류가 발생했습니다.", f"feeder: {self.error}")

    def finish(self, raise_error: bool = True) -> None:
        self._q.put(None)
        if self._thread:
            self._thread.join()
        if raise_error:
            self.raise_if_failed()

    def abort(self) -> None:
        self._q.put(None)

    def _loop(self) -> None:
        try:
            while True:
                item = self._q.get()
                if item is None:
                    return
                files, out_dir = item
                for f in files:
                    if self._cancel.cancelled:
                        return
                    with open(f, "rb") as fh:
                        while True:
                            b = fh.read(1 << 20)
                            if not b:
                                break
                            self._stdin.write(b)
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                tempfs.cleanup_dir(out_dir)
        except BaseException as e:  # 인코더가 죽으면 BrokenPipe 등
            self.error = e
