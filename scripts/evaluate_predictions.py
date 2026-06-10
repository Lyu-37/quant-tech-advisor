"""Evaluate prediction accuracy of past daily snapshots vs realized prices.

Methodology:
  - For each saved snapshot at date X, load all ticker actions
  - Fetch current price + price at date X
  - Per ticker, compute realized return X→today
  - Classify action prediction as HIT / MISS based on direction
  - Aggregate hit rate by action category and by snapshot date

Caveats (printed in report):
  - 6 days of forward data is *very* short; mean-reversion noise dominates
  - "建仓"/"加仓持有" are medium-term calls; judging on days is unfair
  - "观望" / "持有不加" have no clear directional bet — excluded from hit rate
"""
from datetime import date, timedelta
from pathlib import Path
import json
import sys

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.advisor.fetcher import fetch_universe


STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "state"

# How to evaluate each action category
BUY_ACTIONS  = {"建仓", "加仓持有", "试探建仓"}
SELL_ACTIONS = {"减仓", "清仓"}
NEUTRAL_DOWN = {"避免", "观望偏空"}      # no big upside expected
NEUTRAL      = {"持有不加", "观望"}       # no directional bet — exclude from hit rate

# Magnitude thresholds (handle daily noise)
UP_THRESHOLD   = 0.005    # > +0.5% counts as "up"
DOWN_THRESHOLD = -0.005   # < -0.5% counts as "down"


def load_snapshots() -> list[dict]:
    snaps = []
    for f in sorted(STATE_DIR.glob("snapshot-*.json")):
        snaps.append(json.loads(f.read_text(encoding="utf-8")))
    return snaps


def fetch_prices_at_date(tickers: list[str], target_date: date) -> dict[str, float]:
    """Get adjusted close price as of target_date (or nearest prior trading day).

    NOTE: fetch_universe anchors its window to TODAY, so the lookback must
    reach from today all the way back past target_date. The old code passed a
    fixed 13-day lookback, which silently dropped every snapshot older than
    ~2 weeks from the evaluation.
    """
    out = {}
    days_back = (date.today() - target_date).days + 15
    data = fetch_universe(tickers, lookback_days=max(days_back, 15), use_cache=True)
    target_ts = pd.Timestamp(target_date)
    for t, df in data.items():
        if df is None or df.empty:
            continue
        # Find nearest trading day <= target_date
        idx = df.index[df.index <= target_ts]
        if len(idx) == 0:
            continue
        last_idx = idx[-1]
        out[t] = float(df["close"].loc[last_idx])
    return out


def classify_outcome(action: str, ret: float) -> tuple[str, bool | None]:
    """Returns (bucket_name, is_hit).
    bucket_name: BUY / SELL / NEUTRAL_DOWN / NEUTRAL
    is_hit: True/False/None (None = excluded from hit rate)
    """
    if action in BUY_ACTIONS:
        if ret > UP_THRESHOLD:
            return ("BUY", True)
        if ret < DOWN_THRESHOLD:
            return ("BUY", False)
        return ("BUY", None)  # flat = ambiguous
    if action in SELL_ACTIONS:
        if ret < DOWN_THRESHOLD:
            return ("SELL", True)
        if ret > UP_THRESHOLD:
            return ("SELL", False)
        return ("SELL", None)
    if action in NEUTRAL_DOWN:
        # "Avoid" predicts NOT a strong up — hit if return <= +2%
        if ret <= 0.02:
            return ("NEUTRAL_DOWN", True)
        return ("NEUTRAL_DOWN", False)
    return ("NEUTRAL", None)


def evaluate_snapshot(snap: dict, today_prices: dict[str, float],
                      historic_prices: dict[str, float]) -> dict:
    """Evaluate accuracy of a single snapshot's predictions."""
    snap_date = date.fromisoformat(snap["date"])
    results = {
        "snapshot_date": snap_date,
        "days_elapsed": (date.today() - snap_date).days,
        "by_bucket": {},
        "by_action": {},
        "n_actions": 0,
        "ticker_returns": [],
    }
    bucket_stats = {"BUY": [0, 0], "SELL": [0, 0],
                    "NEUTRAL_DOWN": [0, 0], "NEUTRAL": [0, 0]}
    action_stats = {}

    for ticker, info in snap["actions"].items():
        action = info["action"]
        p_then = historic_prices.get(ticker)
        p_now = today_prices.get(ticker)
        if p_then is None or p_now is None or p_then == 0:
            continue
        ret = p_now / p_then - 1
        results["n_actions"] += 1
        results["ticker_returns"].append((ticker, action, ret))

        bucket, is_hit = classify_outcome(action, ret)
        if is_hit is not None:
            bucket_stats[bucket][1] += 1
            if is_hit:
                bucket_stats[bucket][0] += 1

        action_stats.setdefault(action, [0.0, 0])
        action_stats[action][0] += ret
        action_stats[action][1] += 1

    # Hit rates
    for b, (hits, total) in bucket_stats.items():
        results["by_bucket"][b] = {
            "hits": hits,
            "total": total,
            "rate": hits / total if total else None,
        }
    # Avg returns per action
    for a, (sum_ret, n) in action_stats.items():
        results["by_action"][a] = {
            "avg_ret": sum_ret / n if n else 0,
            "n": n,
        }
    return results


