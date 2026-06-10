"""Daily state persistence + day-over-day diff.

Saves a compact JSON snapshot at end of each run; loads yesterday's snapshot
at start of next run to compute "what changed". This is the engine behind
"each day's report feels different" — even if the absolute state is similar,
the *diff* is fresh information.
"""
from datetime import date, timedelta
from pathlib import Path
import json

STATE_DIR = Path(__file__).resolve().parents[2] / "data" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


# Action upgrade/downgrade rank (higher = more bullish).
# Gate-renamed actions sit NEXT TO their ungated origin (等企稳再建仓 ~ 建仓)
# so a regime-gate toggle does not show up as a fake rating downgrade/upgrade
# wave in the daily diff.
ACTION_RANK = {
    "建仓": 9,
    "等企稳再建仓": 8,     # gated 建仓 — same view, entry deferred
    "加仓持有": 8,
    "试探建仓": 7,
    "持有不加": 6,
    "持有博弹性": 6,       # moonshot hold
    "观望": 5,
    "短期反弹候选": 5,     # speculative swing, not a rating change vs 观望
    "观望偏空": 4,
    "减仓": 3,
    "避免": 2,
    "清仓": 1,
}


def _state_path(d: date) -> Path:
    return STATE_DIR / f"snapshot-{d.isoformat()}.json"


def save_snapshot(
    d: date,
    *,
    recommendations,
    semi_score,
    ai_infra_report,
    themes: list[dict],
    earnings_events,
    stretched_tickers: list[str],
) -> Path:
    """Serialize today's state to JSON. Returns path written."""
    payload = {
        "date": d.isoformat(),
        "composite_semi": float(semi_score.composite_0_100),
        "composite_ai_infra": float(ai_infra_report.composite_0_100),
        "macro_score": float(semi_score.macro.get("score", 5)),
        "actions": {
            r.ticker: {
                "action": r.action,
                "action_pregate": getattr(r, "pregate_action", "") or r.action,
                "pending": getattr(r, "pending_action", "") or None,
                "Q": round(float(r.quality_score), 1),
                "R": round(float(r.risk_score), 1),
            }
            for r in recommendations
        },
        "themes": [t.get("theme", "") for t in themes],
        "earnings_upcoming": [
            {
                "ticker": e.ticker,
                "date": e.report_date.isoformat(),
                "days": int(e.days_until),
            }
            for e in earnings_events
        ],
        "stretched": list(stretched_tickers),
    }
    path = _state_path(d)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def snapshot_exists(d: date) -> bool:
    return _state_path(d).exists()


def load_snapshot(d: date) -> dict | None:
    p = _state_path(d)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def find_latest_previous(today: date, max_days_back: int = 7) -> dict | None:
    """Find most recent snapshot before `today`. None if none in range."""
    for back in range(1, max_days_back + 1):
        d = today - timedelta(days=back)
        snap = load_snapshot(d)
        if snap:
            return snap
    return None


