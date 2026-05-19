# paper_sim + KPI compare 8 步实施 plan

项目：ChunkyMonkey；数据库：`data/smartmoney.duckdb`；GCP retrain：`lgbm_phase5_gcp_20260519T143043`。
目标：retrain 完成后拉取 predictions，完成 PIT/leakage 审计，运行 paper_sim，入库 KPI，并和 baseline 做部署守门。
执行规则：T+1 隐式日循环，次日开盘执行；涨跌停/停牌阻断；佣金 `0.03%`、印花税 `0.1%`、滑点 `0.05%`；PIT prior-day amount 过滤。
占位符 `<...>` 执行前必须替换；参数名不确定时先运行脚本 `--help`。

## 步骤1 - Pull predictions GCS 到 local

**目标说明**：确认 `PID 11736` 监控进程状态，等待 retrain 完成后，用 `gcp/pull_results_to_duckdb.py` 拉取新 predictions 到 `mart_p0b_lambdamart_v6_predictions`。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; MONITOR_PID="11736"
MODEL_ID="<填入GCP_retrain产出的新model_id>"; GCS_PREFIX="gs://<填入retrain输出prefix>"
PRED_TABLE="mart_p0b_lambdamart_v6_predictions"
ps -p "${MONITOR_PID}" -o pid,ppid,etime,stat,command || true
while ps -p "${MONITOR_PID}" >/dev/null 2>&1; do date; ps -p "${MONITOR_PID}" -o pid,etime,stat,command; sleep 600; done
python gcp/pull_results_to_duckdb.py --help
python gcp/pull_results_to_duckdb.py --db "${DB}" --table "${PRED_TABLE}" --model-id "${MODEL_ID}" --gcs-prefix "${GCS_PREFIX}"
```

**验证 check**：`duckdb data/smartmoney.duckdb "SELECT model_id, COUNT(*) n_rows, MIN(trade_date), MAX(trade_date) FROM mart_p0b_lambdamart_v6_predictions WHERE model_id='<填入GCP_retrain产出的新model_id>' GROUP BY model_id;"`。
通过标准：新 `model_id` 行数 `>= 1,000,000`，日期范围约 `2023-07` 到 `2026-05`。

**备注**：retrain 返回码非零走步骤8 Fallback A；拉取失败走步骤8 Fallback B。

## 步骤2 - Pre-sim audit: PIT + leakage 检查

**目标说明**：运行 PIT 完整性、覆盖率、registry feature PIT 审计，并检查新 model_id 的 prediction 分布、日期范围、RankIC。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; MODEL_ID="<填入GCP_retrain产出的新model_id>"
FORWARD_RETURN_TABLE="${FORWARD_RETURN_TABLE:-mart_forward_return_5d}"
python backend/scripts/audit_pit_integrity.py --db "${DB}" --model-id "${MODEL_ID}"
python backend/scripts/audit_pit_coverage.py --db "${DB}" --model-id "${MODEL_ID}"
python backend/scripts/audit_registry_feature_pit.py --db "${DB}" --model-id "${MODEL_ID}"
duckdb "${DB}" <<SQL
SELECT COUNT(*) n_rows, COUNT(DISTINCT trade_date) n_dates, MIN(trade_date) min_dt, MAX(trade_date) max_dt, MIN(prediction) min_pred, quantile_cont(prediction,0.5) p50_pred, quantile_cont(prediction,0.99) p99_pred, MAX(prediction) max_pred, AVG(prediction) avg_pred, STDDEV_SAMP(prediction) std_pred FROM mart_p0b_lambdamart_v6_predictions WHERE model_id='${MODEL_ID}';
WITH daily_ic AS (SELECT p.trade_date, corr(p.prediction,r.ret_fwd_5d) rank_ic FROM mart_p0b_lambdamart_v6_predictions p JOIN ${FORWARD_RETURN_TABLE} r USING (trade_date,symbol) WHERE p.model_id='${MODEL_ID}' GROUP BY p.trade_date) SELECT COUNT(*) n_days, AVG(rank_ic) avg_rank_ic, MAX(rank_ic) max_rank_ic FROM daily_ic;
SQL
```

**验证 check**：三个 audit 返回码必须为 `0`；预测不得全空、全常数、日期范围异常；RankIC 不得触发警报。

