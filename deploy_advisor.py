"""Where-to-deploy advisor — ranks candidates for a speculative cash bucket.

The user's framing: $150 CAD from AMD profit ("house money"), wants exposure
to AI/semi/tech upside, accepts high downside. The question is not "which is
safe" — it's "which gives the best risk-adjusted reward given current data".

Methodology per candidate:
  - 60d / 20d return  (recent strength)
  - stretch severity  (penalty for parabolic moves)
  - realized vol      (downside intensity)
  - beta vs SMH       (proxy for sector dependency)
  - asymmetry score   ((1y range position) — high = bought near top)

Final score combines: reward (momentum + beta) - risk (stretch + asymmetry).
"""
from datetime import date
from pathlib import Path
import argparse
import os
import numpy as np
import pandas as pd

from src.advisor.fetcher import fetch_universe
from src.advisor.indicators import summarize_ticker
from src.advisor.discord_push import send_embed, score_to_color


PROJECT_ROOT = Path(__file__).resolve().parent

# Candidates for $150 speculative bucket. Tiered by risk profile.
CANDIDATES = {
    # Tier 1: Pure leverage (highest beta, highest decay)
    "SOXL": ("Direxion 3x Semi Bull",
             "3x leverage, vol drag, user's pick"),
    "TQQQ": ("ProShares 3x Nasdaq",
             "3x Nasdaq, broader than SOXL"),
    "TECL": ("Direxion 3x Tech Bull",
             "3x XLK (broader tech, less semi-concentrated)"),

    # Tier 2: Unleveraged ETFs (basket exposure)
    "SMH":  ("VanEck Semi 1x",
             "1x semi basket - same theme as SOXL without leverage"),
    "SOXX": ("iShares Semi 1x",
             "alt 1x semi basket"),
    "XLK":  ("SPDR Tech Sector 1x",
             "broad tech, less concentrated"),

    # Tier 3: Single names within AI/semi (relative-value plays)
    "NVDA": ("Nvidia",
             "AI king but 60d laggard relative to AMD/INTC"),
    "AVGO": ("Broadcom",
             "custom AI silicon, strong narrative, moderate stretch"),
    "ASML": ("ASML",
             "EUV monopoly, 60d laggard"),
    "TSM":  ("Taiwan Semi",
             "foundry leader, 60d laggard"),

    # Tier 4: AI infra (parallel theme, sometimes less correlated to semi)
    "PWR":  ("Quanta Services",
             "AI power infra #1"),
    "VRT":  ("Vertiv",
             "datacenter cooling+power"),

    # Tier 5: Anti-correlated / wildcards
    "ARM":  ("ARM Holdings",
             "chip IP, AI exposure with different beta profile"),
    "SMCI": ("Super Micro",
             "AI server maker, extreme vol, history of crashes"),
}


def calc_beta(asset: pd.Series, benchmark: pd.Series, window: int = 60) -> float:
    """Beta of asset vs benchmark over `window` daily returns."""
    a = asset.pct_change().dropna().tail(window)
    b = benchmark.pct_change().dropna().tail(window)
    n = min(len(a), len(b))
    if n < 20:
        return float("nan")
    a, b = a.tail(n).values, b.tail(n).values
    var_b = np.var(b)
    if var_b == 0:
        return float("nan")
    return float(np.cov(a, b)[0, 1] / var_b)


def position_in_range(close: pd.Series, window: int = 252) -> float:
    """Where is current price within the trailing range? 0 = low, 1 = high."""
    w = close.tail(window)
    lo, hi = w.min(), w.max()
    if hi == lo:
        return 0.5
    return float((close.iloc[-1] - lo) / (hi - lo))


def evaluate_candidate(close: pd.Series, smh_close: pd.Series) -> dict:
    """Compute the full metric bundle for one candidate."""
    summary = summarize_ticker(close)
    beta = calc_beta(close, smh_close)
    range_pos = position_in_range(close)

    # Reward signal (0-10): momentum percentile + relative laggardness within tier
    reward = (
        summary["momentum"]["score"] * 0.5            # recent momentum
        + (5 - min(5, abs(beta - 1.5) * 2))            # 0-5 — closer to beta 1.5 better for speculation
    )

    # Risk signal (0-10): stretch + range position + vol
    sev = summary.get("stretch", {}).get("severity", 0)
    vol = summary.get("realized_vol_20d", 0.4)
    risk = (
        sev * 1.5                  # 0-6 from stretch
        + range_pos * 3            # 0-3 from being at top of range
        + min(2, vol * 3)          # 0-2 from vol
    )
    # Asymmetry: reward / risk. Higher = better deal.
    asym = reward - risk / 2

    return {
        "summary": summary,
        "beta_vs_smh": beta,
        "range_pos": range_pos,
        "reward": float(reward),
        "risk": float(risk),
        "asymmetry": float(asym),
    }


