"""Rule-based investment-master screens ("distilled guru models").

Each guru's published methodology is encoded as deterministic quantitative
rules over yfinance fundamentals — no LLM, no paid data, no hallucination.
Every guru scores each stock and votes 强烈看好 / 看好 / 中性 / 回避.
A consensus aggregates the votes.

Gurus implemented:
  - Buffett        : moat (high margins) + quality (ROE) + low debt + fair price
  - Graham         : deep value + margin of safety (Graham Number, low PB/PE)
  - Lynch          : GARP — PEG < 1 + earnings growth + not nosebleed
  - Greenblatt     : Magic Formula proxy — ROA for ROIC, EV/EBITDA for EV/EBIT
  - Piotroski      : SIMPLIFIED static health checklist — the real F-Score uses
                     year-over-year DELTAS (ΔROA, Δleverage, Δmargin, Δturnover,
                     no dilution); .info only gives levels, so this version has
                     much weaker discrimination on uniformly-profitable tech.
  - Burry          : deep value + FCF yield + cheap EV/EBITDA

Honesty notes baked into the output:
  - Graham/Burry-style value rules are structurally bearish on growth tech;
    a low bullish count is partly that, not pure bearishness.
  - Cyclical trap: low PE/PEG at an earnings-cycle peak lights up the value
    rules in unison. `analyze_gurus` accepts technical context (stretch /
    above-analyst-target) and flags the consensus instead of presenting it raw.
"""
from dataclasses import dataclass, field

from .factors import NON_FUNDAMENTAL_SYMBOLS, _as_float, get_info


@dataclass
class GuruVote:
    guru: str
    verdict: str            # 强烈看好 / 看好 / 中性 / 回避
    score: float            # 0-100 (guru-specific)
    reasons: list = field(default_factory=list)


@dataclass
class GuruConsensus:
    ticker: str
    votes: list             # list[GuruVote]
    bullish: int = 0        # count 强烈看好 + 看好
    bearish: int = 0        # count 回避
    neutral: int = 0
    consensus: str = ""     # 大佬共识 verdict
    score: float = 0.0      # 0-100 aggregate
    top_fans: list = field(default_factory=list)   # gurus who like it most
    trap_warning: bool = False   # value rules lit up at a technical extreme


def _get_fundamentals(ticker: str) -> dict | None:
    if ticker in NON_FUNDAMENTAL_SYMBOLS:
        return None
    info = get_info(ticker)
    if not info or info.get("marketCap") is None:
        return None
    return info


# ---------- individual guru screens ----------

def _buffett(f: dict) -> GuruVote:
    """Wonderful business at fair price: high ROE, wide moat (margins), low debt."""
    roe = _as_float(f.get("returnOnEquity"))
    gm = _as_float(f.get("grossMargins"))
    om = _as_float(f.get("operatingMargins"))
    de = _as_float(f.get("debtToEquity"))
    fpe = _as_float(f.get("forwardPE"))
    score = 0.0
    reasons = []
    # Moat: gross margin > 40%
    if gm and gm > 0.40:
        score += 25; reasons.append(f"宽护城河 (毛利 {gm*100:.0f}%)")
    elif gm and gm > 0.25:
        score += 12
    # Quality: ROE > 15%
    if roe and roe > 0.20:
        score += 25; reasons.append(f"高质量 (ROE {roe*100:.0f}%)")
    elif roe and roe > 0.12:
        score += 15
    # Operating efficiency
    if om and om > 0.20:
        score += 15; reasons.append(f"经营高效 (营业利润率 {om*100:.0f}%)")
    # Balance sheet: low debt. yfinance debtToEquity is ALWAYS percent
    # (NVDA=6.55 means 6.55%) — divide unconditionally, no heuristic.
    de_ratio = (de / 100.0) if de is not None else None
    if de_ratio is not None and de_ratio < 0.5:
        score += 20; reasons.append("低负债")
    elif de_ratio is not None and de_ratio < 1.5:
        score += 10
    # Fair price: forward PE not insane
    if fpe and 0 < fpe < 25:
        score += 15; reasons.append(f"估值合理 (fwdPE {fpe:.0f})")
    elif fpe and fpe > 45:
        score -= 10; reasons.append("估值偏贵")
    verdict = ("强烈看好" if score >= 75 else "看好" if score >= 55
               else "中性" if score >= 35 else "回避")
    return GuruVote("巴菲特", verdict, min(100, max(0, score)), reasons[:3])


