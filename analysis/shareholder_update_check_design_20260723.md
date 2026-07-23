# 股东 / 机构持仓 — 更新检查机制审计与 Occam 裁决（2026-07-23）

> Status: evidence-only / design（**未开代码刀**；analysis 层证据+裁决落档，不拥有 live control-plane）
> Label: **AUDIT FIXED** · 全市场逐公司扫公告 = **BANNED for daily** · holders notice 稀疏路径 = **ALREADY SHIPPED**
> Multi-model: composer-2.5-fast FOR ([21c1545c](21c1545c-495c-4fc4-abc2-b0c69d2df7f8)) / AGAINST ([38c1e210](38c1e210-1926-40b5-8698-0794185db258))
> Related: `org_holding_incremental_loop_20260723.md` · CX-2 `state_sensors`

## 0. 机构持股 vs 十大流通 — 源与策略（业主追问 2026-07-23）

| 问 | 答 |
|---|---|
| **1. 机构持股真相源** | **不是** tushare 专用字段；**不是**从十大流通解析加工。Provider = 东财妙想 aif10 `RPT_MAIN_ORGHOLDDETAIL`（`SOURCE=miaoxiang`）。Writer = `services.org_holding_aif10` → compat `raw_org_holding_aif10`；formal = `landing_miaoxiang_org_holding` → `canonical_org_holding_detail_period`（dataset `tier0.disclosure.org_holding_detail_period`）。代码自评：tushare **无**等价机构持仓明细（aif10 §4.3 例外）。 |
| **2. 与十大流通关系** | **独立双域**，无 derive-from-top10。十大 = `RPT_F10_EH_FREEHOLDERS` → `holders_aif10` / `fact_top10_holder_period` + formal `landing_miaoxiang_holders_top10` / `canonical_top10_float_holders_period`。tushare `top10_floatholders` 已退役（registry 注释）。档案/传感器可**并列消费**两域，但不把 org 当 top10 加工产物。 |
| **3. 能否复用 notice_date 前沿+稀疏？** | **不能。** org API 轴 = **by-period**（`REPORT_DATE` 全市场 ~830k/期），**无 `NOTICE_DATE`/`UPDATE_DATE`**；by-date land invent **banned**（F7）。holders 稀疏依赖供应商廉价 `UPDATE_DATE≥` 筛码 — org 没有同形 faucet。 |
| **4. 各域合适策略** | **holders**：`MAX(notice_date)` watermark → 1-row provider probe → `UPDATE_DATE≥wm−7d` 受影响股 per-code 幂等覆盖（已 ship）。**org**：每次 `org_holding_period_gap_report`（latest plannable vs raw+accepted）→ 缺则**只拉一期** / 有则 skip；期内晚披露 / 偏少行 = 显式 repair 刀，不进 daily；禁 mass refresh。 |

## 1. 业主问题

是否应检查股东数据的更新机制？`daily_update` 是否应**逐个公司**扫描对比最新公告？

对齐前提（不变）：

- org：报告期增量 OK（新 plannable → 拉一期）
- 禁：已落地期全市场 ~830k 盲刷
- 增减持/比例/退出 = **post-land sensors**，≠ mass re-pull
- 期内晚披露：期已存在则当前 skip

## 2. 现状审计（代码真相）

| 域 | 更新检查机制 | 粒度 | daily_update 行为 | 残差 |
|---|---|---|---|---|
| **holders_aif10 / holders_top10**（十大流通） | `formal_holders_watermark` = canonical `MAX(notice_date)`；`_provider_newest_update_date` 1-row probe；`_affected_stocks_since(UPDATE_DATE≥wm−7d)` → **仅 affected 股** per-stock 全期幂等覆盖 | **notice_date 前沿 + 稀疏 per-code**（非全宇宙逐股探） | `acquire._sync_holders_aif10` → `sync_holders_aif10_incremental`；`provider_max<wm` skip；`==wm` same-day sparse miss；`>wm` safety-window | 同日晚披露 **FIXED**（equal-wm miss probe）；rewrite 放大 ≠ 净新增（已分栏计数） |
| **holders formal land** | `fetch_holders_top10_by_notice_date` 全市场 by UPDATE_DATE（~10–120 行/日，非 mass） | by-notice partition | E0 / disclosure_transport；日常主路径仍是上列增量 | ≤40d；禁 mass dump |
| **org_holding** | `org_holding_period_gap_report`：latest plannable vs raw+accepted | **by-period 存在性** | 每次跑：缺→`fetch_then_accept` 一期；raw 有未 accept→local accept；都有→`skip_current`+next unlock | **期内晚披露 / 偏少行** 不自动重拉；无 NOTICE_DATE；by-date invent banned |
| **stk_holdertrade** | sync_registry `by_ann_date` + `MAX(ann_date)` watermark | 公告日全市场 | registry drain | ~71 行/日；多数日 0 合法 |
| **CX-2 sensors** | `detect_holders_state_changes`：最新 accepted notice vs prior grain | post-land 读 | ratio / rank / exit；**不写 Tier0** | 不能治愈「从未再 land」的源缺口 |

