"""AI infrastructure sub-advisor (GEV focus).

Analyzes the AI-power / datacenter / electrification theme — the user holds
GEV (~9% of portfolio) as indirect AI exposure. Mirrors the semi advisor
structure but ranks AI-infra peers and computes GEV-specific signals.
"""
from dataclasses import dataclass
import pandas as pd

from .indicators import summarize_ticker, relative_strength
from .universe import AI_INFRA_LEADERS, Holding


@dataclass
class AIInfraReport:
    composite_0_100: float
    label: str
    gev_summary: dict
    gev_rs_smh: dict        # vs semi sector (correlation check)
    gev_rs_peers: dict      # vs peer ETF or VRT (closest comp)
    peer_ranking: list      # list of (ticker, summary)
    gev_holding: Holding | None


def analyze_ai_infra(
    data: dict[str, pd.DataFrame],
    smh_close: pd.Series,
    holdings: list[Holding],
) -> AIInfraReport:
    """Produce an AI-infra theme report focused on GEV."""
    peers = [(t, summarize_ticker(data[t]["close"]))
             for t in AI_INFRA_LEADERS if t in data and not data[t].empty]

    gev_holding = next((h for h in holdings if h.ticker == "GEV"), None)

    gev_summary = summarize_ticker(data["GEV"]["close"]) if "GEV" in data else {}

    gev_rs_smh = (relative_strength(data["GEV"]["close"], smh_close, window=60)
                  if "GEV" in data else {"score": 5, "label": "no_data"})

    # VRT (Vertiv) is the cleanest AI-power comp
    if "GEV" in data and "VRT" in data:
        gev_rs_peers = relative_strength(data["GEV"]["close"], data["VRT"]["close"], window=60)
    else:
        gev_rs_peers = {"score": 5, "label": "no_data"}

    # Composite: simple avg of GEV's own trend + momentum + RS vs peer
    if gev_summary:
        sub = (
            gev_summary["trend"]["score"] * 0.40 +
            gev_summary["momentum"]["score"] * 0.30 +
            gev_rs_peers.get("score", 5) * 0.30
        )
        composite = sub * 10
    else:
        composite = 50.0

    if composite >= 70:
        label = "AI 基建仍是 hot theme"
    elif composite >= 50:
        label = "AI 基建中性"
    elif composite >= 30:
        label = "AI 基建动能减弱"
    else:
        label = "AI 基建可能见顶"

    return AIInfraReport(
        composite_0_100=composite,
        label=label,
        gev_summary=gev_summary,
        gev_rs_smh=gev_rs_smh,
        gev_rs_peers=gev_rs_peers,
        peer_ranking=peers,
        gev_holding=gev_holding,
    )


def render_ai_infra_section(report: AIInfraReport) -> str:
    """Markdown block for the AI infra theme."""
    lines = [f"## AI 基建主题分析 (GEV 视角)\n",
             f"**主题评分**: {report.composite_0_100:.1f}/100  — _{report.label}_\n"]

    if report.gev_holding:
        h = report.gev_holding
        lines.append(
            f"你持有 **GEV {h.shares:.4f} 股**, 本金 ${h.cost_basis:.2f} → "
            f"现值 ${h.market_value:.2f} ({h.pnl_pct * 100:+.2f}%)\n"
        )

    if report.gev_summary:
        s = report.gev_summary
        lines.extend([
            "### GEV 个股快照",
            f"- 现价: ${s['last']:.2f}",
            f"- 趋势: {s['trend']['label']}",
            f"- 60d 收益: {s['ret_60d'] * 100:+.2f}%   |   "
            f"252d 收益: {s.get('ret_252d', 0) * 100:+.2f}%",
            f"- 距 SMA50: {s['dist_sma50'] * 100:+.2f}%   |   "
            f"距 SMA200: {s['dist_sma200'] * 100:+.2f}%   |   "
            f"距 52w 高: {s['dd_from_52w_high'] * 100:+.2f}%",
            f"- 年化波动率: {s['realized_vol_20d'] * 100:.2f}%",
        ])
        stretch = s.get("stretch", {})
        if stretch.get("severity", 0) >= 2:
            lines.append(f"- **[拉伸警告]** {stretch['level']}: "
                         + "; ".join(stretch.get("notes", [])))
        lines.extend([
            f"- vs 半导体 (60d): {report.gev_rs_smh.get('label', 'n/a')} "
            f"({report.gev_rs_smh.get('spread', 0) * 100:+.2f}%) — "
            f"{'半导体共振' if report.gev_rs_smh.get('spread', 0) > 0 else '相对落后'}",
            f"- vs VRT (60d): {report.gev_rs_peers.get('label', 'n/a')} "
            f"({report.gev_rs_peers.get('spread', 0) * 100:+.2f}%)",
            "",
        ])

    # Peer ranking
    sorted_peers = sorted(
        report.peer_ranking, key=lambda x: x[1].get("ret_60d") or -999, reverse=True
    )
    lines.extend([
        "### AI 基建龙头排名 (按 60d 收益)",
        "",
        "| 标的 | 现价 | 20d | 60d | 距 SMA50 | 距 52w 高 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for t, s in sorted_peers:
        lines.append(
            f"| **{t}** | ${s['last']:.2f} | {s['ret_20d'] * 100:+.2f}% | "
            f"{s['ret_60d'] * 100:+.2f}% | {s['dist_sma50'] * 100:+.2f}% | "
            f"{s['dd_from_52w_high'] * 100:+.2f}% |"
        )

    # Position-specific recommendation
    lines.extend(["", "### GEV 操作建议"])
    if report.gev_holding:
        h = report.gev_holding
        sev = report.gev_summary.get("stretch", {}).get("severity", 0)
        if sev >= 3:
            lines.append("- GEV 已极端拉伸, 与 AMD 同样需要分批止盈逻辑")
        elif report.composite_0_100 < 40:
            lines.append("- AI 基建主题动能减弱, 考虑减持 GEV 一半")
        elif h.pnl_pct < -0.10:
            lines.append("- GEV 浮亏 >10%, 但仓位小, 不急止损; 看趋势是否破 SMA200")
        else:
            lines.append(f"- GEV 当前 P/L {h.pnl_pct * 100:+.2f}%, 持仓占比 ~9%, 可继续持有")
            lines.append("- 触发减仓: GEV 跌破 SMA50 或 AI 基建评分跌破 35")
    lines.append("")
    return "\n".join(lines)
