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

import pandas as pd

from .config import Config
from .data import DataLoader
from .instruments import Instrument, get as get_instrument
from .message import (format_allocation, format_briefing, format_error, format_exit,
                      format_portfolio, format_signal, format_startup)
from .model import InstrumentModel, interval_seconds, last_closed_index
from .positions import ExitEvent, OpenPosition, check_exit, force_exit
from .strategy import (StrategyParams, allocation_changes, current_targets,
                       suggest_target_vol)
from .signals import Side, Signal, build_signal, combine
from .state import AlertState
from .tz import zone
from .telegram import Telegram

log = logging.getLogger(__name__)

KST = zone("Asia/Seoul")
NY = zone("America/New_York")


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

                # 이미 같은 방향으로 들고 있으면 다시 들어가지 않는다
                held = self.state.get_position(inst_key, tf_name)
                if held is not None and held.side == sig.side.name:
                    log.info("%s/%s 이미 %s 보유 중 — 생략", inst_key, tf_name, held.side)
                    continue

                if not force and not self.state.should_send(
                    inst_key, tf_name, sig.side.name, sig.pred.asof.to_pydatetime(),
                    cooldown, bar_seconds
                ):
                    log.info("%s/%s 중복·쿨다운으로 생략", inst_key, tf_name)
                    continue

                # 반대 방향을 들고 있으면 먼저 청산 알림
                if held is not None:
                    self._emit_exit(
                        sig.pred.instrument,
                        force_exit(held, sig.pred.last_price,
                                   sig.pred.asof.to_pydatetime(),
                                   self._bars_since(held, sig.pred.asof.to_pydatetime(),
                                                    bar_seconds)),
                    )

                if self.telegram.send(format_signal(self.cfg, sig, overlap)):
                    self.state.record_signal(inst_key, tf_name, sig.side.name,
                                             sig.pred.asof.to_pydatetime(), sig.prob)
                    self.state.open_position(OpenPosition(
                        instrument=inst_key, timeframe=tf_name, side=sig.side.name,
                        entry=sig.entry, stop=sig.stop, target=sig.target,
                        entry_bar=sig.pred.asof.isoformat(),
                        horizon=self.cfg.timeframes[tf_name].label.horizon,
                        prob=sig.prob, atr=sig.pred.atr,
                    ))
                    sent += 1
        return sent

    # ------------------------------------------------------------------ 청산 감시
    @staticmethod
    def _bars_since(pos: OpenPosition, now: datetime, bar_seconds: int) -> int:
        entry = datetime.fromisoformat(pos.entry_bar)
        return max(1, int((now - entry).total_seconds() // max(bar_seconds, 1)))

    def _emit_exit(self, inst: Instrument, ev: ExitEvent) -> None:
        """청산 알림을 보내고 포지션 기록을 지운다."""
        if self.cfg.alerts.exit_alerts:
            self.telegram.send(format_exit(self.cfg, ev, inst))
        self.state.close_position(ev.position.instrument, ev.position.timeframe)
        log.info("%s/%s 청산 (%s) %+.2fR", ev.position.instrument,
                 ev.position.timeframe, ev.reason, ev.pnl_r)

    def check_exits(self) -> int:
        """보유 중인 포지션이 목표/손절/만기에 닿았는지 확인. 청산 건수를 반환."""
        closed = 0
        for pos in self.state.all_positions():
            try:
                inst = get_instrument(pos.instrument)
            except KeyError:
                self.state.close_position(pos.instrument, pos.timeframe)
                continue
            if pos.timeframe not in self.cfg.timeframes:
                continue
            try:
                model = self._model(inst, pos.timeframe)
                model.load(force=True)
                tf = self.cfg.timeframes[pos.timeframe]
                idx = last_closed_index(model.df.index, tf.interval)
                if idx < 0:
                    continue
                ev = check_exit(pos, model.df.iloc[:idx + 1])
                if ev is not None:
                    self._emit_exit(inst, ev)
                    closed += 1
            except Exception as exc:
                log.warning("%s/%s 청산 확인 실패: %s", pos.instrument, pos.timeframe, exc)
        return closed

    def send_briefing(self, results: dict[str, dict[str, Signal]]) -> bool:
        prices = {key: next(iter(per_tf.values())).pred.last_price
                  for key, per_tf in results.items() if per_tf}
        return self.telegram.send(format_briefing(
            self.cfg, results, positions=self.state.all_positions(), prices=prices))

    def _send_error(self, context: str, exc: Exception, min_interval: float = 3600.0) -> None:
        """오류 알림은 1시간에 한 번까지만 (도배 방지)."""
        now = time.time()
        if now - self._last_error_sent < min_interval:
            return
        self._last_error_sent = now
        self.telegram.send(format_error(context, exc), silent=True)

    # ------------------------------------------------------------ 전략 배분
    def strategy_params(self, prices: pd.DataFrame | None = None) -> StrategyParams:
        """설정에서 전략 파라미터를 만든다. target_vol=0 이면 계좌에 맞춰 자동."""
        sc = self.cfg.strategy
        params = StrategyParams(
            trend_fast=sc.trend_fast, trend_slow=sc.trend_slow,
            vol_window=sc.vol_window, max_scale=sc.max_scale,
            target_vol=sc.target_vol or 0.15,
        )
        if not sc.target_vol and prices is not None and len(prices):
            point_values = {i.key: i.micro.point_value for i in self.instruments()}
            params.target_vol = suggest_target_vol(
                prices, point_values, self.cfg.account.equity_usd, params)
        return params

    def _daily_prices(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        """전략용 일봉 데이터 (전략은 일봉 기준이다)."""
        tf_name = "daily" if "daily" in self.cfg.timeframes else next(iter(self.cfg.timeframes))
        tf = self.cfg.timeframes[tf_name]
        data: dict[str, pd.DataFrame] = {}
        for inst in self.instruments():
            try:
                data[inst.key] = self.loader.get(inst.yahoo, tf.interval, tf.lookback_days)
            except Exception as exc:
                log.warning("%s 전략 데이터 실패: %s", inst.key, exc)
        prices = pd.DataFrame({k: v["close"] for k, v in data.items()}).ffill()
        return data, prices

    def strategy_state(self):
        """현재 목표 배분 + 파라미터."""
        data, prices = self._daily_prices()
        if not data:
            return [], StrategyParams()
        params = self.strategy_params(prices)
        instruments = {i.key: i for i in self.instruments()}
        return current_targets(data, instruments, params,
                               self.cfg.account.equity_usd), params

    def run_strategy(self, send: bool = True) -> int:
        """목표 배분과 보유를 비교해 변경분만 알린다. 알림 건수를 반환."""
        if not self.cfg.strategy.enabled:
            return 0
        targets, params = self.strategy_state()
        if not targets:
            return 0

        held = self.state.held_contracts()
        changes = allocation_changes(targets, held,
                                     self.cfg.strategy.min_delta_contracts)
        sent = 0
        for change in changes:
            key = change.target.instrument.key
            alloc = self.state.get_allocation(key)
            if send and not self.telegram.send(
                format_allocation(change, self.cfg.account.equity_usd, alloc)
            ):
                continue
            self.state.set_allocation(
                key, change.target.target_contracts, change.target.price,
                datetime.now(timezone.utc), change.target.target_weight)
            sent += 1
            log.info("배분 %s: %s %d→%d계약", change.action, key,
                     change.held, change.target.target_contracts)
        return sent

    def send_portfolio(self) -> bool:
        targets, params = self.strategy_state()
        if not targets:
            return False
        return self.telegram.send(format_portfolio(
            targets, self.state.held_contracts(), self.cfg.account.equity_usd,
            params.target_vol, self.state.data.get("allocations", {})))

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
        if send:
            self.check_exits()               # 청산이 먼저 — 나갈 자리부터 알린다
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
            try:
                self.check_exits()
            except Exception as exc:
                log.warning("청산 확인 중 오류: %s", exc)
            # 전략 배분 점검이 먼저 — 이게 수익 구조의 핵심이다
            try:
                self.run_strategy()
                self.send_portfolio()
            except Exception as exc:
                log.warning("전략 배분 점검 실패: %s", exc)
                self._send_error("전략 배분", exc)

            results = self.evaluate_all(refit=True)
            if results and self.send_briefing(results):
                self.state.record_briefing(now_kst.date())
            if self.cfg.alerts.signal_alerts:
                self.send_signals(results)
            return

        if not is_market_open():
            log.debug("장 마감 시간 — 대기")
            return

        # 2) 보유 포지션 청산 확인 (진입 신호보다 먼저)
        try:
            self.check_exits()
        except Exception as exc:
            log.warning("청산 확인 중 오류: %s", exc)

        if not self.cfg.alerts.signal_alerts:
            return

        # 3) 새 봉이 마감된 종목만 재평가
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
