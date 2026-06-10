"""Position recommendation engine.

For every ticker in the focus universe, compute a concrete action
(建仓 / 加仓 / 持有 / 减仓 / 清仓 / 避免) backed by a rigorous decision
chain — and translate the chain into plain Chinese a beginner can follow.

Scoring overview:
  Quality (0-100, higher = better) — should I want to own this?
    - Trend alignment (40%)
    - Momentum percentile (30%)
    - Relative strength vs sector (30%)

  Risk (0-100, higher = more dangerous) — how close is this to a top?
    - Stretch severity (40%)
    - Range position (30%) — where is price in 52-week range
    - Realized volatility (30%)

  Action matrix (quality × risk):
    Q-high + R-low   = 建仓 (strong buy)
    Q-high + R-mid   = 加仓持有 (accumulate with caution)
    Q-high + R-high  = 持有不加 (hold, no new entry)
    Q-mid  + R-low   = 试探建仓 (small starter position)
    Q-mid  + R-mid   = 观望 (wait)
    Q-mid  + R-high  = 减仓 (trim)
    Q-low  + R-low   = 观望偏空 (wait, slight bias to avoid)
    Q-low  + R-mid   = 避免 (avoid new)
    Q-low  + R-high  = 清仓 (exit if held)
"""
from dataclasses import dataclass, field
import pandas as pd

from .indicators import relative_strength
from .universe import categorize_hot_tech
from .factors import QualityFactor, PEADSignal


@dataclass
class TickerRecommendation:
    ticker: str
    category: str                 # 子板块名
    action: str                   # 建仓 / 加仓 / 持有 / 减仓 / 清仓 / 避免 / 观望 / 试探建仓
    conviction: int               # 1-5, 5 = 高置信
    quality_score: float          # 0-100
    risk_score: float             # 0-100
    suggested_dollars: float | None  # 推荐金额 (假设有 $150 投机预算)
    headline: str                 # 一行核心理由
    supports: list[str] = field(default_factory=list)    # 大白话支持理由
    contras: list[str] = field(default_factory=list)     # 反对意见
    cancel_trigger: str = ""      # 何时取消这个建议
    # Signal-level action BEFORE the regime gate renamed it. The daily diff
    # compares THIS, so a gate toggle (建仓 -> 等企稳再建仓) doesn't show up
    # as a fake rating downgrade wave.
    pregate_action: str = ""


# ---------- 大白话翻译器 ----------

def translate_stretch(severity: int, dist_sma200: float) -> str:
    if severity == 0:
        return "现价离 200 天均价不算远, 没透支"
    if severity == 1:
        return f"现价比 200 天均价高 {dist_sma200 * 100:.0f}%, 略偏高但还在合理区"
    if severity == 2:
        return (f"现价比 200 天均价高 {dist_sma200 * 100:.0f}%, "
                "历史上这种位置容易震荡")
    if severity == 3:
        return (f"现价比 200 天均价高 {dist_sma200 * 100:.0f}%, "
                "是抛物线尾段特征, 大概率短期回调")
    return (f"极端拉伸 — 现价比 200 天均价高出 {dist_sma200 * 100:.0f}%, "
            "这种状态历史上几乎都以快速回调收尾, 不要追入")


def translate_trend(label: str) -> str:
    return {
        "strong_bull": "技术形态非常强 (短/中/长期均线全部多头排列)",
        "bull": "技术形态偏强 (中期均线在长期均线上方)",
        "weak_bull": "技术形态混合, 但仍在长期均线上方",
        "mixed": "技术形态混乱, 方向不清",
        "weak_bear": "技术形态偏弱",
        "bear": "技术形态走弱 (中期均线已破长期)",
        "strong_bear": "技术形态非常弱 (空头排列)",
        "insufficient_data": "数据不足",
    }.get(label, label)


def translate_rs(rs: dict) -> str:
    spread = rs.get("spread", 0)
    if rs.get("label") == "no_data":
        return ""
    if spread > 0.20:
        return f"比 SMH 板块强势 (60 天多涨 {spread * 100:.0f}%)"
    if spread > 0.05:
        return f"略强于 SMH 板块 (60 天多涨 {spread * 100:.0f}%)"
    if spread > -0.05:
        return "与 SMH 板块同步"
    if spread > -0.20:
        return f"略弱于 SMH 板块 (60 天少涨 {-spread * 100:.0f}%)"
    return f"明显落后 SMH 板块 (60 天少涨 {-spread * 100:.0f}%) — 可能是补涨候选"


