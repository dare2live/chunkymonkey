# 数据轴 / 频率 / 表粒评审（2026-07-24）

> Status: evidence-only · Label: **AUDIT FIXED**（无新 class-A 错轴；holders/holdernumber 已 ship）  
> Kids: grok · Live DuckDB：`smartmoney` / `tushare_raw` / `market` · Registry：`backend/config/sync_registry.yaml`  
> 前置：`analysis/holders_ann_date_axis_20260724.md` · `analysis/stk_holdernumber_retire_evidence_20260724.md`

## 0. Ship 确认（owner Q1）

| 刀 | Commit | 在 main？ | daily_update 接线 | Live 残差 |
|---|---|---|---|---|
| holders notice catchup | `542365446` | **YES**（HEAD） | `acquire._sync_holders_aif10` → `sync_holders_aif10_incremental` → **每次**先跑 `catchup_missing_holders_notice_partitions`（≤40 分区/跑）+ provider 领先时 `land_holders_notice_partitions_forward` | **代码已 ship；洞未 drain**：fact→canonical 仍缺 **1271** 个 notice 分区（含 600388/`20260613`）。`smartmoney` mtime≈07-23 23:09，catchup commit=07-24 09:10 → **下一轮 daily_update 才开始修** |
| holdernumber `by_ann_date` | `9bde17735` | **YES** | registry `stk_holdernumber`（无 `sync_policy=on_demand`）∈ `automatic_domains` → acquire `--all-due --drain`；DataAccess + dossier assist | 轴正确；`MAX(ann_date)=20260624`（SLA=90 交易日）— 是否 provider 无更新 vs drain 滞后 → backlog 探针 |

**Q1 结论**：代码路径 **无缺口（YES shipped + wired）**；holders 历史洞属 **ops drain 残差**，不是未接线。

---

## 1. Occam 总裁决

| 问 | 答 |
|---|---|
| 还有 class-A「错轴导致日常更新系统性漏公告」吗？ | **本轮未发现新的。** holders 公告轴漏洞已在 `542365446` 修接线；live 洞是 catchup 尚未跑完。 |
| 报告期轴能否替代公告轴？ | **不能**（holders / holdernumber / share_float / dividend 披露）。报告期是标签；可用性锚在 `notice_date`/`ann_date`。 |
| 本刀要不要再改 update-flow？ | **不必。** 无新错轴；残差进 FOUNDATION 有序 backlog。 |

---

## 2. Verdict 表（registry 轴 × 表粒 × live 异常）

图例：`OK` = 轴/频率/表匹配且无系统性错；`FIX` = 日常会再制造错误（class-A）；`DEFER` = 已知诚实滞后/历史洞/ops，不阻塞轴正确性。

