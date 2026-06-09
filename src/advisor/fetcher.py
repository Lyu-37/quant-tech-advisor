"""Batch OHLCV fetcher.

Optimized for the advisor workflow:
  - one yf.download call for the whole universe
  - parquet cache keyed by (symbol, start, end)
  - returns dict[ticker -> DataFrame]
"""
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(ticker: str, start: str, end: str) -> Path:
    safe = ticker.replace("^", "_idx_").replace("=", "_eq_").replace(".", "_")
    return CACHE_DIR / f"{safe}_{start}_{end}.parquet"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns, ensure tz-naive index, keep standard OHLCV cols."""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[cols]


def _is_stale(df: pd.DataFrame) -> bool:
    """A cache is stale if its last bar is older than the most recent weekday.

    Markets run Mon-Fri. On Monday, a cache whose last bar is Friday is fine
    ONLY before Monday's close; once Monday data exists we want it. Since the
    briefs run at/after market close (15:30, 17:30 ET), we require the cache to
    include the most recent weekday <= today. This prevents serving Friday's
    crash data on an up Monday."""
    if df.empty:
        return True
    last_bar = df.index[-1].date()
    today = datetime.utcnow().date()
    expected = today
    while expected.weekday() >= 5:        # Sat/Sun -> walk back to Friday
        expected = expected - timedelta(days=1)
    # Stale if the cache doesn't reach the most recent expected weekday.
    return last_bar < expected


def fetch_one(ticker: str, start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Fetch a single symbol with file cache (with staleness guard)."""
    cache_file = _cache_path(ticker, start, end)
    if use_cache and cache_file.exists():
        df = pd.read_parquet(cache_file)
        # Only trust the cache if it isn't stale. A cache built pre-market
        # (e.g. 00:03) only has yesterday's data — refetch once today's close
        # is available rather than serving stale data all day.
        if not df.empty and not _is_stale(df):
            return df

    raw = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    if raw.empty:
        # Fall back to (possibly stale) cache rather than crashing
        if use_cache and cache_file.exists():
            cached = pd.read_parquet(cache_file)
            if not cached.empty:
                return cached
        raise ValueError(f"No data returned for {ticker}")

    df = _normalize(raw)
    if df.empty:
        raise ValueError(f"After normalization, {ticker} has no columns")
    df.to_parquet(cache_file)
    return df


def fetch_universe(
    tickers: list[str],
    lookback_days: int = 400,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch all tickers, return {ticker: df}. Failures are logged, not raised."""
    # yfinance history(end=...) is EXCLUSIVE — passing today's date drops
    # today's bar. Add 1 day so the latest trading session is always included.
    end = datetime.utcnow().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    start_s, end_s = start.isoformat(), end.isoformat()

    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            out[t] = fetch_one(t, start_s, end_s, use_cache=use_cache)
        except Exception as e:
            print(f"  ! fetch failed for {t}: {e}")
    return out


def latest_close(df: pd.DataFrame) -> float:
    return float(df["close"].iloc[-1])
