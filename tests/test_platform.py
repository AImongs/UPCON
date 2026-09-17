"""upcon.platform 단위 테스트 (STEP MAC-1).

이 Windows 개발 PC에서 macOS 동작을 실제로 확인할 수는 없다. 대신:
- 플랫폼별 '순수 로직'(경로 계산, 파일명 규칙, 메시지 문구, subprocess 인자 구성)은
  sys.platform 을 monkeypatch 하고 importlib.reload() 로 모듈 상수를 다시 계산시켜 검증한다
  (모듈 상수 IS_WINDOWS/IS_MACOS 는 import 시점 값이라 reload 없이는 안 바뀐다).
- 실제 OS API 호출(keyring 의 macOS Keychain 접근, subprocess 실제 실행)은 하지 않는다 —
  클래스가 올바르게 '선택'되는지만 확인하고, 그 클래스를 실제로 호출하지는 않는다.
- 이 파일 자체는 어느 플랫폼에서 실행해도(Windows/macOS CI 모두) 통과해야 한다.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

import upcon.platform as plat_module


def _reload_for(monkeypatch, platform_value: str):
    """sys.platform 을 바꾸고 upcon.platform 을 다시 import 해, 그 값 기준으로 계산된
    모듈을 돌려준다. 테스트가 끝나면 원래 sys.platform 이 자동 복원되고(monkeypatch),
    실제 이 프로세스의 sys.platform 이 바뀌는 건 이 함수 실행 동안뿐이다."""
    monkeypatch.setattr(sys, "platform", platform_value)
    mod = importlib.reload(plat_module)
    return mod


@pytest.fixture(autouse=True)
def _restore_real_platform_module():
    """각 테스트가 끝나면 실제 sys.platform 기준으로 모듈을 원상 복구한다
    (reload 로 바뀐 모듈 상태가 다른 테스트/파일로 새어나가지 않도록)."""
    yield
    importlib.reload(plat_module)


# ---------------------------------------------------------------- 플랫폼 상수
@pytest.mark.parametrize("value,windows,macos,linux", [
    ("win32", True, False, False),
    ("darwin", False, True, False),
    ("linux", False, False, True),
])
def test_platform_flags(monkeypatch, value, windows, macos, linux):
    mod = _reload_for(monkeypatch, value)
    assert mod.IS_WINDOWS is windows
    assert mod.IS_MACOS is macos
    assert mod.IS_LINUX is linux


def test_current_process_matches_real_sys_platform():
    """reload 없이 그냥 import 한 상태 — 실제 이 프로세스가 돌아가는 플랫폼과 일치해야 한다."""
    assert plat_module.IS_WINDOWS == (sys.platform == "win32")
    assert plat_module.IS_MACOS == (sys.platform == "darwin")


# ---------------------------------------------------------------- subprocess 콘솔창 플래그
def test_no_window_flags_is_zero_everywhere_except_windows(monkeypatch):
    """가장 중요한 회귀 방지: macOS/Linux 에서 0 이 아니면 실제 subprocess 호출이
    ValueError('creationflags is only supported on Windows platforms') 로 죽는다."""
    assert _reload_for(monkeypatch, "win32").NO_WINDOW_FLAGS == 0x08000000
    assert _reload_for(monkeypatch, "darwin").NO_WINDOW_FLAGS == 0
    assert _reload_for(monkeypatch, "linux").NO_WINDOW_FLAGS == 0


def test_no_window_flags_is_always_accepted_by_real_subprocess():
    """실제 subprocess.run 에 현재 플랫폼의 NO_WINDOW_FLAGS 를 넘겨도 ValueError 가 안 난다
    (파이썬 인터프리터 자체로 검증 — creationflags 인자 자체의 유효성만 확인, 실제 콘솔 숨김
    동작은 여기서 검증하지 않는다)."""
    import subprocess
    r = subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True,
                       creationflags=plat_module.NO_WINDOW_FLAGS)
    assert r.returncode == 0


# ---------------------------------------------------------------- 동봉 실행파일 이름
@pytest.mark.parametrize("value,expected", [("win32", "ffmpeg.exe"), ("darwin", "ffmpeg"), ("linux", "ffmpeg")])
def test_bundled_binary_name(monkeypatch, value, expected):
    mod = _reload_for(monkeypatch, value)
    assert mod.bundled_binary_name("ffmpeg") == expected


# ---------------------------------------------------------------- 사용자 데이터 경로
def test_user_data_root_windows(monkeypatch):
    mod = _reload_for(monkeypatch, "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    assert mod.user_data_root() == Path(r"C:\Users\tester\AppData\Local")


def test_user_data_root_macos(monkeypatch):
    mod = _reload_for(monkeypatch, "darwin")
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/tester"))
    assert mod.user_data_root() == Path("/Users/tester/Library/Application Support")


def test_user_data_root_linux_fallback(monkeypatch):
    mod = _reload_for(monkeypatch, "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/home/tester"))
    assert mod.user_data_root() == Path("/home/tester/.local/share")


def test_windows_user_data_dir_unchanged_end_to_end(tmp_path, monkeypatch):
    """실제 upcon.core.paths.user_data_dir() 가 이번 변경으로 조금도 달라지지 않았는지
    (UPCON_DATA_DIR 오버라이드 기준) 확인한다 — Windows 회귀 방지의 핵심 테스트."""
    from upcon.core.paths import user_data_dir
    monkeypatch.setenv("UPCON_DATA_DIR", str(tmp_path / "override"))
    d = user_data_dir()
    assert d == tmp_path / "override" and d.is_dir()


# ---------------------------------------------------------------- keyring 백엔드 선택
def test_keyring_backend_class_windows():
    from keyring.backends.Windows import WinVaultKeyring
    mod = plat_module  # 실제 이 프로세스가 win32 일 때만 의미 있는 비교
    if mod.IS_WINDOWS:
        assert mod.keyring_backend_class() is WinVaultKeyring


def test_keyring_backend_class_macos_is_selectable_without_calling_it(monkeypatch):
    """macOS 에서는 keyring.backends.macOS.Keyring 을 골라야 한다. '고른다'는 것만 확인하고
    실제 Keychain 에 접근하는 메서드(.priority 등)는 절대 호출하지 않는다 — macOS 가 아닌
    환경에서 그 메서드를 부르면 keyring 라이브러리 자체가 RuntimeError('macOS required') 를
    던진다(직접 확인됨). 클래스 identity 비교만으로 '올바른 백엔드를 선택했다'를 검증한다."""
    from keyring.backends.macOS import Keyring as MacOSKeyring
    mod = _reload_for(monkeypatch, "darwin")
    assert mod.keyring_backend_class() is MacOSKeyring


def test_keyring_backend_class_none_on_unverified_platform(monkeypatch):
    mod = _reload_for(monkeypatch, "linux")
    assert mod.keyring_backend_class() is None


@pytest.mark.parametrize("value,expected_substr", [
    ("win32", "Windows"), ("darwin", "macOS"), ("darwin", "키체인"), ("linux", "저장소"),
])
def test_credential_store_name_is_platform_specific(monkeypatch, value, expected_substr):
    mod = _reload_for(monkeypatch, value)
    assert expected_substr in mod.credential_store_name()


# ---------------------------------------------------------------- 로컬 업스케일 지원 여부
def test_local_upscale_supported_only_on_windows(monkeypatch):
    assert _reload_for(monkeypatch, "win32").local_upscale_unsupported_reason() is None
    macos_reason = _reload_for(monkeypatch, "darwin").local_upscale_unsupported_reason()
    assert macos_reason is not None and "클라우드" in macos_reason and "macOS" in macos_reason
    linux_reason = _reload_for(monkeypatch, "linux").local_upscale_unsupported_reason()
    assert linux_reason is not None and "클라우드" in linux_reason


# ---------------------------------------------------------------- 파일 관리자 열기 (실제로 띄우지 않는다)
def test_reveal_in_file_manager_builds_correct_command_per_platform(monkeypatch, tmp_path):
    f = tmp_path / "result_2x.mp4"
    f.write_bytes(b"x")
    for value, expect_prefix in [("win32", ["explorer", "/select,"]), ("darwin", ["open", "-R"])]:
        mod = _reload_for(monkeypatch, value)
        calls = []
        monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd: calls.append(cmd))
        mod.reveal_in_file_manager(f)
        assert calls and calls[0][:len(expect_prefix)] == expect_prefix
        assert calls[0][-1] == str(f)


def test_reveal_in_file_manager_never_raises_on_failure(monkeypatch, tmp_path):
    """파일 관리자를 못 띄워도(예: 헤드리스 CI) 예외가 밖으로 나가면 안 된다 — 업스케일
    자체는 이미 성공했는데 '결과 폴더 열기' 버튼 때문에 앱이 죽으면 안 되기 때문이다."""
    f = tmp_path / "x.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(plat_module.subprocess, "Popen", mock.Mock(side_effect=OSError("no file manager")))
    plat_module.reveal_in_file_manager(f)   # 예외 없이 조용히 로그만 남겨야 한다
    plat_module.open_folder(f.parent)


def test_open_folder_uses_startfile_only_on_windows(monkeypatch, tmp_path):
    d = tmp_path
    mod = _reload_for(monkeypatch, "win32")
    called = []
    monkeypatch.setattr("os.startfile", lambda p: called.append(p), raising=False)
    mod.open_folder(d)
    assert called == [str(d)]

    mod = _reload_for(monkeypatch, "darwin")
    calls = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda cmd: calls.append(cmd))
    mod.open_folder(d)
    assert calls == [["open", str(d)]]


def test_describe_includes_current_platform():
    assert sys.platform in plat_module.describe()


# ---------------------------------------------------------------- env.py 크래시 방지 (STEP MAC-1 핵심)
def test_vulkan_detection_does_not_crash_on_non_windows(monkeypatch):
    """가장 중요한 회귀 방지: ctypes.WinDLL 은 Windows 가 아니면 존재하지 않는 속성이라
    AttributeError 로 죽는다. macOS/Linux 에서는 아예 시도하지 말고 빈 목록을 돌려줘야 한다."""
    import upcon.core.env as env_mod
    monkeypatch.setattr(env_mod._plat, "IS_WINDOWS", False)
    assert env_mod._vulkan_devices() == []


def test_detect_system_env_does_not_crash_when_windows_apis_are_unavailable(monkeypatch):
    """detect_system_env() 전체를 '지금은 Windows 가 아니다' 라고 속이고 실행해도 예외 없이
    끝나야 한다. NVIDIA 감지(pynvml)는 플랫폼과 무관하게 실제 드라이버가 있으면 동작하는 게
    맞으므로(Linux+NVIDIA CI 등을 위해 의도된 설계) 여기서는 Vulkan(ctypes.WinDLL, Windows
    전용) 쪽만 빈 목록인지 — 즉 크래시 없이 끝까지 실행됐는지를 확인한다."""
    import upcon.core.env as env_mod
    monkeypatch.setattr(env_mod._plat, "IS_WINDOWS", False)
    monkeypatch.setattr(env_mod._plat, "IS_MACOS", False)  # sysctl 도 없다고 가정 (예: Linux CI)
    monkeypatch.setattr(env_mod, "_nvidia_devices", lambda: [])  # 이 매트릭스 항목은 GPU 감지 자체가 관심사가 아니다
    result = env_mod.detect_system_env()                   # 여기서 예외가 나면 테스트 실패
    assert result.gpus == []
    assert result.primary_gpu is None


def test_detect_system_env_macos_reads_ram_via_sysctl(monkeypatch):
    """macOS 분기에서는 sysctl 로 RAM 을 읽는다 — 실제 sysctl 을 부르지 않고 결과만 검증한다."""
    import subprocess as sp
    import upcon.core.env as env_mod
    monkeypatch.setattr(env_mod._plat, "IS_WINDOWS", False)
    monkeypatch.setattr(env_mod._plat, "IS_MACOS", True)
    monkeypatch.setattr(env_mod, "_vulkan_devices", lambda: [])
    monkeypatch.setattr(env_mod, "_nvidia_devices", lambda: [])
    fake = mock.Mock(stdout=f"{16 * 1024**3}\n")   # 16GB, sysctl -n hw.memsize 형태(바이트, 개행 포함)
    monkeypatch.setattr(sp, "run", lambda *a, **k: fake)
    result = env_mod.detect_system_env()
    assert result.ram_mb == 16 * 1024
