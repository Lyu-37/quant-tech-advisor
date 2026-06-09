"""Test backtest engine."""
import numpy as np
import pandas as pd

from src.backtest import run_backtest


def test_costs_only_on_rebalance_days():
    """Costs are charged only when position changes."""
    n = 50
    df = pd.DataFrame({
        "close": np.linspace(100, 110, n),
        # Stay long for first half, flat for second half: one transition only
        "position": [1] * 25 + [0] * 25,
    }, index=pd.date_range("2020-01-01", periods=n, freq="B"))

    out = run_backtest(df, commission_bps=10, slippage_bps=0)

    # Exactly one position change (from 1 to 0 at row 25)
    assert out["trades"].sum() == 1
    # Cost only on that one day
    assert (out["costs"] > 0).sum() == 1


def test_flat_position_zero_pnl():
    """If position is always 0, strategy returns must be 0."""
    n = 50
    df = pd.DataFrame({
        "close": np.linspace(100, 200, n),
        "position": [0] * n,
    }, index=pd.date_range("2020-01-01", periods=n, freq="B"))

    out = run_backtest(df)
    assert (out["strategy_returns"] == 0).all()
    assert out["equity"].iloc[-1] == 1.0
