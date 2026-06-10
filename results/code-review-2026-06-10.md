# quant-spy-ma 深度审查报告

日期: 2026-06-10
审查范围: src/advisor/ 全部 22 个模块 + daily_brief.py / preclose_brief.py + 配置 + 计划任务 + evaluate_predictions.py
审查标准: "如果这里错了, 用户会亏钱"
环境实测: yfinance 1.3.0, pandas 3.0.2; 关键 .info 字段语义已用 ARM/NVDA/MU/GEV 线上数据验证

**总评分: 4/10 (当前状态)。** 作为"省盯盘时间的状态描述工具"勉强合格, 作为"买卖建议来源"有多处会直接输出危险信号的缺陷。详见末尾总评。

标记说明: [错误信息→交易] = 该问题会让你基于错误信息做真实交易决策。

---

## 一、致命 (4 条)

### F1. Watchlist 的"buy zone"包含任意深度的崩盘价, 企稳判定形同虚设 [错误信息→交易]

**位置**: `src/advisor/watchlist.py:54` 和 `:57-60`

```python
in_buy_zone = abs(dist) <= zone_tolerance or dist < 0   # 任何低于支撑的价格都算"到买区"
...
not_new_low = close.iloc[-1] > recent.min() * 1.001     # 只要收盘比 5 日最低高 0.1%
stabilized = bounced_today or not_new_low
```

**问题**: `dist < 0` 没有下限——股价跌破 SMA50 之后再崩 25%, 仍然算"已到买区"。"企稳"判定是 OR 关系: 今天涨 0.01% 就算, 或者收盘比 5 日最低点高 0.1% 也算(即使今天刚创了新低, 只要收盘从最低点弹起 0.1% 就通过)。两个都极易满足。

**为什么危险**: 你的 watchlist 里有 SOXL 和 TQQQ(3 倍杠杆)。一次半导体崩盘中, SOXL 单日 -15%、跌破 SMA50 后 30%, 任何一根日内小反弹 → Discord 推送"**可以入场** SOXL — 数据支持入场"。preclose 的"收盘前结论"在 `buy_gate == "caution"`(score 42-61, 很常见)时会直接说"X 到买点了, 收盘前可分批建仓"(preclose_brief.py:93-95 的分支顺序: 只有 temper 才压制 actionable)。这是教科书式的"接飞刀"逻辑, 套在杠杆 ETF 上。

**修复**:
1. buy zone 加下限: `-0.10 <= dist <= zone_tolerance`(跌破支撑 10% 以上 = 支撑已失效, 不是买点);
2. 企稳改 AND 逻辑且加强: 连续 2 日不创 5 日新低 + 收盘高于昨日最高价(或至少昨日收盘);
3. 杠杆 ETF 单独规则(zone 更窄、必须 regime 不是 temper);
4. watchlist verdict 必须过 regime 闸门——现在它完全绕过(见 F3)。

### F2. "最新交易日"没有任何校验——旧数据被标成今天推送 (你被坑过的 bug 根因还在) [错误信息→交易]

**位置**: `src/advisor/fetcher.py:43-48`(`datetime.utcnow()` + 只回退周末不识别假期), `regime.py:41-44` / `market_movers.py:52-57` / `watchlist.py:43` 等所有 `iloc[-1]/iloc[-2]` 调用, embed 标题用 `date.today()`(daily_brief.py:379, preclose_brief.py:149)

**问题**: `use_cache=False` 只是绕开了缓存这条路, 没有解决根本问题: **没有任何一行代码校验"取回来的数据最后一根 bar 是不是预期的交易日"**。fetch 返回什么就当"今天"用什么, 标题日期却永远是 `date.today()`。

**实锤证据**: `data/state/` 里存在 `snapshot-2026-05-25.json`——5 月 25 日是 Memorial Day 休市, 计划任务照常跑(install_daily_task.ps1 的 trigger 只排除周末不排除假期), 那天推送的"今日涨跌/今日异动/体制"全部是上周五 5/22 的数据, 但标题、snapshot、diff 全标成 5/25。还有 snapshot-2026-05-16(周六)、2026-05-24(周日), 是你手动周末跑的, 同样把周五数据标成周末日期。

**为什么危险**: 这就是"周一反弹日显示周五大跌"的同一类故障。下一个必然触发点: **2026-06-19 Juneteenth(下周五, 休市)**, daily brief 会把 6/18 周四的数据当"今天"推给你。另外任何盘前手动跑(周一早上)也会复现: fetch 永远新鲜, 但取到的就是上周五的数据, 系统照样说"今日"。

