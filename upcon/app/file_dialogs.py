"""'영상 추가' 파일 대화상자의 시작 폴더.

처음 사용자는 Windows '동영상' 폴더에서, 그 뒤로는 마지막으로 영상을 추가한 폴더에서 시작한다.
프로그램 설치 폴더(%LOCALAPPDATA%/Programs/UPCON, _internal)나 현재 작업 폴더에서는 절대 시작하지 않는다
— 첫 화면에 unins000.exe 같은 낯선 파일이 보이면 초보 사용자가 당황한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QWidget

from upcon.core.constants import SUPPORTED_EXTENSIONS
from upcon.core.paths import project_root


def _is_dir(p: str | Path) -> bool:
    try:
        return bool(p) and Path(p).is_dir()
    except OSError:
        return False


def _inside_install_dir(p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(project_root().resolve())
    except (OSError, ValueError):
        return False


def default_start_dir() -> Path:
    """Windows 동영상 폴더 → 없으면 사용자 홈 → 그것도 없으면 드라이브 루트."""
    for loc in (QStandardPaths.StandardLocation.MoviesLocation, QStandardPaths.StandardLocation.HomeLocation):
        for d in QStandardPaths.standardLocations(loc):
            if _is_dir(d):
                return Path(d)
    return Path(Path.home().anchor or "/")


def resolve_start_dir(saved: str) -> Path:
    """설정에 저장된 마지막 폴더가 아직 있으면 그 폴더, 아니면 기본 폴더."""
    if _is_dir(saved):
        p = Path(saved)
        if not _inside_install_dir(p):
            return p
    return default_start_dir()


def remember_dir(paths: list[Path]) -> str:
    """추가한 파일들의 폴더 (첫 파일 기준). 설정에 저장할 문자열."""
    for p in paths:
        parent = Path(p).parent
        if _is_dir(parent):
            return str(parent)
    return ""


def pick_videos(parent: QWidget | None, title: str, start_dir: str) -> list[Path]:
    exts = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
    files, _ = QFileDialog.getOpenFileNames(parent, title, str(resolve_start_dir(start_dir)),
                                            f"영상 파일 ({exts});;모든 파일 (*.*)")
    return [Path(f) for f in files]
