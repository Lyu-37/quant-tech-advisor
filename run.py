"""Main entry point: run SMA crossover backtest on SPY."""
from pathlib import Path
import yaml

from src.data import load_ohlcv
from src.strategy import sma_crossover_signals
from src.backtest import run_backtest
from src.metrics import summarize
from src.report import plot_equity


def fmt_pct(x: float) -> str:
    return f"{x * 100:>7.2f}%"


def main():
    cfg_path = Path(__file__).parent / "configs" / "default.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())

    print(f"Loading {cfg['ticker']} from {cfg['start']} to {cfg['end']}...")
    df = load_ohlcv(cfg["ticker"], cfg["start"], cfg["end"])
    print(f"  -> {len(df)} trading days")

    print(f"Generating signals: MA({cfg['short']}) vs MA({cfg['long']})...")
    df = sma_crossover_signals(df, cfg["short"], cfg["long"])

    print(f"Running backtest "
          f"(commission={cfg['commission_bps']}bps, slippage={cfg['slippage_bps']}bps)...")
    df = run_backtest(df, cfg["commission_bps"], cfg["slippage_bps"])

    m = summarize(df)
    print("\n=== Results ===")
    print(f"  ann_return      : {fmt_pct(m['ann_return'])}")
    print(f"  ann_vol         : {fmt_pct(m['ann_vol'])}")
    print(f"  sharpe          : {m['sharpe']:>7.2f}")
    print(f"  max_drawdown    : {fmt_pct(m['max_drawdown'])}")
    print(f"  win_rate        : {fmt_pct(m['win_rate'])}")
    print(f"  n_trades        : {m['n_trades']:>7d}")
    print(f"  final_equity    : {m['final_equity']:>7.2f}x")

    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    plot_equity(df, str(results_dir / "equity.png"))
    df.to_parquet(results_dir / "backtest.parquet")
    print(f"\nSaved: {results_dir / 'equity.png'}")
    print(f"Saved: {results_dir / 'backtest.parquet'}")


if __name__ == "__main__":
    main()