def translate_vol(vol_annualized: float) -> str:
    if pd.isna(vol_annualized):
        return ""
    pct = vol_annualized * 100
    if pct < 25:
        return f"波动率 {pct:.0f}% — 偏稳"
    if pct < 40:
        return f"波动率 {pct:.0f}% — 正常"
    if pct < 60:
        return f"波动率 {pct:.0f}% — 偏高, 单日 ±3-5% 常见"
    if pct < 80:
        return f"波动率 {pct:.0f}% — 高, 单日 ±5-7% 常见"
    return f"波动率 {pct:.0f}% — 极高, 单日 ±7-10% 不罕见"


def translate_action_explanation(action: str) -> str:
    """One-paragraph plain Chinese explanation of what the action means."""
    return {
        "建仓":
            "现在是初次买入的好时机 — 质量好, 风险低. 适合用 30-50% 的预定预算入场, "
            "留 50-70% 等回调再加.",
        "加仓持有":
            "质量好但已经涨了不少, 风险中等. 如果你已经持有可以继续拿, "
            "想加新仓建议分 2-3 次买, 不要一次梭哈.",
        "持有不加":
            "质量好但已经太贵了 — 已经持有的继续拿, 但**不要追加**. 设好止损保护利润.",
        "试探建仓":
            "质量中等, 风险低 — 小金额 (10-15% 预算) 试一下, 涨了再加, 跌了认账.",
        "短期反弹候选":
            "基本面有伤但**短期技术反弹动能起来了**. 小仓位 ($30-50, 不超过预算 20%) "
            "博一波纯技术反弹. **一周内不涨就走**, 这不是长线持有标的. "
            "比 '避免' 激进, 比 '试探建仓' 更投机.",
        "持有博弹性":
            "10x 乐透仓位且动能还在. **仅适用于有浮盈的仓位**: 把止损上移到保本位, "
            "涨到目标价分批止盈 (house-money 玩法). **若你是高位买入的深套仓, "
            "这条不构成继续持有的理由** — 按你自己的止损纪律执行.",
        "观望":
            "信号模糊, 不上不下. 拿着钱等更明确的方向 — 等价格回调或趋势确认再行动.",
        "减仓":
            "已经持有的话, 现在是分批卖出锁定利润的好时机. 一次卖 1/3 到 1/2.",
        "观望偏空":
            "走势偏弱, 不建议新买. 已经持有的等反弹再考虑卖.",
        "避免":
            "现在质量和风险都不站在你这边, 不要新买. 已经持有的设好止损.",
        "清仓":
            "走势疲软 + 风险高 = 双重不利. 已经持有的建议分批卖光, "
            "把钱转到质量更好的标的.",
    }.get(action, "")


# ---------- 评分函数 ----------

# Risk appetite profiles — shift the action-matrix thresholds.
# Aggressive: lower quality bar + much higher risk tolerance, so high-momentum
# high-vol names (IONQ, SOXL, etc.) don't auto-trigger 减仓/清仓.
RISK_PROFILES = {
    "conservative": {"q_high": 65, "q_mid": 40, "r_low": 35, "r_mid": 60,
                     "vol_weight": 0.40, "vol_div": 0.045},
    "balanced":     {"q_high": 62, "q_mid": 40, "r_low": 42, "r_mid": 67,
                     "vol_weight": 0.35, "vol_div": 0.052},
    # Aggressive: high risk tolerance (R<77 still buyable) so high-vol momentum
    # names ride; but q_mid kept at 42 to preserve discrimination on the low
    # end (junk stocks still get 观望偏空/避免, not a flood of 试探建仓).
    "aggressive":   {"q_high": 57, "q_mid": 42, "r_low": 48, "r_mid": 77,
                     "vol_weight": 0.30, "vol_div": 0.060},
}
DEFAULT_PROFILE = "aggressive"


