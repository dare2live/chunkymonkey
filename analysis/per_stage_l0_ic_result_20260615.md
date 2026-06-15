## Phase B 实验1: per-stage L0 IC — reversal 条件化 (2026-06-15)

> 状态: live。owner 实验=`backend/scripts/experiment_per_stage_ic.py`; 结果=`per_stage_l0_ic_result_20260615.json`;
> 设计=`conditional_stage_strategy_design_20260614.md`。预注册判据冻结在脚本头 (跑前)。

### 结果 (reversal lookback=20, horizon=5, embargo=5, 全宇宙 5204 股, 2023-01+, 35 窗 513 OOS 日)
| stage | 形态 | OOS RankIC | IC_IR | 对基线倍数 |
|---|---|---|---|---|
| ALL | 市场级 (对照) | **+0.0640** | 0.387 | 1.0 (复现 L0 标尺, 管线无 drift) |
| 1 | 底部/低位 | **+0.0038** | 0.021 | ~0 |
| **1.5** | **突破中** | **+0.1559** | **0.895** | **2.44x** |
| 2 | 上升趋势 | +0.0658 | 0.453 | 1.03 |
| 3 | 顶部分布 | +0.0534 | 0.352 | 0.83 |
| 4 | 下跌趋势 | +0.0312 | 0.179 | 0.49 |

### 结论 (让数据说话, 不报喜不报忧)
1. **条件化假设成立**: reversal OOS RankIC 确实因形态而异 (0.004 ~ 0.156 跨 stage), 市场级 +0.064 是平均稀释值
   —— 证实"非万能公式, 按形态分"的核心思路。**形态维有解锁价值。**
2. **用户具体假设被数据推翻**: 猜的是"reversal 在低位(Stage1 底部)有效", 实测 Stage1 ≈ 0; 真正赢家是
   **Stage 1.5 突破中** (+0.156)。经济解释 (待证): 突破中且近 20 日跌幅大的股 = "回踩突破/失败后修复"
   continuation 最强。**measured not estimated 的价值: 直觉错了, 数据对了。**
3. **未转正 (PENDING_ABLATION)**: +0.156 = +144% 相对基线, 触发 §4.2 相对红线 (异常高=leakage 警报先怀疑)。
   PIT 链已核干净 (stage[t]/feature[t]/label 全 as-of, technical_stage.py 每行只用 ≤t; embargo=5; OOS-only),
   IC_IR 0.895 示日度一致非少数窗驱动 —— 但**红线要求 ablation 才能信**, 不直接采信。

### 下一步 (Phase B 续)
- **ablation 验 Stage1.5 +0.156** (转正前必做): (a) Gate2 MC 截面置换 (打乱股-收益配对, IC > 95% null?);
  (b) 子周期稳定性 (2023/2024/2025 分段 IC); (c) 排查 stage1.5 定义与 reversal 特征的机械重叠 (是否选择性artifact)。
- **填 (公式×形态) 矩阵**: 对 macd/ma/turtle 跑同实验 → 找"哪个公式适配哪个形态" (用户核心想法的完整证据)。
- 若 ablation 过 → 解锁立方体形态维, Stage1.5×reversal 是第一个正 edge cell。
