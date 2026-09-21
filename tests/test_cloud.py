"""클라우드 관련 단위 테스트 — 실제 API 는 호출하지 않는다."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from pathlib import Path

import httpx
import keyring
import pytest
from fal_client.client import Completed, FalClientHTTPError, FalClientTimeoutError, InProgress, Queued

from upcon.core import credentials, pricing
from upcon.core.config import AppConfig
from upcon.core.constants import ProcessMode
from upcon.core.env import SystemEnv
from upcon.core.jobs import CancelledError, Job, JobManager, JobStatus, Phase
from upcon.core.logging_setup import mask_sensitive
from upcon.core.probe import VideoInfo, probe_video
from upcon.core.router import Router
from upcon.providers.fal_base import FalAuthError, FalBalanceError, FalNetworkError, explain_fal_error
from upcon.providers.fal_flashvsr import ENDPOINT, FalFlashVSRProvider

FAKE_KEY = "12345678-abcd-4321-abcd-1234567890ab:0123456789abcdef0123456789abcdef"


def _info(w, h, fps, dur, frames=0):
    return VideoInfo(path=Path("x.mp4"), width=w, height=h, fps=fps, duration_sec=dur, size_bytes=1_000_000,
                     video_codec="h264", has_audio=True, nb_frames=frames)


# ------------------------------------------------------------------ pricing
def test_pricing_matches_official_example():
    # 공식 예시: 업스케일 결과 1920×1080, 121프레임 → $0.125 (출력 기준)
    src = _info(960, 540, 24, 121 / 24, frames=121)
    c = pricing.estimate_cost(src, 2, ENDPOINT)
    assert (c.out_width, c.out_height, c.frames) == (1920, 1080, 121)
    assert abs(c.megapixels - 250.9056) < 1e-6
    assert abs(c.usd - 0.1254528) < 1e-9 and round(c.usd, 3) == 0.125
    assert c.usd_display == 0.13          # 센트 올림 (보수적)
    assert c.price_source == "documented"


@pytest.mark.parametrize("w,h,dur,expected_usd", [
    (854, 480, 10, 0.19675),     # 480p→960p 10초: 1708×960×240 = 393.5 MP
    (854, 480, 60, 1.1805),      # 480p→960p 1분
    (854, 480, 600, 11.805),     # 480p→960p 10분
    (1920, 1080, 10, 0.99533),   # 1080p→4K 10초: 3840×2160×240 = 1990.7 MP
    (1920, 1080, 60, 5.972),     # 1080p→4K 1분
])
def test_pricing_scenarios(w, h, dur, expected_usd):
    c = pricing.estimate_cost(_info(w, h, 24, dur), 2, ENDPOINT)
    assert abs(c.usd - expected_usd) < 0.001
    assert c.usd_display >= c.usd


def test_pricing_uses_api_unit_price_and_krw():
    c = pricing.estimate_cost(_info(854, 480, 24, 10), 2, ENDPOINT, unit_price_usd=0.0006)
    assert c.price_source == "api" and abs(c.usd - 393.5232 * 0.0006) < 1e-9
    assert c.krw(None) is None and c.krw(0) is None
    assert c.krw(1400) == 340        # $0.24 × 1400 = 336 → 10원 올림


# ------------------------------------------------------------------ error mapping
def _http_err(code, detail="", error_type=None):
    resp = httpx.Response(code, json={"detail": detail}, request=httpx.Request("GET", "https://queue.fal.run/x"))
    return FalClientHTTPError(detail, code, {}, resp, error_type)


def test_error_mapping():
    assert isinstance(explain_fal_error(_http_err(401, "Invalid authentication credentials")), FalAuthError)
    assert isinstance(explain_fal_error(_http_err(403, "User is locked. Reason: Exhausted balance.")), FalBalanceError)
    assert isinstance(explain_fal_error(_http_err(402, "Payment required")), FalBalanceError)
    assert "잠시" in explain_fal_error(_http_err(429, "Too many requests")).user_message
    assert "서버" in explain_fal_error(_http_err(503, "Service unavailable")).user_message
    assert "처리할 수 없습니다" in explain_fal_error(_http_err(422, "Invalid video")).user_message
    assert isinstance(explain_fal_error(httpx.ConnectError("dns fail")), FalNetworkError)
    assert isinstance(explain_fal_error(FalClientTimeoutError(30.0)), FalNetworkError)
    for e in (_http_err(401), httpx.ConnectError("x"), _http_err(500)):
        u = explain_fal_error(e)
        assert FAKE_KEY not in u.user_message and FAKE_KEY not in u.detail


# ------------------------------------------------------------------ credentials / security
class _MemKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


@pytest.fixture
def mem_keyring(monkeypatch):
    original_keyring = keyring.get_keyring()
    kr = _MemKeyring()
    keyring.set_keyring(kr)
    monkeypatch.delenv("FAL_KEY", raising=False)
    yield kr
    keyring.set_keyring(original_keyring)


def test_credentials_roundtrip_and_not_in_config(mem_keyring, tmp_path):
    assert credentials.get_fal_key() is None
    credentials.set_fal_key(FAKE_KEY)
    assert credentials.get_fal_key() == FAKE_KEY
    assert credentials.mask(FAKE_KEY) == "1234********"
    cfg = AppConfig()
    cfg.cloud_provider = "fal"
    cfg.save(tmp_path / "config.json")
    assert FAKE_KEY not in (tmp_path / "config.json").read_text(encoding="utf-8")
    credentials.delete_fal_key()
    assert credentials.get_fal_key() is None


def test_log_masking():
    msgs = [
        f"Authorization: Key {FAKE_KEY}",
        f"FAL_KEY={FAKE_KEY}",
        f"raw {FAKE_KEY} in text",
        "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
        "uploaded to https://v3b.fal.media/files/b/0a98f887/YYbm6L.mp4 ok",
        "status https://queue.fal.run/fal-ai/flashvsr/upscale/video/requests/764cabcf/status?logs=1",
        "signed https://example.com/f.mp4?token=SECRETTOKEN&x=1",
    ]
    for m in msgs:
        out = mask_sensitive(m)
        assert FAKE_KEY not in out and "SECRETTOKEN" not in out and "YYbm6L" not in out and "764cabcf" not in out
        assert "eyJhbGci" not in out


def test_repo_has_no_secret(tmp_path):
    root = Path(__file__).resolve().parents[1]
    import re
    pat = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}", re.I)
    for p in list(root.glob("upcon/**/*.py")) + list(root.glob("*.md")) + list(root.glob("docs/*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in pat.finditer(text):
            assert m.group(0).startswith("12345678-abcd"), f"secret-like string in {p}"


# ------------------------------------------------------------------ routing
def test_router_cloud_requires_key(mem_keyring):
    cfg = AppConfig()
    cloud = FalFlashVSRProvider(cfg)
    router = Router(cfg, [], [cloud])
    d = router.decide(ProcessMode.CLOUD, SystemEnv(), 2)
    assert d.provider is None and "fal.ai 계정을 연결" in d.message
    credentials.set_fal_key(FAKE_KEY)
    d = router.decide(ProcessMode.CLOUD, SystemEnv(), 2)
    assert d.provider is cloud and "클라우드" in d.message
    d = router.decide(ProcessMode.AUTO, SystemEnv(), 2)     # GPU 없음 → 클라우드
    assert d.provider is cloud and "사용 가능한 GPU가 없어" in d.message


def test_router_auto_local_free_message(mem_keyring):
    from upcon.core.env import detect_system_env
    from upcon.providers.local_ncnn import LocalNcnnProvider
    env = detect_system_env()
    if not env.primary_gpu:
        pytest.skip("GPU 없음")
    cfg = AppConfig()
    d = Router(cfg, [LocalNcnnProvider(cfg)], [FalFlashVSRProvider(cfg)]).decide(ProcessMode.AUTO, env, 2)
    assert d.provider is not None and d.provider.kind == "local" and "무료" in d.message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 자격 증명 관리자 전용 테스트")
def test_real_windows_keyring_roundtrip():
    """실제 Windows 자격 증명 관리자에 가짜 값으로 저장→읽기→삭제 (사용자 키는 건드리지 않음).
    macOS Keychain 은 CI(헤드리스)에서 잠금 해제 없이 접근하면 멈추거나 실패할 수 있어
    여기서는 검증하지 않는다(STEP MAC-1) — macOS 백엔드 선택 로직 자체는
    tests/test_platform.py 가 실제 keyring 저장소에 접근하지 않고 검증한다."""
    import keyring as kr
    svc, user = "UPCON-test", "roundtrip"
    kr.set_password(svc, user, "TEST_VALUE")
    assert kr.get_password(svc, user) == "TEST_VALUE"
    kr.delete_password(svc, user)
    assert kr.get_password(svc, user) is None
    assert credentials.backend_name() == "WinVaultKeyring"


# ------------------------------------------------- 유료 submit 은 재시도하지 않는다 (이중 과금 방지)
def test_submit_once_never_retries_on_transport_error():
    """fal_client.submit() 은 TransportError 에 최대 10회 재시도한다 → 큐에 들어간 뒤 응답만 유실되면
    두 번째 유료 요청이 만들어질 수 있다. UPCON 의 submit_once 는 정확히 1회만 POST 해야 한다."""
    from upcon.providers.fal_base import FalApi

    calls = []

    class FakeHttpx:
        def request(self, method, url, **kw):
            calls.append((method, url))
            raise httpx.ConnectError("connection reset")

    class FakeClient:
        _client = FakeHttpx()

    api = FalApi(key=FAKE_KEY)
    with pytest.raises(httpx.ConnectError):
        api.submit_once(FakeClient(), ENDPOINT, {"video_url": "https://x/y.mp4"})
    assert len(calls) == 1, f"유료 submit 이 {len(calls)}회 전송됨 (1회여야 함)"
    assert calls[0][0] == "POST" and ENDPOINT in calls[0][1]


def test_submit_once_does_not_retry_on_429():
    """429 도 재시도하지 않는다 — 재시도 판단은 사용자/호출 측이 한다."""
    from upcon.providers.fal_base import FalApi

    calls = []

    class FakeHttpx:
        def request(self, method, url, **kw):
            calls.append(url)
            return httpx.Response(429, json={"detail": "rate limited"},
                                  request=httpx.Request(method, url))

    class FakeClient:
        _client = FakeHttpx()

    api = FalApi(key=FAKE_KEY)
    with pytest.raises(FalClientHTTPError):
        api.submit_once(FakeClient(), ENDPOINT, {"video_url": "https://x/y.mp4"})
    assert len(calls) == 1, f"유료 submit 이 {len(calls)}회 전송됨 (1회여야 함)"


def test_provider_submit_transport_failure_message_warns_about_possible_request(tmp_path, monkeypatch):
    """전송 실패 시 자동 재시도 대신, 요청이 접수됐을 수 있음을 사용자에게 알려야 한다."""
    import upcon.providers.fal_base as fb
    from upcon.core.jobs import Job

    src_file = tmp_path / "clip.mp4"
    src_file.write_bytes(b"0" * 1024)
    info = VideoInfo(path=src_file, width=854, height=480, fps=24, duration_sec=10,
                     size_bytes=1024, video_codec="h264", has_audio=True, nb_frames=240)

    class FakeClient:
        def upload_file(self, path, lifecycle=None):
            return "https://cdn.example/uploaded.mp4"

    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: FAKE_KEY)
    monkeypatch.setattr(fb.FalApi, "client", lambda self: FakeClient())
    monkeypatch.setattr(fb.FalApi, "submit_once",
                        lambda self, c, e, a, headers=None: (_ for _ in ()).throw(httpx.ConnectError("reset")))

    cfg = AppConfig()
    cfg.temp_dir = str(tmp_path / "tmp")
    job = Job(input_path=src_file, scale=2, info=info)
    with pytest.raises(Exception) as ei:
        FalFlashVSRProvider(cfg).upscale(job, lambda _p: None)
    msg = getattr(ei.value, "user_message", str(ei.value))
    assert "자동으로 다시 보내지 않았습니다" in msg, msg
    assert "확인" in msg
    # 실패했으므로 결과 파일이 남지 않아야 한다
    assert not (tmp_path / "clip_2x.mp4").exists()


def test_upscale_without_key_raises_auth_error(monkeypatch, tmp_path):
    """API Key 없음 → 업로드/submit 시도 전에 즉시 안내 오류로 끝나야 한다."""
    import upcon.providers.fal_base as fb
    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: None)
    cfg = AppConfig()
    cfg.temp_dir = str(tmp_path / "tmp")
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    info = VideoInfo(path=src, width=854, height=480, fps=24, duration_sec=5, size_bytes=1024,
                     video_codec="h264", has_audio=True, nb_frames=120)
    job = Job(input_path=src, scale=2, info=info)
    with pytest.raises(FalAuthError):
        FalFlashVSRProvider(cfg).upscale(job, lambda _p: None)


# ------------------------------------------------------- 실제 fal.ai 없이 전체 흐름을 끝까지 돌리는 가짜 클라우드
class _FakeHandle:
    """실제 fal_client.SyncRequestHandle 대신 쓰는 가짜 핸들. statuses 를 순서대로 돌려준다
    (예외를 넣으면 그 예외가 status()/get() 호출 시 그대로 발생) — 같은 request_id 를 계속 조회하는지,
    새 submit 없이 재시도하는지 검증하는 데 쓴다."""

    def __init__(self, request_id: str, statuses: list, result: dict):
        self.request_id = request_id
        self.response_url = self.status_url = self.cancel_url = ""
        self._statuses = list(statuses)
        self._result = result
        self.status_calls = 0
        self.cancel_calls = 0

    def status(self, with_logs: bool = False):
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
    def __init__(self, url: str = "https://cdn.example/uploaded.mp4"):
        self.url = url
        self.upload_calls = 0

    def upload_file(self, path, lifecycle=None):
        self.upload_calls += 1
        return self.url


class _FakeStreamResp:
    def __init__(self, data: bytes):
        self._data = data
        self.headers = {"content-length": str(len(data))}

    def raise_for_status(self):
        pass

    def iter_bytes(self, n):
        for i in range(0, len(self._data), n):
            yield self._data[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _rig_cloud(monkeypatch, result_bytes: bytes, handle_factory):
    """FalFlashVSRProvider.upscale() 을 실제 네트워크 없이 끝까지 돌릴 수 있게 세팅한다.

    handle_factory(attempt: int) -> _FakeHandle  (attempt 는 1부터, submit_once 호출 순서)
    반환: (fake upload client, submit 호출 인자 리스트, 만들어진 handle 리스트)
    """
    import upcon.providers.fal_base as fb
    import upcon.providers.fal_flashvsr as fvm
    monkeypatch.setattr(fvm.time, "sleep", lambda s: None)          # 폴링/백오프 대기 생략 (테스트 속도)
    monkeypatch.setattr(fb.credentials, "get_fal_key", lambda: FAKE_KEY)
    monkeypatch.setattr(fvm.FalApi, "actual_cost_usd", lambda self, rid, ep: None)   # 실제 청구 조회 안 함(무관)
    upload = _FakeUploadClient()
    monkeypatch.setattr(fvm.FalApi, "client", lambda self: upload)

    submit_calls: list[dict] = []
    handles: list[_FakeHandle] = []

    def fake_submit_once(self, client, endpoint, arguments, headers=None):
        submit_calls.append(dict(arguments))
        h = handle_factory(len(submit_calls))
        handles.append(h)
        return h
    monkeypatch.setattr(fvm.FalApi, "submit_once", fake_submit_once)

    class _FakeHttpxClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, **kw):
            return _FakeStreamResp(result_bytes)

    monkeypatch.setattr(fvm.httpx, "Client", _FakeHttpxClient)
    return upload, submit_calls, handles


def _completed(metrics=None, error=None, error_type=None):
    return Completed(logs=[], metrics=metrics or {}, error=error, error_type=error_type)


def _wait(cond, timeout=10.0):
    t0 = time.time()
    while not cond():
        if time.time() - t0 > timeout:
            raise AssertionError("timeout")
        time.sleep(0.02)


def test_cloud_upscale_full_flow_upload_poll_download_succeeds(monkeypatch, tmp_path, sample_480p):
    """업로드 → submit(1회) → 대기열/처리 폴링 → 결과 다운로드 → 오디오 검증 → 저장까지 전체 경로."""
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}",
                           [Queued(position=2), Queued(position=0), InProgress(logs=[]), _completed(metrics={"inference_time": 3.2})],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig_cloud(monkeypatch, result_bytes, factory)
    provider = FalFlashVSRProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)
    phases = []
    out = provider.upscale(job, lambda p: phases.append(p.phase), None)

    assert upload.upload_calls == 1
    assert len(submit_calls) == 1 and submit_calls[0]["upscale_factor"] == 2
    assert out.exists() and out.parent == Path(cfg.output_dir)
    assert {Phase.UPLOAD, Phase.QUEUE, Phase.CLOUD, Phase.DOWNLOAD, Phase.SAVE}.issubset(set(phases))
    assert job.estimated_cost_usd is not None


def test_cloud_polling_transient_network_failure_recovers_without_resubmit(monkeypatch, tmp_path, sample_480p):
    """폴링 중 일시적 네트워크 오류가 나도 같은 request_id 를 다시 조회할 뿐, 새 submit(재과금)은 없어야 한다."""
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

    upload, submit_calls, handles = _rig_cloud(monkeypatch, result_bytes, factory)
    provider = FalFlashVSRProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info)
    out = provider.upscale(job, lambda _p: None, None)

    assert out.exists()
    assert len(submit_calls) == 1, "폴링이 일시적으로 실패해도 새 submit(재과금)이 없어야 한다"
    assert handles[0].status_calls >= 5   # Queued + 실패 2회(같은 요청 재조회) + InProgress + Completed


def test_cloud_cancel_during_queue_no_resubmit_and_no_output_file(monkeypatch, tmp_path, sample_480p):
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        return _FakeHandle(f"req-{attempt}", [Queued(position=3), Queued(position=1)],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig_cloud(monkeypatch, result_bytes, factory)
    provider = FalFlashVSRProvider(cfg)
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


def test_cloud_retry_after_failure_makes_exactly_one_new_submit(monkeypatch, tmp_path, sample_480p):
    """실패한 작업을 '다시 시도' 하면 새 요청이 1건만 더 생겨야 한다 (기존 실패 요청을 중복 제출하지 않음)."""
    cfg = AppConfig()
    cfg.output_dir = str(tmp_path / "out")
    (tmp_path / "out").mkdir()
    result_bytes = sample_480p.read_bytes()

    def factory(attempt):
        if attempt == 1:
            return _FakeHandle("req-1", [_completed(error="server exploded", error_type="InternalError")],
                               {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})
        return _FakeHandle(f"req-{attempt}", [_completed()],
                           {"video": {"url": "https://cdn.example/result.mp4", "file_size": len(result_bytes)}})

    upload, submit_calls, handles = _rig_cloud(monkeypatch, result_bytes, factory)
    provider = FalFlashVSRProvider(cfg)
    info = probe_video(sample_480p)
    job = Job(input_path=sample_480p, scale=2, info=info, provider_id=provider.id)

    events = []
    m = JobManager(lambda j, cb: provider.upscale(j, cb, None), lambda j: None, events.append)
    m.add(job)
    m.start()
    _wait(lambda: "finished" in events)
    assert job.status == JobStatus.FAILED and len(submit_calls) == 1

    assert m.retry_failed() == 1
    events.clear()
    m.start()
    _wait(lambda: "finished" in events)
    assert job.status == JobStatus.DONE and len(submit_calls) == 2
    m.shutdown()


def test_cloud_batch_two_files_first_fails_second_continues(monkeypatch, tmp_path, sample_480p):
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

    upload, submit_calls, handles = _rig_cloud(monkeypatch, result_bytes, factory)
    provider = FalFlashVSRProvider(cfg)
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
    _wait(lambda: "finished" in events, timeout=20)
    assert job1.status == JobStatus.FAILED and job2.status == JobStatus.DONE
    assert len(submit_calls) == 2          # 파일마다 정확히 1회 (첫 실패가 둘째 파일에 영향 없음)
    m.shutdown()


def test_connection_test_reports_locked_account_before_upload(monkeypatch):
    """단가 조회는 되지만 계정이 잠긴(잔액 소진) 경우, 연결 테스트가 실패로 보고해야 한다.
    (실제로 겪은 문제: '연결 완료' 로 표시된 뒤 영상 업로드 단계에서야 403 으로 실패)"""
    from upcon.providers.fal_base import FalApi

    api = FalApi(key=FAKE_KEY)
    monkeypatch.setattr(FalApi, "get_unit_price", lambda self, ep: (0.0005, "megapixels"))

    def locked(self):
        raise FalClientHTTPError("User is locked. Reason: Exhausted balance.", 403, {},
                                 httpx.Response(403, request=httpx.Request("POST", "https://rest.fal.ai/x")))
    monkeypatch.setattr(FalApi, "verify_key_inference_scope", locked)

    r = api.test_connection(ENDPOINT)
    assert r.ok is False
    assert "잔액" in r.message or "결제" in r.message