def _graham(f: dict) -> GuruVote:
    """Deep value + margin of safety: low PB, low PE, strong current ratio."""
    pb = _as_float(f.get("priceToBook"))
    pe = _as_float(f.get("trailingPE"))
    cr = _as_float(f.get("currentRatio"))
    de = _as_float(f.get("debtToEquity"))
    score = 0.0
    reasons = []
    # Graham: PB < 1.5
    if pb and 0 < pb < 1.5:
        score += 35; reasons.append(f"低 PB ({pb:.1f})")
    elif pb and pb < 3:
        score += 15
    elif pb and pb > 8:
        score -= 10; reasons.append(f"PB 过高 ({pb:.0f})")
    # PE < 15
    if pe and 0 < pe < 15:
        score += 30; reasons.append(f"低 PE ({pe:.0f})")
    elif pe and pe < 25:
        score += 12
    elif pe and pe > 40:
        score -= 5
    # Margin of safety: Graham number proxy — PB*PE < 22.5
    if pb and pe and pb > 0 and pe > 0 and pb * pe < 22.5:
        score += 25; reasons.append("满足 Graham 安全边际")
    # Liquidity: current ratio > 2
    if cr and cr > 2:
        score += 10; reasons.append(f"流动性强 (流动比 {cr:.1f})")
    verdict = ("强烈看好" if score >= 70 else "看好" if score >= 50
               else "中性" if score >= 30 else "回避")
    return GuruVote("格雷厄姆", verdict, min(100, max(0, score)), reasons[:3])


def _lynch(f: dict) -> GuruVote:
    """GARP — growth at a reasonable price: PEG < 1, real earnings growth."""
    peg = _as_float(f.get("pegRatio") or f.get("trailingPegRatio"))
    eg = _as_float(f.get("earningsGrowth"))
    rg = _as_float(f.get("revenueGrowth"))
    fpe = _as_float(f.get("forwardPE"))
    score = 0.0
    reasons = []
    # The Lynch signature: PEG
    if peg and 0 < peg < 0.75:
        score += 45; reasons.append(f"PEG {peg:.2f} (极佳)")
    elif peg and peg < 1.0:
        score += 35; reasons.append(f"PEG {peg:.2f} (<1, 林奇最爱)")
    elif peg and peg < 1.5:
        score += 18
    elif peg and peg > 2.5:
        score -= 10; reasons.append(f"PEG {peg:.1f} (太贵)")
    # Earnings growth. CAUTION: yfinance earningsGrowth is QUARTERLY YoY —
    # at a cycle trough the comp explodes (live examples: MU +756%, GEV
    # +1816%). Growth that extreme is a low-base artifact, not GARP fuel:
    # give it a token nod, not the full Lynch bonus.
    if eg and eg > 2.0:
        score += 8; reasons.append(f"盈利增速 +{eg*100:.0f}% (低基数失真, 打折)")
    elif eg and eg > 0.25:
        score += 25; reasons.append(f"盈利高增长 ({eg*100:.0f}%)")
    elif eg and eg > 0.10:
        score += 12
    elif eg and eg < 0:
        score -= 10
    # Revenue growth (same quarterly-YoY caveat, same cap)
    if rg and rg > 2.0:
        score += 6; reasons.append(f"营收增速 +{rg*100:.0f}% (低基数失真, 打折)")
    elif rg and rg > 0.20:
        score += 20; reasons.append(f"营收高增长 ({rg*100:.0f}%)")
    elif rg and rg > 0.08:
        score += 8
    # Not nosebleed
    if fpe and fpe > 60:
        score -= 10
    verdict = ("强烈看好" if score >= 70 else "看好" if score >= 50
               else "中性" if score >= 30 else "回避")
    return GuruVote("彼得林奇", verdict, min(100, max(0, score)), reasons[:3])


