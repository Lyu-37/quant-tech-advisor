"""Render the analysis report — markdown to disk, summary to terminal."""
from datetime import date
from pathlib import Path

import pandas as pd

from .universe import Holding


def fmt_pct(x: float, signed: bool = True, na: str = "—") -> str:
    if x is None or pd.isna(x):
        return na
    if signed:
        return f"{x * 100:+.2f}%"
    return f"{x * 100:.2f}%"


def fmt_money(x: float) -> str:
    return f"${x:.2f}"


def _bar(score_0_10: float, width: int = 20) -> str:
    """Unicode bar for visual feel in markdown/code blocks."""
    if pd.isna(score_0_10):
        return "[ insufficient data ]"
    filled = int(round(width * score_0_10 / 10))
    return "[" + "█" * filled + "·" * (width - filled) + "]"


def render_portfolio_pnl(holdings: list[Holding]) -> str:
    lines = ["## 持仓 P/L 快照", "", "| 标的 | 股数 | 现值 (基础货币) | 本金 | 盈亏 | 盈亏% |",
             "|---|---:|---:|---:|---:|---:|"]
    total_value = total_cost = 0.0
    for h in holdings:
        lines.append(
            f"| {h.ticker} | {h.shares:.4f} | {fmt_money(h.market_value)} "
            f"| {fmt_money(h.cost_basis)} | {fmt_money(h.pnl)} | {fmt_pct(h.pnl_pct)} |"
        )
        total_value += h.market_value
        total_cost += h.cost_basis
    total_pnl = total_value - total_cost
    total_pct = total_pnl / total_cost if total_cost else 0.0
    lines.append(f"| **合计** | | **{fmt_money(total_value)}** | "
                 f"**{fmt_money(total_cost)}** | **{fmt_money(total_pnl)}** | "
                 f"**{fmt_pct(total_pct)}** |")
    lines.append("")
    return "\n".join(lines)


