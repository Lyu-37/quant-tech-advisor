"""US equity market calendar (NYSE/Nasdaq) — trading days and session logic.

Everything date-related in the advisor must go through this module instead of
naive weekday checks. Times are US/Eastern (Montreal is the same zone).

Holiday table is hardcoded for 2026-2027. extend HOLIDAYS before 2028-01-01;
`expected_latest_session` raises if asked about a date beyond the table so the
failure is loud instead of silently mislabeling data.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Full-day market holidays (observed dates).
HOLIDAYS = {
    # 2026
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Washington's Birthday
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed, Jul 4 = Sat)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # MLK Day
    date(2027, 2, 15),   # Washington's Birthday
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth (observed, Jun 19 = Sat)
    date(2027, 7, 5),    # Independence Day (observed, Jul 4 = Sun)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving
    date(2027, 12, 24),  # Christmas (observed, Dec 25 = Sat)
}
CALENDAR_VALID_UNTIL = date(2028, 1, 1)


def now_et() -> datetime:
    return datetime.now(ET)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def most_recent_trading_day(d: date) -> date:
    """Latest trading day <= d."""
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    """Latest trading day strictly before d."""
    return most_recent_trading_day(d - timedelta(days=1))


def expected_latest_session(now: datetime | None = None) -> date:
    """The trading session whose daily bar should be the newest one available.

    - Before ~09:35 ET (no bar for today yet): the previous trading day.
    - During / after the session on a trading day: today (the bar may be a
      partial intraday print before 16:00 — callers that must distinguish
      should also check `is_session_closed`).
    - Weekends / holidays: the most recent trading day.
    """
    now = now or now_et()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    d = now.date()
    if d >= CALENDAR_VALID_UNTIL:
        raise RuntimeError(
            f"market_calendar holiday table ends {CALENDAR_VALID_UNTIL} — extend HOLIDAYS"
        )
    if not is_trading_day(d):
        return most_recent_trading_day(d)
    if now.time() < time(9, 35):
        return previous_trading_day(d)
    return d


def is_session_closed(now: datetime | None = None) -> bool:
    """True if the regular session for `expected_latest_session` has closed
    (i.e. the latest daily bar is a final close, not an intraday print)."""
    now = now or now_et()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    d = now.date()
    if not is_trading_day(d):
        return True
    return now.time() >= time(16, 5) or now.time() < time(9, 35)


def freshness_warning(data_as_of: date, now: datetime | None = None) -> str | None:
    """Return a user-facing warning string when the data is older than the
    expected latest session, else None."""
    expected = expected_latest_session(now)
    if data_as_of >= expected:
        return None
    return (f"数据截至 {data_as_of.isoformat()} 收盘 — 不是日历预期的最新交易日 "
            f"({expected.isoformat()}), 以下所有 \"今日\" 字段请按 "
            f"{data_as_of.isoformat()} 理解. "
            f"(若 {expected.isoformat()} 为临时休市/哀悼日, 此为正常状态; "
            "否则是数据源滞后)")
