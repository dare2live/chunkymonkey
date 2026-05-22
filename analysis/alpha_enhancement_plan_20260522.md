# Alpha Enhancement Plan — 2026-05-22

> 触发: 2026-05-22 09:42 用户 push back "其他可以增强 alpha 的也可以计划着增加" + verdict warn_only_proxy 不该兴奋 (回测 +71.9% 触发 relative +50% 异常线).
>
> 设计原则:
> - **不依赖 GCP retrain** 启动 (cost 91.4% YELLOW); plan 阶段不耗 GCP
> - **真金白银** (CLAUDE.md §1.4): 每个方向必经 PIT audit + leakage check + walk-forward OOS, 不直接合并
> - **互补 alpha** (用户原话): 跟现 v4 panel + 新 stability model 互补, 不重叠
> - **每方向独立 challenger**, 不动 champion. 跑 paper_sim + Phase4 gate 后才决定 promote
> - **奥卡姆**: 不一次加多 feature, 一个一个 verify

## 数据 ready 状况 (2026-05-22 实测)

| 数据源 | rows | PIT 状态 | 用法 |
|---|---|---|---|
| `fact_shareholder_plan` 股东减持计划 | 8,012 | [OK] PIT-fixed (announce_date NULL 已 fix, commit 69371838) | 减持公告 windowing alpha |
| `fact_hsgt_daily` 北上 | 2,767 | ⚠ 卡 2024-08-16, [HOLD] 复杂度上调 | (HOLD, 待 sync 修) |
| `fact_dzjy_event` 大宗 | 548 | ⚠ 仅 4 天 | (HOLD, 待累积) |
| `fact_lhb_event` 龙虎榜 | 53,481 | [OK] (用户原话: 数据基础已就位) | 机构 / 游资 席位 windowing |
| `fact_capital_flow_pit_daily` 资金流 | 875,349 | [OK] 已 PIT | 多滞后 features (1d/3d/5d/10d) |
| `mart_market_perception_*` Perception 7 mart | varies | [OK] PIT (observed_snapshot) | regime / emotion / theme / under_reaction context features |

## Top 8 Alpha 增强方向 — ROI 排序

按 (alpha 信息领先性 × 数据 ready × 跟现 v4 panel 互补性 × 实施复杂度) 排:

| # | 方向 | 数据 | 复杂度 | 互补性 | 风险 |
|---|---|---|---|---|---|
| **1** | **Multi-horizon label engineering** (fwd_5d/10d 加现有 fwd_20d) | 无新数据, 现 panel `fwd_cost_after_5d/10d/20d` cols 已存在 | 低 — 重 train 加 multi-label objective | 中 (catches short-term reversal) | 低 — 现有数据, 无 leakage 路径新增 |
| **2** | **Factor decay timing** (lagged features 1d/3d/5d, 检测信号 decay 节奏) | 无新数据 | 低-中 — feature engineering + retrain | 高 (现 panel snapshot, 加时序 lag) | 低 |
| **3** | **股东减持公告 windowing** (announce_date ±10d window, 减持 = bear, 增持 = bull) | `fact_shareholder_plan` 8K rows ready | 中 — panel JOIN + PIT-strict (announce_date <= signal_date) | 高 (公告事件 alpha, v4 panel 无此) | 中 — announce_date 历史 NULL 47% fix 后稳, 但仍要 PIT audit |
| **4** | **LHB 席位 windowing** (机构席位 / 游资席位 上榜 ±5d, 持续上榜 = 强信号) | `fact_lhb_event` 53K rows | 中-高 — 席位类型分组 + windowing + PIT | 高 (席位流向 alpha, v4 简化版) | 中 |
| **5** | **Capital flow 多滞后** (1d/3d/5d/10d lagged, 资金路径节奏) | `fact_capital_flow_pit_daily` 875K rows | 低-中 — feature add | 中 (v4 已 1d, 加多 lag) | 低 |
| **6** | **Perception regime/emotion 接入 panel** (P1/P2 已 373 dates, 长期 coverage) | `mart_market_perception_emotion_daily` 373 dates | 中 — feature panel JOIN 物理边界 (Perception sibling repo, 用户多次重申不破) | 高 (regime context, v4 没) | **高** — 破物理边界风险, 需 careful |
| 7 | **Sector rotation lead-lag** (Perception P6 StyleRotation 输出) | P6 mart 14 dates, 长期 coverage 不足 | 高 — 数据不足 | 高 | 高 (Perception 边界) |
| 8 | **跨 horizon label diff** (fwd_20d - fwd_5d = trend persistence signal) | 现 panel | 低 — feature derived | 中 | 低 |

