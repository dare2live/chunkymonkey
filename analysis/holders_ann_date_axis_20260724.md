# 股东户数 / 十大流通 — 公告轴 vs 报告期轴（2026-07-24）

> Status: evidence-only · Label: **AUDIT FIXED** + holders notice-hole catchup **SHIPPED**  
> Owner challenge: 上市公司不只跟年报发十大流通/股东户数，平时也发公告。

## 1. Occam 裁决

| 问 | 答 |
|---|---|
| 业主判断合理吗？ | **合理。** 披露是公告驱动；报告期只是 end/report 标签。 |
| `stk_holdernumber` 用 `by_ann_date` 对吗？ | **对。** TinyShare LIVE：`ann_date` 截面有效；季中日有行；同 `end_date` 可多 `ann_date`。 |
| `holders_top10` 是否仍是 `by_ts_code`+`by_report_period`？ | **否（前提过时）。** tushare `top10_floatholders` 已退役；主源 = 东财 aif10，水位 = canonical `notice_date`（=`UPDATE_DATE`）。 |
| report_period-only 错在哪？ | 水位/`MAX(report)` 前进会**漏季中权益变动公告**；tushare top10 曾实测落后约一季（见 `miaoxiang_aif10_source_decision_20260624.md`）。 |
| 还漏公告吗？ | **曾漏 formal 洞：** `MAX(notice_date)` 前进后，中间稀疏 notice 分区未 land；fact 有、canonical 无。本刀补 catchup。 |

## 2. Vendor 探针（TinyShare / aif10，2026-07-24）

### `stk_holdernumber`

| 查询轴 | 结果 |
|---|---|
| `ann_date=20250429` | 2508 行（全市场截面） |
| `ann_date=20250515` / `20250610` | 72 / 58 行（**季中公告日**） |
| `enddate=20250331` | 3000 行（触页顶；同 end 多 ann） |
| `trade_date=…` | **不可靠**（参数似被忽略，返回无关近日数据） |
| `ts_code=600519.SH` | 同 `end_date=20250331` 有 `ann_date` 20250403 **与** 20250430 |

本地 raw：`raw_tushare_stk_holdernumber` 在 2025-05..06 有 **51** 个 distinct `ann_date`；`end_date=20250331` 有 **30** 个 distinct `ann_date`。

### `top10_floatholders`（tushare，非主源）

支持 `ann_date` / `period` / `ts_code`，但项目已切 aif10（季中事件覆盖更全）。

### aif10 `RPT_F10_EH_FREEHOLDERS`

| `UPDATE_DATE` | rows / stocks | 备注 |
|---|---|---|
| 20260613 | 60 / 6 | 含 **600388**，`report_date=20260608`（季中，非季末） |
| 20260701 | 40 / 4 | 稀疏公告日 |
| 20260515 | 100 / 10 | 季中 |

## 3. 项目同步前沿（改前）

| 域 | 前沿 | daily 行为 |
|---|---|---|
| `stk_holdernumber` | `by_ann_date` + `MAX(ann_date)` | registry `--all-due` drain / incremental_fallback |
| `holders_top10` | canonical `MAX(notice_date)` + affected `UPDATE_DATE≥wm−7d` per-code | `acquire` → `sync_holders_aif10_incremental` |

**漏洞机制（实测）**：wm=`MAX(notice_date)` 可被其它股推高；wm−7d 窗外的季中 notice 不再进入 affected；`formal_only` 后 fact 不再被新 sync 刷新，但历史 fact 仍持有这些分区 → canonical 空洞。

样本：600388 `notice=20260613` / `report=20260608` **在 fact、不在 canonical**。自 20260501 起 fact 有、canonical 无的 notice 分区约 **33** 个（含 20260613、20260701）。

## 4. 本刀改动（最小）

文件：`backend/services/holders_notice_catchup.py`（洞修复）+ `holders_aif10.py`（增量接线；re-export）+ `tests/test_holders_aif10.py`

1. **每次** `sync_holders_aif10_incremental`：`catchup_missing_holders_notice_partitions` — 从 **local fact** accept 缺失 notice 分区（≤40/跑；无 API mass）。
2. provider 领先时：`land_holders_notice_partitions_forward` — 按日 `UPDATE_DATE` 全市场截面 land（~10–120 行/日；空日 skip；≤40 分区）。
3. 保留原 same-day sparse / per-stock 修订路径。

禁：org mass / by_ts_code 全宇宙日扫 / Optuna / invent。

## 5. 验证

```bash
cd backend && pytest tests/test_holders_aif10.py -q
```