| 域 | Registry 轴 | 实际表粒 | Live 样本异常 | Verdict |
|---|---|---|---|---|
| **holders_top10 / aif10** | 非 registry；acquire 专用；水位=`canonical.notice_date` | `fact_top10_holder_period` + `canonical_top10_float_holders_period`（grain≈ stock×report×holder×notice） | fact distinct `notice_date`=**1951**，其中 **非季末 1930**；`report_date` 非季末 **1529**。2025+ 季中例：`notice=20260717/report=20260714`（001233 等）；**600388** `notice=20260613` / `report=20260608` **在 fact、不在 canon**。fact-only notice=**1271**（catchup ≤40/跑） | **OK 轴** + **DEFER drain** |
| **stk_holdernumber** | `by_ann_date` / `ann_date` / wm=`MAX(ann_date)` | `raw_tushare_stk_holdernumber` grain=`[ts_code,end_date]`（多 ann 覆盖同 end） | distinct ann=**2518**（非季末 **2490**）；end 非季末常见（如 `20260610` 750 股 / `20260529` 849 股）。同 end 多 ann：`600519` `end=20250331` 至少 `ann=20250403`。`MAX(ann)=20260624` | **OK**（探针 DEFER） |
| **stk_holdertrade** | `by_ann_date` | raw + `canonical_stk_holdertrade_announcement` | `MAX(ann)=20260715`；事件稀疏合法 | **OK** |
| **daily / qfq** | daily=`by_trade_date`（formal on_demand）；qfq=derive | `canonical_nominal_ohlcv_daily`；`price_kline_qfq_tushare` | 2026-04..07-23 SSE open **77/77** 无洞；qfq max=`2026-07-23` 跟 accepted；raw_daily max=`20260716`（landing 可短滞后） | **OK** |
| **moneyflow** | `by_trade_date` | raw + `fact_stock_moneyflow_daily` | 2026 vs accepted：**133/133**；fact max=`20260720` < raw=`20260723`（Type-B publish 短滞后） | **OK** / publish **DEFER** |
| **moneyflow_hsgt** | `by_trade_date`；`gap_tolerance=hk_holidays` | grain=`trade_date` | 2026 miss **4** 日=`20260403/0407/0525/0701` = registry `known_empty_days` | **OK** |
| **margin** | on_demand catchup（非 all-due mass）；product UNTRUSTED | raw + `canonical_margin_exchange_daily` | raw max=`20260716`；Jun+ canon miss 含 `20260709/10/13/14/23`（已知 class-B） | **DEFER**（诚实 UNTRUSTED，非错轴） |
| **limit_list_d / stk_limit** | `by_trade_date` | raw + `fact_stock_limit_daily` | limit 日覆盖 2026 满；fact max=`20260720` < raw=`20260723` | **OK** / publish **DEFER** |
| **index_daily** | `index_daily_benchmark`=`by_code_list` | raw + `fact_index_daily` | 基准码 2026 满（`000852` 停在 `20260701` 需核对清单）；fact max=`20260716` < raw=`20260723` | **OK** / fact lag **DEFER** |
| **org_holding** | period incremental（禁 mass / by-date invent） | raw + `canonical_org_holding_detail_period` | 仅 **3** 期：`20190331` / `20251231` / `20260331`；中间季洞=log-not-fill | **OK 契约** / 历史洞 **DEFER** |
| **dividend** | `by_trade_date` + `date_param=ex_date`（pit_anchor 明示特征用 `ann_date`） | grain=`[ts_code,end_date,div_proc]` | ann 非季末 **2742/2772**；周末 ann 常见；ex 全在交易日；2024+ null ex=**0** | **OK**（消费 PIT 必须 `ann_date`，勿用 ex 当披露锚） |
| **share_float** | `by_ann_date` | grain 含 ann/float_date | ann 非季末 **2091/2113**；周末 ann 有量（如 `20260718` 10103 行）— 证明改 ann 轴正确 | **OK** |
| **cyq_perf** | `by_trade_date` | grain=`[ts_code,trade_date]` | 2026 vs daily **133/133**；C0 口径审计历史 FAIL（消费禁与 qfq 直混） | **OK 轴** / 语义 **DEFER**（已 annotate） |
| **income/balancesheet/fina_indicator** | `by_ts_code` + `increment_mode=by_report_period` | 财报期粒 | API 强制 ts_code；增量按报告期 — 与披露事件域不同族 | **OK**（勿改 ann） |
| **forecast / report_rc / stk_surv / ths_hot** | `by_ann_date`（历史 from trade_cal 结构性漏周末已修） | 各 raw | 轴已按公告/全日历 | **OK** |

---

## 3. 非财报周期十大股东 — 实测例子

> 证明：十大流通更新不是「只在季末报告期」。

| stock | notice_date | report_date | 是否季末报告期 | 平面 |
|---|---|---|---|---|
| **600388** | 20260613 | 20260608 | **否**（季中权益变动） | fact 有 / **canon 无**（待 catchup） |
| 001233 | 20260717 | 20260714 | 否 | fact |
| 002020 | 20260717 | 20260715 | 否 | fact |
| 300567 | 20260716 | 20260708 | 否 | fact |
| 601369 | 20260711 | 20260626 | 否 | fact |

汇总（`fact_top10_holder_period`）：

- distinct `notice_date`：**1951**（季末日历日仅 21）
- distinct 非季末 `report_date`：**1529**
- 2025-05-01+：fact notice 309 vs canon 197 → **fact_only 117**（全史 fact_only **1271**）

股东户数同构（`raw_tushare_stk_holdernumber`）：

