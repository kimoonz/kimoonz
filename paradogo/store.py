"""추적 이력 저장소(SQLite).

세 가지를 남긴다.

* ``state``  — (날짜, 캐빈) 별 현재 상태. 재시작해도 직전 상태를 이어받아 diff 할 수 있다.
* ``events`` — 상태 전환 로그. "이 날짜는 주로 몇 시에 취소가 나오는가" 같은 통계의 원천.
* ``polls``  — 폴링 성공/실패와 소요 시간. 감시가 조용한 게 '취소가 없어서'인지
  '요청이 계속 실패해서'인지 구분하려면 이게 있어야 한다.

폴링마다 전체 재고를 적재하지 않고 '현재 상태 + 변화 로그'만 남기므로
몇 달을 돌려도 DB가 몇 MB를 넘지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .clock import now_kst
from .inventory import Change, ChangeKind, Slot, Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    stay_date   TEXT NOT NULL,
    cabin       TEXT NOT NULL,
    available   INTEGER NOT NULL,
    zone        TEXT NOT NULL DEFAULT '',
    remaining   INTEGER,
    price       TEXT NOT NULL DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    last_change TEXT,
    PRIMARY KEY (stay_date, cabin)
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    stay_date  TEXT NOT NULL,
    cabin      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    zone       TEXT NOT NULL DEFAULT '',
    remaining  INTEGER,
    price      TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(stay_date);
CREATE TABLE IF NOT EXISTS polls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    slot_count  INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_polls_ts ON polls(ts);
"""


class TrackerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
            # 구역 열은 나중에 추가됐다. 예전 DB도 그대로 열리게 한다.
            for table in ("state", "events"):
                columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
                if "zone" not in columns:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN zone TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TrackerStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ 상태

    def load_state(self, source: str = "db") -> Snapshot | None:
        """마지막으로 저장된 상태를 스냅샷으로 복원한다. 비어 있으면 None."""
        rows = self.conn.execute(
            "SELECT stay_date, cabin, available, remaining, price, zone, last_seen FROM state"
        ).fetchall()
        if not rows:
            return None
        slots = tuple(
            Slot(
                stay_date=r["stay_date"],
                cabin=r["cabin"],
                available=bool(r["available"]),
                remaining=r["remaining"],
                price=r["price"] or "",
                zone=r["zone"] or "",
            )
            for r in rows
        )
        latest = max(r["last_seen"] for r in rows)
        try:
            taken_at = datetime.fromisoformat(latest)
        except ValueError:
            # 타임스탬프가 깨져 있어도 추적 자체를 막지는 않는다.
            taken_at = now_kst()
        return Snapshot(taken_at=taken_at, slots=slots, source=source)

    def save_state(self, snapshot: Snapshot, changed_keys: Iterable[tuple[str, str]] = ()) -> None:
        """현재 상태를 반영한다. 스냅샷에서 사라진 칸은 지운다."""
        ts = snapshot.taken_at.isoformat()
        changed = set(changed_keys)
        with self.conn:
            for slot in snapshot.slots:
                self.conn.execute(
                    """
                    INSERT INTO state
                        (stay_date, cabin, available, remaining, price, zone,
                         first_seen, last_seen, last_change)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stay_date, cabin) DO UPDATE SET
                        available   = excluded.available,
                        remaining   = excluded.remaining,
                        price       = excluded.price,
                        zone        = excluded.zone,
                        last_seen   = excluded.last_seen,
                        last_change = CASE WHEN ? THEN excluded.last_seen
                                           ELSE state.last_change END
                    """,
                    (
                        slot.stay_date,
                        slot.cabin,
                        int(slot.available),
                        slot.remaining,
                        slot.price,
                        slot.zone,
                        ts,
                        ts,
                        ts if slot.key in changed else None,
                        1 if slot.key in changed else 0,
                    ),
                )
            if snapshot.slots:
                keys = {s.key for s in snapshot.slots}
                for row in self.conn.execute("SELECT stay_date, cabin FROM state").fetchall():
                    if (row["stay_date"], row["cabin"]) not in keys:
                        self.conn.execute(
                            "DELETE FROM state WHERE stay_date = ? AND cabin = ?",
                            (row["stay_date"], row["cabin"]),
                        )

    # ------------------------------------------------------------ 이벤트

    def record_events(self, changes: Sequence[Change], note: str = "") -> None:
        if not changes:
            return
        with self.conn:
            self.conn.executemany(
                "INSERT INTO events (ts, stay_date, cabin, kind, zone, remaining, price, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        (change.at or now_kst()).isoformat(),
                        change.slot.stay_date,
                        change.slot.cabin,
                        change.kind.value,
                        change.slot.zone,
                        change.slot.remaining,
                        change.slot.price,
                        note,
                    )
                    for change in changes
                ],
            )

    def record_attempt(
        self, stay_date: str, cabin: str, stage: str, note: str = "", zone: str = ""
    ) -> None:
        """확보 시도 결과. 전환(events)과 같은 표에 남기되 kind 를 달리해서,
        '취소 감지 건수' 통계가 시도 횟수로 부풀지 않게 한다."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO events (ts, stay_date, cabin, kind, zone, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_kst().isoformat(), stay_date, cabin, f"attempt:{stage}", zone, note[:500]),
            )

    def recent_events(self, limit: int = 20, kinds: Sequence[str] | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM events"
        params: list[object] = []
        if kinds:
            query += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            params.extend(kinds)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()

    # ------------------------------------------------------------ 폴링 로그

    def record_poll(
        self,
        source: str,
        ok: bool,
        slot_count: int = 0,
        duration_ms: int = 0,
        error: str = "",
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO polls (ts, source, ok, slot_count, duration_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_kst().isoformat(), source, int(ok), slot_count, duration_ms, error[:500]),
            )

    def poll_health(self, last_n: int = 50) -> dict[str, float | int]:
        rows = self.conn.execute(
            "SELECT ok, duration_ms FROM polls ORDER BY id DESC LIMIT ?", (last_n,)
        ).fetchall()
        if not rows:
            return {"count": 0, "success_rate": 0.0, "avg_ms": 0.0}
        ok_count = sum(r["ok"] for r in rows)
        return {
            "count": len(rows),
            "success_rate": ok_count / len(rows),
            "avg_ms": sum(r["duration_ms"] for r in rows) / len(rows),
        }

    # ------------------------------------------------------------ 통계

    def cancellation_by_hour(self) -> list[tuple[int, int]]:
        """시간대별 취소(OPENED) 건수. '몇 시에 지켜봐야 하는가'에 대한 답."""
        rows = self.conn.execute(
            "SELECT substr(ts, 12, 2) AS hh, COUNT(*) AS n FROM events "
            "WHERE kind = ? GROUP BY hh ORDER BY hh",
            (ChangeKind.OPENED.value,),
        ).fetchall()
        return [(int(r["hh"]), r["n"]) for r in rows]

    def cancellation_by_date(self, limit: int = 15) -> list[tuple[str, int]]:
        """날짜별 취소 건수. 자주 풀리는 날짜를 찾는 데 쓴다."""
        rows = self.conn.execute(
            "SELECT stay_date, COUNT(*) AS n FROM events WHERE kind = ? "
            "GROUP BY stay_date ORDER BY n DESC, stay_date LIMIT ?",
            (ChangeKind.OPENED.value, limit),
        ).fetchall()
        return [(r["stay_date"], r["n"]) for r in rows]

    def cancellation_by_zone(self) -> list[tuple[str, int]]:
        """구역별 취소 건수. 어느 구역이 잘 풀리는지."""
        rows = self.conn.execute(
            "SELECT CASE WHEN zone = '' THEN '미상' ELSE zone END AS z, COUNT(*) AS n "
            "FROM events WHERE kind = ? GROUP BY z ORDER BY n DESC, z",
            (ChangeKind.OPENED.value,),
        ).fetchall()
        return [(r["z"], r["n"]) for r in rows]

    def survival_times(self, limit: int = 20) -> list[tuple[str, str, float]]:
        """취소가 뜨고 다시 마감되기까지 걸린 시간(초).

        '취소표가 평균 몇 초 만에 사라지는지' = 반응 속도를 얼마나 올려야 하는지.
        """
        rows = self.conn.execute(
            "SELECT ts, stay_date, cabin, kind FROM events "
            "WHERE kind IN (?, ?) ORDER BY stay_date, cabin, id",
            (ChangeKind.OPENED.value, ChangeKind.CLOSED.value),
        ).fetchall()
        result: list[tuple[str, str, float]] = []
        opened_at: dict[tuple[str, str], datetime] = {}
        for row in rows:
            key = (row["stay_date"], row["cabin"])
            ts = datetime.fromisoformat(row["ts"])
            if row["kind"] == ChangeKind.OPENED.value:
                opened_at[key] = ts
            elif key in opened_at:
                result.append((key[0], key[1], (ts - opened_at.pop(key)).total_seconds()))
        result.sort(key=lambda item: item[2])
        return result[:limit]

    def counts(self) -> dict[str, int]:
        return {
            "state": self.conn.execute("SELECT COUNT(*) FROM state").fetchone()[0],
            "events": self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "polls": self.conn.execute("SELECT COUNT(*) FROM polls").fetchone()[0],
            "opened": self.conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?", (ChangeKind.OPENED.value,)
            ).fetchone()[0],
        }
