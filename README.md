# Tech Sector Quant Advisor

A personal, rule-based quantitative analysis system for US tech-sector stocks.
Runs daily, scores the whole hot-tech universe across multiple academic +
practitioner dimensions, and pushes an actionable brief to Discord.

> Educational / personal-research tool. **Not investment advice.** Data via
> yfinance (delayed). The system describes market state; it does not predict.

## What it does

Two scheduled briefs per weekday:

- **Pre-close brief** (`preclose_brief.py`, ~15:30 ET) — "what to do before
  today's close": market regime gate + investment-master consensus + watchlist.
- **Daily brief** (`daily_brief.py`, ~17:30 ET) — full multi-dimension scan.

Both render a native Discord embed (no file attachments, no personal holdings
leaked into the channel).

## Evaluation dimensions

| Dimension | Module | Basis |
|---|---|---|
| Trend / momentum / stretch | `indicators.py` | SMA stack, 12-1 momentum (Jegadeesh-Titman) |
| Quality factor | `factors.py` | QMJ-inspired (Asness et al.) |
| PEAD | `factors.py` | post-earnings drift (Bernard-Thomas) |
| Valuation | `valuation.py` | PEG-based + cyclical value-trap filter |
| Analyst consensus | `factors.py` | targets + upside (yfinance) |
| Market regime gate | `regime.py` | VIX / breadth / SPY trend → risk-on/off |
| Investment-master consensus | `guru_screens.py` | rule-based Buffett / Graham / Lynch / Greenblatt / Piotroski / Burry |
| News sentiment + themes | `news.py` | lexicon + keyword clustering |
| Earnings calendar | `events.py` | upcoming reports |
| Pullback watchlist | `watchlist.py` | buy-the-dip-to-support monitor |
| Per-ticker action matrix | `recommendations.py` | quality × risk → buy/add/hold/trim/avoid |

The recommendation engine is **regime-gated**: on a risk-off day, fresh-buy
signals are tempered to "wait for stabilization" rather than blindly screaming
"buy the dip."

## The "guru screens"

`guru_screens.py` is a faithful **rule-based** encoding of six famous
methodologies — no LLM, no paid data:

- **Buffett**: moat (margins) + quality (ROE) + low debt + fair price
- **Graham**: deep value + margin of safety (Graham Number, low PB/PE)
- **Lynch**: GARP — PEG < 1 + real earnings growth
- **Greenblatt Magic Formula**: high ROIC + high earnings yield
- **Piotroski F-Score**: 9-point fundamental-health checklist
- **Burry**: FCF yield + cheap EV/EBITDA deep value

Each votes per stock; a consensus aggregates them.

## Setup

```bash
python -m venv .venv
. .venv/Scripts/activate      # Windows
pip install -e .

cp configs/portfolio.example.yaml configs/portfolio.yaml   # then edit holdings

# one-off run (no Discord)
python daily_brief.py --no-discord
python preclose_brief.py --no-discord

# with Discord push
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python daily_brief.py
```

### Scheduling (Windows)

```powershell
.\scripts\install_daily_task.ps1    -WebhookUrl "<url>" -Time "17:30"
.\scripts\install_preclose_task.ps1 -WebhookUrl "<url>" -Time "15:30"
```

## Original backtest engine

The repo started as a minimal lookahead-free SMA-crossover backtester
(`run.py`, `src/strategy.py`, `src/backtest.py`, `src/metrics.py`). That code
remains and is independently runnable: `python run.py`. The key invariant —
`position[t]` is decided from `signal[t-1]` (`.shift(1)`) so today's signal
only executes tomorrow — is enforced by `tests/test_strategy.py`.

## Privacy

`portfolio.yaml`, `data/state/`, `logs/`, and generated `scripts/_run_*.cmd`
(which embed your webhook URL) are gitignored. Discord output never contains
position sizes or cost basis.

## License

MIT
