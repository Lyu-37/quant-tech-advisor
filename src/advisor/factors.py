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


# Per-run shared .info cache. quality / valuation / analyst / guru screens all
# read the same snapshot — one HTTP call per ticker per run instead of 4-5,
# and no risk of the four consumers seeing different data mid-run.
_INFO_CACHE: dict[str, dict | None] = {}


def get_info(ticker: str) -> dict | None:
    """Fetch (once per run) and cache yfinance .info for a ticker."""
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    if ticker in _INFO_CACHE:
        return _INFO_CACHE[ticker]
    try:
        with redirect_stderr(StringIO()):
            info = yf.Ticker(ticker).info or {}
    except Exception:
        info = None
    _INFO_CACHE[ticker] = info if info else None
    return _INFO_CACHE[ticker]


def clear_info_cache() -> None:
    _INFO_CACHE.clear()


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


def compute_quality(ticker: str, info: dict | None = None) -> QualityFactor | None:
    """Pull yfinance fundamentals; compute QMJ-inspired quality score.

    Returns None for ETFs/indices (no fundamentals).
    `info` is injectable for tests; defaults to the shared per-run cache.
    """
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    if info is None:
        info = get_info(ticker)
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
    # yfinance .info debtToEquity is ALWAYS percent form (verified live:
    # NVDA=6.55 means 6.55%, ARM=5.93, MU=14.9). Never a ratio — divide
    # unconditionally. (The old ">5 means percent" heuristic had a cliff at 5
    # that would zero out the safety score of near-debt-free companies.)
    de = q.debt_to_equity
    if de is None:
        safety_pts = 5.0
    else:
        de_ratio = de / 100.0
        # D/E < 0.5 = excellent (10), > 3 = poor (0)
        safety_pts = float(max(0, min(10, 10 - de_ratio * 3)))
    q.safety_score = safety_pts

    # Composite: 60% profitability + 40% safety. NOTE: this weighting is our
    # own choice — the QMJ paper z-scores components equal-weight. Calling it
    # "QMJ-inspired" is honest; calling it QMJ would not be.
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


def compute_analyst(ticker: str, current_price: float | None = None,
                    info: dict | None = None) -> AnalystConsensus | None:
    """Pull analyst consensus from yfinance. None for ETFs/no coverage."""
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    if info is None:
        info = get_info(ticker)
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
    """Post-earnings price-momentum status.

    Honest framing: this is NOT textbook Bernard-Thomas PEAD (that conditions
    on standardized earnings surprise). It measures price drift AFTER the
    announcement reaction, plus the reported surprise sign when available.
    """
    ticker: str
    in_drift_window: bool = False
    days_since_earnings: int = 0
    earnings_date: Optional[date] = None
    drift_since_earnings: Optional[float] = None
    surprise_pct: Optional[float] = None   # reported EPS surprise %, if known
    direction: str = "neutral"     # positive / negative / neutral
    label: str = "no_recent_earnings"


def compute_pead(ticker: str, close: pd.Series, window_days: int = 60,
                 earnings_df: pd.DataFrame | None = None) -> PEADSignal | None:
    """Detect post-earnings drift within `window_days` of last earnings.

    Drift is anchored to the FIRST close strictly AFTER the announcement date,
    so the announcement-day gap (which you cannot trade) is excluded. The old
    nearest-close anchor counted an AMC gap as "drift" — a stock that gapped
    +12% and went flat showed "+12% drift" you could never capture.

      drift > +5% = positive (post-earnings momentum continues, B-T 1989)
      drift < -5% = negative
      |drift| < 5% = neutral

    `earnings_df` is injectable for tests; defaults to yfinance earnings_dates.
    """
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    sig = PEADSignal(ticker=ticker)
    df = earnings_df
    if df is None:
        try:
            with redirect_stderr(StringIO()):
                df = yf.Ticker(ticker).earnings_dates
        except Exception:
            return sig
    if df is None or df.empty:
        return sig

    today = date.today()
    cutoff = today - timedelta(days=window_days)

    # earnings_dates DF has DatetimeIndex (often tz-aware); may carry a
    # 'Surprise(%)' column with the reported EPS surprise.
    past_earnings: list[tuple[date, Optional[float]]] = []
    surprise_col = next((c for c in df.columns if "surprise" in str(c).lower()), None)
    for ts in df.index:
        try:
            d = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        except (ValueError, TypeError):
            continue
        if cutoff <= d <= today:
            sp = None
            if surprise_col is not None:
                sp = _as_float(df.loc[ts, surprise_col])
            past_earnings.append((d, sp))

    if not past_earnings:
        return sig
    most_recent, surprise = max(past_earnings, key=lambda x: x[0])
    days_since = (today - most_recent).days

    # Anchor: first close strictly AFTER the announcement date. Correct for
    # AMC reports (reaction is next session); conservative for BMO reports
    # (also skips announcement day — underclaims rather than overclaims).
    anchor_idx = int(close.index.searchsorted(pd.Timestamp(most_recent), side="right"))
    if anchor_idx >= len(close):
        return sig          # no post-announcement bar yet (reported today AMC)

    anchor_price = float(close.iloc[anchor_idx])
    if anchor_price == 0:
        return sig
    drift = float(close.iloc[-1] / anchor_price - 1)

    sig.in_drift_window = True
    sig.days_since_earnings = days_since
    sig.earnings_date = most_recent
    sig.drift_since_earnings = drift
    sig.surprise_pct = surprise
    if drift > 0.05:
        sig.direction = "positive"
        sig.label = "财报后正向动量"
    elif drift < -0.05:
        sig.direction = "negative"
        sig.label = "财报后负向动量"
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