def compute_quality(
    summary: dict,
    rs_smh: dict | None,
    quality_factor: QualityFactor | None = None,
    pead: PEADSignal | None = None,
    valuation_tilt: float = 0.0,
) -> float:
    """0-100, higher = better technical + fundamental quality.

    Research-backed weights:
      - Trend alignment (25%): SMA stack ordering
      - 12-1 momentum (20%): Jegadeesh-Titman; replaces raw 20d momentum
      - Short-term momentum (10%): captures recent acceleration
      - Relative strength (15%): vs sector
      - Quality factor (20%): QMJ-inspired fundamentals (profitability + safety)
      - PEAD boost (10%): post-earnings drift signal continues
    """
    trend = summary.get("trend", {}).get("score", 5.0)
    mom_short = summary.get("momentum", {}).get("score", 5.0)
    mom_12_1 = summary.get("momentum_12_1", {}).get("score", 5.0)
    rs_score = (rs_smh or {}).get("score", 5.0)
    quality_pts = quality_factor.composite_score if quality_factor else 5.0

    # PEAD: positive drift = +1 to +2 boost, negative drift = -1 to -2 penalty
    pead_pts = 5.0  # neutral baseline
    if pead and pead.in_drift_window and pead.drift_since_earnings is not None:
        drift = pead.drift_since_earnings
        # Map drift to 0-10: +20% = 10, -20% = 0
        pead_pts = float(max(0, min(10, 5 + drift * 25)))
        # Drift up but the reported EPS surprise was NEGATIVE: price action
        # disagrees with fundamentals — don't reward it (cap at neutral).
        if pead.surprise_pct is not None and pead.surprise_pct < 0:
            pead_pts = min(pead_pts, 5.0)

    weighted = (
        trend * 0.25
        + mom_12_1 * 0.20      # 12-1 momentum (academic standard)
        + mom_short * 0.10     # short-term momentum
        + rs_score * 0.15
        + quality_pts * 0.20   # QMJ fundamentals
        + pead_pts * 0.10      # PEAD boost/penalty
    )
    # Valuation is a MILD tilt (±1.5 quality pts on 0-100 scale), not a driver —
    # cheap names get a small nudge up, nosebleed valuations a nudge down.
    return float(max(0, min(100, weighted * 10 + valuation_tilt)))


def compute_risk(summary: dict, range_pos: float,
                 profile: str = DEFAULT_PROFILE) -> float:
    """0-100, higher = more dangerous.

    Low-vol anomaly (Frazzini-Pedersen) says high IVOL underperforms, so
    conservative profiles weight vol heavily. Aggressive profiles weight it
    lightly (the user *wants* volatility for upside).
    """
    p = RISK_PROFILES.get(profile, RISK_PROFILES[DEFAULT_PROFILE])
    vol_weight = p["vol_weight"]
    vol_div = p["vol_div"]
    other = (1.0 - vol_weight)

    sev = summary.get("stretch", {}).get("severity", 0)   # 0-4
    vol = summary.get("realized_vol_20d", 0.30) or 0.30
    sev_pts = (sev / 4) * 10
    range_pts = range_pos * 10
    vol_pts = min(10, max(0, (vol - 0.15) / vol_div))
    # Split the non-vol weight 4:3 between stretch and range position
    sev_weight = other * (0.40 / 0.70)
    range_weight = other * (0.30 / 0.70)
    weighted = sev_pts * sev_weight + range_pts * range_weight + vol_pts * vol_weight
    return float(weighted * 10)


def range_position(close: pd.Series, window: int = 252) -> float:
    w = close.tail(window) if len(close) >= window else close
    lo, hi = float(w.min()), float(w.max())
    if hi == lo:
        return 0.5
    return (float(close.iloc[-1]) - lo) / (hi - lo)


def pick_action(quality: float, risk: float,
                profile: str = DEFAULT_PROFILE) -> tuple[str, int]:
    """Return (action_label, conviction 1-5)."""
    p = RISK_PROFILES.get(profile, RISK_PROFILES[DEFAULT_PROFILE])
    q_high, q_mid = p["q_high"], p["q_mid"]
    r_low, r_mid = p["r_low"], p["r_mid"]

    if quality >= q_high and risk < r_low:
        return "建仓", 5
    if quality >= q_high and risk < r_mid:
        return "加仓持有", 4
    if quality >= q_high:  # risk >= r_mid
        return "持有不加", 3
    if quality >= q_mid and risk < r_low:
        return "试探建仓", 3
    if quality >= q_mid and risk < r_mid:
        return "观望", 2
    if quality >= q_mid:
        return "减仓", 3
    # quality < q_mid
    if risk < r_low:
        return "观望偏空", 2
    if risk < r_mid:
        return "避免", 3
    return "清仓", 4


