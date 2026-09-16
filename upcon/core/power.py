"""배치 처리 중 Windows 자동 절전 방지.

SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) 는 호출한 프로세스가 살아 있는 동안만
시스템 절전 타이머를 막는 표준 API 다. 전원 설정 자체를 바꾸지 않으며, release() 또는 프로세스 종료 시
원래 동작으로 돌아간다. 화면 끄기는 막지 않는다 (ES_DISPLAY_REQUIRED 미사용).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """실행 상태는 스레드 단위이므로, 전용 스레드 하나가 acquire~release 동안 살아 있게 한다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.calls: list[str] = []          # 테스트/진단용 기록

    @property
    def active(self) -> bool:
        return self._active

    def acquire(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
            self._stop.clear()
            self._thread = threading.Thread(target=self._hold, name="upcon-keepawake", daemon=True)
            self._thread.start()
        self.calls.append("acquire")
        log.info("keep-awake: on (system sleep prevented while batch runs)")

    def release(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.calls.append("release")
        log.info("keep-awake: off (normal power behavior restored)")

    def _hold(self) -> None:
        if sys.platform != "win32":
            self._stop.wait()
            return
        k32 = ctypes.windll.kernel32
        k32.SetThreadExecutionState.restype = ctypes.c_uint32
        prev = k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        if prev == 0:
            log.warning("SetThreadExecutionState failed")
        try:
            self._stop.wait()
        finally:
            k32.SetThreadExecutionState(ES_CONTINUOUS)   # 이 스레드의 요청 해제 → 원래 전원 동작
