"""API Key 저장소 — Windows 자격 증명 관리자(keyring WinVaultKeyring). 평문 파일에 저장하지 않는다.

- 백엔드를 자동 탐색에 맡기지 않고 Windows Credential Manager 로 명시 고정한다
  (PyInstaller 배포본에서는 entry-point 기반 자동 탐색이 실패해 NoKeyringError 가 날 수 있음).
- 저장 후 즉시 읽어 확인한다.
- 개발 편의를 위해 환경변수 FAL_KEY 도 읽지만, 앱은 키를 로그/화면/설정 파일에 절대 쓰지 않는다.
"""

from __future__ import annotations

import logging
import os
import sys

import keyring
from keyring.errors import KeyringError

from upcon import APP_NAME

log = logging.getLogger(__name__)

_SERVICE = APP_NAME
_FAL_USER = "fal_api_key"


class CredentialStoreError(Exception):
    """사용자에게 보여줄 메시지(user_message)를 가진 자격 증명 저장소 오류."""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail


def _ensure_backend() -> None:
    """Windows 에서는 항상 WinVaultKeyring 을 사용한다."""
    if sys.platform != "win32":
        return
    kr = keyring.get_keyring()
    mod = type(kr).__module__
    # 이미 쓸 수 있는 백엔드(WinVault, 또는 테스트용 커스텀 백엔드)면 그대로 둔다.
    # 자동 탐색이 실패한 경우(fail/null/chainer 백엔드)에만 Windows Credential Manager 로 고정한다.
    if not (mod.startswith("keyring.backends.fail") or mod.startswith("keyring.backends.null")
            or mod.startswith("keyring.backends.chainer")):
        return
    try:
        from keyring.backends.Windows import WinVaultKeyring
        keyring.set_keyring(WinVaultKeyring())
        log.info("keyring backend forced to WinVaultKeyring (was %s)", type(kr).__name__)
    except Exception as e:  # noqa: BLE001
        raise CredentialStoreError(
            "Windows 자격 증명 관리자를 사용할 수 없습니다. 프로그램을 다시 설치해 주세요.",
            f"WinVaultKeyring unavailable: {type(e).__name__}: {e}",
        )


def backend_name() -> str:
    try:
        _ensure_backend()
        return type(keyring.get_keyring()).__name__
    except CredentialStoreError as e:
        return f"unavailable ({e.detail})"


def mask(secret: str | None) -> str:
    """로그/화면 표시용. 앞 4자리만 남기고 가린다."""
    if not secret:
        return "(없음)"
    return secret[:4] + "*" * 8


def get_fal_key() -> str | None:
    try:
        _ensure_backend()
        v = keyring.get_password(_SERVICE, _FAL_USER)
        if v:
            return v.strip()
    except (KeyringError, CredentialStoreError, OSError) as e:
        log.warning("keyring 읽기 실패: %s: %s", type(e).__name__, getattr(e, "detail", "") or str(e)[:120])
    env = os.environ.get("FAL_KEY", "").strip()
    return env or None


def set_fal_key(key: str) -> None:
    """저장 후 읽어서 검증. 실패 시 CredentialStoreError(사용자 메시지 포함)."""
    key = key.strip()
    if not key:
        raise CredentialStoreError("API Key를 입력해 주세요.", "empty key")
    if len(key.encode("utf-16-le")) > 2560:  # CredWrite 일반 자격 증명 blob 한도 (512*5 bytes)
        raise CredentialStoreError("API Key가 너무 깁니다. 올바른 키인지 확인해 주세요.", f"key too long: {len(key)} chars")
    try:
        _ensure_backend()
        keyring.set_password(_SERVICE, _FAL_USER, key)
        back = keyring.get_password(_SERVICE, _FAL_USER)
    except CredentialStoreError:
        raise
    except Exception as e:  # noqa: BLE001  (KeyringError, OSError, pywin32 오류 등)
        log.warning("keyring 저장 실패: %s: %s", type(e).__name__, str(e)[:160])
        raise CredentialStoreError(
            f"Windows 자격 증명 관리자에 저장하지 못했습니다. ({type(e).__name__})",
            f"{type(e).__name__}: {str(e)[:160]}",
        )
    if back != key:
        log.warning("keyring 저장 검증 실패 (읽은 값이 다름/없음)")
        raise CredentialStoreError("저장한 API Key를 다시 읽지 못했습니다. Windows 자격 증명 관리자 상태를 확인해 주세요.",
                                   "readback mismatch")
    log.info("fal API key saved to credential manager (%s)", mask(key))


def delete_fal_key() -> None:
    try:
        _ensure_backend()
        keyring.delete_password(_SERVICE, _FAL_USER)
        log.info("fal API key removed from credential manager")
    except (KeyringError, CredentialStoreError, OSError) as e:
        log.info("keyring 삭제: %s", type(e).__name__)


def has_fal_key() -> bool:
    return get_fal_key() is not None