def render_table(results: dict[str, dict]) -> str:
    """Markdown table sorted by asymmetry desc."""
    rows = sorted(results.items(), key=lambda kv: kv[1]["asymmetry"], reverse=True)
    lines = [
        "| 排名 | 标的 | 60d | 距 SMA200 | β vs SMH | 范围位置 | 拉伸 | 回报-风险 | 一句话 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, (ticker, r) in enumerate(rows, 1):
        s = r["summary"]
        sev = s.get("stretch", {}).get("severity", 0)
        sev_disp = "▮" * sev + "▯" * (4 - sev)
        rng = f"{r['range_pos'] * 100:.0f}%"
        beta = f"{r['beta_vs_smh']:.2f}" if not np.isnan(r["beta_vs_smh"]) else "—"
        _, blurb = CANDIDATES.get(ticker, ("", ""))
        lines.append(
            f"| {i} | **{ticker}** | {s['ret_60d'] * 100:+.0f}% "
            f"| {s['dist_sma200'] * 100:+.0f}% | {beta} | {rng} "
            f"| {sev_disp} | {r['asymmetry']:+.2f} | {blurb} |"
        )
    return "\n".join(lines)


def make_recommendation_block(results: dict[str, dict], amount: float) -> str:
    """Top-3 narrative + verdict."""
    rows = sorted(results.items(), key=lambda kv: kv[1]["asymmetry"], reverse=True)
    top3 = rows[:3]
    lines = [
        f"## 数据告诉我的事 (${amount:.0f} CAD 投机仓位)\n",
        "**排名指标**: `回报 (动量 + 适度 β) − 风险 (拉伸 + 范围位置 + 波动率) ÷ 2`",
        "数字越高 = 当前数据下风险/回报越好。\n",
    ]

    lines.append("### Top 3 候选\n")
    for i, (t, r) in enumerate(top3, 1):
        s = r["summary"]
        sev = s.get("stretch", {}).get("severity", 0)
        _, blurb = CANDIDATES.get(t, ("", ""))
        lines.extend([
            f"**#{i} {t}** (asymmetry = {r['asymmetry']:+.2f})",
            f"- 60d 收益 {s['ret_60d'] * 100:+.0f}%, 距 SMA200 {s['dist_sma200'] * 100:+.0f}%",
            f"- 拉伸严重度 {sev}/4, 范围位置 {r['range_pos'] * 100:.0f}%, β vs SMH = {r['beta_vs_smh']:.2f}"
            if not np.isnan(r["beta_vs_smh"]) else
            f"- 拉伸严重度 {sev}/4, 范围位置 {r['range_pos'] * 100:.0f}%",
            f"- _{blurb}_",
            "",
        ])

    # Compare user's pick (SOXL) to top
    soxl_rank = next(i for i, (t, _) in enumerate(rows, 1) if t == "SOXL")
    soxl_data = results["SOXL"]
    top_t = rows[0][0]
    lines.extend([
        "### 跟你的选择 (SOXL) 对比\n",
        f"- SOXL 排名 **第 {soxl_rank}/{len(rows)}**, asymmetry = {soxl_data['asymmetry']:+.2f}",
        f"- 第 1 名是 **{top_t}** (asymmetry = {rows[0][1]['asymmetry']:+.2f})",
        f"- 差距 = {rows[0][1]['asymmetry'] - soxl_data['asymmetry']:+.2f}",
        "",
    ])

    if soxl_rank > 5:
        lines.append("**结论**: 数据上你的 SOXL 选择并不在前列, "
                     f"主要因为它范围位置 {soxl_data['range_pos'] * 100:.0f}% "
                     "(几乎在 52w 高), 拉伸严重, β > 2。换句话说: 你买的是杠杆+在最高位的组合。")
    elif soxl_rank <= 3:
        lines.append("**结论**: SOXL 出现在前 3 名 - 数据上你的直觉有数字支持。")
    else:
        lines.append(f"**结论**: SOXL 在中位 (第 {soxl_rank} 名), 不算最差, 但有比它更好的选择。")
    lines.append("")

    # Two scenarios
    lines.extend([
        "### 同样 $150, 两种思路\n",
        f"**Plan A (追求最大潜在收益)**: 全部 {top_t}",
        f"- 集中赌一个标的, 上限高, 下限也最深",
        "- 适合: 真 house-money 心态, 输了不影响生活",
        "",
        "**Plan B (相同主题但分散)**: $75 #1 + $75 #2",
        f"- 比如 ${rows[0][0]} $75 + ${rows[1][0]} $75",
        f"- 单标的风险减半, 但仍有杠杆/高 β 暴露",
        "",
        "**Plan C (保守版投机)**: $150 进 SMH (1x 半导体基金)",
        f"- 牺牲 2/3 杠杆收益换取 2/3 下行保护",
        f"- 仍在你 AMD 卖出的同一主题, 但风险/回报更对称",
        "",
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount", type=float, default=150.0,
                        help="amount to deploy in CAD (default 150)")
    parser.add_argument("--push", action="store_true",
                        help="push to Discord webhook")
    args = parser.parse_args()

    tickers = list(CANDIDATES.keys()) + ["SMH"]  # ensure SMH for beta
    tickers = sorted(set(tickers))
    print(f"Evaluating {len(CANDIDATES)} candidates for ${args.amount:.0f} CAD bucket...")
    data = fetch_universe(tickers, lookback_days=400)

    if "SMH" not in data:
        raise RuntimeError("SMH data missing — cannot compute beta")
    smh_close = data["SMH"]["close"]

    results = {}
    for t in CANDIDATES:
        if t not in data:
            print(f"  ! skip {t}: no data")
            continue
        results[t] = evaluate_candidate(data[t]["close"], smh_close)

    # Render report
    md_lines = [
        f"# $150 投机仓位选择评估\n",
        f"**评估日期**: {date.today().isoformat()}",
        f"**资金**: ${args.amount:.0f} CAD (你说的 house money)",
        f"**评估标的**: {len(results)} 个 (3x 杠杆 / 1x ETF / 单股 / AI 基建)\n",
        "## 完整排名 (按 asymmetry 降序)\n",
        render_table(results),
        "",
        make_recommendation_block(results, args.amount),
        "## 免责声明",
        "",
        "本评估基于纯量化打分, 不考虑事件驱动 (财报、Fed、地缘)。",
        "_House money_ 心态合理但要承认: 输了 100% 仍是真实的 $150。",
        "数据来源 yfinance, 15-20 分钟延迟, 不适合日内决策。",
    ]
    md = "\n".join(md_lines)

    out_dir = PROJECT_ROOT / "results" / "deploy_advisor"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date.today().isoformat()}-deploy_150.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"\nSaved: {out_path}")

    # Console summary
    rows = sorted(results.items(), key=lambda kv: kv[1]["asymmetry"], reverse=True)
    print("\n" + "=" * 70)
    print(f"TOP 5 for ${args.amount:.0f} CAD speculative bucket")
    print("=" * 70)
    for i, (t, r) in enumerate(rows[:5], 1):
        s = r["summary"]
        print(f"  #{i} {t:>5}  asym {r['asymmetry']:+.2f}  "
              f"60d {s['ret_60d'] * 100:+.0f}%  "
              f"距 SMA200 {s['dist_sma200'] * 100:+.0f}%  "
              f"β {r['beta_vs_smh']:.2f}")
    soxl_rank = next(i for i, (t, _) in enumerate(rows, 1) if t == "SOXL")
    print(f"\n  SOXL ranked: #{soxl_rank}/{len(rows)}")

    if args.push:
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if not url:
            print("  ! no DISCORD_WEBHOOK_URL set, skipping push")
        else:
            # Build embed fields without leaking position size or holdings
            top_rows = [
                (f"#{i}", t,
                 f"{r['asymmetry']:+.2f}",
                 f"{r['summary']['ret_60d'] * 100:+.0f}%",
                 f"{r['summary']['dist_sma200'] * 100:+.0f}%",
                 f"{r['beta_vs_smh']:.2f}" if not np.isnan(r["beta_vs_smh"]) else "—")
                for i, (t, r) in enumerate(rows[:8], 1)
            ]
            top_table = (
                "```\n" +
                f"{'#':<3}{'Ticker':<7}{'asym':<8}{'60d':<7}{'vs200':<8}{'β':<5}\n" +
                "─" * 38 + "\n" +
                "\n".join("".join(c.ljust(w) for c, w in
                                  zip(row, [3, 7, 8, 7, 8, 5]))
                          for row in top_rows) +
                "\n```"
            )

            soxl_r = results.get("SOXL", {})
            soxl_line = (f"SOXL 排名 **#{soxl_rank}/{len(rows)}**, "
                         f"asym {soxl_r.get('asymmetry', 0):+.2f}, "
                         f"β {soxl_r.get('beta_vs_smh', 0):.2f}, "
                         f"距 SMA200 {soxl_r.get('summary', {}).get('dist_sma200', 0) * 100:+.0f}%"
                         if soxl_r else "")

            t1, t2 = rows[0][0], rows[1][0]
            plans = (
                f"**A. 集中** 全部进 {t1} — 数据上 EV 最高, 但单标的风险\n"
                f"**B. 分散** {t1} + {t2} 各 50/50 — 风险减半, 上行减半\n"
                "**C. 保守** SMH (1x 半导体) — 同主题不杠杆"
            )

            best_asym = rows[0][1]["asymmetry"]
            color = (score_to_color(75) if best_asym > 7 else
                     score_to_color(50) if best_asym > 4 else
                     score_to_color(30))

            send_embed(
                title=f"投机仓位评估 · {date.today().isoformat()}",
                description=(
                    f"评估了 **{len(results)} 个候选** (3x ETF / 1x ETF / 单股 / AI 基建).\n\n"
                    + soxl_line
                ),
                fields=[
                    {"name": "[#] Top 8 (按 asymmetry 排序)",
                     "value": top_table, "inline": False},
                    {"name": "→ 三种部署方案",
                     "value": plans, "inline": False},
                ],
                color=color,
                footer="数据 yfinance · 完整 14 标的表见本地报告 · 蒙特利尔 ET",
            )


if __name__ == "__main__":
    main()
