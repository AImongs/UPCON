"""플랫폼(Windows/macOS) 차이를 한 곳에 모은다 (STEP MAC-1).

나머지 코드는 여기서 만든 상수/함수만 쓰고 `sys.platform`/`os.name` 을 직접 비교하지 않는다
— "플랫폼 분기를 UI 곳곳에 하드코딩하지 않는다"는 요구를 지키기 위해서다.

지원 플랫폼:
- Windows(win32): 기존 UPCON 0.3.0 동작을 그대로 유지한다. 이 모듈이 하는 일은 '기존 상수를
  옮겨 적는 것'뿐이고, Windows 쪽 분기의 실제 동작은 한 글자도 바꾸지 않는다.
- macOS(darwin): 이번 STEP에서 새로 지원한다. 로컬(Real-ESRGAN) 업스케일은 아직 없고,
  앱 실행·데이터 저장·자격 증명·FFmpeg·클라우드 업스케일은 되는 것이 목표다.
- 그 외(Linux 등): 이번 STEP의 검증 대상이 아니다. 막지는 않지만(크래시 방지가 우선이므로),
  '검증된 플랫폼'으로 취급하지 않는다 — 자격 증명은 keyring 자동 탐색에 맡기고, 로컬 업스케일은
  macOS 와 같은 이유로 지원하지 않는다고 안내한다.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# subprocess 로 자식 프로세스를 띄울 때 콘솔 창이 뜨지 않게 하는 Windows 전용 플래그.
# 다른 플랫폼에는 이런 개념이 없다 — creationflags 에 0 이 아닌 값을 넘기면 POSIX(macOS/Linux)
# Python 은 곧바로 `ValueError: creationflags is only supported on Windows platforms` 를 던진다.
# 그래서 반드시 0(=아무 효과 없음)이어야 하며, Windows 가 아니면 절대 0x08000000 을 쓰면 안 된다.
NO_WINDOW_FLAGS: int = 0x08000000 if IS_WINDOWS else 0


def bundled_binary_name(name: str) -> str:
    """동봉 실행파일의 실제 파일명. Windows 만 .exe 확장자를 쓴다."""
    return f"{name}.exe" if IS_WINDOWS else name


def user_data_root() -> Path:
    """사용자 데이터를 넣을 상위 폴더(그 아래에 APP_NAME 하나를 더 붙여 쓴다).

    Windows: %LOCALAPPDATA% (기존 그대로).
    macOS:   ~/Library/Application Support (Apple 표준 위치).
    그 외:    XDG_DATA_HOME 또는 ~/.local/share (검증 대상 밖이지만 최소한 동작하도록).
    UPCON_DATA_DIR 오버라이드는 호출부(upcon.core.paths.user_data_dir)가 이 함수보다 먼저 처리한다.
    """
    import os
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def keyring_backend_class():
    """자동 탐색이 실패했을 때(예: PyInstaller 번들 안, entry-point 탐색 불가) 명시적으로
    고정할 keyring 백엔드 클래스. 지원 플랫폼이 아니면 None(자동 탐색에 맡긴다)."""
    if IS_WINDOWS:
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring
    if IS_MACOS:
        from keyring.backends.macOS import Keyring as MacOSKeyring
        return MacOSKeyring
    return None


def credential_store_name() -> str:
    """사용자 화면에 보여줄 자격 증명 저장소 이름."""
    if IS_WINDOWS:
        return "Windows 자격 증명 관리자"
    if IS_MACOS:
        return "macOS 키체인"
    return "이 PC의 자격 증명 저장소"


def local_upscale_unsupported_reason() -> str | None:
    """이 플랫폼에서 로컬(Real-ESRGAN ncnn) 업스케일을 아예 쓸 수 없는 이유.
    지원되면(현재는 Windows 만) None. macOS 로컬 AI 는 MAC-2 이후 과제다."""
    if IS_MACOS:
        return "현재 macOS 버전의 UPCON은 아직 내 PC GPU 업스케일을 지원하지 않습니다. 클라우드 업스케일을 이용해 주세요."
    if IS_LINUX:
        return "현재 Linux 버전의 UPCON은 아직 내 PC GPU 업스케일을 지원하지 않습니다. 클라우드 업스케일을 이용해 주세요."
    return None


def reveal_in_file_manager(path: Path) -> None:
    """결과 파일을 탐색기/Finder 에서 선택된 상태로 연다.

    파일 관리자를 못 띄워도(예: 헤드리스 환경) 예외를 밖으로 던지지 않는다 — 결과 파일 자체는
    이미 정상적으로 만들어졌으므로, '열기' 버튼 하나 때문에 앱이 죽으면 안 된다."""
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif IS_MACOS:
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as e:
        log.warning("파일 관리자를 열지 못했습니다 (%s): %s", path, e)


def open_folder(path: Path) -> None:
    """폴더를 파일 관리자로 연다. Windows 의 os.startfile 은 다른 플랫폼에 없다."""
    try:
        if IS_WINDOWS:
            import os
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 (Windows 전용 API, 의도된 사용)
        elif IS_MACOS:
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as e:
        log.warning("폴더를 열지 못했습니다 (%s): %s", path, e)


def describe() -> str:
    """진단/로그용 한 줄 요약."""
    return f"platform={sys.platform} windows={IS_WINDOWS} macos={IS_MACOS} linux={IS_LINUX}"