## Path 1 — 推荐立即启动 (低风险高 ROI)

**Multi-horizon label engineering** (方向 #1):

- **Why**: 现 stability model 只用 `fwd_cost_after_20d` 单 label. Panel 已含 `fwd_cost_after_5d` / `fwd_cost_after_10d`. 加 multi-label objective (e.g. weighted avg 或 multi-task LambdaMART) 几乎 zero data risk.
- **数据**: 已就位 (本地 panel 已有 3 horizons)
- **实施**: 改 `run_p0b_lambdamart_v6.py --label fwd_cost_after_5d` 等同 multi-horizon, 跑 walk-forward retrain. **本地慢** (full data + 34 windows), 需 GCP 跑 ~1.5h
- **风险**: 无新 leakage 路径; 现 panel 已 PIT-strict
- **ETA**: GCP retrain ~1.5h, 加 paper_sim + Phase4 gate ~30 min, 总 ~2h
- **Cost**: ~$0.50 GCP spot (n2-standard-32 1.5h × $0.376/h)

## Path 2 — 计划阶段 (data PIT audit 先做)

**股东减持公告 windowing** (方向 #3):

- **Why**: 公告事件 alpha 在 A 股市场极强 (减持公告后 5-15 天显著 underperform; 增持公告 outperform). v4 panel 没此 feature.
- **数据**: `fact_shareholder_plan` 8K rows, PIT-fixed (commit 69371838).
- **PIT audit 先**: 验证 announce_date <= signal_date for all rows, COALESCE(announce_date, '1900-01-01') 防 NULL leak. 跑 `/pit-audit` skill 5 步.
- **Feature design**:
  - `days_since_last_holder_plan` (减持公告距今)
  - `recent_holder_plan_event_count_30d` (近 30 天减持公告数)
  - `recent_holder_plan_event_type_share` (减持 / 增持比例)
- **跟 panel 集成**: 加到 backend feature panel build 路径, 加 3 cols
- **ETA**: PIT audit ~30 min, feature implement ~2h, GCP retrain ~1.5h, total ~4h
- **Cost**: GCP ~$0.50

## Path 3 — 中长期 (需 evidence)

**Factor decay timing** (方向 #2):

- **Why**: 现 features 是 snapshot, 不抓时序 decay. 加 lagged 看 alpha 半衰期, 选择性放大长 alpha / 缩短短 alpha.
- **数据**: 现有 panel, 加 lagged versions
- **实施复杂**: feature engineering needs domain understanding + 多 model objective
- **暂列**: 等 Path 1 + 2 验证后再启

## Path 4 — Perception 边界 (谨慎)

**Perception regime/emotion 接入 panel** (方向 #6):

- **物理边界硬约束** (用户 4 次重申): Perception sibling repo, 不接主项目 panel.
- **此方向破约束**, 不该启动. 除非用户明确改约束.
- **替代**: 等用户 verdict 是否破约束; 当前列入 plan 但 mark "NOT ACTIVATED".

## Path 5+ — Backlog

- LHB 席位 windowing (方向 #4): 数据大 (53K rows), 实施中等. 验证 Path 1+2 后启动.
- Capital flow 多滞后 (方向 #5): feature add 简单, 但跟 v4 重叠中. 验证 Path 1+2 后再加.
- 跨 horizon label diff (方向 #8): 等 Path 1 多 horizon retrain 后顺手加.

## 推进顺序

1. **Phase A** (本周): Path 1 multi-horizon label (GCP retrain, ~2h, ~$0.50)
2. **Phase B** (下周): Path 2 股东减持 windowing (PIT audit + panel + GCP retrain, ~4h, ~$0.50)
3. **Phase C** (评估): Path 3 factor decay (本地 verify alpha decay 节奏 first, 再决定 retrain)
4. **Backlog**: Path 4-5

每个 phase 独立 challenger, 不动 champion. 走 plan §5: post_retrain_pipeline → paper_sim + Phase4 gate → 看 verdict → BestChoice import 探索互补.

## 跟 BestChoice Phase 1 关系

- BestChoice Phase 1 (handoff goal.md Stage 2 路径 α 写) = main project verdict PASS 后启
- 当前 verdict warn_only_proxy, BestChoice **不该 Phase 1 启动** (read-only challenger 也不该, 因 BestChoice 没经 Phase4 gate, 直接 challenger 是误导信号)
- Alpha enhancement Phase A/B 先验证 main project alpha 增强, 走 plan §5 后再决定 BestChoice

## 当前 commit

doc-only plan. 不动 code / data. 跟当前 verdict warn_only_proxy + alpha 验证完成的状态对齐.