**备注**：4 组 leakage 阈值为 `RankIC > 0.3`、`sharpe > 5`、`monthly_win > 0.95`、`ann_ret > 100%` 或相对 baseline 年化提升 `> 50%`。任一触发后进入步骤7 Case D。

## 步骤3 - paper_sim 执行

**目标说明**：用 `backend/scripts/run_paper_sim_lambdamart_v6_compare.py` 跑新 GCP 模型，并补跑 production champion `lgbm_phase5_session_20260518T160747`。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; CONFIG="backend/config/paper_sim_ml_score_lambdamart_v6.yaml"
NEW_MODEL_ID="<填入GCP_retrain产出的新model_id>"; CHAMPION_MODEL_ID="lgbm_phase5_session_20260518T160747"
NEW_SIM_RUN_ID="lgbm_p5_gcp_20260519"; CHAMPION_SIM_RUN_ID="lgbm_p5_champion_20260518"
python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --help
time python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --db "${DB}" --config "${CONFIG}" --model-id "${NEW_MODEL_ID}" --sim-run-id "${NEW_SIM_RUN_ID}" --top-k 5 --tx-cost full --execution t-plus-1 --block-limit-up-down true --block-suspended true --pit-prior-day-amount true
time python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --db "${DB}" --config "${CONFIG}" --model-id "${CHAMPION_MODEL_ID}" --sim-run-id "${CHAMPION_SIM_RUN_ID}" --top-k 5 --tx-cost full --execution t-plus-1 --block-limit-up-down true --block-suspended true --pit-prior-day-amount true
```

**验证 check**：`duckdb data/smartmoney.duckdb "SELECT sim_run_id, COUNT(*) n_rows, MIN(trade_date), MAX(trade_date) FROM mart_paper_sim_daily WHERE sim_run_id IN ('lgbm_p5_gcp_20260519','lgbm_p5_champion_20260518') GROUP BY sim_run_id;"`，两行均非零。

**备注**：`driver.py` 确认 T+1；`tradability.py` 负责涨跌停/停牌阻断；`tx_cost.py` 负责完整成本；`ml_score_loader.py` 负责 model_id 加载。2 到 3M predictions 预估几十分钟到 2 小时。

## 步骤4 - KPI 提取入库

**目标说明**：将两个新 sim_run 的 KPI 写入 `mart_paper_sim_kpi`，字段为 `sim_run_id / model_id / ann_ret / max_dd / sharpe / monthly_win / excess_vs_hs300 / n_months / created_at`。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; NEW_MODEL_ID="<填入GCP_retrain产出的新model_id>"
CHAMPION_MODEL_ID="lgbm_phase5_session_20260518T160747"
python - <<'PY'
from backend.services.paper_sim import reporter
print("优先使用 reporter.py 已有 KPI 计算和写入入口：", reporter.__file__)
PY
duckdb "${DB}" <<SQL
INSERT INTO mart_paper_sim_kpi (sim_run_id,model_id,ann_ret,max_dd,sharpe,monthly_win,excess_vs_hs300,n_months,created_at)
SELECT sim_run_id, CASE WHEN sim_run_id='lgbm_p5_gcp_20260519' THEN '${NEW_MODEL_ID}' WHEN sim_run_id='lgbm_p5_champion_20260518' THEN '${CHAMPION_MODEL_ID}' END, ann_ret,max_dd,sharpe,monthly_win,excess_vs_hs300,n_months,current_timestamp FROM mart_paper_sim_kpi_staging WHERE sim_run_id IN ('lgbm_p5_gcp_20260519','lgbm_p5_champion_20260518');
SQL
```

**验证 check**：`duckdb data/smartmoney.duckdb "SELECT sim_run_id, model_id, ann_ret, max_dd, sharpe, monthly_win, excess_vs_hs300, n_months, created_at FROM mart_paper_sim_kpi WHERE sim_run_id IN ('lgbm_p5_gcp_20260519','lgbm_p5_champion_20260518') ORDER BY created_at DESC;"`。

**备注**：优先走 `backend/services/paper_sim/reporter.py`；入库失败时只检查 `backend/services/paper_sim/ddl.py` schema 与 reporter 字段对齐，不临时改现有 schema。

## 步骤5 - KPI 对比表