**修复**(优先级最高的一个改动):
1. 在 `fetch_universe` 返回后取 `max(df.index[-1].date() for df in data.values())` 作为 `data_as_of`;
2. 用 NYSE 交易日历(`exchange_calendars` 库, 或手写 2026-2027 假期表: 7/3, 9/7, 11/26, 12/25, 1/1, 1/19, 2/16, 4/3...)算出"预期最新交易日";
3. 两者不一致 → embed 标题和 description 第一行显著标注"**数据截至 2026-06-18 (今日休市/数据未更新)**", 所有"今日涨跌"字段改叫"最近交易日涨跌"; 假期直接不推送或推一条"今日休市"短消息;
4. 计划任务脚本加假期判断提前退出。

### F3. Regime 闸门有三个旁路: 短期反弹候选、moonshot 卡、watchlist [错误信息→交易]

**位置**: `src/advisor/recommendations.py:487-515`(`apply_regime_gate` 只处理 建仓/加仓持有/试探建仓), `scanner.py:452-474`(moonshot 字段独立生成, 不看 gate), `daily_brief.py:207`(watch_verdicts 不经过 gate), `recommendations.py:331-334`(短期反弹候选在 gate 之前生成, gate 不认识它)

**问题**: 你修 46-buys 事件时加的闸门只盖住了主推荐矩阵。崩盘日(`buy_gate == "temper"`)的同一条 embed 里:
- "建仓"正确地降级成了"等企稳再建仓";
- 但 [反弹] 字段照样列出最多 5 只"短期反弹候选 $30"(崩盘日恰恰是 mean-reversion 候选最多的时候——大量股票满足"-25%~-55% 12-1 动量 + 60d 反弹"条件);
- [M] 10x 候选字段照样说"单笔 $30-50 试水";
- watchlist 字段照样喊"可以入场"。

**为什么危险**: 系统在顶部说"今天 risk-off 不要买", 中部给出 5-8 个带具体金额的买入建议。你只要哪天偷懒只看中间, 就在系统性下跌日加仓投机标的。这不是假设——这正是该闸门当初要防的事故的残留面。

**修复**: `apply_regime_gate` 之外, 在 `build_scanner_embed` 入口统一执行: `if regime.buy_gate == "temper": 跳过反弹候选字段、moonshot 字段加"今日 risk-off 不建议操作"前缀并隐藏金额、watchlist 的"可以入场"降级为"到位但大盘 risk-off, 等体制转好"`。闸门必须管住**所有**含金额的输出。

### F4. Moonshot 永不清仓, 且把"避免"翻译成"持有博弹性" [错误信息→交易]

**位置**: `src/advisor/recommendations.py:338-347`

```python
if is_moonshot:
    if action in {"清仓", "避免"}:
        ret_20d = summary.get("ret_20d") or 0
        action = "减仓" if ret_20d < 0 else "持有博弹性"
```

**问题**: 两层错误。(1) moonshot(包括你实际持有的 IONQ)被结构性豁免"清仓"——最差只会收到"减仓", 无论跌成什么样。(2) 更糟: 质量低+风险高本应"避免"的股票, 只要近 20 天反弹为正, 就被翻转成"持有博弹性"——这个动作的解释文案是"用赚到的钱继续博大涨…止损上移到保本位"(recommendations.py:130-131), 它假设你有浮盈。对一个在高点买入、深套 40%、近 20 天死猫跳 +3% 的 IONQ 持有者, 系统输出的是一个**正面措辞的持有建议**, 措辞里的"保本位止损"对深套仓位毫无意义。

**为什么危险**: "lottery ticket 不止损"的逻辑只对"用利润博"的仓位成立。系统不知道你的成本价(scanner 是无持仓视角), 却输出隐含"你有 house money"的建议。该止损时你拿着系统给的"持有博弹性"心理安慰不动手。

**修复**: (1) "避免"不许翻转——moonshot 的"避免"就显示"避免(框架对 10x 标的部分适用)"。(2) 保留不喊清仓可以, 但当 `dd_from_52w_high < -0.50` 或 `momentum_12_1 < -0.60` 时必须附加显式警告: "若是高位买入的深套仓, 该信号不构成继续持有的理由"。(3) "持有博弹性"文案改为前置条件式: "仅适用于有浮盈的仓位"。

---

## 二、严重 (8 条)

### S1. 同一条 embed 内部自相矛盾: regime 和 market pulse 是两套独立实现、不同阈值 [错误信息→交易]

**位置**: `regime.py:138-141`(crash: SPY≤-2% 或 VIXΔ>25% 或 VIX≥28)vs `scanner.py:342-355`(pulse 系统性大跌: 最差指数≤-2% 或 VIXΔ>15% 或 VIX>25)

**问题**: 你问的"composite score 和 market pulse 会不会冲突"——会, 且不止它们。VIX=26、单日 +18% 的那种日子: description 写"**今日大盘: 系统性大跌**", 顶部体制字段写"体制: neutral — 买入信号可正常参考"(VIX 26<28, Δ18%<25%, 不触发 crash; score 可能还在 42 以上)。一份报告两个声音, 你信哪个? preclose 和 daily 之间也会因此互相矛盾。

