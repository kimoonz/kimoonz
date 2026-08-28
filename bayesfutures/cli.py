"""명령줄 인터페이스."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import walk_forward
from .config import Config, load_config
from .data import DataLoader
from .engine import Engine
from .instruments import INSTRUMENTS, get as get_instrument
from .message import format_briefing, format_signal, tf_ko
from .model import InstrumentModel
from .signals import build_signal, combine
from .state import AlertState
from .telegram import Telegram


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=FutureWarning)


def _load_env(path: Path) -> None:
    """.env 파일이 있으면 환경변수로 읽어들인다 (이미 있는 값은 유지)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _build_engine(cfg: Config, dry_run: bool) -> Engine:
    telegram = Telegram(cfg.telegram_token, cfg.telegram_chat_id, dry_run=dry_run,
                        on_chat_migrated=cfg.save_chat_id)
    state = AlertState.load(cfg.state_dir)
    return Engine(cfg=cfg, telegram=telegram, state=state)


# ---------------------------------------------------------------- 서브커맨드
def cmd_once(args, cfg: Config) -> int:
    engine = _build_engine(cfg, args.dry_run)

    # 전략 배분이 먼저 — 수익 구조의 핵심이다
    if cfg.strategy.enabled and not args.no_strategy:
        try:
            if engine.run_strategy() == 0:
                engine.send_portfolio()
        except Exception as exc:
            print(f"전략 배분 점검 실패: {exc}", file=sys.stderr)

    closed = engine.check_exits()
    if closed:
        print(f"청산 {closed}건 처리")
    results = engine.evaluate_all(refit=True)
    if not results:
        print("평가된 종목이 없습니다. 네트워크/심볼을 확인하세요.", file=sys.stderr)
        return 1

    if args.briefing:
        engine.send_briefing(results)
    else:
        sent = engine.send_signals(results, force=args.force)
        if sent == 0:
            print("발송할 신호가 없습니다 (임계치 미달 또는 중복).")
            print()
            print(_plain(format_briefing(cfg, results)))
    return 0


def cmd_watch(args, cfg: Config) -> int:
    engine = _build_engine(cfg, args.dry_run)
    engine.watch()
    return 0


def cmd_backtest(args, cfg: Config) -> int:
    loader = DataLoader(cfg.data.cache_dir, 10_000, cfg.data.source)
    keys = args.instruments or cfg.instruments
    tfs = args.timeframes or [k for k, v in cfg.timeframes.items() if v.enabled]

    all_preds = []
    for key in keys:
        for tf_name in tfs:
            inst = get_instrument(key)
            model = InstrumentModel(cfg, inst, tf_name, cfg.timeframes[tf_name], loader)
            print(f"\n{'=' * 60}\n{inst.name} · {tf_ko(tf_name)}", flush=True)
            try:
                res = walk_forward(cfg, model, refit_every=args.refit_every,
                                   max_points=args.max_points)
            except Exception as exc:
                print(f"  실패: {exc}")
                continue
            print(f"  기간 {res.metrics['period']}")
            for line in res.summary_lines():
                print("  " + line)
            if args.reliability:
                print("\n  [신뢰도 곡선] 예측확률 구간별 실제 적중률")
                print("  " + res.reliability.to_string(index=False).replace("\n", "\n  "))
            if args.tune:
                print("\n  [임계치별 성과]")
                print("  " + _tune_table(cfg, model, res.predictions)
                      .to_string(index=False).replace("\n", "\n  "))
            preds = res.predictions.copy()
            preds["instrument"] = key
            preds["timeframe"] = tf_name
            all_preds.append(preds)

    if args.out and all_preds:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.concat(all_preds).to_csv(out, index=False)
        print(f"\n예측 결과 저장: {out}")
    return 0