def is_mean_reversion_candidate(summary: dict, news_summary=None) -> bool:
    """Detect short-term technical reversal candidates.

    Filter strict to avoid letting structurally-damaged stocks (e.g. SMCI's
    accounting saga, OKLO regulatory risks) sneak in as 'reversal candidates'.
    They should stay in '避免'.

    Required ALL:
      - 12-1 momentum negative but not death spiral (-55% < m121 < -25%)
      - 60d return > +25%  (clear bounce off lows)
      - 5d return positive (bounce is current, not stale)
      - Vol < 120%  (not in chaos)
      - Stretch severity < 2 (still room to run)
      - News sentiment not heavily negative (no fresh bad catalyst)
    """
    m121 = summary.get("momentum_12_1", {}).get("value")
    ret_60d = summary.get("ret_60d") or 0
    ret_5d = summary.get("ret_5d") or 0
    vol = summary.get("realized_vol_20d") or 0
    sev = summary.get("stretch", {}).get("severity", 0)

    if m121 is None:
        return False
    if not (-0.55 <= m121 <= -0.25):
        return False           # too healthy or too dying
    if ret_60d < 0.25:
        return False           # bounce not strong enough
    if ret_5d < 0:
        return False           # bounce stale, may already be over
    if vol > 1.20:
        return False           # too chaotic to read
    if sev >= 2:
        return False           # already overextended
    if news_summary and news_summary.headlines:
        if news_summary.avg_score < -0.15:
            return False       # fresh bad news = structural damage
    return True


# ---------- 顶层评估函数 ----------

