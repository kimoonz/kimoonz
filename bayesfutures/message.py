"""텔레그램 메시지 포맷 (HTML parse_mode)."""

from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Config
from .features import label_ko
from .model import Prediction
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


def format_signal(cfg: Config, sig: Signal, overlap: str | None = None) -> str:
    """매매 신호 1건."""
    pred = sig.pred
    inst = pred.instrument
    icon = SIDE_ICON[sig.side]
    codes = f"{inst.micro.code}/{inst.full.code}"

    lines = [
        f"{icon} <b>{_esc(sig.side.value)} 신호 · {_esc(inst.name)}</b> "
        f"<code>{_esc(codes)}</code>",
        f"{tf_ko(pred.timeframe)} · {_kst(pred.asof)} 봉 기준",
        "",
        f"<b>확률 {sig.prob:.1%}</b>   기준 {pred.base_rate:.1%} 대비 "
        f"<b>{sig.lift:+.1%}p</b>",
        f"<code>35% {_prob_scale(pred.prob_up, pred.base_rate)} 65%</code>",
        "<i>● 현재확률  ◦ 기준확률(종목 드리프트)  │ 50%</i>",
        f"기대값 <b>{sig.expected_r:+.2f}R</b> · 손익비 1:{sig.risk_reward:.1f}"
        f" · 비용 {sig.cost_r:.2f}R",
        "",
        f"현재가  <code>{inst.fmt(pred.last_price)}</code>",
        f"진입    <code>{inst.fmt(sig.entry)}</code>",
        f"손절    <code>{inst.fmt(sig.stop)}</code>"
        f"  ({sig.stop_distance / max(pred.atr, 1e-9):.1f} ATR)",
        f"목표    <code>{inst.fmt(sig.target)}</code>",
        "",
    ]

    risk_budget = cfg.account.equity_usd * cfg.account.risk_per_trade_pct / 100.0
    lines.append(
        f"<b>계약 수</b> (계좌 ${cfg.account.equity_usd:,.0f} · "
        f"리스크 {cfg.account.risk_per_trade_pct:.1f}% = ${risk_budget:,.0f})"
    )
    for s in sig.sizing:
        per = sig.stop_distance * s.spec.point_value
        lines.append(
            f"  {_esc(s.spec.name)} <code>{_esc(s.spec.code)}</code> "
            f"<b>{s.contracts}계약</b>  (1계약 리스크 ${per:,.0f})"
        )
    if sig.sizing and all(s.contracts == 0 for s in sig.sizing):
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

    lines.append(_regime_line(pred))
    if overlap:
        lines.append(f"🔗 {_esc(overlap)}")
    lines.append(_model_line(pred))
    lines.append("")
    lines.append("<i>확률 추정일 뿐 보장이 아닙니다. 손절은 반드시 걸어두세요.</i>")
    return "\n".join(lines)


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
                    now: datetime | None = None) -> str:
    """하루 1회 전체 요약."""
    now = now or datetime.now(KST)
    lines = [
        f"📊 <b>확률 브리핑</b> · {now.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST",
        "",
    ]
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