def _tune_table(cfg: Config, model: InstrumentModel, preds: pd.DataFrame) -> pd.DataFrame:
    """임계치를 바꿔가며 신호 수와 평균 손익을 보여준다.

    '얼마나 자주 알림을 받을 것인가 vs 얼마나 좋은 신호만 받을 것인가'의
    교환 관계를 눈으로 고르라는 표.
    """
    lab = model.tf.label
    spec = model.inst.micro
    cost_usd = cfg.costs.commission_per_contract + cfg.costs.slippage_ticks * spec.tick_value
    rr_long = lab.up_mult / lab.down_mult          # 매수: 상단이 목표
    rr_short = lab.down_mult / lab.up_mult         # 매도: 하단이 목표 (역수)
    span_years = max((preds["asof"].iloc[-1] - preds["asof"].iloc[0]).days / 365.25, 1e-9)

    rows = []
    for thr in (0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.65):
        lift = preds["prob"] - preds.get("base", 0.5)
        min_lift = model.tf.signal.min_prob_over_base
        longs = preds[(preds["prob"] >= thr) & (lift >= min_lift)]
        shorts = preds[(preds["prob"] <= 1 - thr) & (-lift >= min_lift)]
        n = len(longs) + len(shorts)
        if n == 0:
            rows.append({"임계치": f"{thr:.0%}", "신호수": 0, "월평균": 0.0,
                         "적중률": np.nan, "평균R": np.nan, "누적R": 0.0})
            continue
        won = pd.concat([longs["y"] == 1.0, shorts["y"] == 0.0]).to_numpy()
        atrs = pd.concat([longs["atr"], shorts["atr"]]).to_numpy()
        rr = np.r_[np.full(len(longs), rr_long), np.full(len(shorts), rr_short)]
        stop_mult = np.r_[np.full(len(longs), lab.down_mult),
                          np.full(len(shorts), lab.up_mult)]
        cost_r = cost_usd / np.where(atrs > 0, stop_mult * atrs * spec.point_value, np.nan)
        net = np.where(won, rr, -1.0) - cost_r
        rows.append({
            "임계치": f"{thr:.0%}", "신호수": n,
            "월평균": round(n / (span_years * 12), 1),
            "적중률": round(float(won.mean()), 3),
            "평균R": round(float(np.nanmean(net)), 3),
            "누적R": round(float(np.nansum(net)), 1),
        })
    return pd.DataFrame(rows)


