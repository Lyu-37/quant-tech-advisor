"""Trade journal (configs/trades.yaml) + speculation-sleeve circuit breaker.

The journal is the user's actual fills — the system's own accountability
loop joins it against the daily snapshots ("系统说的 vs 你做的", see
scripts/evaluate_predictions.py). It also powers the account-level circuit
breaker: when the speculation sleeve draws down hard, the correct
professional response is to STOP, not to find better entries.

trades.yaml format (gitignored — personal data; template in
configs/trades.example.yaml):

    trades:
      - {date: 2026-06-05, ticker: IONQ, side: buy,  shares: 0.8,
         price: 35.20, currency: USD, bucket: speculation}
      - {date: 2026-06-09, ticker: IONQ, side: sell, shares: 0.8,
         price: 31.00, currency: USD, bucket: speculation}

Breaker state persists in data/state/circuit_breaker.json:
    {"hwm": 162.5, "until": "2026-07-08"}  # until=null when inactive
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
import json

import pandas as pd
import yaml

from . import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRADES_PATH = PROJECT_ROOT / "configs" / "trades.yaml"
BREAKER_STATE_PATH = PROJECT_ROOT / "data" / "state" / "circuit_breaker.json"


@dataclass
class Trade:
    date: date
    ticker: str
    side: str          # buy / sell
    shares: float
    price: float       # native currency
    currency: str = "USD"
    bucket: str = "speculation"   # speculation / core
    note: str = ""


@dataclass
class SleeveStatus:
    """Speculation sleeve mark-to-market + breaker state."""
    equity: float                 # budget + realized + unrealized (CAD)
    realized_pnl: float
    unrealized_pnl: float
    open_positions: list = field(default_factory=list)  # (ticker, shares, avg_cost_cad)
    hwm: float = 0.0
    drawdown_pct: float = 0.0     # vs hwm, <= 0
    breaker_active: bool = False
    breaker_until: date | None = None
    n_trades: int = 0


def load_trades(path: Path | None = None) -> list[Trade]:
    p = path or TRADES_PATH
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for t in raw.get("trades", []) or []:
        try:
            d = t["date"]
            if isinstance(d, str):
                d = date.fromisoformat(d)
            out.append(Trade(
                date=d, ticker=str(t["ticker"]).upper(),
                side=str(t["side"]).lower(),
                shares=float(t["shares"]), price=float(t["price"]),
                currency=str(t.get("currency", "USD")).upper(),
                bucket=str(t.get("bucket", "speculation")).lower(),
                note=str(t.get("note", "")),
            ))
        except (KeyError, ValueError, TypeError) as e:
            print(f"  ! trades.yaml 跳过一条无法解析的记录: {e}")
    out.sort(key=lambda t: t.date)
    return out


def _mark_positions(trades: list[Trade], data: dict[str, pd.DataFrame],
                    usd_cad: float) -> tuple[float, float, list]:
    """FIFO realized + open-position MTM, all in CAD. Returns
    (realized_pnl, unrealized_pnl, open_positions)."""
    lots: dict[str, list[list[float]]] = {}   # ticker -> [[shares, cost_cad/sh], ...]
    realized = 0.0
    for t in trades:
        fx = 1.0 if t.currency == "CAD" else usd_cad
        px_cad = t.price * fx
        if t.side == "buy":
            lots.setdefault(t.ticker, []).append([t.shares, px_cad])
        elif t.side == "sell":
            remain = t.shares
            queue = lots.get(t.ticker, [])
            while remain > 1e-9 and queue:
                lot = queue[0]
                take = min(remain, lot[0])
                realized += take * (px_cad - lot[1])
                lot[0] -= take
                remain -= take
                if lot[0] <= 1e-9:
                    queue.pop(0)
            # Oversell beyond recorded lots: ignore the excess (journal gap)

    unrealized = 0.0
    open_positions = []
    for ticker, queue in lots.items():
        shares = sum(l[0] for l in queue)
        if shares <= 1e-9:
            continue
        cost = sum(l[0] * l[1] for l in queue) / shares
        df = data.get(ticker)
        if df is None or df.empty:
            # No mark available — carry at cost (P&L 0) rather than guessing
            open_positions.append((ticker, shares, cost))
            continue
        fx = 1.0 if ticker.endswith(".TO") else usd_cad
        mark_cad = float(df["close"].iloc[-1]) * fx
        unrealized += shares * (mark_cad - cost)
        open_positions.append((ticker, shares, cost))
    return realized, unrealized, open_positions


def _load_breaker_state() -> dict:
    if BREAKER_STATE_PATH.exists():
        try:
            return json.loads(BREAKER_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_breaker_state(state: dict) -> None:
    BREAKER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BREAKER_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def evaluate_speculation_sleeve(
    data: dict[str, pd.DataFrame],
    usd_cad: float,
    as_of: date,
    trades: list[Trade] | None = None,
    state: dict | None = None,
    persist: bool = True,
) -> SleeveStatus | None:
    """Mark the speculation sleeve and update the circuit breaker.

    Returns None when there is no journal (feature dormant until the user
    starts logging trades). `trades`/`state` injectable for tests.
    """
    if trades is None:
        trades = load_trades()
    spec = [t for t in trades if t.bucket == "speculation"]
    if not spec:
        return None

    budget = float(config.get("breaker.speculation_budget_cad", 150))
    dd_limit = float(config.get("breaker.drawdown_pct", 0.25))
    cooldown = int(config.get("breaker.cooldown_days", 28))

    realized, unrealized, open_pos = _mark_positions(spec, data, usd_cad)
    equity = budget + realized + unrealized

    st = _load_breaker_state() if state is None else dict(state)
    hwm = max(float(st.get("hwm", budget)), equity)
    drawdown = equity / hwm - 1 if hwm > 0 else 0.0

    until = st.get("until")
    until_d = date.fromisoformat(until) if until else None
    if until_d is not None and as_of > until_d:
        until_d = None                      # cooldown expired
    if drawdown <= -dd_limit and until_d is None:
        until_d = as_of + timedelta(days=cooldown)
        print(f"  [!] 投机桶熔断触发: 权益 ${equity:.0f} 距高水位 ${hwm:.0f} "
              f"回撤 {drawdown * 100:.0f}% — 暂停新买入至 {until_d}")

    new_state = {"hwm": round(hwm, 2),
                 "until": until_d.isoformat() if until_d else None}
    if persist:
        _save_breaker_state(new_state)

    return SleeveStatus(
        equity=equity, realized_pnl=realized, unrealized_pnl=unrealized,
        open_positions=open_pos, hwm=hwm, drawdown_pct=drawdown,
        breaker_active=until_d is not None, breaker_until=until_d,
        n_trades=len(spec),
    )
