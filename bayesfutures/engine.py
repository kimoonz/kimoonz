"""신호 엔진 + 상시 감시 루프.

PC가 하루에 몇 번 재시작돼도 괜찮도록 설계:
  * 모든 예외를 종목 단위로 가둬서 하나가 죽어도 나머지는 계속 돈다
  * 발송 이력은 디스크에 저장 → 재시작 후 같은 신호를 다시 안 보낸다
  * 종료 신호(SIGINT/SIGTERM)를 받으면 상태를 저장하고 깔끔히 끝낸다
"""

from __future__ import annotations

import logging
import signal as signal_module
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config
from .data import DataLoader
from .instruments import Instrument, get as get_instrument
from .message import (format_briefing, format_error, format_signal, format_startup)
from .model import InstrumentModel, interval_seconds, last_closed_index
from .signals import Side, Signal, build_signal, combine
from .state import AlertState
from .telegram import Telegram

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


def is_market_open(now: datetime | None = None) -> bool:
    """CME 글로벡스 대략적 개장 여부.

    일~금 18:00 ET 개장, 17:00 ET 마감, 매일 17:00~18:00 ET 정비 시간.
    (거래소 휴일은 반영하지 않음 — 그날은 새 봉이 안 생겨 알림도 안 나간다)
    """
    now = (now or datetime.now(timezone.utc)).astimezone(NY)
    wd, t = now.weekday(), now.time()          # 월=0 ... 일=6
    if dtime(17, 0) <= t < dtime(18, 0):
        return False                            # 일일 정비
    if wd == 4 and t >= dtime(17, 0):
        return False                            # 금 17:00 이후
    if wd == 5:
        return False                            # 토
    if wd == 6 and t < dtime(18, 0):
        return False                            # 일 18:00 이전
    return True


