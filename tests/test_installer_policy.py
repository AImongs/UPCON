"""Installer 안전 정책 회귀 테스트 (설치를 실행하지는 않는다).

배경 (STEP 8-1 사고)
- Git Bash 로 `/VERYSILENT` 를 넘기면 MSYS 가 `C:/Program Files/Git/VERYSILENT` 로 바꿔버려
  설치 프로그램이 대화형으로 실행됐고, 그 결과 실제 사용자 데이터와 fal API Key 가 삭제됐다.
- 더 근본적으로, Inno 의 평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고 IDYES 를 반환한다.
  무인 제거에서 사용자 데이터를 말없이 지우게 되므로 SuppressibleMsgBox(..., IDNO) 를 써야 한다.

배경 (STEP 9-2 재발)
- 위 사고와 같은 원인(Git Bash 인자 훼손)으로 `/VERYSILENT` 가 인식되지 않아 skipifsilent 가
  실제로는 '지금 silent 가 아니다' 라고 (정확하게) 판단해 postinstall Run 항목이 실행됐고,
  실제 %LOCALAPPDATA%\\UPCON 에 config.json 이 새로 생성됐다 (ISetup.chm topic_runsection 확인 결과
  skipifsilent 자체는 문서대로 정상 동작 — 올바르게 전달된 /VERYSILENT 로 재현 시 Run 항목 0회 실행).
- 재발 방지로 [Run] 의 postinstall 항목에 `Check: ShouldLaunchAfterInstall` 을 추가했다.
  이 함수는 Inno 공식 Pascal Scripting 함수 `WizardSilent: Boolean` 을 사용해 skipifsilent 와
  같은 판단을 [Code] 안에서 독립적으로 한 번 더 강제한다 (플래그가 실수로 지워져도 방어).
  단, 두 메커니즘 모두 Setup 이 실제로 받은 명령줄에 의존하므로 명령줄 자체가 호출 쪽 셸에 의해
  훼손되는 경우는 막지 못한다 — 그래서 설치 프로그램을 호출하는 테스트/자동화는 반드시
  PowerShell Start-Process -ArgumentList 또는 subprocess 리스트 인자만 쓰고, 실행 전에
  UPCON_DATA_DIR 을 격리 폴더로 지정해야 한다 (packaging/test_installer.py 참고).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "packaging" / "upcon.iss"

pytestmark = pytest.mark.skipif(not ISS.is_file(), reason="packaging/upcon.iss 없음")


@pytest.fixture(scope="module")
def iss() -> str:
    return ISS.read_text(encoding="utf-8")


def test_uninstall_uses_suppressible_msgbox_with_keep_default(iss):
    """무인 제거에서 사용자 데이터를 보존해야 한다 (억제 시 IDNO)."""
    assert "SuppressibleMsgBox" in iss, "평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시한다"
    m = re.search(r"SuppressibleMsgBox\((.|\n)*?\)\s*=\s*IDYES", iss)
    assert m, "SuppressibleMsgBox 결과를 IDYES 와 비교하는 분기가 없다"
    assert "IDNO" in m.group(0), "메시지 박스 억제 시 기본값이 IDNO(보존)여야 한다"


def test_destructive_actions_are_behind_explicit_consent(iss):
    """DelTree / credential 삭제는 사용자가 명시적으로 YES 를 고른 분기 안에만 있어야 한다."""
    body = iss[iss.index("procedure CurUninstallStepChanged"):]
    consent = body.index("= IDYES then")
    for destructive in ("DelTree(", "cmdkey /delete"):
        assert destructive in body, f"{destructive} 가 제거 로직에 없다"
        assert body.index(destructive) > consent, f"{destructive} 가 동의 분기 밖에 있다"


def test_uninstall_target_is_only_user_data_dir(iss):
    """삭제 대상은 %LOCALAPPDATA%/UPCON 하나뿐이어야 한다.
    업스케일 결과 영상은 원본 옆에 저장되므로 애초에 대상이 아니다."""
    assert "결과 영상" in iss
    deltrees = [t.strip() for t in re.findall(r"DelTree\(([^,]+),", iss)]
    assert deltrees == ["DataDir"], f"예상 밖 삭제 대상: {deltrees}"
    assign = re.search(r"DataDir\s*:=\s*ExpandConstant\('([^']+)'\)", iss)
    assert assign, "DataDir 할당을 찾지 못했다"
    assert assign.group(1) == r"{localappdata}\UPCON", f"삭제 대상이 잘못됨: {assign.group(1)}"


def test_installer_is_per_user_and_needs_no_admin(iss):
    assert "PrivilegesRequired=lowest" in iss, "관리자 권한을 요구하면 안 된다"
    assert "{autopf}" in iss


def test_appid_is_fixed_for_upgrades(iss):
    """AppId 가 고정돼야 향후 버전이 기존 설치 위에 업그레이드된다."""
    assert re.search(r"AppId=\{\{([0-9A-Fa-f-]{36})\}", iss), "AppId GUID 를 찾지 못했다"


def test_version_comes_from_version_py(iss):
    """버전은 upcon/version.py 에서 /DMyAppVersion 으로 주입된다."""
    assert "AppVersion={#MyAppVersion}" in iss
    build = (ROOT / "packaging" / "build_installer.py").read_text(encoding="utf-8")
    assert "from upcon.version import __version__" in build
    assert "/DMyAppVersion=" in build


def _run_entry(iss: str) -> str:
    m = re.search(r'^Filename:\s*"\{app\}\\\{#MyAppExeName\}".*$', iss, re.MULTILINE)
    assert m, "[Run] 의 UPCON.exe 실행 항목을 찾지 못했다"
    return m.group(0)


def test_postinstall_run_has_both_skipifsilent_and_check_guard(iss):
    """설치 완료 후 자동 실행 항목은 skipifsilent 와 Check: 가드를 모두 갖춰야 한다 (STEP 9-2).

    skipifsilent 하나만으로는 '이 필드가 실수로 삭제되는 미래의 편집' 을 막지 못한다.
    Check: 로 [Code] 의 WizardSilent() 기반 판단을 독립적으로 한 번 더 강제한다."""
    entry = _run_entry(iss)
    assert "postinstall" in entry, "설치 마지막 화면에서만 뜨는 항목이어야 한다"
    assert "skipifsilent" in entry, "silent 설치에서 이 항목을 건너뛰어야 한다"
    m = re.search(r"Check:\s*(\w+)", entry)
    assert m, "postinstall 실행 항목에 Check: 가드가 없다"
    check_fn = m.group(1)
    assert check_fn in iss, f"Check 함수 '{check_fn}' 의 정의를 찾지 못했다"


def test_check_guard_uses_official_wizardsilent_function(iss):
    """Check 가드 함수는 Inno 공식 WizardSilent (Prototype: function WizardSilent: Boolean) 를 써야 한다.
    추측이나 독자적인 silent 판단 로직(명령줄 직접 파싱 등)을 새로 만들지 않는다."""
    m = re.search(r"function\s+ShouldLaunchAfterInstall\s*\(\s*\)\s*:\s*Boolean;\s*"
                 r"begin\s*(.*?)\s*end;", iss, re.DOTALL)
    assert m, "ShouldLaunchAfterInstall 함수 정의를 찾지 못했다"
    body = m.group(1)
    assert "WizardSilent" in body, "WizardSilent() 를 근거로 판단해야 한다"
    assert re.search(r"not\s+WizardSilent\s*\(\s*\)", body), \
        "silent 가 아닐 때만 True 여야 한다 (Result := not WizardSilent())"


def test_no_fake_corresponding_source_url(iss):
    """FFmpeg Corresponding Source URL 은 아직 확정되지 않았다. 가짜 URL 을 넣지 않는다."""
    urls = re.findall(r"https?://[^\s'\"]+", iss)
    bad = [u for u in urls if not u.startswith("https://jrsoftware.org")]
    assert not bad, f"확정되지 않은 URL 이 installer 에 있다: {bad}"


def _functions_calling(path: Path, needle: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            src = ast.unparse(node)
            if needle in src:
                yield node, src


def test_tests_never_read_production_credential():
    """실제 fal credential 을 읽는 테스트는 in-memory keyring 또는 monkeypatch 로 격리돼야 한다.
    (--run-cloud 유료 테스트는 실제 키가 필요하므로 예외)"""
    offenders = []
    for path in list((ROOT / "tests").rglob("*.py")) + list((ROOT / "packaging").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name == "test_installer_policy.py" or "integration/" in rel:
            continue
        for node, src in _functions_calling(path, "get_fal_key()"):
            argnames = {a.arg for a in node.args.args}
            isolated = bool(argnames & {"mem_keyring", "monkeypatch"}) or "get_fal_key =" in src
            if not isolated:
                offenders.append(f"{rel}::{node.name}")
    assert not offenders, "격리 없이 실제 키를 읽는 테스트: " + ", ".join(offenders)


def test_no_code_deletes_production_credential():
    """테스트/패키징 코드가 실제 credential 을 삭제하지 않는다.

    keyring 삭제는 테스트 전용 네임스페이스(UPCON-test / UPCON-selftest)를 쓰는
    테스트 함수 안에서만 허용한다. _MemKeyring 같은 가짜 백엔드의 메서드 '정의'는 제외.
    """
    offenders = []
    for path in list((ROOT / "tests").rglob("*.py")) + list((ROOT / "packaging").rglob("*.py")):
        if path.name == "test_installer_policy.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (node.name.startswith("test_") or node.name == "main"):
                continue        # 가짜 백엔드 메서드 정의 등은 대상 아님
            src = ast.unparse(node)
            if "CredDelete" in src:
                offenders.append(f"{path.name}::{node.name} CredDelete")
            if "delete_password" in src and not re.search(r"UPCON-(test|selftest)|TEST_KEYRING", src):
                offenders.append(f"{path.name}::{node.name} delete_password")
    assert not offenders, "실제 credential 을 삭제할 수 있는 코드: " + ", ".join(offenders)


def test_pytest_keyring_tests_use_test_namespace():
    """실제 Windows keyring 을 쓰는 pytest 는 'UPCON-test' 네임스페이스만 쓴다."""
    path = ROOT / "tests" / "test_cloud.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        src = ast.unparse(node)
        if "set_password" not in src:
            continue
        if "mem_keyring" in {a.arg for a in node.args.args}:
            continue            # in-memory 백엔드 -> 실제 저장소 미접근
        assert "UPCON-test" in src, f"{node.name} 이 실제 keyring 네임스페이스를 쓴다"


# ---------------------------------------------------------------------------------------------
# packaging/test_installer.py --full 격리 안전장치 (STEP 9-3, config.json 오염 사고 재발 방지)
#
# --full 을 여기서 '실행'하지는 않는다. 대신 두 가지 방식으로 격리 로직 자체를 확인한다:
#   (a) assert_isolated / verify_silent_switch_reached_setup 을 실제로 호출해 본다
#       (순수 함수라 부작용이 없다 — subprocess 를 띄우지 않는다)
#   (b) main() 안에서 이 함수들이 실행 전 순서·올바른 인자로 호출되는지 소스를 검사한다
# ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def installer_test_module():
    """packaging/test_installer.py 를 모듈로 import (패키지가 아니므로 경로로 직접 로드)."""
    path = ROOT / "packaging" / "test_installer.py"
    spec = importlib.util.spec_from_file_location("upcon_packaging_test_installer", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def installer_test_src() -> str:
    return (ROOT / "packaging" / "test_installer.py").read_text(encoding="utf-8")


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="real_user_data_dir()는 Windows LOCALAPPDATA(Inno installer 데이터 경로) 전제 — "
           "macOS 에는 대응하는 installer/설치 경로 개념이 없다(DMG 는 drag-install)",
)
def test_assert_isolated_rejects_real_data_dir_and_relatives(installer_test_module):
    """1, 5: 실제 사용자 데이터 경로/그 상하위/임시폴더 밖/생성 안 된 폴더는 전부 즉시 실패해야 한다."""
    ti = installer_test_module
    real = ti.real_user_data_dir()
    for label, bad in [
        ("실제 경로와 동일", real),
        ("실제 경로의 하위", real / "sub"),
        ("실제 경로의 상위", real.parent),
        ("임시 폴더 밖", ROOT / "not_a_temp_dir"),
        ("생성되지 않은 임시 경로", Path(tempfile.gettempdir()) / "upcon_never_created_xyz_9_3"),
    ]:
        with pytest.raises(ti.IsolationError):
            ti.assert_isolated(bad)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="real_user_data_dir()는 Windows LOCALAPPDATA(Inno installer 데이터 경로) 전제 — "
           "macOS 에는 대응하는 installer/설치 경로 개념이 없다(DMG 는 drag-install)",
)
def test_assert_isolated_accepts_real_isolated_temp_dir(installer_test_module):
    """진짜 격리된(시스템 임시 폴더 하위, 생성됨, 실제 경로와 무관) 폴더는 통과해야 한다."""
    ti = installer_test_module
    isolated = Path(tempfile.mkdtemp(prefix="upcon_installer_policy_test_"))
    try:
        ti.assert_isolated(isolated)   # 예외가 나면 이 테스트가 실패한다
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def test_make_isolated_env_overrides_only_data_dir(installer_test_module):
    """2: 자식 프로세스에 넘길 env 는 부모 환경(PATH 등)을 보존하면서 UPCON_DATA_DIR 만 바꿔야 한다.
    os.environ 원본을 오염시키지 않는다 (conftest.py 가 테스트 격리를 위해 이미 UPCON_DATA_DIR
    을 설정해 두므로, 여기서는 '키가 아예 없어야 한다' 가 아니라 '이 호출이 원본 dict 를 직접
    고치지 않고 사본을 돌려주는지, 그 값이 fake 로 오염되지 않는지' 를 확인한다)."""
    ti = installer_test_module
    before = dict(os.environ)
    fake = Path("C:/definitely_fake_isolated_dir")
    env = ti.make_isolated_env(fake)
    assert env["UPCON_DATA_DIR"] == str(fake)
    assert env.get("PATH"), "PATH 가 없으면 자식 프로세스 실행 자체가 깨진다"
    assert env is not os.environ, "os.environ 을 그대로 돌려주면 안 된다 (사본이어야 한다)"
    assert dict(os.environ) == before, "os.environ 원본이 make_isolated_env 호출로 바뀌면 안 된다"


def test_verify_silent_switch_detects_step9_2_style_mangling(installer_test_module, tmp_path):
    """6: STEP 9-2 사고를 그대로 재현한 로그(Git Bash 가 훼손한 명령줄)를 실제로 감지해야 한다."""
    ti = installer_test_module
    mangled = tmp_path / "mangled.log"
    mangled.write_text(
        'Setup command line: /SL5="$1" "C:/Program Files/Git/VERYSILENT" '
        '"C:/Program Files/Git/SUPPRESSMSGBOXES" /LOG=x\n', encoding="utf-8")
    problems = ti.verify_silent_switch_reached_setup(mangled, "/VERYSILENT")
    assert problems, "훼손된 명령줄을 감지하지 못했다"

    clean = tmp_path / "clean.log"
    clean.write_text('Setup command line: /SL5="$1" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\n',
                     encoding="utf-8")
    assert ti.verify_silent_switch_reached_setup(clean, "/VERYSILENT") == []

    missing = tmp_path / "missing.log"
    assert ti.verify_silent_switch_reached_setup(missing, "/VERYSILENT"), "로그가 없으면 실패로 봐야 한다"


def test_full_mode_checks_isolation_before_any_subprocess_call(installer_test_src):
    """1, 5: --full 블록에서 assert_isolated() 호출이 run_setup() 첫 호출보다 앞서야 한다
    ('실행해보고 나중에 복구' 금지 — 시작 전에 안전성이 확인돼야 한다)."""
    src = installer_test_src
    full_block = src[src.index("if a.full:"):]
    assert_pos = full_block.find("assert_isolated(")
    run_pos = full_block.find("run_setup(setup")
    assert assert_pos != -1, "--full 경로에 assert_isolated 호출이 없다"
    assert run_pos != -1, "--full 경로에 실제 설치 호출이 없다"
    assert assert_pos < run_pos, "격리 확인이 설치 실행보다 먼저 실행돼야 한다"


def test_run_setup_requires_explicit_env_and_forwards_it(installer_test_src):
    """2: run_setup 은 env 를 필수 인자로 받고 subprocess.run 에 그대로 넘겨야 한다
    (기본값을 두면 '깜빡하고 안 넘기는' 실수로 실제 데이터 폴더가 쓰일 수 있다)."""
    src = installer_test_src
    m = re.search(r"def run_setup\(([^)]*)\)", src)
    assert m, "run_setup 정의를 찾지 못했다"
    params = m.group(1)
    assert re.search(r"\benv\s*:\s*dict\[str,\s*str\]", params), "env 가 필수(타입 지정된) 인자가 아니다"
    assert "env=" not in params, "env 에 기본값이 있으면 안 된다 (호출자가 매번 명시해야 한다)"
    body = src[src.index("def run_setup("):src.index("def run_setup(") + 800]
    assert re.search(r"subprocess\.run\([^)]*env=env", body, re.DOTALL), \
        "subprocess.run 에 env=env 가 전달되지 않는다"


def test_full_mode_passes_isolated_env_to_install_and_uninstall(installer_test_src):
    """2: 설치·제거 각 run_setup 호출 모두 make_isolated_env() 로 만든 env 를 넘겨야 한다."""
    full_block = installer_test_src[installer_test_src.index("if a.full:"):]
    calls = re.findall(r"run_setup\([^)]*\)", full_block, re.DOTALL)
    assert len(calls) >= 1, "--full 안에 run_setup 호출이 없다"
    for call in calls:
        assert "env=env" in call, f"격리 env 없이 호출됨: {call}"
    assert "make_isolated_env(" in full_block, "make_isolated_env 로 env 를 만들지 않는다"


def test_subprocess_uses_list_args_not_shell_string(installer_test_src):
    """3, 4: subprocess.run 은 리스트 인자([str(exe), *args])만 쓰고 shell=True 를 쓰지 않는다.

    'shell=True' 라는 글자 자체는 이 파일의 경고 주석/docstring에도 등장하므로(예: "shell=True 금지"),
    전체 텍스트에서 찾지 않고 실제 subprocess.run(...) 호출 인자 안에서만 찾는다."""
    src = installer_test_src
    run_calls = re.findall(r"subprocess\.run\([^)]*\)", src, re.DOTALL)
    assert run_calls, "subprocess.run 호출을 찾지 못했다"
    for call in run_calls:
        assert "shell=True" not in call, f"shell=True 가 실제 호출에 쓰였다: {call}"
        assert "shell=False" in call, f"shell=False 를 명시하지 않았다: {call}"
    m = re.search(r"cmd\s*=\s*\[([^\]]*)\]", src)
    assert m, "cmd 를 리스트로 구성하지 않는다"
    assert re.search(r"subprocess\.run\(\s*cmd\s*,", src), "subprocess.run 에 리스트(cmd) 를 넘기지 않는다"


def test_silent_switches_are_separate_list_elements(installer_test_src):
    """6: /VERYSILENT 등은 하나로 합쳐진 문자열이 아니라 리스트의 개별 원소로 전달돼야 한다."""
    src = installer_test_src
    calls = re.findall(r'run_setup\(\s*\w+\s*,\s*\[([^\]]*)\]', src)
    assert calls, "run_setup(..., [...]) 형태의 호출을 찾지 못했다"
    for args_src in calls:
        raw = re.findall(r'f?"([^"]*)"', args_src)
        elems = [e.strip() for e in raw]
        assert "/VERYSILENT" in elems, f"/VERYSILENT 가 개별 원소로 없다: {args_src}"
        assert "/SUPPRESSMSGBOXES" in elems, f"/SUPPRESSMSGBOXES 가 개별 원소로 없다: {args_src}"
        joined_switch = [e for e in elems if " " in e and e.startswith("/")]
        assert not joined_switch, f"스위치가 하나의 문자열로 합쳐져 있다: {joined_switch}"


def test_installer_test_script_has_no_credential_access(installer_test_src):
    """7: 이 파일에는 credential 조회/삭제를 '실행'하는 코드가 전혀 없어야 한다
    (test_no_code_deletes_production_credential/test_tests_never_read_production_credential 이
    packaging/ 전체를 이미 스캔하지만, 이 파일 전용으로 한 번 더 명시적으로 확인한다).

    'cmdkey /delete' 라는 글자는 upcon.iss 안에 실제로 그 명령이 있는지 정적으로 찾아보는
    check_uninstall_failsafe() 의 비교 대상 문자열로도 등장하므로(실행이 아니라 '찾기'),
    그 함수 밖에서만 검사한다."""
    src = installer_test_src
    body_without_static_scan = src[src.index("def run_setup("):]
    for needle in ("CredDelete", "delete_password", "get_fal_key", "cmdkey /delete", "keyring."):
        assert needle not in body_without_static_scan, f"credential 접근/삭제 코드가 있다: {needle}"


def test_full_mode_uses_marker_file_to_verify_data_preservation(installer_test_src):
    """8: 무인 제거 후 '데이터 보존' 은 실제 경로가 아니라 격리 폴더 안에 직접 심어둔
    표식 파일(marker)의 생존 여부로 판단해야 한다. (real_user_data_dir() 자체는 안전성 확인
    출력이나 assert_isolated() 안에서 '읽기 전용으로 경로만 비교'하는 용도로는 등장해도 된다 —
    여기서는 '보존 여부 판정' 로직만 실제 경로를 쓰지 않는지 좁혀서 확인한다.)"""
    full_block = installer_test_src[installer_test_src.index("if a.full:"):]
    assert re.search(r"marker\s*=\s*tmp_dir\s*/", full_block), "표식 파일을 tmp_dir 안에 만들지 않는다"
    assert "marker.write_text(" in full_block, "표식 파일을 실제로 쓰지 않는다"
    assert "marker.exists()" in full_block, "제거 후 표식 파일 생존 여부를 확인하지 않는다"

    m = re.search(r"제거 exit.*?problems\.append\([^)]*무인 제거가[^)]*\)", full_block, re.DOTALL)
    assert m, "무인 제거 후 데이터 보존 판정 블록을 찾지 못했다"
    preservation_check = m.group(0)
    assert "marker.exists()" in preservation_check, "보존 판정이 marker.exists() 를 쓰지 않는다"
    assert "real_user_data_dir()" not in preservation_check, \
        "보존 판정 로직이 실제 사용자 데이터 경로를 참조한다"


def test_full_mode_only_removes_temp_dir_on_cleanup(installer_test_src):
    """정리 단계에서 지우는 것은 임시 폴더(tmp_dir)뿐이어야 한다."""
    full_block = installer_test_src[installer_test_src.index("if a.full:"):]
    assert "shutil.rmtree(tmp_dir" in full_block, "정리 단계가 tmp_dir 을 지우지 않는다"
    assert not re.search(r"rmtree\(\s*real", full_block), "실제 경로를 rmtree 하는 코드가 있다"
    assert "DelTree" not in full_block and "cmdkey" not in full_block, \
        "이 스크립트가 직접 사용자 데이터/credential 삭제 명령을 실행하면 안 된다 (Uninstaller 에게만 맡긴다)"