def _greenblatt(f: dict) -> GuruVote:
    """Magic Formula: high ROIC (ROA proxy) + high earnings yield (1/EV-EBITDA)."""
    roa = _as_float(f.get("returnOnAssets"))
    ev_ebitda = _as_float(f.get("enterpriseToEbitda"))
    score = 0.0
    reasons = []
    # ROIC proxy (ROA)
    if roa and roa > 0.15:
        score += 40; reasons.append(f"高资本回报 (ROA {roa*100:.0f}%)")
    elif roa and roa > 0.08:
        score += 22; reasons.append(f"资本回报中等 (ROA {roa*100:.0f}%)")
    elif roa and roa > 0.03:
        score += 8
    # Earnings yield = 1 / EV-EBITDA. Low EV/EBITDA = high yield = cheap
    if ev_ebitda and 0 < ev_ebitda < 12:
        score += 45; reasons.append(f"高盈利收益率 (EV/EBITDA {ev_ebitda:.0f})")
    elif ev_ebitda and ev_ebitda < 20:
        score += 25; reasons.append(f"EV/EBITDA {ev_ebitda:.0f}")
    elif ev_ebitda and ev_ebitda > 35:
        score -= 5; reasons.append(f"EV/EBITDA {ev_ebitda:.0f} (贵)")
    verdict = ("强烈看好" if score >= 70 else "看好" if score >= 50
               else "中性" if score >= 30 else "回避")
    return GuruVote("神奇公式", verdict, min(100, max(0, score)), reasons[:3])


def _piotroski(f: dict) -> GuruVote:
    """SIMPLIFIED static health checklist, NOT the real F-Score.

    Piotroski's 9 points are mostly year-over-year IMPROVEMENTS (ΔROA,
    Δleverage, Δliquidity, Δmargin, Δturnover, no dilution). .info only
    exposes current levels, so this version checks levels — fine as a
    "is this a financially sound company" gate, but expect uniformly high
    scores (6-8/9) across profitable mega-cap tech. Low discrimination.
    """
    score_pts = 0
    reasons = []
    roa = _as_float(f.get("returnOnAssets"))
    ocf = _as_float(f.get("operatingCashflow"))
    fcf = _as_float(f.get("freeCashflow"))
    de = _as_float(f.get("debtToEquity"))
    cr = _as_float(f.get("currentRatio"))
    gm = _as_float(f.get("grossMargins"))
    eg = _as_float(f.get("earningsGrowth"))
    ni = _as_float(f.get("netIncomeToCommon"))
    # Profitability
    if roa and roa > 0:
        score_pts += 1
    if ocf and ocf > 0:
        score_pts += 1
    if ni and ni > 0:
        score_pts += 1
    if ocf and ni and ocf > ni:   # quality of earnings
        score_pts += 1
    # Leverage / liquidity (debtToEquity is always percent — see _buffett)
    de_ratio = (de / 100.0) if de is not None else None
    if de_ratio is not None and de_ratio < 1.0:
        score_pts += 1
    if cr and cr > 1.5:
        score_pts += 1
    # Operating efficiency
    if gm and gm > 0.30:
        score_pts += 1
    if eg and eg > 0:
        score_pts += 1
    if fcf and fcf > 0:
        score_pts += 1
    # 0-9 -> 0-100
    score = score_pts / 9 * 100
    reasons.append(f"财务健康 {score_pts}/9 (简化静态版)")
    if score_pts >= 7:
        reasons.append("财务健康优秀")
    elif score_pts <= 3:
        reasons.append("财务健康差")
    verdict = ("强烈看好" if score_pts >= 8 else "看好" if score_pts >= 6
               else "中性" if score_pts >= 4 else "回避")
    return GuruVote("Piotroski", verdict, score, reasons[:2])


def _burry(f: dict) -> GuruVote:
    """Deep value: FCF yield + cheap EV/EBITDA + not overpriced on sales."""
    fcf = _as_float(f.get("freeCashflow"))
    mcap = _as_float(f.get("marketCap"))
    ev_ebitda = _as_float(f.get("enterpriseToEbitda"))
    ps = _as_float(f.get("priceToSalesTrailing12Months"))
    score = 0.0
    reasons = []
    # FCF yield
    if fcf and mcap and mcap > 0:
        fcf_yield = fcf / mcap
        if fcf_yield > 0.06:
            score += 40; reasons.append(f"FCF 收益率 {fcf_yield*100:.0f}% (高)")
        elif fcf_yield > 0.03:
            score += 22; reasons.append(f"FCF 收益率 {fcf_yield*100:.0f}%")
        elif fcf_yield < 0.01:
            score -= 10
    # Cheap EV/EBITDA
    if ev_ebitda and 0 < ev_ebitda < 12:
        score += 35; reasons.append(f"EV/EBITDA {ev_ebitda:.0f} (便宜)")
    elif ev_ebitda and ev_ebitda < 20:
        score += 15
    # Not crazy on sales
    if ps and ps < 3:
        score += 25; reasons.append(f"低 PS ({ps:.1f})")
    elif ps and ps > 25:
        score -= 15; reasons.append(f"PS {ps:.0f} (极贵)")
    verdict = ("强烈看好" if score >= 70 else "看好" if score >= 50
               else "中性" if score >= 30 else "回避")
    return GuruVote("Burry", verdict, min(100, max(0, score)), reasons[:3])


