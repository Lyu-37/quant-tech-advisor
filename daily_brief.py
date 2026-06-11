"""Master orchestrator for the daily brief.

Runs in order:
  1. Fetch market data for universe
  2. Semi-sector analysis (AMD focus)
  3. AI infra analysis (GEV focus)
  4. News + sentiment for focus tickers
  5. Render full markdown report
  6. Build compact summary for Discord
  7. Push to Discord (if DISCORD_WEBHOOK_URL set)

Usage:
    python daily_brief.py [--config configs/portfolio.yaml] [--no-discord]
"""
from datetime import date
from pathlib import Path
import argparse
import os
import yaml

from src.advisor.universe import (
    Holding, SEMI_LEADERS, AI_INFRA_LEADERS, HOT_TECH, all_symbols_for_run,
)
from src.advisor.fetcher import fetch_universe, consensus_as_of, lagging_tickers
from src.advisor.market_calendar import (
    is_trading_day, freshness_warning, now_et,
)
from src.advisor.indicators import summarize_ticker, relative_strength
from src.advisor.scoring import composite_sector_score
from src.advisor.ai_infra import analyze_ai_infra, render_ai_infra_section
from src.advisor.news import (
    fetch_news_batch, render_news_section,
    extract_themes, render_themes_block,
)
from src.advisor.events import earnings_for_universe, render_earnings_block
from src.advisor.levels import compute_levels, render_levels_block
from src.advisor.portfolio_metrics import (
    compute_portfolio_metrics, render_portfolio_metrics,
)
from src.advisor.recommendations import (
    rank_recommendations, render_recommendations_block_markdown,
)
from src.advisor.factors import compute_quality, compute_pead
from src.advisor.daily_state import (
    save_snapshot, find_latest_previous, compute_diff, snapshot_exists,
)
from src.advisor.portfolio_daily import compute_daily_pnl
from src.advisor.market_movers import compute_today_movers
from src.advisor.watchlist import evaluate_watchlist
from src.advisor.regime import detect_regime, find_relative_strength_in_selloff
from src.advisor.factors import compute_analyst, render_analyst_field
from src.advisor.valuation import compute_valuation, render_valuation_field
from src.advisor.discord_push import send_embed
from src.advisor.scanner import build_scanner_embed
from src.advisor.report import render_full_report, save_report


PROJECT_ROOT = Path(__file__).resolve().parent

# Tickers to pull news for. Spans the full hot-tech universe so the Discord
# scanner reflects market-wide narrative, not just one slice.
NEWS_FOCUS = [
    # Semis
    "NVDA", "AMD", "AVGO", "TSM", "ARM", "MU",
    # Mega-cap tech
    "AAPL", "MSFT", "GOOG", "META", "AMZN", "TSLA",
    # High-beta AI plays
    "PLTR", "SMCI",
    # AI infra
    "GEV", "VRT", "ETN", "PWR",
    # Frontier: nuclear, optical, quantum, robotics
    "CCJ", "CEG", "VST", "OKLO", "SMR",
    "LITE", "COHR", "FN", "CIEN",
    "IONQ", "RGTI",
    "ISRG", "TER",
    # Levered ETFs (sentiment check)
    "SOXL", "TQQQ",
]


def load_portfolio(path: Path) -> tuple[list[Holding], dict]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    holdings = [
        Holding(**{k: v for k, v in h.items() if k in
                   {"ticker", "shares", "cost_basis", "sector", "note"}})
        for h in cfg["holdings"]
    ]
    return holdings, cfg


