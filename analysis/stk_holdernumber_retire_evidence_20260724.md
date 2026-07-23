# stk_holdernumber 为何不用了（调查；非 DROP）

> **生命周期**：evidence-only（analysis 层；**非** owner bible）
> 日期：2026-07-24  
> 触发：owner 同意 DROP `express`/`fina_mainbz`，并追问 #3 股东户数为何退役。  
> 结论标签：**留 retired / 不 DROP / 不恢复 registry**

## Verdict（Occam）

`stk_holdernumber` 被 `baa239ac4` 墓碑化，是因为 **sync_orphan + drain typed `unsupported` 拖软 DEGRADED**，不是 API 坏了，也不是被 `holders_top10` 替换。

| 假说 | 证据 | 判定 |
|---|---|---|
| 无消费者 | `data_access.yaml` 0 命中；`moth coupling --impact stk_holdernumber` 仅 watermark 注释 / sync_runner 历史注释 / tests/docs；`git log -S` 在 `backend/services`+`routers` 无 `load_holdernumber` 消费函数 | **成立** |
| API 坏了 | registry 退役前有完整 grain/PIT/`data_start_reviewed`；表仍在且 284 902 行、`end_date`→20260622 | **不成立** |
| 被 holders_top10 替换 | `holders_top10` = 十大股东持股名单（妙想/aif10）；`stk_holdernumber` = 股东**户数**慢变量；SLA `holders_top10_float` 指向 aif10 是 top10 域，不是户数 | **不成立** |
| 误删能力 | 注册动机见 `analysis/tushare_alpha_potential_research_20260617.md`（研究候选）；从未进 DataAccess/serve/dossier/feature builder | **非误删**：退役正确；恢复需先有 consumer path（硬禁无消费者 revive） |

## 退役机制（baa239ac4）

1. `--all-due --drain` 选中该域 → `batch_mode=by_ts_code` 且无可用 `increment_mode` 落地路径 → runner 报 typed **`unsupported`**（非 fetch fail）。  
2. 同批 `express`（`by_period`）、`fina_mainbz`（`by_ts_code`）同类。  
3. 处置：registry 墓碑 + `legacy_raw_plane` `role=retired`；表保留冷残差直至 owner 可选 lifecycle。  
4. Owner 本刀仅签字物删 **1+2**；#3 **表保留**。

## 产品/地基是否「还该用」？

股东户数作为筹码集中慢变量在研究笔记里有价值，但当前 foundation/product **没有** 任何读路径（无 DataAccess、无 serve、无 dossier、无 panel feature 接线）。硬禁：**无 consumer path 不 revive**。若未来要用：新 consumer + PIT JOIN（`ann_date`）+ 增量模式（避免再进 unsupported）+ owner 再开域，而不是静默恢复墓碑。

## 本刀动作

- `express` / `fina_mainbz`：lifecycle archive+DROP（manifest `lifecycle_delete_manifest_express_fina_mainbz_20260724.yaml`）  
  - express：26 959 行 → `data/archive/lifecycle/raw_tushare_express.parquet`（≈1.6 MiB）  
  - fina_mainbz：25 674 行 → `…/raw_tushare_fina_mainbz.parquet`（≈0.9 MiB）  
  - compact：**SKIP**（free_blocks +16 ≈4 MiB；整库 rewrite 不划算）  
- `stk_holdernumber`：**不 DROP**；保持 retired 冷残差（表仍在，≈285k 行）
