"""창 화면의 순수 로직. 창을 띄우지 않고 확인할 수 있는 것들."""

import logging
import queue
import threading

import pytest

# tkinter 는 일부 환경(서버·최소 설치)에 없다. 없으면 이 파일은 통째로 건너뛴다.
pytest.importorskip("tkinter", reason="tkinter 가 없는 환경")

from paradogo.gui import (  # noqa: E402
    MAX_LOG_LINES,
    DialogPrompter,
    QueueLogHandler,
    level_tag,
)


def test_level_tag_maps_severity_to_colour():
    assert level_tag(logging.DEBUG) == "info"
    assert level_tag(logging.INFO) == "info"
    assert level_tag(logging.WARNING) == "warn"
    assert level_tag(logging.ERROR) == "error"
    assert level_tag(logging.CRITICAL) == "error"


def test_log_handler_puts_formatted_records_on_the_queue():
    sink = queue.Queue()
    handler = QueueLogHandler(sink)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("paradogo.test.gui")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.warning("취소 발생")
    finally:
        logger.removeHandler(handler)

    kind, levelno, text = sink.get_nowait()
    assert kind == "log"
    assert levelno == logging.WARNING
    assert text == "취소 발생"


def test_log_handler_never_raises():
    class Exploding(queue.Queue):
        def put(self, *a, **k):
            raise RuntimeError("큐가 죽음")

    handler = QueueLogHandler(Exploding())
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "메시지", None, None)
    handler.emit(record)  # 로그 때문에 프로그램이 죽으면 안 된다


def test_log_cap_is_bounded():
    assert 200 <= MAX_LOG_LINES <= 20000


# --- 워커 스레드 ↔ 화면 사이의 물음/답 ---------------------------------------


def answer_next(sink: queue.Queue, value):
    """메인 스레드 흉내: 요청을 받아 답을 채우고 깨운다."""
    kind, req = sink.get(timeout=3)
    assert kind == "prompt"
    req.answer = value
    req.done.set()
    return req


def test_ask_marshals_to_the_main_thread():
    sink = queue.Queue()
    prompter = DialogPrompter(sink)
    result: list[str] = []

    worker = threading.Thread(target=lambda: result.append(prompter.ask("날짜?", "2026-09-19")))
    worker.start()
    req = answer_next(sink, "2026-10-03")
    worker.join(timeout=3)

    assert req.kind == "ask"
    assert req.message == "날짜?"
    assert req.default == "2026-09-19"
    assert result == ["2026-10-03"]


def test_ask_falls_back_to_default_when_dialog_is_cancelled():
    sink = queue.Queue()
    prompter = DialogPrompter(sink)
    result: list[str] = []

    worker = threading.Thread(target=lambda: result.append(prompter.ask("날짜?", "2026-09-19")))
    worker.start()
    answer_next(sink, None)  # 사용자가 [취소]
    worker.join(timeout=3)

    assert result == ["2026-09-19"]


def test_ask_yes_returns_the_dialog_answer():
    sink = queue.Queue()
    prompter = DialogPrompter(sink)
    result: list[bool] = []

    worker = threading.Thread(target=lambda: result.append(prompter.ask_yes("할까요?")))
    worker.start()
    answer_next(sink, False)
    worker.join(timeout=3)

    assert result == [False]


def test_ask_yes_uses_default_when_dialog_gives_nothing():
    sink = queue.Queue()
    prompter = DialogPrompter(sink)
    result: list[bool] = []

    worker = threading.Thread(
        target=lambda: result.append(prompter.ask_yes("할까요?", default=True))
    )
    worker.start()
    answer_next(sink, None)
    worker.join(timeout=3)

    assert result == [True]


def test_info_goes_straight_to_the_log():
    sink = queue.Queue()
    DialogPrompter(sink).info("  진행 중  \n")
    kind, levelno, text = sink.get_nowait()
    assert (kind, levelno) == ("log", logging.INFO)
    assert text == "  진행 중"


def test_worker_blocks_until_the_main_thread_answers():
    # 답하기 전에 워커가 먼저 지나가 버리면 마법사가 엉뚱한 상태로 진행된다.
    sink = queue.Queue()
    prompter = DialogPrompter(sink)
    finished = threading.Event()

    def work():
        prompter.ask("기다려", "")
        finished.set()

    threading.Thread(target=work, daemon=True).start()
    kind, req = sink.get(timeout=3)
    assert not finished.wait(0.3), "답하기 전에 워커가 진행해 버렸다"
    req.answer = "ok"
    req.done.set()
    assert finished.wait(3)
