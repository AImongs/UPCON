"""클라우드 Provider: fal.ai FlashVSR (`fal-ai/flashvsr/upscale/video`).

흐름: 영상 정보 → 예상 비용(UI 가 사용자 확인) → fal CDN 업로드(만료 설정) → Queue submit
     → status 폴링(큐 순번/경과 표시, 실제 % 없음) → 결과 URL → 바이트 진행률 다운로드
     → ffprobe 검증(해상도·오디오; 오디오 누락 시 로컬 mux) → 파일명_2x.mp4 저장 → 실제 청구액 조회.

API 사실 (공식 문서 2026-09-15): 입력 `video_url`, `upscale_factor`(기본 2), `preserve_audio`,
`acceleration`(regular/high/full), `color_fix`(true), `quality`(70), `output_format`("X264 (.mp4)").
출력 `video.url`. 가격 $0.0005/MP(출력 W×H×프레임). 길이/용량 제한은 문서에 없음 → 실측.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from fal_client.client import Completed, InProgress, Queued, StorageSettings

from upcon.core import ffmpeg as ff
from upcon.core import pricing, tempfs
from upcon.core.config import AppConfig
from upcon.core.constants import DEFAULT_OUTPUT_MODE, OutputMode
from upcon.core.env import SystemEnv
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, Job, Phase, Progress, ProgressCallback
from upcon.core.probe import VideoInfo, format_bytes, probe_video
from upcon.providers.base import Availability, Estimate, UpscalerProvider
from upcon.providers.fal_base import FalApi, FalNetworkError, explain_fal_error

log = logging.getLogger(__name__)

ENDPOINT = "fal-ai/flashvsr/upscale/video"
UPLOAD_EXPIRES = "1d"          # 업로드한 원본은 하루 뒤 CDN 에서 자동 삭제 (개인정보 최소화)
POLL_INTERVAL = 2.0
MAX_WAIT_SEC = 3 * 3600
MAX_TRANSIENT_RETRIES = 5      # 폴링/결과조회 일시적 네트워크 오류 재시도 횟수 (같은 request_id 재조회, 재-submit 없음)


def _with_transient_retry(fn, what: str, cancel):
    """일시적 네트워크 오류만 같은 request(=fn 이 호출하는 handle.status/get)로 재시도한다.

    submit(과금 요청)은 이 함수를 쓰지 않는다 — 여기서 재시도하는 건 이미 접수된 요청의 상태/결과를
    다시 조회하는 것뿐이라 재시도해도 새 과금이 생기지 않는다. 네트워크가 아닌 오류(인증/잔액/서버가
    명시적으로 돌려준 오류 등)는 재시도하지 않고 바로 올린다.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            err = explain_fal_error(e)
            attempt += 1
            if not isinstance(err, FalNetworkError) or attempt > MAX_TRANSIENT_RETRIES:
                raise err
            wait = min(2 ** attempt, 30)
            log.warning("fal %s 일시적 오류 (%d/%d회), 같은 요청으로 %d초 후 재조회: %s",
                       what, attempt, MAX_TRANSIENT_RETRIES, wait, err.detail)
            cancel.raise_if_cancelled()
            time.sleep(wait)


