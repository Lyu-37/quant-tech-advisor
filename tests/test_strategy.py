"""Test strategy module — focus on lookahead protection."""
import numpy as np
import pandas as pd
import pytest

from src.strategy import sma_crossover_signals


def _make_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"close": prices}, index=dates)


def test_no_lookahead_in_position():
    """Position at time t must NOT change if we modify close[t].

    This is the canonical lookahead test: tampering with today's
    close should not retroactively change today's position.
    """
    df = _make_df()
    out_baseline = sma_crossover_signals(df, 10, 30)

    df_tampered = df.copy()
    df_tampered.iloc[100, df_tampered.columns.get_loc("close")] = 9999.0  # spike

    out_tampered = sma_crossover_signals(df_tampered, 10, 30)

    # Position[100] is set from signal[99], which uses close[<=99].
    # Tampering close[100] must NOT affect position[100].
    assert out_baseline["position"].iloc[100] == out_tampered["position"].iloc[100]


def test_warmup_zeros():
    """Until long MA can be computed, position must be 0."""
    df = _make_df(n=100)
    out = sma_crossover_signals(df, short_window=20, long_window=60)
    # First 60 rows: long MA is NaN -> signal is 0 -> position (shifted) is 0
    assert out["position"].iloc[:60].sum() == 0


def test_short_must_be_less_than_long():
    df = _make_df(n=100)
    with pytest.raises(AssertionError):
        sma_crossover_signals(df, short_window=60, long_window=20)


def test_position_is_binary():
    df = _make_df()
    out = sma_crossover_signals(df, 10, 30)
    assert set(out["position"].unique()).issubset({0, 1})
