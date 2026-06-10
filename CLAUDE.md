# Quant SPY SMA — Project Rules for Claude Code

## 项目目标
SPY 标的双均线交叉（SMA crossover）的最小可运行回测，作为后续扩展的骨架。
**这不是生产交易系统**，是一个学习/实验框架。

---

## 数据约束（违反 = 整个回测作废）

1. **禁止前视偏差（look-ahead bias）**
   - 信号在 t 日收盘后生成，但 position 是 `signal.shift(1)`，即 t+1 日才执行。
   - 任何新策略实现都必须保留这个 `.shift(1)` 模式，或等价的延迟。
   - 修改 `src/strategy.py` 中的 lookahead 保护逻辑前必须征得人工同意。

2. **复权处理必须开启**
   - yfinance 调用必须用 `auto_adjust=True`，否则分红/拆股会污染收益。

3. **禁止静默 dropna**
   - 缺失值必须显式处理（前向填充 / 删除 / 标记），不能直接 `.dropna()` 装作没事。

---

## 代码规范

1. **策略 = 纯函数**：输入 DataFrame，输出 DataFrame，无全局状态、无副作用。
2. **禁止 magic number**：所有窗口大小、阈值、手续费率必须在 `configs/*.yaml` 中。
3. **测试覆盖**：新增任何策略或回测逻辑，必须配对单元测试。lookahead 测试是 mandatory。
4. **类型提示**：所有公开函数必须有 type hints 和 docstring。

---

## 反过拟合规则

- 默认参数 `(20, 60)` 是教科书值，**不是优化结果**。
- 任何参数扫描必须用 walk-forward 分析，禁止全样本优化。
- 全样本 Sharpe > 2.5 → **停下来审计是否有数据泄漏或前视偏差**。
- 单一标的、单一时间段的"好结果"不算证据。要么扩到多标的，要么做 OOS 验证。

---

## Agent 边界（针对 Claude Code 自身）

- **不许自评结果**：禁止用 "good"、"profitable"、"ship-ready"、"strategy works" 等措辞评价回测结果。只能客观陈述指标。
- **不许悄悄绕过保护**：不要为了让测试通过而修改 lookahead 保护，要么修代码要么修测试，但必须显式说明。
- **每个 sprint 只做一件事**：例如"加 Bollinger Bands 策略"和"加 walk-forward 分析"是两个 sprint，不要混。
- **TDD**：新策略实现前先写 lookahead 测试。

---

## 下一步建议（按优先级）

1. **Walk-forward 参数分析** — 把 (short, long) 在滚动窗口上 sweep，看参数稳健性
2. **第二个策略对比基线** — Bollinger Bands 或 Donchian，验证回测框架可扩展
3. **波动率目标仓位（vol targeting）** — 把固定 1.0 仓位换成动态仓位
4. **多标的扩展** — 同一框架跑 SPY/QQQ/IWM 三个 ETF
5. **手续费/滑点敏感性分析** — 看策略在不同 cost 假设下的退化

---

## 启动命令

```bash
pip install -e ".[dev]"  # 安装依赖
python run.py            # 跑默认回测
pytest tests/            # 跑测试
```

---

## Advisor 子系统硬性约束 (2026-06-10 审查后确立)

完整审查报告: `results/code-review-2026-06-10.md`。修改 advisor 前先读它。

1. **"今日"语义必须经过 as-of 校验**
   - 所有日期逻辑必须走 `src/advisor/market_calendar.py`(ET 时区 + NYSE 假期表),禁止 `datetime.utcnow()` / 裸 weekday 判断。
   - 运行的有效数据日 = `fetcher.consensus_as_of(data)`(横截面众数, 对 24h 品种隔夜 bar 和临时休市都稳健), 再被 `expected_latest_session()` 钳制。
   - 数据日 != 预期交易日时, embed 必须带"数据截至 X"警告; `iloc[-1]` 滞后于 as-of 的 ticker 必须从一切"今日"计算中剔除。
   - 假期表只写到 2027 年底, 2028 前必须扩展 `HOLIDAYS`(越界会 raise)。

2. **regime 闸门覆盖一切带金额的买入输出**
   - 建仓/加仓/试探/短期反弹候选/moonshot 试水/watchlist 可以入场, 全部受 `buy_gate` 管制, 不允许新增绕过闸门的买入路径。
   - VIX 或 SPY 数据缺失时闸门强制不高于 caution(缺数据 != 安全)。
   - market pulse 必须从 regime 对象派生, 禁止第二套市场状态判定。

3. **yfinance 字段语义(实测验证, 勿改回)**
   - `debtToEquity` 恒为百分比 → 一律 `/100`, 禁止 ">5 才是百分比"启发式。
   - `earningsGrowth`/`revenueGrowth` 是季度 YoY, 周期底会出现 +700%+ 的低基数失真 → guru 规则对 >200% 打折。
   - `earnings_dates` 依赖 lxml(在 pyproject 里), 缺了会静默返回空 → PEAID 信号消失。
   - `.info` 每 run 通过 `factors.get_info()` 共享缓存, 禁止各模块自己拉。

4. **PEAD drift 从公告后首个收盘起算**(跳空不可交易, 不算 drift), 负 surprise 时价格漂移不加分。

5. **moonshot 豁免有边界**: "避免"永不翻转; 距 52w 高 -50% 或 12-1 < -60% 的死亡螺旋保留"清仓"。

6. **snapshot 是预测历史记录**: 按 as-of 交易日存, 盘前重跑禁止覆写已存在的 snapshot(evaluate_predictions 依赖它)。

7. **关键回归测试在 `tests/test_advisor_critical.py`** — 改 watchlist/regime/gate/factors/calendar 任何一处, 先跑 `pytest tests/`。
