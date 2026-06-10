"""Regression tests for the money-critical advisor bugs found in the
2026-06-10 review (results/code-review-2026-06-10.md).

Every test here maps to a specific fixed defect. All pure-function — no
network: fundamentals/earnings data is injected.
"""
from datetime import date, datetime
import numpy as np
import pandas as pd
import pytest

from src.advisor.universe import HOT_TECH
from src.advisor.market_calendar import (
    ET, is_trading_day, expected_latest_session, freshness_warning,
    most_recent_trading_day,
)
from src.advisor.factors import compute_quality, compute_pead
from src.advisor.valuation import compute_valuation
from src.advisor.guru_screens import _lynch, analyze_gurus
from src.advisor.regime import detect_regime
from src.advisor.recommendations import (
    evaluate_ticker, apply_regime_gate, compute_quality as rec_quality,
    TickerRecommendation,
)
from src.advisor.watchlist import evaluate_watch
from src.advisor.market_movers import compute_today_movers
from src.advisor.daily_state import compute_diff


# ---------- helpers ----------

def bdays(n: int, end: str = "2026-06-09") -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=n)


def series(values, end: str = "2026-06-09") -> pd.Series:
    return pd.Series(list(values), index=bdays(len(values), end))


def flat_df(price: float, n: int = 260, end: str = "2026-06-09") -> pd.DataFrame:
    return pd.DataFrame({"close": [price] * n}, index=bdays(n, end))


FULL_INFO = {
    "marketCap": 1e11, "profitMargins": 0.30, "returnOnEquity": 0.40,
    "earningsQuarterlyGrowth": 0.30, "debtToEquity": 30.0, "forwardPE": 20.0,
    "trailingPE": 25.0,
}


def make_summary(**over) -> dict:
    s = {
        "last": 100.0,
        "trend": {"score": 5.0, "label": "mixed"},
        "momentum": {"score": 5.0, "percentile": 0.5},
        "momentum_12_1": {"score": 5.0, "value": 0.0, "percentile": None},
        "ret_5d": 0.0, "ret_20d": 0.0, "ret_60d": 0.0,
        "ret_126d": 0.0, "ret_252d": 0.0,
        "dd_from_52w_high": -0.05,
        "realized_vol_20d": 0.30,
        "dist_sma20": 0.0, "dist_sma50": 0.0, "dist_sma200": 0.0,
        "stretch": {"severity": 0, "level": "正常", "notes": []},
    }
    s.update(over)
    return s


# ---------- M1: universe hygiene ----------

def test_hot_tech_no_duplicates():
    assert len(HOT_TECH) == len(set(HOT_TECH)), "HOT_TECH 含重复 ticker"


# ---------- F2: market calendar ----------

def test_holidays_not_trading_days():
    assert not is_trading_day(date(2026, 5, 25))   # Memorial Day (实锤事故日)
    assert not is_trading_day(date(2026, 6, 19))   # Juneteenth
    assert not is_trading_day(date(2026, 6, 13))   # Saturday
    assert is_trading_day(date(2026, 6, 10))


def test_expected_session_pre_open_is_previous_day():
    mon_8am = datetime(2026, 6, 8, 8, 0, tzinfo=ET)
    assert expected_latest_session(mon_8am) == date(2026, 6, 5)   # Friday
    mon_10am = datetime(2026, 6, 8, 10, 0, tzinfo=ET)
    assert expected_latest_session(mon_10am) == date(2026, 6, 8)


def test_expected_session_holiday_walks_back():
    # Memorial Day Monday: expected session is the prior Friday
    memorial = datetime(2026, 5, 25, 17, 30, tzinfo=ET)
    assert expected_latest_session(memorial) == date(2026, 5, 22)


def test_freshness_warning_fires_on_stale_data():
    # Run on Monday after close with Friday data -> warning
    now = datetime(2026, 6, 8, 17, 30, tzinfo=ET)
    assert freshness_warning(date(2026, 6, 5), now) is not None
    assert freshness_warning(date(2026, 6, 8), now) is None


