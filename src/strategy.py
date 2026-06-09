"""SMA crossover strategy.

Critical invariant: position at time t is decided using ONLY data
available at the close of t-1. This is enforced by .shift(1).
"""
import pandas as pd


def sma_crossover_signals(
    df: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 60,
) -> pd.DataFrame:
    """Generate position signals from SMA crossover.

    Logic:
        signal[t]   = 1 if MA_short[t] > MA_long[t] else 0   (computed at close of t)
        position[t] = signal[t-1]                            (executed open of t+1)

    Args:
        df: must contain a 'close' column
        short_window: fast MA length
        long_window:  slow MA length (must be > short)

    Returns:
        DataFrame with added columns: ma_short, ma_long, signal, position
    """
    assert short_window < long_window, (
        f"short_window ({short_window}) must be < long_window ({long_window})"
    )
    assert "close" in df.columns, "DataFrame must contain 'close'"

    out = df.copy()
    out["ma_short"] = out["close"].rolling(short_window).mean()
    out["ma_long"] = out["close"].rolling(long_window).mean()

    # Long-only: in market when fast > slow, flat otherwise
    out["signal"] = (out["ma_short"] > out["ma_long"]).astype(int)

    # *** LOOKAHEAD PROTECTION ***
    # signal[t] uses close[t]. We can only ACT on it at t+1 open.
    # So the position we actually hold during day t is signal[t-1].
    out["position"] = out["signal"].shift(1).fillna(0).astype(int)

    return out
