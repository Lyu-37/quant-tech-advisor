"""Valuation dimension — growth-adjusted, not naive PE.

Naive forward-PE misleads on tech: high-growth names look "expensive" on PE
but cheap on PEG (PE/growth), and unprofitable names have no PE at all.

Approach:
  1. PEG ratio is primary  (PE / earnings growth — fair across growth rates)
       <1.0 = cheap, 1.0-2.0 = fair, >2.0 = expensive
  2. Forward PE as secondary signal
  3. Price/Sales as a backstop for unprofitable names (negative PE) —
     flags extreme multiples (PS > 30 = nosebleed) the PEG can't see

Output is a label + a mild tilt fed into quality scoring. Valuation is a
SLOW signal — it should nudge, not dominate momentum/quality.
"""
from dataclasses import dataclass

from .factors import NON_FUNDAMENTAL_SYMBOLS, _as_float, get_info


@dataclass
class Valuation:
    ticker: str
    forward_pe: float | None = None
    trailing_pe: float | None = None
    peg: float | None = None
    ps: float | None = None
    gross_margin: float | None = None
    label: str = ""             # 便宜 / 合理 / 偏贵 / 极贵 / 无盈利
    tilt: float = 0.0           # -1.5 .. +1.5 quality points (mild)
    detail: str = ""
    raw_label: str = ""         # pre-context label (before trap adjustment)
    trap_warning: bool = False  # cheap-on-paper but parabolic / above target


def apply_context(v: "Valuation", stretch_severity: int = 0,
                  above_analyst_target: bool = False) -> "Valuation":
    """Neutralize the 'cheap' tilt when context says it's a value trap.

    A low PEG is only a BUY signal when technicals are sane. Cheap + parabolic
    (stretch >= 2) or cheap + already-above-analyst-target is the classic
    cyclical top trap (e.g. memory stocks: low PE *because* earnings peaked).
    """
    v.raw_label = v.label
    # Only the positive (cheap/合理) tilts are vulnerable to the trap.
    if v.tilt > 0 and (stretch_severity >= 2 or above_analyst_target):
        reasons = []
        if stretch_severity >= 2:
            reasons.append(f"技术极度拉伸 (拉伸 {stretch_severity}/4)")
        if above_analyst_target:
            reasons.append("现价已超分析师目标")
        v.trap_warning = True
        v.label = f"账面便宜但有陷阱"
        v.tilt = 0.0   # neutralize — don't reward cheap-on-paper at a top
        v.detail = f"{v.detail} — 警告: {', '.join(reasons)} (周期顶低PE陷阱)"
    return v


