"""수강생 확인 — 수강생만 UPCON 을 쓰게 한다 (2026-10-31).

DramaCon(0.2.33~)과 같은 강사 구글 시트 웹 앱에 이름 + 전화번호 + PC 코드를 보내 확인한다.
  시트 '수강생' 탭에 있는 사람만 통과, 'PC기록' 탭이 한 사람당 PC 대수(기본 2)를 센다.
  요청 {action: activate|check|release, name, phone, pc, host, app_version}
  응답 {ok, used, max} 또는 {ok:false, reason: not_student|limit|pc_removed|bad_request}
PC 코드는 DramaCon 과 같은 방식으로 만든다 — 같은 PC 에서 DramaCon 과 UPCON 을 함께 써도 PC 1대로 센다.

- 이름·전화번호는 자격 증명 관리자에 저장한다(설정 파일·로그에 쓰지 않는다). 확인 상태는 데이터 폴더 license.json.
- 켤 때마다 시트에 다시 묻는다(app/license_gate.py) — 명단에서 지우면 다음 실행부터 막힌다.
- 인터넷이 안 되면 마지막으로 확인된 상태를 GRACE_DAYS(7일) 동안 인정한다.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

ENFORCED = True
GRACE_DAYS = 7
STUDENT_MAX_PCS = 2
STATE_FILE = "license.json"
SECRET_USER = "student_roster"           # 자격 증명 관리자(서비스 이름 UPCON) 안의 항목 이름

# DramaCon 과 같은 강사 시트 웹 앱 (DramaCon dramacon/core/license.py ROSTER_URL, 2026-10-29 배포)
ROSTER_URL = "https://script.google.com/macros/s/AKfycbzA2BejY_qJ9DPPBSd0IHyC-0UNUKE0x3tLuXClLW649-rhpIIfUafH68j1VOrXVXDpUg/exec"

ROSTER_REASONS = {
    "not_student": "수강생 명단에 없습니다 — 수강 신청 때 쓴 이름과 전화번호를 그대로 넣었는지 확인하세요(이름 띄어쓰기는 상관없음).",
    "limit": "이 수강생 정보로 이미 PC {max}대에서 쓰고 있습니다. 쓰지 않는 PC 에서 '정보 → 이 PC 등록 해제'를 누르거나 강사님께 문의하세요.",
    "pc_removed": "강사님이 이 PC 의 사용 등록을 지웠습니다 — 다시 쓰려면 이름·전화번호로 다시 확인하세요.",
    "bad_request": "이름과 전화번호를 모두 넣으세요.",
}


class LicenseError(Exception):
    def __init__(self, message: str, *, limit_reached: bool = False):
        super().__init__(message)
        self.limit_reached = limit_reached


@dataclass
class LicenseState:
    status: str = ""                 # active | invalid | "" (확인한 적 없음)
    holder: str = ""                 # 수강생 이름
    phone_hint: str = ""             # 전화번호 끝 4자리(화면 표시용)
    verified_unix: float = 0.0       # 마지막으로 확인에 성공한 시각
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ 저장
def load_state(data_dir: Path) -> LicenseState:
    try:
        d = json.loads((Path(data_dir) / STATE_FILE).read_text(encoding="utf-8"))
        return LicenseState(**{k: v for k, v in d.items() if k in LicenseState.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return LicenseState()


def save_state(data_dir: Path, st: LicenseState) -> None:
    p = Path(data_dir) / STATE_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def is_active(st: LicenseState, now: float | None = None) -> bool:
    """쓸 수 있는가 — 확인에 성공했고, 마지막 확인이 GRACE_DAYS 안이어야 한다."""
    now = now or time.time()
    return st.status == "active" and now - st.verified_unix <= GRACE_DAYS * 86400


def describe(st: LicenseState, now: float | None = None) -> str:
    now = now or time.time()
    if st.status != "active":
        return st.message or "수강생 확인 전입니다."
    text = f"수강생 확인됨 · {st.holder} (전화 …{st.phone_hint})"
    used, cap = st.extra.get("used"), st.extra.get("max")
    if cap:
        text += f" · PC {used}/{cap}대 사용 중"
    days = (now - st.verified_unix) / 86400
    if days > 1:
        text += f" · 마지막 확인 {days:.0f}일 전(인터넷 연결 시 다시 확인, {GRACE_DAYS}일 지나면 다시 확인 필요)"
    return text


# ------------------------------------------------------------------ PC 코드 · 입력 정리
def _machine_id() -> str:
    """이 PC 고유값 — Windows MachineGuid, Mac IOPlatformUUID, 그 밖에는 MAC 주소."""
    import platform
    import uuid
    try:
        if platform.system() == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                return str(winreg.QueryValueEx(k, "MachineGuid")[0])
        if platform.system() == "Darwin":
            import subprocess
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                 capture_output=True, text=True, timeout=10).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                return m.group(1)
    except Exception as e:  # noqa: BLE001
        log.info("PC 고유값을 읽지 못함(MAC 주소로 대신): %s", e)
    return f"node-{uuid.getnode():012x}"


def machine_code(raw: str | None = None) -> str:
    """시트에 기록되는 'PC 코드'(예 7KQ2M-XW9PA). 고유값을 그대로 내보내지 않고 해시한다.
    접두어는 DramaCon 과 같은 'dramacon-pc:' — 같은 PC 면 두 앱의 PC 코드가 같아 1대로 센다."""
    digest = hashlib.sha256(("dramacon-pc:" + (raw if raw is not None else _machine_id())).encode()).digest()
    s = base64.b32encode(digest).decode()[:10]
    return f"{s[:5]}-{s[5:]}"


def normalize_phone(phone: str) -> str:
    """숫자만, 국가번호 82 → 0. 010-1234-5678 / 01012345678 / +82 10 1234 5678 이 같은 번호."""
    m = re.search(r"(?:\+?82[\s-]?0?|(?<!\d)0)1\d[\s-]?\d{3,4}[\s-]?\d{4}", phone)
    d = "".join(ch for ch in (m.group(0) if m else phone) if ch.isdigit())
    if d.startswith("82"):
        d = "0" + d[2:]
    if len(d) < 9:
        raise LicenseError("전화번호를 확인하세요(예 010-1234-5678).")
    return d


def clean_name(name: str) -> str:
    """이름 칸에 시트 한 줄(이름·번호·대수)을 통째로 붙여 넣어도 이름만 남긴다."""
    return re.split(r"[\t\d+]", name.strip(), maxsplit=1)[0].strip()


def _default_host() -> str:
    import platform
    import socket
    try:
        host = socket.gethostname()
    except OSError:
        host = "PC"
    return f"UPCON · {host} · {platform.system()}"


# ------------------------------------------------------------------ 시트 웹 앱
def roster_call(action: str, name: str, phone: str, *, post: Callable[..., Any] | None = None,
                url: str | None = None, pc_code: str | None = None, host: str = "") -> dict[str, Any]:
    """명단 웹 앱에 묻는다(action: activate|check|release). 네트워크 오류는 그대로 올린다(호출부가 유예 처리)."""
    import httpx
    from upcon import APP_VERSION
    url = ROSTER_URL if url is None else url
    if not url:
        raise LicenseError("수강생 확인 주소가 설정되지 않은 버전입니다 — 강사님께 문의하세요.")
    body = json.dumps({"action": action, "name": clean_name(name), "phone": normalize_phone(phone),
                       "pc": pc_code or machine_code(), "host": host or _default_host(),
                       "app_version": f"UPCON {APP_VERSION}"}, ensure_ascii=False)
    post = post or httpx.post
    timeout = 12 if action == "check" else 25
    r = post(url, content=body.encode("utf-8"), headers={"Content-Type": "text/plain; charset=utf-8"},
             timeout=timeout, follow_redirects=True)
    try:
        data = r.json()
    except ValueError:
        raise LicenseError(f"수강생 확인 서버 응답을 읽지 못했습니다(HTTP {r.status_code}).") from None
    return data if isinstance(data, dict) else {}


def _active_state(data: dict[str, Any], name: str, phone: str, now: float) -> LicenseState:
    return LicenseState(status="active", holder=clean_name(name), phone_hint=normalize_phone(phone)[-4:],
                        verified_unix=now, extra={"used": data.get("used"), "max": data.get("max")})


def _roster_error(data: dict[str, Any]) -> LicenseError:
    reason = str(data.get("reason") or "")
    msg = ROSTER_REASONS.get(reason, f"확인 실패: {data.get('message') or reason or '알 수 없는 오류'}")
    return LicenseError(msg.format(max=data.get("max") or STUDENT_MAX_PCS), limit_reached=reason == "limit")


# ------------------------------------------------------------------ 자격 증명 저장
class KeyringStore:
    """이름·전화번호를 OS 자격 증명 저장소에 둔다(core/credentials.py 와 같은 서비스 이름)."""

    def __init__(self, service: str | None = None):
        from upcon import APP_NAME
        self.service = service or APP_NAME

    def get(self) -> str | None:
        import keyring
        from upcon.core.credentials import _ensure_backend
        try:
            _ensure_backend()
            return keyring.get_password(self.service, SECRET_USER)
        except Exception as e:  # noqa: BLE001
            log.warning("수강생 정보 읽기 실패: %s", type(e).__name__)
            return None

    def set(self, value: str) -> None:
        import keyring
        from upcon.core.credentials import _ensure_backend
        _ensure_backend()
        keyring.set_password(self.service, SECRET_USER, value)

    def delete(self) -> None:
        import keyring
        try:
            keyring.delete_password(self.service, SECRET_USER)
        except Exception:  # noqa: BLE001
            pass


class LicenseManager:
    """확인 상태 파일 + 자격 증명 + 시트 웹 앱. 화면(app/license_gate.py)은 이것만 쓴다."""

    def __init__(self, data_dir: Path, store=None, *, post: Callable[..., Any] | None = None,
                 url: str | None = None, pc_code: str | None = None):
        self.data_dir = Path(data_dir)
        self.store = store or KeyringStore()
        self.post, self.url, self.pc_code = post, url, pc_code
        self.state = load_state(self.data_dir)

    def allowed(self) -> bool:
        return (not ENFORCED) or is_active(self.state)

    def describe(self) -> str:
        return describe(self.state)

    def _call(self, action: str, name: str, phone: str) -> dict[str, Any]:
        return roster_call(action, name, phone, post=self.post, url=self.url, pc_code=self.pc_code)

    def _creds(self) -> tuple[str, str] | None:
        try:
            d = json.loads(self.store.get() or "")
            return str(d["name"]), str(d["phone"])
        except (ValueError, KeyError, TypeError):
            return None

    def activate(self, name: str, phone: str) -> LicenseState:
        """이름 + 전화번호로 이 PC 를 등록한다. 시트가 PC 대수를 센다."""
        if not name.strip() or not phone.strip():
            raise LicenseError(ROSTER_REASONS["bad_request"])
        data = self._call("activate", name, phone)
        if not data.get("ok"):
            raise _roster_error(data)
        st = _active_state(data, name, phone, time.time())
        self.store.set(json.dumps({"name": clean_name(name), "phone": normalize_phone(phone)}, ensure_ascii=False))
        self.state = st
        save_state(self.data_dir, st)
        return st

    def refresh(self, now: float | None = None) -> LicenseState:
        """시트에 다시 묻는다. 인터넷이 안 되면 그대로 둔다(유예), 명단에서 빠졌으면 막는다."""
        now = now or time.time()
        creds = self._creds()
        if creds is None or self.state.status != "active":
            return self.state
        try:
            data = self._call("check", *creds)
        except LicenseError as e:
            self.state = LicenseState(**{**self.state.to_dict(), "message": str(e)})
            save_state(self.data_dir, self.state)
            return self.state
        except Exception as e:  # noqa: BLE001 — 인터넷 끊김 등: 유예 기간 동안 마지막 상태 유지
            log.info("수강생 재확인 실패(유예 유지): %s", type(e).__name__)
            return self.state
        if data.get("ok"):
            self.state = _active_state(data, *creds, now)
        else:
            self.state = LicenseState(**{**self.state.to_dict(), "status": "invalid", "verified_unix": now,
                                         "message": str(_roster_error(data))})
        save_state(self.data_dir, self.state)
        return self.state

    def release(self) -> None:
        """이 PC 등록을 해제해 다른 PC 에서 쓸 자리를 돌려준다."""
        creds = self._creds()
        if creds:
            data = self._call("release", *creds)
            if not data.get("ok"):
                raise _roster_error(data)
        self.store.delete()
        self.state = LicenseState()
        save_state(self.data_dir, self.state)
