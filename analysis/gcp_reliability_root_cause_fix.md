# GCP retrain 中断根因与可靠性修复方案

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


## 0. 调查摘要

本文记录 `lgbm_phase5_gcp_20260519T143043` 的 read-only 调查结论。本次没有修改任何 `.py`、`.sh` 或配置文件, 唯一写入文件是本文档。
Job 启动: 2026-05-19 22:30:43 北京时间。Job 停止: 2026-05-20 02:02 北京时间, 用户记录。
运行时长: 约 3h32min。计划规模: 50 trials, 30 walk-forward windows, `OPTUNA_N_JOBS=8`。
实际结果: 约 11 个 COMPLETE trials, 另有多个 pruned trials。当前 best: trial 9, score 约 0.443, RankIC 约 0.0148。
健康判断: RankIC 未触发明显 leakage 警报。直接影响: `materialize_best_predictions` 未执行, predictions 未落库。
预算状态: 已用约 $2.45 / $10, 剩余约 $7.55。本次 run 估算花费约 $1.32。

已读脚本: `scripts/run_phase5_extended_retrain.sh`, `scripts/run_phase5_auto_chain.sh`, `gcp/cost_tracker.sh`, `backend/scripts/run_p0b_lambdamart_v6.py`, `backend/scripts/retrain_lambdamart_v6.py`, `gcp/vm_start.sh`, `gcp/setup_ssh_vm.sh`, `scripts/monitor_phase5_gcp_retrain.sh`。
已读本地证据: `data/reports/phase5_chain/monitor.log`, `/private/tmp/phase5_monitor.log`, `data/reports/phase5_chain/status.json`, `data/reports/gcp_cost_summary.json`, `data/reports/gcp_vm_uptime_log.csv`, `/private/tmp/gcp_cost_tracker.log`, `/private/tmp/gcp_preflight_222629.log`, `/private/tmp/gcp_preflight_v2_222904.log`。
日志缺口: `gcp/logs/` 不存在; `logs/` 只有无关 `phase3a_train.log`; `/tmp/*phase5*` 对应 `/private/tmp/phase5_monitor.log`。

serial port 读取尝试:

```bash
gcloud compute instances get-serial-port-output chunkymonkey-retrain --zone=us-central1-a 2>&1 | tail -200
gcloud compute instances get-serial-port-output chunkymonkey-optuna --zone=us-central1-a 2>&1 | tail -200
```

实际失败:

```text
ERROR: Unable to create private file [/Users/dp/.config/gcloud/credentials.db]: Operation not permitted
```

证据缺口: serial port output 无法访问, VM `dmesg` 无法访问, 远端完整 retrain log 无法访问。因此本文不能把任何 stop cause 写成已确认。

## A. 5 维根因分析与证据

### A1. C1 Spot 被抢占

Likelihood: 60%。
结论: 当前最可能, 但未被 serial port 证实。
证据: `gcp/setup_ssh_vm.sh` 使用 `--provisioning-model=SPOT` 和 `--instance-termination-action=STOP`; `session_20260519_snapshot.md` 写明 VM 是 `n2-standard-32 spot`; `gcp/vm_start.sh` 注释也写 spot。
证据: monitor 最后一条是 02:01 `VM=RUNNING elapsed=203min`; 之后没有 Python 正常完成、`MODEL_ID=...`、predictions pull 日志。
反证: serial port 无法访问, 未看到 `preempting`; 没有 GCP operations log 或 Cloud Logging stop reason。
判断: 可以写“高度怀疑 Spot preemption”, 不能写“已确认”。

### A2. C2 cost_tracker auto-stop

Likelihood: 15%。
结论: 有危险代码路径, 但本次无触发实锤。
证据: `gcp/cost_tracker.sh` 在 RUNNING 且无 marker 时记录 idle; 默认 `IDLE_GRACE_MIN=5`; idle 超过 grace 后会调用 `gcp/vm_stop.sh`。
证据: `gcp/vm_start.sh` 会写 `data/reports/gcp_vm_active_job.marker`; 当前本地 marker、idle marker、`gcp_auto_stop.log` 都不存在。
证据: `/private/tmp/gcp_cost_tracker.log` 只有 `Operation not permitted`; `gcp_vm_uptime_log.csv` 没有记录运行中的采样窗口。
反证: 没有 `AUTO-ACTION: idle > grace` 或 `VM auto-stopped` 日志; launchd 版本 cost_tracker 看起来没有成功执行。
判断: 不能确认 cost_tracker 杀掉了 job, 但 5min grace 对长跑 retrain 仍然太激进。