def compute_diff(today_data: dict, prev: dict | None) -> dict:
    """Compute changes between today and the most recent previous snapshot.

    Returns a dict with keys ready for embed rendering.
    """
    if prev is None:
        return {"first_run": True}

    buy_actions = {"建仓", "加仓持有", "试探建仓"}
    sell_actions = {"减仓", "清仓"}

    composite_delta = today_data["composite_semi"] - prev["composite_semi"]
    ai_delta = today_data["composite_ai_infra"] - prev["composite_ai_infra"]
    macro_delta = today_data.get("macro_score", 5) - prev.get("macro_score", 5)

    today_actions = today_data["actions"]
    prev_actions = prev["actions"]

    upgraded, downgraded = [], []
    new_in_buy, new_in_sell = [], []
    new_in_avoid_or_clear = []

    for t, info in today_actions.items():
        yest = prev_actions.get(t)
        if not yest:
            continue
        # Rating moves compare PRE-GATE actions (the signal), so a regime-gate
        # toggle is not reported as an up/downgrade wave. Old snapshots
        # without the field fall back to the gated action.
        t_act = info.get("action_pregate") or info["action"]
        y_act = yest.get("action_pregate") or yest["action"]
        t_rank = ACTION_RANK.get(t_act, 5)
        y_rank = ACTION_RANK.get(y_act, 5)
        if t_rank - y_rank >= 2:
            upgraded.append({"ticker": t, "from": y_act, "to": t_act})
        elif y_rank - t_rank >= 2:
            downgraded.append({"ticker": t, "from": y_act, "to": t_act})
        if (info["action"] in buy_actions
                and yest["action"] not in buy_actions):
            new_in_buy.append({"ticker": t, "action": info["action"]})
        if (info["action"] in sell_actions
                and yest["action"] not in sell_actions):
            new_in_sell.append({"ticker": t, "action": info["action"]})

    # Themes added/dropped
    t_themes = set(today_data.get("themes", []))
    p_themes = set(prev.get("themes", []))
    new_themes = sorted(t_themes - p_themes)
    dropped_themes = sorted(p_themes - t_themes)

    # Stretched changes
    t_stretched = set(today_data.get("stretched", []))
    p_stretched = set(prev.get("stretched", []))
    new_stretched = sorted(t_stretched - p_stretched)
    cleared_stretched = sorted(p_stretched - t_stretched)

    # Earnings entering 7-day window today (weren't there yesterday)
    t_e7 = {e["ticker"] for e in today_data.get("earnings_upcoming", [])
            if e["days"] <= 7}
    p_e7 = {e["ticker"] for e in prev.get("earnings_upcoming", [])
            if e["days"] <= 7}
    earnings_entering_week = sorted(t_e7 - p_e7)

    return {
        "first_run": False,
        "prev_date": prev["date"],
        "composite_delta": composite_delta,
        "ai_delta": ai_delta,
        "macro_delta": macro_delta,
        "upgraded": upgraded[:6],
        "downgraded": downgraded[:6],
        "new_in_buy": new_in_buy[:6],
        "new_in_sell": new_in_sell[:6],
        "new_themes": new_themes,
        "dropped_themes": dropped_themes,
        "new_stretched": new_stretched[:6],
        "cleared_stretched": cleared_stretched[:6],
        "earnings_entering_week": earnings_entering_week,
    }


def render_diff_for_embed(diff: dict) -> dict | None:
    """Build the Discord embed field for the diff block. Returns None if first run."""
    if diff.get("first_run"):
        return {
            "name": "[变化] 今日 vs 昨日",
            "value": "_首次运行, 还没有昨日数据可对比. 明天起此处会显示日变化._",
            "inline": False,
        }

    lines = []

    # Composite changes (always show; even small deltas are signal)
    cd = diff["composite_delta"]
    ad = diff["ai_delta"]
    arrow = lambda x: "↑" if x > 0.5 else "↓" if x < -0.5 else "→"
    lines.append(f"半导体景气 {arrow(cd)} **{cd:+.1f}** "
                 f" · AI 基建 {arrow(ad)} **{ad:+.1f}**")

    # Upgraded / new buys
    if diff["new_in_buy"]:
        tickers = ", ".join(f"**{x['ticker']}** ({x['action']})"
                            for x in diff["new_in_buy"])
        lines.append(f"[+] 新进入买入区: {tickers}")
    if diff["upgraded"]:
        items = ", ".join(f"**{x['ticker']}** ({x['from']}→{x['to']})"
                          for x in diff["upgraded"][:3])
        lines.append(f"[↑] 评级上调: {items}")

    # Downgraded / new sells
    if diff["new_in_sell"]:
        tickers = ", ".join(f"**{x['ticker']}** ({x['action']})"
                            for x in diff["new_in_sell"])
        lines.append(f"[-] 新进入卖出区: {tickers}")
    if diff["downgraded"]:
        items = ", ".join(f"**{x['ticker']}** ({x['from']}→{x['to']})"
                          for x in diff["downgraded"][:3])
        lines.append(f"[↓] 评级下调: {items}")

    # Stretched
    if diff["new_stretched"]:
        lines.append(f"[!] 新触发拉伸警告: **{', '.join(diff['new_stretched'])}**")
    if diff["cleared_stretched"]:
        lines.append(f"[*] 拉伸解除: {', '.join(diff['cleared_stretched'])}")

    # Earnings entering 7d window
    if diff["earnings_entering_week"]:
        lines.append("[$] 本周财报进入 7 天窗口: "
                     + ", ".join(f"**{t}**"
                                 for t in diff["earnings_entering_week"]))

    # Themes
    if diff["new_themes"]:
        lines.append(f"[T+] 新主题: {', '.join(diff['new_themes'])}")
    if diff["dropped_themes"]:
        lines.append(f"[T-] 主题消失: {', '.join(diff['dropped_themes'])}")

    if not lines:
        return {
            "name": f"[变化] 今日 vs {diff['prev_date']}",
            "value": "_今日核心信号无重大变化, 维持昨日观察_",
            "inline": False,
        }

    return {
        "name": f"[变化] 今日 vs {diff['prev_date']}",
        "value": "\n".join(lines),
        "inline": False,
    }
