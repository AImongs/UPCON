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
6. FFmpeg/FFprobe 실행 가능 여부(STEP MAC-4B부터: CI가 scripts/build_ffmpeg_macos_arm64.sh로
   직접 빌드해 bin/ 에 복사해 둔 바이너리를 find_binary()의 "동봉 bin/ 우선" 경로로 찾는다 —
   더 이상 Homebrew PATH 폴백에 의존하지 않는다. 로컬에서 그 스크립트를 안 돌렸으면 여전히
   PATH로 폴백한다)
7. FalByteDanceProvider 임포트/생성 + endpoint/기본 옵션(aigc/pro/high/scale_ratio=2) 확인 (STEP MAC-3)
8. ByteDance 비용 계산(pricing.estimate_bytedance_cost) 실제 호출 — 네트워크 없음
9. target_fps 보정(resolve_target_fps) 정상/클램프 케이스
10. CloudConfirmDialog 생성 — crash 없이 뜨는지 (실제 승인 없이 닫음)
11. API Key 가 없어도 check_availability() 가 크래시 없이 "연결해 주세요" 안내를 돌려주는지
12. Router AUTO: 로컬 unavailable(macOS) → ByteDance PRO Cloud 가 선택 가능한지, 그 과정에서
    유료 submit(FalApi.submit_once)이 단 한 번도 호출되지 않는지(가짜 키로 감시)

