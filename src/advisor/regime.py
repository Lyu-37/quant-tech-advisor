"""Market regime detector — risk-on / risk-off classification.

The single most important missing dimension: WHEN should the system be
buying at all? On a -4% VIX-spiking day, screaming '46 buys' is wrong. This
module reads the actual market state and produces a regime that GATES the
recommendation tone.

Signals used:
  - VIX level + 1-day change (fear)
  - SPY position vs SMA50 / SMA200 (trend)
  - Breadth: % of HOT_TECH stocks above their own SMA50 (participation)
  - 10y yield 1-week change (macro pressure)

Regime drives a `buy_gate`:
  risk_on    -> buys pass through normally
  neutral    -> buys pass, but flag caution
  risk_off   -> downgrade aggressive 建仓 to 等企稳; surface what's holding up
"""
from dataclasses import dataclass
from datetime import date
import pandas as pd

from .universe import HOT_TECH
from . import config


@dataclass
class MarketRegime:
    label: str                  # risk_on / neutral / risk_off / 系统性大跌
    score: float                # 0-100, higher = more risk-on
    spy_today: float | None
    qqq_today: float | None
    vix_level: float | None
    vix_change: float | None
    breadth_above_sma50: float | None   # 0-1
    spy_vs_sma50: float | None
    yield_chg_1w: float | None
    buy_gate: str               # "pass" / "caution" / "temper"
    summary: str                # plain-Chinese one-liner
    detail: list                # bullet reasons
    vix_term_ratio: float | None = None   # VIX / VIX3M; >1 = backwardation
    breadth_ratio_60d: float | None = None  # QQQE/QQQ 60d trend


def _today_change(df: pd.DataFrame, as_of: date | None = None) -> float | None:
    """Last-bar % change. If `as_of` is given and this ticker's last bar is
    older (halted / lagging feed), return None instead of presenting an old
    session's move as 'today'."""
    if df is None or df.empty or len(df) < 2:
        return None
    if as_of is not None and df.index[-1].date() < as_of:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)


def compute_breadth(data: dict[str, pd.DataFrame]) -> float | None:
    """% of HOT_TECH stocks trading above their own SMA50."""
    above = total = 0
    for t in HOT_TECH:
        df = data.get(t)
        if df is None or df.empty or len(df) < 50:
            continue
        sma50 = df["close"].rolling(50).mean().iloc[-1]
        if pd.isna(sma50):
            continue
        total += 1
        if df["close"].iloc[-1] > sma50:
            above += 1
    return above / total if total else None