**目标说明**：生成 6 列矩阵 `sim_run_id | ann_ret | max_dd | sharpe | monthly_win | excess_vs_hs300`，包含 4 个已有 baseline、新 GCP retrain、champion model，共 6 行。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; BASELINE_4_SIM_RUN_ID="<填入第四个已存在baseline的sim_run_id>"
duckdb "${DB}" <<SQL
.mode markdown
WITH wanted(sim_run_id,ord) AS (VALUES ('sizer_ablation_equal_20260517',1),('swap_v1_20260516_133642',2),('swap_v1_20260516_131621',3),('${BASELINE_4_SIM_RUN_ID}',4),('lgbm_p5_gcp_20260519',5),('lgbm_p5_champion_20260518',6))
SELECT k.sim_run_id,k.ann_ret,k.max_dd,k.sharpe,k.monthly_win,k.excess_vs_hs300 FROM wanted w LEFT JOIN mart_paper_sim_kpi k USING (sim_run_id) ORDER BY w.ord;
SQL
```

**验证 check**：`duckdb data/smartmoney.duckdb "WITH wanted(sim_run_id) AS (VALUES ('sizer_ablation_equal_20260517'),('swap_v1_20260516_133642'),('swap_v1_20260516_131621'),('<填入第四个已存在baseline的sim_run_id>'),('lgbm_p5_gcp_20260519'),('lgbm_p5_champion_20260518')) SELECT COUNT(k.sim_run_id) matched_rows FROM wanted w JOIN mart_paper_sim_kpi k USING (sim_run_id);"`，通过标准为 `matched_rows = 6`。

**备注**：已知 baseline 为 `sizer_ablation_equal_20260517`：`ann +68.3% / dd -21.7% / sharpe 0.91 / 月胜 45%`；`swap_v1_20260516_133642`：`ann +56.7% / dd -20.0% / sharpe 1.42 / 月胜 67%`；`swap_v1_20260516_131621`：`ann +17.7% / dd -20.2% / sharpe 0.74 / 月胜 44%`。第四个 baseline 执行前从历史 KPI 表确认。

## 步骤6 - 真金白银守门

**目标说明**：对照 Pareto target：年化 `30%`、dd 不差于 `-20%`、月胜 `55%`、超额 HS300 `> 0`；净增益相对当前最强 baseline `swap_v1_20260516_133642`。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"
duckdb "${DB}" <<SQL
.mode markdown
WITH c AS (SELECT * FROM mart_paper_sim_kpi WHERE sim_run_id='lgbm_p5_gcp_20260519'), b AS (SELECT * FROM mart_paper_sim_kpi WHERE sim_run_id='swap_v1_20260516_133642')
SELECT c.sim_run_id, c.ann_ret, CASE WHEN c.ann_ret>=0.30 THEN 'PASS' ELSE 'FAIL' END ann_gate, c.max_dd, CASE WHEN c.max_dd>=-0.20 THEN 'PASS' ELSE 'FAIL' END dd_gate, c.monthly_win, CASE WHEN c.monthly_win>=0.55 THEN 'PASS' ELSE 'FAIL' END win_gate, c.excess_vs_hs300, CASE WHEN c.excess_vs_hs300>0 THEN 'PASS' ELSE 'FAIL' END excess_gate, c.ann_ret-b.ann_ret ann_delta, c.monthly_win-b.monthly_win win_delta, c.max_dd-b.max_dd dd_improve, CASE WHEN c.ann_ret-b.ann_ret>=0.05 OR c.monthly_win-b.monthly_win>=0.05 OR c.max_dd-b.max_dd>=0.02 THEN 'PASS' ELSE 'FAIL' END net_gain_gate FROM c CROSS JOIN b;
SQL
```

**验证 check**：总体通过条件是 Pareto 四维全 PASS，且满足 `+5pp ann_ret` 或 `+5pp 月胜` 或 `dd 改善 >= 2pp` 任一净增益。

**备注**：每个维度单独判定后再给总体判定；dd 改善用 `candidate.max_dd - baseline.max_dd >= 0.02`，例如 `-18%` 相对 `-20%` 改善 `2pp`。

## 步骤7 - 决策点: 4 分支

