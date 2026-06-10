"""Evaluate past daily snapshots vs realized forward EXCESS returns.

为什么是超额而不是命中率: 牛市里方向命中率必然好看, 毫无信息量. 系统的
预注册主指标 (configs/PARAMS-CHANGELOG.md):

    建仓 bucket 的 20 日超额收益 (vs QQQ), 样本 N>=30 有效;
    连续两个季度 20 日超额 <= 0 -> 系统降级为仅状态描述, 买入字段停用.

Methodology:
  - For each snapshot date D and each ticker action, compute forward returns
    at +5 and +20 TRADING days (QQQ's bar index is the trading calendar).
  - Excess = ticker forward return - QQQ forward return over the same window.
  - Signals are evaluated at the PRE-GATE level (action_pregate): we score
    the signal engine, not the regime gate's renaming.
  - Buckets: BUY (建仓/加仓持有/试探建仓) wants excess > 0;
    SELL (减仓/清仓) and AVOID (避免/观望偏空) want excess < 0;
    REVERSAL (短期反弹候选) is judged at 5d only (its design horizon).
  - If configs/trades.yaml exists, your actual fills are joined against the
    snapshot signals: 跟随信号 / 反向操作 / 自主交易, with the same
    forward-excess scoring. The gap between "系统说的" and "你做的" is
    usually more informative than either alone.

Usage:
    python scripts/evaluate_predictions.py [--discord] [--horizons 5,20]
"""
from datetime import date, timedelta
from pathlib import Path
import argparse
import json
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.advisor.fetcher import fetch_universe

STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "state"
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "eval"

BENCH = "QQQ"
BUY_ACTIONS = {"建仓", "加仓持有", "试探建仓"}
SELL_ACTIONS = {"减仓", "清仓"}
AVOID_ACTIONS = {"避免", "观望偏空"}
REVERSAL_ACTIONS = {"短期反弹候选"}

MIN_N = 30      # 预注册: 样本低于此数不下结论


def load_snapshots() -> list[dict]:
    snaps = []
    for f in sorted(STATE_DIR.glob("snapshot-*.json")):
        try:
            snaps.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return snaps


def build_panel(tickers: list[str], oldest: date) -> dict[str, pd.Series]:
    lookback = (date.today() - oldest).days + 60
    data = fetch_universe(sorted(set(tickers + [BENCH])),
                          lookback_days=max(lookback, 90), use_cache=True)
    return {t: df["close"] for t, df in data.items()
            if df is not None and not df.empty}


def forward_excess(panel: dict[str, pd.Series], ticker: str,
                   d: date, horizon: int) -> float | None:
    """Ticker forward return minus QQQ forward return over `horizon` trading
    days starting at the close of (the last trading day <= d)."""
    bench = panel.get(BENCH)
    px = panel.get(ticker)
    if bench is None or px is None:
        return None
    ts = pd.Timestamp(d)
    b_pos = int(bench.index.searchsorted(ts, side="right")) - 1
    if b_pos < 0 or b_pos + horizon >= len(bench):
        return None                      # not enough forward data yet
    start_ts, end_ts = bench.index[b_pos], bench.index[b_pos + horizon]
    try:
        p0 = float(px.asof(start_ts))
        p1 = float(px.asof(end_ts))
    except (KeyError, TypeError):
        return None
    if not (p0 > 0 and p1 > 0) or pd.isna(p0) or pd.isna(p1):
        return None
    # Guard: ticker must actually have data near the window start
    if px.index[0] > start_ts:
        return None
    ret = p1 / p0 - 1
    b_ret = float(bench.iloc[b_pos + horizon]) / float(bench.iloc[b_pos]) - 1
    return ret - b_ret


def bucket_of(action: str) -> str | None:
    if action in BUY_ACTIONS:
        return "BUY"
    if action in SELL_ACTIONS:
        return "SELL"
    if action in AVOID_ACTIONS:
        return "AVOID"
    if action in REVERSAL_ACTIONS:
        return "REVERSAL"
    return None      # 观望/持有 etc: no directional bet


def evaluate(snaps: list[dict], panel: dict[str, pd.Series],
             horizons: list[int]) -> dict:
    """Returns {bucket: {horizon: {"excess": [...], "n": int}}}."""
    stats: dict = {}
    for snap in snaps:
        d = date.fromisoformat(snap["date"])
        for ticker, info in snap.get("actions", {}).items():
            action = info.get("action_pregate") or info.get("action", "")
            bucket = bucket_of(action)
            if bucket is None:
                continue
            hs = [5] if bucket == "REVERSAL" else horizons
            for h in hs:
                ex = forward_excess(panel, ticker, d, h)
                if ex is None:
                    continue
                cell = stats.setdefault(bucket, {}).setdefault(h, [])
                cell.append(ex)
    return stats


def summarize(stats: dict, horizons: list[int]) -> list[str]:
    lines = []
    want_neg = {"SELL", "AVOID"}
    label = {"BUY": "买入 (建仓/加仓/试探)", "SELL": "卖出 (减仓/清仓)",
             "AVOID": "回避 (避免/观望偏空)", "REVERSAL": "短期反弹候选 (5d)"}
    for bucket in ("BUY", "REVERSAL", "SELL", "AVOID"):
        if bucket not in stats:
            continue
        for h in sorted(stats[bucket]):
            xs = stats[bucket][h]
            n = len(xs)
            avg = sum(xs) / n
            good = (sum(1 for x in xs if x < 0) if bucket in want_neg
                    else sum(1 for x in xs if x > 0))
            note = "" if n >= MIN_N else f"  (样本不足 {MIN_N}, 仅供参考)"
            direction = "希望<0" if bucket in want_neg else "希望>0"
            lines.append(
                f"{label[bucket]} @{h}d: 平均超额 {avg * 100:+.2f}% "
                f"({direction}) · 超额胜率 {good}/{n} = {good / n * 100:.0f}%{note}")
    return lines


