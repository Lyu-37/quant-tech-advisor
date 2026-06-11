"""No-ticker event calendar: IPOs, FOMC, holidays, expirations, index events.

The system's information universe is ticker-keyed (news per ticker, earnings
per ticker) — anything without a symbol was invisible. This module covers
that blind spot with a hand-maintained calendar (configs/market_events.yaml),
rendered into both briefs with loud T-0/T-1 markers.

The blind spot was exposed 2026-06-11: the SpaceX IPO (largest in history,
next day) appeared nowhere in the briefs because SPCX had no ticker yet.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import yaml

EVENTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "market_events.yaml"

KIND_TAG = {
    "ipo": "[IPO]", "macro": "[宏观]", "holiday": "[休市]",
    "expiry": "[到期]", "index": "[指数]", "other": "[事件]",
}


@dataclass
class MarketEvent:
    date: date
    name: str
    kind: str = "other"
    note: str = ""


def load_market_events(path: Path | None = None) -> list[MarketEvent]:
    p = path or EVENTS_PATH
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for e in raw.get("events", []) or []:
        try:
            d = e["date"]
            if isinstance(d, str):
                d = date.fromisoformat(d)
            out.append(MarketEvent(
                date=d, name=str(e.get("name", "")),
                kind=str(e.get("kind", "other")).lower(),
                note=" ".join(str(e.get("note", "")).split()),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    out.sort(key=lambda x: x.date)
    return out


def upcoming_events(events: list[MarketEvent], as_of: date,
                    within_days: int = 14) -> list[MarketEvent]:
    return [e for e in events if 0 <= (e.date - as_of).days <= within_days]


def render_events_field(events: list[MarketEvent], as_of: date,
                        within_days: int = 14, limit: int = 6) -> dict | None:
    """Discord embed field. Today/tomorrow events get loud markers."""
    up = upcoming_events(events, as_of, within_days)
    if not up:
        return None
    lines = []
    for e in up[:limit]:
        days = (e.date - as_of).days
        when = ("**今天**" if days == 0 else "**明天**" if days == 1
                else f"{days} 天后")
        marker = "▲ " if days <= 1 else "○ "
        tag = KIND_TAG.get(e.kind, "[事件]")
        line = f"{marker}{tag} `{e.date.isoformat()}` ({when})  **{e.name}**"
        if e.note and days <= 3:
            line += f"\n    _{e.note}_"
        lines.append(line)
    return {
        "name": "[日历] 无代码事件窗  ·  IPO / FOMC / 到期 / 休市",
        "value": "\n".join(lines),
        "inline": False,
    }