이 스크립트는 fal.ai 에 어떤 네트워크 요청도 보내지 않는다(진짜 API Key 를 쓰지 않고,
FAL_KEY 환경변수에 가짜 문자열만 넣는다 — keyring 조회는 실패해도 이 가짜 값으로 폴백된다).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# python3 scripts/macos_ci_smoke.py 로 직접 실행하면(=파일 경로로 실행) 파이썬이 sys.path[0]에
# scripts/ 만 넣는다 — pytest 는 rootdir 를 자동으로 넣어주므로 이 문제가 없었을 뿐이다.
# packaging/test_installer.py 와 같은 방식으로 저장소 루트를 명시적으로 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    # ------------------------------------------------------------------
    # STEP MAC-3: ByteDance PRO Cloud provider — macOS 에서도 그대로 재사용되는지.
    # 이 아래 체크들은 전부 fal.ai 에 어떤 네트워크 요청도 보내지 않는다.
    # FAL_KEY 는 진짜 키가 아니라 "키가 존재한다"만 흉내 내는 가짜 문자열이다.
    # ------------------------------------------------------------------
    os.environ["FAL_KEY"] = "mock_smoke_test_key_not_real"

    def _bytedance_provider_basics():
        from upcon.core.config import AppConfig
        from upcon.core import pricing
        from upcon.providers.fal_bytedance import DEFAULT_ARGS, FalByteDanceProvider
        p = FalByteDanceProvider(AppConfig())
        assert p.id == "fal_bytedance_pro"
        assert p.endpoint == pricing.BYTEDANCE_ENDPOINT == "fal-ai/bytedance-upscaler/upscale/video"
        assert DEFAULT_ARGS == {"enhancement_preset": "aigc", "enhancement_tier": "pro", "fidelity": "high"}
        return f"endpoint={p.endpoint} defaults={DEFAULT_ARGS}"

    check("7. FalByteDanceProvider 생성 + endpoint/기본 옵션(aigc/pro/high) 확인", _bytedance_provider_basics)

    def _bytedance_pricing():
        from datetime import datetime
        from pathlib import Path as P
        from upcon.core import pricing
        from upcon.core.probe import VideoInfo
        info = VideoInfo(path=P("smoke_test.mp4"), width=1920, height=1080, fps=30.0, duration_sec=10.0,
                         size_bytes=10_000_000, video_codec="h264", has_audio=True, audio_codec="aac",
                         nb_frames=300)
        est = pricing.estimate_bytedance_cost(info, target_fps=30.0, scale_ratio=2)
        assert est.usd_display > 0, est
        return f"1920x1080 10s 30fps scale=2 -> ${est.usd_display:.4f} ({datetime.now().date()})"

    check("8. ByteDance 비용 계산(pricing.estimate_bytedance_cost) 실행", _bytedance_pricing)

    def _target_fps_resolution():
        from pathlib import Path as P
        from upcon.core.probe import VideoInfo
        from upcon.providers.fal_bytedance import MAX_TARGET_FPS, MIN_TARGET_FPS, resolve_target_fps
        normal = VideoInfo(path=P("a.mp4"), width=1920, height=1080, fps=30.0, duration_sec=1.0,
                           size_bytes=1, video_codec="h264", has_audio=False)
        fps, note = resolve_target_fps(normal)
        assert fps == 30.0 and note is None, (fps, note)
        low = VideoInfo(path=P("a.mp4"), width=1920, height=1080, fps=10.0, duration_sec=1.0,
                        size_bytes=1, video_codec="h264", has_audio=False)
        fps2, note2 = resolve_target_fps(low)
        assert fps2 == MIN_TARGET_FPS and note2, (fps2, note2)
        return f"30fps -> {fps} (보정 없음) / 10fps -> {fps2} (최소 {MIN_TARGET_FPS}fps 보정, 최대 {MAX_TARGET_FPS}fps)"

    check("9. target_fps 보정(resolve_target_fps) 정상/클램프 케이스", _target_fps_resolution)

    def _cloud_confirm_dialog():
        from pathlib import Path as P
        from PySide6.QtWidgets import QApplication
        from upcon.app.widgets.cloud_confirm_dialog import CloudConfirmDialog
        from upcon.core.jobs import Job
        QApplication.instance() or QApplication([])
        job = Job(input_path=P("smoke_test.mp4"), scale=2, estimated_cost_usd=0.05, cost_uncertain=False)
        dlg = CloudConfirmDialog([job], scale=2, total_usd=0.05, krw_per_usd=1400.0, why="스모크 테스트")
        try:
            return f"CloudConfirmDialog 생성 OK (승인 없이 닫음, title={dlg.windowTitle()!r})"
        finally:
            dlg.close()

    check("10. CloudConfirmDialog 생성 (실제 승인 없이 닫음)", _cloud_confirm_dialog)

    def _no_key_no_crash():
        """실제 keyring(이 머신의 Keychain/자격 증명 관리자)에 이미 뭔가 저장돼 있을 수도 있으므로
        환경변수만 지우는 게 아니라 credentials.get_fal_key() 자체를 확실히 None 으로 고정한다
        — 어떤 개발 PC/CI 러너에서 실행하든 항상 같은 결과가 나와야 한다."""
        from unittest import mock

        from upcon.core.config import AppConfig
        from upcon.core.constants import DEFAULT_OUTPUT_MODE
        from upcon.core.env import SystemEnv
        from upcon.providers.fal_bytedance import FalByteDanceProvider
        with mock.patch("upcon.providers.fal_base.credentials.get_fal_key", return_value=None):
            p = FalByteDanceProvider(AppConfig())
            a = p.check_availability(SystemEnv(), 2, DEFAULT_OUTPUT_MODE)
            assert a.ok is False and "fal.ai" in a.reason, a.reason
            return f"키 없음 → ok=False, message={a.reason!r} (크래시 없음)"

    check("11. API Key 없을 때 check_availability() 크래시 없이 안내 반환", _no_key_no_crash)

    def _router_auto_no_submit():
        """AUTO 모드에서 macOS 는 Local 이 unavailable → Cloud(ByteDance PRO) 로 넘어가야 하고,
        이 판단(check_availability 호출)만으로는 절대 유료 submit 이 일어나면 안 된다.
        FalApi.submit_once 를 감시해 호출되면 즉시 실패시킨다(가짜 키 사용, 실제 네트워크 없음)."""
        from unittest import mock

        from upcon.app.controller import Controller
        from upcon.core.config import AppConfig
        from upcon.core.constants import ProcessMode
        from upcon.providers.fal_bytedance import FalByteDanceProvider

        def _forbidden_submit(*a, **k):
            raise AssertionError("스모크 테스트 중 FalApi.submit_once 가 호출됨 — 유료 submit 발생 위험")

        with mock.patch("upcon.providers.fal_base.FalApi.submit_once", side_effect=_forbidden_submit):
            ctrl = Controller(AppConfig())
            try:
                decision = ctrl.decide(ProcessMode.AUTO, 2)
                assert isinstance(decision.provider, FalByteDanceProvider), \
                    f"AUTO 모드에서 Cloud(ByteDance PRO) 가 선택돼야 하는데: {decision.provider}"
                return f"AUTO → provider={decision.provider.id}, message={decision.message!r}, submit 0회"
            finally:
                ctrl.shutdown()

    check("12. Router AUTO: Local unavailable → Cloud 선택, submit 0회 보장", _router_auto_no_submit)

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
