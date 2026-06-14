# L0 裸K线基准 Spec — best-OOS-params 标尺 (2026-06-14)

> 状态: 草案 — 执行寻优前必过 grill gate (chunkymonkey-governance) + pre-reg 冻结判据。
> owner = 本文件 (L0 设计真相源); goal.md P1 行为薄指针; 上位 = alpha_validation_program_spec §3.1 (技术公式消费者轴)。
> 缘起 (用户 3 条指令, 2026-06-14): (1) 最小主动信号 + 公式全保留为 config 备选;
> (2) **裸K线寻出的最佳 OOS 参数作基准**; (3) **不要过拟合** + 吸取项目教训 + 充分用 moth。

## 0. 一句话
裸K线基准 = 对纯 OHLCV 派生的技术公式做 **walk-forward OOS 参数寻优, 取最佳 OOS 参数配置**作标尺;
每个 alpha 因子必须超越这个"调到最优的纯价量策略"才算携带真增量。防过拟合是本模块第一约束。

## 1. 为何 best-OOS-params 而非固定参数 (用户 msg2)
固定拍参数的基准太弱 (alpha 轻易超越 = 假增量)。诚实地板 = 纯价量在**最优调参**下能达到的 OOS 上限。
alpha 只有超越它, 才证明增量来自新数据而非"价量本可以调出来的"。

## 2. 防过拟合契约 (用户 msg3 第一约束; 复用幸存 optuna_config.yaml 治理)
参数寻优是过拟合重灾区 (在历史搜参→in-sample 漂亮/OOS 崩)。死亡条款映射 + 已编码治理:

| 防线 | 机制 (optuna_config.yaml 已编码, 重建代码读它) | 死亡条款 |
|---|---|---|
| 选参只看 OOS | `walk_forward.default_mode=expanding_monthly` (R1); selector 只读 `oos_*` 列; `governance.require_walk_forward=true` 拒 in-sample 入库 | 估计死 |
| 去搜索过拟合 | Deflated Sharpe (Bailey-LdP 2014, `deflated_sharpe` 节; cells×trials 多时校正 E[max SR]); trials 上界 `max_n_trials=500` | 估计死 |
| 异常高数字熔断 | `governance` realistic caps: sharpe<5 / win<0.95 / avg_ret<0.5; 命中走异常核查协议 | 自欺死/感知死 |
| 判据先于结果 | pre-reg 冻结 J1-J3 + prereg_hash 机器对账 (改判据=作废) | 谄媚死 |
| label 安全 | purged k-fold + embargo >= 1x forward 期; bars[:sig_i+1] | 泄漏死 |
| 限维度 | **只搜少量参数** (信号核心 + 5维 strategy exit), 不堆维度; 搜索空间越大越拟合噪声 | 估计死 |
| 诚实报弱 | OOS 弱就报弱 (基准弱反而好: alpha 容易超越的真实地板); 不调到舒服方向 | 谄媚死 |

**反例对照 (§4.5 + mythos)**: stage_opt MAX(oos_sharpe) 给每 signal_date 用未来 Optuna = systemic leakage;
vol-aware stop/target hardcode "业界常用" 丢 search space; selector ORDER BY sharpe (非 oos) = in-sample fit 假象。

## 3. 标尺池 (最小主动信号, 用户 msg1: 池子小防过拟合)
全 9 个纯 OHLCV 公式见 `formula_candidates.yaml` (全保留为 config 备选)。L0 **active 子集**选最规范、最少:
先 3-4 个 (macd_golden_cross[评估器幸存] + ma_base_breakout + turtle_breakout + reversal_short_term),
其余 status=candidate (定义留存, 不进 L0 寻优, 防"池子越大越拟合")。涨停/活跃度/gs 等待 active 子集验通再逐个解锁。

## 4. reset 后 survives vs rebuilds (下沉核证, 非信旧 spec)
**幸存 (config 治理契约完整)**: `optuna_config.yaml` (全治理) / `stock_formula_optuna.yaml` (阈值) /
9 个 `formula_*.yaml` (信号参数) / `formula_engine/{base,ddl,macd_golden_cross,shared_windows,technical_stage}.py` /
`portfolio_walk_forward/{metrics,regime}.py` (NAV→sharpe/excess) / `v_price_kline_qfq` (PIT 复权 K线源)。
**须重建 (reset 删的执行代码, 非复活全 god-layer, 只裸K线切片)**:
- `services.optimization` config loader (OptunaConfig 读 optuna_config.yaml) + enforce_pre_optimize/enforce_pre_insert。
- walk-forward expanding_monthly OOS 主循环 (复用 metrics.py)。
- `plan_validator.enforce_optuna_plan` (search space 非空校验; 2026-05-26 29/34 公式白跑反例)。
- deflated_sharpe.py (读 optuna_config.deflated_sharpe)。
- 缺失公式评估器 (turtle/reversal/dynamic_ma 等) 从 git 639e0dfb~1 恢复 OR 参数驱动通用评估器。
- search_space.py (5维 strategy: hp/stop/target/trailing/buy_offset, 读 optuna_config.search_space)。

## 5. build 序列 (每步有界 + commit; 寻优 RUN 前必 pre-reg + grill)
1. **公式候选库** `formula_candidates.yaml` (本轮): 9 公式索引 + OHLCV-only 分类 + 评估器状态 + active/candidate。✓ 公式保留落地。
2. **walk-forward OOS 引擎** (复用 metrics.py): expanding_monthly 窗口 + OOS RankIC/IC_IR/sharpe; 防回退测试 (red→green 验未来不可见)。
3. **optimization 治理层最小重建**: OptunaConfig loader + plan_validator (search space 非空) + selector 只读 oos_* + DSR。
4. **pre-reg** `analysis/prereg_l0_baseline_<date>.md`: 冻结 J1-J3 (OOS RankIC 阈 / DSR p>0.95 / max_dd 约束) + prereg_hash。
5. **裸K线寻优 RUN** (grill gate 后): active 池逐公式 walk-forward OOS 搜参 → best-OOS-params → 经 consumer_alpha 执行器写 `fact_consumer_alpha_ic_scan` (consumer_id=L0_baseline) + verdict。
6. **标尺落地**: baseline OOS 指标入留档, 作 S3 每个 alpha 的判负线。

## 6. 验收 gate
- 引擎: 单测 expanding_monthly 窗口正确 + OOS 不含未来 (red→green) + metrics 复用无回归。
- 治理: search space 非空校验真触发 (单测, 防死闸反例); selector 只读 oos_* (静态守门)。
- 基准: best-params 来自 OOS 非 in-sample (审计); DSR 校正后仍显著才采信; 异常高数字走核查协议。
- moth: 防过拟合不变量固化 (require_walk_forward / oos-only selector / pre-reg 存在)。

## 7. 已知坑 (本模块特有, 防重蹈)
- 池子贪多 = 拟合噪声: active 子集刻意小, 验通再解锁 (用户 msg1 最小信号)。
- trials 贪多 = E[max SR] 虚高: 守 max_n_trials + DSR 校正。
- 基准"太好" = 警报非成就: sharpe>5/年化>100% 触发核查, 真实纯价量地板应温和。
- 把 in-sample best 当 OOS: selector 死守 oos_* + require_walk_forward, 防 stage_opt leakage 反例复发。
