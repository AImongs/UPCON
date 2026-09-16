"""fal.ai 공통: 인증, 연결 테스트, 단가 조회, 실제 청구 조회, 오류 → 한국어 메시지.

- 인증 헤더 `Authorization: Key <FAL_KEY>` (공식 문서). 키는 keyring 에서만 읽는다.
- Queue REST: https://queue.fal.run/{endpoint} (submit / status / result / cancel) — fal_client SDK 사용.
- Platform API: https://api.fal.ai/v1/models/pricing, /billing-events (단가·실청구 확인).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from fal_client.client import FalClientError, FalClientHTTPError, FalClientTimeoutError, SyncClient

from upcon.core import credentials
from upcon.core.errors import UpconError

log = logging.getLogger(__name__)

PLATFORM_API = "https://api.fal.ai/v1"
_UA = "UPCON/0.4 (+https://github.com/upcon)"


class FalAuthError(UpconError):
    pass


class FalBalanceError(UpconError):
    pass


class FalNetworkError(UpconError):
    pass


def explain_fal_error(e: BaseException) -> UpconError:
    """API/네트워크 예외를 사용자용 한국어 메시지로 변환. detail 에 키·URL 을 넣지 않는다."""
    if isinstance(e, UpconError):
        return e
    if isinstance(e, FalClientHTTPError):
        code, msg, et = e.status_code, (e.message or "")[:300], e.error_type or ""
        low = msg.lower()
        if code == 401 or code == 403 and ("unauthorized" in low or "invalid" in low and "key" in low):
            return FalAuthError("API Key가 올바르지 않습니다. 설정 → 클라우드 업스케일에서 fal.ai API Key를 확인해 주세요.",
                                f"http {code} {et}: {msg}")
        if code in (402,) or ("balance" in low or "locked" in low or "credit" in low or "billing" in low):
            return FalBalanceError("fal.ai 잔액이 부족하거나 결제 설정이 필요합니다. fal.ai 대시보드(Billing)에서 크레딧을 충전해 주세요.",
                                   f"http {code} {et}: {msg}")
        if code == 403:
            return FalAuthError("이 API Key로는 요청이 허용되지 않습니다. fal.ai 계정 상태와 키 권한을 확인해 주세요.",
                                f"http 403 {et}: {msg}")
        if code == 404:
            return UpconError("클라우드 업스케일 모델을 찾을 수 없습니다. 프로그램을 최신 버전으로 업데이트해 주세요.", f"http 404: {msg}")
        if code == 422 or code == 400:
            return UpconError("클라우드가 이 영상을 처리할 수 없습니다 (형식·길이·크기 제한). 다른 영상으로 시도해 주세요.",
                              f"http {code} {et}: {msg}")
        if code == 429:
            return UpconError("요청이 너무 많아 잠시 제한되었습니다. 1~2분 후 다시 시도해 주세요.", f"http 429 {et}: {msg}")
        if code >= 500:
            return UpconError("fal.ai 서버에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요. (서버 오류는 과금되지 않습니다)",
                              f"http {code} {et}: {msg}")
        return UpconError("클라우드 요청이 실패했습니다. 잠시 후 다시 시도해 주세요.", f"http {code} {et}: {msg}")
    if isinstance(e, FalClientTimeoutError):
        return FalNetworkError("클라우드 응답이 너무 오래 걸립니다. 네트워크 상태를 확인하고 다시 시도해 주세요.", f"timeout: {e}")
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError,
                      httpx.NetworkError, httpx.TimeoutException)):
        return FalNetworkError("인터넷에 연결할 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.", f"{type(e).__name__}: {e}")
    if isinstance(e, httpx.HTTPStatusError):
        return explain_fal_error(FalClientHTTPError(e.response.text[:300], e.response.status_code, {}, e.response))
    if isinstance(e, FalClientError):
        return UpconError("클라우드 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", f"{type(e).__name__}: {str(e)[:300]}")
    return UpconError("클라우드 처리 중 알 수 없는 오류가 발생했습니다.", f"{type(e).__name__}: {str(e)[:300]}")


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    unit_price_usd: float | None = None   # 조회된 단가 (USD/MP)
    unit: str = ""


class FalApi:
    """키 1개로 동작하는 얇은 래퍼. UI 는 이 클래스만 안다."""

    def __init__(self, key: str | None = None, timeout: float = 60.0):
        self._key = key or credentials.get_fal_key()
        self.timeout = timeout

    @property
    def has_key(self) -> bool:
        return bool(self._key)

    def _require_key(self) -> str:
        if not self._key:
            raise FalAuthError("fal.ai 계정이 연결되어 있지 않습니다. 설정 → 클라우드 업스케일에서 API Key를 입력해 주세요.", "no key")
        return self._key

    def client(self) -> SyncClient:
        return SyncClient(key=self._require_key(), default_timeout=self.timeout)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {self._require_key()}", "User-Agent": _UA}

    # ---- Platform API ----
    def get_unit_price(self, endpoint: str) -> tuple[float, str]:
        """(unit_price, unit). 인증 필요 → 키 검증에도 쓴다."""
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{PLATFORM_API}/models/pricing", params={"endpoint_id": endpoint}, headers=self._headers())
            if r.status_code >= 400:
                raise FalClientHTTPError(_safe_detail(r), r.status_code, {}, r, r.headers.get("x-fal-error-type"))
            data = r.json()
        for p in data.get("prices", []):
            if p.get("endpoint_id") == endpoint:
                return float(p["unit_price"]), str(p.get("unit", ""))
        raise UpconError("클라우드 모델 단가를 조회하지 못했습니다.", f"pricing response without endpoint: {data}")

    def verify_key_inference_scope(self) -> None:
        """Platform API 권한이 없는 일반(API 범위) 키도 검증할 수 있는 엔드포인트.
        CDN 토큰 발급은 SDK 가 파일 업로드에 쓰는 인증 경로이며, 잘못된 키면 401 을 돌려준다."""
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post("https://rest.fal.ai/storage/auth/token", params={"storage_type": "fal-cdn-v3"},
                       headers={**self._headers(), "Content-Type": "application/json"}, content="{}")
            if r.status_code >= 400:
                raise FalClientHTTPError(_safe_detail(r), r.status_code, {}, r, r.headers.get("x-fal-error-type"))

    def test_connection(self, endpoint: str) -> ConnectionTestResult:
        if not self._key:
            return ConnectionTestResult(False, "API Key를 입력해 주세요.")
        try:
            price, unit = self.get_unit_price(endpoint)
        except Exception as e:  # noqa: BLE001
            err = explain_fal_error(e)
            if isinstance(err, FalAuthError):
                # 키 범위(API/ADMIN) 때문에 Platform API 만 거부될 수 있음 → 추론용 인증 경로로 재확인
                try:
                    self.verify_key_inference_scope()
                    log.info("fal key valid for inference; platform pricing API not permitted → documented price")
                    return ConnectionTestResult(True, "fal.ai 연결 완료 (단가 조회 권한 없음 → 문서 기준 단가 사용)")
                except Exception as e2:  # noqa: BLE001
                    err = explain_fal_error(e2)
            log.warning("fal connection test failed: %s", err.detail)
            return ConnectionTestResult(False, err.user_message)
        log.info("fal connection ok: %s unit_price=%s/%s", endpoint, price, unit)
        return ConnectionTestResult(True, "fal.ai 연결 완료", price, unit)

    def actual_cost_usd(self, request_id: str, endpoint: str) -> float | None:
        """billing-events 에서 해당 요청의 실제 청구액(USD). 아직 집계 전이면 None."""
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(f"{PLATFORM_API}/models/billing-events",
                          params={"request_id": request_id, "endpoint_id": endpoint, "limit": 5}, headers=self._headers())
                if r.status_code >= 400:
                    log.info("billing-events http %s", r.status_code)
                    return None
                items = r.json().get("items") or r.json().get("events") or r.json().get("data") or []
        except Exception as e:  # noqa: BLE001
            log.info("billing-events lookup failed: %s", type(e).__name__)
            return None
        total = 0.0
        found = False
        for it in items:
            if it.get("request_id") == request_id:
                found = True
                total += float(it.get("cost_total") or 0.0)
        return total if found else None


def _safe_detail(r: httpx.Response) -> str:
    """오류 본문에서 사람이 읽을 메시지만 추출 ({"detail": ...} 또는 {"error": {"message": ...}})."""
    try:
        body = r.json()
        if isinstance(body, dict):
            if isinstance(body.get("error"), dict):
                return str(body["error"].get("message") or body["error"].get("type") or "")[:300]
            return str(body.get("detail", ""))[:300]
    except ValueError:
        pass
    return r.text[:300]