def evaluate_ticker(
    ticker: str,
    summary: dict,
    close: pd.Series,
    smh_close: pd.Series,
    quality_factor: QualityFactor | None = None,
    pead: PEADSignal | None = None,
    news_summary=None,
    profile: str = DEFAULT_PROFILE,
    is_moonshot: bool = False,
    valuation_tilt: float = 0.0,
) -> TickerRecommendation:
    rs_smh = relative_strength(close, smh_close, window=60)
    rng = range_position(close)

    quality = compute_quality(summary, rs_smh, quality_factor, pead, valuation_tilt)
    risk = compute_risk(summary, rng, profile)
    action, conviction = pick_action(quality, risk, profile)

    # Override: if a low-quality stock qualifies as mean-reversion candidate,
    # bump it from 避免/观望偏空 into 短期反弹候选 (still cautious).
    # Structurally damaged stocks (recent bad news) are blocked by the filter.
    if action in {"避免", "观望偏空"} and is_mean_reversion_candidate(summary, news_summary):
        action = "短期反弹候选"
        conviction = 3

    # Moonshot positions are lottery tickets — the core-holding Q/R matrix
    # over-fires on them. BUT the exemption has limits:
    #   - "避免" is a don't-buy signal: it stays. (The old code flipped it to
    #     "持有博弹性" on any positive 20d return — turning a negative signal
    #     into a positive-sounding one. Never again.)
    #   - "清仓" softens to "减仓" only while the name is NOT in a death
    #     spiral. Down >50% from the 52w high or 12-1 momentum < -60% is a
    #     death spiral — the exit signal stands, lottery ticket or not.
    moonshot_exit_note = ""
    if is_moonshot:
        dd_52w = summary.get("dd_from_52w_high") or 0.0
        m121_val = summary.get("momentum_12_1", {}).get("value")
        death_spiral = dd_52w < -0.50 or (m121_val is not None and m121_val < -0.60)
        if action == "清仓":
            if not death_spiral:
                action = "减仓"
                conviction = 2
            else:
                moonshot_exit_note = (f"距 52w 高 {dd_52w * 100:.0f}% — "
                                      "深度死亡螺旋, 乐透逻辑不适用, 退出信号有效")
        elif action == "减仓":
            ret_20d = summary.get("ret_20d") or 0
            if ret_20d > 0 and not death_spiral:
                # Ran hard with momentum intact: ride with house money
                action = "持有博弹性"
                conviction = 2

    # Build plain-Chinese support list
    supports = []
    contras = []
    if moonshot_exit_note:
        contras.append(moonshot_exit_note)

    trend_label = summary.get("trend", {}).get("label", "")
    supports.append(translate_trend(trend_label))

    mom_pct = (summary.get("momentum", {}).get("percentile", 0.5) or 0.5)
    if mom_pct >= 0.80:
        supports.append(f"短期动量强 (20 天涨幅前 {(1 - mom_pct) * 100:.0f}%)")
    elif mom_pct <= 0.20:
        contras.append(f"短期动量疲软 (20 天涨幅后 {mom_pct * 100:.0f}%)")

    # NEW: 12-1 momentum (Jegadeesh-Titman academic standard)
    m121 = summary.get("momentum_12_1", {})
    m121_val = m121.get("value")
    m121_pct = m121.get("percentile")
    if m121_val is not None:
        if m121_pct is not None and m121_pct >= 0.75:
            supports.append(f"长期动量强 (12-1 月动量 {m121_val * 100:+.0f}%, "
                            f"历史 {m121_pct * 100:.0f}% 分位)")
        elif m121_pct is not None and m121_pct <= 0.25:
            contras.append(f"长期动量弱 (12-1 月动量 {m121_val * 100:+.0f}%, "
                           f"历史 {m121_pct * 100:.0f}% 分位)")
        elif m121_val >= 0.30:
            supports.append(f"长期动量强 (12-1 月 +{m121_val * 100:.0f}%)")
        elif m121_val <= -0.10:
            contras.append(f"长期动量弱 (12-1 月 {m121_val * 100:.0f}%)")

    # NEW: Quality factor from fundamentals
    if quality_factor:
        if quality_factor.composite_score >= 7:
            details = []
            if quality_factor.profit_margin and quality_factor.profit_margin > 0.20:
                details.append(f"利润率 {quality_factor.profit_margin * 100:.0f}%")
            if quality_factor.roe and quality_factor.roe > 0.20:
                details.append(f"ROE {quality_factor.roe * 100:.0f}%")
            supports.append(
                f"基本面优质 ({quality_factor.label}, {', '.join(details)})"
                if details else f"基本面优质 ({quality_factor.label})"
            )
        elif quality_factor.composite_score <= 4:
            details = []
            if quality_factor.profit_margin is not None and quality_factor.profit_margin < 0:
                details.append(f"利润率 {quality_factor.profit_margin * 100:.0f}% (亏损)")
            if quality_factor.debt_to_equity and quality_factor.debt_to_equity > 200:
                details.append(f"D/E {quality_factor.debt_to_equity:.0f}% (高负债)")
            contras.append(f"基本面偏弱 ({quality_factor.label}"
                           + (", " + ", ".join(details) if details else "")
                           + ")")

    # NEW: PEAD signal
    if pead and pead.in_drift_window:
        days = pead.days_since_earnings
        drift_pct = (pead.drift_since_earnings or 0) * 100
        if pead.direction == "positive":
            supports.append(f"财报后正向漂移 (PEAD): {days} 天前财报后 "
                            f"涨 {drift_pct:+.0f}%, 历史上仍有 60d 持续动量")
        elif pead.direction == "negative":
            contras.append(f"财报后负向漂移 (PEAD): {days} 天前财报后 "
                           f"跌 {drift_pct:+.0f}%, 通常继续走弱")

    rs_text = translate_rs(rs_smh)
    if rs_text:
        if rs_smh.get("spread", 0) > 0:
            supports.append(rs_text)
        else:
            (supports if "补涨" in rs_text else contras).append(rs_text)

    stretch = summary.get("stretch", {})
    sev = stretch.get("severity", 0)
    dist_200 = summary.get("dist_sma200", 0) or 0
    stretch_text = translate_stretch(sev, dist_200)
    if sev >= 2:
        contras.append(stretch_text)
    else:
        supports.append(stretch_text)

    vol_text = translate_vol(summary.get("realized_vol_20d", 0))
    if vol_text:
        if summary.get("realized_vol_20d", 0) > 0.60:
            contras.append(vol_text)
        else:
            supports.append(vol_text)

    # Range position
    if rng > 0.95:
        contras.append(f"现价在 52 周高点附近 ({rng * 100:.0f}% 分位), 追高风险大")
    elif rng < 0.30:
        supports.append(f"现价在 52 周低位 ({rng * 100:.0f}% 分位), 下跌空间小")

    # Headline (one-line summary)
    headline = f"{action} · 质量 {quality:.0f}/100, 风险 {risk:.0f}/100"

    # Cancel trigger
    if action in ("建仓", "加仓持有", "试探建仓"):
        cancel = "若 SMH 板块跌破 SMA50 或本股跌破 SMA50, 取消此建议"
    elif action == "短期反弹候选":
        cancel = ("**纯短线**: 若 5 个交易日内未涨 +5%, 或跌破近 5 日低点, "
                  "立即清仓认错")
    elif action == "持有博弹性":
        cancel = "止损上移到保本位; 到目标价 (成本 2x) 分批止盈 1/3"
    elif action in ("减仓", "清仓"):
        cancel = "若本股 5 天内反弹超过 10% 且板块恢复多头, 暂缓减仓"
    elif action == "持有不加":
        cancel = "若本股跌破 SMA50, 升级为减仓"
    else:
        cancel = "等下一次每日扫描"

    # Suggested dollars assuming a $150 speculation budget
    suggested = None
    if action == "建仓":
        suggested = 60.0       # 40% of budget
    elif action == "加仓持有":
        suggested = 45.0       # 30%
    elif action == "试探建仓":
        suggested = 20.0       # 13%
    elif action == "短期反弹候选":
        suggested = 30.0       # 20%, technical swing trade
    elif action == "持有博弹性":
        suggested = 0.0        # already held; ride it, don't add
    elif action in ("持有不加", "观望", "观望偏空", "避免", "减仓", "清仓"):
        suggested = 0.0

    return TickerRecommendation(
        ticker=ticker,
        category=categorize_hot_tech(ticker),
        action=action,
        pregate_action=action,
        conviction=conviction,
        quality_score=quality,
        risk_score=risk,
        suggested_dollars=suggested,
        headline=headline,
        supports=[s for s in supports if s],
        contras=[c for c in contras if c],
        cancel_trigger=cancel,
    )


