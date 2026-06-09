"""Tech-sector daily scanner: build the Discord embed payload.

No personal holdings appear in the scanner output — it's a market-state view.
Personal portfolio analysis stays in the local markdown report.
"""
import pandas as pd

from .universe import HOT_TECH, MOONSHOT_LEADERS, categorize_hot_tech
from .indicators import summarize_ticker
from .news import TickerNewsSummary, category_breakdown
from .events import EarningsEvent
from .levels import ActionLevels
from .recommendations import (
    TickerRecommendation, render_recommendations_for_embed,
)
from .weekly_theme import get_today_theme
from .daily_state import render_diff_for_embed
from .portfolio_daily import HoldingPnL, render_portfolio_pnl_field
from .market_movers import MarketMovers, render_movers_field
from .watchlist import WatchVerdict, render_watchlist_field
from .discord_push import (
    score_to_color, COLOR_GREEN, COLOR_YELLOW, COLOR_RED,
)


def _fmt_pct(x: float, sign: bool = True) -> str:
    if x is None or pd.isna(x):
        return "—"
    return (f"{x * 100:+.0f}%" if sign else f"{x * 100:.0f}%")


def _build_sector_tilts(summaries: dict[str, dict]) -> list[str]:
    """Pair-trade / relative-value narrative within each major sub-sector.

    For each pre-defined comparison pair, pick the side with better (Q-R) balance
    using 12-1 momentum + distance to SMA200 (less is better) + 20d return.
    Returns one-line strings of the form '半导体: 优选 NVDA > AMD (理由)'.
    """
    pairs = [
        ("半导体 (AI 算力)", ["NVDA", "AMD", "AVGO", "TSM", "MU"]),
        ("AI 基建 (数据中心)", ["VRT", "GEV", "PWR", "ETN", "ANET"]),
        ("Mag 7 (大科技)", ["AAPL", "AMZN", "GOOG", "MSFT", "META", "TSLA"]),
        ("量子计算", ["IONQ", "QUBT", "RGTI", "QBTS"]),
        ("光通信", ["LITE", "COHR", "CIEN", "FN", "AAOI"]),
    ]
    lines = []
    for label, tickers in pairs:
        scored = []
        for t in tickers:
            s = summaries.get(t)
            if not s:
                continue
            # Asymmetry score: prefer high 12-1 + low stretch + recent ret>0
            m121 = (s.get("momentum_12_1", {}).get("value") or 0)
            dist200 = (s.get("dist_sma200") or 0)
            ret20 = (s.get("ret_20d") or 0)
            sev = s.get("stretch", {}).get("severity", 0)
            asym = m121 * 0.5 - max(0, dist200 - 0.3) * 1.0 + ret20 * 0.3 - sev * 0.05
            scored.append((t, asym, s))
        if len(scored) < 2:
            continue
        scored.sort(key=lambda x: -x[1])
        winner, _, win_s = scored[0]
        loser, _, lose_s = scored[-1]
        if winner == loser:
            continue
        # Build comparison rationale
        w_d200 = (win_s.get("dist_sma200") or 0) * 100
        l_d200 = (lose_s.get("dist_sma200") or 0) * 100
        if abs(w_d200) < abs(l_d200):
            reason = (f"距 SMA200: {winner} +{w_d200:.0f}% vs {loser} +{l_d200:.0f}% · "
                      f"前者更安全")
        else:
            w_m = (win_s.get("momentum_12_1", {}).get("value") or 0) * 100
            l_m = (lose_s.get("momentum_12_1", {}).get("value") or 0) * 100
            reason = f"12-1 动量: {winner} {w_m:+.0f}% vs {loser} {l_m:+.0f}%"
        lines.append(
            f"▶  **{label}**\n"
            f"     优选 `{winner}` ↑  vs  `{loser}` ↓\n"
            f"     _{reason}_"
        )
    return lines