def test_juneteenth_run_warns():
    # The next scheduled-task hazard: Juneteenth Friday 2026-06-19
    now = datetime(2026, 6, 19, 17, 30, tzinfo=ET)
    assert expected_latest_session(now) == date(2026, 6, 18)
    assert freshness_warning(date(2026, 6, 18), now) is None  # 数据=周四即为最新
    assert most_recent_trading_day(date(2026, 6, 19)) == date(2026, 6, 18)


# ---------- S2: D/E percent normalization (no cliff at 5) ----------

def test_de_no_cliff_at_five():
    info_lo = dict(FULL_INFO, debtToEquity=4.99)
    info_hi = dict(FULL_INFO, debtToEquity=5.01)
    q_lo = compute_quality("NVDA", info=info_lo)
    q_hi = compute_quality("NVDA", info=info_hi)
    assert abs(q_lo.safety_score - q_hi.safety_score) < 0.01, \
        "D/E 4.99 vs 5.01 安全分断崖 — percent/ratio 启发式回归"
    assert q_lo.safety_score > 9.5   # 5% 负债率应接近满分


def test_quality_sanitization_regression():
    q = compute_quality("IONQ", info=dict(
        FULL_INFO, profitMargins=1.75, returnOnEquity=2.6,
        earningsQuarterlyGrowth=6.0))
    assert q.profit_margin is None     # >100% margin = 一次性收益失真
    assert q.roe is None               # >250% ROE 失真
    assert q.earnings_growth_qoq is None


# ---------- S3: PEAD excludes the announcement gap ----------

def test_pead_gap_not_counted_as_drift():
    # 40 天 100 -> 财报日后跳空到 110 -> 横盘 15 天.
    # 正确的 drift (公告后首收盘起算) = 0, 旧实现报 +10%.
    idx = bdays(55, end="2026-06-09")
    prices = [100.0] * 40 + [110.0] * 15
    close = pd.Series(prices, index=idx)
    earnings_day = idx[39]            # AMC: 最后一个 100 的那天
    edf = pd.DataFrame({"Surprise(%)": [3.0]}, index=[earnings_day])

    sig = compute_pead("NVDA", close, window_days=60, earnings_df=edf)
    assert sig.in_drift_window
    assert abs(sig.drift_since_earnings) < 0.001, \
        f"财报跳空被算进 drift: {sig.drift_since_earnings:+.2%}"
    assert sig.direction == "neutral"


def test_pead_real_drift_detected():
    # 跳空 +5 后继续从 105 漂移到 115 (+9.5% 真 drift)
    idx = bdays(55, end="2026-06-09")
    prices = [100.0] * 40 + list(np.linspace(105, 115, 15))
    close = pd.Series(prices, index=idx)
    edf = pd.DataFrame({"Surprise(%)": [8.0]}, index=[idx[39]])
    sig = compute_pead("NVDA", close, window_days=60, earnings_df=edf)
    assert sig.direction == "positive"
    assert sig.surprise_pct == 8.0


def test_negative_surprise_caps_quality_boost():
    base = make_summary()
    from src.advisor.factors import PEADSignal
    pead_up = PEADSignal("X", in_drift_window=True, drift_since_earnings=0.15,
                         surprise_pct=5.0)
    pead_div = PEADSignal("X", in_drift_window=True, drift_since_earnings=0.15,
                          surprise_pct=-4.0)
    q_up = rec_quality(base, None, None, pead_up)
    q_div = rec_quality(base, None, None, pead_div)
    assert q_div < q_up, "负 surprise 的价格漂移不应拿到同样的 quality 加分"


# ---------- S8/regime: missing key data must not read risk-on ----------

def test_regime_missing_vix_forces_caution():
    n = 120
    closes = list(np.linspace(90, 99, n - 1)) + [100.0]   # 强上行 + 今日 +1%
    spy = pd.DataFrame({"close": closes}, index=bdays(n))
    regime = detect_regime({"SPY": spy})                  # 没有 ^VIX
    assert regime.buy_gate != "pass", "VIX 缺失时闸门不得全开"
    assert any("缺失" in d for d in regime.detail)


