"""Academic factors: Quality (QMJ-inspired) + PEAD (post-earnings drift).

References:
  - Asness, Frazzini, Pedersen (2019), "Quality Minus Junk", Review of Accounting Studies.
    QMJ = Profitability + Growth + Safety + Payout.
  - Bernard & Thomas (1989), "Post-Earnings-Announcement Drift". Stocks that beat
    earnings drift up for ~60 days; stocks that miss drift down.

Both factors pull fundamentals from yfinance.Ticker.info / .earnings_dates.
ETF and index symbols return None for these signals.
"""
from contextlib import redirect_stderr
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from typing import Optional
import pandas as pd
import yfinance as yf


NON_FUNDAMENTAL_SYMBOLS = {
    "SMH", "SOXX", "SOXL", "QQQ", "XLK", "TQQQ", "TECL", "SPY",
    "VDY.TO", "^TNX", "^VIX", "DX-Y.NYB", "QTUM", "CHPS.TO",
}


@dataclass
class QualityFactor:
    """QMJ-style quality score (0-10, higher = higher quality)."""
    ticker: str
    profit_margin: Optional[float] = None
    roe: Optional[float] = None
    earnings_growth_qoq: Optional[float] = None
    debt_to_equity: Optional[float] = None
    forward_pe: Optional[float] = None

    profitability_score: float = 5.0  # 0-10
    safety_score: float = 5.0          # 0-10
    composite_score: float = 5.0       # 0-10
    label: str = "neutral"


def compute_quality(ticker: str) -> QualityFactor | None:
    """Pull yfinance fundamentals; compute QMJ-inspired quality score.

    Returns None for ETFs/indices (no fundamentals).
    """
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    try:
        with redirect_stderr(StringIO()):
            info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    if not info or info.get("trailingPE") is None and info.get("profitMargins") is None:
        return None

    q = QualityFactor(ticker=ticker)
    q.profit_margin     = _as_float(info.get("profitMargins"))
    q.roe               = _as_float(info.get("returnOnEquity"))
    q.earnings_growth_qoq = _as_float(info.get("earningsQuarterlyGrowth"))
    q.debt_to_equity    = _as_float(info.get("debtToEquity"))
    q.forward_pe        = _as_float(info.get("forwardPE"))

    # Sanitize: yfinance's `profitMargins` is sometimes the latest quarter
    # which can be distorted by one-time gains (e.g. IONQ reporting 175%).
    # Cap at "no real company can do this" thresholds:
    #   - profit_margin > 100% = impossible (revenue includes the gain too)
    #   - ROE > 200% = usually buyback-distorted equity, but legit for AAPL etc.
    # Keep NVDA (margin 56%, ROE 101%) and AAPL (ROE ~150%) intact.
    if q.profit_margin is not None and (q.profit_margin > 1.00
                                         or q.profit_margin < -2.00):
        q.profit_margin = None
    if q.roe is not None and abs(q.roe) > 2.50:
        q.roe = None
    if q.earnings_growth_qoq is not None and abs(q.earnings_growth_qoq) > 5.0:
        q.earnings_growth_qoq = None

    # Profitability sub-score (0-10): blend of margin + ROE + earnings growth
    margin_pts = _clip_score(q.profit_margin, lo=0.00, hi=0.30)   # 0% = 0, 30%+ = 10
    roe_pts    = _clip_score(q.roe, lo=0.00, hi=0.30)             # 0% = 0, 30%+ = 10
    growth_pts = _clip_score(q.earnings_growth_qoq, lo=-0.20, hi=0.40)  # -20% to +40%

    profitability_score = (margin_pts + roe_pts + growth_pts) / 3
    q.profitability_score = float(profitability_score)

    # Safety sub-score (0-10): lower debt = safer
    # yfinance debtToEquity is often percentage (e.g. 50 = 50%, sometimes ratio)
    de = q.debt_to_equity
    if de is None:
        safety_pts = 5.0
    else:
        # Normalize: large numbers are percentages, small are ratios
        de_ratio = de / 100 if abs(de) > 5 else de
        # D/E < 0.5 = excellent (10), > 3 = poor (0)
        safety_pts = float(max(0, min(10, 10 - de_ratio * 3)))
    q.safety_score = safety_pts

    # Composite: 60% profitability + 40% safety (Asness weighting)
    q.composite_score = float(profitability_score * 0.60 + safety_pts * 0.40)

    if q.composite_score >= 7:
        q.label = "高质量"
    elif q.composite_score >= 5:
        q.label = "中等"
    elif q.composite_score >= 3:
        q.label = "偏低"
    else:
        q.label = "差"

    return q


@dataclass
class AnalystConsensus:
    """Wall Street consensus: rating + price target + upside."""
    ticker: str
    rating_mean: float | None = None      # 1=Strong Buy ... 5=Strong Sell
    rating_label: str = ""
    num_analysts: int | None = None
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    current: float | None = None
    upside_pct: float | None = None       # to mean target
    flag: str = ""                        # "远超目标价" / "深度低于目标" / ""


