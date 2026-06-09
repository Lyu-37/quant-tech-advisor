"""Portfolio-level risk metrics for the LOCAL report only.

Never goes into Discord — these reveal portfolio weights and dollar exposures.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .universe import Holding


@dataclass
class PortfolioMetrics:
    total_value: float
    weighted_beta_spy: float | None
    weighted_beta_smh: float | None
    expected_daily_vol_pct: float | None      # annualized too, see below
    expected_annual_vol_pct: float | None
    max_dd_252d_simulated: float | None       # historical simulation
    largest_position_pct: float
    largest_position_ticker: str
    semi_exposure_pct: float                  # % of portfolio in semi+ETF+SOXL
    n_holdings: int
    largest_loser_pct: float | None = None
    largest_winner_pct: float | None = None


def _beta(asset: pd.Series, bench: pd.Series, window: int = 60) -> float | None:
    a = asset.pct_change().dropna().tail(window)
    b = bench.pct_change().dropna().tail(window)
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a.tail(n).values, b.tail(n).values
    v = np.var(b)
    if v == 0:
        return None
    return float(np.cov(a, b)[0, 1] / v)


def compute_portfolio_metrics(
    holdings: list[Holding],
    data: dict[str, pd.DataFrame],
) -> PortfolioMetrics:
    total = sum(h.market_value for h in holdings)
    if total == 0:
        # Defensive zeros — should never happen in practice
        return PortfolioMetrics(0, None, None, None, None, None, 0, "", 0, len(holdings))

    # Weighted beta vs SPY and SMH
    def weighted_beta(bench: str) -> float | None:
        if bench not in data:
            return None
        b = data[bench]["close"]
        accum, w_sum = 0.0, 0.0
        for h in holdings:
            if h.ticker not in data or h.market_value == 0:
                continue
            beta = _beta(data[h.ticker]["close"], b)
            if beta is None:
                continue
            w = h.market_value / total
            accum += beta * w
            w_sum += w
        return accum / w_sum if w_sum else None

    beta_spy = weighted_beta("SPY")
    beta_smh = weighted_beta("SMH")

    # Portfolio daily returns (historical sim)
    weights = {h.ticker: h.market_value / total for h in holdings}
    portfolio_rets = None
    for t, w in weights.items():
        if t not in data:
            continue
        rets = data[t]["close"].pct_change().dropna().tail(252)
        if portfolio_rets is None:
            portfolio_rets = rets * w
        else:
            portfolio_rets = portfolio_rets.add(rets * w, fill_value=0)

    daily_vol = annual_vol = max_dd = None
    if portfolio_rets is not None and len(portfolio_rets) >= 60:
        daily_vol = float(portfolio_rets.std())
        annual_vol = daily_vol * np.sqrt(252)
        equity = (1 + portfolio_rets).cumprod()
        peak = equity.cummax()
        dd = (equity - peak) / peak
        max_dd = float(dd.min())

    # Largest position
    largest = max(holdings, key=lambda h: h.market_value)
    largest_pct = largest.market_value / total

    # Semi exposure (semi stocks + semi ETFs)
    semi_set = {"AMD", "NVDA", "AVGO", "TSM", "ARM", "MU", "INTC",
                "ASML", "AMAT", "LRCX", "KLA",
                "SMH", "SOXX", "SOXL"}
    semi_value = sum(h.market_value for h in holdings if h.ticker in semi_set)
    semi_pct = semi_value / total

    # Best / worst position by P&L pct
    pnl_sorted = sorted(holdings, key=lambda h: h.pnl_pct)
    worst = pnl_sorted[0] if pnl_sorted else None
    best = pnl_sorted[-1] if pnl_sorted else None

    return PortfolioMetrics(
        total_value=total,
        weighted_beta_spy=beta_spy,
        weighted_beta_smh=beta_smh,
        expected_daily_vol_pct=daily_vol,
        expected_annual_vol_pct=annual_vol,
        max_dd_252d_simulated=max_dd,
        largest_position_pct=largest_pct,
        largest_position_ticker=largest.ticker,
        semi_exposure_pct=semi_pct,
        n_holdings=len(holdings),
        largest_loser_pct=worst.pnl_pct if worst else None,
        largest_winner_pct=best.pnl_pct if best else None,
    )


def render_portfolio_metrics(m: PortfolioMetrics) -> str:
    """Markdown for local report."""
    def pct(x, na="—", signed=False):
        if x is None:
            return na
        return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"

    lines = [
        "## 组合层面 Greeks (本地报告专用, 不进 Discord)",
        "",
        f"- **总市值**: ${m.total_value:.2f} CAD across {m.n_holdings} 仓位",
        f"- **加权 β vs SPY**: " + (f"{m.weighted_beta_spy:.2f}"
                                     if m.weighted_beta_spy else "—"),
        f"- **加权 β vs SMH**: " + (f"{m.weighted_beta_smh:.2f}"
                                     if m.weighted_beta_smh else "—"),
        f"- **预期年化波动率**: " + pct(m.expected_annual_vol_pct),
        f"- **历史模拟最大回撤 (252d)**: " + pct(m.max_dd_252d_simulated, signed=True),
        f"- **最大单仓位**: {m.largest_position_ticker} ({pct(m.largest_position_pct)})",
        f"- **半导体敞口** (含 ETF): " + pct(m.semi_exposure_pct),
        "",
    ]

    # Stress scenarios
    lines.append("**压力测试** (如果当日发生这些事件, 组合预计变动):")
    if m.weighted_beta_spy:
        for shock, name in [(-0.05, "SPY -5%"), (-0.10, "SPY -10%"), (0.05, "SPY +5%")]:
            est = m.weighted_beta_spy * shock
            lines.append(f"  - {name} → 组合 ~{est * 100:+.2f}%")
    if m.weighted_beta_smh:
        for shock, name in [(-0.10, "SMH -10% (半导体回调)"),
                            (-0.20, "SMH -20% (深度回调)")]:
            est = m.weighted_beta_smh * shock
            dollar = est * m.total_value
            lines.append(f"  - {name} → ~{est * 100:+.2f}% (${dollar:+.0f} CAD)")
    lines.append("")
    return "\n".join(lines)