def _table_block(rows: list[tuple], headers: tuple, col_widths: tuple) -> str:
    """Build a monospace code-block table for Discord field values."""
    lines = []
    fmt = "".join(f"{{:<{w}}}" for w in col_widths)
    lines.append(fmt.format(*headers))
    lines.append(fmt.format(*("─" * (w - 1) for w in col_widths)))
    for r in rows:
        lines.append(fmt.format(*[str(c) for c in r]))
    return "```\n" + "\n".join(lines) + "\n```"


# Moonshot themes for display tagging (compact)
MOONSHOT_THEME = {
    "IONQ": "量子 (你持有)", "RGTI": "量子", "QBTS": "量子",
    "QUBT": "量子", "ARQQ": "量子",
    "RKLB": "太空", "ASTS": "太空", "ACHR": "eVTOL", "JOBY": "eVTOL",
    "NNE": "小核电", "LTBR": "小核电", "ASPI": "同位素",
    "BBAI": "国防 AI", "AVAV": "国防 AI", "KTOS": "国防 AI",
    "SOUN": "AI 语音", "PATH": "AI 自动化", "AI": "企业 AI",
    "CRSP": "基因编辑", "NTLA": "基因编辑", "BEAM": "基因编辑",
    "ENVX": "电池", "QS": "电池",
    "MARA": "BTC 矿", "CLSK": "BTC 矿",
}


def moonshot_score(summary: dict, news_summary=None) -> dict:
    """Score for 10x candidates (different from main Q/R matrix).

    Moonshots are explicitly low-quality by QMJ standards. Don't use the same
    framework. Instead, score on:
      - Recent momentum (acceleration is the signal)
      - Room to run (NOT extreme stretch — already 10x'd has no upside)
      - News catalyst (positive sentiment recent)
      - Not in a death spiral (12-1 momentum not severely negative)

    Returns dict with score 0-100, label, and risk flags.
    """
    if not summary or summary.get("ret_60d") is None:
        return {"score": 0, "label": "no_data", "risk_flags": ["no_data"]}

    # Reward signals (0-10 each)
    ret_60d = summary["ret_60d"]
    ret_20d = summary.get("ret_20d") or 0
    # Bonus for accelerating: 20d > 60d/3 means recent acceleration
    accel = ret_20d - ret_60d / 3
    accel_pts = float(max(0, min(10, accel * 50 + 5)))  # +10% spread = 10

    # Recent return: positive = good, but capped (extreme already moved)
    ret_pts = float(max(0, min(10, ret_60d * 30 + 3)))  # +30% in 60d = 12 capped to 10

    # Room to run (no extreme stretch)
    sev = summary.get("stretch", {}).get("severity", 0)
    room_pts = float(max(0, 10 - sev * 2.5))  # 0 stretch = 10, 4 = 0

    # 12-1 momentum: penalty if deeply negative (in death spiral)
    m121 = summary.get("momentum_12_1", {}).get("value")
    m121_pts = 5.0
    if m121 is not None:
        if m121 < -0.50:
            m121_pts = 0.0      # down 50%+ over 12-1 = dying
        elif m121 < -0.20:
            m121_pts = 3.0
        elif m121 > 0.20:
            m121_pts = 8.0      # already moving = catalysts working
        elif m121 > 0.50:
            m121_pts = 10.0

    # News sentiment bonus
    news_pts = 5.0
    if news_summary and news_summary.headlines:
        avg = news_summary.avg_score
        news_pts = float(max(0, min(10, 5 + avg * 5)))  # -1..+1 → 0..10

    # Composite (weighted)
    composite = (
        accel_pts * 0.25
        + ret_pts * 0.20
        + room_pts * 0.20
        + m121_pts * 0.20
        + news_pts * 0.15
    )

    # Risk flags
    risk_flags = []
    vol = summary.get("realized_vol_20d", 0)
    if vol and vol > 1.0:
        risk_flags.append(f"vol {vol * 100:.0f}%")
    if sev >= 3:
        risk_flags.append("已过热")
    if m121 is not None and m121 < -0.50:
        risk_flags.append(f"12-1 {m121 * 100:.0f}%")
    if summary.get("dd_from_52w_high", 0) < -0.40:
        risk_flags.append(f"距52w高{summary['dd_from_52w_high'] * 100:.0f}%")

    if composite >= 7:
        label = "热度高"
    elif composite >= 5:
        label = "值得观察"
    elif composite >= 3:
        label = "弱信号"
    else:
        label = "回避"

    return {
        "score": float(composite * 10),
        "label": label,
        "accel": float(accel),
        "risk_flags": risk_flags,
    }