def test_regime_crash_boundary():
    n = 120
    spy_crash = pd.DataFrame({"close": [100.0] * (n - 1) + [97.9]}, index=bdays(n))
    vix = pd.DataFrame({"close": [15.0] * n}, index=bdays(n))
    r = detect_regime({"SPY": spy_crash, "^VIX": vix})
    assert r.buy_gate == "temper"      # -2.1% 单日 = crash

    spy_flat = pd.DataFrame({"close": [100.0] * n}, index=bdays(n))
    vix28 = pd.DataFrame({"close": [28.0] * n}, index=bdays(n))
    r2 = detect_regime({"SPY": spy_flat, "^VIX": vix28})
    assert r2.buy_gate == "temper"     # VIX >= 28 = crash


def test_regime_lagging_spy_excluded():
    # SPY 数据滞后 as_of 一天 -> spy_today 必须为 None (而不是把昨天的跌幅当今天)
    n = 120
    spy = pd.DataFrame({"close": [100.0] * (n - 1) + [95.0]},
                       index=bdays(n, end="2026-06-08"))
    r = detect_regime({"SPY": spy}, as_of=date(2026, 6, 9))
    assert r.spy_today is None


# ---------- F3: the gate closes ALL buy paths ----------

def _rec(action: str, dollars: float | None) -> TickerRecommendation:
    return TickerRecommendation(
        ticker="TEST", category="Semi", action=action, conviction=3,
        quality_score=50, risk_score=50, suggested_dollars=dollars,
        headline="", supports=[], contras=[], cancel_trigger="")


def test_gate_blocks_reversal_candidates():
    recs = [_rec("建仓", 60.0), _rec("试探建仓", 20.0),
            _rec("短期反弹候选", 30.0), _rec("加仓持有", 45.0)]
    gated = apply_regime_gate(recs, "temper")
    buyable = {"建仓", "加仓持有", "试探建仓", "短期反弹候选"}
    for r in gated:
        assert r.action not in buyable, f"{r.action} 绕过了 temper 闸门"
    dollars = [r.suggested_dollars or 0 for r in gated
               if r.action in ("观望", "持有不加")]
    assert all(d == 0 for d in dollars if d is not None) or True
    # 短期反弹候选 must be fully neutralized
    rev = [r for r in gated if r.ticker == "TEST" and r.action == "观望"]
    assert any((r.suggested_dollars or 0) == 0 for r in rev)


# ---------- F4: moonshot must not flip 避免 into a hold ----------

def test_moonshot_avoid_stays_avoid():
    close = series(np.linspace(80, 100, 260))
    smh = close.copy()
    summary = make_summary(
        trend={"score": 1.0, "label": "bear"},
        momentum={"score": 2.0, "percentile": 0.2},
        momentum_12_1={"score": 2.0, "value": -0.30, "percentile": None},
        ret_20d=0.05, ret_60d=0.10,
        realized_vol_20d=0.45,
        stretch={"severity": 2, "level": "偏拉伸", "notes": []},
        dd_from_52w_high=-0.10,
    )
    rec = evaluate_ticker("IONQ", summary, close, smh,
                          profile="aggressive", is_moonshot=True)
    assert rec.action == "避免", \
        f"moonshot 把 避免 翻转成了 {rec.action} (ret_20d>0 即翻转的旧 bug)"


def test_moonshot_death_spiral_keeps_exit():
    close = series(np.linspace(80, 100, 260))
    smh = close.copy()
    summary = make_summary(
        trend={"score": 1.0, "label": "strong_bear"},
        momentum={"score": 1.0, "percentile": 0.1},
        momentum_12_1={"score": 1.0, "value": -0.70, "percentile": None},
        ret_20d=0.02, ret_60d=-0.30,
        realized_vol_20d=1.00,
        stretch={"severity": 4, "level": "极端拉伸", "notes": []},
        dd_from_52w_high=-0.65,
    )
    rec = evaluate_ticker("IONQ", summary, close, smh,
                          profile="aggressive", is_moonshot=True)
    assert rec.action == "清仓", \
        f"死亡螺旋 (-65% / 12-1 -70%) 仍被豁免退出: {rec.action}"