**修复**: 删掉 scanner 里的 `_today_move`/pulse 重新实现, pulse 直接从传入的 `regime` 对象派生(`regime.label` 的措辞映射)。一个市场状态, 一个真相源。

### S2. debtToEquity 归一化在 5 处有断崖, NVDA/ARM 正好悬在崖边 [错误信息→交易]

**位置**: `factors.py:94`, `guru_screens.py:86`, `guru_screens.py:217`(三处同样的 `de/100 if de > 5 else de`)

**实测证据**: yfinance 的 `debtToEquity` **永远是百分比**。今天实测: NVDA=6.555(即 6.6% 负债率), ARM=5.926, MU=14.9, GEV=24.9。代码假设"小于 5 的是倍数"——这个假设没有任何成立场景, 而 NVDA/ARM 离 5 只差 1 个百分点。NVDA 下季度若 D/E 降到 4.9(完全可能, 它在还债): `de_ratio=4.9`(被当成 4.9 倍杠杆!)→ QMJ safety 分 10→0(composite 掉 4 分, 0-10 制), Buffett"低负债"+20 分→0, Piotroski 杠杆项丢 1 分。**一家继续降杠杆的公司, 在它负债率改善的那天, 系统给它的安全分从满分跳到零分。**

**修复**: 删掉启发式, 一律 `de / 100`。这是确定性修复, 没有任何风险。

### S3. "PEAD"把财报日跳空算进 drift, 而且根本没有盈余惊喜维度 [错误信息→交易]

**位置**: `factors.py:259-268`

**问题**: (1) `get_indexer(..., method="nearest")` 对盘后(AMC)财报取到的是**公告前**的收盘价, drift 里包含了财报当日的整个跳空。一只财报跳空 +12% 然后横盘 20 天的股票, 系统报告"财报后正向漂移 +12%, 历史上仍有 60d 持续动量"——但那 12% 你根本吃不到, 真正的"漂移"是 0。Bernard-Thomas 的 PEAD 明确从公告后窗口起算。(2) 学术 PEAD 的条件变量是 SUE(标准化盈余惊喜), 这里完全没有——beat 还是 miss 都不知道, 只看价格。这不是 PEAD, 是"财报后价格动量", 包装成了学术因子。它还占 quality 分 10% 权重(recommendations.py:188-201), 跳空 +15% 直接换算成 8.75/10 的加分。

**修复**: (1) drift 起点改为公告日**之后**第一个交易日的收盘(AMC/BMO 都安全); (2) 文案改名"财报后动量"; (3) 若想要真 PEAD, 用 `earnings_dates` 的 `Surprise(%)` 列做 SUE 方向, 价格 drift 只做确认。

### S4. 大佬规则被周期股的失真增长率灌爆, 且与估值陷阱过滤互不沟通 [错误信息→交易]

**位置**: `guru_screens.py:136,151-156`(earningsGrowth), 整个 guru 模块无 trap 逻辑; 对照 `valuation.py:39-59`(trap 过滤只作用于 valuation tilt)

**实测证据**: `earningsGrowth` 是**季度 YoY**: MU=+756%, GEV=+1816%(周期底部低基数 comp)。Lynch 规则 `eg > 0.25 → +25 分`照单全收; MU 的 PEG 0.32 再 +45。MU 今天就会在 preclose 拿到 Lynch/Greenblatt/Burry 多票"看好"→"大佬最爱 MU, 收盘前可分批"。而**同一天**的 daily brief 里, valuation 模块可能把 MU 标成"账面便宜但有陷阱"(你专门写的周期陷阱过滤!)。陷阱过滤只接到了 valuation tilt 这一根线上, 大佬共识、preclose 简报完全没接。低 PE/低 PEG 的周期顶恰恰是 value 类规则(Graham/Burry/Greenblatt/Lynch)集体失真的场景——6 票里 4 票都会被同一个失真源点亮, "共识"是假的独立性。

**修复**: (1) `analyze_gurus` 接收 stretch severity + above_target 参数, 触发 trap 时在 consensus 上加"周期顶低估值陷阱风险"标记并降权; (2) earningsGrowth 加 cap(|eg|>2 视为低基数失真, 降权或用 revenueGrowth 替代); (3) preclose 的"大佬最爱"行直接显示 trap 标记。

### S5. Piotroski 不是 F-Score——9 项里该是"同比变化"的全用了静态水平

**位置**: `guru_screens.py:195-238`

**问题**: 真 F-Score 9 项中 4 项是 Δ(ΔROA、ΔLeverage、ΔLiquidity、ΔMargin/ΔTurnover)+ 1 项增发检查。这里全部替换成静态水平(ROA>0, D/E<1, CR>1.5, GM>30%, eg>0)。结果: 盈利的科技大盘股普遍拿 6-8 分, "Piotroski 看好"对你的 universe 几乎无区分度——它本来是为在**低 PB 价值股**里挑"正在改善的"设计的。docstring 自称"faithful quantitative encodings"(guru_screens.py:16-17), 不实。

