# 参数变更日志

规则: 任何 `configs/advisor.yaml` 阈值改动必须在这里记一条 (日期 / 改了什么 / 为什么 / 是否过了 shadow)。
信号类阈值 (regime, profiles) 改动必须先 shadow >= 2 周: 候选值写 `configs/advisor.shadow.yaml`,
看 `logs/shadow/` 里的每日 diff, 确认行为符合预期再合入本文件。

---

## 2026-06-10 — 初始化 (审计后基线)

- 全部参数从代码硬编码迁出, 取值 = 迁出时的现值 (即 2026-06-10 审计修复后的状态)。
- 这些值的已知出处:
  - `profiles.*`: 从未回测, 历史上为修复 46-buys 事件手调过一次 (对单一事件过拟合风险)。
  - `regime.crash_*`: 作者直觉值。
  - `regime.term_ratio_*`: 新增 (VIX 期限结构倒挂 = 压力, 文献支撑充分), 初始阈值 1.00/1.08 为行业常用值。
  - `sizing.*`: 新增, 风险预算法 (1% / 笔) 替代固定金额表; min 15 由 CAD->USD 往返换汇 ~3% 摩擦推出。
  - `breaker.*`: 新增, -25% / 28 天为作者设定, 未优化。
  - `hysteresis.confirm_days: 2`: 新增; 只延迟"进买入组", 不延迟卖出 — 慢加风险快减风险。
  - `data_quality.fail_closed: true`: 新增; 坏数据日抑制买入字段而非仅警告。
- 预注册评审判据 (写死, 防止事后找借口): 系统以 建仓 bucket 的 20 日超额收益 (vs QQQ) 为主指标,
  样本 N>=30 才有效; **连续两个季度 20 日超额 <= 0 -> 系统降级为仅状态描述, 买入字段停用**。

## 2026-06-10 — risk_appetite: aggressive -> balanced (portfolio.yaml)

- 审计建议 + 用户委托执行 ("你直接帮我做了吧")。档位保守一档, 让 regime 闸门成为第二道防线而非唯一一道。
- 属用户风险偏好选择而非系统阈值, 未走 shadow 流程; 效果可在每周 eval 报告的 bucket 构成变化里观察。
- 同日: portfolio.yaml 股数按 06-08 截图市值 / 06-08 收盘价重建 (原数据偏差 8x-20x 且互不一致);
  重建后组合 ~$1074 CAD 与截图 $1106 吻合。AMD 是否已按 planned_trades 卖出待用户确认。

## 2026-06-10 (深夜) — breaker.speculation_budget_cad: 150 -> 255

- 对齐实际已投入投机资本: IONQ 期初合并仓 3.088 股 x $58.91 USD ≈ 253.6 CAD (Wealthsimple 截图反推)。
- 同日发现并修正: 用户旧持仓的 BRK/MSFT 实为 Cboe Canada CDR (.NE, CAD 计价) 而非美股 —
  系统全链路 (市值/台账/日PnL) 的 CAD 判定从 ".TO" 扩展为 is_cad_listed (.TO/.NE/.V/.CN)。

## 2026-06-10 (12:50) — breaker.speculation_budget_cad: 255 -> 415; 持仓按 TFSA 实况重建

- 用户更正: 之前的转入 robo 截图是旧照片。TFSA 实况 (11:36 截图): IONQ 5.088 / VDY 4.8037 /
  VFV 8.4826 / QQQX 15.8586, 组合 ~$3000 CAD。BRK.NE/MSFT.NE 已不持有, 移除。
- IONQ 加仓 lot2 (2.0 股, 价格估算) 入台账; 投机资本 ~415 CAD, 熔断基数对齐。
- account_equity_cad 800 -> 3000。VFV/QQQX/IONQ 的 cost_basis 为估算值, 待用户提供
  各持仓 all-time return 后校正。

## 2026-06-11 — IPS 激进版定稿 + "挣来的激进"阶梯预注册

- 用户选定激进配置 (configs/IPS-2026-06.md, gitignored): VFV 47 / QQQX 13 / XEF 15 /
  XIC+VDY 5 / HEQL(1.25x) 10 / 投机桶 10, 有效股票敞口 ~102.5%, 最坏回撤预算 -55%。
- **预注册升级阶梯**: 投机桶 cap 基线 10%; 周报 N>=30 且 建仓 bucket 20d 超额 > 0
  连续两个完整季度 -> cap +5pp (10->15->20, 封顶 25%); 连续两季 <=0 -> 系统降级
  仅状态描述 + cap 回 10% + 熔断冷却翻倍。杠杆 sleeve 固定 10% 不参与阶梯。
- 永不清单入册: 3x 日重置不作持仓 / 投机超 cap 不补 / 不卖核心补投机 /
  TFSA 不开投机新仓 / 期权暂禁。
- sizing.max_position_cad 60 -> 300: 组合从 ~$3k 扩到 ~$13k, 投机桶 ~$1,300,
  3-5 票篮子单票上限相应放大。机械随账户规模缩放, 非信号阈值, 不需 shadow。
- watchlist 整体替换为 IPS 部署目标 (VFV/XEF/QQQX/HEQL 回踩位)。
- breaker.speculation_budget_cad 暂保持 415, 非注册投机账户注资 $900 落地后改 1300。
