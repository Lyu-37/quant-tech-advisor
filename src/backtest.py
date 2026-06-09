"""Vectorized backtest engine.

Assumes positions are already lookahead-free (position[t] decided at t-1).
"""
import pandas as pd


def run_backtest(
    df: pd.DataFrame,
    commission_bps: float = 5.0,
    slippage_bps: float = 2.0,
) -> pd.DataFrame:
    """Compute PnL given OHLCV + position columns.

    Args:
        df: must contain 'close' and 'position'
        commission_bps: round-trip commission in basis points (1 bp = 0.01%)
        slippage_bps:   estimated market impact per side, basis points

    Returns:
        DataFrame with added columns:
            returns          — market daily return
            strategy_returns — gross strategy return
            trades           — |Δposition|, marks rebalance events
            costs            — transaction cost on rebalance days
            net_returns      — strategy_returns - costs
            equity           — cumulative net equity (starts at 1)
    """
    assert "close" in df.columns and "position" in df.columns

    out = df.copy()

    # Daily market returns
    out["returns"] = out["close"].pct_change().fillna(0)

    # Strategy gross return: position held during the day * day's return
    out["strategy_returns"] = out["position"] * out["returns"]

    # Cost only on days where position changes
    out["trades"] = out["position"].diff().abs().fillna(0)
    cost_per_unit = (commission_bps + slippage_bps) / 10_000.0
    out["costs"] = out["trades"] * cost_per_unit

    out["net_returns"] = out["strategy_returns"] - out["costs"]
    out["equity"] = (1 + out["net_returns"]).cumprod()

    return out
