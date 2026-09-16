r"""Installer 설치/제거 자동 테스트 (개발 전용).

    .\.venv\Scripts\python packaging\test_installer.py [--full]

기본(안전 모드): 설치파일 존재/무결성, 무인 스위치 전달 방식, uninstall fail-safe 정적 검증만.
--full: 실제 설치/제거까지 수행한다. **현재 설치된 UPCON 을 덮어쓰므로** 의도적으로 기본값이 아니다.

절대 규칙 (STEP 8-1 사고 재발 방지)
1. Windows 스위치를 **Git Bash 로 전달하지 않는다.** MSYS 경로 변환이 `/VERYSILENT` 를
   `C:/Program Files/Git/VERYSILENT` 로 바꿔버려서 설치 프로그램이 대화형으로 실행되고,
   그 결과 실제 사용자 데이터와 fal API Key 가 삭제된 적이 있다.
   -> 반드시 subprocess 리스트 인자 또는 PowerShell Start-Process -ArgumentList 로만 실행한다.
2. 무인 제거는 사용자 데이터를 **보존**해야 한다. upcon.iss 는 SuppressibleMsgBox(..., IDNO)
   를 써야 하며, 평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고 IDYES 를 반환한다.
3. 테스트는 실제 fal credential(서비스 이름 'UPCON')을 읽거나 삭제하지 않는다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISS = ROOT / "packaging" / "upcon.iss"
# 실제 credential 서비스 이름. 테스트는 이 이름을 절대 건드리지 않는다.
PRODUCTION_KEYRING_SERVICE = "UPCON"


def check_uninstall_failsafe() -> list[str]:
    """upcon.iss 의 제거 정책이 안전한지 정적 검증."""
    problems: list[str] = []
    text = ISS.read_text(encoding="utf-8")

    if "SuppressibleMsgBox" not in text:
        problems.append("upcon.iss 가 SuppressibleMsgBox 를 쓰지 않는다 "
                        "(평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고 IDYES 를 반환한다)")
    if "IDNO" not in text:
        problems.append("무인 제거 시 기본값 IDNO(보존)가 지정되어 있지 않다")

    # 파괴적 동작(DelTree / cmdkey /delete)이 사용자 동의 분기 안에 있어야 한다
    for destructive in ("DelTree", "cmdkey /delete"):
        if destructive in text and "= IDYES then" not in text:
            problems.append(f"{destructive} 가 명시적 IDYES 동의 분기 안에 있지 않다")

    # 결과 영상은 삭제 대상이 아님이 문구에 남아 있어야 한다
    if "결과 영상" not in text:
        problems.append("제거 안내에 '결과 영상은 삭제되지 않는다' 설명이 없다")
    return problems


def run_setup(exe: Path, args: list[str], timeout: int = 600) -> int:
    """설치/제거 실행. 리스트 인자로만 넘겨 MSYS 경로 변환을 원천 차단한다."""
    cmd = [str(exe), *args]
    print("  run:", " ".join(cmd))
    return subprocess.run(cmd, timeout=timeout).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="실제 설치/제거까지 수행 (현재 설치본을 덮어쓴다)")
    a = ap.parse_args()

    from upcon.version import __version__
    setup = ROOT / "dist-installer" / f"UPCON_Setup_{__version__}.exe"

    print("[1] uninstall fail-safe 정적 검증")
    problems = check_uninstall_failsafe()
    for p in problems:
        print("  FAIL:", p)
    if not problems:
        print("  OK: SuppressibleMsgBox(..., IDNO), 파괴적 동작은 IDYES 분기 안")

    print("\n[2] 설치파일")
    if setup.is_file():
        print(f"  OK: {setup.name}  {setup.stat().st_size / 1048576:.1f} MB")
    else:
        print(f"  없음: {setup}  (먼저 packaging/build_installer.py 실행)")
        problems.append("설치파일 없음")

    print("\n[3] credential 격리")
    print(f"  테스트는 '{PRODUCTION_KEYRING_SERVICE}' 서비스를 읽지도 삭제하지도 않는다.")
    print("  selftest 는 'UPCON-selftest', pytest 는 'UPCON-test' 네임스페이스만 사용한다.")

    if a.full:
        if not setup.is_file():
            return 1
        print("\n[4] 실제 무인 설치/제거 (리스트 인자 방식)")
        rc = run_setup(setup, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        print("  설치 exit:", rc)
        import os
        unins = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "UPCON" / "unins000.exe"
        if unins.is_file():
            rc = run_setup(unins, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            print("  제거 exit:", rc)
            data = Path(os.environ["LOCALAPPDATA"]) / "UPCON"
            print("  사용자 데이터 보존:", "OK" if data.exists() else "삭제됨 (FAIL)")
            if not data.exists():
                problems.append("무인 제거가 사용자 데이터를 삭제했다")
    else:
        print("\n[4] 실제 설치/제거는 생략 (--full 로 실행)")

    print("\n결과:", "OK" if not problems else f"{len(problems)}건 문제")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
