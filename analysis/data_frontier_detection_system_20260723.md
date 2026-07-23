# 数据前沿检测系统映射（2026-07-23）

> Status: evidence-only / live mapping + shipped primitive  
> Label: **FIXED（子集）** — shared `frontier_decision` + holders sparse + `by_ann_date` ann_reprobe  
> Trigger: owner 「做吧」cross-domain unified frontier；holders fix `e040f4889`  
> Related: `holders_stock_coverage_alignment_20260723.md` · `shareholder_update_check_design_20260723.md` · `org_holding_incremental_loop_20260723.md` · acceptance `unified_frontier_detection_acceptance_20260723.md`

---

## 0. 一句话

业主统一模型是：`local max(axis) → 日历/披露规则 → 应有集合 → fetch gap`。  
代码原语：`backend/services/data_sources/frontier_decision.py`（typed outcomes，非 DetectionService / plugin / DAG）。

---

## 1. Shared primitive

| 项 | 值 |
|---|---|
| Module | `services.data_sources.frontier_decision` |
| Compare | `decide_frontier(axis, local_max, target_max, *, clock_pending, probe_failed)` |
| Day policy | `plan_incremental_days(..., policy=atomic_skip\|ann_reprobe)` |
| Org hook | `org_holding_period_frontier_hook` — period 存在性；equal → remap `skip_behind`（禁 by-date invent） |

Typed outcomes:

| Outcome | 含义 |
|---|---|
| `skip_behind` | target < local（provider/日历落后） |
| `equal_day_population_gap` | target == local → **日期相等 ≠ 人口完备** |
| `advance_window` | target > local（或 target unknown → 开窗） |
| `pending_clock` | 发布钟未开（caller 声明） |
| `hard_fail` | probe 失败 fail-closed |

Pattern 永远是 **存量最新日 + 应拉日**，不是 wall-clock「对昨天」。

---

## 2. `e040f4889` holders 路径（已接入 primitive）

| 条件 | `decide_frontier` | 行为 |
|---|---|---|
| `provider_max < wm` | `skip_behind` | skip `watermark_unchanged` |
| `provider_max == wm` | `equal_day_population_gap` | sparse miss codes only / `same_day_coverage_complete` |
| `provider_max > wm`（或 None） | `advance_window` | safety-window affected incremental |

调用链：`daily_update → run_acquire → _sync_holders_aif10 → sync_holders_aif10_incremental`。

---

## 3. 域映射表（updated）

### 3.1 Formal / acquire 专路径

| 域 | Frontier 轴 | daily_update | 检测形态 | Primitive | 残差 |
|---|---|---|---|---|---|
| **daily / stock_st** | `trade_date` + typed `availability_policy` | 是（formal on_demand catchup） | eligible day land→accept；0 行 typed pending | 日历 eligibility（非 wm-skip 人口门） | Continuity / 盘前 soft |
| **holders_aif10** | `notice_date` | 是 | local MAX + provider probe + equal sparse | **wired** `decide_frontier` | BSE out-of-dim |
| **org_holding** | `report_period`（plannable） | 是（period gap） | latest plannable 存在性 | **hook only**（equal≠population；禁 by-date） | 期内晚披露 = repair 刀 |
| **QFII** | 季度末披露水位 | 是 | 已有季则 skip | 未抽（非 notice 稀疏） | 非本刀 |

### 3.2 sync_registry（`--all-due` / `run_domain`）

| 形态 | 代表域 | Frontier | Primitive policy | 残差 |
|---|---|---|---|---|
| `by_ann_date`（6） | stk_holdertrade, forecast, report_rc, stk_surv, ths_hot*, share_float | `MAX(ann_date/…)` vs eligible calendar end | **`ann_reprobe`** — equal/advance **保留 wm 当天**全日批重拉 | 稠密日成本略增（可接受）；无 per-code sparse |
| `by_trade_date`（~26） | moneyflow, stk_limit, … | `MAX(trade_date)` vs eligible trading day | **`atomic_skip`** — 窗继续时仍跳 wm 当天 | 无 equal-day 人口 re-probe（稠密全日批假设） |
| `by_ts_code` + `by_report_period` | income, balancesheet, fina_indicator | 每股期完备 | 未抽 | 期内修正靠缺期重拉 |
| `by_period` | express | 报告期批 | 未抽 | 期轴 |
| typed `availability_policy` | daily, stock_st, margin | trading_day + clock | availability 层 | 时钟门 ≠ 人口门 |

\* ths_hot：ann_date/calendar + `ann_reprobe`；子榜 freshness 另有 `freshness_group_col`。

### 3.3 声明轴（不单独驱动）

| 源 | 轴 |
|---|---|
| `disclosure_boundaries` | holders=`notice_date`；org=`available_date`；stk_holdertrade=`ann_date` |
| `source_watermarks` / SLA | 观测 `last_data_date`；不做应有人口差 |

---

## 4. 对照业主模型 — 缺口状态

| # | 缺口 | 状态 |
|---|---|---|
| G1 | 无跨域「应有−实有」原语 | **FIXED（子集）** — `frontier_decision` |
| G2 | sync_runner 增量跳过 wm 当天（稀疏 ann） | **FIXED（by_ann_date）** — `ann_reprobe` |
| G3 | org 期内晚披露 | 已知裁决 — period-gap + repair；hook 不 invent |
| G4 | typed `availability_policy` 覆盖窄 | open（时钟 ≠ 人口） |
| G5 | disclosure 三域未进同一 runner 契约 | open（旁路 acquire 仍合法） |
| G6 | 「同日晚披露」语义名 | holders + by_ann_date 已对齐 equal-frontier 语义 |

**非缺口：** 点击更新不是「对昨天」；holders 同日补漏已在 acquire；禁 daily 全宇宙逐公司扫。

---

## 5. 验证命令

```bash
PYTHONPATH=backend python -m pytest \
  backend/tests/services/test_frontier_decision.py \
  backend/tests/test_by_ann_date_equal_day_reprobe.py \
  backend/tests/test_holders_aif10.py \
  -k "incremental_same_day or incremental_skips_when_provider or frontier or equal_day or ann_watermark or equal_frontier" -q

rg -n "decide_frontier|plan_incremental_days|ann_reprobe" \
  backend/services/data_sources/sync_runner.py \
  backend/services/holders_aif10.py \
  backend/services/org_holding_aif10.py
```

---

## 6. Label

**FIXED（子集）**  
- shared primitive + holders wired + all `by_ann_date` registry domains via sync_runner  
- residual owner = G4/G5 + by_trade_date equal-day（若未来 miss ledger 证伪原子假设）+ org 期内 repair  
- next verification = 下一交易日 canary：ann 域 wm 日重拉 + holders equal-wm sparse 无需 `--symbols`
