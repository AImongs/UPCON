"""패키징/재현성 테스트 — 새 PC(클린 환경)에서 UPCON 이 그대로 구축되는지 지킨다.

배경: requirements.txt 에 keyring/httpx/fal-client 가 빠져 있어, 새로 clone 한 환경에서
`pip install -r requirements.txt` 후 `python -m upcon` 이 ImportError 로 죽는 문제가 있었다.
개발 PC 에는 수동 설치돼 있어 드러나지 않았으므로, 선언과 실제 import 를 자동으로 대조한다.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# import 이름 → 배포 패키지 이름 (다른 경우만)
IMPORT_TO_DIST = {
    "pynvml": "nvidia-ml-py",
    "fal_client": "fal-client",
}


def _third_party_imports(pkg_dir: Path) -> set[str]:
    """pkg_dir 아래 모든 .py 의 최상위 서드파티 import 모듈명."""
    stdlib = set(sys.stdlib_module_names)
    found: set[str] = set()
    for f in pkg_dir.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in stdlib and m != "upcon"}


def _declared(section: str = "dependencies") -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if section == "dependencies":
        specs = data["project"]["dependencies"]
    else:
        specs = data["project"]["optional-dependencies"][section]
    out = set()
    for s in specs:
        name = s.split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
        out.add(name.lower())
    return out


def test_every_runtime_import_is_declared():
    """upcon/ 이 import 하는 모든 서드파티가 pyproject dependencies 에 있어야 한다."""
    declared = _declared()
    missing = []
    for mod in sorted(_third_party_imports(ROOT / "upcon")):
        dist = IMPORT_TO_DIST.get(mod, mod).lower()
        if dist not in declared:
            missing.append(f"{mod} (배포명 {dist})")
    assert not missing, (
        "런타임 의존성 선언 누락 — 새 PC 에서 clone 하면 ImportError 로 실행 불가:\n  "
        + "\n  ".join(missing))


def test_requirements_txt_matches_pyproject():
    """requirements.txt 와 pyproject dependencies 가 같은 패키지 집합이어야 한다."""
    req = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        req.add(line.split(">=")[0].split("==")[0].split("<")[0].strip().lower())
    assert req == _declared(), f"requirements.txt={sorted(req)} != pyproject={sorted(_declared())}"


def test_runtime_deps_do_not_include_dev_or_convert_only():
    """pytest/pyinstaller/torch 는 런타임 의존성이 아니어야 한다."""
    declared = _declared()
    for name in ("pytest", "pyinstaller", "torch", "pillow"):
        assert name not in declared, f"{name} 은 런타임 의존성이 아니다"


def test_fal_client_internal_api_contract():
    """fal_client 의 내부 심볼에 의존하고 있으므로 존재를 검증한다.
    (업그레이드로 사라지면 유료 요청 중이 아니라 여기서 먼저 실패해야 한다.)"""
    import fal_client.client as fc
    for name in ("QUEUE_URL_FORMAT", "SyncRequestHandle", "_raise_for_status", "SyncClient"):
        assert hasattr(fc, name), f"fal_client.client.{name} 없음 — submit_once() 수정 필요"


def test_no_video_files_tracked_in_repo():
    """테스트 영상/결과 영상이 저장소에 들어가지 않아야 한다."""
    import subprocess

    import pytest
    try:
        r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        pytest.skip("git 실행파일 없음")
    if r.returncode != 0:
        pytest.skip("git 저장소 아님")
    bad = [f for f in r.stdout.splitlines()
           if Path(f).suffix.lower() in (".mp4", ".mov", ".mkv", ".avi", ".webm")]
    assert not bad, f"영상 파일이 추적되고 있음: {bad}"


def test_version_single_source_of_truth():
    """버전 문자열은 upcon/version.py 한 곳에서 온다. pyproject 와 어긋나면 실패한다."""
    import tomllib

    from upcon import APP_VERSION
    from upcon.version import __version__

    assert APP_VERSION == __version__, "upcon.APP_VERSION 이 version.py 와 다르다"
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__, (
        f"pyproject.toml version={data['project']['version']} != "
        f"upcon/version.py {__version__} — 한쪽만 올리면 About 화면과 배포본 버전이 어긋난다")


def test_notices_file_resolvable_without_absolute_path():
    """제3자 라이선스 문서는 project_root() 기준 상대 위치로 찾을 수 있어야 한다.
    (개발 트리와 PyInstaller 번들 모두 같은 코드 경로를 쓴다.)"""
    from upcon.core.paths import notices_file, project_root

    p = notices_file()
    assert p.is_file(), f"고지 문서 없음: {p}"
    assert p.is_relative_to(project_root()), "project_root() 밖을 가리키면 번들에서 깨진다"
    text = p.read_text(encoding="utf-8")
    assert "FFmpeg" in text and "GPL" in text
    assert "Real-ESRGAN" in text


def test_app_icon_optional():
    """아이콘 파일이 없어도 None 을 돌려주고 빌드/실행이 되어야 한다."""
    from upcon.core.paths import app_icon_file, resources_dir

    icon = app_icon_file()
    assert icon is None or icon == resources_dir() / "upcon.ico"
    if icon is not None:
        assert icon.is_file()


def test_about_dialog_texts_have_required_notices():
    """About 화면이 FFmpeg/GPL/제3자 사실을 명시해야 한다.
    확정되지 않은 Corresponding Source URL 을 넣지 않았는지도 확인한다."""
    import re

    from upcon.app.about_dialog import THIRD_PARTY_SUMMARY

    for token in ("FFmpeg", "GPLv3", "Real-ESRGAN", "ncnn", "제3자"):
        assert token in THIRD_PARTY_SUMMARY, f"About 고지에 {token} 누락"
    urls = re.findall(r"https?://\S+", THIRD_PARTY_SUMMARY)
    assert not urls, f"확정되지 않은 URL 이 About 고지에 있다: {urls}"