def compute_analyst(ticker: str, current_price: float | None = None) -> AnalystConsensus | None:
    """Pull analyst consensus from yfinance. None for ETFs/no coverage."""
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    try:
        with redirect_stderr(StringIO()):
            info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    if not info:
        return None

    a = AnalystConsensus(ticker=ticker)
    a.rating_mean   = _as_float(info.get("recommendationMean"))
    a.num_analysts  = info.get("numberOfAnalystOpinions")
    a.target_mean   = _as_float(info.get("targetMeanPrice"))
    a.target_high   = _as_float(info.get("targetHighPrice"))
    a.target_low    = _as_float(info.get("targetLowPrice"))
    a.current       = current_price or _as_float(info.get("currentPrice"))

    # No useful data
    if a.target_mean is None and a.rating_mean is None:
        return None

    # Rating label
    if a.rating_mean is not None:
        if a.rating_mean <= 1.5:
            a.rating_label = "强力买入"
        elif a.rating_mean <= 2.3:
            a.rating_label = "买入"
        elif a.rating_mean <= 2.7:
            a.rating_label = "增持"
        elif a.rating_mean <= 3.4:
            a.rating_label = "持有"
        else:
            a.rating_label = "减持/卖出"

    # Upside to target
    if a.target_mean is not None and a.current and a.current > 0:
        a.upside_pct = a.target_mean / a.current - 1
        if a.upside_pct < -0.10:
            a.flag = "远超目标价 (贵)"     # trading well ABOVE target
        elif a.upside_pct > 0.25:
            a.flag = "深度低于目标 (机会或有雷)"

    return a


def render_analyst_field(analyst_data: dict) -> dict | None:
    """Discord embed field — analyst targets + upside, sorted by upside."""
    if not analyst_data:
        return None
    items = [a for a in analyst_data.values()
             if a.target_mean is not None and a.upside_pct is not None]
    if not items:
        return None
    items.sort(key=lambda a: -(a.upside_pct or 0))

    rows = []
    for a in items[:10]:
        arrow = "▲" if a.upside_pct > 0.05 else "▼" if a.upside_pct < -0.05 else "─"
        flag = ""
        if a.upside_pct < -0.10:
            flag = " 贵"
        elif a.upside_pct > 0.30:
            flag = " 空间大"
        rows.append(
            f"{arrow} `{a.ticker}` ${a.current:.0f}→${a.target_mean:.0f} "
            f"(**{a.upside_pct * 100:+.0f}%**){flag}"
        )
    return {
        "name": "[分析师] 华尔街目标价  ·  距目标空间",
        "value": "\n".join(rows),
        "inline": False,
    }


@dataclass
class PEADSignal:
    """Post-earnings-announcement drift status."""
    ticker: str
    in_drift_window: bool = False
    days_since_earnings: int = 0
    earnings_date: Optional[date] = None
    drift_since_earnings: Optional[float] = None
    direction: str = "neutral"     # positive / negative / neutral
    label: str = "no_recent_earnings"


def compute_pead(ticker: str, close: pd.Series, window_days: int = 60) -> PEADSignal | None:
    """Detect post-earnings drift within `window_days` of last earnings.

    Returns the cumulative price change since the most recent past earnings
    date (if within window). Per Bernard-Thomas, this drift tends to continue:
      drift > +5% = positive PEAD (continue bullish)
      drift < -5% = negative PEAD (continue bearish)
      |drift| < 5% = neutral
    """
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    sig = PEADSignal(ticker=ticker)
    try:
        with redirect_stderr(StringIO()):
            t = yf.Ticker(ticker)
            df = t.earnings_dates
    except Exception:
        return sig
    if df is None or df.empty:
        return sig

    today = date.today()
    cutoff = today - timedelta(days=window_days)

    # earnings_dates DF has DatetimeIndex (often tz-aware)
    past_earnings = []
    for ts in df.index:
        try:
            d = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        except (ValueError, TypeError):
            continue
        if cutoff <= d <= today:
            past_earnings.append(d)

    if not past_earnings:
        return sig
    most_recent = max(past_earnings)
    days_since = (today - most_recent).days

    # Find close price at most_recent (or nearest trading day)
    try:
        idx = close.index.get_indexer([pd.Timestamp(most_recent)], method="nearest")[0]
    except (KeyError, IndexError):
        return sig
    if idx < 0 or idx >= len(close):
        return sig

    price_at_earnings = float(close.iloc[idx])
    if price_at_earnings == 0:
        return sig
    drift = float(close.iloc[-1] / price_at_earnings - 1)

    sig.in_drift_window = True
    sig.days_since_earnings = days_since
    sig.earnings_date = most_recent
    sig.drift_since_earnings = drift
    if drift > 0.05:
        sig.direction = "positive"
        sig.label = "财报后正向漂移"
    elif drift < -0.05:
        sig.direction = "negative"
        sig.label = "财报后负向漂移"
    else:
        sig.direction = "neutral"
        sig.label = "财报后横盘"
    return sig


# ---------- helpers ----------

def _as_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clip_score(value: Optional[float], lo: float, hi: float) -> float:
    """Map `value` from [lo, hi] linearly to [0, 10]. None → 5."""
    if value is None:
        return 5.0
    if value <= lo:
        return 0.0
    if value >= hi:
        return 10.0
    return (value - lo) / (hi - lo) * 10