**修复**: 用 `Ticker.financials/balance_sheet` 的年度对比算真 Δ 项(yfinance 有数据); 或诚实改名"财务健康检查(简化版)"并在 footer 注明。Graham 同理: 对全科技 universe 它结构性全票"回避", "X/6 大佬看好"的分母里有两票是注定不可能的——展示时建议改成"X/6 (Graham/Burry 类价值规则对成长股结构性偏空)"。

### S6. 硬编码的过期持仓市值驱动所有组合级输出 [错误信息→交易]

**位置**: `daily_brief.py:121-126`

```python
# Set market values (CAD, from screenshot)
user_supplied_values = {"AMD": 380.11, "GOOG": 185.69, ...}
```

**问题**: 组合总值、AMD 权重(daily_brief.py:155)、组合 P&L%、portfolio Greeks、压力测试的美元数全部基于一张截图时刻的冻结数字。而且它和 `portfolio.yaml` 已经互相矛盾: yaml 的 `planned_trades` 写着 AMD 全卖(380.11 正是那笔计划的金额), holdings 里 AMD 还在。**系统有三个互相打架的持仓真相源**: 硬编码 dict、yaml holdings、你的真实账户。本地报告的"GEV 操作建议"还有写死的"持仓占比 ~9%"(ai_infra.py:145)。

**为什么危险**: "最大单仓 AMD 38%, SMH -20% 压力测试亏 $X"——全是幻影组合的数字。基于它做加减仓决策就是基于错误信息。

**修复**: 删掉 `user_supplied_values`, 市值 = `data[t].close.iloc[-1] × shares × FX`(`portfolio_daily.py` 已经实现了这个逻辑, 直接复用); cost_basis 从 yaml 读。yaml 的 holdings 和 planned_trades 状态同步问题: 在 README 加一条"成交后必须更新 holdings 并清空 planned_trades", 或加启动时一致性警告。

### S7. 12-1 动量的 percentile 路径是死代码——数据只有它需要量的一半

**位置**: `indicators.py:58-66`(需要 504 根 bar)vs `fetcher.py / daily_brief.py:117`(lookback_days=400 自然日 ≈ 275 根 bar)

**问题**: `momentum_12_1_score` 的滚动分位数分支永远走不到, 生产中永远落进"no_history"的粗糙线性映射(`5 + current/0.06`)。推荐理由文案里设计的"历史 X% 分位"(recommendations.py:367-372)从不出现, 走的全是 raw 阈值分支。注: `momentum_12_1` 本体(iloc[-21]/iloc[-252])实现是对的, 这点可以放心。

**修复**: lookback_days 提到 800(分位数有意义需要 2 年), 或删掉 percentile 分支承认用线性映射。顺带: 800 天也让 `ret_252d`/52w 高低不再"刚好够"。

### S8. 失败 ticker 静默消失 + 关键数据缺失时系统性偏乐观 [错误信息→交易]

**位置**: `fetcher.py:91-95`(失败只 print, 不重试), `regime.py:71,89-104`(VIX 缺失 → 不扣分、不触发 crash), `guru_screens.py` 全部 `if x and ...`(字段缺失 → 0 分趋向回避), `factors.py:297-305`(缺失 → 中性 5 分)

**问题**: 80+ 顺序 HTTP 请求(fetch)+ 每 ticker 4-5 次重复 `.info` 调用(quality/valuation/analyst/guru 各自独立抓!)≈ 单次 daily run 200+ 次 Yahoo 请求, 无重试、无退避。被限流时: (1) 个股静默从所有版块消失, 报告看起来仍然完整; (2) `^VIX` 失败那天, regime 失去最大权重的恐慌信号——**在 Yahoo 最容易出问题的高波动日, 闸门恰好变得最乐观**; (3) SPY/SMH 失败直接 KeyError 崩掉, 你只是收不到简报(计划任务无失败通知)。三个模块对缺失数据的哲学还不一致(中性/偏空/偏多)。

**修复**: (1) `.info` 每 run 抓一次存共享 dict, 四个消费者用同一份(速度 ×4, 一致性 +); (2) fetch 加 2 次指数退避重试; (3) embed footer 显示"N/80 成功, 失败: X,Y"; (4) VIX/SPY 缺失 → `buy_gate` 强制不高于 caution 并在顶部警告; (5) main() 包 try/except, 崩溃时往 webhook 推一条"今日简报失败: <原因>"。

---

## 三、中等 (12 条)

