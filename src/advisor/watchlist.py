"""Pullback-to-support entry monitor.

For each watchlist ticker, evaluate whether it has pulled back to a buy zone
(SMA50 or SMA200) AND shown stabilization, then emit a verdict.

Entry logic per ticker:
  - "in buy zone" = within +4% above support down to a FLOOR below it
    (-10% normal, -5% leveraged ETFs). Below the floor the support has
    FAILED — that is a breakdown, not a buy zone. The old logic treated any
    depth below support as "in zone", which turned crashes into buy alerts.
  - "stabilized" = today is green AND neither of the last two sessions
    undercut the prior 5-day low. One green candle the day after a fresh low
    does NOT qualify — the first green day after capitulation shows as
    "等企稳", entry signals only fire once the low has held for 2 sessions.
  - Verdicts:
      到位+企稳        -> "可以入场"
      到位+未稳        -> "到支撑了-等企稳"
      跌破 floor       -> "支撑已破-不抄底"
      接近 (<=12% 上方) -> "接近买点"
      还远             -> "还在高位-继续等"
"""
from dataclasses import dataclass
from datetime import date
import pandas as pd

# 3x/2x products: vol drag + gap risk — tighter floor, no knife-catching.
LEVERAGED_ETFS = {"SOXL", "SOXS", "TQQQ", "SQQQ", "TECL", "TECS",
                  "UPRO", "SPXU", "USD", "QLD"}


@dataclass
class WatchVerdict:
    ticker: str
    note: str
    current: float
    target_label: str       # "SMA50" / "SMA200"
    target_price: float
    dist_to_target_pct: float   # +ve = above support (still need to fall)
    in_buy_zone: bool
    stabilized: bool
    bounced_today: bool
    today_pct: float
    verdict: str            # 可以入场 / 等企稳 / 支撑已破 / 接近买点 / 继续等
    detail: str


def evaluate_watch(ticker: str, close: pd.Series, buy_zone: str = "sma50",
                   note: str = "", zone_tolerance: float = 0.04) -> WatchVerdict | None:
    if len(close) < 60:
        return None

    current = float(close.iloc[-1])
    today_pct = float(close.iloc[-1] / close.iloc[-2] - 1)

    if buy_zone == "sma200" and len(close) >= 200:
        target = float(close.rolling(200).mean().iloc[-1])
        target_label = "SMA200"
    else:
        target = float(close.rolling(50).mean().iloc[-1])
        target_label = "SMA50"

    dist = (current - target) / target  # +ve = above support

    floor = -0.05 if ticker in LEVERAGED_ETFS else -0.10
    support_broken = dist < floor
    in_buy_zone = (floor <= dist <= zone_tolerance)

    # Stabilization: today green AND the prior 5-day low has held for the
    # last TWO sessions (low must be >= 2 sessions old).
    bounced_today = today_pct > 0
    low_before_today = float(close.iloc[-6:-1].min())
    low_before_yesterday = float(close.iloc[-7:-2].min())
    held_today = current > low_before_today
    held_yesterday = float(close.iloc[-2]) > low_before_yesterday
    stabilized = bounced_today and held_today and held_yesterday

    # Verdict
    if support_broken:
        verdict = "支撑已破-不抄底"
        detail = (f"已跌破 {target_label} 超过 {-floor * 100:.0f}% "
                  f"(现 {dist * 100:+.0f}%) — 支撑失效, 这是破位不是回调, "
                  "等新的底部结构形成再说")
    elif in_buy_zone and stabilized:
        verdict = "可以入场"
        detail = (f"已到 {target_label} 买区 "
                  f"({'低于' if dist < 0 else '接近'}支撑), "
                  f"且近 2 日未创新低 + 今日收红 — 数据支持分批入场")
    elif in_buy_zone and not stabilized:
        verdict = "到支撑了-等企稳"
        detail = (f"已到 {target_label} 买区, 但低点还太新鲜 — "
                  "等连续 2 日不破低 + 一根阳线再进, 别接下跌的刀")
    elif 0 < dist <= 0.12:
        verdict = "接近买点"
        detail = (f"距 {target_label} 买区还差 {dist * 100:.0f}% "
                  f"(支撑 ${target:.2f}) — 继续等回踩")
    else:
        verdict = "还在高位-继续等"
        detail = (f"距 {target_label} 还有 {dist * 100:.0f}% "
                  f"(支撑 ${target:.2f}) — 现在追高风险大")

    return WatchVerdict(
        ticker=ticker, note=note, current=current,
        target_label=target_label, target_price=target,
        dist_to_target_pct=dist,
        in_buy_zone=in_buy_zone, stabilized=stabilized,
        bounced_today=bounced_today, today_pct=today_pct,
        verdict=verdict, detail=detail,
    )


def evaluate_watchlist(watch_cfg: list[dict],
                       data: dict[str, pd.DataFrame],
                       as_of: date | None = None) -> list[WatchVerdict]:
    """watch_cfg: list of {ticker, buy_zone, note} from portfolio.yaml.

    Tickers whose last bar is older than `as_of` are skipped — a stale series
    must not produce an entry alert dated today.
    """
    out = []
    for item in watch_cfg:
        t = item.get("ticker")
        if not t or t not in data or data[t].empty:
            continue
        df = data[t]
        if as_of is not None and df.index[-1].date() < as_of:
            print(f"  ! watchlist {t}: data lags as-of date, skipped")
            continue
        v = evaluate_watch(
            t, df["close"],
            buy_zone=item.get("buy_zone", "sma50"),
            note=item.get("note", ""),
        )
        if v:
            out.append(v)
    # Sort: actionable first
    order = {"可以入场": 0, "到支撑了-等企稳": 1, "接近买点": 2,
             "支撑已破-不抄底": 3, "还在高位-继续等": 4}
    out.sort(key=lambda v: order.get(v.verdict, 9))
    return out


def render_watchlist_field(verdicts: list[WatchVerdict],
                           buy_gate: str = "pass") -> dict | None:
    """Discord embed field for the pullback watchlist.

    `buy_gate`: the regime gate. On "temper" (risk-off), 可以入场 must not be
    presented as an actionable entry — the gate covers ALL buy paths.
    """
    if not verdicts:
        return None

    icon = {
        "可以入场": "▲ 可以入场",
        "到支撑了-等企稳": "◆ 到支撑·等企稳",
        "支撑已破-不抄底": "✕ 支撑已破·不抄底",
        "接近买点": "○ 接近买点",
        "还在高位-继续等": "▽ 高位·等回踩",
    }
    lines = []
    for v in verdicts:
        tag = icon.get(v.verdict, v.verdict)
        detail = v.detail
        if v.verdict == "可以入场" and buy_gate == "temper":
            tag = "◆ 到位但 risk-off·等体制转好"
            detail = "价位条件满足, 但大盘 risk-off — 闸门关闭, 等体制转好再进"
        arrow = "▲" if v.today_pct > 0 else "▼" if v.today_pct < 0 else "─"
        lines.append(
            f"**{tag}**  ·  `{v.ticker}` ${v.current:.2f} ({arrow}{v.today_pct * 100:+.1f}% 今日)\n"
            f"   买区 {v.target_label} ${v.target_price:.2f}  ·  距支撑 {v.dist_to_target_pct * 100:+.0f}%\n"
            f"   _{detail}_"
        )
    return {
        "name": "[盯盘] 回调买点监控  ·  你的 watchlist",
        "value": "\n\n".join(lines),
        "inline": False,
    }
