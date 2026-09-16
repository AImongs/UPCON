"""사용자용 오류 메시지(한국어)와 개발자용 상세 정보를 분리한 예외."""

from __future__ import annotations


class UpconError(Exception):
    """사용자에게 보여줄 메시지(user_message)와 로그용 상세(detail)를 분리한다.

    UI 는 항상 user_message 만 표시하고, detail 은 로그에만 기록한다.
    detail 에는 API Key 등 민감정보를 넣지 않는다.
    """

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail


class UnsupportedFileError(UpconError):
    pass


class ProbeError(UpconError):
    pass


class BinaryNotFoundError(UpconError):
    pass