def cmd_plan(args, cfg: Config) -> int:
    """계좌 규모로 이 전략을 어떻게 굴릴 수 있는지."""
    import numpy as np

    from .strategy import (StrategyParams, affordable_instruments, evaluate,
                           performance, suggest_target_vol, target_weights)

    equity = args.equity or cfg.account.equity_usd
    loader = DataLoader(cfg.data.cache_dir, 10_000, cfg.data.source)
    instruments = [get_instrument(k) for k in cfg.instruments]
    prices, point_values = {}, {}
    for inst in instruments:
        try:
            prices[inst.key] = loader.get(inst.yahoo, "1d", 7300)["close"]
            point_values[inst.key] = inst.micro.point_value
        except Exception as exc:
            print(f"  {inst.name} 데이터 실패: {exc}")
    if not prices:
        print("시세를 가져오지 못했습니다.", file=sys.stderr)
        return 1
    px = pd.DataFrame(prices).ffill()

    print(f"계좌 ${equity:,.0f} 기준\n")
    print("1계약 명목가치 (마이크로)")
    for inst in instruments:
        if inst.key not in px:
            continue
        notional = float(px[inst.key].iloc[-1]) * inst.micro.point_value
        share = notional / equity
        mark = "  ⚠️ 계좌의 절반 초과" if share > 0.5 else ""
        print(f"  {inst.micro.code:5s} {inst.name:10s} ${notional:>10,.0f}"
              f"  (계좌의 {share:>5.0%}){mark}")

    ok = affordable_instruments(px, point_values, equity)
    print(f"\n분산에 쓸 수 있는 종목: "
          f"{', '.join(get_instrument(k).micro.code for k in ok) if ok else '없음'}")
    if len(ok) < 2:
        print("  ⚠️ 2종목 미만이면 분산 효과가 없습니다. 이 구조의 핵심이 분산인데,")
        print("     계좌 대비 계약이 너무 커서 나눠 담을 수가 없는 상태입니다.")

    base = StrategyParams(trend_fast=cfg.strategy.trend_fast,
                          trend_slow=cfg.strategy.trend_slow,
                          vol_window=cfg.strategy.vol_window,
                          max_scale=cfg.strategy.max_scale)
    split = pd.Timestamp("2018-01-01", tz="UTC")
    print("\n목표 변동성별 예상 (2018년 이후 = 표본외 구간, 왕복 2bp 차감)")
    rows = []
    for tv in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50):
        params = StrategyParams(**{**base.__dict__, "target_vol": tv})
        ret = evaluate(px, params, equity, point_values)
        stats = performance(ret[ret.index >= split])
        if not stats:
            continue
        contracts = np.floor((target_weights(px, params) * equity)
                             / (px * pd.Series(point_values))).clip(lower=0)
        rows.append({
            "목표변동성": f"{tv:.0%}", "연수익": f"{stats['연수익']:.1%}",
            "샤프": round(stats["샤프"], 2),
            "최대낙폭": f"{stats['최대낙폭']:.1%}",
            "칼마": round(stats["칼마"], 2) if np.isfinite(stats["칼마"]) else None,
            "평균계약": round(float(contracts.sum(axis=1).tail(2520).mean()), 1),
        })
    table = pd.DataFrame(rows)
    print(table.to_string(index=False) if len(table) else "  계산 불가")

    auto = suggest_target_vol(px, point_values, equity, base)
    print(f"\n자동 추천: 목표 변동성 {auto:.0%}"
          f"  (config.yaml 의 strategy.target_vol 에 넣으면 고정됩니다)")
    print("\n※ 목표 변동성은 낙폭에 거의 비례합니다. 위 표에서 감당할 수 있는")
    print("  최대낙폭을 먼저 고르고, 그 줄의 목표 변동성을 쓰세요.")
    return 0


def cmd_strategy(args, cfg: Config) -> int:
    """전략 현재 상태 확인 / 알림 발송."""
    engine = _build_engine(cfg, args.dry_run)
    if args.portfolio:
        engine.send_portfolio()
        return 0
    sent = engine.run_strategy()
    if sent == 0:
        print("배분 변경 없음 — 현재 상태:")
        print()
        engine.send_portfolio()
    return 0


def cmd_positions(args, cfg: Config) -> int:
    """지금 보유 중인 것으로 추적되는 포지션."""
    from .message import format_positions

    state = AlertState.load(cfg.state_dir)
    positions = state.all_positions()
    print(_plain(format_positions(positions)))
    if positions and args.clear:
        for pos in positions:
            state.close_position(pos.instrument, pos.timeframe)
        print(f"\n{len(positions)}건을 기록에서 지웠습니다.")
    return 0


def cmd_telegram_test(args, cfg: Config) -> int:
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    if not tg.configured:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않았습니다.\n"
              ".env 파일을 만들거나 환경변수를 설정하세요.", file=sys.stderr)
        return 1
    try:
        name = tg.check()
    except Exception as exc:
        print(f"봇 확인 실패: {exc}", file=sys.stderr)
        return 1
    ok = tg.send("✅ <b>연결 테스트 성공</b>\n확률 기반 선물 알림봇이 정상 연결되었습니다.")
    print(f"봇 @{name} — 발송 {'성공' if ok else '실패'}")
    return 0 if ok else 1


