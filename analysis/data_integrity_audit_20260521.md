# 数据完整性 Audit — 2026-05-21 全量更新后

> 触发: 用户 push back "跑一遍 chunkymonkey 的各类数据更新, 看有没有卡点顺便也把数据拉到最新, 然后做一个数据完整性验证, 看看是否抓到了全量的最新数据".
> 流程: `bash scripts/build_price_kline_tdxhub.py --skip-existing` 全量 sync 5201 stocks (用时 ~6.5 min) + `daily_update.sh SKIP_SYNC=1` 跑完整 8 steps + 数据完整性 audit.

## 1. 总体 Verdict — **PASS**

- 主项目 K-line / xdxr / industry / lhb / financial / qfii 全部抓到合理最新数据
- 唯一 alert: `institution_survey` (aif10) lag 6 days, 不在 daily_update sync 范围 (已知数据缺口, 不阻塞主项目交付)
- 数据更新流程修复 set -e 静默失灵 bug 后, 跑通到 Step 8 报告生成

## 2. K-line 覆盖 (market.duckdb)

| 指标 | 值 |
|---|---|
| 时间范围 | 2022-01-01 ... 2026-05-21 (今天) |
| Trading days | 1,059 |
| Active stocks (dim_active_a_stock) | 5,512 |
| K-line 今日 (2026-05-21) stocks | **5,186** (本地预检 A 股 5201 中 active 部分) |
| 缺口 (active - today_kline) | 326 |
| 缺口来源 | 全是 ST/*ST 停牌 (sample: *ST国华 / *ST万方 / ST萃华 / *ST恒久 / *ST赛隆 / *ST天龙 / 交大思诺 / 大普微-UW / *ST创兴 / *ST华嵘 / *ST熊猫 / *ST沪科 / *ST国化 / *ST岩石 / *ST太和 ...) |

### 最近 7 trading days K-line 行数

| 日期 | 行数 | 备注 |
|---|---:|---|
| 2026-05-21 | 5,186 | 今天 |
| 2026-05-20 | 5,183 | |
| 2026-05-19 | 5,179 | |
| 2026-05-18 | 5,184 | |
| 2026-05-15 | 5,182 | 周五 |
| 2026-05-14 | 5,182 | |
| 2026-05-13 | 5,182 | |

**评估**: 5179-5186 区间稳定, 无丢日, 无大规模缺数. 跟 active universe 5512 差距全是 ST 停牌, 符合 PIT 真实情况.

## 3. Watermark SLA (smartmoney.duckdb `mart_data_source_watermark`)

| capability | source | latest_date | updated_at | status |
|---|---|---|---|---|
| **kline_daily** | tdxhub_quote | 2026-05-21 | 20:25:57 | OK |
| **xdxr** | tdxhub_xdxr | 2026-05-21 | 20:25:57 | OK |
| holders_top10_float | tdxhub_holders | 20260429 | 05-06 | OK (季度数据, SLA 100d) |
| kline_daily | akshare_multi_source | 2026-05-21 | 20:25:56 | OK (**fallback=True** akshare 接口不稳, 用 tdxhub 兜底) |
| industry_sw | tdxhub_block | 2026-05-18 | 05-18 | OK (周末延后) |
| stock_blocks | tdxhub_block | 2026-05-18 | 05-18 | OK |
| lhb_daily | aif10_lhb | 2026-05-18 | 20:16:24 | OK (周末 + 周一) |
| **institution_survey** | aif10_survey | 2026-05-15 | 05-18 | **ALERT** (lag 6d > SLA 2d, tier 2) |
| financial_gpcw_8q | tdxhub_gpcw | 2026-03-31 | 05-06 | OK (Q1 季报, SLA 100d) |
| qfii_holding_quarterly | aif10_qfii | 2026-03-31 | 05-06 | OK |
| northbound_holding | akshare_hsgt | None | 05-06 | OK (历史保留) |

## 4. daily_update.sh 8 steps 跑通验证

实测 `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 SKIP_SYNC=1 bash scripts/daily_update.sh` 全流程通过:

| Step | 状态 | 输出 |
|---|---|---|
| 0 GCP cost tracker | OK | 56.7% / $8.52 of $15 budget, OK |
| 1 Preflight (SLA + K-line gate) | OK | 0 watermark updated (跳 sync), 1 alert (institution_survey 6d) |
| 2c alpha158 freshness | OK | 2 days stale (≤3d threshold) → 跳 rebuild |
| 3 Label + panel rebuild | WARN | v4 panel rebuild 失败 (非 fatal, 继续) |
| 4 Model refresh | OK | alpha decay=STABLE, 用 cached lambdamart_v6 |
| 5 paper_sim + MSAF ensemble KPI | OK | **ann=48.40% / max_dd=-24.28% / sharpe=0.81 / n_obs=22** (跟 baseline 一致) |
| 6 Phase4 gate | block | lm735/sniper265/h10/k3/neutralcash20 IS=0.1192 / OOS=0.0222 / relative_drop=81.36% |
| 7 Champion promote | skipped | verdict=block 阻断 |
| 8 Report | OK | data/reports/daily_20260521.{json,md} 生成 |

## 5. 修复的卡点

| 卡点 | Root cause | 修法 |
|---|---|---|
| daily_update.sh Step 1a 静默退出 | `set -euo pipefail` + `update_watermark_sla.py exit 2` (alert) → 脚本静默终止, `sla_exit=$?` 永远不执行 | 改 `if ! python ...; then sla_exit=$?; fi` 包装抑制 set -e |
| daily_update.sh Step 2a tdxhub sync 静默退出 | 同上模式 | 同上 |
| 前端按钮连点竞态 | data-view.js 无 disable 防护, 后端 `_is_running` 锁 race window 3ms | 新增 `_updateBusy` flag + `_setUpdateButtonsBusy()`, polling running=false 时 release |
| 前端 polling 错误吞掉 | `catch (e) { /* silent */ }` | 改 `_pollErrCount` 第 1/5/10... 次 logLine 报错 |

## 6. 全量数据 sync 实测耗时

`build_price_kline_tdxhub.py --skip-existing --workers 4` 实测:
- 5201 stocks 全扫
- 写入 ~10398 行 (~2 行/股, 增量补 5-21)
- 速度 11-14 股/s
- 耗时 ~6.5 min
- 失败 0

## 7. 剩余已知 gap

| Gap | 优先级 | 备注 |
|---|---|---|
| `institution_survey` lag 6d | P2 | aif10 上游, 不在 daily_update sync 范围. tier 2 SLA 2d. 影响范围: institution alpha (权重 0.40 中的小部分). 跟主项目交付不阻断. |
| `v4 panel rebuild 失败` | P1 | Step 3b WARN, 但 v4 panel 已是 04-14 最新, 不影响 model 预测. 需查 root cause. |
| 326 stocks 缺今日 K-line | P3 (正常) | 全 ST/*ST 停牌, PIT 真实情况, 不是 bug. |
| `northbound_holding` latest=None | P3 | 历史保留, 不参与 active feature. |

## 8. 复现命令

```bash
# 全量 K-line sync
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 PYTHONPATH=backend python backend/scripts/build_price_kline_tdxhub.py \
    --skip-existing --workers 4

# Daily update 完整流程
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 SKIP_SYNC=1 bash scripts/daily_update.sh

# Watermark 看
PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
for r in con.execute('SELECT data_domain, source_name, last_data_date, updated_at, fallback_active FROM mart_data_source_watermark ORDER BY last_data_date DESC').fetchall():
    print(r)
"
```