### M1. HOT_TECH 含重复 ticker, breadth 和 movers 双重计数
`universe.py:130-145` — IONQ/RGTI/QBTS 同时在 QUANTUM_LEADERS 和 MOONSHOT_LEADERS, HOT_TECH 拼接后出现两次。`compute_breadth`(regime.py:50)分子分母双计, `compute_today_movers` 可能让同一只股票占据涨幅榜两格, `daily_brief.py:195` 的 action_levels 重复两行。修复: `HOT_TECH = list(dict.fromkeys(...))`。

### M2. moonshot 评分分支次序错误, `>0.50` 不可达
`scanner.py:146-150` — `elif m121 > 0.20: 8.0; elif m121 > 0.50: 10.0` 第二个分支永远走不到。12-1 动量 >50% 的标的少拿 4 分(0-100 制)。把 0.50 分支放前面。

### M3. `_is_stale` 用 UTC 日期且不识别假期
`fetcher.py:43-48` — 蒙特利尔 20:00(EDT)后 `utcnow().date()` 已是明天: 周日晚 8 点后 expected=周一, 缓存永远"stale"; 假期(周一休市)expected 停在周一, 全天重抓。生产两条入口 use_cache=False 不受影响, 但 `analyze_semi.py:48` 和 `deploy_advisor.py:228` 走默认 True, 受影响。修复: `datetime.now(ZoneInfo("America/New_York"))` + 交易日历(和 F2 同一个日历)。

### M4. 15:30 盘中价被写进缓存, 同日 ad-hoc 工具会当成收盘价
`fetcher.py:74` — `fetch_one` 无条件 `to_parquet`, preclose 在 15:30 把盘中价写盘(end 键和 17:30 的 daily 相同)。16:00-17:30 之间跑 `analyze_semi.py`/`deploy_advisor.py`(use_cache=True)会把 15:30 的价格当"今日收盘"做分析。修复: 当 ET 时间 <16:05 时, 写缓存前丢掉最后一根当日 bar(读取路径仍返回完整数据)。

### M5. 新闻情绪词典: "ai" 是正面词
`news.py:24-31` — 你的 universe 全是 AI 股, 几乎每条标题含 "AI" → 系统性正偏。"cut" 算负面(Fed rate cut 对成长股是利好), "high" 算正面("inflation high")。这个 avg_score 进了 mean-reversion 候选的否决条件(recommendations.py:301-303)和 moonshot news_pts(scanner.py:154-156)。修复: 删 "ai"/"high"/"demand" 这类裸词, 只留方向性短语; 或对每 ticker 用相对自身历史的 z 分而非绝对分。

### M6. 财报日历把 Yahoo 估计日期当确定事件
`events.py` — `.calendar` 的 Earnings Date 对未确认的季度是 Yahoo 的**估计**, 经常偏移 1-2 周。"距今 5 天 ★ 本周事件"有假精度, 你可能为一个不存在的财报日提前减仓。修复: 显示为"约 6-12 (Yahoo 估计)"; 7 天内的事件尽量用 `get_earnings_dates()` 二次确认。

### M7. 闸门改名的动作不在 ACTION_RANK 里, regime 切换日产生假升降级潮
`daily_state.py:17-27` — "等企稳再建仓"/"短期反弹候选"/"持有博弹性"都不在表里(默认 rank 5)。risk-off 日: 昨天"建仓"(9)→ 今天"等企稳再建仓"(5)→ diff 报"评级下调"×N; 次日体制恢复又报"上调"×N。你看到的是闸门开关, 不是基本面变化。修复: 补全 rank(等企稳再建仓=9 或单列), 或 diff 用 gate 前的原始 action 比较。

### M8. 价格低于 SMA50 时 Top3 卡的"止损"在现价上方, R/R 显示 0.0:1
`recommendations.py:656-664` + `levels.py:72` — `stop_sma50` 对"试探建仓"类(价格可能在 SMA50 下)给出高于现价的"止损", stop_pct>0 时 rr=0, 卡片显示"止损 $X (+3%) · R/R 0.0:1"。修复: `stop = min(L.stop_tight, L.stop_sma50)`; stop 仍在上方则不显示 R/R。

### M9. ATR 系数的数学注释是错的, 假精度链条
`levels.py:50` — 注释称 `1.4 ≈ E[|X|]/σ`, 实际正态下 E|X|/σ=0.798。1.4 当成"close-to-close σ 放大到含跳空真实波幅"的经验系数也许凑合, 但整条链(ATR 近似 → 目标价 → "R/R 2.3:1" 两位小数)对外呈现的精确度远超输入精度。conviction 也是按动作格子给的常数(`pick_action`), "5/5 置信"无任何统计含义。修复: 注释改实话; R/R 显示一位小数并加"约"; conviction 要么删要么接到真实证据数量上。

