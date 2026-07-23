# stk_holdernumber：退役原因 → 恢复路径（证据）

> **生命周期**：evidence-only（analysis 层；**非** owner bible）  
> 日期：2026-07-24  
> 触发：owner 追问 #3 股东户数为何退役，并要求可用（集中度 / 与股价关系 / 因子辅助）。  
> 结论标签：**RESTORE FIXED**（`by_ann_date` 增量 + DataAccess + dossier assist）

## Q1 — 为什么 `by_ts_code` 进不了正常增量 drain（unsupported）？

Occam（代码真相源 = `sync_runner.drain_domain` + drain 主循环 fallback）：

| 路径 | 条件 | 结果 |
|---|---|---|
| 真 drain（按日补洞） | `batch_mode == by_trade_date` | 日历 gap → 逐日重拉 |
| 增量 fallback | `by_ann_date` / `full_refresh` / `by_date_range` / `by_code_list` | `unsupported` → `run_domain` watermark 增量 |
| 增量 fallback | `by_ts_code` **且** `increment_mode == by_report_period` | 同上（财报/十大股东型） |
| **typed unsupported** | `by_ts_code` **无** `increment_mode` | **硬失败**（进 `--all-due` 则 soft DEGRADED） |

退役前 `stk_holdernumber` = `by_ts_code` 且无 `increment_mode` → 落最后一行。  
对比：`daily`/`moneyflow`/`margin` = `by_trade_date`（或 formal on_demand）；`holders`/`fina_indicator` = `by_ts_code`+`by_report_period`；`share_float`/`stk_holdertrade` = `by_ann_date`。

**增量可 drain 的 Occam 定义**：要么真按日 gap drain，要么有**已实现的 typed fallback**；裸 `by_ts_code` 全宇宙日扫既不进 gap drain，也不进 fallback。

## Q2 — 其他数据都有消费方吗？

否。S7 诚实分层（`legacy_raw_plane.yaml`）：

| 类 | 代表 | 消费方 |
|---|---|---|
| formal→accepted→serve | daily / stock_st / margin | derive / form / pulse / dossier / Cap |
| fact publication (B2) | moneyflow / limit / index_daily / top_inst | pulse / paper / tech / institution |
| serve_l0_declared | share_float / cyq / forecast / report_rc / fina… | DataAccess 已声明；**部分尚无 live router** |
| sync_orphan | balancesheet / income / ths_hot / hsgt… | **研究孤儿**（禁假 COMPAT / 禁 standby） |
| pulse builder-only | moneyflow_ind_dc / sw_daily / dc_index… | mart_* 展示，非个股 L0 叶 |

股东户数退役时属 sync_orphan（0 DataAccess/serve）；**不是**被 `holders_top10` 替换（top10=名单；户数=慢变量）。

## Q3 — 恢复交付（本刀）

1. **sync_registry**：恢复 `stk_holdernumber`，`batch_mode=by_ann_date`（禁 `by_ts_code` 全宇宙日扫）。  
   - LIVE 2026-07-24：`ann_date=20250429` → 2508 行；vendor 单次硬顶 3000 → `page_limit=1500`。  
   - 旧注释「截面 enddate 返 0」已过时（同日 `enddate=20250331` 返满页 3000）。  
2. **scope**：documented **raw_evidence**（landing 保留供应商响应；暂无 formal accept plane；同类 share_float/cyq）。  
3. **DataAccess** entity `holder_number`（PIT=`ann_date`）。  
4. **serve**：`holdernumber_assist` → dossier `holder_number`（集中度方向 + 同窗 qfq 涨跌辅助；fail-closed；非 Optuna）。  
5. **plane**：`legacy_raw_plane` retired→`ssot/serve_l0_declared`；S7 墙 **20 ssot / 8 declared / 3 retired**。  
6. **update-flow**：进 `--all-due`；drain 对 `by_ann_date` 走既有 `incremental_fallback`。

## 历史（保留）

- `baa239ac4` 墓碑原因成立（orphan + unsupported）。  
- `express`/`fina_mainbz` 仍 lifecycle DROP；本域表保留并恢复写路径。