@dataclass
class Engine:
    cfg: Config
    telegram: Telegram
    state: AlertState
    loader: DataLoader = field(init=False)
    models: dict[tuple[str, str], InstrumentModel] = field(default_factory=dict, init=False)
    _last_bar: dict[tuple[str, str], datetime] = field(default_factory=dict, init=False)
    _last_error_sent: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.loader = DataLoader(
            cache_dir=self.cfg.data.cache_dir,
            cache_minutes=self.cfg.data.cache_minutes,
            source=self.cfg.data.source,
        )

    # ------------------------------------------------------------------ 구성
    def instruments(self) -> list[Instrument]:
        return [get_instrument(k) for k in self.cfg.instruments]

    def active_timeframes(self) -> list[str]:
        return [name for name, tf in self.cfg.timeframes.items() if tf.enabled]

    def _model(self, inst: Instrument, tf_name: str) -> InstrumentModel:
        key = (inst.key, tf_name)
        if key not in self.models:
            self.models[key] = InstrumentModel(
                self.cfg, inst, tf_name, self.cfg.timeframes[tf_name], self.loader
            )
        return self.models[key]

    # ------------------------------------------------------------------ 평가
    def evaluate(self, inst: Instrument, tf_name: str, refit: bool = True) -> Signal:
        """한 종목/타임프레임의 현재 신호."""
        model = self._model(inst, tf_name)
        model.load(force=refit)
        if refit:
            model.model = None                  # 새 봉이 생겼으면 다시 학습
        pred = model.predict_latest()
        return build_signal(self.cfg, pred)

    def evaluate_all(self, refit: bool = True) -> dict[str, dict[str, Signal]]:
        """전 종목 × 전 타임프레임. 실패한 조합은 건너뛴다."""
        out: dict[str, dict[str, Signal]] = {}
        for inst in self.instruments():
            per_tf: dict[str, Signal] = {}
            for tf_name in self.active_timeframes():
                try:
                    per_tf[tf_name] = self.evaluate(inst, tf_name, refit=refit)
                except Exception as exc:
                    log.warning("%s/%s 평가 실패: %s", inst.key, tf_name, exc)
            if per_tf:
                out[inst.key] = per_tf
        return out

    # ------------------------------------------------------------------ 발송
    def send_signals(self, results: dict[str, dict[str, Signal]],
                     force: bool = False) -> int:
        """조건을 만족하는 신호만 발송. 발송 건수를 반환."""
        sent = 0
        for per_tf in results.values():
            overlap = combine(per_tf)
            for tf_name, sig in per_tf.items():
                if not sig.is_actionable and not (force and self.cfg.alerts.send_on_hold):
                    continue
                inst_key = sig.pred.instrument.key
                bar_seconds = interval_seconds(sig.pred.interval)
                cooldown = self.cfg.timeframes[tf_name].signal.cooldown_bars
                if not force and not self.state.should_send(
                    inst_key, tf_name, sig.side.name, sig.pred.asof.to_pydatetime(),
                    cooldown, bar_seconds
                ):
                    log.info("%s/%s 중복·쿨다운으로 생략", inst_key, tf_name)
                    continue
                if self.telegram.send(format_signal(self.cfg, sig, overlap)):
                    self.state.record_signal(inst_key, tf_name, sig.side.name,
                                             sig.pred.asof.to_pydatetime(), sig.prob)
                    sent += 1
        return sent

    def send_briefing(self, results: dict[str, dict[str, Signal]]) -> bool:
        return self.telegram.send(format_briefing(self.cfg, results))

    def _send_error(self, context: str, exc: Exception, min_interval: float = 3600.0) -> None:
        """오류 알림은 1시간에 한 번까지만 (도배 방지)."""
        now = time.time()
        if now - self._last_error_sent < min_interval:
            return
        self._last_error_sent = now
        self.telegram.send(format_error(context, exc), silent=True)

    # ------------------------------------------------------------------ 새 봉 감지
    def _new_bar_ready(self, inst: Instrument, tf_name: str) -> bool:
        """마지막으로 처리한 봉보다 새로 마감된 봉이 있는지."""
        model = self._model(inst, tf_name)
        tf = self.cfg.timeframes[tf_name]
        try:
            model.load(force=True)
        except Exception as exc:
            log.warning("%s/%s 데이터 갱신 실패: %s", inst.key, tf_name, exc)
            return False
        idx = last_closed_index(model.df.index, tf.interval)
        if idx < 0:
            return False
        bar_time = model.df.index[idx].to_pydatetime()
        key = (inst.key, tf_name)
        if self._last_bar.get(key) == bar_time:
            return False
        self._last_bar[key] = bar_time
        return True

    # ------------------------------------------------------------------ 1회 실행
    def run_once(self, send: bool = True, force: bool = False) -> dict[str, dict[str, Signal]]:
        results = self.evaluate_all(refit=True)
        if send:
            self.send_signals(results, force=force)
        return results

    # ------------------------------------------------------------------ 상시 감시
    def watch(self) -> None:
        stop = {"flag": False}

        def _handle(signum, _frame):
            log.info("종료 신호(%s) 수신 — 정리 중", signum)
            stop["flag"] = True

        for sg in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                signal_module.signal(sg, _handle)
            except (ValueError, OSError):
                pass                            # 메인 스레드가 아니면 무시

        self.state.mark_start()
        bot = None
        try:
            bot = self.telegram.check() if self.telegram.configured else None
        except Exception as exc:
            log.warning("봇 확인 실패: %s", exc)
        self.telegram.send(format_startup(self.cfg, bot))
        log.info("감시 시작 — %d초마다 확인", self.cfg.poll_seconds)

        while not stop["flag"]:
            cycle_start = time.time()
            try:
                self._cycle()
            except Exception as exc:            # 루프는 절대 죽지 않는다
                log.exception("감시 주기 오류")
                self._send_error("감시 주기", exc)

            elapsed = time.time() - cycle_start
            remaining = max(self.cfg.poll_seconds - elapsed, 5.0)
            slept = 0.0
            while slept < remaining and not stop["flag"]:
                time.sleep(min(2.0, remaining - slept))
                slept += 2.0

        self.state.save()
        log.info("감시 종료")

    def _cycle(self) -> None:
        now_kst = datetime.now(KST)

        # 1) 정기 브리핑 (장이 닫혀 있어도 보낸다)
        if self.cfg.alerts.briefing_enabled and self._briefing_due(now_kst):
            results = self.evaluate_all(refit=True)
            if results and self.send_briefing(results):
                self.state.record_briefing(now_kst.date())
            if self.cfg.alerts.signal_alerts:
                self.send_signals(results)
            return

        if not is_market_open():
            log.debug("장 마감 시간 — 대기")
            return
        if not self.cfg.alerts.signal_alerts:
            return

        # 2) 새 봉이 마감된 종목만 재평가
        for inst in self.instruments():
            for tf_name in self.active_timeframes():
                try:
                    if not self._new_bar_ready(inst, tf_name):
                        continue
                    log.info("%s/%s 새 봉 — 평가", inst.key, tf_name)
                    sig = self.evaluate(inst, tf_name, refit=True)
                    self.send_signals({inst.key: {tf_name: sig}})
                except Exception as exc:
                    log.warning("%s/%s 처리 실패: %s", inst.key, tf_name, exc)
                    self._send_error(f"{inst.name}/{tf_name}", exc)

    def _briefing_due(self, now_kst: datetime) -> bool:
        """오늘 브리핑 시각이 지났고 아직 안 보냈으면 True."""
        try:
            hh, mm = (int(x) for x in self.cfg.alerts.briefing_time_kst.split(":"))
        except ValueError:
            log.warning("briefing_time_kst 형식 오류: %s", self.cfg.alerts.briefing_time_kst)
            return False
        target = now_kst.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_kst < target:
            return False
        # 시각이 한참 지난 뒤 켜졌으면(재시작 등) 2시간 안쪽일 때만 보낸다
        if now_kst - target > timedelta(hours=2):
            return False
        return not self.state.briefing_sent_today(now_kst.date())