def headline_verdict(stats: dict) -> str:
    """预注册主指标判定: BUY @20d 平均超额."""
    xs = stats.get("BUY", {}).get(20, [])
    if not xs:
        return "主指标 (建仓类 20d 超额): 尚无足够 forward 数据"
    avg = sum(xs) / len(xs)
    n = len(xs)
    verdict = ("有效样本不足, 不下结论" if n < MIN_N
               else ("正超额 — 系统买入信号目前创造价值" if avg > 0
                     else "无超额 — 注意: 连续两个季度如此则按预注册规则降级系统"))
    return (f"主指标 · 建仓类信号 20 日超额 (vs {BENCH}): "
            f"**{avg * 100:+.2f}%** (N={n}) — {verdict}")


def evaluate_trades(panel: dict[str, pd.Series], snaps: list[dict]) -> list[str]:
    """你做的 vs 系统说的: join configs/trades.yaml against snapshots."""
    try:
        from src.advisor.ledger import load_trades
    except ImportError:
        return []
    trades = [t for t in load_trades() if t.side == "buy"]
    if not trades:
        return []
    snap_by_date = {date.fromisoformat(s["date"]): s for s in snaps}
    groups = {"跟随信号": [], "反向操作": [], "自主交易": []}
    for tr in trades:
        # 最近一个 <= 成交日且 3 天内的 snapshot
        snap = None
        for back in range(0, 4):
            snap = snap_by_date.get(tr.date - timedelta(days=back))
            if snap:
                break
        action = ""
        if snap:
            info = snap.get("actions", {}).get(tr.ticker, {})
            action = info.get("action_pregate") or info.get("action", "")
        if action in BUY_ACTIONS or action in REVERSAL_ACTIONS:
            g = "跟随信号"
        elif action in SELL_ACTIONS or action in AVOID_ACTIONS:
            g = "反向操作"
        else:
            g = "自主交易"
        ex = forward_excess(panel, tr.ticker, tr.date, 20)
        groups[g].append((tr.ticker, tr.date, ex))

    lines = ["", "## 你做的 vs 系统说的 (买入成交 · 20d 超额)"]
    for g, items in groups.items():
        if not items:
            continue
        scored = [x for _, _, x in items if x is not None]
        avg = (f"平均超额 {sum(scored) / len(scored) * 100:+.2f}%"
               if scored else "forward 数据未满")
        names = ", ".join(f"{t}({d})" for t, d, _ in items[:6])
        lines.append(f"- **{g}** × {len(items)}: {avg} — {names}")
    if groups["反向操作"]:
        lines.append("- 注意: 反向操作的笔数和收益值得单独复盘 — "
                     "要么你看到了系统看不到的东西, 要么纪律破了")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discord", action="store_true",
                        help="push a compact summary embed")
    parser.add_argument("--horizons", default="5,20")
    args = parser.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]

    snaps = load_snapshots()
    if len(snaps) < 2:
        print("Need at least 2 snapshots to evaluate")
        return
    all_tickers = sorted({t for s in snaps for t in s.get("actions", {})})
    oldest = date.fromisoformat(snaps[0]["date"])
    print(f"Snapshots: {len(snaps)} ({snaps[0]['date']} .. {snaps[-1]['date']}), "
          f"{len(all_tickers)} tickers, benchmark {BENCH}")

    panel = build_panel(all_tickers, oldest)
    if BENCH not in panel:
        raise RuntimeError(f"benchmark {BENCH} 数据缺失, 无法计算超额")

    stats = evaluate(snaps, panel, horizons)
    head = headline_verdict(stats)
    body = summarize(stats, horizons)
    trade_lines = evaluate_trades(panel, snaps)

    print()
    print(head.replace("**", ""))
    for ln in body:
        print(" ", ln)
    for ln in trade_lines:
        print(ln.replace("**", "").replace("## ", ""))

    # Markdown artifact
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = ["# 信号验证周报", "",
          f"生成: {date.today().isoformat()} · 覆盖 snapshot "
          f"{snaps[0]['date']} .. {snaps[-1]['date']} · 基准 {BENCH}", "",
          head, ""]
    md += [f"- {ln}" for ln in body]
    md += trade_lines
    md += ["", "_预注册规则: 建仓类 20d 超额连续两个季度 <= 0 -> "
               "系统降级为仅状态描述. 规则写于 2026-06-10, 不许事后改._"]
    out_path = OUT_DIR / f"eval-{date.today().isoformat()}.md"
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nSaved {out_path}")

    if args.discord:
        from src.advisor.discord_push import send_embed, COLOR_BLUE
        send_embed(
            title=f"信号验证周报 · {date.today().isoformat()}",
            description=head + "\n\n" + "\n".join(f"· {ln}" for ln in body[:8]),
            color=COLOR_BLUE,
            footer=f"基准 {BENCH} · 预注册主指标: 建仓类 20d 超额 · N>={MIN_N} 才有效",
        )


if __name__ == "__main__":
    main()