### A3. C3 self-shutdown bug

Likelihood: 10%。
结论: 老脚本有明确 bug, 但本次不像最强根因。
证据: `run_phase5_extended_retrain.sh` 无条件 `shutdown -h +1`, 不区分 retrain rc, Python 非 0 退出后也只保留 1min inspection。
证据: `run_phase5_auto_chain.sh` 已修成 rc-based shutdown: rc=0 用 `shutdown -h +5`, rc!=0 用 `shutdown -h +60`。
反证: 本地 monitor 没有 `[remote] retrain rc=X`; 本次 Model ID 是 `lgbm_phase5_gcp_...`, 不是 extended 脚本默认前缀。
判断: self-shutdown 是现存可靠性风险, 本次证据不足。

### A4. C4 OOM 或磁盘满

Likelihood: 10%。
结论: 无法确认, 当前证据偏弱。
证据: snapshot 记录 RSS 约 25.4GB, n2-standard-32 通常为 128GB RAM, 未接近上限。
证据: preflight v2 成功导入 4,240,940 rows panel 和 4,135,443 rows label panel。
证据: 本地日志未发现 retrain 相关 `OOM`, `out of memory`, `No space`, `disk full`。
缺口: serial port、VM `dmesg`、远端 `/var/log/syslog` 均无法访问。
判断: 不能排除内核 OOM killer, 但没有正向证据。

### A5. C5 SSH 断连或 Mac 休眠影响

Likelihood for VM stop: 5%。
Likelihood for missing pull: 75%。
结论: 不太可能让 VM 停机, 很可能解释没有自动 pull。
证据: monitor 是本地 nohup 轮询脚本, 默认 `POLL_INTERVAL_SEC=600`, `MAX_DURATION_HOURS=8`。
证据: 只有看到 `VM_STATUS=TERMINATED` 后才触发 pull; monitor 最后一条停在 02:01 `VM=RUNNING`, 没有 02:11 下一轮。
证据: 没有 `VM TERMINATED - triggering pull_predictions`, 也没有 `pull done`。
判断: Mac sleep/SSH 卡住不是 VM stop 主因, 但很可能是 predictions 未自动拉回的直接原因。

### A6. 综合判断

最强 stop-cause 猜测是 Spot preemption, 但由于 serial port 无法读取, stop cause 仍是“无法确认”。
最确定的问题是可靠性设计缺口: Optuna study 是 in-memory, best params 无结构化 checkpoint, predictions 只在 Optuna 全部结束后才 materialize, monitor 没有独立持久化运行保证, cost_tracker idle grace 过于激进。

## B. 5 个修复方案

### B1. F1 [P0] Optuna SQLite 持久化存储

目标: interrupted 后可以 resume。成本: 本地代码修改, 0 GCP cost。
当前问题: `optuna.create_study(...)` 没有 `storage`; `study_name` 每次 timestamp + uuid; 没有 `load_if_exists=True`。

建议片段, 不实施:

```python
def run_optuna(..., study_storage: str | None = None, study_name: str | None = None):
    resolved_name = study_name or f"p0b_{model_name}_v6_{datetime.now(UTC):%Y%m%dT%H%M%S}"
    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
        pruner=pruner,
        study_name=resolved_name,
        storage=study_storage,
        load_if_exists=study_storage is not None,
    )
```

建议 CLI 片段, 不实施:

```python
parser.add_argument("--study-storage", default=None)
parser.add_argument("--study-name", default=None)
result = run_optuna(..., study_storage=args.study_storage, study_name=args.study_name)
```

建议运行参数:

```bash
--study-storage sqlite:///data/reports/optuna/lgbm_phase5_gcp_20260519T143043.db \
--study-name lgbm_phase5_gcp_20260519T143043
```

