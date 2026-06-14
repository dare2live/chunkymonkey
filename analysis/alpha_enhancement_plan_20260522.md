# Alpha Enhancement Plan — 2026-05-22 (evidence-driven rewrite v2)

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


> v1 (09:55) 是 doc 推断 (data ready × 互补性 × 复杂度), 不是 实证驱动. 用户 push back: "经过这么多轮的验证你应该有个大概的感觉哪些指标可能会显著提升, 可以按这个规律去决定测试的参数".
>
> v2 重写: **按 ChunkyMonkey 历史几轮 retrain 的实证规律选方向**, drop 没历史证据的 "加 features / 加新数据" 方向.

## 历史实证累积 (按 commit + goal.md ledger)

| 改动 | 效果 | 规律 |
|---|---|---|
| 本轮 stability retrain: 加 `--window-rank-ic-std-penalty=0.50 + neg_rate-penalty=0.20` | PBO 0.626→0.102 (5.1x), NDCG10 +8.5%, OOS RankIC IR **1.535→11.186 (7.3x)**, Sharpe 1.32→2.09, ann +65% | **改 Optuna objective 加 stability penalty = 显著 + 低 risk** |
| v3 102 features +75% RankIC vs baseline | 触发 leakage (inst_path_a latest snapshot + sector 99.978% fallback) | **加 features 大幅 RankIC 提升 → 怀疑 leakage** |
| Hardcode vol stop/target/trailing → Optuna walk-forward sweep | 显著 + 真 alpha (commit history) | **阈值/权重必走 Optuna + walk-forward** |
| portfolio_backtest naive +45% vs paper_sim 含 tx_cost | naive 多算 ~30pp | **paper_sim 必含真实成本/T+1** |
| swap_uplift_estimate 公式 → 真 K-line forward | 拉低 ann 33pp | **forward 反事实必真 K-line** |
| lm735/sniper265 6 维 hyperparams sweep | True IS/OOS drop 81.36% FAIL | **同 model 多维 sweep 不能解 model-level blocker** |
| `mart_stock_industry_pit` fallback 99.978% → observed only | RankIC 假 +0.035 → 真 +0.022 | **PIT-strict 必 observed, fallback 高 = leakage** |

## 归纳

**显著提升的 4 个共同点**:
1. 改 Optuna **objective** (per-window stability / portfolio metric)
2. PIT-strict 数据路径 (observed > 99%)
3. 真实交易成本反事实
4. Walk-forward OOS gate

**没显著效果 / 多 leakage 的方向**:
- 加 features (v3 leakage)
- 加新数据源 (announcement / institution visit 数据缺)
- 多维 hyperparams sweep 同 model (lm735 系列)
- formula-based estimation (swap_uplift_estimate 公式)

## v2 Phase 排序 — 实证驱动

### Phase A (推荐立即启动, 极低 risk 高确定性)

**Stability penalty weight sweep** — 现 std=0.50 / neg_rate=0.20 是 first-try, walk-forward sweep 不同 weight combo 找 optimal.

- **Why**: 本轮已证 stability penalty 工作 (PBO 5.1x, IR 7.3x). Weight 是否 optimal 未知; first-try 不一定 best
- **实施**:
  - Sweep combo: (std=0.3, neg=0.1) / (std=0.5, neg=0.2 current) / (std=0.7, neg=0.3) / (std=1.0, neg=0.5)
  - GCP retrain 4 × 80-trial Optuna stability retrain
  - 每个 combo 走 post_retrain_pipeline → Phase4 gate verdict
  - 找 verdict PASS (非 warn_only_proxy) + Sharpe/ann/dd 最优 combo
- **数据**: 无新数据
- **Risk**: 极低 (现有 objective + 现有 panel, 只 sweep penalty weight)
- **Cost**: 4 × ~$0.50 = $2 GCP / 4 × ~1.5h = 6h
- **预期**: 至少 1 个 combo 显著优于 current 0.5/0.2 (基于 hyperparams sweep 经验, first-try 通常非 optimal)

### Phase B (中确定性, 需 objective 函数改写)

**Portfolio-objective replace NDCG ranking** — Optuna optimize Sharpe / Calmar / max_dd 直接, 而非中间 ranking metric.

- **Why**: stability penalty 思路 = "直接 optimize 想要的", portfolio metric 比 NDCG ranking 更接近实盘目标
- **实施**:
  - 改 `run_p0b_lambdamart_v6.py` Optuna objective: 加 `--objective-mode {ranking,sharpe,calmar,multi_objective}`
  - sharpe mode: 每 window OOS 跑 mini paper_sim 算 portfolio Sharpe, return 作为 trial objective
  - GCP retrain 1 × 80-trial
