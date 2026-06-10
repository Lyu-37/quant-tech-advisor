"""Concrete actionable price levels per ticker.

For each ticker we compute reference points a discretionary trader would
look at: stop loss, take profit, support, resistance — all derived from
existing OHLCV so no extra data dependencies.

ATR is approximated from close-to-close volatility because we don't fetch
high/low. This is the std True ATR replacement used by most retail tools.
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class ActionLevels:
    ticker: str
    current: float
    # Support / resistance
    sma20: float
    sma50: float
    sma200: float
    high_52w: float
    low_52w: float
    # ATR-based stops / targets
    atr_proxy: float                # dollar value of "typical daily move"
    stop_tight: float               # current - 2 * ATR (active swing stop)
    stop_loose: float               # current - 3 * ATR (positional stop)
    stop_sma50: float               # below SMA50 (trend-following stop)
    target_1r: float                # current + 1 * ATR
    target_2r: float                # current + 2 * ATR
    # Distance to next resistance level above
    next_resistance: float | None
    next_support: float | None


def compute_levels(close: pd.Series, ticker: str = "") -> ActionLevels | None:
    if len(close) < 50:
        return None
    current = float(close.iloc[-1])
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])
    sma200 = (float(close.rolling(200).mean().iloc[-1])
              if len(close) >= 200 else float("nan"))
    window = close.tail(252) if len(close) >= 252 else close
    high_52w = float(window.max())
    low_52w = float(window.min())

    # ATR proxy from close-to-close vol. NOTE: for a normal r.v. E[|X|]/σ is
    # ~0.8, NOT 1.4 — the 1.4 here is an EMPIRICAL fudge that widens the
    # close-only estimate toward a true ATR (which includes overnight gaps and
    # intraday range we don't fetch). It is an approximation, nothing more.
    rets = close.pct_change().dropna().tail(20)
    atr_proxy = float(current * rets.std() * 1.4)

    next_resistance = None
    candidates_above = [v for v in [sma200, sma50, sma20, high_52w]
                        if v and not pd.isna(v) and v > current]
    if candidates_above:
        next_resistance = min(candidates_above)

    next_support = None
    candidates_below = [v for v in [sma20, sma50, sma200, low_52w]
                        if v and not pd.isna(v) and v < current]
    if candidates_below:
        next_support = max(candidates_below)

    return ActionLevels(
        ticker=ticker,
        current=current,
        sma20=sma20, sma50=sma50, sma200=sma200,
        high_52w=high_52w, low_52w=low_52w,
        atr_proxy=atr_proxy,
        stop_tight=current - 2 * atr_proxy,
        stop_loose=current - 3 * atr_proxy,
        stop_sma50=sma50,
        target_1r=current + 1 * atr_proxy,
        target_2r=current + 2 * atr_proxy,
        next_resistance=next_resistance,
        next_support=next_support,
    )


def render_levels_block(levels: list[ActionLevels]) -> str:
    """Markdown table for local report."""
    if not levels:
        return ""
    lines = ["## 重点标的可操作价位", "",
             "| 标的 | 现价 | SMA50 (硬止损) | 2×ATR 紧止损 | 1×ATR 目标 | 下个支撑 | 下个阻力 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for L in levels:
        sup = f"${L.next_support:.2f}" if L.next_support else "—"
        res = f"${L.next_resistance:.2f}" if L.next_resistance else "**新高**"
        lines.append(
            f"| **{L.ticker}** | ${L.current:.2f} | ${L.sma50:.2f} "
            f"| ${L.stop_tight:.2f} | ${L.target_1r:.2f} | {sup} | {res} |"
        )
    lines.append("")
    return "\n".join(lines)