验收: 重跑同一命令时已 COMPLETE trials 不重算; SQLite DB 里能看到 trials; 日志出现 resume existing study 等价信息。

### B2. F2 [P0] 每 trial checkpoint best params 与增量 predictions

目标: 中断时仍保留当前 best params 和可用 predictions。成本: 本地代码修改, 0 GCP cost。
当前问题: `retrain_lambdamart_v6.py` 在 `run_optuna` 返回后才 materialize; VM 停在 Optuna 阶段时 predictions 永远不会落库; trial params 只在 log。

建议 callback 片段, 不实施:

```python
def checkpoint_best(study: optuna.Study, trial: optuna.FrozenTrial) -> None:
    if trial.state != optuna.trial.TrialState.COMPLETE:
        return
    best = study.best_trial
    payload = {
        "best_trial_number": best.number,
        "best_value": best.value,
        "best_params": best.params,
        "best_user_attrs": best.user_attrs,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = checkpoint_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(checkpoint_path)
```

建议 optimize 与增量落库片段, 不实施:

```python
study.optimize(objective, n_trials=n_trials, gc_after_trial=True, n_jobs=n_jobs, callbacks=[checkpoint_best])
```

```sql
DELETE FROM mart_p0b_lambdamart_v6_predictions
WHERE model_id = ? AND test_start = ? AND test_end = ?;
INSERT INTO mart_p0b_lambdamart_v6_predictions SELECT * FROM _pred_window;
```

验收: 每个 COMPLETE trial 后生成 `<model_id>.best.json`; checkpoint 含 trial number, value, RankIC, params; materialize 每个 window commit 一次。

### B3. F3 [P1] 长跑 retrain 改用 non-spot

目标: 降低 4-6h retrain 被抢占风险。
当前情况: `setup_ssh_vm.sh` hardcode `--provisioning-model=SPOT`; `vm_start.sh` 只 start 既有 VM, 没有 spot/non-spot 选项。
成本对比: spot $0.376/h, 5h 约 $1.88; non-spot $1.55/h, 5h 约 $7.75; 当前剩余约 $7.55。
判断: 5h non-spot 比剩余预算高约 $0.20; Option A 或 C 可用 non-spot, 完整 50 trials 先上 F1/F2。

建议创建命令, 不实施:

```bash
gcloud compute instances create chunkymonkey-retrain-std \
  --zone=us-central1-a --machine-type=n2-standard-32 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=100GB --provisioning-model=STANDARD \
  --metadata=enable-oslogin=TRUE --quiet
```

### B4. F4 [P1] monitor MAX_DURATION_HOURS 8 改 24, cron 替代 nohup

目标: 防 Mac 休眠、shell 退出、SSH 卡住导致 missed TERMINATED。
当前问题: monitor 是本地 nohup 长循环; 最后一条日志停在 02:01; `MAX_DURATION_HOURS=8` 对失败保留窗口不够。

建议片段, 不实施:

```bash
MAX_DURATION_HOURS="${MAX_DURATION_HOURS:-24}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-300}"
```

```cron
*/5 * * * * cd /Users/dp/Documents/M/stock/chunkymonkey && MODEL_ID=$(cat data/reports/phase5_chain/model_id.txt) MAX_DURATION_HOURS=24 bash scripts/monitor_phase5_gcp_retrain.sh >> /private/tmp/phase5_monitor_cron.log 2>&1
```

更稳妥: 改成单次 probe, 由 cron/launchd 每 5min 调一次。

### B5. F5 [P1] cost_tracker IDLE_GRACE 5min 改 30min, retrain start 强制写 marker

目标: 防误判 idle stop。当前问题: 5min 太激进; marker 没有 model_id、TTL、owner_script; 手动启动 retrain 可能绕过 marker。

建议片段, 不实施:

```bash
IDLE_GRACE_MIN="${GCP_IDLE_GRACE_MIN:-30}"
cat > "$REPO_ROOT/data/reports/gcp_vm_active_job.marker" <<EOF
model_id=$MODEL_ID
job_type=phase5_retrain
started_at=$(date -Iseconds)
expected_max_hours=24
owner_script=${BASH_SOURCE[0]}
EOF
```