def apply_regime_gate(recs: list[TickerRecommendation],
                      buy_gate: str) -> list[TickerRecommendation]:
    """Temper buy signals based on market regime.

    On risk-off ("temper"), downgrade aggressive entries — you shouldn't be
    initiating fresh 建仓 into a -4% VIX-spiking day. Existing holds unaffected.
    """
    if buy_gate == "pass":
        return recs
    for r in recs:
        if buy_gate == "temper":
            # Downgrade fresh entries to "等企稳" wording, halve suggested size
            if r.action == "建仓":
                r.action = "等企稳再建仓"
                r.headline = "[体制 risk-off] " + r.headline
                if r.suggested_dollars:
                    r.suggested_dollars = round(r.suggested_dollars * 0.5)
                r.cancel_trigger = ("大盘 risk-off — 等大盘企稳 (SPY 收回 SMA50 / "
                                    "VIX 回落) + 本股出现阳线再进")
            elif r.action == "加仓持有":
                r.action = "持有不加"
                r.cancel_trigger = "大盘 risk-off — 持有可以, 但暂不追加"
            elif r.action == "试探建仓":
                r.action = "观望"
            elif r.action == "短期反弹候选":
                # Mean-reversion bounces into a systemic down day are knife-
                # catching — the gate must close this path too (it was the
                # last $-carrying buy signal that bypassed the regime gate).
                r.action = "观望"
                r.suggested_dollars = 0.0
                r.cancel_trigger = ("大盘 risk-off — 反弹交易暂停, "
                                    "等体制转好再看反弹候选")
        elif buy_gate == "caution":
            # Just annotate, don't downgrade
            if r.action in ("建仓", "加仓持有"):
                r.cancel_trigger = "市场偏谨慎 — 分批入场, 不要一次梭哈. " + (r.cancel_trigger or "")
    return recs