def compute_valuation(ticker: str, info: dict | None = None) -> Valuation | None:
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    if info is None:
        info = get_info(ticker)
    if not info:
        return None

    v = Valuation(ticker=ticker)
    v.forward_pe   = _as_float(info.get("forwardPE"))
    v.trailing_pe  = _as_float(info.get("trailingPE"))
    v.peg          = _as_float(info.get("pegRatio") or info.get("trailingPegRatio"))
    v.ps           = _as_float(info.get("priceToSalesTrailing12Months"))
    v.gross_margin = _as_float(info.get("grossMargins"))
    profit_margin  = _as_float(info.get("profitMargins"))

    # Missing forwardPE is NOT evidence of unprofitability (Yahoo hiccups,
    # missing estimates). Only declare 无盈利 when there is positive evidence
    # of losses; with a positive trailingPE or margin, fall through to the
    # profitable paths using trailingPE as the PE fallback.
    has_profit_evidence = ((v.trailing_pe is not None and v.trailing_pe > 0)
                           or (profit_margin is not None and profit_margin > 0))
    if v.forward_pe is None and has_profit_evidence:
        v.forward_pe = v.trailing_pe
        if v.forward_pe is None:
            # Profitable by margin but no PE fields at all — data gap, not loss.
            v.label = "数据不足"
            v.tilt = 0.0
            v.detail = ("盈利 (margin "
                        + (f"{profit_margin * 100:.0f}%" if profit_margin else "?")
                        + ") 但 PE 字段缺失, 不参与估值分类")
            return v

    # ---- Classify ----
    # Case 1: unprofitable (negative PE, or no PE and no profit evidence)
    if v.forward_pe is None or v.forward_pe < 0:
        if v.forward_pe is None and v.ps is None:
            v.label = "数据不足"
            v.tilt = 0.0
            v.detail = "估值字段缺失, 无法分类 (非 '无盈利')"
            return v
        if v.ps is not None:
            if v.ps > 40:
                v.label = "极贵 (亏损+高PS)"
                v.tilt = -1.5
                v.detail = f"无盈利, PS {v.ps:.0f} (极端)"
            elif v.ps > 20:
                v.label = "偏贵 (亏损)"
                v.tilt = -0.8
                v.detail = f"无盈利, PS {v.ps:.0f}"
            else:
                v.label = "无盈利"
                v.tilt = -0.3
                v.detail = f"无盈利, PS {v.ps:.0f}"
        else:
            v.label = "无盈利"
            v.tilt = -0.3
            v.detail = "无盈利, 无估值数据"
        return v

    # Case 2: profitable — use PEG primary
    if v.peg is not None and v.peg > 0:
        if v.peg < 1.0:
            v.label = "便宜"
            v.tilt = +1.2
            v.detail = f"PEG {v.peg:.2f} (<1, 成长调整后便宜), fwdPE {v.forward_pe:.0f}"
        elif v.peg < 1.5:
            v.label = "合理"
            v.tilt = +0.4
            v.detail = f"PEG {v.peg:.2f} (合理), fwdPE {v.forward_pe:.0f}"
        elif v.peg < 2.5:
            v.label = "偏贵"
            v.tilt = -0.4
            v.detail = f"PEG {v.peg:.2f} (偏贵), fwdPE {v.forward_pe:.0f}"
        else:
            v.label = "贵"
            v.tilt = -1.0
            v.detail = f"PEG {v.peg:.2f} (贵), fwdPE {v.forward_pe:.0f}"
        return v

    # Case 3: profitable but no PEG — fall back to forward PE
    if v.forward_pe < 15:
        v.label = "便宜"
        v.tilt = +1.0
        v.detail = f"fwdPE {v.forward_pe:.0f} (低)"
    elif v.forward_pe < 30:
        v.label = "合理"
        v.tilt = +0.2
        v.detail = f"fwdPE {v.forward_pe:.0f}"
    elif v.forward_pe < 50:
        v.label = "偏贵"
        v.tilt = -0.5
        v.detail = f"fwdPE {v.forward_pe:.0f} (偏高)"
    else:
        v.label = "贵"
        v.tilt = -1.0
        v.detail = f"fwdPE {v.forward_pe:.0f} (高)"
    return v


def render_valuation_field(val_data: dict) -> dict | None:
    """Discord embed field — valuation labels, cheapest first."""
    if not val_data:
        return None
    items = [v for v in val_data.values() if v.label]
    if not items:
        return None
    # Sort by tilt desc (cheap first)
    items.sort(key=lambda v: -v.tilt)

    # Sort: real-cheap first, then traps, then expensive
    items.sort(key=lambda v: (-(v.tilt), v.trap_warning))
    rows = []
    for v in items[:12]:
        if v.trap_warning:
            icon = "[陷阱]"
        elif v.tilt >= 0.8:
            icon = "▲"
        elif v.tilt > 0:
            icon = "△"
        elif v.tilt > -0.8:
            icon = "▽"
        else:
            icon = "▼"
        rows.append(f"{icon} `{v.ticker}` {v.label}  _{v.detail}_")
    return {
        "name": "[估值] 成长调整估值 (PEG, 含周期陷阱过滤)  ·  便宜→贵",
        "value": "\n".join(rows),
        "inline": False,
    }
