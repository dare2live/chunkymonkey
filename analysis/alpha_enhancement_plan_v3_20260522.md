# Alpha Enhancement Plan v3 — 2026-05-22 (true verdict driven)

> v1 (09:55) doc-driven, drop.
> v2 (10:10) evidence from stability vs old model, drop most candidates: top 3 = stability sweep / portfolio-objective / regime-conditional.
> v3 (11:15) **true train-log Phase4 verdict BLOCK** (IS-OOS drop 92.43%) → drop stability sweep, refocus on **OOS RankIC collapse 根因**.

## 新增实证 (vs v2)

| Test | Old model `lgbm_phase5_gcp_20260520T010718` | New stability model `lgbm_phase5_stability_20260521T055800Z` | 同病 |
|---|---|---|---|
| PBO | 0.626 FAIL | 0.102 PASS | stability 帮 PBO |
| IS RankIC | 0.0195 (proxy) / 0.119 (true) | 0.034 (proxy) / **0.114 (true)** | IS 都强 |
| OOS RankIC | 0.0063 (proxy) / 0.022 (true) | 0.034 (proxy) / **0.0086 (true)** | **OOS 都 near 0** |
| IS-OOS true relative_drop | 81.4% FAIL | **92.4% FAIL** | 同 BLOCK |
| paper_sim Sharpe | 1.32 | 2.09 | stability 改善 ranking 稳定 |
| paper_sim ann | +43.7% | +71.9% | stability 帮 portfolio |
| Verdict (true train-log) | block | **block** | 两 model 同 FAIL IS-OOS gate |

**核心发现**: stability penalty 改 PBO + paper_sim ranking + 稳定 portfolio, **不解 OOS RankIC collapse**. 两 model 都是"top-K 偶然 good, 全 universe ranking OOS 失效".

## 根因 hypothesis (待 v3 验证)

| Hypothesis | 测法 | 优先级 |
|---|---|---|
| **H1: feature panel 过 spec, model 记 noise** | feature ablation: drop 一组 features 看 OOS RankIC up/down | **#1 高** |
| **H2: label `fwd_cost_after_20d` 单 horizon 过窄, model 学 forward bias** | multi-horizon multi-task (fwd_5d/10d/20d joint) | **#2 中** |
| **H3: walk-forward expanding_monthly 切分不够 OOS** | 改 sliding_window 12mo train / 1mo OOS, 或 purge + embargo 加宽 | #3 中 |
| **H4: train 区间含 regime shift, model 记 regime artifact** | regime-conditional + 各 regime 单独 train + 加权 ensemble | #4 中 |
| **H5: LightGBM 算法对 ranking task 过拟合, 换 XGBoost / 线性模型 baseline** | switch model class | #5 低 |
| **H6: label leakage in panel** | 全 panel PIT audit (重新跑 [[pit-audit]] 5 步) | #6 中 (sanity) |

## v3 推进顺序

### Phase A (新 #1): Feature ablation 找 noise 组

1. **本地 quick**: load mart_p0a_feature_label_panel_v4, group features by category (alpha158, instflow, sniper, etc), drop one group, fit LightGBM, eval OOS RankIC
2. 找出 drop 哪组 → OOS RankIC up (说明该组 add noise)
3. **不耗 GCP**, 全 local
4. ETA: ~2-3h (load panel, train 6-10 ablation, eval)
5. Cost: 0 GCP

### Phase B (v2 priority): Portfolio-objective replace NDCG

直接 optimize Sharpe / max_dd / Calmar in Optuna, 不优 NDCG ranking metric.
- 改 `run_p0b_lambdamart_v6.py` Optuna objective
- GCP retrain 1 × 50-trial with 1×32 thread + n_est=100 (plan C config 验证可 fit preempt cycle)
- ETA: ~12h GCP (50 trial × 15min); cost ~$2 (over budget, defer or do subset)

### Phase C (v2 hold): Regime-conditional

- 加 regime feature + 训练 3 sub-models (bull/sideways/bear)
- 配合 H4

### Phase D (新 sanity): Full PIT audit

- 跑 `/pit-audit` 5 步 on panel build + JOIN paths
- 防 hidden leakage (H6)
- 本地, ~1h

### BestChoice Phase 3 (并行)

- adapter 已就位 (commit 30b4511c)
- paper_sim 跑中 (b6tbiw052)
- 看 BestChoice candidates 作 challenger 是否互补
- 不依赖 stability model

## 月预算考虑

| Cost so far | $9.6 |
| Projected month | ~$14 (~93%) |
| Remaining buffer | ~$1.0 |
| Phase A local | $0 ✓ |
| Phase B 50-trial GCP plan C config | $2 (超 buffer, 等 reset 或 subset) |
| Phase D local | $0 ✓ |

**推进顺序优化** (cost 约束):
1. **Phase A 立即跑** (local, 0 cost) — find noise feature group
2. **Phase D 并行** (local, 0 cost) — PIT audit sanity
3. **Phase B 等下月 reset** (6/1 后 $15 reset, 跑 50-trial portfolio-objective)
4. **BestChoice Phase 3** 跑中, 看 KPI 后续

## 跟 BestChoice 关系

BestChoice candidates 是另一 alpha source (formula-triggered, 不依赖 ML ranking). 它的 paper_sim KPI 出来后:
- 若 BestChoice Sharpe ≥ 1.3 / ann ≥ 30% / dd ≥ -20% / win ≥ 55% → BestChoice 是 viable challenger
- 若 BestChoice 也 OOS 失效 → 同样问题, alpha source 整体不稳

但 BestChoice 不该直接当 champion — plan §5 Phase 4 complementarity check 是关键: BestChoice 跟 ML model 互补 or 重叠?

## 删 / drop 的方向 (v2 → v3 调整)

| v2 方向 | v3 决定 | 原因 |
|---|---|---|
| Phase A stability penalty sweep | **drop** | 已证不解 IS-OOS overfit (新 stability model true drop 92.43% FAIL) |
| Phase B portfolio-objective | **keep, hold to next month** | 仍 worth try, 但等 budget reset |
| Phase C regime-conditional | **keep, hold to next month** | 同 |
| Multi-horizon label | **upgrade to v3 high** | hypothesis H2 |
| Feature ablation | **NEW #1** | hypothesis H1 |
| 减持/LHB/capital flow lag | drop (v2 已 drop, v3 confirm) | 加 features 增 noise risk |
| Perception 接 panel | drop (v2 已 drop) | 破物理边界 |

## 时机

- Phase A + D 本地立即可跑 (今晚 / 明天)
- Phase B + C 等月底 / 6/1 budget reset
- BestChoice Phase 3 paper_sim 跑中 (b6tbiw052), 完后看 KPI

## 总结

stability retrain 是 useful experiment (stability penalty improves PBO + paper_sim portfolio metrics) **but does NOT fix the OOS RankIC collapse root cause**. v3 plan refocuses on:
1. **Find OOS RankIC collapse 根因** (feature ablation + multi-horizon label + PIT audit) — local, immediate
2. **Try portfolio-objective + regime-conditional** when budget reset
3. **BestChoice alt alpha source** parallel run for互补
