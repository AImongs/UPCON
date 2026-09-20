"""ByteDance PRO 클라우드 provider 단위 테스트 — 실제 API는 호출하지 않는다 (유료 호출 0회).

fal_flashvsr.py 의 기존 테스트 패턴(tests/test_cloud.py)을 그대로 따른다: 실제 네트워크 대신
FalApi.client/submit_once/httpx.Client 를 monkeypatch 로 대체해 전체 흐름을 끝까지 돌린다.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import httpx
import keyring
import pytest
from fal_client.client import Completed, FalClientHTTPError, InProgress, Queued

from upcon.core import pricing
from upcon.core.config import AppConfig
from upcon.core.errors import UpconError
from upcon.core.jobs import CancelledError, Job, JobManager, JobStatus, Phase
from upcon.core.probe import VideoInfo, probe_video
from upcon.providers.fal_bytedance import ENDPOINT, FalByteDanceProvider, resolve_target_fps

FAKE_KEY = "12345678-abcd-4321-abcd-1234567890ab:0123456789abcdef0123456789abcdef"


class _MemKeyring(keyring.backend.KeyringBackend):
    """tests/test_cloud.py 와 동일한 패턴 — 진짜 credential store 를 절대 건드리지 않는 가짜 백엔드.
    (test_installer_policy.py::test_no_code_deletes_production_credential 이 test_ 함수 본문에서
    직접 delete_password 를 정의/호출하는지 정적 검사한다 — 그래서 module 레벨에 둔다.)"""

    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


def _info(w, h, fps, dur, frames=0, has_audio=True):
    return VideoInfo(path=Path("x.mp4"), width=w, height=h, fps=fps, duration_sec=dur, size_bytes=1_000_000,
                     video_codec="h264", has_audio=has_audio, nb_frames=frames)


# ============================================================== FPS clamp
@pytest.mark.parametrize("fps,expected_target,expect_note", [
    (24.0, 24.0, False),
    (23.976, 24.0, True),      # 23.976 < 24 → clamp up + note
    (30.0, 30.0, False),
    (60.0, 60.0, False),
    (120.0, 120.0, False),
    (121.0, 120.0, True),      # 초과 → clamp down + note
    (10.0, 24.0, True),        # 훨씬 낮음 → clamp up + note
])
def test_resolve_target_fps_clamp(fps, expected_target, expect_note):
    target, note = resolve_target_fps(_info(960, 540, fps, 5.0))
    assert target == expected_target
    assert (note is not None) == expect_note


def test_resolve_target_fps_unknown_fps_falls_back_to_none_with_note():
    target, note = resolve_target_fps(_info(960, 540, 0.0, 5.0))
    assert target is None and note is not None and "확인할 수 없어" in note


# ============================================================== 가격 계산
@pytest.mark.parametrize("w,h,expected_tier", [
    (1920, 1080, "1080p"),
    (960, 540, "1080p"),        # 480p 원본 → 2x = 960x540 → 1080p tier 이하로 올림
    (2560, 1440, "2k"),
    (2000, 1200, "2k"),         # 1080p 초과, 2K 이하 → 2k 로 올림
    (3840, 2160, "4k"),
    (5000, 3000, "4k"),         # 4K 초과값이 들어와도(이론상 발생 안 함) 4k 로 처리
])
def test_bytedance_resolution_tier(w, h, expected_tier):
    assert pricing.bytedance_resolution_tier(w, h) == expected_tier


def test_bytedance_price_1080p_30fps_pro_10x():
    # 480x270 원본 * scale=2 = 960x540(1080p tier 이하), 30fps, 10초
    info = _info(480, 270, 30.0, 10.0)
    c = pricing.estimate_bytedance_cost(info, target_fps=30.0, scale_ratio=2.0)
    assert c.resolution_tier == "1080p"
    assert c.rate_usd_per_sec == 0.0072
    assert c.tier_multiplier == 10.0
    assert abs(c.usd - 0.0072 * 10.0 * 10.0) < 1e-9   # $0.72
    assert not c.uncertain


def test_bytedance_price_2k_60fps_pro_10x():
    info = _info(1280, 720, 60.0, 5.0)   # *2 = 2560x1440 → 2k
    c = pricing.estimate_bytedance_cost(info, target_fps=60.0, scale_ratio=2.0)
    assert c.resolution_tier == "2k"
    assert c.rate_usd_per_sec == 0.0288          # 2k standard 60fps rate
    assert abs(c.usd - 0.0288 * 10.0 * 5.0) < 1e-9
    assert not c.uncertain


def test_bytedance_price_4k_30fps_pro_10x():
    info = _info(1920, 1080, 24.0, 5.0)  # *2 = 3840x2160 → 4k
    c = pricing.estimate_bytedance_cost(info, target_fps=24.0, scale_ratio=2.0)
    assert c.resolution_tier == "4k"
    assert c.rate_usd_per_sec == 0.0288           # 4k standard le30 rate
    assert abs(c.usd - 0.0288 * 10.0 * 5.0) < 1e-9


@pytest.mark.parametrize("target_fps,expected_bracket", [
    (45.0, "31-59(미확인)"),
    (90.0, "61-120(미확인)"),
])
def test_bytedance_price_uncertain_fps_bracket_uses_minimum_known_rate_not_guessed_multiplier(target_fps, expected_bracket):
    """31~59/61~120fps 는 공식 배율이 없다 — 임의의 배율(3배/4배 등)을 만들지 않고,
    알려진 가장 가까운 하한 요율로 '최소 예상치'만 계산하고 uncertain=True 로 표시해야 한다."""
    info = _info(480, 270, target_fps, 5.0)
    c = pricing.estimate_bytedance_cost(info, target_fps=target_fps, scale_ratio=2.0)
    assert c.uncertain is True
    assert c.fps_bracket == expected_bracket
    assert c.uncertain_note  # 경고 문구 존재
    # 45fps는 <=30 요율을 하한으로, 90fps는 60fps 요율을 하한으로 사용 (임의 배율 아님)
    if target_fps < 60:
        assert c.rate_usd_per_sec == 0.0072
    else:
        assert c.rate_usd_per_sec == 0.0144


def test_bytedance_cost_usd_display_rounds_up_conservatively():
    c = pricing.estimate_bytedance_cost(_info(100, 100, 24.0, 1.0), target_fps=24.0, scale_ratio=2.0)
    assert c.usd_display >= c.usd
    assert c.usd_display == max(0.01, c.usd_display)


# ============================================================== provider 기본
def test_check_availability_requires_key(monkeypatch):
    import upcon.providers.fal_base as fb
    cfg = AppConfig()
    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: None)
    from upcon.core.env import SystemEnv
    a = FalByteDanceProvider(cfg).check_availability(SystemEnv())
    assert not a.ok and "연결" in a.reason

    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: FAKE_KEY)
    a = FalByteDanceProvider(cfg).check_availability(SystemEnv())
    assert a.ok


def test_check_availability_rejects_non_2x_output_mode():
    from upcon.core.constants import OutputMode
    from upcon.core.env import SystemEnv
    cfg = AppConfig()
    a = FalByteDanceProvider(cfg).check_availability(SystemEnv(), output_mode=OutputMode.FHD)
    assert not a.ok and "2×" in a.reason


def test_upscale_without_key_raises_auth_error(monkeypatch, tmp_path):
    import upcon.providers.fal_base as fb
    from upcon.providers.fal_base import FalAuthError
    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: None)
    cfg = AppConfig()
    cfg.temp_dir = str(tmp_path / "tmp")
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    info = _info(854, 480, 24, 5, frames=120)
    info.path = src
    job = Job(input_path=src, scale=2, info=info)
    with pytest.raises(FalAuthError):
        FalByteDanceProvider(cfg).upscale(job, lambda _p: None)


# ============================================================== 전체 흐름 (fake cloud)
class _FakeHandle:
    def __init__(self, request_id, statuses, result):
        self.request_id = request_id
        self.response_url = self.status_url = self.cancel_url = ""
        self._statuses = list(statuses)
        self._result = result
        self.status_calls = 0
        self.cancel_calls = 0

    def status(self, with_logs=False):
        self.status_calls += 1
        item = self._statuses.pop(0) if self._statuses else Completed(logs=[], metrics={}, error=None, error_type=None)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self):
        return self._result

    def cancel(self):
        self.cancel_calls += 1


class _FakeUploadClient:
    def __init__(self, url="https://cdn.example/uploaded.mp4"):
        self.url = url
        self.upload_calls = 0

    def upload_file(self, path, lifecycle=None):
        self.upload_calls += 1
        return self.url


class _FakeStreamResp:
    def __init__(self, data: bytes, fail: Exception | None = None):
        self._data = data
        self._fail = fail
        self.headers = {"content-length": str(len(data))}

    def raise_for_status(self):
        if self._fail:
            raise self._fail

    def iter_bytes(self, n):
        for i in range(0, len(self._data), n):
            yield self._data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _completed(metrics=None, error=None, error_type=None):
    return Completed(logs=[], metrics=metrics or {}, error=error, error_type=error_type)


def _rig(monkeypatch, result_bytes: bytes, handle_factory, download_fail: Exception | None = None):
    import upcon.providers.fal_base as fb
    import upcon.providers.fal_bytedance as bdm
    monkeypatch.setattr(bdm.time, "sleep", lambda s: None)
    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: FAKE_KEY)
    monkeypatch.setattr(bdm.FalApi, "actual_cost_usd", lambda self, rid, ep: None)
    upload = _FakeUploadClient()
    monkeypatch.setattr(bdm.FalApi, "client", lambda self: upload)

    submit_calls: list[dict] = []
    handles: list[_FakeHandle] = []

    def fake_submit_once(self, client, endpoint, arguments, headers=None):
        submit_calls.append(dict(arguments))
        h = handle_factory(len(submit_calls))
        handles.append(h)
        return h
    monkeypatch.setattr(bdm.FalApi, "submit_once", fake_submit_once)

    class _FakeHttpxClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, **kw):
            return _FakeStreamResp(result_bytes, fail=download_fail)

    monkeypatch.setattr(bdm.httpx, "Client", _FakeHttpxClient)
    return upload, submit_calls, handles


def test_full_flow_submit_once_with_expected_args(monkeypatch, tmp_path, sample_480p):
    """submit 정확히 1회, args에 aigc/pro/high/scale_ratio/target_fps(=원본 24fps)가 담기는지."""
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}",
                           [Queued(position=0), InProgress(logs=[]), _completed(metrics={"inference_time": 3.2})],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)   # 854x480, 24fps, 5s, audio 있음
    job = Job(input_path=sample_480p, scale=2, info=info)
    phases = []
    out = provider.upscale(job, lambda p: phases.append(p.phase), None)

    assert upload.upload_calls == 1
    assert len(submit_calls) == 1
    args = submit_calls[0]
    assert args["enhancement_preset"] == "aigc"
    assert args["enhancement_tier"] == "pro"
    assert args["fidelity"] == "high"
    assert args["scale_ratio"] == 2
    assert args["target_fps"] == 24.0
    assert out.exists()
    assert {Phase.UPLOAD, Phase.QUEUE, Phase.CLOUD, Phase.DOWNLOAD, Phase.SAVE}.issubset(set(phases))
    assert job.estimated_cost_usd is not None
    assert job.cost_uncertain is False
    assert job.note and ("예상 비용" in job.note or "실제 청구" in job.note)


def test_polling_transient_network_failure_recovers_without_resubmit(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [
            Queued(position=0),
            httpx.ConnectError("network blip"),
            httpx.ConnectError("network blip"),
            InProgress(logs=[]),
            _completed(),
        ], {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)

    assert out.exists()
    assert len(submit_calls) == 1, "폴링이 일시적으로 실패해도 새 submit(재과금)이 없어야 한다"
    assert handles[0].status_calls >= 5


def test_cancel_during_queue_no_resubmit_and_no_output_file(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [Queued(position=3), Queued(position=1)],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)

    def cb(p):
        if p.phase == Phase.QUEUE:
            job.cancel.cancel()

    with pytest.raises(CancelledError):
        provider.upscale(job, cb, None)
    assert len(submit_calls) == 1
    assert handles[0].cancel_calls == 1
    assert not list(Path(cfg.output_dir).glob("*.mp4"))


def test_download_failure_no_resubmit_no_output(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    fail = httpx.HTTPStatusError("500 server error", request=httpx.Request("GET", "https://cdn.example/result.mp4"),
                                 response=httpx.Response(500, request=httpx.Request("GET", "https://cdn.example/result.mp4")))
    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory, download_fail=fail)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)

    with pytest.raises(UpconError):
        provider.upscale(job, lambda _p: None, None)
    assert len(submit_calls) == 1, "다운로드 실패해도 재-submit 하면 안 된다"
    assert not list(Path(cfg.output_dir).glob("*.mp4"))


def test_decode_validation_failure_fails_job_without_resubmit_and_preserves_file(monkeypatch, tmp_path, sample_480p):
    """decode 검증 실패 시: (1) Cloud API 재-submit 절대 금지, (2) job 실패,
    (3) 진단용으로 결과 파일을 보존한다."""
    import upcon.providers.fal_bytedance as bdm

    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory)
    monkeypatch.setattr(bdm.ff, "verify_video_decodable",
                        lambda path, timeout=600: (_ for _ in ()).throw(UpconError("결과 영상을 확인할 수 없습니다.", "decode check failed (fake)")))

    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)

    with pytest.raises(UpconError) as ei:
        provider.upscale(job, lambda _p: None, None)
    assert "확인할 수 없습니다" in ei.value.user_message

    assert len(submit_calls) == 1, "decode 검증 실패 후 재-submit 하면 안 된다"
    salvaged = list(Path(cfg.output_dir).glob("*_DECODE_FAILED*.mp4"))
    assert salvaged, "진단 가능하도록 실패한 결과 파일이 보존되어야 한다"


# ============================================================== 오디오
def test_audio_present_in_output_is_kept_without_remux(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()   # 오디오 있는 결과

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)
    out_info = probe_video(out)
    assert out_info.has_audio
    assert "합쳤습니다" not in (job.note or "")   # 이미 있으므로 다시 mux 하지 않음 → note 없음


def test_audio_missing_in_output_but_present_in_source_triggers_local_mux(monkeypatch, tmp_path, sample_480p, ffmpeg_bin):
    """ByteDance 결과에 오디오가 없고 원본엔 있으면 로컬 mux로 붙이고, note에 남긴다."""
    import subprocess
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()

    no_audio_result = tmp_path / "no_audio_result.mp4"
    subprocess.run([str(ffmpeg_bin), "-v", "error", "-y", "-i", str(sample_480p), "-an", "-c:v", "copy", str(no_audio_result)], check=True)
    result_bytes = no_audio_result.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(sample_480p)   # 원본은 오디오 있음
    job = Job(input_path=sample_480p, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)
    out_info = probe_video(out)
    assert out_info.has_audio, "원본 오디오가 로컬 mux로 붙어야 한다"
    assert "합쳤습니다" in job.note


def test_source_without_audio_produces_silent_output_without_note(monkeypatch, tmp_path, ffmpeg_bin, samples_dir):
    """원본 자체에 오디오가 없으면 무음 결과가 정상이다 — 오디오 관련 note를 만들면 안 된다."""
    import subprocess
    src = samples_dir / "no_audio_src.mp4"
    if not src.exists():
        subprocess.run([str(ffmpeg_bin), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
                        "-t", "3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)

    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = src.read_bytes()   # 클라우드 결과도 무음 (원본이 무음이므로 당연)

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    info = probe_video(src)
    assert not info.has_audio
    job = Job(input_path=src, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)
    out_info = probe_video(out)
    assert not out_info.has_audio
    assert "합쳤습니다" not in (job.note or "") and "오디오" not in (job.note or "")


# ============================================================== duration/FPS 이상 note
def test_fps_or_duration_mismatch_between_input_and_output_surfaces_in_job_note(monkeypatch, tmp_path, sample_480p):
    """clamp로 인한 fps 보정처럼, 실제 결과 fps가 요청한 target_fps와 다르면 note에 남아야 한다."""
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()

    # sample_480p 는 24fps 인데, 결과로 그대로 재사용하면 target_fps(24)와 일치해 mismatch가 안 남는다.
    # info.fps 를 억지로 10fps(=API 최소 24 미만)로 바꿔 target_fps=24로 clamp 되게 하고,
    # 실제 반환 파일(24fps짜리 sample_480p)과 비교했을 때는 일치하므로, 대신 duration 불일치를 검증한다.
    info = probe_video(sample_480p)
    info.duration_sec = 999.0  # 원본 길이를 실제와 다르게 조작 → 결과와 큰 차이가 나게 함

    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    job = Job(input_path=sample_480p, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)
    assert out.exists()
    assert "영상 길이가 원본과 다릅니다" in job.note


def test_fps_clamp_note_surfaces_in_job_note(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()

    info = probe_video(sample_480p)
    info.fps = 10.0  # API 최소(24) 미만 → clamp 발생

    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    job = Job(input_path=sample_480p, scale=2, info=info)
    provider.upscale(job, lambda _p: None, None)
    assert "24fps로 보정했습니다" in job.note


# ============================================================== batch 연속성
def test_batch_first_file_fails_second_continues(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        if attempt == 1:
            return _FakeHandle("req-1", [_completed(error="broken input", error_type="ValidationError")],
                               {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})
        return _FakeHandle(f"req-{attempt}", [Queued(position=0), _completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig(monkeypatch, result_bytes, factory)
    provider = FalByteDanceProvider(cfg)
    src1, src2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    shutil.copy(sample_480p, src1)
    shutil.copy(sample_480p, src2)
    job1 = Job(input_path=src1, scale=2, info=probe_video(src1), provider_id=provider.id)
    job2 = Job(input_path=src2, scale=2, info=probe_video(src2), provider_id=provider.id)

    events = []
    m = JobManager(lambda j, cb: provider.upscale(j, cb, None), lambda j: None, events.append)
    m.add(job1)
    m.add(job2)
    m.start()
    t0 = time.time()
    while "finished" not in events:
        if time.time() - t0 > 20:
            raise AssertionError("timeout")
        time.sleep(0.02)
    assert job1.status == JobStatus.FAILED and job2.status == JobStatus.DONE
    assert len(submit_calls) == 2
    m.shutdown()


# ============================================================== config migration
def test_config_migrates_flashvsr_to_bytedance_and_preserves_other_fields(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "cloud_model": "fal-ai/flashvsr/upscale/video",
        "krw_per_usd": 1400,
        "cloud_unit_prices": {"fal-ai/flashvsr/upscale/video": 0.0005},
    }), encoding="utf-8")
    cfg = AppConfig.load(p)
    assert cfg.cloud_model == ENDPOINT
    assert cfg.krw_per_usd == 1400
    assert cfg.cloud_unit_prices.get("fal-ai/flashvsr/upscale/video") == 0.0005   # 데이터 보존, 삭제 안 함
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["cloud_model"] == ENDPOINT   # 마이그레이션 결과가 파일에도 반영됨


def test_config_fresh_user_defaults_to_bytedance():
    assert AppConfig().cloud_model == ENDPOINT


def test_config_migration_never_touches_fal_api_key(tmp_path, monkeypatch):
    """cloud_model 마이그레이션은 config.json 문자열 하나만 바꾼다 — keyring의 실제 API Key는
    config.json 에 아예 저장되지 않으므로 로드/마이그레이션과 완전히 무관해야 한다.

    keyring.set_keyring() 은 프로세스 전역 상태다 — 이 테스트가 끝난 뒤 실제 Windows 자격 증명
    관리자 백엔드로 반드시 복원해야, 이후 실행되는 다른 테스트(예: test_cloud.py 의 실제
    Windows keyring 왕복 테스트)가 가짜 백엔드를 보고 오작동하지 않는다 (tests/test_cloud.py 의
    mem_keyring 픽스처와 동일한 원칙)."""
    from upcon.core import credentials
    kr = _MemKeyring()
    keyring.set_keyring(kr)
    monkeypatch.delenv("FAL_KEY", raising=False)
    try:
        credentials.set_fal_key(FAKE_KEY)
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"cloud_model": "fal-ai/flashvsr/upscale/video"}), encoding="utf-8")
        cfg = AppConfig.load(p)
        assert cfg.cloud_model == ENDPOINT
        assert credentials.get_fal_key() == FAKE_KEY   # 키 그대로 보존
        assert FAKE_KEY not in p.read_text(encoding="utf-8")  # config.json 에는 애초에 안 들어감
    finally:
        credentials.delete_fal_key()
        keyring.set_keyring(keyring.core.load_keyring("keyring.backends.Windows.WinVaultKeyring"))


# ============================================================== 가짜(mock) 유닛으로 실제 API 요약 (paid submit 아님)
def test_error_mapping_reused_from_fal_base():
    """ByteDance provider는 fal_flashvsr.py 와 같은 explain_fal_error/FalApi를 공유하므로,
    이미 tests/test_cloud.py::test_error_mapping 에서 검증됨 — 여기서는 import 경로만 재확인."""
    from upcon.providers.fal_base import explain_fal_error
    assert explain_fal_error is not None
