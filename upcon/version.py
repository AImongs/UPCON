"""앱 버전/저작권 표기의 단일 출처(single source of truth).

여기만 고치면 About 화면, 상태 표시줄, Portable 빌드, 향후 Installer 가 모두 따라간다.
pyproject.toml 의 version 과 일치해야 하며,
tests/test_packaging.py::test_version_single_source_of_truth 가 자동으로 대조한다.
"""

from __future__ import annotations

__version__ = "0.3.3"

APP_NAME = "UPCON"
APP_TAGLINE = "AI VIDEO UPSCALER"
APP_DESCRIPTION = "AI Video Upscaler"
COPYRIGHT = "Copyright © 2026 UPCON"