def detect_regime(data: dict[str, pd.DataFrame],
                  as_of: date | None = None) -> MarketRegime:
    spy = data.get("SPY")
    qqq = data.get("QQQ")
    vix = data.get("^VIX")
    vix3m = data.get("^VIX3M")
    qqqe = data.get("QQQE")
    tnx = data.get("^TNX")

    spy_today = _today_change(spy, as_of)
    qqq_today = _today_change(qqq, as_of)
    vix_level = float(vix["close"].iloc[-1]) if vix is not None and not vix.empty else None
    vix_change = _today_change(vix, as_of)

    # VIX term structure: spot/3M. Contango (~0.9) is normal; >1 means the
    # market pays MORE for near-term protection = stress. Catches the
    # slow-grind selloff a spot-level threshold misses for days.
    vix_term_ratio = None
    if (vix_level is not None and vix3m is not None and not vix3m.empty
            and float(vix3m["close"].iloc[-1]) > 0):
        vix_term_ratio = vix_level / float(vix3m["close"].iloc[-1])

    # Equal-weight vs cap-weight breadth (QQQE/QQQ 60d trend) — a breadth
    # read that does not depend on our survivor-biased ticker list.
    breadth_ratio_60d = None
    if (qqqe is not None and qqq is not None
            and len(qqqe) > 60 and len(qqq) > 60):
        ratio_now = float(qqqe["close"].iloc[-1]) / float(qqq["close"].iloc[-1])
        ratio_60 = float(qqqe["close"].iloc[-61]) / float(qqq["close"].iloc[-61])
        if ratio_60 > 0:
            breadth_ratio_60d = ratio_now / ratio_60 - 1

    spy_vs_sma50 = None
    if spy is not None and len(spy) >= 50:
        sma50 = float(spy["close"].rolling(50).mean().iloc[-1])
        spy_vs_sma50 = float(spy["close"].iloc[-1] / sma50 - 1)

    breadth = compute_breadth(data)

    yield_chg_1w = None
    if tnx is not None and len(tnx) >= 6:
        yield_chg_1w = float(tnx["close"].iloc[-1] - tnx["close"].iloc[-6])

    # ----- Score 0-100 (higher = risk-on) -----
    score = 50.0
    detail = []

    # VIX level (biggest weight)
    if vix_level is not None:
        if vix_level >= 30:
            score -= 25; detail.append(f"VIX {vix_level:.0f} 极高 (恐慌)")
        elif vix_level >= 22:
            score -= 15; detail.append(f"VIX {vix_level:.0f} 偏高 (紧张)")
        elif vix_level >= 18:
            score -= 5; detail.append(f"VIX {vix_level:.0f} 中性偏高")
        elif vix_level < 14:
            score += 10; detail.append(f"VIX {vix_level:.0f} 低 (平静)")
        else:
            detail.append(f"VIX {vix_level:.0f} 正常")

    # VIX 1-day spike
    if vix_change is not None and vix_change > 0.20:
        score -= 12; detail.append(f"VIX 单日飙 {vix_change * 100:+.0f}% (恐慌升温)")

    # SPY today move
    if spy_today is not None:
        if spy_today <= -0.02:
            score -= 18; detail.append(f"SPY 今日 {spy_today * 100:+.1f}% (大跌)")
        elif spy_today <= -0.008:
            score -= 8; detail.append(f"SPY 今日 {spy_today * 100:+.1f}%")
        elif spy_today >= 0.008:
            score += 8; detail.append(f"SPY 今日 {spy_today * 100:+.1f}% (上涨)")

    # SPY trend
    if spy_vs_sma50 is not None:
        if spy_vs_sma50 > 0.02:
            score += 8; detail.append(f"SPY 在 SMA50 上方 {spy_vs_sma50 * 100:+.0f}% (趋势健康)")
        elif spy_vs_sma50 < -0.02:
            score -= 12; detail.append(f"SPY 跌破 SMA50 {spy_vs_sma50 * 100:+.0f}% (趋势走弱)")

    # Breadth
    if breadth is not None:
        if breadth >= 0.70:
            score += 10; detail.append(f"{breadth * 100:.0f}% 科技股在 SMA50 上方 (参与广)")
        elif breadth <= 0.35:
            score -= 12; detail.append(f"仅 {breadth * 100:.0f}% 科技股在 SMA50 上方 (普跌)")
        else:
            detail.append(f"{breadth * 100:.0f}% 科技股在 SMA50 上方")

    # Yield pressure
    if yield_chg_1w is not None and yield_chg_1w > 0.15:
        score -= 8; detail.append(f"10y 利率 1 周升 {yield_chg_1w * 100:+.0f}bp (压制成长)")

    # VIX term structure
    term_caution = config.get("regime.term_ratio_caution", 1.00)
    term_crash = config.get("regime.term_ratio_crash", 1.08)
    term_stressed = vix_term_ratio is not None and vix_term_ratio > term_caution
    if term_stressed:
        score -= 12
        detail.append(f"VIX 期限结构倒挂 ({vix_term_ratio:.2f} > {term_caution:.2f}) — "
                      "市场在抢近期保护")

    # Equal-weight breadth
    breadth_warn = config.get("regime.breadth_ratio_warn", -0.03)
    if breadth_ratio_60d is not None and breadth_ratio_60d < breadth_warn:
        score -= 6
        detail.append(f"等权/市值权 60d {breadth_ratio_60d * 100:+.1f}% — 广度收窄, 靠少数权重股撑")

    score = max(0, min(100, score))

    # ----- Classify -----
    crash = (spy_today is not None
             and spy_today <= config.get("regime.crash_spy_pct", -0.02)) or \
            (vix_change is not None
             and vix_change > config.get("regime.crash_vix_chg", 0.25)) or \
            (vix_level is not None
             and vix_level >= config.get("regime.crash_vix_level", 28)) or \
            (vix_term_ratio is not None and vix_term_ratio > term_crash)
    gate_pass = config.get("regime.gate_pass_score", 62)
    gate_caution = config.get("regime.gate_caution_score", 42)
    if crash:
        label = "系统性大跌"
        buy_gate = "temper"
        summary = "今天是 risk-off 大跌 — 不要追买入信号, 等企稳再说"
    elif score >= gate_pass:
        label = "risk_on"
        buy_gate = "pass"
        summary = "风险偏好正常 — 买入信号可正常参考"
    elif score >= gate_caution:
        label = "neutral"
        buy_gate = "caution"
        summary = "市场中性偏谨慎 — 买入信号留意, 分批不梭哈"
    else:
        label = "risk_off"
        buy_gate = "temper"
        summary = "risk-off 环境 — 买入信号打折, 优先看防御 / 抗跌标的"

    # Term-structure backwardation caps the gate at caution even when the
    # score survives — backwardation days are when "buy the dip" hurts most.
    if term_stressed and buy_gate == "pass":
        buy_gate = "caution"
        summary = "分数尚可但 VIX 期限结构倒挂 — 买入降级为谨慎"

    # Fail-safe: if the gate's PRIMARY inputs are missing (VIX or SPY failed
    # to fetch), the score above silently lost its biggest penalty terms and
    # drifts optimistic — on exactly the volatile days Yahoo is most likely
    # to flake. Missing key data must never read as "all clear".
    if vix_level is None or spy_today is None:
        missing = [n for n, v in [("VIX", vix_level), ("SPY", spy_today)] if v is None]
        detail.insert(0, f"关键数据缺失 ({'/'.join(missing)}) — 闸门强制保守")
        if buy_gate == "pass":
            buy_gate = "caution"
            label = "neutral"
            summary = "关键市场数据缺失 — 体制判断不完整, 按谨慎处理"

    return MarketRegime(
        label=label, score=score,
        spy_today=spy_today, qqq_today=qqq_today,
        vix_level=vix_level, vix_change=vix_change,
        breadth_above_sma50=breadth, spy_vs_sma50=spy_vs_sma50,
        yield_chg_1w=yield_chg_1w,
        buy_gate=buy_gate, summary=summary, detail=detail,
        vix_term_ratio=vix_term_ratio,
        breadth_ratio_60d=breadth_ratio_60d,
    )