def cmd_chatid(args, cfg: Config) -> int:
    """봇에게 아무 메시지나 보낸 뒤 실행하면 chat_id를 찾아준다."""
    import requests
    token = cfg.telegram_token or args.token
    if not token:
        print("TELEGRAM_BOT_TOKEN 을 설정하거나 --token 으로 넘기세요.", file=sys.stderr)
        return 1
    resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    if resp.status_code != 200:
        print(f"조회 실패 ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
        return 1
    updates = resp.json().get("result", [])
    if not updates:
        print("받은 메시지가 없습니다.\n"
              "텔레그램에서 봇을 찾아 /start 를 보낸 뒤 다시 실행하세요.")
        return 1
    seen = {}
    for u in updates:
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
        if chat:
            seen[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name", "")
    for cid, label in seen.items():
        print(f"chat_id = {cid}   ({label})")
    print("\n.env 파일에 아래 줄을 넣으세요:")
    print(f"TELEGRAM_CHAT_ID={next(iter(seen))}")
    return 0


def _plain(html_text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html_text)


# ---------------------------------------------------------------- 진입점
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bayesfutures",
        description="확률·추세 기반 해외선물 매매 타이밍 텔레그램 알림",
    )
    p.add_argument("-c", "--config", help="설정 파일 경로 (기본: config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    sub = p.add_subparsers(dest="command", required=True)

    once = sub.add_parser("once", help="지금 한 번 평가하고 신호를 보낸다")
    once.add_argument("--dry-run", action="store_true", help="발송하지 않고 화면에만 출력")
    once.add_argument("--force", action="store_true", help="중복/쿨다운 무시하고 발송")
    once.add_argument("--briefing", action="store_true", help="신호 대신 전체 브리핑 발송")
    once.add_argument("--no-strategy", action="store_true", help="전략 배분 점검 생략")
    once.set_defaults(func=cmd_once)

    watch = sub.add_parser("watch", help="상시 감시 (PC에 띄워두는 모드)")
    watch.add_argument("--dry-run", action="store_true", help="발송하지 않고 화면에만 출력")
    watch.set_defaults(func=cmd_watch)

    bt = sub.add_parser("backtest", help="워크포워드 검증")
    bt.add_argument("-i", "--instruments", nargs="*", choices=list(INSTRUMENTS))
    bt.add_argument("-t", "--timeframes", nargs="*")
    bt.add_argument("--max-points", type=int, default=1500, help="검증할 최근 봉 수")
    bt.add_argument("--refit-every", type=int, default=63, help="재학습 주기(봉)")
    bt.add_argument("--reliability", action="store_true", help="신뢰도 곡선 출력")
    bt.add_argument("--tune", action="store_true", help="임계치별 성과표 출력")
    bt.add_argument("--out", help="예측 결과 CSV 저장 경로")
    bt.set_defaults(func=cmd_backtest)

    pl = sub.add_parser("plan", help="계좌 규모로 이 전략을 어떻게 굴릴지")
    pl.add_argument("--equity", type=float, help="계좌 평가금 (기본: config.yaml 값)")
    pl.set_defaults(func=cmd_plan)

    st = sub.add_parser("strategy", help="추세·분산 배분 전략 점검 및 알림")
    st.add_argument("--dry-run", action="store_true", help="발송하지 않고 화면에만 출력")
    st.add_argument("--portfolio", action="store_true", help="현황만 출력")
    st.set_defaults(func=cmd_strategy)

    ps = sub.add_parser("positions", help="보유 중으로 추적되는 포지션 조회")
    ps.add_argument("--clear", action="store_true",
                    help="추적 기록을 모두 지운다 (실제 주문과 어긋났을 때)")
    ps.set_defaults(func=cmd_positions)

    tt = sub.add_parser("telegram-test", help="텔레그램 연결 확인")
    tt.set_defaults(func=cmd_telegram_test)

    ci = sub.add_parser("chatid", help="내 chat_id 찾기")
    ci.add_argument("--token", help="봇 토큰 (환경변수 대신 직접 지정)")
    ci.set_defaults(func=cmd_chatid)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    _load_env(Path(".env"))
    cfg = load_config(args.config)
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print("\n중단됨")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