- **数据**: 无新数据
- **Risk**: 中 (代码改 + objective 函数计算量大 — paper_sim per trial 慢)
- **Cost**: ~$1 GCP (objective 复杂可能 2-3 倍慢) / ~3-4h
- **预期**: 不一定显著优于 Phase A, 但 alpha source 不同 (portfolio metric vs ranking metric)

### Phase C (中等确定性, regime evidence)

**Regime-conditional model** — bull/sideways/bear 三套 hyperparams or regime feature 显式.

- **Why**: 旧 model bull regime OOS RankIC -0.012 vs neutral/bear positive — **regime mismatch evidence**, model 在 bull 失效
- **实施**:
  - 加 regime label 到 panel (用 `mart_market_perception_emotion_daily.market_regime_score` 或 main project regime detector — 注意 Perception 物理边界, 应 main project 自有 regime feature)
  - Optuna 加 regime-conditional weight: lambda_bull / lambda_sideways / lambda_bear
  - OR 训练 3 sub-models, regime gate ensemble
- **数据**: regime label (现有 main project regime detector 或 panel feature)
- **Risk**: 中 (regime 切分 OOS 风险; regime label 本身 PIT)
- **Cost**: ~$0.50 GCP / ~2h
- **预期**: 解决 bull regime 失效, ann / Sharpe 可能再 +10-20%

### Drop (不推荐, 历史无显著效果或多 leakage)

| 方向 | drop 原因 |
|---|---|
| Multi-horizon label engineering | Panel 已 3 horizon cols, 单 fwd_20d 已 Sharpe 2.09, 边际预期小. 不优先 |
| 股东减持公告 windowing | 加 features 历史多 leakage / 8K rows 数据小 |
| LHB 席位 windowing | 同上, 53K rows 但席位 alpha 半衰期短难抓 |
| Capital flow 多滞后 | v4 已用, 多 lag 边际 |
| Perception regime 接 panel | 破物理边界硬约束 (4 次重申) |
| Factor decay timing | 复杂度高, 实证缺 |

## 推进顺序

1. **Phase A** (立即, 6h $2): stability penalty weight sweep (4 combo)
2. **看 A 结果**:
   - 若有 combo PASS (非 warn_only_proxy) + Sharpe/ann 进一步提升 → 走 plan §5 promote 路径
   - 若全 warn_only_proxy → 启 Phase B portfolio-objective
3. **Phase B** (~$1 3h): portfolio-objective if A 不解 promote
4. **Phase C** (~$0.5 2h): regime-conditional if B 仍 warn

每 phase 独立 challenger 不动 champion. 走 plan §5 标准路径. 总预算 ~$3.5 GCP, 在 月预算 buffer 内 (现 91.4% 剩 $1.30, 月底前不够; 但下月 reset 后可 fit).

## 月预算考虑

current month projected $13.71 / $15 (91.4%, $1.30 buffer). Phase A 4 combo = $2, **超 buffer**. 选项:
- 等下月 1 号 reset 后启动 Phase A (推迟 9 天, 但本月可做 doc/audit 准备)
- 现在跑 Phase A subset (e.g. 2 combo $1.00, 留 $0.30 buffer)
- 现在跑 Phase A 1 combo (0.7/0.3 most promising) + Phase B subset

**推荐**: 现在跑 Phase A subset = (std=0.7, neg=0.3) 1 combo (~$0.50, 留 $0.80 月底 buffer); 看效果决定 Phase B/C 启动 time.

## 跟 BestChoice Phase 1 关系

- 当前 verdict warn_only_proxy, BestChoice Phase 1 **不该启动** (handoff §5 路径 α 需 PASS)
- Phase A 若给 PASS verdict → 走 plan §5 promote → 然后 BestChoice Phase 1
- 不在 Phase A 跑完前提前启 BestChoice

## v1 vs v2 对比

v1 (doc 推断): 8 方向, top 3 是 multi-horizon label / 减持 windowing / factor decay
v2 (实证驱动): 3 方向, top 1 是 stability penalty sweep / portfolio-objective / regime-conditional

v1 推断 ROI 高的方向 (加 features / 加数据) 在实证中**多触发 leakage 或边际小**. v2 选实证显著的方向 (改 objective, 不动 features) 复制本轮 stability retrain 思路.
