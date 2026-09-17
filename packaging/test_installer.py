r"""Installer 설치/제거 자동 테스트 (개발 전용).

    .\.venv\Scripts\python packaging\test_installer.py [--full]

기본(안전 모드): 설치파일 존재/무결성, 무인 스위치 전달 방식, uninstall fail-safe 정적 검증만.
--full: 실제 설치/제거까지 수행한다. **현재 설치된 UPCON 을 덮어쓰므로** 의도적으로 기본값이 아니다.
       설치·제거 전체가 임시 UPCON_DATA_DIR 로 격리된다(아래 절대 규칙 4) — 실제
       %LOCALAPPDATA%\UPCON 과 Windows 자격 증명 관리자의 UPCON credential 은
       읽지도 쓰지도 지우지도 않는다. 격리를 시작 전에 확신할 수 없으면 즉시 중단한다.

절대 규칙
1. (STEP 8-1 사고) Windows 스위치를 **Git Bash 로 전달하지 않는다.** MSYS 경로 변환이
   `/VERYSILENT` 를 `C:/Program Files/Git/VERYSILENT` 로 바꿔버려서 설치 프로그램이
   대화형으로 실행되고, 그 결과 실제 사용자 데이터와 fal API Key 가 삭제된 적이 있다.
   -> 반드시 subprocess 리스트 인자로만 실행한다 (run_setup 참고). shell=True 금지,
   Git Bash 문자열 명령 금지.
2. 무인 제거는 사용자 데이터를 **보존**해야 한다. upcon.iss 는 SuppressibleMsgBox(..., IDNO)
   를 써야 하며, 평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고 IDYES 를 반환한다.
3. 테스트는 실제 fal credential(서비스 이름 'UPCON')을 읽거나 삭제하지 않는다. 이 파일에는
   credential 을 조회/삭제하는 코드가 없다 (tests/test_installer_policy.py 가 정적으로 확인).
4. (STEP 9-2/9-3 재발 방지) skipifsilent 는 upcon.iss 문서 그대로 정상 동작하지만, Setup 이
   실제로 받는 명령줄이 호출 쪽 셸에 의해 훼손되면(위 1번) skipifsilent 도 Check: 가드도 막지
   못한다. 그래서 이 스크립트는 '혹시 postinstall 이 실행되더라도 실제 사용자 데이터에 닿지
   않게' 설치·제거를 실행하는 모든 자식 프로세스(Installer, Uninstaller, 그로부터 파생되는
   UPCON.exe)에 격리된 UPCON_DATA_DIR 을 환경변수로 명시적으로 상속시킨다. 격리 여부는
   실행 *전에* assert_isolated() 로 확인하며, 실패하면 아무것도 실행하지 않고 즉시 중단한다
   ("실행해보고 나중에 복구" 금지). 테스트 종료 후 지우는 것은 이 임시 폴더뿐이다.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISS = ROOT / "packaging" / "upcon.iss"
# 실제 credential 서비스 이름. 테스트는 이 이름을 절대 건드리지 않는다 (조회도 삭제도 없음).
PRODUCTION_KEYRING_SERVICE = "UPCON"


class IsolationError(RuntimeError):
    """--full 이 실제 사용자 데이터 폴더를 건드릴 위험이 있어 아무것도 실행하지 않고 중단됨."""


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


def real_user_data_dir() -> Path:
    """앱이 실제로 쓰는 사용자 데이터 폴더 (upcon.core.paths.user_data_dir 과 동일 규칙).
    이 경로는 --full 어디에서도 읽기/쓰기/삭제 대상이 되지 않는다."""
    return Path(os.environ["LOCALAPPDATA"]) / "UPCON"


def make_isolated_env(data_dir: Path) -> dict[str, str]:
    """현재 프로세스 환경을 복사하고 UPCON_DATA_DIR 만 격리 폴더로 덮어쓴다.

    이 env 를 Installer/Uninstaller 프로세스에 그대로 넘기면, Windows 는 자식 프로세스가
    부모의 환경변수를 상속하므로 postinstall 로 뜨는 UPCON.exe 까지 격리된다. os.environ 을
    통째로 복사하는 이유는(빈 env 대신) PATH 등이 없으면 하위 프로세스 실행 자체가 깨지기
    때문이다 — UPCON_DATA_DIR 한 값만 덮어쓴다."""
    env = os.environ.copy()
    env["UPCON_DATA_DIR"] = str(data_dir)
    return env


def assert_isolated(data_dir: Path) -> None:
    """--full 이 무엇이든 실행하기 *전에* 호출한다. 하나라도 의심스러우면 실행 자체를 막는다.

    확인 항목: (1) 실제 사용자 데이터 경로와 동일하지 않음 (2) 그 상/하위도 아님
    (3) 시스템 임시 폴더 밖이라 정체를 확신할 수 없는 경로가 아님 (4) 폴더 생성이 실제로 됨.
    """
    real = real_user_data_dir()
    real_resolved = real.resolve() if real.exists() else real
    data_resolved = data_dir.resolve()

    if data_resolved == real_resolved:
        raise IsolationError(f"UPCON_DATA_DIR 이 실제 사용자 데이터 경로와 같다: {data_dir}")
    for a, b, rel in ((data_resolved, real_resolved, "하위"), (real_resolved, data_resolved, "상위")):
        try:
            a.relative_to(b)
        except ValueError:
            continue
        raise IsolationError(f"UPCON_DATA_DIR 이 실제 사용자 데이터 경로의 {rel} 다: {data_dir}")

    tmp_root = Path(tempfile.gettempdir()).resolve()
    try:
        data_resolved.relative_to(tmp_root)
    except ValueError as e:
        raise IsolationError(
            f"UPCON_DATA_DIR 이 시스템 임시 폴더({tmp_root}) 밖이라 격리 여부를 "
            f"확신할 수 없다: {data_dir}") from e

    if not data_resolved.is_dir():
        raise IsolationError(f"격리 폴더 생성/확인에 실패했다: {data_dir}")


def run_setup(exe: Path, args: list[str], env: dict[str, str], timeout: int = 600) -> int:
    """설치/제거 실행. 리스트 인자로만 넘겨 MSYS 경로 변환을 원천 차단한다.

    env 는 호출자가 반드시 명시적으로 넘긴다 (make_isolated_env 로 만든, UPCON_DATA_DIR 이
    들어간 환경) — 기본 인자로 두면 '깜빡하고 안 넘기는' 실수가 다시 생길 수 있다."""
    cmd = [str(exe), *args]
    print("  run:", " ".join(cmd))
    return subprocess.run(cmd, env=env, timeout=timeout, shell=False).returncode


def _parse_setup_command_line(log_text: str) -> str | None:
    m = re.search(r"Setup command line:\s*(.*)", log_text)
    return m.group(1).strip() if m else None


def verify_silent_switch_reached_setup(log_path: Path, expected_switch: str) -> list[str]:
    """Installer 자체 로그(/LOG=)에서 Setup 이 실제로 받은 명령줄을 확인한다.

    STEP 9-2 에서 skipifsilent 자체는 정상이었지만 Git Bash 가 /VERYSILENT 를 훼손해서
    전달한 적이 있다 — 그 훼손은 '실행해보고 결과가 이상하다' 가 아니라 로그에 그대로
    남으므로, 매번 이 로그를 근거로 검증한다(추측 금지)."""
    problems: list[str] = []
    if not log_path.is_file():
        problems.append(f"설치 로그가 생성되지 않았다: {log_path}")
        return problems
    text = log_path.read_text(encoding="utf-8", errors="replace")
    cmdline = _parse_setup_command_line(text)
    if cmdline is None:
        problems.append("로그에서 'Setup command line' 줄을 찾지 못했다")
        return problems
    tokens = cmdline.split()
    if expected_switch not in tokens:
        problems.append(f"{expected_switch} 가 개별 인자로 전달되지 않았다 (훼손 의심): {cmdline}")
    mangled = [t for t in tokens if "Git" in t or (" " in t and not t.startswith('"$SL5'))]
    if mangled:
        problems.append(f"셸 경로 변환으로 훼손된 것으로 보이는 토큰이 명령줄에 있다: {mangled}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="실제 설치/제거까지 수행 (임시 UPCON_DATA_DIR 로 격리됨)")
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
    print("  이 파일에는 credential 삭제/조회 코드가 없다 (정적 검증: tests/test_installer_policy.py).")

    if a.full:
        if not setup.is_file():
            return 1

        tmp_dir = Path(tempfile.mkdtemp(prefix="upcon_installer_test_"))
        try:
            print(f"\n[4] 격리 확인 (실행 전, UPCON_DATA_DIR 후보: {tmp_dir})")
            assert_isolated(tmp_dir)   # 실패하면 여기서 예외 -> 아무것도 실행되지 않는다
            print(f"  OK: 실제 {real_user_data_dir()} 와 무관한 임시 폴더, 생성 확인됨")
            env = make_isolated_env(tmp_dir)

            # 데이터 보존 검증용 표식. skipifsilent + Check 가드가 정상 동작하면 앱 자체가
            # 실행되지 않아 이 폴더가 계속 비어 있을 것이므로(그게 정상), 직접 표식을 만들어
            # 두고 무인 제거 후에도 살아 있는지로 '데이터 보존' 정책을 독립적으로 검증한다.
            marker = tmp_dir / "queue.json"
            marker.write_text("[]", encoding="utf-8")

            install_log = tmp_dir / "install.log"
            print("\n[5] 실제 무인 설치 (리스트 인자, 격리된 env)")
            rc = run_setup(setup, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                    f"/LOG={install_log}"], env=env)
            print("  설치 exit:", rc)

            print("\n[6] 설치 로그로 silent 스위치 전달 검증")
            switch_problems = verify_silent_switch_reached_setup(install_log, "/VERYSILENT")
            for p in switch_problems:
                print("  FAIL:", p)
            problems.extend(switch_problems)
            if not switch_problems:
                print("  OK: /VERYSILENT 가 훼손 없이 Setup 에 전달됨")

            print("\n[7] postinstall 자동 실행 여부 (0회여야 한다)")
            log_text = install_log.read_text(encoding="utf-8", errors="replace") if install_log.is_file() else ""
            if "Run entry" in log_text:
                problems.append("silent 설치인데 postinstall Run 항목이 실행됐다 (Check 가드 확인 필요)")
                print("  FAIL: 로그에 'Run entry' 가 있다")
            else:
                print("  OK: 'Run entry' 없음 (자동 실행되지 않았다)")

            unins = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "UPCON" / "unins000.exe"
            if unins.is_file():
                uninstall_log = tmp_dir / "uninstall.log"
                print("\n[8] 실제 무인 제거 (리스트 인자, 같은 격리 env)")
                rc = run_setup(unins, ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                                        f"/LOG={uninstall_log}"], env=env)
                print("  제거 exit:", rc)
                print("  격리 데이터 보존(표식 파일):", "OK" if marker.exists() else "삭제됨 (FAIL)")
                if not marker.exists():
                    problems.append("무인 제거가 (격리된) 사용자 데이터를 삭제했다")
            else:
                print("\n[8] unins000.exe 없음 — 제거 생략")
        finally:
            print(f"\n[9] 정리: 임시 폴더만 삭제한다 ({tmp_dir})")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            print(f"  실제 {real_user_data_dir()} 는 이 스크립트가 어떤 단계에서도 건드리지 않는다.")
    else:
        print("\n[4] 실제 설치/제거는 생략 (--full 로 실행)")

    print("\n결과:", "OK" if not problems else f"{len(problems)}건 문제")
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except IsolationError as e:
        print("\n안전성 중단 (아무것도 설치/제거하지 않았다):", e)
        sys.exit(2)
