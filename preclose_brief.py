"""Pre-close trading brief — runs ~15:30 ET, 30 min before US close.

Lighter than the full daily brief. Answers ONE question: "given today's
action + the masters' consensus, is there anything to do before the close?"

Combines:
  - Market regime (risk-on/off gate — should you be acting at all today?)
  - Guru consensus (which names the investment masters love)
  - Watchlist (did anything hit a buy zone today?)
  - Today's biggest movers

Pushes a compact Discord embed. No full recommendation matrix — this is the
"last call before close" view.
"""
from datetime import date
from pathlib import Path
import argparse
import os
import yaml

from src.advisor.universe import HOT_TECH, all_symbols_for_run, Holding
from src.advisor.fetcher import fetch_universe
from src.advisor.regime import detect_regime, find_relative_strength_in_selloff
from src.advisor.guru_screens import analyze_guru_universe, render_guru_field
from src.advisor.watchlist import evaluate_watchlist
from src.advisor.market_movers import compute_today_movers
from src.advisor.discord_push import send_embed, score_to_color

PROJECT_ROOT = Path(__file__).resolve().parent

# Stocks to run guru screens on (large/mid caps with real fundamentals)
GURU_FOCUS = [
    "NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "ASML", "AMAT", "LRCX", "ARM",
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA",
    "GEV", "VRT", "ETN", "PWR", "PLTR", "CRWD", "NET",
    "LITE", "COHR", "CIEN", "FN",
]


def load_cfg(path: Path):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    holdings = [
        Holding(**{k: v for k, v in h.items() if k in
                   {"ticker", "shares", "cost_basis", "sector", "note"}})
        for h in cfg.get("holdings", [])
    ]
    return holdings, cfg


def build_preclose_embed(run_date, data, regime, rs_leaders,
                         guru_map, watch_verdicts, movers):
    fields = []

    # 1. Market pulse / regime — should you act today at all?
    from src.advisor.regime import render_regime_field
    fields.append(render_regime_field(regime, rs_leaders))

    # 2. Watchlist — anything actionable before close?
    actionable = [v for v in watch_verdicts if v.verdict == "可以入场"]
    near = [v for v in watch_verdicts if v.verdict == "到支撑了-等企稳"]
    if actionable or near:
        lines = []
        for v in actionable:
            lines.append(f"▲ **可以入场** `{v.ticker}` ${v.current:.2f} — {v.detail}")
        for v in near:
            lines.append(f"◆ 等企稳 `{v.ticker}` ${v.current:.2f} — 到支撑但还在跌")
        fields.append({
            "name": "[盘前最后一看] 你的 watchlist",
            "value": "\n".join(lines),
            "inline": False,
        })

    # 3. Guru consensus — what the masters love right now
    guru_field = render_guru_field(guru_map, top_n=8)
    if guru_field:
        fields.append(guru_field)

    # 4. Today's movers (compact)
    if movers.gainers:
        gain = "  ·  ".join(f"`{m.ticker}` {m.today_pct*100:+.1f}%"
                            for m in movers.gainers[:4])
        loss = "  ·  ".join(f"`{m.ticker}` {m.today_pct*100:+.1f}%"
                            for m in movers.losers[:4])
        fields.append({
            "name": "[今日盘中] 涨跌 Top",
            "value": f"涨: {gain}\n跌: {loss}",
            "inline": False,
        })

    # 5. Bottom line — one actionable sentence
    if regime.buy_gate == "temper":
        bottom = "**今日定调**: risk-off, 收盘前不建议追买. 现金为王, 等明天体制转好."
    elif actionable:
        names = ", ".join(v.ticker for v in actionable)
        bottom = f"**今日定调**: {names} 到买点了, 收盘前可分批建仓. 其余观望."
    elif regime.buy_gate == "pass":
        top_guru = sorted(guru_map.values(), key=lambda c: -c.bullish)[:2]
        names = ", ".join(c.ticker for c in top_guru if c.bullish >= 4)
        bottom = (f"**今日定调**: 体制正常. 大佬最爱 {names}. "
                  "无紧急操作, 按计划分批." if names
                  else "**今日定调**: 体制正常, 无紧急操作.")
    else:
        bottom = "**今日定调**: 中性谨慎, 无紧急操作, 分批不梭哈."
    fields.append({"name": "→ 收盘前结论", "value": bottom, "inline": False})

    return {
        "title": f"收盘前交易简报 · {run_date.isoformat()} · 美东 15:30",
        "description": (f"**体制: {regime.label}**  "
                        f"SPY {(regime.spy_today or 0)*100:+.1f}%  "
                        f"QQQ {(regime.qqq_today or 0)*100:+.1f}%  "
                        f"VIX {regime.vix_level:.0f}\n"
                        "_收盘前 30 分钟最后一看 — 今天还有什么要动手的_"),
        "fields": fields,
        "color": score_to_color(regime.score),
        "footer": "投资大佬规则共识 (巴菲特/格雷厄姆/林奇/神奇公式/Piotroski/Burry) · 蒙特利尔 ET",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/portfolio.yaml")
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    holdings, cfg = load_cfg(PROJECT_ROOT / args.config)
    watch_cfg = cfg.get("watchlist", []) or []

    symbols = sorted(set(HOT_TECH + GURU_FOCUS + ["SPY", "QQQ", "^VIX", "^TNX"]
                         + [w["ticker"] for w in watch_cfg]))
    print(f"[1/4] Fetching {len(symbols)} symbols...")
    # Always fresh: stale data showing yesterday's crash on an up day is the
    # worst failure mode for this tool.
    data = fetch_universe(symbols, lookback_days=400, use_cache=False)

    print("[2/4] Regime + movers + watchlist...")
    regime = detect_regime(data)
    rs_leaders = find_relative_strength_in_selloff(data)
    movers = compute_today_movers(data)
    watch_verdicts = evaluate_watchlist(watch_cfg, data)
    print(f"      regime={regime.label} gate={regime.buy_gate}")

    print(f"[3/4] Running guru screens on {len(GURU_FOCUS)} stocks...")
    guru_map = analyze_guru_universe(GURU_FOCUS)
    top = sorted(guru_map.values(), key=lambda c: -c.bullish)[:3]
    print("      top guru picks: " +
          ", ".join(f"{c.ticker}({c.bullish}/6)" for c in top))

    print("[4/4] Discord push...")
    embed = build_preclose_embed(date.today(), data, regime, rs_leaders,
                                 guru_map, watch_verdicts, movers)
    if args.no_discord:
        print("      (skipped, --no-discord)")
        # Print summary to console
        print(f"\n收盘前结论: {embed['fields'][-1]['value']}")
    else:
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if not url:
            print("      ! DISCORD_WEBHOOK_URL not set")
        else:
            send_embed(**embed)
    print("\nDone.")


if __name__ == "__main__":
    main()