def rank_recommendations(
    summaries: dict[str, dict],
    data: dict[str, pd.DataFrame],
    smh_close: pd.Series,
    quality_factors: dict[str, QualityFactor] | None = None,
    pead_signals: dict[str, PEADSignal] | None = None,
    news_summaries: dict | None = None,
    profile: str = DEFAULT_PROFILE,
    moonshot_set: set | None = None,
    valuations: dict | None = None,
) -> list[TickerRecommendation]:
    """Build recommendations for all tickers, sorted by conviction × quality."""
    quality_factors = quality_factors or {}
    pead_signals = pead_signals or {}
    news_summaries = news_summaries or {}
    moonshot_set = moonshot_set or set()
    valuations = valuations or {}
    recs = []
    for t, s in summaries.items():
        if t not in data or data[t].empty:
            continue
        val = valuations.get(t)
        rec = evaluate_ticker(
            t, s, data[t]["close"], smh_close,
            quality_factor=quality_factors.get(t),
            pead=pead_signals.get(t),
            news_summary=news_summaries.get(t),
            profile=profile,
            is_moonshot=(t in moonshot_set),
            valuation_tilt=(val.tilt if val else 0.0),
        )
        recs.append(rec)
    # Sort: 建仓/加仓 first, then by quality
    action_priority = {
        "建仓": 0, "加仓持有": 1, "试探建仓": 2, "短期反弹候选": 3,
        "持有博弹性": 4, "持有不加": 5, "观望": 6, "观望偏空": 7,
        "减仓": 8, "避免": 9, "清仓": 10,
    }
    recs.sort(key=lambda r: (action_priority.get(r.action, 10), -r.quality_score))
    return recs


# ---------- 渲染函数 ----------

def render_recommendations_block_markdown(
    recs: list[TickerRecommendation],
    speculation_budget: float = 150.0,
) -> str:
    """Comprehensive markdown for local report."""
    lines = [f"## 仓位建议 (假设 ${speculation_budget:.0f} CAD 投机预算)", "",
             "_每只股票都给出: **动作** + **质量分** + **风险分** + **大白话理由** + **取消条件**_", ""]

    # Group by action
    by_action = {}
    for r in recs:
        by_action.setdefault(r.action, []).append(r)

    action_order = ["建仓", "加仓持有", "试探建仓", "短期反弹候选",
                    "持有博弹性", "持有不加", "观望", "观望偏空",
                    "减仓", "避免", "清仓"]
    action_icons = {
        "建仓": "[强力建仓]", "加仓持有": "[加仓]", "试探建仓": "[试探]",
        "短期反弹候选": "[短期反弹]", "持有博弹性": "[博弹性]",
        "持有不加": "[持有]", "观望": "[观望]", "观望偏空": "[观望偏空]",
        "减仓": "[减仓]", "避免": "[避免]", "清仓": "[清仓]",
    }

    for action in action_order:
        items = by_action.get(action, [])
        if not items:
            continue
        icon = action_icons[action]
        lines.append(f"### {icon} {action} ({len(items)} 只)")
        lines.append("")
        lines.append(translate_action_explanation(action))
        lines.append("")

        for r in items:
            dollar_hint = (f"(建议 ${r.suggested_dollars:.0f} CAD)"
                           if r.suggested_dollars and r.suggested_dollars > 0
                           else "")
            lines.append(f"**{r.ticker}** ({r.category}) "
                         f"— 质量 {r.quality_score:.0f}/100, "
                         f"风险 {r.risk_score:.0f}/100  {dollar_hint}")
            for s in r.supports:
                lines.append(f"  - 支持: {s}")
            for c in r.contras:
                lines.append(f"  - 反对: {c}")
            if r.cancel_trigger:
                lines.append(f"  - **取消条件**: {r.cancel_trigger}")
            lines.append("")
    return "\n".join(lines)


