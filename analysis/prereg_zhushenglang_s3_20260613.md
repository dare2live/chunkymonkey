# 预注册冻结 — 主升浪 S3 (LightGBM walk-forward 重验 ML 假设)

> 状态: **FROZEN 2026-06-13** (跑前冻结; 看到结果后改判据 = 挪门柱, 禁止)。
> owner = analysis/zhushenglang_rebuild_plan_20260613.md (S3 步)。grill 走 engineering-discipline.
> 数据地基: fact_rally_ground_truth (S1 落库, 读法B) + fact_feature_panel (68 PIT 特征复用)。

## 创世层

- 为什么存在: 一次性判决"研究日志 V12/V16 的 70.9%/86% 胜率"在**严格 PIT + walk-forward +
  embargo-180** 纪律下是否成立 — 这是产品北极星 (主升浪猎手) 的 ML 层生死验证。原研究 §1
  自列样本/窗口局限, 且很可能未做重叠标签的 embargo (经典泄漏陷阱), 本实验严格补上。
- 死线: (1) **embargo >= 180 交易日强制**: label 取 [t+1, t+180] max gain, 训练/测试前瞻窗
  重叠 = 标签泄漏; 不做 embargo 的任何"高胜率"全废。(2) **forward_ret_* / close / 源 meta /
  键列必须排除出特征** (它们是 label 或非因果)。(3) 判负按处置条款归档, 不放宽不复跑。

## 假设 (单一, 可证伪)

LightGBM 在 68 个 PIT 特征 (fact_feature_panel) 上, 对突破事件宇宙内的 TRUE 主升浪 (读法 B:
峰位<=180日, gain>=50%, dd>-20%) 做二分预测, 在 walk-forward (expanding, embargo>=180 交易日)
OOS 上, 区分力显著高于 base rate (10.3%), 且在标签置换对照下崩塌到随机 (证明非管道泄漏)。

## 冻结定义 (判断法典, 人话+机器话)

| 条款 | 人话 | 机器话 |
|---|---|---|
| 目标 | 突破事件是否真主升浪 (二分) | `y := fact_rally_ground_truth.is_true_rally` (读法 B, 2023+ 段) |
| 特征 | 68 个 PIT 列, 排除 label/键/源 meta | `fact_feature_panel` 全列 EXCLUDE (stock_code,date,close,kline_source_*,forward_ret_*) |
| 样本 | 2023+ 突破事件 (面板起 2023-01, 2022 段无特征排除) | JOIN on (stock_code,date), 实测 26,797 事件 / 2,784 TRUE |
| 切分 | expanding 训练, OOS 折间 embargo>=180 交易日 (防重叠标签泄漏) | 折 k 训练 [start, T_k], 测试 [T_k + 180 交易日, T_k + 180 + win]; >=3 折 |
| 选参 | 固定超参 (不在判决轮 Optuna; 防 OOS 选参错误教训) | LGBM 固定: n_est=300/lr=0.05/leaves=31/seed=20260613; Optuna 留判正后增量轮 |
| base rate | 训练窗 TRUE 占比 | `mean(y)` 实测 10.3% |

## 修订 1 (2026-06-13, 首轮判后 — 泄漏排除完整性修正, 判据数值线未动)

首轮 verdict=REJECT 由 J2 leakage-ceiling 触发 (AUC 0.779 > 0.75)。ablation 彻查确认 = **特征级
前瞻泄漏, 非真 edge**: 手写 EXCLUDE_COLS 漏了 `follow_net_return_5/10/20/60/90d` 标签族 (builder
build_feature_panel_duck.py 已标 PIT_LABEL_COLS / MODEL_INPUT_EXCLUDED_COLS, LEAD(exit_price) 算的
前瞻标签), 它们贡献 top15 gain 51.8% (follow_net_return_90d 单列 29%); 剔除后 fold0 AUC 0.8368→
0.4797 (比随机差), 仅用这 5 列即 0.8419 = edge 几乎全来自标签泄漏。
**修正 (非挪门柱, 是排除 builder 已声明的标签)**: EXCLUDE_COLS 改为复用 builder
`MODEL_INPUT_EXCLUDED_COLS` 单一真相源 (含全 PIT_LABEL_COLS) — 不手写第二份防漂移。三判官数值线
(J1/J2/J3) 与 embargo 一字未动。corrected 重跑结论见 verdict JSON; 预期 AUC≈0.5 (无真 edge,
诚实负结果) — 若 corrected 反而显著, 那是去掉泄漏后的**真**信号 (用户要的"不放过真实增强")。

## 三判官 + 泄漏对照 (全部满足 = GO; 任一不满足 = NO-GO)

```yaml
# prereg_zhushenglang_s3 verdict constants — 脚本常量必须与本块逐字一致 (--check-prereg)
J1_signal_exists:
  rule: "OOS (embargo>=180d) top-decile precision >= 1.3x base rate 且 OOS AUC >= 0.55"
  precision_mult: 1.3      # 锚 = K 线层天花板 ~60% (研究日志 §10), 区分力须显著但不夸张
  auc_floor: 0.55          # 锚 = 研究 V9 walk-forward 真实 58-60% / random 55.3%
J2_not_leakage:
  rule: "OOS AUC <= 0.75 (>0.75 主升浪预测=异常高 leakage 警报, 不兴奋) 且标签置换对照 AUC in [0.45,0.55]"
  auc_ceiling: 0.75        # §4.2 异常高红线 (主升浪 ML 真实期望 < 此; 超 = 怀疑泄漏)
  shuffle_auc_band: [0.45, 0.55]  # 折内标签置换 → 必须退化随机; 否则管道泄漏 (硬证伪装置)
J3_fold_consistency:
  rule: "top-decile precision > base rate 的折数 >= 多数 (sign consistency, 防单折偶然)"
```

## 死亡条款 (实验自身)

- 感知死: 判决 7 天内未入 ledger = 作废重跑。
- 判断死: embargo < 180 交易日强行开跑 = 判决无效 (标签泄漏); 标签置换对照 AUC 显著 > 0.55 =
  管道有泄漏, 先修不判。
- 谄媚死: 看到结果后讨论"降 embargo / 换折法 / 调超参就显著了" = 触发本条; 全部冻结。

## 判负处置 (预注册)

S3 NO-GO (区分力不显著, 或 embargo-180 下 V12/V16 的 70-78% 崩塌) → 结论 = "研究日志的高胜率
含重叠标签泄漏 / 样本偏差, K 线+现有特征层 PIT-honest 区分力不足"; 主升浪 ML 线降级为
"需新特征 (CYQ 全市场复算 modal 轮 / 链谱 / 新数据源) 才可能成立", 不接 live, 不在现有特征上
继续调参续命。判正 (GO) → 才谈 CYQ modal 特征扩展增量轮 + paper_sim。

## 七问对账

为什么存在/死线 = 创世层 | 目标 = 三判官 + 置换对照表 | 拍板 = 用户 (判正后是否投 modal 特征扩展) |
环境 = 本地 (LightGBM 27k×68 秒级, 不上 modal) | 什么算好 = yaml 块 | 预算 = 本地 CPU 分钟级 +
0 API + 0 modal | 缺口 = 2022 段无特征 (面板起 2023-01, 已披露) + Optuna 留判正后

## 开跑前置 (机器可检)

`experiment_zhushenglang_s3.py --check-prereg` PASS (常量逐字一致) + fact_rally_ground_truth
落库 + fact_feature_panel JOIN 2023+ 100% (实测) + lightgbm 在盘 (4.6.0)。