- distinct `ann_date`：**2518**（非季末 **2490**）
- 非季末 `end_date` 截面：`20260610`→750 股；`20260529`→849 股
- `600519` 同 `end_date=20250331` 可多 `ann_date`（至少 20250403）

---

## 4. 轴错配检查清单（by_*）

| 模式 | 适用 | 反例 / 已修 |
|---|---|---|
| `by_trade_date` | 日频市场（K/资金/涨跌停/筹码日） | 曾误用于 `report_rc`/`forecast`/`share_float`/`stk_surv`/`ths_hot` → 周末结构性漏 → 已改 `by_ann_date` |
| `by_ann_date` | 披露/事件（户数/增减持/解禁/研报日） | holdernumber 2026-07-24 RESTORE；禁改回 `by_ts_code` mass |
| `by_report_period` + `by_ts_code` | 财务报表三表 | API 要 ts_code；与 holders 公告轴不同 |
| `by_code_list` | 指数/板块清单 | `index_daily_benchmark` |
| period incremental | org_holding | 禁每次 mass ~830k |
| notice catchup | holders_top10 | `MAX(notice)` 跃进会漏中间稀疏分区 — **已接线**，待 drain |

**dividend 特例**：同步参数用 `ex_date`（除权生效日，交易日历合法）；**特征 PIT 必须用 `ann_date`**（registry `pit_anchor` 已写死）。不是错轴，是双日期语义。

---

## 5. 有序 backlog（无本刀代码；进 FOUNDATION）

1. **P0 ops**：跑 `daily_update`（或等价 acquire）直到 holders notice catchup 清空 fact-only（≈1271/40 ≈ 32 跑；可另开 bounded one-shot knife 提速，仍禁 by_ts_code mass）。验收：`600388/20260613` 进 canonical + accepted。
2. **P1 探针**：TinyShare `stk_holdernumber` 是否存在 `ann_date>20260624`；有则查 drain/wm；无则标稀疏 OK。
3. **P2 Type-B publish**：`fact_stock_moneyflow_daily` / `fact_stock_limit_daily` / `fact_index_daily` 相对 raw 短滞后（1–3 交易日）— 跟 serve→accepted 闭环，不改轴。
4. **P2 margin**：继续 class-B UNTRUSTED + bounded catchup；禁洗绿 READY。
5. **P3 org**：中间历史季洞仅显式 backfill 刀；日常保持 incremental-check-every-run。
6. **P3 cyq**：消费前口径门（历史 C0 FAIL）— 语义债，非采集轴。

---

## 6. 验证命令（只读）

```bash
# ship
git merge-base --is-ancestor 542365446 main && git merge-base --is-ancestor 9bde17735 main

# holders 非季末 notice + 洞
python3 - <<'PY'
import duckdb
sm=duckdb.connect('data/smartmoney.duckdb', read_only=True)
print(sm.execute("""
WITH nd AS (SELECT DISTINCT notice_date d FROM fact_top10_holder_period)
SELECT COUNT(*) n,
  SUM(CASE WHEN substr(d,5,4) IN ('0331','0630','0930','1231') THEN 1 ELSE 0 END) qe,
  SUM(CASE WHEN substr(d,5,4) NOT IN ('0331','0630','0930','1231') THEN 1 ELSE 0 END) non_qe
FROM nd""").fetchone())
print(sm.execute("""
SELECT notice_date, report_date, COUNT(*) n FROM fact_top10_holder_period
WHERE stock_code='600388' AND notice_date='20260613' GROUP BY 1,2
""").fetchall())
print(sm.execute("""
SELECT COUNT(*) FROM canonical_top10_float_holders_period
WHERE stock_code='600388' AND notice_date='20260613'
""").fetchone())
PY
```

---

## 7. 标签

| 项 | 状态 |
|---|---|
| Q1 ship+wire | **YES**（live holders 洞 = drain 未跑，非未接线） |
| 新 class-A 错轴 | **无** → 本刀不改 update-flow |
| 文档 | 本文件 + FOUNDATION backlog 指针 |
| 下一步验证 | 下一次 `daily_update` 后复查 fact-only 计数与 600388 |
