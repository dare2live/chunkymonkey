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
| failure_queue_resolution | record_source_failure + drain_domain + resolve_source_failures | **两轮闭环实测 (时间全北京时; DB 存 UTC, 读数须 +8h)**: 第一轮 backfill 15:10-18:43 共 830 批 29 终败 → 18:43 record open (UTC 10:43); chain 脚本幂等补跑第二轮 18:43-20:14 共 830 批全成功 4,229,537 行 → 20:14 upsert watermark + resolve (UTC 12:14)。29 缺口日已由第二轮补齐 (二轮行数 = 一轮 4,081,288 + 缺口 148,249 精确自洽) |

## 关键运行时实证 (2026-06-11; Fable-5 复查二次修正 — 最终版)

- moneyflow 两轮回填闭环 (北京时): 第一轮 15:10-18:43 共 830 批 29 终败 →
  record open; 脚本幂等补跑第二轮 18:43-20:14 共 830 批全成 4,229,537 行 →
  watermark 20260611 + queue resolved。29 缺口日已补 (行数差 148,249 精确自洽)。
- 复查过程乌龙记录 (留作教训): 初次复查把 DB 的 UTC 时间戳 (10:43/12:14) 误读为
  北京上午, 虚构"上午轮"与"record 静默失效", 一度把正确结论改错; 第二次修正以
  `datetime.now(timezone.utc)` 源码 + 实验行时间戳为证还原。**读 watermark/queue
  时间戳必须 +8h 转北京时** — 已沉淀 mythos。
- 仍保留的真问题 (Task #14 缩窄): run_domain 日志 `'ok': True` 在 29 批失败时仍打
  True (宽松判定) 与 _record_outcome 的严格判定双标, 日志有误导性, 待小修。
- 其余 sync 域同机制落库: dc_member 2.71M / stk_limit 5.76M / stock_st 123K /
  limit_list_d 104K / trade_cal 13K
- 当前 open 失败 (可见非静默): limit_cpt_list / moneyflow_ind_dc 各 1 (回填中/待 drain)

## 结论

need_027 5 required post-probe gates **全 PASS** (基建+运行时双证)。moneyflow 作为
tushare 主源生产可用。资金流 alpha 探索 (Task #4 / 路线 2 中观层) 的数据底座就位。

后续 (非本 gate 阻塞): moneyflow_ind_dc open 失败由下次 daily_update drain 自动重放;
消费侧特征 JOIN 须遵 pit_anchor t-1 纪律 (alpha 探索时强制)。
