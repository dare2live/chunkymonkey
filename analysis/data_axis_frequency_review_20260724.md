# 数据轴 / 频率 / 表粒评审（2026-07-24）

> Status: evidence-only · Label: **AUDIT FIXED** + **A1/A2 ops drain FIXED**  
> Kids: grok · Live DuckDB：`smartmoney` / `tushare_raw` / `market` · Registry：`backend/config/sync_registry.yaml`  
> 前置：`analysis/holders_ann_date_axis_20260724.md` · `analysis/stk_holdernumber_retire_evidence_20260724.md`

## 0. Ship 确认（owner Q1）

| 刀 | Commit | 在 main？ | daily_update 接线 | Live 残差 |
|---|---|---|---|---|
| holders notice catchup | `542365446` | **YES** | `acquire._sync_holders_aif10` → `sync_holders_aif10_incremental` → **每次**先跑 `catchup_missing_holders_notice_partitions`（≤40 分区/跑）+ provider 领先时 `land_holders_notice_partitions_forward` | **drain 完成**：fact→canonical notice 洞 **1271→0**；`600388`/`20260613` **canonical 12 行** |
| holdernumber `by_ann_date` | `9bde17735` | **YES** | registry `stk_holdernumber` ∈ `automatic_domains` → acquire `--all-due --drain`；DataAccess + dossier assist | **drain lag（非源端停更）**：TinyShare `ann_date∈(20260625..20260723]` 有 27 个非空日；已 `chunkyctl sync --backfill --start 20260625 --end 20260723` → live `MAX(ann_date)=20260723`（+2752 行） |

**Q1 结论**：代码路径 **无缺口（YES shipped + wired）**；holders 历史洞已 ops drain；catchup 曾被 `DUPLICATE_GRAIN`（legacy `row_seq=1`）卡住 → 本刀补 `assign_unique_holders_row_seq` 于 `accept_holders_top10_partition_from_legacy`（与 `disclosure_transport` from-local-raw 对齐）。

---

## 0b. A1/A2 ops drain 证据（2026-07-24 grok follow-up）

### A1 holders notice holes

| 项 | 值 |
|---|---|
| before fact-only notice partitions | **1271** |
| after | **0** |
| `600388`/`20260613` | fact 有 → **canonical 12 行**（`report_date=20260608`） |
| 路径 | 反复 `sync_holders_aif10_incremental`（wired catchup ≤40/跑；非 by_ts_code mass） |
| 卡点 | pass1：newest-first 队列被 `formal_accept_rejected`/`DUPLICATE_GRAIN` 占满（≈1158 残留停） |
| 代码修 | `accept_holders_top10_partition_from_legacy` 调 `assign_unique_holders_row_seq` |
| pass2 | 修复后 29 跑清空（errors=0） |
| 日志 | `/tmp/holders_notice_catchup_drain_20260724.jsonl` + `_pass2.jsonl` |

### A2 stk_holdernumber 前沿

| 项 | 值 |
|---|---|
| 审计时 local `MAX(ann_date)` | `20260624` |
| TinyShare 探针 | `20260625..20260723` 共 **27** 日有行（例 0723=203）；`20260724`=0（t+1/`pending_today`） |
| 结论 | **drain lag**，不是 provider 无更新 |
| 动作 | 有界 forward：`chunkyctl sync --domain stk_holdernumber --backfill --start 20260625 --end 20260723` → `batches=29 rows=2752 ok`；live `MAX(ann)=20260723` |
| 注意 | 裸 `--drain` 会扫深史日历洞（见 log 里 `ann_date=2019…`）— 前沿追赶用 `--start/--end` backfill，勿与深史 gap drain 混淆 |

---

## 1. Occam 总裁决

| 问 | 答 |
|---|---|
| 还有 class-A「错轴导致日常更新系统性漏公告」吗？ | **本轮未发现新的。** holders 公告轴漏洞已在 `542365446` 修接线；live 洞已 drain；legacy accept 的 `row_seq` 对齐是 catchup 畅通补丁。 |
| 报告期轴能否替代公告轴？ | **不能**（holders / holdernumber / share_float / dividend 披露）。报告期是标签；可用性锚在 `notice_date`/`ann_date`。 |
| 本刀要不要再改 update-flow？ | **仅最小 accept 补丁**（row_seq）；无新错轴；不改 registry 轴。 |

---

## 2. Verdict 表（registry 轴 × 表粒 × live 异常）

图例：`OK` = 轴/频率/表匹配且无系统性错；`FIX` = 日常会再制造错误（class-A）；`DEFER` = 已知诚实滞后/历史洞/ops，不阻塞轴正确性。