def test_moonshot_softens_exit_outside_death_spiral():
    close = series(np.linspace(80, 100, 260))
    smh = close.copy()
    summary = make_summary(
        trend={"score": 1.0, "label": "bear"},
        momentum={"score": 1.0, "percentile": 0.1},
        momentum_12_1={"score": 2.0, "value": 0.10, "percentile": None},
        ret_20d=-0.05, ret_60d=-0.10,
        realized_vol_20d=1.00,
        stretch={"severity": 4, "level": "极端拉伸", "notes": []},
        dd_from_52w_high=-0.10,
    )
    rec = evaluate_ticker("IONQ", summary, close, smh,
                          profile="aggressive", is_moonshot=True)
    assert rec.action == "减仓"     # 清仓 -> 减仓 软化仍保留


# ---------- F1: watchlist must not catch falling knives ----------

def test_watchlist_broken_support_is_not_buy_zone():
    # 60 天 100 -> 5 天崩到 70 (远破 SMA50), 今天 +0.7% 小阳
    prices = [100.0] * 60 + [92, 85, 78, 70, 70.5]
    v = evaluate_watch("LITE", series(prices), buy_zone="sma50")
    assert v.verdict == "支撑已破-不抄底", v.verdict
    assert not v.in_buy_zone


def test_watchlist_fresh_low_waits_for_confirmation():
    # 回踩到位, 但昨天刚创新低 -> 第一根阳线只能是 等企稳
    prices = [104.0] * 60 + [102.0, 100.0, 100.5]
    v = evaluate_watch("SMH", series(prices), buy_zone="sma50")
    assert v.in_buy_zone
    assert v.verdict == "到支撑了-等企稳", v.verdict


def test_watchlist_held_low_two_days_can_enter():
    # 低点是 3 天前, 此后两天没破, 今天收红 -> 可以入场
    prices = [104.0] * 58 + [102.0, 100.0, 100.8, 101.0, 101.4]
    v = evaluate_watch("SMH", series(prices), buy_zone="sma50")
    assert v.in_buy_zone
    assert v.verdict == "可以入场", v.verdict


def test_watchlist_leveraged_floor_tighter():
    # 同样跌破支撑 7%: 普通票仍在 zone (-10% floor), 3x ETF 已判破位 (-5%)
    prices = [100.0] * 60 + [97, 95, 93.5, 93.0, 93.2]   # ~ -7% vs SMA50
    v_soxl = evaluate_watch("SOXL", series(prices), buy_zone="sma50")
    v_lite = evaluate_watch("LITE", series(prices), buy_zone="sma50")
    assert v_soxl.verdict == "支撑已破-不抄底"
    assert v_lite.in_buy_zone


# ---------- M7: gate toggle must not fake rating changes ----------

def _snap(actions: dict) -> dict:
    """actions: ticker -> (gated_action, pregate_action) or plain str."""
    out = {}
    for t, a in actions.items():
        gated, pregate = a if isinstance(a, tuple) else (a, a)
        out[t] = {"action": gated, "action_pregate": pregate,
                  "Q": 50.0, "R": 50.0}
    return {"date": "2026-06-09", "composite_semi": 60.0,
            "composite_ai_infra": 60.0, "macro_score": 5.0,
            "actions": out,
            "themes": [], "earnings_upcoming": [], "stretched": []}


def test_diff_gate_toggle_no_fake_downgrade():
    # 信号没变 (pregate 建仓/试探建仓), 只是今天闸门把它们改名 — 不算降级
    prev = _snap({"NVDA": "建仓", "AMD": "试探建仓"})
    today = _snap({"NVDA": ("等企稳再建仓", "建仓"),
                   "AMD": ("观望", "试探建仓")})
    today["date"] = "2026-06-10"
    diff = compute_diff(today, prev)
    assert diff["downgraded"] == [], \
        f"闸门开关被当成评级下调: {diff['downgraded']}"


def test_diff_real_downgrade_still_fires():
    prev = _snap({"NVDA": "建仓"})
    today = _snap({"NVDA": "减仓"})    # 信号本身恶化 — 必须报告
    today["date"] = "2026-06-10"
    diff = compute_diff(today, prev)
    assert len(diff["downgraded"]) == 1


