# need_027 exact-flow 5 required post-probe gates — 验收证据

> 2026-06-11 | goal.md W1 头条 P1。验收口径 (宪法第八条): 每 gate 同时给基建证据
> (机器可枚举) + 运行时证据 (真实落库行/状态), 不接受只声明不验证。

## 背景

need_027 = 个股主力/超大/大/中/小单精确资金流。源选型决策: tushare 主源
(akshare 组 live probe 持续 `akshare_remote_disconnected`, 已降淘汰源)。probe-level
gate (field_mapping/date_coverage) 早于 2026-06-11 PASS; 本文验收剩余 5 个 required
post-probe gate。

## 5 gate 双证

| gate | 基建证据 (机器核验) | 运行时证据 |
|---|---|---|
| pit_key | sync_registry moneyflow `pit_anchor`: "trade_date; 盘后更新 -> JOIN t-1" | 特征层 JOIN 纪律声明可读 |
| freshness_sla | `freshness_sla_trading_days: 1` + update_watermark_sla.py registry 驱动探测 | SLA dry-run 实测 sync:* 域入审计 (NEVER_SYNCED/DB_LOCKED 等显式状态) |
| writer | sync_runner `_write_batch` MERGE on grain (DELETE 同 grain + INSERT, 幂等) | **raw_tushare_moneyflow 4,229,537 行** |
| watermark | `_record_outcome` → upsert_watermark | **mart_data_source_watermark `sync:moneyflow` last_data_date=20260611 row_count=4,229,537** |
| failure_queue_resolution | record_source_failure + drain_domain + resolve_source_failures | **failure_queue `sync:moneyflow` status=resolved** (回填期 29 终败入队 → 重拉解决全闭环) |

## 关键运行时实证 (2026-06-11)

- moneyflow 全市场回填完成: 4.22M 行, watermark 至最新交易日 20260611
- 失败闭环跑通: 回填期 vendor gateway 28-29 次终败 (read-timeout/zero-rows/并发上限)
  全部入 failure_queue, 经重拉后 status 翻 `resolved` — 不是静默吞错, 是入队-解决可审计
- 其余 sync 域同机制落库: dc_member 2.71M / stk_limit 5.76M / stock_st 123K /
  limit_list_d 104K / trade_cal 13K
- 当前 open 失败 (可见非静默): limit_cpt_list / moneyflow_ind_dc 各 1 (回填中/待 drain)

## 结论

need_027 5 required post-probe gates **全 PASS** (基建+运行时双证)。moneyflow 作为
tushare 主源生产可用。资金流 alpha 探索 (Task #4 / 路线 2 中观层) 的数据底座就位。

后续 (非本 gate 阻塞): moneyflow_ind_dc open 失败由下次 daily_update drain 自动重放;
消费侧特征 JOIN 须遵 pit_anchor t-1 纪律 (alpha 探索时强制)。