def render_sector_score(score) -> str:
    lines = [
        f"## 半导体板块景气评分: **{score.composite_0_100:.1f} / 100**  ({score.label})",
        "",
        "```",
        f"  趋势 (25%)    {_bar(score.sub_scores['trend'])}  "
        f"{score.sub_scores['trend']:.1f}/10  [{score.trend['label']}]",
        f"  动量 (20%)    {_bar(score.sub_scores['momentum'])}  "
        f"{score.sub_scores['momentum']:.1f}/10  [{score.momentum['label']}]",
        f"  相对强弱(15%) {_bar(score.sub_scores['rel_strength'])}  "
        f"{score.sub_scores['rel_strength']:.1f}/10  [{score.rel_strength['label']}]",
        f"  波动率 (15%)  {_bar(score.sub_scores['vol_regime'])}  "
        f"{score.sub_scores['vol_regime']:.1f}/10  [{score.vol_regime['label']}]",
        f"  宏观 (25%)    {_bar(score.sub_scores['macro'])}  "
        f"{score.sub_scores['macro']:.1f}/10  [{score.macro['label']}]",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_sector_details(score, smh_summary: dict, soxx_summary: dict) -> str:
    lines = ["## 板块技术细节", ""]
    smh_stretch = smh_summary.get("stretch", {})
    if smh_stretch.get("severity", 0) >= 2:
        lines.append(f"> **[警告] SMH 拉伸**: {smh_stretch['level']} — "
                     + "; ".join(smh_stretch.get("notes", [])))
        lines.append("")
    lines.extend(["| 指标 | SMH | SOXX |", "|---|---:|---:|"])
    rows = [
        ("现价", fmt_money(smh_summary["last"]), fmt_money(soxx_summary["last"])),
        ("距 SMA20", fmt_pct(smh_summary["dist_sma20"]),
         fmt_pct(soxx_summary["dist_sma20"])),
        ("距 SMA50", fmt_pct(smh_summary["dist_sma50"]),
         fmt_pct(soxx_summary["dist_sma50"])),
        ("距 SMA200", fmt_pct(smh_summary["dist_sma200"]),
         fmt_pct(soxx_summary["dist_sma200"])),
        ("距 52w 高", fmt_pct(smh_summary["dd_from_52w_high"]),
         fmt_pct(soxx_summary["dd_from_52w_high"])),
        ("5d 收益", fmt_pct(smh_summary["ret_5d"]),
         fmt_pct(soxx_summary["ret_5d"])),
        ("20d 收益", fmt_pct(smh_summary["ret_20d"]),
         fmt_pct(soxx_summary["ret_20d"])),
        ("60d 收益", fmt_pct(smh_summary["ret_60d"]),
         fmt_pct(soxx_summary["ret_60d"])),
        ("252d 收益", fmt_pct(smh_summary["ret_252d"]),
         fmt_pct(soxx_summary["ret_252d"])),
        ("年化波动率", fmt_pct(smh_summary["realized_vol_20d"], signed=False),
         fmt_pct(soxx_summary["realized_vol_20d"], signed=False)),
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    lines.append("")
    return "\n".join(lines)


def render_leader_ranking(leaders: list[tuple[str, dict]], rs_vs_smh: dict) -> str:
    """leaders: list of (ticker, summary_dict); rs_vs_smh: dict of ticker -> rs dict."""
    lines = ["## 龙头股健康度排名 (按 60d 相对 SMH)", "",
             "| 标的 | 现价 | 5d | 20d | 60d | 距 SMA50 | 距 52w 高 | RS vs SMH |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    # sort by 60d momentum descending
    sorted_leaders = sorted(
        leaders, key=lambda x: x[1].get("ret_60d") or -999, reverse=True
    )
    for ticker, s in sorted_leaders:
        rs = rs_vs_smh.get(ticker, {})
        rs_disp = (f"{rs.get('spread', 0) * 100:+.1f}%" if rs else "—")
        lines.append(
            f"| **{ticker}** | {fmt_money(s['last'])} | {fmt_pct(s['ret_5d'])} "
            f"| {fmt_pct(s['ret_20d'])} | {fmt_pct(s['ret_60d'])} "
            f"| {fmt_pct(s['dist_sma50'])} | {fmt_pct(s['dd_from_52w_high'])} | {rs_disp} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_amd_focus(amd_summary: dict, amd_rs_smh: dict, amd_rs_nvda: dict,
                     amd_holding: Holding | None) -> str:
    lines = ["## AMD 专项分析", ""]
    if amd_holding:
        lines.extend([
            f"你持有 **{amd_holding.shares:.4f} 股 AMD**, 本金 "
            f"{fmt_money(amd_holding.cost_basis)} → 现值 "
            f"{fmt_money(amd_holding.market_value)} (**{fmt_pct(amd_holding.pnl_pct)}**)",
            "",
        ])

    stretch = amd_summary.get("stretch", {})
    if stretch.get("severity", 0) >= 2:
        lines.append(f"> **[红色警告] AMD 拉伸**: {stretch['level']}")
        for n in stretch.get("notes", []):
            lines.append(f">  - {n}")
        lines.append("> ")
        lines.append("> _统计上极端拉伸后, 短期回踩概率显著上升 (无论基本面多好), "
                     "这是落袋为安的强信号_")
        lines.append("")

    lines.extend([
        f"- 现价: {fmt_money(amd_summary['last'])}",
        f"- 趋势: {amd_summary['trend']['label']} "
        f"(_{amd_summary['trend'].get('detail', '')}_)",
        f"- 动量: 20d 收益 {fmt_pct(amd_summary['ret_20d'])} "
        f"(分位 {amd_summary['momentum'].get('percentile', 0) * 100:.0f}%)",
        f"- 距 SMA50: {fmt_pct(amd_summary['dist_sma50'])} | "
        f"距 SMA200: {fmt_pct(amd_summary['dist_sma200'])} | "
        f"距 52w 高: {fmt_pct(amd_summary['dd_from_52w_high'])}",
        f"- 年化波动率: {fmt_pct(amd_summary['realized_vol_20d'], signed=False)}",
        f"- **AMD vs SMH (60d)**: {amd_rs_smh['label']} "
        f"({fmt_pct(amd_rs_smh.get('spread', 0))}) — "
        f"{'板块内强' if amd_rs_smh.get('spread', 0) > 0 else '板块内弱'}",
        f"- **AMD vs NVDA (60d)**: {amd_rs_nvda['label']} "
        f"({fmt_pct(amd_rs_nvda.get('spread', 0))}) — "
        f"{'对标龙头强' if amd_rs_nvda.get('spread', 0) > 0 else '对标龙头弱'}",
        "",
    ])
    return "\n".join(lines)


def render_macro_context(macro_dict: dict) -> str:
    lines = ["## 宏观环境", ""]
    if "tnx_pct" in macro_dict:
        delta = macro_dict.get("tnx_delta_60d", 0)
        direction = "↑" if delta > 0.05 else "↓" if delta < -0.05 else "→"
        lines.append(
            f"- **10y 国债**: {macro_dict['tnx_pct']:.2f}% "
            f"(60d {direction} {delta * 100:+.0f} bp) — "
            f"{'对高估值科技股压制' if macro_dict['tnx_pct'] > 4.3 else '相对友好'}"
        )
    if "dxy" in macro_dict:
        d = macro_dict.get("dxy_change_60d", 0)
        lines.append(
            f"- **美元指数 (DXY)**: {macro_dict['dxy']:.2f} "
            f"(60d {fmt_pct(d)}) — "
            f"{'美元走强压制海外营收' if d > 0.02 else '美元偏弱有利' if d < -0.02 else '稳定'}"
        )
    lines.append(f"- **综合宏观评分**: {macro_dict.get('score', 5):.1f}/10 "
                 f"({macro_dict.get('label', 'neutral')})")
    lines.append("")
    return "\n".join(lines)


def render_action_plan(score, amd_summary, amd_rs_smh, amd_rs_nvda,
                      amd_holding, portfolio_weight_amd: float,
                      gev_holding: Holding | None) -> str:
    """Conditional action recommendations — covers add / hold / take_profit / diversify."""
    lines = ["## 个性化行动建议", ""]
    composite = score.composite_0_100
    trend_label = amd_summary["trend"]["label"]
    amd_pnl_pct = amd_holding.pnl_pct if amd_holding else 0
    amd_below_sma50 = amd_summary["dist_sma50"] < 0
    amd_in_uptrend = amd_summary["dist_sma200"] > 0

    amd_stretch_sev = amd_summary.get("stretch", {}).get("severity", 0)
    lines.append(f"**整体定调** (景气评分 {composite:.0f}/100, AMD 拉伸 severity={amd_stretch_sev}):")
    if amd_stretch_sev >= 3:
        tone = ("**[警告] AMD 已极端拉伸**, 即便板块趋势仍强, "
                "**优先级是分批止盈锁定利润**, 而不是问要不要加仓")
    elif composite >= 65:
        tone = "板块有顺风。但你 AMD 重仓 (~34%) 已经 +90%, 不建议追高加仓, 重心放在锁定利润和分散"
    elif composite >= 45:
        tone = "板块中性。维持现状, 不追涨不杀跌"
    else:
        tone = "板块有压力。考虑给 AMD 设移动止损保护已实现的 +90% 浮盈"
    lines.extend([tone, ""])

    # --- Scenario A: Add more AMD ---
    lines.extend([
        "### 情境 A — 如果想加仓 AMD",
        f"- **当前不建议**: AMD 已占组合 {portfolio_weight_amd:.0%}, 单点风险过高",
        f"- 触发条件 (同时满足): 板块评分 < 50 *AND* AMD 回踩 SMA50 (现价 ~"
        f"{amd_summary['last'] / (1 + amd_summary['dist_sma50']):.0f}) *AND* AMD 组合占比降到 25% 以下",
        f"- 即便加, 单次加仓 ≤ 本金 10% (~$50 CAD)",
        "",
    ])

    # --- Scenario B: Take profit on AMD ---
    lines.extend([
        "### 情境 B — 如果想止盈 AMD",
        f"- **理由充分**: 浮盈 {fmt_pct(amd_pnl_pct)}, 占比 {portfolio_weight_amd:.0%}, 集中度高",
        f"- 建议方式: **分批止盈**, 不一次性清仓",
        f"  - 第一档: 现价或反弹时卖 1/4 (约 1 股), 套现 ~$95 CAD",
        f"  - 第二档: 跌破 SMA50 卖 1/4",
        f"  - 第三档: 跌破 SMA200 卖剩下 1/2",
        f"- 套现资金优先补 NVDA / AVGO / SMH ETF 分散半导体敞口",
        "",
    ])

    # --- Scenario C: Diversify within semi ---
    lines.extend([
        "### 情境 C — 如果想分散到其他半导体",
        f"- **优先级**: 你只有 AMD 一个半导体名字, 分散是改善风险/回报比最直接的方式",
        f"- 候选 (按当前 60d 相对强弱排序见上方表格):",
        f"  - **SMH ETF**: 一键持有 25 个半导体股, 单股风险最低, 适合 $50-100 试水",
        f"  - **NVDA**: AI 龙头, 但单价高, 用 fractional shares 可买",
        f"  - **AVGO**: 定制硅 + 网络芯片, 受益 AI 但波动小于 NVDA",
        f"- 资金来源: 从 AMD 止盈或本金外新增",
        "",
    ])

    # --- Scenario D: Sit tight ---
    lines.extend([
        "### 情境 D — 如果什么都不做",
        f"- **可接受**: 趋势 ({trend_label}) 仍在你这边",
        f"- 但必须设定**反应阈值**, 不要让 +90% 浮盈无止损暴露:",
        f"  - 软警报: AMD 跌破 SMA50 → 进入观察, 准备减仓",
        f"  - 硬触发: AMD 跌破 SMA200 ({fmt_money(amd_summary['last'] / (1 + amd_summary['dist_sma200']))}) → 至少减半仓",
        f"  - 板块触发: 评分跌破 30 → 全面回顾",
        "",
    ])

    # --- GEV note ---
    if gev_holding:
        lines.extend([
            "### 关于 GEV (AI 基建间接敞口)",
            f"- GE Vernova 是数据中心电力供应链, 与半导体 AI 主题正相关但不同步",
            f"- 当前你 GEV {fmt_pct(gev_holding.pnl_pct)}, 占比 ~9%",
            f"- 如果你看好 AI 但担心半导体估值, GEV 可以作为续命仓位, **不建议同时减 AMD 和 GEV**",
            "",
        ])

    return "\n".join(lines)


def render_caveats() -> str:
    return ("## 免责声明\n\n"
            "- 本报告基于规则化的多因子打分, **不是投资建议**\n"
            "- 数据来源 yfinance, 可能有 15-20 分钟延迟, 不适合日内交易\n"
            "- 评分模型未做样本外验证, 不能预测未来\n"
            "- 你的最终决策应该结合本报告 + 自己的风险承受度 + 你的研究\n")


def render_full_report(
    *,
    run_date: date,
    holdings: list[Holding],
    score,
    smh_summary: dict,
    soxx_summary: dict,
    leader_summaries: list[tuple[str, dict]],
    rs_vs_smh: dict,
    amd_summary: dict,
    amd_rs_smh: dict,
    amd_rs_nvda: dict,
    macro_dict: dict,
    portfolio_weight_amd: float,
) -> str:
    amd_holding = next((h for h in holdings if h.ticker == "AMD"), None)
    gev_holding = next((h for h in holdings if h.ticker == "GEV"), None)
    sections = [
        f"# 半导体行业每日决策报告\n\n**日期**: {run_date.isoformat()}  \n"
        f"**关注**: AMD (你唯一的半导体持仓, 占组合 ~{portfolio_weight_amd:.0%})\n",
        render_portfolio_pnl(holdings),
        render_sector_score(score),
        render_sector_details(score, smh_summary, soxx_summary),
        render_leader_ranking(leader_summaries, rs_vs_smh),
        render_amd_focus(amd_summary, amd_rs_smh, amd_rs_nvda, amd_holding),
        render_macro_context(macro_dict),
        render_action_plan(score, amd_summary, amd_rs_smh, amd_rs_nvda,
                           amd_holding, portfolio_weight_amd, gev_holding),
        render_caveats(),
    ]
    return "\n".join(sections)


def save_report(report_md: str, run_date: date, base_dir: Path) -> Path:
    out_dir = base_dir / "results" / "sector_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_date.isoformat()}-semi.md"
    path.write_text(report_md, encoding="utf-8")
    return path
