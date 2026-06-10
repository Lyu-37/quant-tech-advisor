"""Earnings calendar + macro event lookups.

Pulls upcoming earnings dates from yfinance and flags tickers reporting
within a configurable window — these are event-risk warnings the chart
alone won't show.
"""
from contextlib import redirect_stderr
from dataclasses import dataclass
from datetime import date, timedelta, datetime
import io
import pandas as pd
import yfinance as yf


# ETFs and indices don't have earnings — skip the calendar lookup entirely
# to avoid yfinance's noisy HTTP 404 logs on stderr.
NON_EARNINGS_SYMBOLS = {
    "SMH", "SOXX", "SOXL", "QQQ", "QQQE", "XLK", "TQQQ", "TECL", "SPY",
    "VDY.TO", "VFV.TO", "QQQX", "XQQ.TO", "ZQQ.TO", "QQC.TO",
    "^TNX", "^VIX", "^VIX3M", "DX-Y.NYB",
}


@dataclass
class EarningsEvent:
    ticker: str
    report_date: date
    days_until: int
    eps_estimate: float | None = None
    revenue_estimate: float | None = None
    # Yahoo gives a RANGE of candidate dates for unconfirmed reports. A single
    # date is *probably* confirmed; a range is definitely an estimate. Render
    # estimates with an "约" marker — Yahoo's estimated dates routinely move
    # by days, and a false "5 天后财报" can trigger premature de-risking.
    confirmed: bool = True


def _coerce_date(x) -> date | None:
    if x is None or (hasattr(pd, "isna") and pd.isna(x)):
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, pd.Timestamp):
        return x.date()
    try:
        return pd.Timestamp(x).date()
    except (ValueError, TypeError):
        return None


def upcoming_earnings(ticker: str, within_days: int = 21) -> EarningsEvent | None:
    """Return next earnings if within `within_days` days."""
    if ticker in NON_EARNINGS_SYMBOLS:
        return None
    try:
        t = yf.Ticker(ticker)
        # yfinance's .calendar internally hits an endpoint that 404s for many
        # tickers and prints to stderr — swallow that to keep our logs clean.
        with redirect_stderr(io.StringIO()):
            cal = t.calendar
    except Exception:
        return None
    if cal is None:
        return None

    today = date.today()
    cutoff = today + timedelta(days=within_days)

    # cal can be a dict (newer yfinance) or DataFrame (older)
    report_dates = []
    eps_est = rev_est = None

    if isinstance(cal, dict):
        for k in ("Earnings Date", "earnings_date", "EarningsDate"):
            if k in cal:
                v = cal[k]
                if isinstance(v, (list, tuple)):
                    report_dates.extend(_coerce_date(x) for x in v)
                else:
                    report_dates.append(_coerce_date(v))
                break
        eps_est = cal.get("Earnings Average") or cal.get("earnings_estimate")
        rev_est = cal.get("Revenue Average") or cal.get("revenue_estimate")
    elif isinstance(cal, pd.DataFrame) and not cal.empty:
        try:
            row = cal.loc["Earnings Date"] if "Earnings Date" in cal.index else None
            if row is not None:
                for v in row.values:
                    report_dates.append(_coerce_date(v))
        except Exception:
            pass

    all_dates = [d for d in report_dates if d is not None]
    in_window = [d for d in all_dates if today <= d <= cutoff]
    if not in_window:
        return None

    nearest = min(in_window)
    return EarningsEvent(
        ticker=ticker,
        report_date=nearest,
        days_until=(nearest - today).days,
        eps_estimate=float(eps_est) if isinstance(eps_est, (int, float)) else None,
        revenue_estimate=float(rev_est) if isinstance(rev_est, (int, float)) else None,
        confirmed=(len(set(all_dates)) == 1),
    )


def earnings_for_universe(tickers: list[str], within_days: int = 21) -> list[EarningsEvent]:
    """Batch query, return only tickers with upcoming earnings."""
    events = []
    for t in tickers:
        ev = upcoming_earnings(t, within_days)
        if ev:
            events.append(ev)
    events.sort(key=lambda e: e.days_until)
    return events


def render_earnings_block(events: list[EarningsEvent], limit: int = 10) -> str:
    """Markdown block for local report."""
    if not events:
        return "## 财报日历 (未来 21 天)\n\n*近期无重大财报事件*\n"
    lines = ["## 财报日历 (未来 21 天)", "",
             "| 标的 | 日期 | 距今 | EPS 预期 |",
             "|---|---|---:|---:|"]
    for e in events[:limit]:
        eps = f"${e.eps_estimate:.2f}" if e.eps_estimate else "—"
        urgency = "**[本周]**" if e.days_until <= 7 else ""
        approx = "" if e.confirmed else " (约)"
        lines.append(f"| **{e.ticker}** | {e.report_date.isoformat()}{approx} "
                     f"| {e.days_until}d {urgency} | {eps} |")
    return "\n".join(lines) + "\n"