**目标说明**：根据步骤2 leakage 与步骤6 守门结果，选择唯一分支：部署、hold、继续调参或停止清理。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"
duckdb "${DB}" "WITH c AS (SELECT * FROM mart_paper_sim_kpi WHERE sim_run_id='lgbm_p5_gcp_20260519'), b AS (SELECT * FROM mart_paper_sim_kpi WHERE sim_run_id='swap_v1_20260516_133642'), g AS (SELECT c.*, c.ann_ret>=0.30 pass_ann, c.max_dd>=-0.20 pass_dd, c.monthly_win>=0.55 pass_win, c.excess_vs_hs300>0 pass_excess, (c.ann_ret-b.ann_ret>=0.05 OR c.monthly_win-b.monthly_win>=0.05 OR c.max_dd-b.max_dd>=0.02) pass_gain FROM c CROSS JOIN b) SELECT sim_run_id, CASE WHEN pass_ann AND pass_dd AND pass_win AND pass_excess AND pass_gain THEN 'Case A' WHEN pass_ann AND pass_dd AND pass_win AND pass_excess AND NOT pass_gain THEN 'Case B' WHEN NOT (pass_ann AND pass_dd AND pass_win AND pass_excess) THEN 'Case C' ELSE 'Manual Review' END decision_case FROM g;"
python backend/scripts/cleanup_leakage_data.py --db data/smartmoney.duckdb --model-id "<触发leakage的model_id>" --dry-run
python backend/scripts/cleanup_leakage_data.py --db data/smartmoney.duckdb --model-id "<触发leakage的model_id>"
```

**验证 check**：`duckdb data/smartmoney.duckdb "SELECT sim_run_id, ann_ret, max_dd, sharpe, monthly_win, excess_vs_hs300 FROM mart_paper_sim_kpi WHERE sim_run_id IN ('lgbm_p5_gcp_20260519','swap_v1_20260516_133642');"`。

**备注**：Case A：过 Pareto 全部维度且超 baseline 净增益，部署 production，更新 champion。Case B：过 Pareto 但未超 baseline 净增益，模型 hold，记录结论，下一轮调参再尝试。Case C：任一 Pareto 维度不达标，重看 Optuna config，考虑继续调参或切换特征。Case D：触发 leakage 警报，立即停止，执行 `backend/scripts/cleanup_leakage_data.py`，排查数据管道。

## 步骤8 - Fallback 场景

**目标说明**：覆盖 retrain 失败、GCS 拉取失败、paper_sim crash 或结果为 0、KPI 入库失败四类情况。

**具体命令**
```bash
set -euo pipefail
DB="data/smartmoney.duckdb"; MODEL_ID="<填入GCP_retrain产出的新model_id>"
GCS_PREFIX="gs://<填入retrain输出prefix>"; LOCAL_TMP="/tmp/chunkymonkey_gcp_predictions_${MODEL_ID}"
ps -p 11736 -o pid,etime,stat,command || true
find . -maxdepth 3 -type f \( -name '*phase5*gcp*log*' -o -name '*retrain*log*' \) -print
gsutil ls -la "${GCS_PREFIX}" || true
mkdir -p "${LOCAL_TMP}" && gsutil -m cp -r "${GCS_PREFIX}" "${LOCAL_TMP}/"
duckdb "${DB}" "CREATE OR REPLACE TEMP TABLE tmp_gcp_predictions AS SELECT * FROM read_parquet('${LOCAL_TMP}/**/*.parquet'); SELECT COUNT(*) FROM tmp_gcp_predictions;"
python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --help
grep -n "model_id" backend/services/paper_sim/ml_score_loader.py
grep -n "lambdamart" backend/config/paper_sim_ml_score_lambdamart_v6.yaml
grep -n "mart_paper_sim_kpi" backend/services/paper_sim/ddl.py
grep -n "sim_run_id\\|ann_ret\\|max_dd\\|sharpe\\|monthly_win" backend/services/paper_sim/reporter.py
```

**验证 check**：Fallback A 检查 `scripts/monitor_phase5_gcp_retrain.sh` 日志、GCS 错误、retrain rc；Fallback B 用临时表确认行数大于 `0`；Fallback C 检查 `ml_score_loader.py` model_id 过滤与 config yaml；Fallback D 检查 `ddl.py` schema 与 `reporter.py` 字段对齐。

**备注**：Fallback 只用于定位失败原因，不把失败运行当成模型质量结论。任何清理或重写数据库动作前，先保留日志、model_id、sim_run_id、行数和日期范围证据。
