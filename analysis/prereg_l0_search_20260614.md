# Pre-registration: L0 裸K线参数寻优 (2026-06-14, 冻结于 RUN 之前)

> 谄媚死守门: 本判据在看到寻优结果**之前**冻结。prereg_hash 机器锁 (改判据=作废)。
> owner=analysis/l0_bare_kline_baseline_spec_20260614.md §5 step 4-5。

prereg_hash: d80e8ce636826a4f8e8b7e74f486bb3a3d534aac4ddbcf8107e1faa8e01ea409

## 假设
裸K线公式 (active 4) 在各自小网格上寻优, 取 walk-forward OOS 最佳参数, 可得比默认参数更高 (或不更差)
的 OOS RankIC 标尺。预期改进温和 (网格小, 防过拟合); 不预期突破 §4.2 红线。

## 冻结的实验设计 (RUN 前定, 不许事后改)
- **数据**: v_price_kline_qfq 全市场, start=2023-01-01, 5204 股。
- **目标度量**: OOS RankIC (expanding_monthly walk-forward, horizon=5, embargo=5, 只读 OOS test)。
- **搜索空间** (frozen, 见 formula_search_spaces.yaml): macd 9 / ma_base 4 / turtle 4 / reversal 3 组合。
- **选择规则**: max |OOS RankIC| (反转类负相关也是可预测信号; 标尺取最强可预测性)。
- **多重比较校正**: DSR (Bailey-LdP) deflate 最佳 IC_IR, n_trials=该公式网格组合数。
- **防泄露 3 门**: PIT 行为门 / 切分纪律 (embargo>=horizon) / 异常红线 — RUN 内联强制。

## 判官 (J1-J3, 冻结)
- **J1 (改进非退化)**: 每公式 best |OOS RankIC| >= 默认参数 baseline |OOS RankIC| (寻优不该更差)。
  报告改进幅度; 若 best < default 则记录"网格未含更优点"不强解释。
- **J2 (显著性)**: best IC_IR 的 DSR p-value > 0.95 才算"真有 alpha 非试错噪音"; <=0.95 则标
  `selection_noise_risk` 不宣称显著 (诚实报弱)。
- **J3 (异常核查)**: 任一组合 |OOS RankIC| > 0.3 (§4.2 红线) -> 触发异常核查协议 (ablation/PIT 溯源),
  不直接采信也不直接排除。预期不触发 (默认参数实测 ~0.06)。

## 成败 (预注册, 防事后挪门柱)
- **成功**: J1 全过 + 至少 1 公式 J2 显著 -> best-OOS-params 入标尺 (替默认参数标尺)。
- **部分**: J1 过但 J2 全不显著 -> 标尺仍用 best params 但标 `dsr_not_significant` (网格太小/信号太弱)。
- **失败/异常**: J3 触发 -> 走异常核查, 暂不更新标尺。

## 算力
本地 (~20 组合 × 全市场单公式特征+IC ≈ 5-10 min, 可接受)。网格扩大/加公式则上 Modal (用户:大计算交Modal;
spec §6 dry_run 默认 + artifact-manifest 契约; Modal adapter 待建非本 RUN 阻塞项)。