### M10. 计划任务 10 分钟超时 + 失败完全静默
`scripts/install_daily_task.ps1`(ExecutionTimeLimit 10min)— daily run 200+ 网络调用, 慢网/限流日会被任务计划器直接杀掉, 你只是"今天没收到简报"。配合 S8 的崩溃路径, 系统的失败模式全是静默缺席。修复: 超时提到 30 分钟; main() 兜底 webhook 报错(见 S8)。

### M11. valuation 把"数据缺失"标成"无盈利"
`valuation.py:82` — `forward_pe is None` 就走无盈利分支。Yahoo 抖一天, NVDA 也会被标"无盈利 (tilt -0.3)"。修复: 先看 `trailingPE`/`profitMargins`/`netIncomeToCommon` 是否证明盈利, 都缺才标"数据不足"(而非"无盈利")。

### M12. evaluate_predictions.py 的回看窗口锚错了日期, 老 snapshot 评不了
`scripts/evaluate_predictions.py:47-56` — `fetch_universe(tickers, lookback_days=(end-start).days)` 里 lookback 是从**今天**往回算的, 不是从 target_date; 13 天前的 snapshot 取不到当时价格, ticker 静默跳过 → 命中率统计悄悄只覆盖最近一周多。修复: lookback_days = (today - target_date).days + 15。这个脚本是整个系统最有价值的部件(见总评), 值得修好。

---

## 四、轻微

- L1. `recommendations.py:386-388` 运算符优先级导致 supports 文案缺右括号: "基本面优质 (高质量, 利润率 56%" — 每天出现在报告里。三元表达式需要整体括起来。
- L2. `fetcher.py:3-4` docstring 称 "one yf.download call for the whole universe", 实际是逐 ticker 循环——文档与实现不符(批量 download 恰恰是该做的优化)。
- L3. `market_movers.py:101` "新 52w 高"判定: 距旧高 0.1% 以内也算"新高"。
- L4. `news.py:304-306` 新闻无日期时 fallback 成"现在", 旧闻穿透 7 天过滤。
- L5. `scanner.py:32-82` `_build_sector_tilts` 已不被调用(死代码); `recommendations.py:716-719` new_sells 逻辑注释自认"wiring 没修"。
- L6. `daily_brief.py` 渲染本地报告时 `parts = semi_md.split("## 免责声明")` — 若 report.py 改标题文案, parts[1] 直接 IndexError。
- L7. 周末手动跑会写周末日期的 snapshot, 污染 diff 的 prev_date 语义(和 F2 一起修: 非交易日拒绝写 snapshot)。

---

## 五、对你五个问题的直接回答

**1. 缓存 bug 修干净了吗?** 没有。修法是绕过(use_cache=False), 不是修复。`_is_stale` 的 UTC 漏洞和假期盲区还在(M3), 15:30 盘中价还在污染缓存(M4), `analyze_semi.py`/`deploy_advisor.py` 仍走缓存路径。更重要的是: 缓存只是"旧数据当今天"的一种成因, 另一种成因(fetch 回来的就是旧数据——盘前/假期/周末)完全没有防护, 而且有 5/25 Memorial Day snapshot 实锤已经发生过(F2)。**"今日"语义目前靠运气而非校验。**

**2. 前视偏差?** 严格的未来函数没有发现: momentum_12_1 的 iloc[-21]/[-252]、滚动分位、range_position、52w 计算都只用过去数据; 老 backtest 引擎甚至有一个写得不错的 lookahead 测试。两个灰色地带: (a) PEAD 把公告日跳空计入"可延续的 drift"——不是看未来, 但把**不可交易的收益**当信号卖给你(S3); (b) preclose 在 15:30 用盘中价算所有日线指标(RSI/SMA/突破)——对"盘中最后一看"的用途是合理设计, 但写进缓存就变成污染(M4)。

**3. 阈值是依据还是拍脑袋?** 分三档。有文献锚点但实现走样的: 12-1 momentum(实现对, 但 score 映射的 0.06 斜率是拍的)、QMJ(60/40 权重注释里写"Asness weighting", Asness 论文里没有这个权重——z 分等权才是)、PEAD(见 S3)、Piotroski(见 S5)。行业惯例级别的: PEG<1 便宜、Graham PB×PE<22.5、F-Score 分档。**纯拍脑and且影响大的**: Q/R 矩阵全部切点(57/42/48/77——注释直说是为了让 46-buys 不再发生而调的, 这是对单一事件的过拟合)、quality 六因子权重 25/20/10/15/20/10("Research-backed" 是自我声明)、regime 各扣分值(-25/-15/-12/-18)、guru 各规则加分值、stretch 30%/50% 分界、moonshot 评分全部系数。这些没有一个跑过回测——老回测引擎只测过 SPY 均线交叉, advisor 的打分体系从未被历史数据验证过。它们不一定是错的, 但目前它们的地位是"作者直觉", 报告呈现时却带着两位小数的权威感。