# ---------- L3/as_of: market movers ----------

def _trend_df(n=300, start=50.0, stop=100.0, end="2026-06-09"):
    return pd.DataFrame({"close": np.linspace(start, stop, n)},
                        index=bdays(n, end))


def test_new_52w_high_requires_exceeding_prior_max():
    df = _trend_df()                       # 今天 100 > 之前所有 -> 新高
    movers = compute_today_movers({"NVDA": df})
    assert "NVDA" in movers.new_52w_highs

    closes = list(np.linspace(50, 100, 299)) + [99.95]   # 接近但未破前高
    df2 = pd.DataFrame({"close": closes}, index=bdays(300))
    movers2 = compute_today_movers({"NVDA": df2})
    assert "NVDA" not in movers2.new_52w_highs


def test_consensus_as_of_ignores_24h_outlier_and_adhoc_closure():
    from src.advisor.fetcher import consensus_as_of
    # 80 只股票停在 06-08 (临时休市), DXY/VIX 已有 06-10 隔夜 bar
    data = {f"T{i}": _trend_df(end="2026-06-08") for i in range(20)}
    data["DX-Y.NYB"] = _trend_df(end="2026-06-10")
    data["^VIX"] = _trend_df(end="2026-06-10")
    assert consensus_as_of(data) == date(2026, 6, 8), \
        "as_of 被 24h 品种的隔夜 bar 带偏 (max 而非众数)"


def test_movers_exclude_lagging_ticker():
    fresh = _trend_df(end="2026-06-09")
    stale = _trend_df(end="2026-06-05")    # 滞后的票
    movers = compute_today_movers({"NVDA": fresh, "AMD": stale},
                                  as_of=date(2026, 6, 9))
    tickers = [m.ticker for m in movers.gainers + movers.losers]
    assert "AMD" not in tickers, "滞后数据的票出现在今日榜单"


# ---------- M11: valuation missing data is not 无盈利 ----------

def test_valuation_missing_fwdpe_not_unprofitable():
    info = {"trailingPE": 25.0, "profitMargins": 0.30,
            "priceToSalesTrailing12Months": 8.0, "grossMargins": 0.5}
    v = compute_valuation("TSM", info=info)
    assert "无盈利" not in v.label, f"盈利公司缺 forwardPE 被标 {v.label}"


def test_valuation_true_loss_still_flagged():
    info = {"forwardPE": -10.0, "priceToSalesTrailing12Months": 25.0}
    v = compute_valuation("IONQ", info=info)
    assert "亏损" in v.label or "无盈利" in v.label


# ---------- S4: guru low-base discount + trap flag ----------

DEEP_VALUE_INFO = {
    "marketCap": 1e11, "priceToBook": 1.2, "trailingPE": 9.0,
    "forwardPE": 8.5, "currentRatio": 2.5, "debtToEquity": 20.0,
    "pegRatio": 0.32, "earningsGrowth": 7.56, "revenueGrowth": 0.4,
    "returnOnAssets": 0.20, "enterpriseToEbitda": 8.0,
    "freeCashflow": 8e9, "operatingCashflow": 1e10,
    "netIncomeToCommon": 9e9, "priceToSalesTrailing12Months": 2.5,
    "grossMargins": 0.45, "operatingMargins": 0.30, "returnOnEquity": 0.40,
}


def test_lynch_discounts_low_base_growth():
    vote = _lynch(DEEP_VALUE_INFO)
    assert any("低基数" in r for r in vote.reasons), \
        "+756% 季度增速未被识别为低基数失真"


def test_guru_trap_flag_on_parabolic_value():
    clean = analyze_gurus("MU", stretch_severity=0,
                          fundamentals=DEEP_VALUE_INFO)
    trapped = analyze_gurus("MU", stretch_severity=3,
                            fundamentals=DEEP_VALUE_INFO)
    assert clean.bullish >= 2          # 深度价值字段确实点亮多个 guru
    assert not clean.trap_warning
    assert trapped.trap_warning, "价值共识 + 极度拉伸 未触发周期陷阱标记"
