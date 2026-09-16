"""클라우드 관련 단위 테스트 — 실제 API 는 호출하지 않는다."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import keyring
import pytest
from fal_client.client import FalClientHTTPError, FalClientTimeoutError

from upcon.core import credentials, pricing
from upcon.core.config import AppConfig
from upcon.core.constants import ProcessMode
from upcon.core.env import SystemEnv
from upcon.core.logging_setup import mask_sensitive
from upcon.core.probe import VideoInfo
from upcon.core.router import Router
from upcon.providers.fal_base import FalAuthError, FalBalanceError, FalNetworkError, explain_fal_error
from upcon.providers.fal_flashvsr import ENDPOINT, FalFlashVSRProvider

SAMPLES = Path(__file__).parent / "samples"
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
    kr = _MemKeyring()
    keyring.set_keyring(kr)
    monkeypatch.delenv("FAL_KEY", raising=False)
    yield kr
    keyring.set_keyring(keyring.core.load_keyring("keyring.backends.Windows.WinVaultKeyring"))


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


def test_real_windows_keyring_roundtrip():
    """실제 Windows 자격 증명 관리자에 가짜 값으로 저장→읽기→삭제 (사용자 키는 건드리지 않음)."""
    import keyring as kr
    svc, user = "UPCON-test", "roundtrip"
    kr.set_password(svc, user, "TEST_VALUE")
    assert kr.get_password(svc, user) == "TEST_VALUE"
    kr.delete_password(svc, user)
    assert kr.get_password(svc, user) is None
    assert credentials.backend_name() == "WinVaultKeyring"
