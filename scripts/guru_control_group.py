"""Guru-screen control group: calibrate the hand-picked universe's bias.

The "X/6 大佬看好" numbers only mean something relative to a baseline. This
runs the same guru screens on 30 boring non-tech large caps and compares the
average bullish count / score against GURU_FOCUS. If the tech universe scores
systematically higher, the screens are measuring "what growth tech looks
like", not "what is worth buying" — read the daily guru field accordingly.

Run on demand (hits .info for ~57 tickers, ~1-2 min):
    python scripts/guru_control_group.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.advisor.guru_screens import analyze_gurus

# Fixed, diversified, deliberately boring control group (non-tech S&P names).
CONTROL = [
    "JNJ", "PG", "KO", "PEP", "WMT", "COST", "MCD", "HD", "LOW", "NKE",
    "XOM", "CVX", "COP", "JPM", "BAC", "WFC", "GS", "UNH", "PFE", "MRK",
    "ABBV", "T", "VZ", "DIS", "CAT", "DE", "UPS", "HON", "MMM", "DUK",
]

TECH_FOCUS = [
    "NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "ASML", "AMAT", "LRCX", "ARM",
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "TSLA",
    "GEV", "VRT", "ETN", "PWR", "PLTR", "CRWD", "NET",
    "LITE", "COHR", "CIEN", "FN",
]


def group_stats(tickers: list[str]) -> tuple[float, float, int]:
    bullish, scores, n = 0.0, 0.0, 0
    for t in tickers:
        c = analyze_gurus(t)
        if c is None:
            continue
        bullish += c.bullish
        scores += c.score
        n += 1
    return (bullish / n if n else 0.0, scores / n if n else 0.0, n)


def main():
    print("Running guru screens on control group (30 boring large caps)...")
    c_bull, c_score, c_n = group_stats(CONTROL)
    print(f"  control: avg bullish {c_bull:.2f}/6, avg score {c_score:.0f} (n={c_n})")
    print("Running guru screens on tech focus universe...")
    t_bull, t_score, t_n = group_stats(TECH_FOCUS)
    print(f"  tech:    avg bullish {t_bull:.2f}/6, avg score {t_score:.0f} (n={t_n})")

    gap_b = t_bull - c_bull
    gap_s = t_score - c_score
    print()
    print(f"Universe bias: bullish {gap_b:+.2f}/6, score {gap_s:+.0f}")
    if gap_s > 10 or gap_b > 0.8:
        print("=> 你的科技池系统性高分 — 大佬字段读作 \"池内相对排序\", 不是绝对买入依据")
    elif gap_s < -10 or gap_b < -0.8:
        print("=> 价值类规则对科技池结构性偏空 (符合预期) — 低分主要是风格错配, 不是个股问题")
    else:
        print("=> 两组接近 — 大佬字段的绝对水平这段时间可以正常解读")


if __name__ == "__main__":
    main()