**4. 大跌日 46 个买入信号还会发生吗?** 主矩阵不会(gate 会拦), 但旁路会: 崩盘日"短期反弹候选"+"10x 试水"+"watchlist 可以入场"加起来给出 6-10 个带金额的买入建议是完全可能的(F3), 且 caution 档(score 42-61)对 watchlist 的"收盘前可分批建仓"毫无约束(F1)。闸门本身的脆弱点: 它 100% 依赖 VIX/SPY 当日数据, VIX 取数失败的那天它最乐观(S8); 连续阴跌(每天 -1.5%, VIX 缓涨)不会触发 crash 条件, 只能靠 score 缓慢滑落, 存在 2-3 天的迟钝窗口——这是单日阈值设计的固有盲区, 建议加"5 日累计 -5%"条件。

**5. 存活者偏差 / 这个工具到底能不能信?** Universe 是 2024-2026 牛市叙事的赢家名单(量子、核电、光通信、AI 基建——全是已经涨出来才被你注意到的板块)。后果: (a) 所有"percentile in own history"类指标的基准分布来自幸存者的牛市历史, 中性分被系统性抬高; (b) "大佬共识"只在这 80 只里排序——它说"MU 最被看好"的真实含义是"在你预先挑的热门股里, MU 的 yfinance 字段最像价值股", 这和巴菲特会不会买 MU 没有任何关系; (c) stretch >30% 在这批股票里是常态而非"2 sigma 事件"(stretch_flag 注释的统计声明对这个 universe 不成立)。系统无法告诉你"该看的股票不在名单里", 也无法告诉你"这个板块整体是泡沫"——它的世界里只有这 80 只。

---

## 六、最该补的测试 (按价值排序)

系统现状: advisor 零测试, 只有老回测引擎 2 个文件。以下每个用例都对应上面一个真实缺陷, 写测试的过程就是修复验证:

1. **数据新鲜度**(F2): 冻结时钟到 2026-06-19(Juneteenth)和周一 09:00 ET, 喂只到前一交易日的 fixture 数据 → 断言 embed 标注"数据截至 X"且不把旧数据标为"今日"。
2. **闸门全覆盖**(F3): 枚举所有 action × 所有 buy_gate, 快照断言: temper 时任何输出字段不得含 suggested_dollars>0 或"可以入场"。现在这个测试会立刻红。
3. **watchlist 接飞刀**(F1): fixture: 价格在 SMA50 下方 25%、昨日创 5 日新低、今日 +0.4% → 断言不是"可以入场"。现在会红。
4. **D/E 断崖**(S2): de=4.99 和 5.01 两侧 → safety 分必须连续。现在会红。
5. **PEAD 跳空**(S3): fixture: 财报日 +10% 跳空后 20 天横盘 → drift 应 ≈0。现在返回 +10%, 红。
6. **regime 数据缺失**(S8): data 里删掉 ^VIX → buy_gate 不得为 pass。现在会红。
7. **regime 边界**: SPY -1.99%/-2.01%, VIX 27.9/28.0, VIXΔ 24%/26% 六个点的分类快照。
8. **diff 假信号**(M7): 同一组推荐, 仅 gate 从 pass→temper → compute_diff 不得报告 downgrade。现在会红。
9. **HOT_TECH 唯一性**(M1): `assert len(HOT_TECH) == len(set(HOT_TECH))`。一行, 现在会红。
10. **52w/iloc 停牌安全**(F2 派生): 某 ticker 数据少一天 → movers 不得把它的昨日涨跌当今日。
11. **quality 卫生**(已有逻辑的回归保护): profitMargins=1.75 → None; ROE=2.6 → None; eg_qoq=6 → None。
12. **snapshot 非交易日拒写**(L7)。

测试基建建议: 把 yfinance 调用全部隔离到 fetcher/factors 的薄层后面, 用录制的 parquet/json fixture(`tests/fixtures/`)驱动其余纯函数——advisor 的 90% 逻辑是纯函数, 完全可以离线测试。

---

## 七、总评

**当前能不能放心每天用?**

按用途分两个答案:

**作为"市场状态描述器/省盯盘工具"——可以用, 但要带三个心智补丁**: (1) 看到任何"今日"先核对日期是不是真的交易日(F2 修好前); (2) 大跌日只看顶部体制字段, 跳过所有带金额的字段(F3 修好前); (3) "大佬共识"读作"价值类字段筛选器", "PEAD"读作"财报后涨跌", "Piotroski"读作"是否盈利大盘股"——按它们实际是什么, 而不是按它们叫什么。

**作为"买卖建议来源"——现在不能, 而且问题不在 bug 数量**。这个系统最深的结构性问题是: **它从未被要求证明自己**。所有阈值没有回测, 所有建议没有追踪(evaluate_predictions.py 存在但有 bug 且不在任何计划任务里), "建仓"的历史命中率是多少? 没人知道, 包括系统自己。声称"描述不预测", 但输出形式(动作动词 + 金额 + 目标价 + R/R 比 + 5/5 置信度)全是预测的修辞。修辞制造的确定性, 是这个系统卖给你的最大的不存在的东西。