def find_relative_strength_in_selloff(data: dict[str, pd.DataFrame],
                                      top_n: int = 6,
                                      as_of: date | None = None) -> list[tuple]:
    """On a down day, which stocks are HOLDING UP best (defensive leaders)."""
    moves = []
    seen = set()
    for t in HOT_TECH:
        if t in seen:
            continue
        seen.add(t)
        chg = _today_change(data.get(t), as_of)
        if chg is None:
            continue
        moves.append((t, chg))
    moves.sort(key=lambda x: -x[1])   # best performers first
    return moves[:top_n]


def render_regime_field(regime: MarketRegime,
                        rs_leaders: list[tuple] | None = None) -> dict:
    """Discord embed field for market regime."""
    gate_icon = {"pass": "▲", "caution": "◆", "temper": "▼"}.get(regime.buy_gate, "◆")
    lines = [f"**{gate_icon} 体制: {regime.label}**  (风险偏好分 {regime.score:.0f}/100)"]
    lines.append(f"_{regime.summary}_")
    # Top 3 reasons
    if regime.detail:
        lines.append("  " + "  ·  ".join(regime.detail[:4]))
    # On risk-off, show what's holding up
    if regime.buy_gate == "temper" and rs_leaders:
        held = "  ".join(f"`{t}` {c * 100:+.1f}%" for t, c in rs_leaders[:5])
        lines.append(f"**今日最抗跌**: {held}")
    return {
        "name": "[体制] 市场风险偏好  ·  买入信号闸门",
        "value": "\n".join(lines),
        "inline": False,
    }
