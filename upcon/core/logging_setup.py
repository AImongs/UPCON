"""로깅 설정. 파일 로그(%LOCALAPPDATA%/UPCON/logs) + 민감정보 마스킹.

마스킹은 최종 포맷 문자열(예외 스택 포함)에 적용된다 → API Key, 토큰, fal CDN/큐 URL 이 로그에 남지 않는다.
"""

from __future__ import annotations

import logging
import logging.handlers
import re

from upcon.core.paths import logs_dir

_SENSITIVE_PATTERNS = [
    re.compile(r"(Authorization\s*['\"]?\s*[:=]\s*['\"]?Key\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(FAL_KEY\s*[=:]\s*)[^\s'\"]+", re.IGNORECASE),
    # fal 자격증명 형태 (uuid:secret)
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-zA-Z_\-]{16,}\b", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(https?://[^\s]+?[?&](?:token|signature|sig|key)=)[^&\s]+", re.IGNORECASE),
    # 업로드/결과 CDN 주소 (사용자 영상에 접근 가능한 링크) 와 큐 요청 URL
    re.compile(r"https?://[a-z0-9.-]*fal\.media/[^\s'\"]+", re.IGNORECASE),
    re.compile(r"https?://queue\.fal\.run/[^\s'\"]+", re.IGNORECASE),
]


def mask_sensitive(text: str) -> str:
    for pat in _SENSITIVE_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.lastindex else "") + "***", text)
    return text


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_sensitive(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        logs_dir() / "upcon.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # 외부 라이브러리의 요청 로그(URL/헤더 포함 가능)는 WARNING 이상만
    for name in ("httpx", "httpcore", "fal_client", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