**评分: 4/10。** 给到 4 的理由: 数据获取在调度时间点上基本工作、核心指标数学大多正确、闸门/陷阱过滤/止损位这些"防御意识"的方向都对、代码可读性好。扣 6 分的理由: 三个买入旁路绕过闸门、watchlist 接飞刀逻辑、"今日"语义无校验且已有事故实锤、字段语义断崖悬在 NVDA 头上、同一报告两套市场判断、以及"零验证循环"。

**到 7/10 需要什么**(按顺序):
1. 修 F1-F4(一个下午的工作量, 全部是局部修改);
2. 数据截至日显示 + NYSE 日历 + 失败告警(半天);
3. .info 共享 + 重试(半天);
4. S1-S6 的语义修复(一天);
5. 上面前 9 个测试(一天);
6. **把 evaluate_predictions.py 修好(M12)、扩展到 5d/20d 双窗口、设成每周五自动跑并推送 Discord**——从此每个阈值调整都有命中率数据背书。这是从"工具"变"可信工具"的那一步。

**到 8 以上**: 需要第二数据源对 SPY/VIX 做交叉校验(免费方案: Stooq), 以及对 Q/R 矩阵做一次正经的历史回放(你有 400 天数据和全部纯函数, 回放框架两天能搭)。再往上是 yfinance 免费数据的天花板, 不值得在这个架构里追。

---

## 八、额外建议 (你要求"有更好的建议一定要说")

1. **交易成本没有建模, 而它对你的仓位规模是致命的**。你在加拿大用 CAD 买美股: 券商免佣但 CAD→USD 换汇 ~1.5% 单边, 往返 ~3%。"短期反弹候选: $30, 5 日不涨 +5% 就走"——这个策略的目标利润是 5%, 摩擦成本是 3%, 期望值直接腰斩还不算点差。**$150 预算下任何 5 日级别的短线 USD 策略都几乎注定为券商打工**。建议: 短线类建议(短期反弹候选)只对 USD 余额或 ≥$500 仓位显示; 或在文案里印上"往返换汇成本 ~3%, 本建议目标收益 5%"让矛盾自己显形。说真的, 以你的资金规模, 系统里值得保留的是"建仓/分批/止损位"这类周-月级信号, 5 日级别的信号全部是噪声+摩擦。
2. **把"动作"输出改成"条件"输出**。你已经有 cancel_trigger 这个好设计——扩展它: 与其说"建仓 NVDA $60", 不如说"NVDA 满足 X/Y/Z, 若你本来就想买, 当前是相对好的位置; 破 $A 该论点失效"。同样的信息, 移除掉伪装成确定性的部分。这和"描述不预测"的自我定位才是一致的。
3. **每天的 embed 加一行数据质量页脚**: `数据截至 2026-06-09 收盘 · 78/80 成功 · guru 字段完整度 91%`。三个数字, 把所有静默降级变成可见。
4. **风险档位建议从 aggressive 降到 balanced**。aggressive 的 r_mid=77 意味着风险分 76 的股票还能拿到"加仓持有"。配合你 universe 里 25 只 moonshot, aggressive 档实际上把风险过滤器拧到了几乎最松。你已经被 46-buys 教育过一次, 闸门是后来打的补丁——更稳的做法是档位本身保守一点, 让闸门成为第二道而不是唯一一道防线。
5. **删掉或合并重复计算**: scanner 里重算 summaries(daily_brief 已算过 hot_summaries)、pulse 重算市场状态(S1)、4 处独立 .info 抓取(S8)。每处重复都是未来不一致的种子。
6. **关于 universe 的诚实选项**: 给"大佬共识"换个基准——同时跑一遍 S&P 500 随机 30 只作对照组, 每月看一次你的 universe 平均 guru 分比对照组高多少。如果系统性高 15 分, 说明这些规则在你的池子里测出的是"成长股长这样", 不是"值得买"。一次性脚本, 一小时工作量, 能校准你对整套 guru 输出的信任度。
7. **pandas 3 / Python 3.12 现代化**: `datetime.utcnow()` 已 deprecated, 反正 F2/M3 要改时区逻辑, 一并迁到 `datetime.now(ZoneInfo("America/New_York"))`, 这也是正确语义本身。

---

*审查方法说明: 全部 22 个 advisor 模块逐行人工审读; debtToEquity/earningsGrowth/pegRatio 字段语义用本机 venv (yfinance 1.3.0) 对 ARM/NVDA/MU/GEV 实测验证; 日期类缺陷用 data/state/ 中的周末/假期 snapshot 文件取证。未运行完整 brief(避免触发 Discord 推送)。*
