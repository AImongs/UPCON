"""GitHub Actions macOS Runner 전용 헤드리스 스모크 체크 (STEP MAC-1).

Mac 실기기가 없는 Windows 개발 PC에서는 이 스크립트를 실행할 수 없다(신호처럼 import 조차
PySide6 의 macOS 전용 동작을 타지 않을 뿐 실행 자체는 되지만, 검증 대상은 macOS 러너다).
GitHub Actions 워크플로(.github/workflows/build-macos.yml)가 macOS 러너에서 이 스크립트를 돌리고,
결과를 CI 로그로 남긴다 — "Mac 에서 실제로 성공했다"의 근거가 되는 파일이다.

QT_QPA_PLATFORM=offscreen 으로 실제 디스플레이 없이도(진짜 macOS 데스크톱 세션이 있는 GH 러너든,
언젠가 순수 헤드리스 러너로 바뀌든) 항상 같은 방식으로 검증한다.

확인 항목 (실패하면 비영시(0이 아닌) 종료 코드로 CI 를 실패시킨다):
1. UPCON 모듈 임포트
2. macOS 데이터 경로 계산이 실제로 Library/Application Support 아래인지
3. keyring 백엔드가 macOS Keychain 클래스로 선택되는지(실제 Keychain 접근은 하지 않음)
4. AppConfig 생성/저장/로드 라운드트립 (격리된 UPCON_DATA_DIR)
5. MainWindow 생성 — crash 없이 뜨는지, 로컬 GPU 업스케일 선택 시 안내 문구가 뜨는지
6. FFmpeg/FFprobe 실행 가능 여부
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        RESULTS.append((name, True, str(detail) if detail else "OK"))
    except Exception as e:  # noqa: BLE001 - 스모크 체크는 원인까지 로그에 남기고 계속 진행한다
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["UPCON_DATA_DIR"] = tempfile.mkdtemp(prefix="upcon_macos_ci_")

    def _import_upcon():
        import upcon  # noqa: F401
        from upcon.app.main_window import MainWindow  # noqa: F401
        from upcon.core.config import AppConfig  # noqa: F401
        return f"upcon v{__import__('upcon').APP_VERSION}"

    check("1. UPCON 모듈 임포트 (PySide6 포함)", _import_upcon)

    def _data_path():
        from upcon.core.paths import user_data_dir
        d = user_data_dir()
        assert "Library/Application Support/UPCON" in str(d).replace("\\", "/"), f"예상 밖 경로: {d}"
        return d

    check("2. macOS 데이터 경로 (Library/Application Support)", _data_path)

    def _keyring_backend():
        from upcon import platform as plat
        from upcon.core import credentials
        assert plat.IS_MACOS, f"이 스크립트는 macOS 러너 전용인데 platform={sys.platform}"
        cls = plat.keyring_backend_class()
        assert cls is not None and cls.__module__.startswith("keyring.backends.macOS"), cls
        return f"backend class = {cls.__module__}.{cls.__name__} (실제 Keychain 접근 없음)"

    check("3. keyring 백엔드 선택 = macOS Keychain (실제 접근 안 함)", _keyring_backend)

    def _config_roundtrip():
        from upcon.core.config import AppConfig
        cfg = AppConfig()
        cfg.output_mode = "1080p"
        cfg.save()
        loaded = AppConfig.load()
        assert loaded.output_mode == "1080p"
        return f"config.json @ {os.environ['UPCON_DATA_DIR']}"

    check("4. AppConfig 저장/로드 라운드트립 (격리된 UPCON_DATA_DIR)", _config_roundtrip)

    def _main_window():
        from PySide6.QtWidgets import QApplication
        from upcon.core.config import AppConfig
        from upcon.core.constants import ProcessMode
        app = QApplication.instance() or QApplication([])
        from upcon.app.controller import Controller
        Controller.detect_env_async = lambda self: None  # 이 스크립트에서는 백그라운드 스레드를 띄우지 않는다
        from upcon.app.main_window import MainWindow
        win = MainWindow(AppConfig())
        try:
            decision = win.controller.decide(ProcessMode.LOCAL, 2)
            assert decision.provider is None, "macOS 에는 로컬 Provider 가 아직 없어야 한다"
            assert "클라우드" in decision.message and "macOS" in decision.message, decision.message
            return f"창 생성 OK, 로컬 선택 시 안내: {decision.message!r}"
        finally:
            win.controller.shutdown()
            win.close()

    check("5. MainWindow 생성 + 로컬 업스케일 미지원 안내 확인", _main_window)

    def _ffmpeg():
        from upcon.core.binaries import ffmpeg_path, ffprobe_path
        import subprocess
        fm = subprocess.run([str(ffmpeg_path()), "-version"], capture_output=True, text=True, timeout=10)
        fp = subprocess.run([str(ffprobe_path()), "-version"], capture_output=True, text=True, timeout=10)
        assert fm.returncode == 0 and fp.returncode == 0
        return f"ffmpeg={fm.stdout.splitlines()[0]} | ffprobe={fp.stdout.splitlines()[0]}"

    check("6. FFmpeg/FFprobe 실행 (PATH, 예: Homebrew)", _ffmpeg)

    print("\n=== UPCON macOS 헤드리스 스모크 체크 결과 ===")
    ok = True
    for name, passed, detail in RESULTS:
        mark = "PASS" if passed else "FAIL"
        ok = ok and passed
        print(f"[{mark}] {name}\n       {detail}")
    print("=== 전체:", "OK" if ok else "실패 있음", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