# Note: removed `build_compact_summary` (was holdings-aware, file-attached).
# Replaced by `build_scanner_embed` in src/advisor/scanner.py (market-wide,
# no personal holdings, native Discord embed).


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/portfolio.yaml")
    parser.add_argument("--no-discord", action="store_true",
                        help="skip Discord push even if webhook URL is set")
    parser.add_argument("--no-news", action="store_true",
                        help="skip news fetch (faster local runs)")
    parser.add_argument("--no-llm", action="store_true",
                        help="skip Ollama theme extraction (saves ~30-60s)")
    parser.add_argument("--force", action="store_true",
                        help="run even on a non-trading day (data will be "
                             "labeled with its true as-of date)")
    args = parser.parse_args()

    # Market-closed guard: the scheduled task fires Mon-Fri and used to run on
    # holidays too (snapshot-2026-05-25 = Memorial Day exists as evidence),
    # pushing Friday's data labeled as "today". Skip unless forced.
    if not is_trading_day(date.today()) and not args.force:
        print(f"{date.today()} 休市 (周末/假期) — 跳过简报. 用 --force 强制运行.")
        return

    cfg_path = PROJECT_ROOT / args.config
    holdings, cfg = load_portfolio(cfg_path)
    print(f"[1/7] Loaded {len(holdings)} holdings from {cfg_path.name}")

    symbols = all_symbols_for_run(holdings)
    # Add watchlist tickers (may be outside HOT_TECH universe)
    watch_cfg = cfg.get("watchlist", []) or []
    for item in watch_cfg:
        t = item.get("ticker")
        if t and t not in symbols:
            symbols.append(t)
    print(f"[2/7] Fetching {len(symbols)} symbols...")
    # Always fresh: briefs run once/twice a day, stale data is the worst failure
    data = fetch_universe(symbols, lookback_days=800, use_cache=False)
    n_failed = len(symbols) - len(data)
    fetch_note = f"{len(data)}/{len(symbols)} 数据源成功"
    print(f"      -> got {len(data)} successfully ({n_failed} failed)")

    # ------- data freshness: the single most important check -------
    # Consensus (modal) date, clamped by the calendar: robust both to 24h
    # instruments carrying tomorrow's bar overnight AND to ad-hoc closures
    # (mourning days) no hardcoded holiday table can know about.
    from src.advisor.market_calendar import expected_latest_session
    as_of_consensus = consensus_as_of(data)
    if as_of_consensus is None:
        raise RuntimeError("No data returned for ANY symbol — aborting brief")
    as_of = min(as_of_consensus, expected_latest_session())
    fresh_warn = freshness_warning(as_of)
    laggards = lagging_tickers(data, as_of)
    if fresh_warn:
        print(f"      [!] {fresh_warn}")
    if laggards:
        print(f"      [!] {len(laggards)} tickers lag the as-of date "
              f"(excluded from today-math): {', '.join(laggards[:8])}")

    # Market values from LIVE data (close × shares × FX) — replaces the old
    # hardcoded "from screenshot" dict that froze portfolio values in May.
    from src.advisor.portfolio_daily import _fetch_usd_cad
    from src.advisor.universe import is_cad_listed
    usd_cad = _fetch_usd_cad()
    for h in holdings:
        df = data.get(h.ticker)
        if df is None or df.empty:
            h.set_market_value(0.0)
            continue
        px = float(df["close"].iloc[-1])
        fx = 1.0 if is_cad_listed(h.ticker) else usd_cad
        h.set_market_value(px * h.shares * fx)

    # ------- buy suppression: fail-closed data quality + circuit breaker ----
    from src.advisor.ledger import evaluate_speculation_sleeve
    from src.advisor import safeguards
    sleeve = evaluate_speculation_sleeve(data, usd_cad, as_of)
    if sleeve is not None:
        print(f"      投机桶: 权益 ${sleeve.equity:.0f} "
              f"(已实现 {sleeve.realized_pnl:+.0f} / 浮动 {sleeve.unrealized_pnl:+.0f}), "
              f"距高水位 {sleeve.drawdown_pct * 100:.0f}%"
              + (f" — 熔断至 {sleeve.breaker_until}" if sleeve.breaker_active else ""))
    buy_suppression = safeguards.combine(
        safeguards.data_quality_suppression(fresh_warn, n_failed, len(symbols)),
        safeguards.breaker_suppression(sleeve),
    )
    if buy_suppression:
        print(f"      [停] 买入建议抑制: {buy_suppression}")

    # ------- semi sector composite -------
    print("[3/7] Computing semi sector score...")
    smh_df = data["SMH"]
    spy_df = data["SPY"]
    vix_df = data.get("^VIX")
    tnx_df = data.get("^TNX")
    dxy_df = data.get("DX-Y.NYB")

    semi_score = composite_sector_score(
        sector_close=smh_df["close"],
        benchmark_close=spy_df["close"],
        vix_close=vix_df["close"] if vix_df is not None else None,
        tnx_df=tnx_df, dxy_df=dxy_df,
    )

    smh_summary = summarize_ticker(smh_df["close"])
    soxx_summary = summarize_ticker(data["SOXX"]["close"])
    leader_summaries = [(t, summarize_ticker(data[t]["close"]))
                        for t in SEMI_LEADERS if t in data]
    rs_vs_smh = {t: relative_strength(data[t]["close"], smh_df["close"], window=60)
                 for t in SEMI_LEADERS if t in data}
    amd_summary = summarize_ticker(data["AMD"]["close"])
    amd_rs_smh = relative_strength(data["AMD"]["close"], smh_df["close"], window=60)
    amd_rs_nvda = relative_strength(data["AMD"]["close"], data["NVDA"]["close"], window=60)

    total_value = sum(h.market_value for h in holdings)
    total_cost = sum(h.cost_basis for h in holdings)
    weight_amd = (next((h.market_value for h in holdings if h.ticker == "AMD"), 0)
                  / total_value if total_value else 0.0)
    total_pnl_pct = (total_value - total_cost) / total_cost if total_cost else 0.0

    # ------- AI infra theme -------
    print("[4/7] Analyzing AI infrastructure (GEV focus)...")
    ai_infra_report = analyze_ai_infra(data, smh_df["close"], holdings)

    # ------- news + sentiment -------
    if args.no_news:
        print("[5a/9] Skipping news fetch (--no-news)")
        news_summaries = {}
        themes = []
    else:
        print(f"[5a/9] Fetching news for {len(NEWS_FOCUS)} focus tickers...")
        news_summaries = fetch_news_batch(NEWS_FOCUS)
        total_headlines = sum(len(s.headlines) for s in news_summaries.values())
        print(f"      -> {total_headlines} headlines classified")

        if args.no_llm:
            print("[5b/9] Skipping theme extraction (--no-llm)")
            themes = []
        else:
            print("[5b/9] Extracting cross-ticker themes (keyword rules)...")
            themes = extract_themes(news_summaries)
            print(f"      -> {len(themes)} themes detected")

    # ------- earnings calendar -------
    print("[6/9] Earnings calendar lookup...")
    earnings_events = earnings_for_universe(
        list(set(NEWS_FOCUS + [h.ticker for h in holdings])),
        within_days=21,
    )
    print(f"      -> {len(earnings_events)} upcoming earnings")

    # ------- action levels: compute for ALL hot tech (cheap, ~ms each) -------
    # Previously a hardcoded LEVELS_FOCUS list missed Mag 7 / ETFs which then
    # showed empty target prices in Top 3 cards. Now compute for everything.
    print("[7/9] Computing action levels for full universe...")
    from src.advisor.universe import HOT_TECH as _HOT
    action_levels = []
    for t in _HOT:
        if t in data and not data[t].empty:
            L = compute_levels(data[t]["close"], ticker=t)
            if L:
                action_levels.append(L)

    # ------- portfolio daily P&L (for Discord top of embed) -------
    print("[8/9a] Computing portfolio daily P&L...")
    portfolio_pnl = compute_daily_pnl(holdings, data)
    print(f"      -> {len(portfolio_pnl)} holdings with valid data")

    # ------- pullback watchlist (your buy-the-dip monitor) -------
    watch_verdicts = evaluate_watchlist(watch_cfg, data, as_of=as_of)
    if watch_verdicts:
        actionable = [v for v in watch_verdicts if v.verdict == "可以入场"]
        print(f"[8/9a2] Watchlist: {len(watch_verdicts)} tickers, "
              f"{len(actionable)} 可以入场")

    # ------- market regime (risk-on/off gate) -------
    print("[8/9a3] Detecting market regime...")
    regime = detect_regime(data, as_of=as_of)
    rs_leaders = find_relative_strength_in_selloff(data, as_of=as_of)
    print(f"      -> {regime.label} (score {regime.score:.0f}, gate={regime.buy_gate})")

    # ------- today's market movers + level breaks -------
    print("[8/9b] Scanning today's gainers/losers + level breaks...")
    market_movers = compute_today_movers(data, as_of=as_of)
    top_g = (f"{market_movers.gainers[0].ticker} "
             f"({market_movers.gainers[0].today_pct * 100:+.2f}%)"
             if market_movers.gainers else "n/a")
    print(f"      -> top mover {top_g}; "
          f"{len(market_movers.level_breaks)} level breaks; "
          f"{len(market_movers.new_52w_highs)} new 52w highs")

    # ------- IPS target-allocation drift (local only) -------
    from src.advisor.ips_monitor import evaluate_ips_drift, render_ips_drift
    ips_rows = evaluate_ips_drift(holdings, cfg.get("ips_targets"))
    n_out = sum(1 for r in ips_rows if r.out_of_band)
    if n_out:
        print(f"      [IPS] {n_out} 个资产组偏离目标超带宽 — 看本地报告")

    # ------- holdings exit-condition watch (local only) -------
    from src.advisor.holdings_watch import evaluate_holdings, render_holdings_watch
    holding_alerts = evaluate_holdings(holdings, data)
    n_fired = sum(1 for a in holding_alerts if a.triggered)
    if n_fired:
        fired_names = ", ".join(a.ticker for a in holding_alerts if a.triggered)
        print(f"      [!] 持仓退出条件触发: {fired_names} — 看本地报告 持仓监察 节")

    # ------- portfolio metrics (local only) -------
    print("[8/9] Computing portfolio Greeks...")
    pmetrics = compute_portfolio_metrics(holdings, data)

    # ------- per-ticker recommendations -------
    print("[8b/9] Generating per-ticker action recommendations...")
    from src.advisor.universe import HOT_TECH
    hot_summaries = {
        t: summarize_ticker(data[t]["close"])
        for t in HOT_TECH if t in data and not data[t].empty
    }

    # Pull QMJ quality factors + PEAD signals (skip ETFs internally).
    # These add ~30s due to per-ticker yfinance.info fetches.
    print("      Computing QMJ quality factors + PEAD signals...")
    quality_factors = {}
    pead_signals = {}
    for t in hot_summaries:
        q = compute_quality(t)
        if q is not None:
            quality_factors[t] = q
        p = compute_pead(t, data[t]["close"])
        if p is not None and p.in_drift_window:
            pead_signals[t] = p
    print(f"      -> {len(quality_factors)} quality factors, "
          f"{len(pead_signals)} active PEAD signals")

    # ------- valuation (PEG-based) — computed BEFORE recs so it can tilt quality -------
    print("[8b2/9] Computing valuation (PEG-based) + analyst consensus...")
    analyst_focus = ["NVDA", "AMD", "AVGO", "TSM", "MU", "IONQ", "GOOG",
                     "MSFT", "AAPL", "AMZN", "META", "ASML", "ARM",
                     "PLTR", "SMCI", "INTC", "LRCX", "AMAT"]
    from src.advisor.valuation import apply_context
    analyst_data = {}
    valuation_data = {}
    for t in analyst_focus:
        if t in data and not data[t].empty:
            cur = float(data[t]["close"].iloc[-1])
            a = compute_analyst(t, cur)
            if a is not None:
                analyst_data[t] = a
            v = compute_valuation(t)
            if v is not None and v.label:
                # Context-adjust: cheap-tilt is a TRAP if parabolic or above target
                sev = hot_summaries.get(t, {}).get("stretch", {}).get("severity", 0)
                above_target = (a is not None and a.upside_pct is not None
                                and a.upside_pct < -0.03)
                valuation_data[t] = apply_context(v, sev, above_target)
    n_traps = sum(1 for v in valuation_data.values() if v.trap_warning)
    print(f"      -> {len(analyst_data)} analyst, {len(valuation_data)} valuations "
          f"({n_traps} 价值陷阱)")

    from src.advisor.universe import MOONSHOT_LEADERS
    from src.advisor import config as adv_config
    risk_appetite = cfg.get("risk_appetite", "aggressive")
    # Per-trade risk budget = live portfolio value x risk%, drives
    # stop-distance-based sizing (falls back to fixed table if unavailable).
    risk_pct = float(adv_config.get("sizing.risk_pct_per_trade", 0.01))
    # Equity base: live mark of holdings; falls back to the user-stated
    # account_equity_cad while share counts are unfilled (shares: 0).
    equity_base = total_value if total_value > 0 else float(
        cfg.get("account_equity_cad", 0) or 0)
    risk_dollars = equity_base * risk_pct if equity_base > 0 else None
    unfilled = [h.ticker for h in holdings if h.shares <= 0]
    if unfilled:
        print(f"      [!] portfolio.yaml 股数未填: {', '.join(unfilled)} — "
              "盈亏/组合权重不可用, 风险预算用 account_equity_cad 估计")
    print(f"      risk appetite: {risk_appetite}"
          + (f", 单笔风险预算 ${risk_dollars:.0f} CAD"
             f" (权益基数 ${equity_base:.0f} × {risk_pct:.0%})" if risk_dollars else ""))
    if risk_dollars and risk_dollars > 50:
        # The budget is derived from portfolio.yaml share counts x live
        # prices. If it looks too big, the share counts are probably stale —
        # sizing is still capped by sizing.max_position_cad, but fix the yaml.
        print("      [!] 风险预算偏大 — 检查 portfolio.yaml 股数是否与真实账户一致 "
              "(单笔仓位仍被 max_position_cad 封顶)")
    recommendations = rank_recommendations(
        hot_summaries, data, smh_df["close"],
        quality_factors=quality_factors,
        pead_signals=pead_signals,
        news_summaries=news_summaries,
        profile=risk_appetite,
        moonshot_set=set(MOONSHOT_LEADERS),
        valuations=valuation_data,
        risk_dollars=risk_dollars,
    )
    # Hysteresis (signal-level, BEFORE the gate): upgrades into the buy group
    # need a 2nd consecutive day before they publish — slow to add risk.
    from src.advisor.recommendations import apply_regime_gate, apply_hysteresis
    prev_snapshot = find_latest_previous(as_of)
    recommendations = apply_hysteresis(
        recommendations, (prev_snapshot or {}).get("actions"))
    n_pending = sum(1 for r in recommendations if r.pending_action)
    if n_pending:
        print(f"      [迟滞] {n_pending} 个买入升级待第 2 天确认")
    # Apply regime gate — temper buy signals on risk-off days
    recommendations = apply_regime_gate(recommendations, regime.buy_gate)
    rec_counts = {}
    for r in recommendations:
        rec_counts[r.action] = rec_counts.get(r.action, 0) + 1
    print(f"      -> {len(recommendations)} recs (gate={regime.buy_gate}): " +
          ", ".join(f"{k}={v}" for k, v in rec_counts.items()))

    # ------- shadow mode: candidate parameters run in parallel -------
    # Threshold-change discipline (configs/PARAMS-CHANGELOG.md): put candidate
    # values in configs/advisor.shadow.yaml, watch logs/shadow/ for two weeks,
    # only then promote to advisor.yaml. Published output is untouched.
    shadow_path = PROJECT_ROOT / "configs" / "advisor.shadow.yaml"
    if shadow_path.exists():
        shadow_cfg = yaml.safe_load(shadow_path.read_text(encoding="utf-8")) or {}
        with adv_config.override(shadow_cfg):
            s_regime = detect_regime(data, as_of=as_of)
            s_recs = rank_recommendations(
                hot_summaries, data, smh_df["close"],
                quality_factors=quality_factors, pead_signals=pead_signals,
                news_summaries=news_summaries, profile=risk_appetite,
                moonshot_set=set(MOONSHOT_LEADERS), valuations=valuation_data,
                risk_dollars=risk_dollars)
            s_recs = apply_hysteresis(s_recs, (prev_snapshot or {}).get("actions"))
            s_recs = apply_regime_gate(s_recs, s_regime.buy_gate)
        pub_map = {r.ticker: r.action for r in recommendations}
        sh_map = {r.ticker: r.action for r in s_recs}
        changes = sorted((t, pub_map[t], sh_map[t])
                         for t in pub_map if t in sh_map and pub_map[t] != sh_map[t])
        shadow_dir = PROJECT_ROOT / "logs" / "shadow"
        shadow_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"# shadow diff · {as_of.isoformat()}",
                 f"体制: 发布 {regime.label}/{regime.buy_gate} vs "
                 f"shadow {s_regime.label}/{s_regime.buy_gate}",
                 f"动作变化 {len(changes)} 个:"]
        lines += [f"- {t}: {a} -> {b}" for t, a, b in changes]
        (shadow_dir / f"shadow-{as_of.isoformat()}.md").write_text(
            "\n".join(lines), encoding="utf-8")
        print(f"      [shadow] 候选参数: gate {regime.buy_gate}->{s_regime.buy_gate}, "
              f"{len(changes)} 个动作不同 (logs/shadow/)")

    # ------- snapshot diff (today vs most recent previous) -------
    # Keyed by the DATA's trading date (as_of), not the wall-clock date — a
    # weekend/holiday run must not mint a phantom non-trading-day snapshot.
    # (prev_snapshot already loaded above for hysteresis.)
    print("[8c/9] Computing diff vs previous snapshot...")
    today_data = {
        "date": as_of.isoformat(),
        "composite_semi": float(semi_score.composite_0_100),
        "composite_ai_infra": float(ai_infra_report.composite_0_100),
        "macro_score": float(semi_score.macro.get("score", 5)),
        "actions": {
            r.ticker: {"action": r.action,
                       "action_pregate": r.pregate_action or r.action,
                       "pending": r.pending_action or None,
                       "Q": round(float(r.quality_score), 1),
                       "R": round(float(r.risk_score), 1)}
            for r in recommendations
        },
        "themes": [t.get("theme", "") for t in themes],
        "earnings_upcoming": [
            {"ticker": e.ticker,
             "date": e.report_date.isoformat(),
             "days": int(e.days_until)}
            for e in earnings_events
        ],
        "stretched": [t for t, s in hot_summaries.items()
                      if s.get("stretch", {}).get("severity", 0) >= 2],
    }
    diff = compute_diff(today_data, prev_snapshot)
    if prev_snapshot:
        print(f"      -> diff vs {prev_snapshot['date']}: "
              f"semi Δ{diff['composite_delta']:+.1f}, "
              f"upgraded={len(diff['upgraded'])}, "
              f"downgraded={len(diff['downgraded'])}")
    else:
        print("      -> no previous snapshot (first run)")

    # ------- render markdown -------
    print("[9/9] Rendering report + Discord push...")
    semi_md = render_full_report(
        run_date=date.today(),
        holdings=holdings,
        score=semi_score,
        smh_summary=smh_summary,
        soxx_summary=soxx_summary,
        leader_summaries=leader_summaries,
        rs_vs_smh=rs_vs_smh,
        amd_summary=amd_summary,
        amd_rs_smh=amd_rs_smh,
        amd_rs_nvda=amd_rs_nvda,
        macro_dict=semi_score.macro,
        portfolio_weight_amd=weight_amd,
    )

    ai_infra_md = render_ai_infra_section(ai_infra_report)
    news_md = render_news_section(news_summaries, NEWS_FOCUS) if news_summaries else ""
    earnings_md = render_earnings_block(earnings_events)
    levels_md = render_levels_block(action_levels)
    pmetrics_md = render_portfolio_metrics(pmetrics)
    themes_md = render_themes_block(themes)
    spec_budget = float(adv_config.get("breaker.speculation_budget_cad", 150.0))
    recs_md = render_recommendations_block_markdown(recommendations,
                                                    speculation_budget=spec_budget)

    # Compose final report. Recommendations go FIRST (most actionable),
    # then context (sectors/news/levels), then portfolio Greeks at the end.
    head, sep, tail = semi_md.partition("## 免责声明")
    holdings_md = render_holdings_watch(holding_alerts)
    ips_md = render_ips_drift(
        ips_rows, float((cfg.get("ips_targets") or {}).get("band_pp", 5)))
    body = (
        ips_md + "\n"               # IPS 偏离 / 部署进度最前
        + holdings_md + "\n"        # 持仓退出监察 — 已持有的优先于该买什么
        + recs_md + "\n"
        + ai_infra_md + "\n"
        + earnings_md + "\n"
        + levels_md + "\n"
        + themes_md
        + news_md + "\n"
        + pmetrics_md + "\n"
    )
    full_md = head + body + sep + tail   # sep/tail empty if header missing

    out_path = save_report(full_md, date.today(), PROJECT_ROOT)
    print(f"      -> saved {out_path}")

    # ------- Discord push (tech scanner embed; no holdings, no file) -------
    embed_kwargs = build_scanner_embed(
        run_date=date.today(),
        data=data,
        semi_score=semi_score,
        ai_infra_report=ai_infra_report,
        news_summaries=news_summaries,
        earnings_events=earnings_events,
        action_levels=action_levels,
        themes=themes,
        recommendations=recommendations,
        speculation_budget=spec_budget,
        diff=diff,
        portfolio_pnl=portfolio_pnl,
        market_movers=market_movers,
        watch_verdicts=watch_verdicts,
        regime=regime,
        rs_leaders=rs_leaders,
        analyst_field=render_analyst_field(analyst_data),
        valuation_field=render_valuation_field(valuation_data),
        data_as_of=as_of,
        freshness_warning_text=fresh_warn,
        fetch_note=fetch_note,
        buy_suppression=buy_suppression,
    )

    if args.no_discord:
        print("      (skipped, --no-discord)")
    else:
        send_embed(**embed_kwargs)

    # ------- save today's snapshot for tomorrow's diff -------
    # A pre-open rerun (wall date != as_of) must NOT overwrite the historical
    # snapshot written after that session's close — .info fields drift
    # overnight and would silently rewrite the prediction record that
    # evaluate_predictions.py scores.
    if snapshot_exists(as_of) and date.today() != as_of:
        print(f"      -> snapshot {as_of} already exists (pre-open rerun) — "
              "keeping the original")
    else:
        snap_path = save_snapshot(
            as_of,
            recommendations=recommendations,
            semi_score=semi_score,
            ai_infra_report=ai_infra_report,
            themes=themes,
            earnings_events=earnings_events,
            stretched_tickers=today_data["stretched"],
        )
        print(f"      -> snapshot saved: {snap_path.name}")

    print("\nDone. Composite semi score: "
          f"{semi_score.composite_0_100:.1f}/100 ({semi_score.label})")
    if amd_summary.get("stretch", {}).get("severity", 0) >= 3:
        print("AMD stretch severity HIGH — see local report for action plan.")


def _notify_failure(err: Exception) -> None:
    """A silently-missing brief is itself a data-integrity failure — tell the
    user the run died instead of letting them assume 'no news today'."""
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        return
    try:
        send_embed(
            title=f"daily_brief 运行失败 · {date.today().isoformat()}",
            description=(f"今日简报没有生成 — **不要把 \"没收到\" 当 \"无事发生\"**.\n"
                         f"```\n{type(err).__name__}: {str(err)[:600]}\n```"),
            color=0xED4245,
            footer="检查 logs/daily_brief.err.log",
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _notify_failure(e)
        raise
