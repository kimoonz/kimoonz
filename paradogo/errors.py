"""도메인 예외 정의."""

from __future__ import annotations


class ParadogoError(Exception):
    """이 패키지가 발생시키는 모든 예외의 최상위 타입."""


class ConfigError(ParadogoError):
    """설정 파일이 잘못됐거나 필수 값이 비어 있을 때."""


class SelectorNotFound(ParadogoError):
    """selectors.yaml 의 후보 셀렉터가 하나도 화면에서 찾아지지 않을 때."""

    def __init__(self, key: str, candidates: list[str]) -> None:
        self.key = key
        self.candidates = candidates
        joined = "\n  - ".join(candidates) if candidates else "(후보 없음)"
        super().__init__(
            f"셀렉터 '{key}' 를 찾지 못했습니다. 시도한 후보:\n  - {joined}\n"
            f"→ `python -m paradogo discover` 로 실제 셀렉터를 다시 수집한 뒤 "
            f"config/selectors.yaml 의 '{key}' 항목을 고쳐 주세요."
        )


class LoginFailed(ParadogoError):
    """로그인 후에도 로그인 완료 표식이 나타나지 않을 때."""


class NoAvailability(ParadogoError):
    """원하는 날짜/캐빈에 빈자리가 없을 때."""


class BookingFailed(ParadogoError):
    """예약 진행 중 되돌릴 수 없는 단계에서 실패했을 때."""