def main():
    snaps = load_snapshots()
    if len(snaps) < 2:
        print("Need at least 2 snapshots to evaluate")
        return

    # Pool all tickers across all snapshots
    all_tickers = set()
    for s in snaps:
        all_tickers.update(s["actions"].keys())
    all_tickers = sorted(all_tickers)

    print(f"Loaded {len(snaps)} snapshots, evaluating {len(all_tickers)} tickers")
    print(f"Today: {date.today().isoformat()}")
    print()

    today_prices = fetch_prices_at_date(all_tickers, date.today())
    print(f"Fetched today's prices for {len(today_prices)} tickers\n")

    # Per-snapshot evaluation
    per_snap_results = []
    for snap in snaps[:-1]:  # exclude latest (no forward data yet)
        snap_date = date.fromisoformat(snap["date"])
        # Skip if snapshot is today
        if snap_date >= date.today():
            continue
        hist_prices = fetch_prices_at_date(all_tickers, snap_date)
        r = evaluate_snapshot(snap, today_prices, hist_prices)
        per_snap_results.append(r)

    # ---------- Report ----------
    print("=" * 72)
    print("PREDICTION ACCURACY REPORT")
    print("=" * 72)
    print()
    print("Methodology: For each snapshot at date X, compare predicted")
    print("direction (BUY / SELL / AVOID) vs realized return X→today.")
    print()

    # Per-snapshot summary
    print(f"{'Snap Date':<12}{'Days':<6}{'N':<5}"
          f"{'BUY hit':<12}{'SELL hit':<12}{'AVOID hit':<12}")
    print("-" * 72)
    for r in per_snap_results:
        b = r["by_bucket"]["BUY"]
        s = r["by_bucket"]["SELL"]
        a = r["by_bucket"]["NEUTRAL_DOWN"]
        def fmt(stats):
            if stats["total"] == 0:
                return "n/a"
            pct = stats["rate"] * 100
            return f"{stats['hits']}/{stats['total']} ({pct:.0f}%)"
        print(f"{r['snapshot_date'].isoformat():<12}"
              f"{r['days_elapsed']:<6}"
              f"{r['n_actions']:<5}"
              f"{fmt(b):<12}{fmt(s):<12}{fmt(a):<12}")

    # Aggregate across all snapshots
    print()
    print("=" * 72)
    print("AGGREGATE (all snapshots pooled)")
    print("=" * 72)
    agg = {"BUY": [0, 0], "SELL": [0, 0], "NEUTRAL_DOWN": [0, 0]}
    for r in per_snap_results:
        for b in agg:
            agg[b][0] += r["by_bucket"][b]["hits"]
            agg[b][1] += r["by_bucket"][b]["total"]
    for b, (h, t) in agg.items():
        if t > 0:
            label = {"BUY": "买入/加仓", "SELL": "减仓/清仓",
                     "NEUTRAL_DOWN": "避免"}[b]
            print(f"  {label:<10} {h}/{t} = {h/t*100:.1f}% hit rate")

    # Per-action average returns
    print()
    print("=" * 72)
    print("AVG RETURN BY ACTION (across all snapshots)")
    print("=" * 72)
    action_agg = {}
    for r in per_snap_results:
        for a, info in r["by_action"].items():
            action_agg.setdefault(a, [0.0, 0])
            action_agg[a][0] += info["avg_ret"] * info["n"]
            action_agg[a][1] += info["n"]

    # Compute benchmark: avg of all returns
    all_returns = []
    for r in per_snap_results:
        for _, _, ret in r["ticker_returns"]:
            all_returns.append(ret)
    benchmark = sum(all_returns) / len(all_returns) if all_returns else 0
    print(f"  Universe benchmark (equal-weight avg return): {benchmark * 100:+.2f}%")
    print()

    action_order = ["建仓", "加仓持有", "试探建仓", "持有不加",
                    "观望", "观望偏空", "减仓", "避免", "清仓"]
    for a in action_order:
        if a in action_agg:
            avg_ret, n = action_agg[a][0] / action_agg[a][1], action_agg[a][1]
            excess = avg_ret - benchmark
            tag = ""
            if a in BUY_ACTIONS:
                tag = "(应该 > 基准)"
            elif a in SELL_ACTIONS or a in NEUTRAL_DOWN:
                tag = "(应该 < 基准)"
            print(f"  {a:<10} avg ret {avg_ret * 100:+5.2f}%  "
                  f"(超 benchmark {excess * 100:+.2f}%)  n={n}  {tag}")

    # Highlight notable hits and misses
    print()
    print("=" * 72)
    print("NOTABLE HITS / MISSES (latest snapshot's predictions)")
    print("=" * 72)
    if per_snap_results:
        latest = per_snap_results[-1]
        ticker_rets = latest["ticker_returns"]
        # Top buy hits
        buy_picks = [(t, a, r) for t, a, r in ticker_rets if a in BUY_ACTIONS]
        buy_picks.sort(key=lambda x: -x[2])
        print(f"\n买入推荐里涨幅 Top 3 (from {latest['snapshot_date']}):")
        for t, a, r in buy_picks[:3]:
            print(f"  + {t:<6} ({a})  {r * 100:+.2f}%")
        print(f"买入推荐里跌幅 Top 3:")
        for t, a, r in buy_picks[-3:]:
            print(f"  - {t:<6} ({a})  {r * 100:+.2f}%")

        # Avoid that ran
        avoid_picks = [(t, a, r) for t, a, r in ticker_rets if a in NEUTRAL_DOWN]
        avoid_picks.sort(key=lambda x: -x[2])
        if avoid_picks:
            print(f"\n避免/观望偏空里反而涨的 Top 3 (反指):")
            for t, a, r in avoid_picks[:3]:
                marker = "  X" if r > 0.02 else "   "
                print(f"  {marker} {t:<6} ({a})  {r * 100:+.2f}%")


if __name__ == "__main__":
    main()
