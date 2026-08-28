"""알림 상태 저장.

PC가 재시작돼도 (1) 이미 보낸 신호를 또 보내지 않고
(2) 쿨다운이 유지되도록 디스크에 남긴다.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .positions import OpenPosition

log = logging.getLogger(__name__)


@dataclass
class AlertState:
    path: Path
    data: dict

    @classmethod
    def load(cls, state_dir: str | Path) -> "AlertState":
        path = Path(state_dir) / "alerts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {"signals": {}, "briefing": {}, "positions": {}, "allocations": {}, "started": None}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("상태 파일을 읽지 못해 새로 시작합니다 (%s): %s", path, exc)
        return cls(path, data)

    def save(self) -> None:
        """원자적 저장 — 쓰는 중에 재시작돼도 파일이 깨지지 않는다."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("상태 저장 실패: %s", exc)

    # ------------------------------------------------------------------
    @staticmethod
    def _key(instrument: str, timeframe: str) -> str:
        return f"{instrument}:{timeframe}"

    def last_signal(self, instrument: str, timeframe: str) -> dict | None:
        return self.data["signals"].get(self._key(instrument, timeframe))

    def should_send(self, instrument: str, timeframe: str, side: str,
                    bar_time: datetime, cooldown_bars: int, bar_seconds: int) -> bool:
        """같은 봉 중복 발송과 쿨다운 중 같은 방향 재발송을 막는다."""
        prev = self.last_signal(instrument, timeframe)
        if prev is None:
            return True
        if prev.get("bar_time") == bar_time.isoformat():
            return False                      # 같은 봉은 한 번만
        if prev.get("side") != side:
            return True                       # 방향이 바뀌면 즉시 알림
        try:
            prev_time = datetime.fromisoformat(prev["bar_time"])
        except (KeyError, ValueError):
            return True
        elapsed_bars = (bar_time - prev_time).total_seconds() / max(bar_seconds, 1)
        return elapsed_bars >= cooldown_bars

    def record_signal(self, instrument: str, timeframe: str, side: str,
                      bar_time: datetime, prob: float) -> None:
        self.data["signals"][self._key(instrument, timeframe)] = {
            "side": side, "bar_time": bar_time.isoformat(), "prob": prob,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def briefing_sent_today(self, today: date) -> bool:
        return self.data.get("briefing", {}).get("date") == today.isoformat()

    def record_briefing(self, today: date) -> None:
        self.data["briefing"] = {
            "date": today.isoformat(),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    # ------------------------------------------------------- 보유 포지션 추적
    def open_position(self, pos: OpenPosition) -> None:
        self.data.setdefault("positions", {})[self._key(pos.instrument, pos.timeframe)] = \
            pos.to_dict()
        self.save()

    def get_position(self, instrument: str, timeframe: str) -> OpenPosition | None:
        raw = self.data.get("positions", {}).get(self._key(instrument, timeframe))
        if not raw:
            return None
        try:
            return OpenPosition.from_dict(raw)
        except (TypeError, KeyError):
            log.warning("포지션 기록이 손상되어 무시합니다: %s/%s", instrument, timeframe)
            return None

    def close_position(self, instrument: str, timeframe: str) -> None:
        self.data.get("positions", {}).pop(self._key(instrument, timeframe), None)
        self.save()

    def all_positions(self) -> list[OpenPosition]:
        out = []
        for raw in list(self.data.get("positions", {}).values()):
            try:
                out.append(OpenPosition.from_dict(raw))
            except (TypeError, KeyError):
                continue
        return out

    # ------------------------------------------------------- 전략 배분 보유 현황
    def get_allocation(self, instrument: str) -> dict:
        return self.data.get("allocations", {}).get(instrument, {})

    def held_contracts(self) -> dict[str, int]:
        return {k: int(v.get("contracts", 0))
                for k, v in self.data.get("allocations", {}).items()}

    def set_allocation(self, instrument: str, contracts: int, price: float,
                       when: datetime, target_weight: float = 0.0) -> None:
        allocations = self.data.setdefault("allocations", {})
        if contracts <= 0:
            allocations.pop(instrument, None)
        else:
            prev = allocations.get(instrument, {})
            allocations[instrument] = {
                "contracts": int(contracts),
                "price": float(price),
                "target_weight": float(target_weight),
                # 신규 진입일 때만 진입가를 새로 쓴다 (증량은 기존 진입가 유지)
                "entry_price": float(prev.get("entry_price", price)) if prev else float(price),
                "entry_date": prev.get("entry_date") or when.isoformat(),
                "updated": when.isoformat(),
            }
        self.save()

    def mark_start(self) -> None:
        self.data["started"] = datetime.now(timezone.utc).isoformat()
        self.save()
