"""Performance metrics."""
import numpy as np
import pandas as pd

TRADING_DAYS = 252  # standard convention for US equities


def annualized_return(returns: pd.Series) -> float:
    """CAGR from a daily-return series."""
    if len(returns) == 0:
        return 0.0
    total = (1 + returns).prod()
    years = len(returns) / TRADING_DAYS
    if years <= 0 or total <= 0:
        return 0.0
    return total ** (1 / years) - 1


def annualized_vol(returns: pd.Series) -> float:
    """Annualized std dev of daily returns."""
    return float(returns.std() * np.sqrt(TRADING_DAYS))


def sharpe(returns: pd.Series, rf_annual: float = 0.04) -> float:
    """Sharpe ratio.

    Args:
        returns: daily returns of the strategy
        rf_annual: annualized risk-free rate (default 4%, typical for current
                   US 10y Treasury territory; you can adjust)
    """
    if len(returns) == 0 or returns.std() == 0:
        return 0.0
    daily_rf = rf_annual / TRADING_DAYS
    excess = returns - daily_rf
    return float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS))


def max_drawdown(equity: pd.Series) -> float:
    """Max drawdown as a negative fraction (e.g. -0.30 = -30%)."""
    if len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def win_rate(strategy_returns: pd.Series) -> float:
    """Fraction of in-market days that were positive."""
    active = strategy_returns[strategy_returns != 0]
    if len(active) == 0:
        return 0.0
    return float((active > 0).sum() / len(active))


def summarize(df: pd.DataFrame) -> dict:
    """Aggregate all metrics from a backtest output DataFrame."""
    r = df["net_returns"]
    eq = df["equity"]
    return {
        "ann_return":    annualized_return(r),
        "ann_vol":       annualized_vol(r),
        "sharpe":        sharpe(r),
        "max_drawdown":  max_drawdown(eq),
        "win_rate":      win_rate(df["strategy_returns"]),
        "n_trades":      int(df["trades"].sum()),
        "final_equity":  float(eq.iloc[-1]),
    }