class FalFlashVSRProvider(UpscalerProvider):
    id = "fal_flashvsr"
    name = "클라우드 GPU (fal.ai FlashVSR)"
    kind = "cloud"
    temporal = True
    endpoint = ENDPOINT

    def __init__(self, config: AppConfig):
        self.config = config

    # ------------------------------------------------------------ 가용성/예상
    def check_availability(self, env: SystemEnv, scale: int = 2,
                           output_mode: OutputMode = DEFAULT_OUTPUT_MODE) -> Availability:
        """STEP 10: FlashVSR API 는 배율(upscale_factor)만 받고 목표 해상도 개념이 없다.
        API 계약을 새로 만들지 않고(클라우드 모델 변경 금지), 2× 가 아닌 출력 모드는 아예
        사용 불가로 보고한다 — 자동 모드에서 로컬로 넘어가거나, 사용자에게 정직하게 안내한다."""
        if output_mode != OutputMode.TWO_X:
            return Availability(False, "클라우드 GPU는 아직 2× 업스케일만 지원합니다. 1080p/4K는 내 PC GPU를 이용해 주세요.",
                                f"cloud does not support output_mode={output_mode.value}")
        if not FalApi().has_key:
            return Availability(False, "클라우드 GPU를 쓰려면 설정 → 클라우드 업스케일에서 fal.ai 계정을 연결해 주세요.", "no fal key")
        return Availability(True, "클라우드 GPU(fal.ai FlashVSR)를 사용합니다.", "fal key present")

    def unit_price(self) -> float | None:
        v = self.config.cloud_unit_prices.get(self.endpoint)
        return float(v) if v else None

    def cost(self, info: VideoInfo, scale: int) -> pricing.CostEstimate:
        return pricing.estimate_cost(info, scale, self.endpoint, self.unit_price())

    def estimate(self, info: VideoInfo, scale: int) -> Estimate:
        c = self.cost(info, scale)
        return Estimate(cost_usd=c.usd_display, seconds=None, note=c.basis_text())

    # ------------------------------------------------------------ 실행
    def upscale(self, job: Job, progress: ProgressCallback, env: SystemEnv | None = None) -> Path:
        cancel = job.cancel
        scale = job.scale
        progress(Progress(Phase.ANALYZE))
        info = job.info or probe_video(job.input_path)
        job.info = info
        job.estimated_cost_usd = self.cost(info, scale).usd_display
        cancel.raise_if_cancelled()

        api = FalApi()
        api._require_key()
        out_dir = Path(self.config.output_dir) if self.config.output_dir else None
        output = job.output_path or ff.unique_output_path(info.path, scale, out_dir)
        job.output_path = output
        ff.ensure_writable_dir(output.parent)

        # 디스크: 결과 다운로드 + 검증용 임시본
        temp_root = tempfs.default_temp_root(self.config.temp_dir)
        out_est_mb = int(info.size_bytes * 4 / (1024 * 1024)) + 50
        if tempfs.free_disk_mb(temp_root) < out_est_mb * 2 + tempfs.SAFETY_MARGIN_MB:
            raise UpconError("결과를 저장할 디스크 공간이 부족합니다.", f"need ~{out_est_mb * 2}MB")

        job_dir = tempfs.new_job_dir(temp_root)
        handle = None
        t0 = time.time()
        try:
            client = api.client()

            # 1) 업로드
            progress(Progress(Phase.UPLOAD, detail=f"{info.path.name} ({info.size_text})"))
            t_up = time.time()
            try:
                video_url = client.upload_file(info.path, lifecycle=StorageSettings(expires_in=UPLOAD_EXPIRES))
            except Exception as e:  # noqa: BLE001
                raise explain_fal_error(e)
            log.info("fal upload done in %.1fs (%s)", time.time() - t_up, info.size_text)
            cancel.raise_if_cancelled()
            progress(Progress(Phase.UPLOADED, detail=f"{time.time() - t_up:.0f}초 걸림"))

            # 2) submit
            args = {
                "video_url": video_url,
                "upscale_factor": scale,
                "preserve_audio": True,
                "output_format": "X264 (.mp4)",
            }
            args.update(self.config.cloud_extra_args or {})
            headers = {"X-Fal-Object-Lifecycle-Preference": json.dumps({"expiration_duration_seconds": 7 * 86400})}
            try:
                # 과금 지점: 자동 재시도 없이 정확히 1회만 전송 (이중 과금 방지)
                handle = api.submit_once(client, self.endpoint, args, headers=headers)
            except Exception as e:  # noqa: BLE001
                err = explain_fal_error(e)
                if isinstance(err, FalNetworkError):
                    err = UpconError(
                        "클라우드에 요청을 보내는 중 연결이 끊겼습니다. 요청이 접수됐을 수 있으니 "
                        "자동으로 다시 보내지 않았습니다. fal.ai 대시보드에서 요청 상태를 확인한 뒤 다시 시도해 주세요.",
                        f"submit transport failure (not retried): {err.detail}")
                raise err
            job.note = ""
            log.info("fal submitted request (id=%s)", handle.request_id)

            # 3) 상태 폴링 (실제 % 없음 → 큐 순번/경과 시간만)
            t_q = time.time()
            in_progress_since: float | None = None
            while True:
                if cancel.cancelled:
                    self._try_cancel(handle, job)
                    raise CancelledError()
                st = _with_transient_retry(lambda: handle.status(with_logs=False), "상태 조회", cancel)
                if isinstance(st, Queued):
                    progress(Progress(Phase.QUEUE, detail=f"대기열 {st.position + 1}번째 · {int(time.time() - t_q)}초 경과"))
                elif isinstance(st, InProgress):
                    in_progress_since = in_progress_since or time.time()
                    progress(Progress(Phase.CLOUD, detail=f"{int(time.time() - in_progress_since)}초 경과 (클라우드는 진행률을 제공하지 않습니다)"))
                elif isinstance(st, Completed):
                    if st.error:
                        raise UpconError("클라우드 업스케일이 실패했습니다. 잠시 후 다시 시도하거나 다른 영상으로 시도해 주세요.",
                                         f"fal completed with error: {st.error_type} {st.error[:300]}")
                    break
                if time.time() - t_q > MAX_WAIT_SEC:
                    raise UpconError("클라우드 처리가 너무 오래 걸려 중단했습니다.", "poll timeout")
                time.sleep(POLL_INTERVAL)
            inference_time = None
            if isinstance(st, Completed):
                inference_time = (st.metrics or {}).get("inference_time")

            # 4) 결과 (처리는 이미 끝났음 — 결과 조회 재시도도 새 과금과 무관)
            result = _with_transient_retry(lambda: handle.get(), "결과 조회", cancel)
            video = (result or {}).get("video") or {}
            url = video.get("url")
            if not url:
                raise UpconError("클라우드가 결과 영상을 돌려주지 않았습니다.", f"no video url in result keys={list((result or {}).keys())}")
            size_hint = int(video.get("file_size") or 0)

            # 5) 다운로드 (바이트 진행률)
            tmp_out = job_dir / "result.mp4"
            self._download(url, tmp_out, size_hint, cancel, progress)

            # 6) 검증 + 저장
            progress(Progress(Phase.SAVE))
            final_tmp = self._verify_and_fix_audio(tmp_out, info, scale, job_dir)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(final_tmp), str(output))
            dt = time.time() - t0
            log.info("cloud upscale done: %s %dx%d %.2fs → %s | total %.1fs, fal inference %s s, est $%.4f",
                     info.path.name, info.width, info.height, info.duration_sec, output.name, dt,
                     inference_time, job.estimated_cost_usd or 0)

            # 7) 실제 청구액 (집계 지연 가능 → 짧게만 시도)
            actual = None
            for _ in range(3):
                actual = api.actual_cost_usd(handle.request_id, self.endpoint)
                if actual is not None:
                    break
                time.sleep(2)
            job.actual_cost_usd = actual
            job.note = (f"실제 청구 ${actual:.4f}" if actual is not None else
                        f"예상 비용 ${job.estimated_cost_usd:.2f} (실제 청구액은 fal.ai 대시보드에서 확인)")
            return output
        except BaseException:
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            raise
        finally:
            tempfs.cleanup_dir(job_dir)

    # ------------------------------------------------------------ 보조
    def _try_cancel(self, handle, job: Job) -> None:
        try:
            st = handle.status(with_logs=False)
            handle.cancel()
            if isinstance(st, InProgress):
                job.note = "이미 처리 중이던 요청은 취소가 반영되지 않을 수 있으며, 그 경우 비용이 청구될 수 있습니다."
            else:
                job.note = "대기열에서 제거되었습니다 (비용 없음)."
            log.info("fal cancel requested (state=%s)", type(st).__name__)
        except Exception as e:  # noqa: BLE001
            job.note = "클라우드 요청 취소를 전달하지 못했습니다. fal.ai 대시보드에서 요청 상태를 확인해 주세요."
            log.warning("fal cancel failed: %s", type(e).__name__)

    def _download(self, url: str, dst: Path, size_hint: int, cancel, progress: ProgressCallback) -> None:
        done = 0
        last = 0.0
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, read=120.0), follow_redirects=True) as c:
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length") or size_hint or 0)
                    with open(dst, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            if cancel.cancelled:
                                raise CancelledError()
                            f.write(chunk)
                            done += len(chunk)
                            now = time.time()
                            if now - last > 0.2:
                                last = now
                                frac = (done / total) if total else None
                                progress(Progress(Phase.DOWNLOAD, fraction=frac,
                                                  detail=f"{format_bytes(done)}" + (f" / {format_bytes(total)}" if total else "")))
        except CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            raise explain_fal_error(e)
        if done == 0:
            raise UpconError("결과 영상을 다운로드하지 못했습니다.", "empty download")
        log.info("downloaded result %s", format_bytes(done))

    def _verify_and_fix_audio(self, tmp: Path, info: VideoInfo, scale: int, job_dir: Path) -> Path:
        out = probe_video(tmp)
        exp_w, exp_h = info.width * scale, info.height * scale
        if abs(out.width - exp_w) > 32 or abs(out.height - exp_h) > 32:
            log.warning("cloud output size %dx%d differs from expected %dx%d", out.width, out.height, exp_w, exp_h)
        if abs(out.duration_sec - info.duration_sec) > max(1.0, info.duration_sec * 0.05):
            log.warning("cloud output duration %.2fs vs source %.2fs", out.duration_sec, info.duration_sec)
        log.info("cloud output verified: %dx%d %.3ffps %.2fs audio=%s size=%s",
                 out.width, out.height, out.fps, out.duration_sec, out.has_audio, out.size_text)
        if info.has_audio and not out.has_audio:
            # preserve_audio 가 동작하지 않은 경우: 원본 오디오를 로컬에서 mux
            log.warning("cloud output has no audio → muxing original audio locally")
            fixed = job_dir / "result_audio.mp4"
            acodec = ["-c:a", "copy"] if info.audio_codec in ("aac", "mp3", "ac3", "eac3", "alac") else ["-c:a", "aac", "-b:a", "192k"]
            cmd = [str(ff.ffmpeg_path()), "-v", "error", "-y", "-i", str(tmp), "-i", str(info.path),
                   "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *acodec, "-shortest", "-movflags", "+faststart", str(fixed)]
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=ff.CREATE_NO_WINDOW)
            if r.returncode != 0 or not fixed.exists():
                raise UpconError("결과 영상에 오디오를 합치지 못했습니다.", f"mux rc={r.returncode}: {r.stderr[-400:]}")
            return fixed
        return tmp
