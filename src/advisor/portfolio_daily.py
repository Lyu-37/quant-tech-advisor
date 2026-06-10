"""Per-holding daily P&L computation.

For each holding read from portfolio.yaml:
  - today's % change (close vs previous trading day close)
  - estimated CAD dollar change today
  - cumulative position value + total P&L since cost basis

FX handling: US-listed tickers priced in USD; we convert to CAD using a
recent USD/CAD rate fetched from yfinance. TSX tickers (.TO) already CAD.
"""
from dataclasses import dataclass
from contextlib import redirect_stderr
import io

import pandas as pd
import yfinance as yf

from .universe import Holding, is_cad_listed


# Fallback FX if yfinance is unavailable (rough but stable)
DEFAULT_USD_CAD = 1.38


@dataclass
class HoldingPnL:
    ticker: str
    shares: float
    cost_basis_cad: float
    today_close_native: float
    today_pct: float
    today_dollar_cad: float
    current_value_cad: float
    total_pnl_cad: float
    total_pnl_pct: float
    currency: str               # "USD" or "CAD"


def _fetch_usd_cad() -> float:
    """Get current USD/CAD rate. Falls back to DEFAULT_USD_CAD on failure."""
    try:
        with redirect_stderr(io.StringIO()):
            df = yf.Ticker("USDCAD=X").history(period="5d", auto_adjust=False)
        if not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return DEFAULT_USD_CAD


def compute_daily_pnl(
    holdings: list[Holding],
    data: dict[str, pd.DataFrame],
) -> list[HoldingPnL]:
    """For each holding, compute today's daily change + total P&L.

    Args:
        holdings: parsed from portfolio.yaml
        data: dict[ticker -> OHLCV DataFrame] from fetcher

    Returns: list of HoldingPnL, one per holding with valid data.
    """
    usd_cad = _fetch_usd_cad()
    out = []

    for h in holdings:
        if h.shares <= 0:
            continue          # share count unfilled — no meaningful P&L
        df = data.get(h.ticker)
        if df is None or df.empty or len(df) < 2:
            continue

        today_close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])
        today_pct = (today_close / prev_close - 1) if prev_close > 0 else 0.0

        # Native currency (covers TSX .TO and Cboe-Canada CDRs .NE)
        currency = "CAD" if is_cad_listed(h.ticker) else "USD"
        fx = 1.0 if currency == "CAD" else usd_cad

        current_value_native = today_close * h.shares
        current_value_cad = current_value_native * fx
        # Today's dollar change in CAD: position value × today's %
        today_dollar_cad = current_value_cad * today_pct
        total_pnl_cad = current_value_cad - h.cost_basis
        total_pnl_pct = total_pnl_cad / h.cost_basis if h.cost_basis else 0.0

        out.append(HoldingPnL(
            ticker=h.ticker,
            shares=h.shares,
            cost_basis_cad=h.cost_basis,
            today_close_native=today_close,
            today_pct=today_pct,
            today_dollar_cad=today_dollar_cad,
            current_value_cad=current_value_cad,
            total_pnl_cad=total_pnl_cad,
            total_pnl_pct=total_pnl_pct,
            currency=currency,
        ))
    return out


def render_portfolio_pnl_field(pnl_list: list[HoldingPnL]) -> dict | None:
    """Build Discord embed field showing per-holding daily + total P&L."""
    if not pnl_list:
        return None

    rows = []
    total_today_cad = 0.0
    total_value_cad = 0.0
    total_cost_cad = 0.0

    for p in pnl_list:
        arrow = ("▲" if p.today_pct > 0.001
                 else "▼" if p.today_pct < -0.001 else "─")
        # Compact one-line per holding
        rows.append((
            arrow,
            p.ticker,
            f"{p.today_pct * 100:+.2f}%",
            f"{p.today_dollar_cad:+.2f}",
            f"{p.total_pnl_pct * 100:+.1f}%",
        ))
        total_today_cad += p.today_dollar_cad
        total_value_cad += p.current_value_cad
        total_cost_cad += p.cost_basis_cad

    # Totals
    total_today_pct = (total_today_cad / (total_value_cad - total_today_cad)
                       if (total_value_cad - total_today_cad) > 0 else 0)
    total_pct = (total_value_cad - total_cost_cad) / total_cost_cad if total_cost_cad else 0

    # Build code-block table
    header = f"{'':<3}{'Ticker':<9}{'今日%':<9}{'今日$CAD':<11}{'累计%':<8}"
    sep = "─" * 40
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"{r[0]:<3}{r[1]:<9}{r[2]:<9}{r[3]:<11}{r[4]:<8}"
        )
    lines.append(sep)
    today_arrow = "▲" if total_today_cad > 0 else "▼" if total_today_cad < 0 else "─"
    lines.append(f"{today_arrow}  组合今日 {total_today_pct * 100:+.2f}% "
                 f"({total_today_cad:+.2f} CAD)")
    lines.append(f"   组合累计 {total_pct * 100:+.1f}% "
                 f"({total_value_cad - total_cost_cad:+.0f} CAD) · "
                 f"总市值 ${total_value_cad:.0f}")

    return {
        "name": "[$$] 你的持仓今日表现  ·  P&L",
        "value": "```\n" + "\n".join(lines) + "\n```",
        "inline": False,
    }
