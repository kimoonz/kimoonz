"""텔레그램 메시지 포맷 (HTML parse_mode)."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Config
from .features import label_ko
from .model import Prediction, interval_seconds
from .positions import ExitEvent
from .signals import Side, Signal

KST = ZoneInfo("Asia/Seoul")
SIDE_ICON = {Side.LONG: "🟢", Side.SHORT: "🔴", Side.FLAT: "⚪"}
TF_KO = {"daily": "일봉", "hourly": "1시간봉"}


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _kst(ts: datetime, fmt: str = "%m/%d %H:%M") -> str:
    return ts.astimezone(KST).strftime(fmt)


def tf_ko(name: str) -> str:
    return TF_KO.get(name, name)


def _prob_scale(p: float, base: float | None = None, width: int = 15,
                lo: float = 0.35, hi: float = 0.65) -> str:
    """35~65% 구간 눈금. │=50%, ◦=기준확률, ●=현재 확률.

    관건이 되는 구간이 40~60%라서, 0~100% 막대로는 55%와 45%가
    똑같아 보인다. 가운데를 확대한 눈금이 훨씬 잘 읽힌다.
    """
    def pos(v: float) -> int:
        v = min(max(v, lo), hi)
        return int(round((v - lo) / (hi - lo) * (width - 1)))

    track = ["·"] * width
    track[width // 2] = "│"
    if base is not None:
        track[pos(base)] = "◦"
    track[pos(p)] = "●"
    return "".join(track)


def _horizon_ko(interval: str, bars: int) -> str:
    """보유기간을 사람이 쓰는 단위로."""
    if interval.endswith("d"):
        weeks = bars / 5.0                     # 영업일 기준
        return f"{bars}봉 (약 {weeks:.0f}주)" if weeks >= 1 else f"{bars}봉"
    hours = bars * interval_seconds(interval) / 3600.0
    if hours >= 24:
        return f"{bars}봉 (약 {hours / 24:.0f}일)"
    return f"{bars}봉 (약 {hours:.0f}시간)"


def format_signal(cfg: Config, sig: Signal, overlap: str | None = None) -> str:
    """매매 신호 1건 — 언제 들어가고 언제 나올지."""
    pred = sig.pred
    inst = pred.instrument
    icon = SIDE_ICON[sig.side]
    horizon = cfg.timeframes[pred.timeframe].label.horizon
    gap = sig.stop_distance

    lines = [
        f"{icon} <b>{_esc(sig.side.value)} · {_esc(inst.name)}</b> "
        f"<code>{_esc(inst.micro.code)}/{_esc(inst.full.code)}</code>",
        f"{tf_ko(pred.timeframe)} · {_kst(pred.asof)} 마감 기준",
        "",
        f"<b>진입  {inst.fmt(sig.entry)}</b>  ← 지금",
        f"손절  {inst.fmt(sig.stop)}  ({-gap:+,.{inst.price_decimals}f})",
        f"목표  {inst.fmt(sig.target)}  ({abs(sig.target - sig.entry):+,.{inst.price_decimals}f})",
        "",
        f"<b>확률 {sig.prob:.1%}</b>   기준 {pred.base_rate:.1%} 대비 "
        f"<b>{sig.lift:+.1%}p</b>",
        f"<code>35% {_prob_scale(pred.prob_up, pred.base_rate)} 65%</code>",
        f"기대값 {sig.expected_r:+.2f}R · 손익비 1:{sig.risk_reward:.1f}"
        f" · 비용 {sig.cost_r:.2f}R",
        "",
    ]

    if cfg.alerts.show_position_size and sig.sizing:
        risk_budget = cfg.account.equity_usd * cfg.account.risk_per_trade_pct / 100.0
        lines.append(
            f"<b>계약 수</b> (계좌 ${cfg.account.equity_usd:,.0f} · "
            f"리스크 {cfg.account.risk_per_trade_pct:.1f}% = ${risk_budget:,.0f})"
        )
        for sz in sig.sizing:
            lines.append(
                f"  {_esc(sz.spec.name)} <code>{_esc(sz.spec.code)}</code> "
                f"<b>{sz.contracts}계약</b>  (1계약 리스크 ${sz.per_contract_risk:,.0f})"
            )
        if all(sz.contracts == 0 for sz in sig.sizing):
            lines.append(_zero_size_note(cfg, sig))
        lines.append("")

    if sig.reasons:
        lines.append("<b>판단 근거</b> (로그오즈 기여)")
        for r in sig.reasons[:5]:
            lines.append(
                f"  <code>{r['contribution']:+.2f}</code> {_esc(label_ko(r['name']))}"
                f"  <i>{_esc(r['bin'])}</i>"
            )
        lines.append("")

    lines += [
        "<b>청산</b> — 셋 중 먼저 닿는 것",
        f"  ① <code>{inst.fmt(sig.target)}</code> 도달 → 익절",
        f"  ② <code>{inst.fmt(sig.stop)}</code> 도달 → 손절",
        f"  ③ {_horizon_ko(pred.interval, horizon)} 경과 → 시간 청산",
        "  <i>도달하면 청산 알림을 다시 보냅니다</i>",
        "",
        _regime_line(pred),
    ]
    if overlap:
        lines.append(f"🔗 {_esc(overlap)}")
    lines.append(_model_line(pred))
    lines.append("")
    lines.append("<i>확률 추정일 뿐 보장이 아닙니다. 손절은 반드시 걸어두세요.</i>")
    return "\n".join(lines)


EXIT_ICON = {"target": "✅", "stop": "❌", "timeout": "⏱", "reverse": "🔄"}


def format_exit(cfg: Config, ev: ExitEvent, instrument) -> str:
    """청산 알림 — 진입 알림을 보낸 포지션이 목표/손절/만기에 닿았을 때."""
    pos = ev.position
    side_ko = "매수" if pos.side == "LONG" else "매도"
    icon = EXIT_ICON.get(ev.reason, "•")
    sign = "+" if ev.pnl_price >= 0 else ""

    return "\n".join([
        f"{icon} <b>청산 · {_esc(instrument.name)} {side_ko}</b> "
        f"<code>{_esc(instrument.micro.code)}</code>",
        f"{tf_ko(pos.timeframe)} · {_esc(ev.reason_ko)}",
        "",
        f"진입  <code>{instrument.fmt(pos.entry)}</code>  {_kst(pd.Timestamp(pos.entry_bar))}",
        f"청산  <code>{instrument.fmt(ev.exit_price)}</code>  {_kst(pd.Timestamp(ev.exit_bar))}",
        f"<b>손익  {sign}{ev.pnl_price:,.{instrument.price_decimals}f} "
        f"({ev.pnl_r:+.2f}R)</b>  ·  {ev.bars_held}봉 보유",
        "",
        f"<i>진입 시 확률 {pos.prob:.1%}였습니다.</i>",
    ])


def _zero_size_note(cfg: Config, sig: Signal) -> str:
    """0계약이 나온 이유를 정확히 짚어준다.

    '계좌가 작아서'와 '확률이 임계치에 가까워서'는 대응이 완전히 다르다.
    """
    cheapest = min(sig.sizing, key=lambda s: s.spec.point_value)
    if cheapest.blocked_by_confidence:
        return (
            f"  ⚠️ 신뢰도 스케일링으로 0계약입니다. 확률 {sig.prob:.1%}가 임계치에 가까워"
            f" 리스크 한도의 {cheapest.size_factor:.0%}만 씁니다."
            f" 한도를 다 쓰면 {_esc(cheapest.spec.code)} {cheapest.full_budget_contracts}계약."
            f" 확신이 서면 소량, 아니면 보류."
        )
    per = cheapest.per_contract_risk
    pct = cfg.account.risk_per_trade_pct / 100.0
    need_equity = per / pct if pct > 0 else 0.0
    need_pct = per / cfg.account.equity_usd * 100.0 if cfg.account.equity_usd else 0.0
    return (
        f"  ⚠️ 1계약도 리스크 한도를 넘습니다. {_esc(cheapest.spec.code)} 1계약 리스크가"
        f" ${per:,.0f}이라, 계좌 ${need_equity:,.0f} (현 리스크"
        f" {cfg.account.risk_per_trade_pct:.1f}%) 또는 리스크 {need_pct:.1f}%가"
        f" 필요합니다 — 진입 보류를 권합니다."
    )


def _regime_line(pred: Prediction) -> str:
    if pred.regime_probs is None or not pred.regime_names:
        return "국면: 미사용"
    best = int(pred.regime_probs.argmax())
    name = pred.regime_names[best] if best < len(pred.regime_names) else f"상태{best}"
    return f"국면: {_esc(name)} {pred.regime_probs[best]:.0%}"


def _model_line(pred: Prediction) -> str:
    return (f"<i>모델: 학습 {pred.n_train}봉 · 기준확률 {pred.base_rate:.1%}"
            f" · 보정 a={pred.calib_a:.2f}</i>")


def format_briefing(cfg: Config, results: dict[str, dict[str, Signal]],
                    now: datetime | None = None,
                    positions: list | None = None,
                    prices: dict[str, float] | None = None) -> str:
    """하루 1회 전체 요약. 보유 중인 포지션이 있으면 맨 위에 보여준다."""
    now = now or datetime.now(KST)
    lines = [
        f"📊 <b>확률 브리핑</b> · {now.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST",
        "",
    ]
    if positions:
        lines.append("<b>보유 중</b>")
        lines += _position_lines(positions, prices or {})
        lines.append("")
    for inst_key, per_tf in results.items():
        if not per_tf:
            continue
        any_sig = next(iter(per_tf.values()))
        lines.append(f"<b>{_esc(any_sig.pred.instrument.name)}</b> "
                     f"<code>{_esc(any_sig.pred.instrument.micro.code)}</code>  "
                     f"{any_sig.pred.instrument.fmt(any_sig.pred.last_price)}")
        for tf_name, sig in per_tf.items():
            icon = SIDE_ICON[sig.side]
            p_up = sig.pred.prob_up
            note = (f"{_esc(sig.side.value)} {sig.expected_r:+.2f}R"
                    if sig.is_actionable else "관망")
            lines.append(
                f"  {icon} {tf_ko(tf_name)}  {p_up:.1%} (기준 {sig.pred.base_rate:.0%}"
                f", {sig.lift:+.1%}p) · {note}"
            )
            lines.append(f"     <code>{_prob_scale(p_up, sig.pred.base_rate)}</code>")
        lines.append("")

    actionable = [s for per_tf in results.values() for s in per_tf.values() if s.is_actionable]
    if actionable:
        lines.append(f"👉 진입 후보 {len(actionable)}건")
    else:
        lines.append("👉 오늘은 확률이 임계치를 넘은 종목이 없습니다. 관망.")
    return "\n".join(lines)


def _position_lines(positions: list, prices: dict[str, float]) -> list[str]:
    """보유 포지션을 현재가 기준 평가손익과 함께."""
    from .instruments import INSTRUMENTS

    out = []
    for pos in positions:
        inst = INSTRUMENTS.get(pos.instrument)
        name = inst.name if inst else pos.instrument
        fmt = inst.fmt if inst else (lambda v: f"{v:,.2f}")
        side_ko = "매수" if pos.side == "LONG" else "매도"
        icon = "🟢" if pos.side == "LONG" else "🔴"
        now_price = prices.get(pos.instrument)
        pnl = f"  평가 {pos.pnl_r(now_price):+.2f}R" if now_price else ""
        out.append(
            f"  {icon} {_esc(name)} {side_ko} @{fmt(pos.entry)}"
            f"  손절 {fmt(pos.stop)} / 목표 {fmt(pos.target)}{pnl}"
        )
    return out


def format_positions(positions: list, prices: dict[str, float] | None = None) -> str:
    """보유 포지션 조회."""
    if not positions:
        return "📭 <b>보유 중인 포지션이 없습니다.</b>"
    lines = [f"📋 <b>보유 포지션 {len(positions)}건</b>", ""]
    lines += _position_lines(positions, prices or {})
    return "\n".join(lines)


def format_startup(cfg: Config, bot_name: str | None = None) -> str:
    tfs = ", ".join(tf_ko(k) for k, v in cfg.timeframes.items() if v.enabled)
    names = ", ".join(k for k in cfg.instruments)
    return (
        "✅ <b>감시 시작</b>\n"
        f"종목: {_esc(names)}\n"
        f"타임프레임: {_esc(tfs)}\n"
        f"확인 주기: {cfg.poll_seconds}초\n"
        f"브리핑: 매일 {cfg.alerts.briefing_time_kst} KST\n"
        + (f"봇: @{_esc(bot_name)}\n" if bot_name else "")
        + f"<i>{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST</i>"
    )


def format_error(context: str, exc: Exception) -> str:
    return (f"⚠️ <b>오류</b> · {_esc(context)}\n<code>{_esc(repr(exc)[:400])}</code>\n"
            f"<i>{datetime.now(KST).strftime('%m/%d %H:%M')} KST · 감시는 계속됩니다</i>")


# ---------------------------------------------------------------- 전략 배분 알림
ACTION_ICON = {"enter": "🟢", "exit": "🔴", "increase": "⬆️", "decrease": "⬇️"}
ACTION_KO = {"enter": "진입", "exit": "청산", "increase": "증량", "decrease": "감량"}


def format_allocation(change, equity: float, portfolio: dict | None = None) -> str:
    """배분 변경 알림 — 무엇을 몇 계약 사고팔지."""
    t = change.target
    inst = t.instrument
    icon = ACTION_ICON.get(change.action, "•")
    verb = "매수" if change.delta > 0 else "매도"

    lines = [
        f"{icon} <b>{ACTION_KO.get(change.action, change.action)} · "
        f"{_esc(inst.name)}</b> <code>{_esc(inst.micro.code)}</code>",
        f"{_esc(change.reason)}",
        "",
        f"<b>{verb}  {_esc(inst.micro.code)} {abs(change.delta)}계약</b>"
        f"  @ <code>{inst.fmt(t.price)}</code>",
    ]
    if change.action in ("increase", "decrease"):
        lines.append(f"보유  {change.held}계약 → <b>{t.target_contracts}계약</b>")
    elif change.action == "enter":
        lines.append(f"목표 비중  {t.target_weight:.0%}  "
                     f"(노출 ${t.target_contracts * t.contract_notional:,.0f})")

    alloc = portfolio or {}
    if change.action == "exit" and alloc.get("entry_price"):
        entry = float(alloc["entry_price"])
        pnl_pct = (t.price - entry) / entry if entry else 0.0
        pnl_usd = (t.price - entry) * inst.micro.point_value * change.held
        lines.append(f"진입  <code>{inst.fmt(entry)}</code>"
                     + (f"  ({_kst(pd.Timestamp(alloc['entry_date']))})"
                        if alloc.get("entry_date") else ""))
        lines.append(f"<b>손익  {pnl_pct:+.1%}  (${pnl_usd:+,.0f})</b>")

    lines += ["", "<b>추세 기준선</b>"]
    lines.append(f"  126일  <code>{inst.fmt(t.ma_fast)}</code>"
                 f"  {'✅' if t.trend_fast_ok else '❌'}")
    lines.append(f"  252일  <code>{inst.fmt(t.ma_slow)}</code>"
                 f"  {'✅' if t.trend_slow_ok else '❌'}")
    if t.trend_on and t.target_contracts > 0:
        drop = t.distance_to_exit / t.price if t.price else 0.0
        lines.append(f"  청산선 <code>{inst.fmt(t.exit_level)}</code>"
                     f"  (현재가 대비 {-drop:.1%})")
    lines.append(f"변동성  연 {t.realized_vol:.0%}")
    lines.append("")
    lines.append("<i>추세가 살아있는 동안 보유합니다. 기준선을 하향 이탈하면 "
                 "청산 알림을 보냅니다.</i>" if change.action != "exit"
                 else "<i>추세가 다시 켜지면 진입 알림을 보냅니다.</i>")
    return "\n".join(lines)


def format_portfolio(targets: list, held: dict, equity: float,
                     target_vol: float, allocations: dict | None = None,
                     now: datetime | None = None) -> str:
    """포트폴리오 현황 브리핑."""
    now = now or datetime.now(KST)
    allocations = allocations or {}
    lines = [
        f"📊 <b>포트폴리오</b> · {now.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST",
        f"<i>계좌 ${equity:,.0f} · 목표 변동성 연 {target_vol:.0%}</i>",
        "",
    ]

    holding = [t for t in targets if held.get(t.instrument.key, 0) > 0]
    if holding:
        lines.append("<b>보유 중</b>")
        for t in holding:
            n = held[t.instrument.key]
            inst = t.instrument
            alloc = allocations.get(inst.key, {})
            entry = float(alloc.get("entry_price", t.price))
            pnl = (t.price - entry) / entry if entry else 0.0
            lines.append(
                f"  🟢 {_esc(inst.name)} <code>{_esc(inst.micro.code)}</code> {n}계약"
                f"  {inst.fmt(entry)} → {inst.fmt(t.price)}  <b>{pnl:+.1%}</b>"
            )
            lines.append(f"     청산선 {inst.fmt(t.exit_level)}"
                         f"  ({-t.distance_to_exit / t.price:.1%})")
        lines.append("")

    flat = [t for t in targets if held.get(t.instrument.key, 0) == 0]
    pending = [t for t in flat if t.target_contracts > 0]
    if pending:
        lines.append("<b>진입 대상</b>")
        for t in pending:
            inst = t.instrument
            lines.append(
                f"  🟢 {_esc(inst.name)} <code>{_esc(inst.micro.code)}</code> "
                f"{t.target_contracts}계약 매수  @ {inst.fmt(t.price)}"
                f"  (목표 비중 {t.target_weight:.0%})"
            )
        lines.append("")

    waiting = [t for t in flat if t.target_contracts == 0]
    if waiting:
        lines.append("<b>대기</b>")
        for t in waiting:
            inst = t.instrument
            if t.blocked_by_granularity:
                lines.append(
                    f"  ⚠️ {_esc(inst.name)} {inst.fmt(t.price)} — 추세 ON이지만 "
                    f"1계약(${t.contract_notional:,.0f})이 목표 비중 초과"
                )
                lines.append(f"     이 종목을 담으려면 계좌 ${t.equity_needed:,.0f} 필요")
                continue
            missing = []
            if not t.trend_fast_ok:
                missing.append(f"126일 {inst.fmt(t.ma_fast)}")
            if not t.trend_slow_ok:
                missing.append(f"252일 {inst.fmt(t.ma_slow)}")
            lines.append(
                f"  ⚪ {_esc(inst.name)} {inst.fmt(t.price)}"
                + (f" — {_esc(' · '.join(missing))} 아래" if missing
                   else " — 목표 비중이 1계약에 못 미침")
            )
        lines.append("")

    exposure = sum(held.get(t.instrument.key, 0) * t.contract_notional for t in targets)
    if equity:
        lines.append(f"총 노출 <b>${exposure:,.0f}</b> (계좌의 {exposure / equity:.0%})")
    return "\n".join(lines)
