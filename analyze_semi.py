"""Semiconductor sector daily analysis with personalized actions.

Usage:
    python analyze_semi.py [--config configs/portfolio.yaml]

Pulls market data, computes a 5-layer sector score, ranks the leaders,
analyzes AMD specifically, and writes a markdown report to
results/sector_reports/YYYY-MM-DD-semi.md.
"""
from datetime import date
from pathlib import Path
import argparse
import yaml

from src.advisor.universe import (
    Holding, SEMI_ETFS, BENCHMARKS, SEMI_LEADERS, MACRO,
    all_symbols_for_run,
)
from src.advisor.fetcher import fetch_universe, latest_close
from src.advisor.indicators import (
    summarize_ticker, relative_strength,
)
from src.advisor.scoring import composite_sector_score
from src.advisor.report import render_full_report, save_report


PROJECT_ROOT = Path(__file__).resolve().parent


def load_portfolio(path: Path) -> list[Holding]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Holding(**{k: v for k, v in h.items() if k in
                       {"ticker", "shares", "cost_basis", "sector", "note"}})
            for h in cfg["holdings"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/portfolio.yaml")
    args = parser.parse_args()

    cfg_path = PROJECT_ROOT / args.config
    holdings = load_portfolio(cfg_path)
    print(f"Loaded {len(holdings)} holdings from {cfg_path.name}")

    symbols = all_symbols_for_run(holdings)
    print(f"Fetching {len(symbols)} symbols...")
    data = fetch_universe(symbols, lookback_days=400)
    print(f"  -> got {len(data)} successfully\n")

    # ------- holdings: attach current price (USD/native for most, CAD for VDY.TO) -------
    # All US tickers report in USD via yfinance. The user holdings are in CAD.
    # For a portfolio-level P/L we already have the CAD value from the screenshot,
    # but for analytics we use native close. We attach the *user-supplied* market
    # value to each holding from the screenshot to avoid FX confusion.
    user_supplied_values = {
        "AMD": 380.11, "GOOG": 185.69, "MSFT": 96.98, "GEV": 100.66,
        "GE": 95.04, "BRK-B": 96.75, "VDY.TO": 150.94,
    }
    for h in holdings:
        h.set_market_value(user_supplied_values.get(h.ticker, 0.0))

    # ------- sector composite score -------
    smh_df = data["SMH"]
    spy_df = data["SPY"]
    vix_df = data.get("^VIX")
    tnx_df = data.get("^TNX")
    dxy_df = data.get("DX-Y.NYB")

    score = composite_sector_score(
        sector_close=smh_df["close"],
        benchmark_close=spy_df["close"],
        vix_close=vix_df["close"] if vix_df is not None else None,
        tnx_df=tnx_df,
        dxy_df=dxy_df,
    )

    # ------- sector details -------
    smh_summary = summarize_ticker(smh_df["close"])
    soxx_summary = summarize_ticker(data["SOXX"]["close"])

    # ------- leader ranking + RS vs SMH -------
    leader_summaries = []
    rs_vs_smh = {}
    for t in SEMI_LEADERS:
        if t not in data:
            continue
        s = summarize_ticker(data[t]["close"])
        leader_summaries.append((t, s))
        rs_vs_smh[t] = relative_strength(data[t]["close"], smh_df["close"], window=60)

    # ------- AMD focus -------
    amd_summary = summarize_ticker(data["AMD"]["close"])
    amd_rs_smh = relative_strength(data["AMD"]["close"], smh_df["close"], window=60)
    amd_rs_nvda = relative_strength(data["AMD"]["close"], data["NVDA"]["close"], window=60)

    # ------- portfolio weights -------
    total_value = sum(h.market_value for h in holdings)
    weight_amd = next((h.market_value for h in holdings if h.ticker == "AMD"), 0) / total_value

    # ------- render report -------
    md = render_full_report(
        run_date=date.today(),
        holdings=holdings,
        score=score,
        smh_summary=smh_summary,
        soxx_summary=soxx_summary,
        leader_summaries=leader_summaries,
        rs_vs_smh=rs_vs_smh,
        amd_summary=amd_summary,
        amd_rs_smh=amd_rs_smh,
        amd_rs_nvda=amd_rs_nvda,
        macro_dict=score.macro,
        portfolio_weight_amd=weight_amd,
    )

    out_path = save_report(md, date.today(), PROJECT_ROOT)
    print(f"Report saved: {out_path}\n")
    print("=" * 60)
    print(f"COMPOSITE SCORE: {score.composite_0_100:.1f}/100  ({score.label})")
    print("=" * 60)
    print(f"  趋势       {score.sub_scores['trend']:.1f}/10  [{score.trend['label']}]")
    print(f"  动量       {score.sub_scores['momentum']:.1f}/10  [{score.momentum['label']}]")
    print(f"  相对强弱   {score.sub_scores['rel_strength']:.1f}/10  [{score.rel_strength['label']}]")
    print(f"  波动率     {score.sub_scores['vol_regime']:.1f}/10  [{score.vol_regime['label']}]")
    print(f"  宏观       {score.sub_scores['macro']:.1f}/10  [{score.macro['label']}]")
    print("=" * 60)
    print(f"  AMD vs SMH 60d: {amd_rs_smh['label']} ({amd_rs_smh.get('spread', 0) * 100:+.1f}%)")
    print(f"  AMD vs NVDA 60d: {amd_rs_nvda['label']} ({amd_rs_nvda.get('spread', 0) * 100:+.1f}%)")
    print(f"\n完整报告见: {out_path}")


if __name__ == "__main__":
    main()
