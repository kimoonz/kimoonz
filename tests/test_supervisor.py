import os
from datetime import timedelta

from paradogo.clock import now_kst
from paradogo.config import Config
from paradogo.supervisor import (
    FIRST_BACKOFF_SEC,
    HEARTBEAT_STALE_SEC,
    MAX_BACKOFF_SEC,
    Heartbeat,
    describe,
    heartbeat_path,
    next_backoff,
    process_alive,
    read_heartbeat,
    write_heartbeat,
)

BASE = {
    "site": {"base_url": "https://example.test"},
    "account": {"login_id": "u", "password": "p"},
    "target": {"check_in_dates": ["2026-09-19"]},
}


def cfg_with_db(tmp_path):
    return Config.from_dict(
        {**BASE, "run": {"track": {"db_path": str(tmp_path / "state" / "tracker.db")}}}
    )


def test_backoff_grows_then_caps():
    step = FIRST_BACKOFF_SEC
    seen = []
    for _ in range(10):
        step = next_backoff(step)
        seen.append(step)
    assert seen[0] == FIRST_BACKOFF_SEC * 2
    assert seen[-1] == MAX_BACKOFF_SEC
    assert all(a <= b for a, b in zip(seen, seen[1:]))  # 단조 증가


def test_backoff_never_drops_below_first():
    assert next_backoff(0.0) == FIRST_BACKOFF_SEC
    assert next_backoff(-5) == FIRST_BACKOFF_SEC


def test_heartbeat_roundtrip(tmp_path):
    path = tmp_path / "hb.json"
    write_heartbeat(path, Heartbeat(state="tracking", pid=os.getpid(), round=7, opened_total=2))
    beat = read_heartbeat(path)
    assert beat.state == "tracking"
    assert beat.round == 7
    assert beat.opened_total == 2
    assert beat.updated_at


def test_heartbeat_path_sits_next_to_the_database(tmp_path):
    cfg = cfg_with_db(tmp_path)
    assert heartbeat_path(cfg) == tmp_path / "state" / "heartbeat.json"


def test_missing_heartbeat_reads_as_none(tmp_path):
    assert read_heartbeat(tmp_path / "nope.json") is None


def test_corrupt_heartbeat_reads_as_none(tmp_path):
    path = tmp_path / "hb.json"
    path.write_text("{ 깨진 파일", encoding="utf-8")
    assert read_heartbeat(path) is None


def test_heartbeat_ignores_unknown_fields(tmp_path):
    # 예전 버전이 남긴 파일에 없는 열이 있어도 죽지 않아야 한다.
    path = tmp_path / "hb.json"
    path.write_text('{"state": "tracking", "옛날필드": 1}', encoding="utf-8")
    assert read_heartbeat(path).state == "tracking"


def test_fresh_heartbeat_of_this_process_is_alive(tmp_path):
    path = tmp_path / "hb.json"
    write_heartbeat(path, Heartbeat(state="tracking", pid=os.getpid()))
    assert read_heartbeat(path).alive


def test_stale_heartbeat_is_not_alive():
    old = (now_kst() - timedelta(seconds=HEARTBEAT_STALE_SEC + 60)).isoformat()
    beat = Heartbeat(state="tracking", pid=os.getpid(), updated_at=old)
    assert not beat.alive


def test_heartbeat_of_dead_process_is_not_alive():
    beat = Heartbeat(state="tracking", pid=0, updated_at=now_kst().isoformat())
    assert not beat.alive


def test_stopped_heartbeat_is_not_alive():
    beat = Heartbeat(state="stopped", pid=os.getpid(), updated_at=now_kst().isoformat())
    assert not beat.alive


def test_process_alive_on_self_and_on_nothing():
    assert process_alive(os.getpid())
    assert not process_alive(0)


def test_describe_without_heartbeat_tells_how_to_start():
    text = describe(None)
    assert "돌고 있지 않습니다" in text
    assert "--forever" in text


def test_describe_running():
    beat = Heartbeat(
        state="tracking", pid=os.getpid(), updated_at=now_kst().isoformat(),
        started_at=(now_kst() - timedelta(hours=3)).isoformat(),
        round=120, slots=240, available=2, opened_total=5, source="api",
    )
    text = describe(beat)
    assert "감시 중" in text
    assert "3시간" in text
    assert "240칸 중 2칸" in text


def test_describe_needs_login_points_at_the_fix():
    beat = Heartbeat(state="needs_login", pid=0, updated_at=now_kst().isoformat())
    text = describe(beat)
    assert "로그인" in text
    assert "login --manual" in text


def test_describe_dead_process_is_reported_as_dead():
    beat = Heartbeat(
        state="tracking", pid=0,
        updated_at=(now_kst() - timedelta(hours=1)).isoformat(),
    )
    assert "죽어" in describe(beat)
