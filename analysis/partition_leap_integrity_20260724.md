# Partition tip-leap 完整性扫描 + 共享 catchup 法（2026-07-24）

> Status: evidence-only · Label: **PARTIAL**（扫描 + 共享原语 + holders/holdertrade 接线；Type-B publish 滞后另轨）  
> Owner challenge: watermark tip leap 不只 holders；要跨域完整性检查 + 根本解，非永久 per-domain bandage。  
> Related: `holders_ann_date_axis_20260724.md` · `data_frontier_detection_system_20260723.md` · `data_axis_frequency_review_20260724.md`

---

## 0. 一句话

**根因类**：水位用 `MAX(axis)` 前进时，稀疏中间分区可留在 source/fact，而 accepted/canonical 已把 tip 推过它们 → **洞在 tip 之下（集合差）**，不是 tip+1。  
**根本法**：共享 `plan_partition_catchup`：`due = (source\\accepted) ∪ (calendar\\accepted\\known_empty)` 且 `P≤watermark`，bound≤40；域模块只执行 accept/land。

---

## 1. Live 完整性（Pattern A / B）

| class | 含义 |
|---|---|
| **TRUE_LEAP** | source 有 P、accepted 无 P，且 P ≤ tip |
| **PUBLISH_LAG** | tip 滞后：raw 领先 fact（洞在 tip **之上**） |
| **EXPECTED_EMPTY / SPARSE** | 稀疏事件或 known_empty |
| **CONTRACT_*** | owner 契约允许（org log-not-fill） |

| 域 | 轴 | Live（2026-07-24） | class |
|---|---|---|---|
| **holders** | notice_date | fact_only 曾≈1271；catchup 后 CLEAN（含 600388/20260613） | **CLEAN**（原 TRUE_LEAP） |
| **stk_holdernumber** | ann_date | 无 accept 平面 | **N/A** |
| **stk_holdertrade** | ann_date | 共享 law 已接线；**全史 raw→canon raw_only=0** | **CLEAN** |
| **daily / moneyflow / limit / index** | trade_date | 稠密；残差多为 publish tip 短滞后 | **PUBLISH_LAG** / OK |
| **moneyflow_hsgt** | trade_date | known_empty 港休 | **EXPECTED_EMPTY** |
| **margin** | trade_date | bounded catchup；产品诚实门 | **DEFER** |
| **org_holding** | period | 中间季 = log-not-fill | **CONTRACT** |

**结论**：与 holders 同构的 tip-leap 主要落在 **披露稀疏轴 + 双平面**。日频稠密域残差以 Type-B publish 滞后为主。

---

## 2. 根本设计

```text
due = missing partitions among:
  A: source_partitions \ accepted_partitions   where P ≤ watermark
  B: calendar_partitions \ accepted \ known_empty  where P ≤ watermark
       (only when caller opts into dense calendar expectation)
bound ≤ 40
```

| 层 | 模块 | 职责 |
|---|---|---|
| Law | `frontier_decision.plan_partition_catchup` | 集合差 + tip 过滤 + bound |
| Execute | `holders_notice_catchup` / `stk_holdertrade_catchup` | 读集合 → law → accept |
| Wire | acquire holders 增量；`sync_runner` stk_holdertrade | 每次增量先修洞 |

---

## 3. 已落地

| 项 | 状态 |
|---|---|
| `plan_partition_catchup` | SHIPPED |
| holders catchup 迁到共享 law | SHIPPED |
| `stk_holdertrade_catchup` + sync_runner 接线 | SHIPPED |
| 单测 frontier + holdertrade + holders | targeted green |

---

## 4. 历史完整性回填（owner: 漏掉的都要）

| 域 | 动作 | Live |
|---|---|---|
| **stk_holdertrade** | 本地 raw→formal 全史 catchup（1982 分区 / +63449 行） | **raw_only=0**；canon 自 `20190102`；canon_only=6（formal 领先 raw，正常） |
| **holders** | 先前 tip-leap drain | **fact_only=0** |

日常增量仍 `newest_first`≤40；全史债用 `oldest_first` / 多轮 catchup 直到 raw_only=0。

## 5. 残差

- Type-B `fact_*` tip 滞后 → publish 闭环（另轨）
- org 中间季 → 显式 backfill 刀（契约 log-not-fill，非 tip-leap）
- 禁：org mass / by_ts_code 全宇宙日扫 / Continuity 洗绿 / Optuna
