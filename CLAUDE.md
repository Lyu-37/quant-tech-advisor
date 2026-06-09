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
