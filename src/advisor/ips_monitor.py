"""IPS target-allocation drift monitor.

The IPS (configs/IPS-2026-06.md) defines target weights; portfolio.yaml's
`ips_targets` block mirrors them so the system can check actual weights
against targets every day. Outside the band -> flagged; during deployment
(tranches pending) the same table doubles as a progress checklist.

Local report only — contains personal allocation data.
"""
from dataclasses import dataclass

from .universe import Holding


@dataclass
class DriftRow:
    name: str
    tickers: list[str]
    target_pct: float
    actual_pct: float
    drift_pp: float          # actual - target, percentage points
    out_of_band: bool
    value_cad: float


def evaluate_ips_drift(holdings: list[Holding],
                       ips_cfg: dict | None) -> list[DriftRow]:
    """Compare live holding weights against IPS targets.

    Holdings must already carry market values (set_market_value done).
    Returns [] when no ips_targets config or no valued holdings.
    """
    if not ips_cfg or not ips_cfg.get("groups"):
        return []
    total = sum(h.market_value for h in holdings)
    if total <= 0:
        return []
    band = float(ips_cfg.get("band_pp", 5))
    by_ticker = {h.ticker: h.market_value for h in holdings}

    rows = []
    for g in ips_cfg["groups"]:
        tickers = list(g.get("tickers", []))
        value = sum(by_ticker.get(t, 0.0) for t in tickers)
        actual = value / total * 100
        target = float(g.get("pct", 0))
        drift = actual - target
        rows.append(DriftRow(
            name=str(g.get("name", "/".join(tickers))),
            tickers=tickers, target_pct=target, actual_pct=actual,
            drift_pp=drift, out_of_band=abs(drift) > band,
            value_cad=value,
        ))
    return rows


def render_ips_drift(rows: list[DriftRow], band_pp: float = 5) -> str:
    """Markdown block for the local report."""
    if not rows:
        return ""
    lines = ["## IPS 配置偏离监控 (本地专用)", "",
             f"_目标 = IPS-2026-06; 偏离超过 ±{band_pp:.0f}pp 标记;"
             " 部署期偏离属预期, 此表兼任部署进度清单_", "",
             "| 资产组 | 目标% | 实际% | 偏离 | 现值 CAD | 状态 |",
             "|---|---:|---:|---:|---:|---|"]
    for r in rows:
        status = "**[出带]**" if r.out_of_band else "在带内"
        lines.append(f"| {r.name} | {r.target_pct:.0f}% | {r.actual_pct:.1f}% "
                     f"| {r.drift_pp:+.1f}pp | ${r.value_cad:,.0f} | {status} |")
    n_out = sum(1 for r in rows if r.out_of_band)
    if n_out:
        lines.append("")
        lines.append(f"_{n_out} 组出带 — 部署完成后仍出带时, 按 IPS 第 4 节"
                     "用新资金回平, 避免卖出_")
    lines.append("")
    return "\n".join(lines)