def rank_moonshots(
    summaries: dict[str, dict],
    news_summaries: dict | None = None,
) -> list[dict]:
    """Score all moonshot candidates, return sorted list."""
    news_summaries = news_summaries or {}
    out = []
    for t in MOONSHOT_LEADERS:
        s = summaries.get(t)
        if not s:
            continue
        sc = moonshot_score(s, news_summaries.get(t))
        out.append({
            "ticker": t,
            "theme": MOONSHOT_THEME.get(t, ""),
            "summary": s,
            "score": sc["score"],
            "label": sc["label"],
            "risk_flags": sc["risk_flags"],
            "accel": sc.get("accel", 0),
        })
    out.sort(key=lambda x: -x["score"])
    return out


def build_scanner_embed(
    *,
    run_date,
    data: dict[str, pd.DataFrame],
    semi_score,
    ai_infra_report,
    news_summaries: dict[str, TickerNewsSummary],
    earnings_events: list[EarningsEvent] | None = None,
    action_levels: list[ActionLevels] | None = None,
    themes: list[dict] | None = None,
    recommendations: list[TickerRecommendation] | None = None,
    speculation_budget: float = 150.0,
    diff: dict | None = None,
    portfolio_pnl: list[HoldingPnL] | None = None,
    market_movers: MarketMovers | None = None,
    watch_verdicts: list[WatchVerdict] | None = None,
    regime=None,
    rs_leaders: list[tuple] | None = None,
    analyst_field: dict | None = None,
    valuation_field: dict | None = None,
) -> dict:
    """Return kwargs dict ready for discord_push.send_embed."""
    earnings_events = earnings_events or []
    action_levels = action_levels or []
    themes = themes or []
    recommendations = recommendations or []
    portfolio_pnl = portfolio_pnl or []
    watch_verdicts = watch_verdicts or []
    today_theme = get_today_theme(run_date)
    # ----- Hot tech summaries -----
    summaries = {}
    for t in HOT_TECH:
        if t not in data or data[t].empty:
            continue
        summaries[t] = summarize_ticker(data[t]["close"])

    # ----- Sector-level momentum aggregates -----
    # Use 126d (~6 month) momentum per academic sector rotation research:
    # Top 3 sectors by 6m momentum returned 13.7% annualized 1999-2024 vs SPY 10.1%
    # (vs 60d which is noisier and tilts to short-term reversal).
    sector_mom = {}   # category -> [(ticker, ret_126d), ...]
    for t, s in summaries.items():
        cat = categorize_hot_tech(t)
        if cat in {"Sector ETF", "Leveraged ETF", "Other"}:
            continue
        # Prefer 126d (6m) over 60d; fall back to 60d if 126d unavailable
        ret = s.get("ret_126d") if s.get("ret_126d") is not None else s.get("ret_60d")
        if ret is None or pd.isna(ret):
            continue
        sector_mom.setdefault(cat, []).append((t, ret))

    sector_summary = []
    for cat, members in sector_mom.items():
        avg = sum(r for _, r in members) / len(members)
        top_t, top_r = max(members, key=lambda x: x[1])
        worst_t, worst_r = min(members, key=lambda x: x[1])
        sector_summary.append({
            "cat": cat, "avg_60d": avg, "n": len(members),
            "leader": top_t, "leader_ret": top_r,
            "laggard": worst_t, "laggard_ret": worst_r,
        })
    sector_summary.sort(key=lambda s: -s["avg_60d"])

    # ----- Winners (top 60d) -----
    by_60d = sorted(
        [(t, s) for t, s in summaries.items() if s["ret_60d"] is not None],
        key=lambda x: x[1]["ret_60d"], reverse=True
    )
    winners = by_60d[:8]
    laggards = list(reversed(by_60d[-6:]))   # weakest first

    # ----- Stretch warnings -----
    stretched = sorted(
        [(t, s) for t, s in summaries.items()
         if s.get("stretch", {}).get("severity", 0) >= 2],
        key=lambda x: (
            -x[1]["stretch"]["severity"],
            -x[1]["dist_sma200"],
        ),
    )[:8]

    # ----- 20d momentum reversals (recent acceleration / deceleration) -----
    # Find names where 20d return >> 60d/3 (accelerating) or vice versa
    accel = []
    for t, s in summaries.items():
        if s["ret_60d"] and s["ret_20d"]:
            implied = s["ret_60d"] / 3
            if s["ret_20d"] - implied > 0.10:   # accelerating
                accel.append((t, s, s["ret_20d"] - implied))
    accel.sort(key=lambda x: -x[2])
    accel = accel[:5]

    # ----- News sentiment ranking -----
    news_rank = []
    for t, n in news_summaries.items():
        if n.headlines:
            news_rank.append((t, n.label, n.avg_score, len(n.headlines)))
    news_rank.sort(key=lambda x: -x[2])

    # ----- Macro snapshot -----
    macro = semi_score.macro

    # ----- Description (plain-Chinese narrative) -----
    composite = semi_score.composite_0_100
    ai_score = ai_infra_report.composite_0_100

    # Market pulse — read TODAY's actual SPY/QQQ/VIX moves. This is the real
    # "today's temperature", separate from the slow sector composite score.
    def _today_move(ticker: str):
        df = data.get(ticker)
        if df is None or df.empty or len(df) < 2:
            return None
        return float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)

    spy_chg = _today_move("SPY")
    qqq_chg = _today_move("QQQ")
    vix_df = data.get("^VIX")
    vix_level = float(vix_df["close"].iloc[-1]) if vix_df is not None and not vix_df.empty else None
    vix_chg = _today_move("^VIX")

    # Classify the day from real data
    worst = min([x for x in [spy_chg, qqq_chg] if x is not None], default=0)
    best = max([x for x in [spy_chg, qqq_chg] if x is not None], default=0)
    vix_spike = (vix_chg is not None and vix_chg > 0.15) or (vix_level is not None and vix_level > 25)

    if worst <= -0.02 or vix_spike:
        pulse = "系统性大跌"
    elif worst <= -0.008:
        pulse = "回调下行"
    elif best >= 0.008 and worst >= -0.003:
        pulse = "普涨"
    elif best - worst >= 0.015:
        pulse = "板块分化 (有涨有跌)"
    else:
        pulse = "窄幅整理"

    # Build the real one-line pulse with numbers
    parts = []
    if spy_chg is not None:
        parts.append(f"SPY {spy_chg * 100:+.1f}%")
    if qqq_chg is not None:
        parts.append(f"QQQ {qqq_chg * 100:+.1f}%")
    if vix_level is not None:
        v_arrow = "↑" if (vix_chg or 0) > 0 else "↓"
        parts.append(f"VIX {vix_level:.0f}{v_arrow}")
    pulse_numbers = "  ".join(parts)

    macro_warning = ""
    if "tnx_pct" in macro:
        tnx_pct = macro["tnx_pct"]
        if tnx_pct > 4.3:
            macro_warning = f" · 10y 利率 {tnx_pct:.2f}% 偏高, 压制高估值股"
        elif tnx_pct < 3.8:
            macro_warning = f" · 10y 利率 {tnx_pct:.2f}% 偏低, 利好成长股"
        else:
            macro_warning = f" · 10y 利率 {tnx_pct:.2f}%"

    n_stretched = len(stretched)

    # Count recommendations by action
    rec_counts = {}
    for r in recommendations:
        rec_counts[r.action] = rec_counts.get(r.action, 0) + 1
    n_buy = (rec_counts.get("建仓", 0) + rec_counts.get("加仓持有", 0)
             + rec_counts.get("试探建仓", 0))
    n_sell = rec_counts.get("减仓", 0) + rec_counts.get("清仓", 0)
    n_avoid = rec_counts.get("避免", 0)

    desc = (
        f"**【{today_theme['weekday_name']}主题: {today_theme['name']}】**\n"
        f"_{today_theme['focus']}_\n\n"
        f"**今日大盘: {pulse}**   {pulse_numbers}{macro_warning}\n"
        f"扫描 {len(summaries)} 只热门科技股 · {n_stretched} 只过热 · "
        f"**{n_buy} 买 / {n_sell} 卖 / {n_avoid} 避**\n\n"
        f"_Q: 越高越好 (技术 + 动量 + 同行)_ · "
        f"_R: 越高越危险 (估值 + 高位 + 波动)_"
    )

    # ----- Fields -----
    fields = []

    # Removed [$$] 持仓今日 P&L: 用户每周都在加仓, 维护 portfolio.yaml
    # 麻烦, 不如不放. 个人 P&L 看 Wealthsimple app 即可.
    # portfolio_daily 模块仍保留供本地报告使用.

    # ===== 顶部第一: 市场体制闸门 (决定今天该不该买) =====
    if regime is not None:
        from .regime import render_regime_field
        fields.append(render_regime_field(regime, rs_leaders))

    # ===== 第二: 你的回调买点 watchlist (最个性化+可操作) =====
    watch_field = render_watchlist_field(watch_verdicts)
    if watch_field:
        fields.append(watch_field)

    # ===== 第二: 今日实际异动 (实际涨跌 / 关键位破位 / 52w 极值) =====
    if market_movers is not None:
        mv_field = render_movers_field(market_movers)
        if mv_field:
            fields.append(mv_field)

    # ===== 第二: 昨日 → 今日 diff (评级变化) =====
    diff_field = render_diff_for_embed(diff) if diff is not None else None
    if diff_field:
        fields.append(diff_field)

    # Extract new-entry sets from diff for use in recommendation rendering
    new_in_buy_set = set()
    new_in_sell_set = set()
    new_stretched_set = set()
    if diff and not diff.get("first_run"):
        new_in_buy_set = {x["ticker"] for x in diff.get("new_in_buy", [])}
        new_in_sell_set = {x["ticker"] for x in diff.get("new_in_sell", [])}
        new_stretched_set = set(diff.get("new_stretched", []))

    # ===== 然后: Top 3 高信号买入卡 (含目标价 + 上行空间) =====
    fields.extend(render_recommendations_for_embed(
        recommendations, speculation_budget,
        action_levels=action_levels,
        new_in_buy_tickers=new_in_buy_set,
    ))

    # ===== 分析师共识 (华尔街目标价 + 空间) =====
    if analyst_field:
        fields.append(analyst_field)

    # ===== 估值 (PEG 成长调整) =====
    if valuation_field:
        fields.append(valuation_field)

    # ===== 10x 候选 (单独评分体系, 主推荐之后) =====
    moonshots = rank_moonshots(summaries, news_summaries)
    top_moonshots = [m for m in moonshots if m["score"] >= 50][:5]
    if top_moonshots:
        rows = []
        for m in top_moonshots:
            s = m["summary"]
            flags = "/".join(m["risk_flags"][:2]) if m["risk_flags"] else ""
            rows.append((
                m["ticker"],
                m["theme"][:7],
                _fmt_pct(s["ret_60d"]),
                f"{m['score']:.0f}",
                m["label"][:8],
                flags[:18],
            ))
        fields.append({
            "name": "[M] 10x 候选 (冷门小盘 · 单独评分 · 极高波动)",
            "value": (_table_block(rows,
                                   ("Ticker", "主题", "60d", "分", "档", "警示"),
                                   (8, 8, 7, 5, 9, 19))
                      + "_这些标的: 高赔率 / 高失败率, 单笔 $30-50 试水, 不超过组合 5%_"),
            "inline": False,
        })

    # Removed [T2] 板块相对优选 + [S] 板块 6 月动量热度:
    # both change slowly across days (sectors don't rotate fast), low daily
    # signal value. Same info still available in the local markdown report.

    # Note: stretched warnings live ONLY in the diff field at the top now.
    # No standalone field — eliminates the "AMD 4/4 every day" problem.
    # Full stretched list still appears in the local markdown report.

    # Acceleration: compact, backtick tickers, arrow indicator
    if accel:
        accel_line = "  ·  ".join(
            f"`{t}` ↑ 20d **{s['ret_20d'] * 100:+.0f}%** / 60d {s['ret_60d'] * 100:+.0f}%"
            for t, s, _ in accel[:4]
        )
        fields.append({
            "name": "[*] 短期动能拐点  ·  近 20d 加速",
            "value": accel_line,
            "inline": False,
        })

    # News sentiment: compact top 4 with direction indicator
    if news_rank:
        def _arrow(score: float) -> str:
            return "▲" if score > 0.2 else "▼" if score < -0.2 else "◆"
        compact_news = "  ·  ".join(
            f"{_arrow(score)} `{t}` **{score:+.2f}**"
            for t, label, score, n in news_rank[:5]
        )
        fields.append({
            "name": "[~] 新闻情绪  ·  近 7 天",
            "value": compact_news,
            "inline": False,
        })

    # Upcoming earnings — show urgency markers, not code-block table (cleaner)
    if earnings_events:
        lines = []
        for e in earnings_events[:6]:
            marker = "▲" if e.days_until <= 7 else "○"
            urgency = "  ★ 本周事件" if e.days_until <= 7 else ""
            lines.append(
                f"{marker}  `{e.ticker}`  ·  {e.report_date.isoformat()}  "
                f"·  距今 **{e.days_until} 天**{urgency}"
            )
        fields.append({
            "name": "[$] 财报日历  ·  未来 21 天  ·  高事件风险",
            "value": "\n".join(lines),
            "inline": False,
        })

    # Themes (rule-based keyword clustering)
    if themes:
        dir_map = {"bullish": "▲", "bearish": "▼", "mixed": "◆"}
        theme_lines = []
        for th in themes[:5]:
            d = dir_map.get(str(th.get("direction", "")).lower(), "◆")
            tickers = th.get("tickers", []) or []
            tkrs = "  ".join(f"`{t}`" for t in tickers[:5])
            text = th.get("theme", "")[:140]
            theme_lines.append(f"{d}  **{text}**\n    {tkrs}")
        fields.append({
            "name": "[T] 当日跨股票主题  ·  关键词聚类",
            "value": "\n\n".join(theme_lines),
            "inline": False,
        })

    # Removed: [L] action levels table — too static (same prices day to day),
    # not actionable for daily reader. Top 3 cards already include target/stop
    # for the highest-conviction picks. Action levels still in local report.

    # Today's takeaway (one paragraph)
    takeaway = []
    if composite < 35:
        takeaway.append("板块走弱, 不宜追高新仓位.")
    elif composite >= 65 and n_stretched >= 5:
        takeaway.append("板块强但多只龙头极端拉伸, 优先获利了结而非加仓.")
    elif composite >= 65:
        takeaway.append("板块顺风, 可选择性配置, 但仍需各股拉伸检查.")
    else:
        takeaway.append("板块中性, 关注分化机会.")

    # Pick a laggard with low stretch as rotation idea
    laggard_with_room = next(
        (t for t, s in laggards
         if s.get("stretch", {}).get("severity", 0) <= 1
         and s["dist_sma200"] is not None and 0 < s["dist_sma200"] < 0.40),
        None
    )
    if laggard_with_room:
        takeaway.append(f"若想在主题内继续暴露, **{laggard_with_room}** 是数据上"
                        "最干净的滞涨/低拉伸候选.")

    if takeaway:
        fields.append({
            "name": "→ 今日核心结论",
            "value": " ".join(takeaway),
            "inline": False,
        })

    return {
        "title": (f"科技板块扫描 · {run_date.isoformat()} · "
                  f"{today_theme['weekday_name']} · {today_theme['name']}"),
        "description": desc,
        "fields": fields,
        "color": score_to_color(composite),
        "footer": "数据 yfinance · 完整报告已保存本地 · 蒙特利尔 ET",
    }
