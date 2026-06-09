"""Data loader for OHLCV from yfinance."""
from pathlib import Path
import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_ohlcv(
    ticker: str,
    start: str,
    end: str,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Load adjusted OHLCV data for a single ticker.

    Uses yfinance .history() with auto_adjust=True so all prices are
    already adjusted for splits/dividends. Caches to local parquet.

    Args:
        ticker: e.g. "SPY"
        start:  "YYYY-MM-DD" inclusive
        end:    "YYYY-MM-DD" exclusive
        use_cache: if True, reuse parquet cache when present

    Returns:
        DataFrame indexed by date, columns: open, high, low, close, volume
    """
    cache_file = CACHE_DIR / f"{ticker}_{start}_{end}.parquet"

    if use_cache and cache_file.exists():
        df = pd.read_parquet(cache_file)
    else:
        df = yf.Ticker(ticker).history(
            start=start, end=end, auto_adjust=True
        )
        if df.empty:
            raise ValueError(f"No data returned for {ticker}")
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        # parquet doesn't like tz-aware index
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.to_parquet(cache_file)

    # --- Integrity checks (fail loud, don't paper over bad data) ---
    assert df.index.is_monotonic_increasing, "Dates not sorted"
    assert not df.index.has_duplicates, "Duplicate dates"
    assert (df["close"] > 0).all(), "Non-positive close prices"
    assert (df["volume"] >= 0).all(), "Negative volume"

    return df
