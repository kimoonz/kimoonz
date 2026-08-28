"""계속 켜두기 — 죽으면 되살리고, 살아 있는지 밖에서 확인할 수 있게 한다.

`track` 하나만 돌리면 오류가 몇 번 겹치거나 로그인 세션이 풀리는 순간 멈춘다.
취소는 새벽에도 나오므로, 멈춘 걸 아침에야 알면 그 사이는 놓친 것이다.

여기서 하는 일:

* 추적이 멈추면 backoff 를 두고 다시 띄운다.
* 사람이 손대야만 풀리는 문제(로그인 만료)는 재시도로 낫지 않으므로 알리고 기다린다.
* 매 회차 heartbeat 파일을 남겨, 다른 터미널에서 `status` 로 살아 있는지 볼 수 있게 한다.
* 하루 한 번 '아직 지켜보는 중' 요약을 보낸다. 조용한 게 정상인지 죽은 건지 구분되게.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .clock import humanize, now_kst
from .config import Config
from .errors import LoginFailed
from .flow import BookingResult
from .notify import Notifier
from .selectors import SelectorMap
from .tracker import run_track

log = logging.getLogger(__name__)

FIRST_BACKOFF_SEC = 30.0
MAX_BACKOFF_SEC = 900.0        # 15분. 이보다 길어지면 취소를 너무 오래 놓친다.
LOGIN_RETRY_SEC = 600.0        # 사람이 다시 로그인할 시간을 준다.
HEARTBEAT_STALE_SEC = 180.0    # 이만큼 갱신이 없으면 죽은 것으로 본다.
DAILY_SUMMARY_HOURS = 24.0


def next_backoff(current: float) -> float:
    """실패가 이어질수록 간격을 늘리되 상한을 둔다."""
    return min(MAX_BACKOFF_SEC, max(FIRST_BACKOFF_SEC, current * 2))


@dataclass(slots=True)
class Heartbeat:
    """지금 살아 있는지, 무엇을 하고 있는지."""

    state: str = "starting"       # starting | tracking | restarting | needs_login | stopped
    updated_at: str = ""
    started_at: str = ""
    pid: int = 0
    round: int = 0
    slots: int = 0
    available: int = 0
    opened_total: int = 0
    source: str = ""
    restarts: int = 0
    last_error: str = ""
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Heartbeat":
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    @property
    def age_seconds(self) -> float | None:
        if not self.updated_at:
            return None
        try:
            return (now_kst() - datetime.fromisoformat(self.updated_at)).total_seconds()
        except ValueError:
            return None

    @property
    def alive(self) -> bool:
        """갱신이 최근이고, 그 프로세스가 실제로 살아 있는가."""
        age = self.age_seconds
        if age is None or age > HEARTBEAT_STALE_SEC:
            return False
        if self.state in ("stopped",):
            return False
        return process_alive(self.pid)


def process_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 남의 프로세스지만 살아 있긴 하다
    except OSError:
        return False
    return True


def heartbeat_path(cfg: Config) -> Path:
    return cfg.run.track.db_path.parent / "heartbeat.json"


def write_heartbeat(path: Path, beat: Heartbeat) -> None:
    beat.updated_at = now_kst().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(beat.to_json(), encoding="utf-8")
        tmp.replace(path)  # 읽는 쪽이 반쪽짜리 파일을 보지 않도록
    except OSError as exc:
        log.debug("heartbeat 기록 실패: %s", exc)


def read_heartbeat(path: Path) -> Heartbeat | None:
    if not path.exists():
        return None
    try:
        return Heartbeat.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        log.debug("heartbeat 읽기 실패: %s", exc)
        return None


def describe(beat: Heartbeat | None) -> str:
    """`status` 가 보여줄 사람 말."""
    if beat is None:
        return "감시가 돌고 있지 않습니다. (기록 없음)\n→ python -m paradogo track --forever"

    age = beat.age_seconds
    lines: list[str] = []
    if beat.alive:
        lines.append(f"● 감시 중 (PID {beat.pid})")
    elif beat.state == "needs_login":
        lines.append("▲ 멈춰 있음 — 다시 로그인이 필요합니다")
        lines.append("  → python -m paradogo login --manual")
    elif beat.state == "stopped":
        lines.append("○ 정상 종료됨")
    else:
        lines.append("✗ 죽어 있습니다 (heartbeat 가 멈춤)")
        lines.append("  → python -m paradogo track --forever")

    if beat.started_at:
        try:
            uptime = (now_kst() - datetime.fromisoformat(beat.started_at)).total_seconds()
            lines.append(f"  가동 시간   : {humanize(uptime)}")
        except ValueError:
            pass
    if age is not None:
        lines.append(f"  마지막 확인 : {age:.0f}초 전 ({beat.round}회차)")
    if beat.slots:
        lines.append(f"  재고        : {beat.slots}칸 중 {beat.available}칸 예약가능")
    lines.append(f"  누적 취소   : {beat.opened_total}건 · 재시작 {beat.restarts}회")
    if beat.source:
        lines.append(f"  조회 경로   : {beat.source}")
    if beat.last_error:
        lines.append(f"  마지막 오류 : {beat.last_error[:200]}")
    if beat.note:
        lines.append(f"  메모        : {beat.note}")
    return "\n".join(lines)


@dataclass(slots=True)
class Supervisor:
    cfg: Config
    smap: SelectorMap
    notifier: Notifier
    stop_on_success: bool = False
    beat: Heartbeat = field(default_factory=Heartbeat)
    _stop: bool = False

    def __post_init__(self) -> None:
        self.beat.pid = os.getpid()
        self.beat.started_at = now_kst().isoformat()

    @property
    def path(self) -> Path:
        return heartbeat_path(self.cfg)

    def save(self, state: str | None = None, note: str = "") -> None:
        if state:
            self.beat.state = state
        if note:
            self.beat.note = note
        write_heartbeat(self.path, self.beat)

    def request_stop(self, *_: object) -> None:
        log.info("종료 신호를 받았습니다. 이번 회차를 마치고 멈춥니다.")
        self._stop = True

    def on_round(self, info: dict[str, Any]) -> None:
        self.beat.round = int(info.get("round", 0))
        self.beat.slots = int(info.get("slots", 0))
        self.beat.available = int(info.get("available", 0))
        self.beat.opened_total = int(info.get("opened_total", 0))
        self.beat.source = str(info.get("source", ""))
        self.beat.last_error = ""
        self.save("tracking")

    async def run(self) -> BookingResult | None:
        backoff = FIRST_BACKOFF_SEC
        last_summary = now_kst()
        self.save("starting")

        self.notifier.send(
            "🟢 상시 감시를 켰습니다",
            "\n".join(
                [
                    f"대상: {', '.join(d.isoformat() for d in self.cfg.target.check_in_dates)}",
                    f"박수: {', '.join(f'{n}박' for n in self.cfg.target.nights_options)}",
                    "멈추면 알아서 다시 띄웁니다. 하루 한 번 살아 있다고 알려드립니다.",
                    "상태 확인: python -m paradogo status",
                ]
            ),
        )

        try:
            while not self._stop:
                try:
                    result = await run_track(
                        self.cfg, self.smap, self.notifier, on_round=self.on_round
                    )

                    if result is not None and not result.ok:
                        # 추적기가 스스로 손을 든 경우(조회 연속 실패 등).
                        # 예외로 죽은 것과 똑같이 세고 똑같이 물러섰다 다시 온다.
                        self.beat.restarts += 1
                        self.beat.last_error = result.message
                        self.save("restarting")
                        log.warning("추적이 중단됐습니다(%d번째): %s",
                                    self.beat.restarts, result.message)
                        await self._sleep(backoff)
                        backoff = next_backoff(backoff)
                        continue

                    backoff = FIRST_BACKOFF_SEC  # 한 바퀴 정상적으로 돌았으면 리셋

                    if result is not None and result.reached_payment:
                        if self.stop_on_success:
                            self.save("stopped", "결제 페이지 도달 — 감시를 멈춥니다.")
                            return result
                        # 결제를 놓쳤을 수도 있으니 다시 지켜본다.
                        self.notifier.send(
                            "🔁 감시를 계속합니다",
                            "결제 화면까지 갔던 건은 브라우저에서 마무리하세요.\n"
                            "혹시 놓쳤을 수 있으니 감시는 계속합니다.",
                        )
                    if self._stop:
                        break

                except LoginFailed as exc:
                    # 재시도로 낫지 않는다. 사람이 다시 로그인해야 한다.
                    self.beat.last_error = str(exc)
                    self.save("needs_login")
                    self.notifier.send(
                        "🔑 다시 로그인이 필요합니다",
                        f"{exc}\n\n로그인만 다시 해주시면 감시는 알아서 이어집니다.",
                    )
                    log.error("%s", exc)
                    await self._sleep(LOGIN_RETRY_SEC)
                    continue

                except Exception as exc:
                    self.beat.restarts += 1
                    self.beat.last_error = f"{type(exc).__name__}: {exc}"
                    self.save("restarting")
                    log.warning(
                        "감시가 멈췄습니다(%d번째). %.0f초 뒤 다시 띄웁니다: %s",
                        self.beat.restarts,
                        backoff,
                        exc,
                    )
                    if self.beat.restarts in (1, 5, 20):
                        # 매번 알리면 시끄럽다. 처음과 계속 실패할 때만.
                        self.notifier.send(
                            f"⚠️ 감시가 멈춰서 다시 띄웁니다 ({self.beat.restarts}번째)",
                            f"{self.beat.last_error}\n{backoff:.0f}초 뒤 재시작합니다.",
                        )
                    await self._sleep(backoff)
                    backoff = next_backoff(backoff)
                    continue

                if now_kst() - last_summary >= timedelta(hours=DAILY_SUMMARY_HOURS):
                    last_summary = now_kst()
                    self.notifier.send(
                        "📅 아직 지켜보는 중입니다",
                        f"{self.beat.round}회 확인 · 누적 취소 {self.beat.opened_total}건 "
                        f"· 재시작 {self.beat.restarts}회",
                    )

                if not self._stop:
                    await self._sleep(FIRST_BACKOFF_SEC)
        finally:
            self.save("stopped")

        self.notifier.send("⏹️ 상시 감시를 껐습니다", f"{self.beat.round}회 확인했습니다.")
        return None

    async def _sleep(self, seconds: float) -> None:
        """종료 신호를 받으면 즉시 깨어난다."""
        remaining = seconds
        while remaining > 0 and not self._stop:
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step


async def run_forever(
    cfg: Config,
    smap: SelectorMap,
    notifier: Notifier,
    stop_on_success: bool = False,
) -> BookingResult | None:
    supervisor = Supervisor(cfg, smap, notifier, stop_on_success=stop_on_success)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, supervisor.request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows 는 add_signal_handler 를 지원하지 않는다. KeyboardInterrupt 로 처리된다.
            pass
    return await supervisor.run()