验收: marker 必定包含 model_id; auto-stop 日志必须写明 reason; idle stop 至少 30min 后才触发。

## C. 完整 retrain 重试方案

### C1. Option A - trial 9 best params 直接 final materialize predictions

推荐度: 最高。耗时: 30-60min。成本: 约 $0.40 spot。
适用: trial 9 已健康, RankIC 约 0.0148, 无明显 leakage。
目标: 不再跑 Optuna, 只 materialize predictions。
前提: VM disk 上仍有 `logs/retrain_lgbm_phase5_gcp_20260519T143043.log`。
风险: 本地没有 trial 9 params 完整行, 命令从远端 log 解析。

具体执行命令:

```bash
MODEL_ID=lgbm_phase5_gcp_20260519T143043
gcloud compute instances start chunkymonkey-optuna --zone=us-central1-a
sleep 60
gcloud compute ssh chunkymonkey-optuna --zone=us-central1-a --tunnel-through-iap --command "
set -euo pipefail
cd ~/chunkymonkey
source .venv/bin/activate
export PYTHONPATH=backend
export MODEL_ID=$MODEL_ID
python - <<'PY'
import ast, os, re
from pathlib import Path
from scripts.retrain_lambdamart_v6 import DEFAULT_FEATURE_VERSION, DEFAULT_LABEL_VERSION, complete_lambdamart_params, materialize_best_predictions, persist_predictions, register_lambdamart_v6_asset
from scripts.run_p0b_lambdamart_v6 import build_walk_forward_windows, load_rank_panel
from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

model_id = os.environ['MODEL_ID']
text = Path(f'logs/retrain_{model_id}.log').read_text(encoding='utf-8', errors='replace')
m = re.search(r'Trial 9 finished with value: .*? parameters: ({.*?})\\.', text)
if not m:
    raise SystemExit('trial 9 params not found')
best_params = ast.literal_eval(m.group(1))
panel = load_rank_panel(db_path=str(DB_PATH), feature_panel='mart_p0a_feature_label_panel_v4', label_col='fwd_cost_after_20d', start_date='2023-01-03', end_date='2026-05-19', exclude_cols='')
windows = build_walk_forward_windows(panel, min_train_months=6, forward_months=1, max_windows=None)
params = complete_lambdamart_params(best_params, seed=42, n_estimators=300)
predictions = materialize_best_predictions(panel=panel, windows=windows, params=params, label_col='fwd_cost_after_20d', model_id=model_id, feature_version=DEFAULT_FEATURE_VERSION, label_version=DEFAULT_LABEL_VERSION)
conn = duck_connect(str(DB_PATH))
try:
    n_rows = persist_predictions(conn, predictions, model_id=model_id)
    register_lambdamart_v6_asset(conn)
    conn.commit()
finally:
    conn.close()
print(f'MODEL_ID={model_id} PREDICTION_ROWS={n_rows}')
PY
gcloud storage cp data/smartmoney.duckdb gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb
"
gcloud storage cp gs://chunkymonkey-data-0517/phase5/smartmoney_post_${MODEL_ID}.duckdb data/smartmoney_post_${MODEL_ID}.duckdb.bak
gcloud compute instances stop chunkymonkey-optuna --zone=us-central1-a
```

后置验证:

```bash
bash scripts/run_phase5_post_retrain.sh lgbm_phase5_gcp_20260519T143043
PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py
PYTHONPATH=backend python backend/scripts/audit_data_completeness.py
```

### C2. Option B - 完整 50 trials 重跑

推荐度: 中。前提: 先实施 F1 SQLite 和 F2 checkpoint。耗时: 4-6h。成本: spot 约 $2。
优点: 最接近原计划。缺点: 未实施 F1/F2 前不建议裸跑。

```bash
OPTUNA_N_JOBS=8 OMP_NUM_THREADS=4 LIGHTGBM_NUM_THREADS=4 PYTHONPATH=backend \
python backend/scripts/retrain_lambdamart_v6.py \
  --model-id lgbm_phase5_gcp_retry_$(date +%Y%m%dT%H%M%S) \
  --start-date 2023-01-03 --end-date 2026-05-19 --n-trials 50 \
  --min-train-months 6 --top-k 20 \
  --study-storage sqlite:///data/reports/optuna/phase5_retry.db --study-name phase5_retry
```

