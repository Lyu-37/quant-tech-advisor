"""Batch OHLCV fetcher.

Optimized for the advisor workflow:
  - per-ticker yf.Ticker(...).history calls with retry + backoff
  - parquet cache keyed by (symbol, start, end)
  - returns dict[ticker -> DataFrame]

Freshness contract: this module can only fetch what Yahoo has — it CANNOT
guarantee the latest bar is today's. Callers that present "today" data MUST
check `data_as_of()` against `market_calendar.expected_latest_session()` and
surface a warning when they differ (see daily_brief / preclose_brief).
"""
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf

from .market_calendar import (
    ET, now_et, expected_latest_session, is_session_closed,
)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FETCH_RETRIES = 3          # total attempts per ticker
RETRY_BACKOFF_S = 1.5      # sleep grows linearly: 1.5s, 3.0s


def _cache_path(ticker: str, start: str, end: str) -> Path:
    safe = ticker.replace("^", "_idx_").replace("=", "_eq_").replace(".", "_")
    return CACHE_DIR / f"{safe}_{start}_{end}.parquet"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns, ensure tz-naive index, keep standard OHLCV cols.

    Drops rows without a close: pre-market, Yahoo emits a placeholder row for
    the upcoming session with NaN OHLC — one trailing NaN poisons every
    iloc[-1] computation downstream (composite=nan, movers=+nan%...).
    A bar with no close is not a bar."""
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[cols]
    if "close" in df.columns:
        df = df[df["close"].notna()]
    return df


def _is_stale(df: pd.DataFrame) -> bool:
    """A cache is stale if its last bar is older than the expected latest
    session (ET clock + NYSE holiday calendar — NOT naive UTC weekdays)."""
    if df.empty:
        return True
    last_bar = df.index[-1].date()
    return last_bar < expected_latest_session()


def _cacheable_view(df: pd.DataFrame) -> pd.DataFrame:
    """What we are willing to persist. If the last bar is today's session and
    the session has NOT closed yet, drop it: a 15:30 intraday print must never
    be served later as if it were a final close."""
    if df.empty:
        return df
    last_bar = df.index[-1].date()
    if last_bar >= now_et().date() and not is_session_closed():
        return df.iloc[:-1]
    return df


def fetch_one(ticker: str, start: str, end: str, use_cache: bool = True) -> pd.DataFrame:
    """Fetch a single symbol with file cache (staleness-guarded) and retries."""
    cache_file = _cache_path(ticker, start, end)
    if use_cache and cache_file.exists():
        df = pd.read_parquet(cache_file)
        if not df.empty and not _is_stale(df):
            return df

    raw = None
    last_err: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            raw = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
            if raw is not None and not raw.empty:
                break
        except Exception as e:               # network / parse / rate-limit
            last_err = e
        if attempt < FETCH_RETRIES - 1:
            _time.sleep(RETRY_BACKOFF_S * (attempt + 1))

    if raw is None or raw.empty:
        # Fall back to the cache even when use_cache=False: a stale series the
        # caller can date-check beats a silently missing ticker. Downstream
        # "today" computations exclude lagging tickers via data_as_of checks.
        if cache_file.exists():
            cached = pd.read_parquet(cache_file)
            if not cached.empty:
                print(f"  ! {ticker}: fetch failed, falling back to cached data "
                      f"(through {cached.index[-1].date()})")
                return cached
        raise ValueError(f"No data returned for {ticker}"
                         + (f" ({last_err})" if last_err else ""))

    df = _normalize(raw)
    if df.empty:
        raise ValueError(f"After normalization, {ticker} has no columns")
    to_cache = _cacheable_view(df)
    if not to_cache.empty:
        to_cache.to_parquet(cache_file)
    return df


def fetch_universe(
    tickers: list[str],
    lookback_days: int = 800,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch all tickers, return {ticker: df}. Failures are logged, not raised.

    lookback_days is CALENDAR days. 800 ≈ 550 trading bars — enough for the
    12-1 momentum rolling percentile (needs 504) plus 52w stats.
    """
    # yfinance history(end=...) is EXCLUSIVE — pass tomorrow (ET) so the
    # latest session is always included.
    end = now_et().date() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    start_s, end_s = start.isoformat(), end.isoformat()

    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            out[t] = fetch_one(t, start_s, end_s, use_cache=use_cache)
        except Exception as e:
            print(f"  ! fetch failed for {t}: {e}")
    return out


def data_as_of(data: dict[str, pd.DataFrame]) -> date | None:
    """The newest bar date across the universe — the run's true 'as of' date."""
    dates = [df.index[-1].date() for df in data.values()
             if df is not None and not df.empty]
    return max(dates) if dates else None


def consensus_as_of(data: dict[str, pd.DataFrame]) -> date | None:
    """The MODAL last-bar date across the universe.

    Robust where max() is not: 24h instruments (DXY, ^VIX quotes from 03:00
    ET) can carry a newer bar than every equity, and an AD-HOC market closure
    (day of mourning — not in any hardcoded holiday table) leaves the whole
    equity universe one session behind the calendar's expectation. The modal
    date is what the market actually traded last; individually lagging
    tickers (halts) still stand out against it."""
    counts: dict[date, int] = {}
    for df in data.values():
        if df is None or df.empty:
            continue
        d = df.index[-1].date()
        counts[d] = counts.get(d, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda d: counts[d])


def lagging_tickers(data: dict[str, pd.DataFrame],
                    as_of: date | None = None) -> list[str]:
    """Tickers whose last bar is older than the universe as-of date — their
    iloc[-1] is NOT 'today' and they must be excluded from today-change math."""
    as_of = as_of or data_as_of(data)
    if as_of is None:
        return []
    return [t for t, df in data.items()
            if df is not None and not df.empty and df.index[-1].date() < as_of]


def latest_close(df: pd.DataFrame) -> float:
    return float(df["close"].iloc[-1])