def render_recommendations_for_embed(
    recs: list[TickerRecommendation],
    speculation_budget: float = 150.0,
    action_levels: list | None = None,
    new_in_buy_tickers: set[str] | None = None,
) -> list[dict]:
    """Discord embed fields — Wall Street research-desk style.

    Design principles:
      - Top 3 high-conviction cards with concrete price targets (Goldman CL style)
      - Only NEW additions, not repeated state (DE Shaw / Renaissance discipline)
      - No "hold" tag (user explicit request, also irrelevant if user doesn't own)
      - Compact data, narrative-led
    """
    fields = []
    new_in_buy_tickers = new_in_buy_tickers or set()

    by_action = {}
    for r in recs:
        by_action.setdefault(r.action, []).append(r)

    # Build a ticker -> ActionLevels map for fast lookup
    levels_map = {L.ticker: L for L in (action_levels or [])}

    # ===== Top 3 deep cards (Goldman Conviction List style) =====
    buy_actions = (by_action.get("建仓", []) +
                   by_action.get("加仓持有", []) +
                   by_action.get("试探建仓", []))
    top3 = buy_actions[:3]
    if top3:
        cards = []
        for i, r in enumerate(top3, 1):
            L = levels_map.get(r.ticker)
            if L:
                current = L.current
                # Target: use next_resistance if it's a real 5-25% upside;
                # else fall back to 2×ATR (~1-2 month projection)
                cand = L.next_resistance
                if cand and 0.05 < (cand - current) / current < 0.25:
                    target = cand
                    target_note = "下个阻力"
                else:
                    target = L.target_2r
                    target_note = "2×ATR 目标"
                upside = (target - current) / current * 100
                # Trend stop = SMA50 when price is above it; if price is
                # already below SMA50 (possible for 试探建仓), an "SMA50 stop"
                # would sit ABOVE the entry — fall back to the 2×ATR stop so
                # the stop is always below price and R/R stays meaningful.
                stop = L.stop_sma50 if L.stop_sma50 < current else L.stop_tight
                stop_pct = (stop - current) / current * 100
                # Reward/Risk ratio = potential upside / potential downside
                rr = upside / abs(stop_pct) if stop_pct < 0 else 0
                price_block = (f"${current:.2f} → **${target:.2f}** "
                               f"(**{upside:+.1f}%** {target_note}) · "
                               f"止损 ${stop:.2f} ({stop_pct:+.1f}%) · "
                               f"R/R {rr:.1f}:1")
            else:
                price_block = "—"

            new_marker = "  ★ 新进入" if r.ticker in new_in_buy_tickers else ""
            reasons = (r.supports[:2] if r.supports else [r.headline])
            reasons_txt = " · ".join(s[:60] for s in reasons[:2])
            dollar = (f"${r.suggested_dollars:.0f} CAD"
                      if r.suggested_dollars else "—")

            # Multi-line clean format with visual hierarchy
            if L:
                cards.append(
                    f"**#{i}  `{r.ticker}`**  ·  {r.action}  ·  {dollar}{new_marker}\n"
                    f"  ${L.current:.2f}  →  **${target:.2f}**  "
                    f"(**{upside:+.1f}%** · {target_note})\n"
                    f"  止损 ${stop:.2f} ({stop_pct:+.1f}%)  ·  "
                    f"R/R **{rr:.1f} : 1**  ·  Q{r.quality_score:.0f} / R{r.risk_score:.0f}\n"
                    f"  _{reasons_txt}_"
                )
            else:
                cards.append(
                    f"**#{i}  `{r.ticker}`**  ·  {r.action}  ·  {dollar}{new_marker}\n"
                    f"  Q{r.quality_score:.0f} / R{r.risk_score:.0f}\n"
                    f"  _{reasons_txt}_"
                )
        fields.append({
            "name": "[★] 今日 Top 3 高信号买入  ·  目标价 / 止损 / 风险回报",
            "value": "\n\n".join(cards),
            "inline": False,
        })

    # ===== 短期反弹候选 (visually distinct from main buys — pure swing trade) =====
    reversal = by_action.get("短期反弹候选", [])[:5]
    if reversal:
        rev_lines = []
        for r in reversal:
            dollar = (f"${r.suggested_dollars:.0f}"
                      if r.suggested_dollars else "—")
            reason = r.supports[0] if r.supports else r.headline
            rev_lines.append(
                f"`{r.ticker}` · {dollar} CAD · Q{r.quality_score:.0f}/R{r.risk_score:.0f}\n"
                f"   _{reason[:80]}_"
            )
        fields.append({
            "name": "[反弹] 短期反弹候选  ·  基本面伤但技术反弹  ·  5 日不涨即走",
            "value": "\n\n".join(rev_lines),
            "inline": False,
        })

    # ===== Sells (all of today's; header says so) =====
    sell_actions = by_action.get("减仓", []) + by_action.get("清仓", [])
    if sell_actions:
        compact = " · ".join(
            f"**{r.ticker}**({r.action})"
            for r in sell_actions[:5]
        )
        fields.append({
            "name": "[减仓] 建议卖出 (今日全部)",
            "value": compact,
            "inline": False,
        })

    return fields