结论：**holders 已经在做「公告前沿 → 稀疏受影响代码」**；业主担心的「逐公司扫」若指 ~5k 全宇宙逐股探 API，**今天没有、也不该加**。org **只能**做期存在性检查——供应商是 by-period ~830k、无 NOTICE_DATE。

## 3. 要不要 daily 逐公司扫最新公告？

### Pros（FOR 摘要）

- 期存在性看不见期内晚披露 / 同 report_date 修正
- notice 前沿 + 只拉 frontier 前进的代码，可在无 mass 下补洞
- 传感器只读，治不了 Tier0 未再 land

### Cons（AGAINST 摘要 + 实测纠正）

- org API **没有**廉价 per-code 公告轴；「逐公司」易滑向已禁的 period refresh
- holders 主路径**已是**市场级 `UPDATE_DATE≥` 筛码 → 稀疏抓取；再叠一层全宇宙探是重复税
- 假绿风险：扫了 N 家 ≠ accepted population 完整；PIT/`available_at` 若跟公告可见性绑错会泄漏
- 成本：~5k round-trip ≫ 一次 by-notice 全日 / 一次 by-period 批

### Occam 裁决（现刻）

| 问题 | 裁决 |
|---|---|
| daily 全市场逐公司扫最新公告？ | **否** |
| holders 要不要再造 notice 稀疏 catchup？ | **否 — 已 ship**（`sync_holders_aif10_incremental`） |
| org 要不要期内 late-filer 自动补？ | **否 — 现刻**；缺测 miss ledger + 安全 API 前保持 period-gap + 显式 repair 刀 |
| 传感器角色 | 保持 post-land；不替代 acquire |

**实现刀**：**不开**。本文件 = 设计 + 审计落档。低风险「再加一层 daily check」会与现有 holders 路径重复，或逼 org 发明 by-date，**不 fail-closed-safe**。

## 4. 若将来要补洞：证据门（全测过再开刀）

1. **Miss ledger ≥90d**：period accept 之后、下一 plannable 之前，晚披露改了 rank/ratio/exit，且 **未被** holders notice 增量 / CX-2 / 下期 gap 捕获 — 按域计数
2. **Provider 证明**：org 若有 per-code 安全 faucet（非对 by-period 端点伪造 NOTICE_DATE），含配额 / p99 / 空批率
3. **决策影响**：机构档案 / screening / 验证结论真变，非仪表盘新鲜度虚荣
4. **PIT**：部分期内更新的 typed `available_at`；禁 pre-accept / pre-deadline 上浮
5. **成本天花板**：墙钟+API vs 一期 fetch；catchup 触发下 **硬禁** ~830k 全期 refresh

通过前：org 偏少行 / 晚披露 = **显式 repair 刀**（见 `org_holding_incremental_loop` §3），不进 daily loop。

## 5. 与已有裁决的关系

- goal.md / MASTER：org mass + by-date invent banned；每次更新 **检增量**（period）— 本裁决不回翻
- holders F6 / notice watermark ops counters — 保持
- CX-2 PASS — 保持只读传感

## 6. 验证（只读）

```bash
rg -n "sync_holders_aif10_incremental|org_holding_period_gap_report|_affected_stocks_since" \
  backend/services/holders_aif10.py backend/services/org_holding_aif10.py \
  backend/services/pipeline/acquire.py
```
