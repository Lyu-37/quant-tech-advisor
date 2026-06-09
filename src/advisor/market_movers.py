"""Today's market intelligence: gainers, losers, level breaks.

Surfaces what actually happened TODAY across the HOT_TECH universe — the
high-frequency "news" that justifies checking every day. Separates:

  - Gainers / losers (top moves)
  - Level breaks (crossed SMA50 or SMA200 today)
  - New 52w highs / lows
  - Significant unusual moves (>2 sigma)
"""
from dataclasses import dataclass, field
import pandas as pd

from .universe import HOT_TECH, categorize_hot_tech


@dataclass
class DailyMover:
    ticker: str
    today_pct: float
    today_dollar: float        # USD price change
    category: str


@dataclass
class LevelBreak:
    ticker: str
    kind: str                   # "上穿 SMA50", "跌破 SMA200", "新 52w 高", etc.
    price: float
    category: str


@dataclass
class MarketMovers:
    gainers: list[DailyMover] = field(default_factory=list)
    losers: list[DailyMover] = field(default_factory=list)
    level_breaks: list[LevelBreak] = field(default_factory=list)
    new_52w_highs: list[str] = field(default_factory=list)
    new_52w_lows: list[str] = field(default_factory=list)


def compute_today_movers(data: dict[str, pd.DataFrame],
                          min_sigma: float = 2.0) -> MarketMovers:
    """Scan all HOT_TECH tickers for today's biggest moves + level breaks."""
    result = MarketMovers()

    all_moves = []
    for t in HOT_TECH:
        df = data.get(t)
        if df is None or df.empty or len(df) < 51:
            continue
        today = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])
        if prev <= 0:
            continue
        today_pct = today / prev - 1
        today_dollar = today - prev

        all_moves.append(DailyMover(
            ticker=t, today_pct=today_pct,
            today_dollar=today_dollar,
            category=categorize_hot_tech(t),
        ))

        # ----- Level breaks (crossed today) -----
        sma50_today = float(df["close"].rolling(50).mean().iloc[-1])
        sma50_prev = float(df["close"].rolling(50).mean().iloc[-2])
        if len(df) >= 200:
            sma200_today = float(df["close"].rolling(200).mean().iloc[-1])
            sma200_prev = float(df["close"].rolling(200).mean().iloc[-2])
        else:
            sma200_today = sma200_prev = None

        cat = categorize_hot_tech(t)

        # SMA50 cross
        if prev < sma50_prev and today > sma50_today:
            result.level_breaks.append(LevelBreak(
                ticker=t, kind="上穿 SMA50", price=today, category=cat
            ))
        elif prev > sma50_prev and today < sma50_today:
            result.level_breaks.append(LevelBreak(
                ticker=t, kind="跌破 SMA50", price=today, category=cat
            ))

        # SMA200 cross (more important)
        if sma200_today is not None:
            if prev < sma200_prev and today > sma200_today:
                result.level_breaks.append(LevelBreak(
                    ticker=t, kind="上穿 SMA200 ★", price=today, category=cat
                ))
            elif prev > sma200_prev and today < sma200_today:
                result.level_breaks.append(LevelBreak(
                    ticker=t, kind="跌破 SMA200 ★", price=today, category=cat
                ))

        # 52w high/low
        if len(df) >= 252:
            high_52w = float(df["close"].tail(252).max())
            low_52w = float(df["close"].tail(252).min())
            if abs(today - high_52w) / high_52w < 0.001:  # within 0.1% of 52w high
                result.new_52w_highs.append(t)
            elif abs(today - low_52w) / low_52w < 0.001:
                result.new_52w_lows.append(t)

    # Sort gainers / losers
    all_moves.sort(key=lambda m: -m.today_pct)
    result.gainers = all_moves[:5]
    result.losers = list(reversed(all_moves[-5:]))

    return result


def render_movers_field(movers: MarketMovers) -> dict | None:
    """Build Discord embed field for today's gainers/losers + level breaks."""
    if not movers.gainers and not movers.level_breaks:
        return None

    lines = []

    # Top 3 gainers / losers
    if movers.gainers:
        gain_line = "  ·  ".join(
            f"▲ `{m.ticker}` **{m.today_pct * 100:+.1f}%**"
            for m in movers.gainers[:5]
        )
        lines.append(f"**今日涨幅 Top 5**:  {gain_line}")

    if movers.losers:
        loss_line = "  ·  ".join(
            f"▼ `{m.ticker}` **{m.today_pct * 100:+.1f}%**"
            for m in movers.losers[:5]
        )
        lines.append(f"**今日跌幅 Top 5**:  {loss_line}")

    # Level breaks (most actionable signals)
    if movers.level_breaks:
        # Group by kind
        breaks_by_kind = {}
        for lb in movers.level_breaks:
            breaks_by_kind.setdefault(lb.kind, []).append(lb.ticker)
        lines.append("")  # blank line
        lines.append("**关键技术位破位** (今日):")
        # Show SMA200 breaks first (most important)
        order = ["上穿 SMA200 ★", "跌破 SMA200 ★",
                 "上穿 SMA50", "跌破 SMA50"]
        for kind in order:
            if kind in breaks_by_kind:
                arrow = "↑" if "上穿" in kind else "↓"
                tickers = "  ".join(f"`{t}`" for t in breaks_by_kind[kind][:6])
                lines.append(f"  {arrow} {kind}:  {tickers}")

    # New 52w highs / lows
    extras = []
    if movers.new_52w_highs:
        tickers = "  ".join(f"`{t}`" for t in movers.new_52w_highs[:6])
        extras.append(f"  ★ 新 52w 高:  {tickers}")
    if movers.new_52w_lows:
        tickers = "  ".join(f"`{t}`" for t in movers.new_52w_lows[:6])
        extras.append(f"  ✗ 新 52w 低:  {tickers}")
    if extras:
        if not movers.level_breaks:
            lines.append("")
        lines.extend(extras)

    return {
        "name": "[今日异动] 实际涨跌  ·  关键位破位  ·  52w 极值",
        "value": "\n".join(lines),
        "inline": False,
    }