GURU_FUNCS = [_buffett, _graham, _lynch, _greenblatt, _piotroski, _burry]


def analyze_gurus(ticker: str, stretch_severity: int = 0,
                  above_analyst_target: bool = False,
                  fundamentals: dict | None = None) -> GuruConsensus | None:
    """Run all guru screens on a ticker, return consensus.

    `stretch_severity` / `above_analyst_target` provide the technical context
    for the cyclical-trap flag: when the value rules light up on a name that
    is simultaneously parabolic (stretch >= 2) or already above the analyst
    target, the "cheap" consensus is the classic peak-earnings trap (low PE
    BECAUSE earnings peaked) and must not be presented raw.
    """
    f = fundamentals if fundamentals is not None else _get_fundamentals(ticker)
    if f is None:
        return None
    votes = [fn(f) for fn in GURU_FUNCS]

    bullish = sum(1 for v in votes if v.verdict in ("强烈看好", "看好"))
    bearish = sum(1 for v in votes if v.verdict == "回避")
    neutral = len(votes) - bullish - bearish
    avg_score = sum(v.score for v in votes) / len(votes)

    # Consensus
    if bullish >= 4:
        consensus = "多位大佬看好"
    elif bullish >= 2 and bearish <= 1:
        consensus = "部分大佬看好"
    elif bearish >= 4:
        consensus = "多位大佬回避"
    else:
        consensus = "分歧/中性"

    # Top fans: gurus with highest score who are bullish
    fans = sorted([v for v in votes if v.verdict in ("强烈看好", "看好")],
                  key=lambda v: -v.score)
    top_fans = [(v.guru, v.verdict, v.reasons[0] if v.reasons else "")
                for v in fans[:3]]

    # Cyclical-trap flag: bullish value consensus + parabolic technicals.
    # Same context rule as valuation.apply_context — keeps the two modules
    # from contradicting each other on the same day (e.g. MU at a memory-
    # cycle top: valuation says trap, gurus must not say "everyone loves it").
    trap = bool(bullish >= 2 and (stretch_severity >= 2 or above_analyst_target))
    if trap:
        consensus += " (周期顶低估值陷阱风险)"

    return GuruConsensus(
        ticker=ticker, votes=votes,
        bullish=bullish, bearish=bearish, neutral=neutral,
        consensus=consensus, score=avg_score, top_fans=top_fans,
        trap_warning=trap,
    )


def analyze_guru_universe(tickers: list[str],
                          stretch_map: dict[str, int] | None = None,
                          above_target_map: dict[str, bool] | None = None,
                          ) -> dict[str, GuruConsensus]:
    stretch_map = stretch_map or {}
    above_target_map = above_target_map or {}
    out = {}
    for t in tickers:
        c = analyze_gurus(
            t,
            stretch_severity=stretch_map.get(t, 0),
            above_analyst_target=above_target_map.get(t, False),
        )
        if c is not None:
            out[t] = c
    return out


def render_guru_field(consensus_map: dict[str, GuruConsensus],
                      top_n: int = 8) -> dict | None:
    """Discord embed field — stocks most loved by the masters."""
    if not consensus_map:
        return None
    # Sort: clean consensus first, trap-flagged sink below (then by score)
    ranked = sorted(consensus_map.values(),
                    key=lambda c: (c.trap_warning, -c.bullish, -c.score))
    rows = []
    for c in ranked[:top_n]:
        if c.bullish == 0 and c.score < 40:
            continue
        fans = ", ".join(g for g, _, _ in c.top_fans[:3])
        trap_tag = "  [陷阱?] 周期顶低估值" if c.trap_warning else ""
        rows.append(
            f"`{c.ticker}` **{c.bullish}/6 大佬看好** (均分 {c.score:.0f})"
            + (f" · {fans}" if fans else "") + trap_tag
        )
    if not rows:
        return None
    return {
        "name": "[大佬] 投资大师共识  ·  谁被几位大佬看好 (价值规则对成长股结构性偏空)",
        "value": "\n".join(rows),
        "inline": False,
    }
