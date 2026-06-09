"""Technical indicators + cross-asset metrics.

Conventions:
  - All functions accept a single-ticker OHLCV DataFrame (lowercase columns)
  - Returns scalars or short Series — keep memory light
  - No look-ahead: anything labeled '_today' uses the last bar only, and
    everything assumes the user is acting on the next session's open.
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window).mean()


def realized_vol(close: pd.Series, window: int = 20) -> float:
    """Annualized realized vol over the last `window` days."""
    rets = close.pct_change().dropna()
    if len(rets) < window:
        return float("nan")
    return float(rets.tail(window).std() * np.sqrt(TRADING_DAYS))


def momentum(close: pd.Series, window: int) -> float:
    """Total return over the last `window` trading days."""
    if len(close) <= window:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-window - 1] - 1)


def momentum_12_1(close: pd.Series) -> float:
    """12-1 momentum (Jegadeesh-Titman 1993, academic standard).

    Returns the 12-month return excluding the most recent month — skips the
    1-month short-term reversal effect that contaminates raw 12-month return.
    This is the single most-cited cross-sectional momentum signal in finance.

    Formula: (price_at_t-21 / price_at_t-252) - 1
    """
    if len(close) < 252:
        return float("nan")
    p_recent = float(close.iloc[-21])     # ~1 month ago (skip last month)
    p_year_ago = float(close.iloc[-252])  # ~12 months ago
    if p_year_ago == 0:
        return float("nan")
    return p_recent / p_year_ago - 1


def momentum_12_1_score(close: pd.Series) -> dict:
    """Score 0-10 based on rolling percentile of 12-1 momentum.

    Compares current 12-1 momentum to the distribution of 12-1 momentums
    over the trailing ~1 year of rolling values.
    """
    if len(close) < 504:
        # Need 2 years of data for meaningful percentile
        current = momentum_12_1(close)
        if pd.isna(current):
            return {"score": 5.0, "label": "insufficient", "value": None}
        # Without percentile context, just convert raw 12-1 momentum to score
        # +30% over 12m = full bullish, -30% = full bearish
        score = float(np.clip(5 + current / 0.06, 0, 10))
        return {"score": score, "label": "no_history", "value": current}

    # Rolling 12-1 every 5 trading days
    rolling_values = []
    for i in range(252, len(close), 5):
        if i - 21 < 0 or i - 252 < 0:
            continue
        p_recent = float(close.iloc[i - 21])
        p_year_ago = float(close.iloc[i - 252])
        if p_year_ago > 0:
            rolling_values.append(p_recent / p_year_ago - 1)

    current = momentum_12_1(close)
    if pd.isna(current) or not rolling_values:
        return {"score": 5.0, "label": "insufficient", "value": None}

    percentile = sum(1 for r in rolling_values if r < current) / len(rolling_values)
    score = percentile * 10
    label = "strong" if score >= 7 else "weak" if score <= 3 else "neutral"
    return {
        "score": float(score),
        "label": label,
        "value": float(current),
        "percentile": float(percentile),
    }


def drawdown_from_52w_high(close: pd.Series) -> float:
    """Current price relative to trailing 252-day max. Negative or zero."""
    if len(close) < 2:
        return 0.0
    window = min(252, len(close))
    high = close.tail(window).max()
    return float(close.iloc[-1] / high - 1)


def distance_to_ma(close: pd.Series, window: int) -> float:
    """(price - MA) / MA. Positive = above MA."""
    ma = close.rolling(window).mean()
    if pd.isna(ma.iloc[-1]):
        return float("nan")
    return float(close.iloc[-1] / ma.iloc[-1] - 1)


def trend_alignment(close: pd.Series) -> dict:
    """Classify trend by SMA stack ordering.

    Bull stack:  price > SMA20 > SMA50 > SMA200
    Bear stack:  price < SMA20 < SMA50 < SMA200
    """
    if len(close) < 200:
        return {"label": "insufficient_data", "score": 5.0}
    p = close.iloc[-1]
    s20 = close.rolling(20).mean().iloc[-1]
    s50 = close.rolling(50).mean().iloc[-1]
    s200 = close.rolling(200).mean().iloc[-1]
    if p > s20 > s50 > s200:
        return {"label": "strong_bull", "score": 9.0,
                "detail": f"price>{s20:.2f}>{s50:.2f}>{s200:.2f}"}
    if p > s50 > s200:
        return {"label": "bull", "score": 7.0,
                "detail": f"price>SMA50({s50:.2f})>SMA200({s200:.2f})"}
    if p > s200:
        return {"label": "weak_bull", "score": 5.5,
                "detail": f"price>SMA200({s200:.2f}) but mid-MAs mixed"}
    if p < s20 < s50 < s200:
        return {"label": "strong_bear", "score": 1.0,
                "detail": f"price<{s20:.2f}<{s50:.2f}<{s200:.2f}"}
    if p < s50 and p < s200:
        return {"label": "bear", "score": 3.0,
                "detail": f"price<SMA50({s50:.2f}), <SMA200({s200:.2f})"}
    return {"label": "mixed", "score": 5.0,
            "detail": f"p={p:.2f} s20={s20:.2f} s50={s50:.2f} s200={s200:.2f}"}


def momentum_score(close: pd.Series) -> dict:
    """Score 0-10 based on percentile of 20d momentum in 252d history."""
    rets_20d = close.pct_change(20).dropna()
    if len(rets_20d) < 60:
        return {"score": 5.0, "label": "insufficient_data"}
    current = rets_20d.iloc[-1]
    pct = (rets_20d < current).mean()  # percentile in own history
    score = pct * 10
    label = "strong" if score >= 7 else "weak" if score <= 3 else "neutral"
    return {"score": float(score), "label": label,
            "value_pct": float(current), "percentile": float(pct)}


def relative_strength(asset: pd.Series, benchmark: pd.Series, window: int = 60) -> dict:
    """Asset vs benchmark over `window` days. score 0-10."""
    if len(asset) < window or len(benchmark) < window:
        return {"score": 5.0, "label": "insufficient"}
    a_ret = asset.iloc[-1] / asset.iloc[-window - 1] - 1
    b_ret = benchmark.iloc[-1] / benchmark.iloc[-window - 1] - 1
    spread = a_ret - b_ret
    # Score: each 5% outperformance = +1 point, capped 0-10
    score = float(np.clip(5 + spread / 0.05, 0, 10))
    label = "outperforming" if spread > 0.02 else "underperforming" if spread < -0.02 else "in_line"
    return {"score": score, "label": label,
            "asset_ret": float(a_ret), "bench_ret": float(b_ret),
            "spread": float(spread)}


def vol_regime_score(close: pd.Series, vix: float | None = None) -> dict:
    """Lower realized vol + lower VIX => higher score (calmer regime)."""
    rv = realized_vol(close, window=20)
    # SMH/SOXX historical baseline: ~25% annualized
    # Below 20%: very calm; above 35%: stressed
    if pd.isna(rv):
        rv_score = 5.0
    else:
        rv_score = float(np.clip(10 - (rv - 0.18) / 0.025, 0, 10))

    if vix is None or pd.isna(vix):
        vix_score = 5.0
    else:
        # VIX < 15: calm, > 25: stress
        vix_score = float(np.clip(10 - (vix - 12) / 1.8, 0, 10))

    combined = 0.6 * rv_score + 0.4 * vix_score
    label = "calm" if combined >= 7 else "stressed" if combined <= 3 else "normal"
    return {"score": combined, "label": label,
            "realized_vol_20d": float(rv) if not pd.isna(rv) else None,
            "vix": float(vix) if vix and not pd.isna(vix) else None}


def macro_pressure_score(tnx_df: pd.DataFrame, dxy_df: pd.DataFrame) -> dict:
    """Macro hostility to high-growth tech. Lower 10y + lower DXY => higher score."""
    sub = {}

    if tnx_df is not None and not tnx_df.empty:
        # yfinance returns ^TNX as actual percentage (4.36 = 4.36%)
        tnx_pct = float(tnx_df["close"].iloc[-1])
        tnx_60d_ago = float(tnx_df["close"].iloc[-60]) if len(tnx_df) > 60 else tnx_pct
        delta = tnx_pct - tnx_60d_ago
        # Score: 3% rate -> 10, 5% rate -> 0
        level_score = float(np.clip(10 - (tnx_pct - 3.0) / 0.2, 0, 10))
        # Penalize rising
        direction_penalty = float(np.clip(delta / 0.1, -3, 3))
        tnx_score = float(np.clip(level_score - direction_penalty, 0, 10))
        sub["tnx_pct"] = tnx_pct
        sub["tnx_delta_60d"] = delta
        sub["tnx_score"] = tnx_score
    else:
        sub["tnx_score"] = 5.0

    if dxy_df is not None and not dxy_df.empty:
        dxy_now = float(dxy_df["close"].iloc[-1])
        dxy_60d_ago = float(dxy_df["close"].iloc[-60]) if len(dxy_df) > 60 else dxy_now
        delta = dxy_now / dxy_60d_ago - 1
        # DXY rising hurts US multinationals' foreign earnings
        dxy_score = float(np.clip(5 - delta * 50, 0, 10))
        sub["dxy"] = dxy_now
        sub["dxy_change_60d"] = delta
        sub["dxy_score"] = dxy_score
    else:
        sub["dxy_score"] = 5.0

    combined = 0.7 * sub["tnx_score"] + 0.3 * sub["dxy_score"]
    label = "supportive" if combined >= 7 else "hostile" if combined <= 3 else "neutral"
    return {"score": float(combined), "label": label, **sub}


def stretch_flag(dist_sma50: float, dist_sma200: float) -> dict:
    """Flag when price is statistically far from key MAs.

    Mean-reversion risk rises sharply when a stock is more than ~30%
    above SMA50 or ~50% above SMA200 — these are 2+ sigma events
    for most equities and typically resolve via correction or sideways.
    """
    severity = 0
    notes = []
    if pd.isna(dist_sma50) or pd.isna(dist_sma200):
        return {"severity": 0, "level": "unknown", "notes": ["insufficient_data"]}
    if dist_sma50 > 0.30:
        severity += 1
        notes.append(f"距 SMA50 +{dist_sma50 * 100:.0f}% (>30%, 历史高位)")
    if dist_sma50 > 0.50:
        severity += 1
        notes.append("距 SMA50 >50% — 极端拉伸, 大概率回踩")
    if dist_sma200 > 0.50:
        severity += 1
        notes.append(f"距 SMA200 +{dist_sma200 * 100:.0f}% (>50%, 历史高位)")
    if dist_sma200 > 1.00:
        severity += 1
        notes.append("距 SMA200 >100% — 罕见拉伸, 抛物线尾段特征")
    level = "极端拉伸" if severity >= 3 else "偏拉伸" if severity >= 1 else "正常"
    return {"severity": severity, "level": level, "notes": notes}


def summarize_ticker(close: pd.Series) -> dict:
    """One-shot summary for any single ticker."""
    ds50 = distance_to_ma(close, 50)
    ds200 = distance_to_ma(close, 200)
    return {
        "last": float(close.iloc[-1]),
        "trend": trend_alignment(close),
        "momentum": momentum_score(close),
        "momentum_12_1": momentum_12_1_score(close),  # NEW: Jegadeesh-Titman
        "ret_5d": momentum(close, 5),
        "ret_20d": momentum(close, 20),
        "ret_60d": momentum(close, 60),
        "ret_126d": momentum(close, 126),  # NEW: 6-month for sector rotation
        "ret_252d": momentum(close, 252),
        "dd_from_52w_high": drawdown_from_52w_high(close),
        "realized_vol_20d": realized_vol(close, 20),
        "dist_sma20": distance_to_ma(close, 20),
        "dist_sma50": ds50,
        "dist_sma200": ds200,
        "stretch": stretch_flag(ds50, ds200),
    }