### C3. Option C - 25 trials with checkpoints

推荐度: 高于 Option B, 低于 Option A。前提: 最好先实施 F1/F2。耗时: 2-3h。成本: spot 约 $1。
优点: 比 Option A 多一点 search。缺点: 不一定超过 trial 9。

```bash
OPTUNA_N_JOBS=8 OMP_NUM_THREADS=4 LIGHTGBM_NUM_THREADS=4 PYTHONPATH=backend \
python backend/scripts/retrain_lambdamart_v6.py \
  --model-id lgbm_phase5_gcp_25trial_$(date +%Y%m%dT%H%M%S) \
  --start-date 2023-01-03 --end-date 2026-05-19 --n-trials 25 \
  --min-train-months 6 --top-k 20 \
  --study-storage sqlite:///data/reports/optuna/phase5_25trial.db --study-name phase5_25trial
```

推荐先执行 Option A: 已知 trial 9 健康, 不再承担 Optuna search 中断风险, 30-60min 可补齐 predictions, 约 $0.40 对 $7.55 剩余预算压力小。
若 Option A 的 paper_sim 不达标, 再实施 F1/F2 后跑 Option C。

## D. GCP 服务可靠性长期改进

### D1. serial port output 到 Cloud Logging

本次 serial port 因 gcloud credential 写权限无法读取。建议开启 serial-port logging。

```bash
gcloud compute instances add-metadata chunkymonkey-optuna --zone=us-central1-a --metadata=serial-port-logging-enable=true
```

### D2. VM startup-script 加 active_job cleanup

建议 VM boot 时写 `/var/log/chunkymonkey_job_state.log`, 记录 model_id、git commit、spot/non-spot、shutdown policy。本地 marker 加 TTL, 超时只报警不直接 stop。

```bash
echo "$(date -Iseconds) boot chunkymonkey-optuna" >> /var/log/chunkymonkey_job_state.log
pgrep -fa retrain_lambdamart_v6 >> /var/log/chunkymonkey_job_state.log || true
```

### D3. retrain log 实时 sync 到 GCS

建议每 1min sync retrain log、checkpoint JSON、SQLite study DB。

```bash
while true; do
  gsutil cp "logs/retrain_${MODEL_ID}.log" "gs://chunkymonkey-data-0517/phase5/logs/retrain_${MODEL_ID}.log" || true
  gsutil cp "data/reports/optuna/${MODEL_ID}.db" "gs://chunkymonkey-data-0517/phase5/optuna/${MODEL_ID}.db" || true
  sleep 60
done &
```

### D4. Optuna 用 GCS bucket 作 storage backend

严格说 Optuna `storage=` 最稳是 SQLite/Postgres RDB URL, `gs://...` 不是原生 RDB storage URL。
本项目建议 SQLite 本地写入, 每 trial 后 sync 到 GCS; 如果需要多机共享 study, 用 Cloud SQL Postgres。
GCS 在这里作为 checkpoint artifact backend 最合适。

### D5. 统一 stop reason 审计

所有 stop 路径写同一个 JSONL。

```json
{"at":"2026-05-20T02:02:00+08:00","actor":"cost_tracker","reason":"idle_grace","model_id":"..."}
{"at":"2026-05-20T02:02:00+08:00","actor":"remote_shutdown","reason":"retrain_rc_0","model_id":"...","rc":0}
{"at":"2026-05-20T02:02:00+08:00","actor":"gcp","reason":"spot_preempted","model_id":"..."}
```

没有 stop reason 审计, 下次仍只能做概率判断。

## E. Commit message 草稿

```text
docs: analyze GCP retrain interruption and reliability fixes

Document evidence for the interrupted phase5 GCP retrain, including
spot preemption likelihood, cost tracker and self-shutdown risks,
missing monitor pull behavior, and Optuna persistence gaps.

Propose SQLite-backed Optuna resume, best-trial checkpoints,
incremental prediction materialization, safer monitor/cost-tracker
defaults, and retry options for materializing trial 9 predictions.
```
