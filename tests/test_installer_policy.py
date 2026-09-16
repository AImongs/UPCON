"""Installer 안전 정책 회귀 테스트 (설치를 실행하지는 않는다).

배경 (STEP 8-1 사고)
- Git Bash 로 `/VERYSILENT` 를 넘기면 MSYS 가 `C:/Program Files/Git/VERYSILENT` 로 바꿔버려
  설치 프로그램이 대화형으로 실행됐고, 그 결과 실제 사용자 데이터와 fal API Key 가 삭제됐다.
- 더 근본적으로, Inno 의 평범한 MsgBox 는 /SUPPRESSMSGBOXES 를 무시하고 IDYES 를 반환한다.
  무인 제거에서 사용자 데이터를 말없이 지우게 되므로 SuppressibleMsgBox(..., IDNO) 를 써야 한다.
"""

from __future__ import annotations

import ast
import re
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