| 域 | Registry 轴 | 实际表粒 | Live 样本异常 | Verdict |
|---|---|---|---|---|
| **holders_top10 / aif10** | 非 registry；acquire 专用；水位=`canonical.notice_date` | `fact_top10_holder_period` + `canonical_top10_float_holders_period`（grain≈ stock×report×holder×notice） | fact-only notice **0**（was 1271）；`600388`/`20260613` **in canon** | **OK** |
| **stk_holdernumber** | `by_ann_date` / `ann_date` / wm=`MAX(ann_date)` | `raw_tushare_stk_holdernumber` grain=`[ts_code,end_date]`（多 ann 覆盖同 end） | `MAX(ann)=20260723`（was 20260624；源端有更新，ops 已追） | **OK** |
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
| **600388** | 20260613 | 20260608 | **否**（季中权益变动） | fact + **canon**（drain 后） |
| 001233 | 20260717 | 20260714 | 否 | fact |
| 002020 | 20260717 | 20260715 | 否 | fact |
| 300567 | 20260716 | 20260708 | 否 | fact |
| 601369 | 20260711 | 20260626 | 否 | fact |

汇总（`fact_top10_holder_period`）：

- distinct `notice_date`：**1951**（季末日历日仅 21）
- distinct 非季末 `report_date`：**1529**
- fact-only notice：**0**（drain 后；was **1271**）

股东户数同构（`raw_tushare_stk_holdernumber`）：

- distinct `ann_date`：随 forward sync 更新；`MAX(ann_date)=20260723`
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
| notice catchup | holders_top10 | `MAX(notice)` 跃进会漏中间稀疏分区 — **已接线 + live drain**；legacy accept 须 `assign_unique_holders_row_seq` |

**dividend 特例**：同步参数用 `ex_date`（除权生效日，交易日历合法）；**特征 PIT 必须用 `ann_date`**（registry `pit_anchor` 已写死）。不是错轴，是双日期语义。

---

## 5. 有序 backlog（进 FOUNDATION）

1. ~~**P0 ops** holders notice catchup drain~~ → **DONE**（1271→0；600388 OK）
2. ~~**P1 探针** holdernumber `ann>20260624`~~ → **DONE**：源有数；forward backfill 至 `20260723`。裸 `--drain` 深史洞另议，勿当日常前沿。
3. **P2 Type-B publish**：`fact_stock_moneyflow_daily` / `fact_stock_limit_daily` / `fact_index_daily` 相对 raw 短滞后（1–3 交易日）— 跟 serve→accepted 闭环，不改轴。
4. **P2 margin**：继续 class-B UNTRUSTED + bounded catchup；禁洗绿 READY。
5. **P3 org**：中间历史季洞仅显式 backfill 刀；日常保持 incremental-check-every-run。
6. **P3 cyq**：消费前口径门（历史 C0 FAIL）— 语义债，非采集轴。

---

## 6. 验证命令（只读）

```bash
# ship
git merge-base --is-ancestor 542365446 main && git merge-base --is-ancestor 9bde17735 main

# holders 洞 + 600388
python3 - <<'PY'
import duckdb
sm=duckdb.connect('data/smartmoney.duckdb', read_only=True)
print('holes', sm.execute("""
WITH f AS (SELECT DISTINCT replace(CAST(notice_date AS VARCHAR), '-', '') nd
 FROM fact_top10_holder_period WHERE source='miaoxiang' AND notice_date IS NOT NULL),
 c AS (SELECT DISTINCT replace(CAST(notice_date AS VARCHAR), '-', '') nd
 FROM canonical_top10_float_holders_period)
SELECT COUNT(*) FROM f LEFT JOIN c USING(nd) WHERE c.nd IS NULL AND length(f.nd)=8
""").fetchone()[0])
print(sm.execute("""
SELECT notice_date, report_date, COUNT(*) n FROM canonical_top10_float_holders_period
WHERE stock_code='600388' AND notice_date='20260613' GROUP BY 1,2
""").fetchall())
sm.close()
PY
```

---

## 7. 标签

| 项 | 状态 |
|---|---|
| Q1 ship+wire | **YES** |
| A1 notice drain | **FIXED**（1271→0；600388 in canon） |
| A2 holdernumber | **drain lag** → forward 至 `20260723` |
| 新 class-A 错轴 | **无** |
| 代码 | legacy accept `row_seq` 对齐（catchup 畅通） |
| 下一步 | 日常 `daily_update` 保持 catchup；holdernumber 用 all-due/前沿，慎裸深史 `--drain` |
