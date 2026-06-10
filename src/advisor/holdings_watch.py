"""Per-holding exit monitoring — the missing mirror of the watchlist.

The watchlist watches names you DON'T own for entries; nothing watched the
names you DO own for exits with the same rigor. Every day, each holding is
checked against its trend stops (SMA50 / SMA200 / 2×ATR) and flagged when an
exit condition fires. Local report only — personal position data never goes
to Discord.
"""
from dataclasses import dataclass
import pandas as pd

from .indicators import distance_to_ma
from .levels import compute_levels
from .universe import Holding


@dataclass
class HoldingAlert:
    ticker: str
    current: float
    pnl_pct: float
    dist_sma50: float | None
    dist_sma200: float | None
    stop_tight: float | None       # current - 2×ATR
    triggered: list[str]           # fired exit conditions, empty = healthy


def evaluate_holdings(holdings: list[Holding],
                      data: dict[str, pd.DataFrame]) -> list[HoldingAlert]:
    out = []
    for h in holdings:
        df = data.get(h.ticker)
        if df is None or df.empty or len(df) < 60:
            continue
        close = df["close"]
        current = float(close.iloc[-1])
        d50 = distance_to_ma(close, 50)
        d200 = distance_to_ma(close, 200) if len(close) >= 200 else None
        L = compute_levels(close, h.ticker)

        triggered = []
        if d50 is not None and not pd.isna(d50) and d50 < 0:
            triggered.append(f"跌破 SMA50 ({d50 * 100:+.1f}%) — 趋势止损检查: "
                             "核心仓考虑减 1/3, 投机仓执行止损")
        if d200 is not None and not pd.isna(d200) and d200 < 0:
            triggered.append(f"跌破 SMA200 ({d200 * 100:+.1f}%) — 长期趋势失效, "
                             "这是最后一道线")
        # 5 日内击穿 2×ATR 紧止损位 (从 5 日前的价格算)
        if L is not None and len(close) >= 6:
            ref = float(close.iloc[-6])
            atr_stop = ref - 2 * L.atr_proxy
            if current < atr_stop:
                triggered.append(f"5 日内跌穿 2×ATR 止损 (${atr_stop:.2f}) — "
                                 "波动超出正常范围, 检查是否有基本面变化")

        out.append(HoldingAlert(
            ticker=h.ticker, current=current,
            pnl_pct=h.pnl_pct,
            dist_sma50=None if d50 is None or pd.isna(d50) else float(d50),
            dist_sma200=None if d200 is None or pd.isna(d200) else float(d200),
            stop_tight=L.stop_tight if L else None,
            triggered=triggered,
        ))
    # Triggered first
    out.sort(key=lambda a: (len(a.triggered) == 0, a.ticker))
    return out


def render_holdings_watch(alerts: list[HoldingAlert]) -> str:
    """Markdown block for the LOCAL report (contains personal P&L)."""
    if not alerts:
        return ""
    lines = ["## 持仓监察 (退出条件盯盘, 本地专用)", "",
             "_watchlist 盯入场, 这里盯退出 — 每天检查每个持仓是否触发了它的离场条件_",
             "",
             "| 持仓 | 现价 | 累计P/L | 距SMA50 | 距SMA200 | 2×ATR止损 | 状态 |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for a in alerts:
        d50 = f"{a.dist_sma50 * 100:+.1f}%" if a.dist_sma50 is not None else "—"
        d200 = f"{a.dist_sma200 * 100:+.1f}%" if a.dist_sma200 is not None else "—"
        stop = f"${a.stop_tight:.2f}" if a.stop_tight else "—"
        status = "**[触发]**" if a.triggered else "正常"
        lines.append(f"| **{a.ticker}** | ${a.current:.2f} "
                     f"| {a.pnl_pct * 100:+.1f}% | {d50} | {d200} "
                     f"| {stop} | {status} |")
    fired = [a for a in alerts if a.triggered]
    if fired:
        lines.append("")
        lines.append("**触发明细:**")
        for a in fired:
            for t in a.triggered:
                lines.append(f"- **{a.ticker}**: {t}")
    lines.append("")
    return "\n".join(lines)
