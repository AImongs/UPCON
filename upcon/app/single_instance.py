"""중복 실행 방지 — 같은 사용자 데이터 폴더(queue.json/config.json)를 쓰는 UPCON 은 한 번에 하나만.

첫 실행이 느린 동안 아이콘을 두 번 더블클릭하면 창이 두 개 뜨고 queue.json 을 동시에 덮어쓰게 된다.
QLocalServer(Windows 명명 파이프) 이름을 데이터 폴더 경로에서 만들기 때문에
UPCON_DATA_DIR 가 다른 테스트 인스턴스는 서로 막지 않는다.

두 번째 실행 → 기존 서버에 'activate' 를 보내고 즉시 종료. 기존 창은 activated 시그널로 앞으로 나온다.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)
ACTIVATE = b"activate\n"


def instance_key(data_dir: Path) -> str:
    """데이터 폴더마다 다른 파이프 이름. 경로는 대소문자/구분자 정규화 후 해시 (파이프 이름 길이 제한)."""
    norm = str(Path(data_dir).resolve()).replace("\\", "/").lower()
    return "upcon-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


class SingleInstance(QObject):
    activated = Signal()      # 다른 프로세스가 실행을 시도했다 → 창을 앞으로

    def __init__(self, data_dir: Path, parent: QObject | None = None, connect_timeout_ms: int = 500):
        super().__init__(parent)
        self.key = instance_key(data_dir)
        self.already_running = False
        self._server: QLocalServer | None = None
        self._timeout = connect_timeout_ms
        self._acquire()

    # ---- 시작 ----
    def _acquire(self) -> None:
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if sock.waitForConnected(self._timeout):
            self.already_running = True
            sock.write(ACTIVATE)
            sock.flush()
            sock.waitForBytesWritten(self._timeout)
            sock.disconnectFromServer()
            log.info("another UPCON instance owns %s → asked it to activate", self.key)
            return
        # 비정상 종료로 남은 이름 정리 후 서버 시작
        QLocalServer.removeServer(self.key)
        srv = QLocalServer(self)
        if not srv.listen(self.key):
            log.warning("single-instance listen failed (%s) — continuing without guard", srv.errorString())
            return
        srv.newConnection.connect(self._on_connection)
        self._server = srv

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    def _on_connection(self) -> None:
        """이 파이프에 접속하는 쪽은 UPCON 두 번째 프로세스뿐이다 → 접속 자체를 '창 앞으로' 요청으로 본다.
        (payload 를 기다리면 상대가 먼저 끊었을 때 놓칠 수 있다)"""
        srv = self._server
        activated = False
        while srv is not None and srv.hasPendingConnections():
            conn = srv.nextPendingConnection()
            if conn is None:
                break
            conn.waitForReadyRead(50)
            conn.readAll()
            conn.disconnected.connect(conn.deleteLater)
            conn.disconnectFromServer()
            activated = True
        if activated:
            self.activated.emit()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self.key)
            self._server = None
