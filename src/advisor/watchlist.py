"""Pullback-to-support entry monitor.

For each watchlist ticker, evaluate whether it has pulled back to a buy zone
(SMA50 or SMA200) AND shown stabilization (a bounce), then emit a clear
"可以入场 / 再等" verdict. Removes the need to stare at charts daily.

Entry logic per ticker:
  - Compute distance to target support (SMA50 or SMA200)
  - "in buy zone" when price within +/- 4% of support
  - "stabilized" when today is green OR last 2 days didn't make new lows
  - Verdict:
      到位+企稳   -> "可以入场" (alert)
      到位+未稳   -> "到支撑了, 等企稳" (watch closely)
      接近        -> "接近买点 (还差 X%)"
      还远        -> "还在高位, 继续等"
"""
from dataclasses import dataclass
import pandas as pd


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
    verdict: str            # 可以入场 / 等企稳 / 接近买点 / 继续等
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

    in_buy_zone = abs(dist) <= zone_tolerance or dist < 0  # at/below support

    # Stabilization: today green, or last 2 closes didn't set a new 5-day low
    bounced_today = today_pct > 0
    recent = close.tail(5)
    not_new_low = close.iloc[-1] > recent.min() * 1.001
    stabilized = bounced_today or not_new_low

    # Verdict
    if in_buy_zone and stabilized:
        verdict = "可以入场"
        detail = (f"已到 {target_label} 买区 "
                  f"({'低于' if dist < 0 else '接近'}支撑), "
                  f"且{'今日反弹' if bounced_today else '止跌企稳'} — 数据支持入场")
    elif in_buy_zone and not stabilized:
        verdict = "到支撑了-等企稳"
        detail = (f"已到 {target_label} 买区, 但仍在创新低 — "
                  "等一根阳线或不破前低再进, 别接下跌的刀")
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
                       data: dict[str, pd.DataFrame]) -> list[WatchVerdict]:
    """watch_cfg: list of {ticker, buy_zone, note} from portfolio.yaml."""
    out = []
    for item in watch_cfg:
        t = item.get("ticker")
        if not t or t not in data or data[t].empty:
            continue
        v = evaluate_watch(
            t, data[t]["close"],
            buy_zone=item.get("buy_zone", "sma50"),
            note=item.get("note", ""),
        )
        if v:
            out.append(v)
    # Sort: actionable first (可以入场 > 等企稳 > 接近 > 远)
    order = {"可以入场": 0, "到支撑了-等企稳": 1, "接近买点": 2, "还在高位-继续等": 3}
    out.sort(key=lambda v: order.get(v.verdict, 9))
    return out


def render_watchlist_field(verdicts: list[WatchVerdict]) -> dict | None:
    """Discord embed field for the pullback watchlist."""
    if not verdicts:
        return None

    icon = {
        "可以入场": "▲ 可以入场",
        "到支撑了-等企稳": "◆ 到支撑·等企稳",
        "接近买点": "○ 接近买点",
        "还在高位-继续等": "▽ 高位·等回踩",
    }
    lines = []
    for v in verdicts:
        tag = icon.get(v.verdict, v.verdict)
        arrow = "▲" if v.today_pct > 0 else "▼" if v.today_pct < 0 else "─"
        lines.append(
            f"**{tag}**  ·  `{v.ticker}` ${v.current:.2f} ({arrow}{v.today_pct * 100:+.1f}% 今日)\n"
            f"   买区 {v.target_label} ${v.target_price:.2f}  ·  距支撑 {v.dist_to_target_pct * 100:+.0f}%\n"
            f"   _{v.detail}_"
        )
    return {
        "name": "[盯盘] 回调买点监控  ·  你的 watchlist",
        "value": "\n\n".join(lines),
        "inline": False,
    }
