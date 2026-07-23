# Acceptance — unified frontier detection（2026-07-23）

> Status: evidence-only / acceptance
> Label: **FIXED（子集）**  
> Knife: cross-domain shared frontier primitive + `by_ann_date` equal-day reprobe  
> Authority: `analysis/data_frontier_detection_system_20260723.md` · holders `e040f4889` · owner 「做吧」

## What shipped

1. **Primitive** `backend/services/data_sources/frontier_decision.py`  
   Outcomes: `skip_behind | equal_day_population_gap | advance_window | pending_clock | hard_fail`.  
   Day policies: `atomic_skip` (dense trade_date) / `ann_reprobe` (sparse ann).  
   Occam: one compare helper — not DetectionService / plugin / DAG.

2. **Wired**
   - `holders_aif10.sync_holders_aif10_incremental` → `decide_frontier(notice_date)`
   - `sync_runner` `by_ann_date` → `ann_reprobe`（equal/advance **保留 wm 当天**）
   - `sync_runner` `by_trade_date` → `atomic_skip`（行为不变；经同一原语）
   - `org_holding_period_gap_report` → optional `org_holding_period_frontier_hook`（period 存在；禁 by-date invent）

3. **Tests**（blocking CI surface）  
   `test_frontier_decision.py` · `test_by_ann_date_equal_day_reprobe.py` · holders equal-day branches unchanged green.

4. **Mapping** updated in `data_frontier_detection_system_20260723.md`.

## Not in scope

- Mass org_holding / by-date invent  
- Optuna / north-star rewrite  
- by_trade_date equal-day population probe（仍 atomic_skip）  
- QFII / by_period 全量抽原语

## Residual

| Item | Owner |
|---|---|
| typed `availability_policy` 窄覆盖（时钟≠人口） | future knife |
| disclosure 旁路未并入同一 runner 契约 | future / optional |
| org 期内晚披露人口 | explicit repair knife only |
| by_trade_date equal-day if miss ledger proves non-atomic | evidence gate then knife |

## Parent note — ~54% `mart_inst_profile`

见本会话 return message 中文段：十大流通 distinct holders → profile mart 覆盖；episode ≠ profile；honesty 门已 FIXED，覆盖提升属独立产品/rebuild 轨。
