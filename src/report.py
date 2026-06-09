"""Plot equity curve and drawdown."""
from pathlib import Path
import matplotlib.pyplot as plt
import pandas as pd


def plot_equity(df: pd.DataFrame, output_path: str = "results/equity.png") -> None:
    """Plot strategy equity vs buy-and-hold, with drawdown panel."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})

    # Buy & hold benchmark (always in market)
    bh = (1 + df["returns"]).cumprod()

    axes[0].plot(df.index, df["equity"], label="SMA Strategy", linewidth=1.5)
    axes[0].plot(df.index, bh, label="Buy & Hold", alpha=0.6, linewidth=1.2)
    axes[0].set_ylabel("Equity (start = 1.0)")
    axes[0].set_title("SMA Crossover Backtest")
    axes[0].legend(loc="upper left")
    axes[0].grid(True, alpha=0.3)

    peak = df["equity"].cummax()
    drawdown = (df["equity"] - peak) / peak
    axes[1].fill_between(df.index, drawdown, 0, alpha=0.5, color="C3")
    axes[1].set_ylabel("Drawdown")
    axes[1].set_xlabel("Date")
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=120)
    plt.close()
