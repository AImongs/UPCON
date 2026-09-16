"""앱이 사용하는 디렉터리 경로. 개발 실행과 PyInstaller 배포 실행 모두에서 동작한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from upcon import APP_NAME


def project_root() -> Path:
    """소스 트리(개발) 또는 PyInstaller 번들(배포)의 루트."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def resources_dir() -> Path:
    return project_root() / "upcon" / "resources"


def bundled_bin_dir() -> Path:
    """동봉된 ffmpeg / ffprobe / ncnn 실행파일 위치."""
    return project_root() / "bin"


def bundled_models_dir() -> Path:
    return project_root() / "models"


def notices_file() -> Path:
    """제3자 라이선스 고지 문서. 개발 트리와 PyInstaller 번들 양쪽에서 같은 상대 위치."""
    return project_root() / "docs" / "THIRD_PARTY_NOTICES.md"


def app_icon_file() -> Path | None:
    """앱 아이콘(.ico). 아직 최종 디자인이 없으므로 파일이 없으면 None 을 돌려주고
    기본 아이콘으로 동작한다. 나중에 upcon/resources/upcon.ico 하나만 넣으면
    실행 파일(PyInstaller)과 창 아이콘, 향후 Installer 가 모두 이 파일을 쓴다."""
    p = resources_dir() / "upcon.ico"
    return p if p.is_file() else None


def user_data_dir() -> Path:
    """설정·로그 저장 위치: %LOCALAPPDATA%\\UPCON (테스트는 UPCON_DATA_DIR 환경변수로 격리)"""
    override = os.environ.get("UPCON_DATA_DIR")
    if override:
        d = Path(override)
        d.mkdir(parents=True, exist_ok=True)
        return d
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return user_data_dir() / "config.json"
