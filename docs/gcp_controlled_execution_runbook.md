# GCP Controlled Execution Runbook

Updated: 2026-05-21

## Why This Exists

On 2026-05-21 a train-log replay attempt used an ad hoc nested SSH command. A
quoting error caused the remote shell to fail, but a scheduled `shutdown +1`
survived and later terminated a retry before it could write artifacts. The VM
did stop, so cost was bounded, but the run wasted time and produced no result.

The same incident also confirmed that Spot preemption is expected behavior, not
an exceptional code failure. The prevention is resumable job design: each unit of
expensive completed work must be committed and verifiable before the next unit
starts.

This runbook is mandatory for future GCP compute jobs.

## Required Launch Pattern

Use a wrapper script, not a long one-line SSH command. Prefer a checked-in script;
for one-off controlled work, write a remote heredoc script, then run it.

For LambdaMART train-log replay, use:

```bash
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 MODEL_ID=<model_id> bash scripts/gcp_train_log_replay.sh
```

For new LambdaMART searches after the 2026-05-21 true train-log failure, do not
rerun same-model strategy/cash sweeps as a substitute for model evidence. Use a
new model id and the stability-aware wrapper:

```bash
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 MODEL_ID=<new_model_id> bash scripts/gcp_stability_retrain.sh
```

Default stability-search penalties are deliberately explicit and opt-in:
`WINDOW_RANK_IC_STD_PENALTY_WEIGHT=0.50` and
`WINDOW_RANK_IC_NEGATIVE_RATE_PENALTY_WEIGHT=0.20`. Adjust them only as part of
a stated search plan.

`scripts/run_phase5_extended_retrain.sh` and `scripts/run_phase5_auto_chain.sh`
are now deprecated compatibility shims that exit before launch. They must not be
used for new cloud work because the old implementations relied on mixed remote
`git pull` / fragile SSH retrain flows.

Every wrapper must:

- start with `set -euo pipefail`;
- `cd ~/chunkymonkey`;
- cancel stale shutdowns before starting: `sudo shutdown -c || true`;
- activate the expected environment: `. .venv/bin/activate`;
- set `PYTHONPATH=backend` and compute env such as `OMP_NUM_THREADS`;
- for nested Optuna + LightGBM jobs, set both outer and inner parallelism so
  `OPTUNA_N_JOBS * OMP_NUM_THREADS` does not exceed the VM vCPU count;
- create a stable report directory under `data/reports/<job_name>/`;
- write `current.pid`, `current.logpath`, `current.artifact`, and
  `current.gcs_dir`;
- stream stdout/stderr to the log file;
- record the command exit code;
- export the smallest sufficient result artifact;
- upload the artifact and log to GCS;
- stop the VM only after upload is attempted.

## Preflight Checklist

- State objective, command family, expected wall time, rough cost, input
  snapshot, output paths, artifact preservation, monitor plan, and stop plan.
- Run budget/status checks with `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.
- Verify required remote data and checkpoints exist before launching.
- Verify remote code contains the needed patch. If copying code to a dirty VM,
  back up remote files first and copy only the scoped files.
- Run `python -m py_compile` or a relevant smoke test on remote edited scripts.
- For objective changes, run a local narrow test that proves the requested metric
  is actually populated for the target model family. For stability retrain, a
  LambdaMART smoke must prove `window_rank_ic_*` attrs and
  `rank_ic_stability_penalty` are non-null/positive before a long run.
- If retrying a job, cancel stale shutdowns before launch and inspect previous
  logs/artifacts first.

## Monitoring Checklist

- Prefer the standard read-only probe:
  `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 TAIL_LINES=80 bash scripts/gcp_stability_status.sh`.
  It does not start the VM, export predictions, or write remote files; it reads
  `current.*` pointers, process state, Optuna trial states, best/summary
  artifact existence, and the active log tail.
- Poll `current.pid` with `ps -p`.
- Tail `current.logpath`.
- A stability retrain is not importable until at least one Optuna trial is
  `COMPLETE` and the `<model_id>.best.json` checkpoint or final summary exists.
- If SSH/IAP fails, first check `gcloud compute instances describe` and GCS
  artifact paths. Do not assume the job is still running.
- If the VM stopped without artifacts, inspect the latest remote log after
  restart before relaunching.
- Keep a fallback TTL shutdown while manually monitored work is running, but
  cancel any stale TTL before relaunching.

## Artifact Rule

Do not move a full DuckDB when a small report is enough. For train-log replay,
export the row from `fact_model_train_log` to JSON, upload that JSON plus the log,
then import the row locally.

2026-05-21 reference run:

- model: `lgbm_phase5_gcp_20260520T010718`;
- local artifact: `data/reports/train_log_replay/lgbm_phase5_gcp_20260520T010718_train_log_20260521T024117Z.json`;
- local log: `data/reports/train_log_replay/lgbm_phase5_gcp_20260520T010718_train_log_20260521T024117Z.log`;
- GCS prefix: `gs://chunkymonkey-data-0517/phase5/train_log_replay/`;
- completion checks: `expected_windows=34`, `verified_windows=34`,
  `window_metrics_len=34`, `window_integrity_bad_count=0`;
- after local import, true train-log Phase4 gate still blocked the former best
  proxy candidate because train/test RankIC relative drop was 81.36%.

2026-05-21 stability retrain historical first launch:

- model: `lgbm_phase5_stability_20260521T035555Z`;
- remote backup before scoped sync:
  `data/reports/code_sync_backup/20260521T035347Z`;
- remote log:
  `data/reports/stability_retrain/lgbm_phase5_stability_20260521T035555Z_stability_retrain_20260521T035616Z.log`;
- remote summary:
  `data/reports/stability_retrain/lgbm_phase5_stability_20260521T035555Z_stability_retrain_20260521T035616Z.json`;
- GCS prefix: `gs://chunkymonkey-data-0517/phase5/stability_retrain`;
- current pointers live in `data/reports/stability_retrain/current.*`;
- smoke note: the VM `.venv` does not include pytest, so remote smoke uses
  `py_compile` plus wrapper `--dry-run`; keep targeted pytest evidence local
  unless the VM test environment is deliberately installed.

2026-05-21 stability retrain abort/restart note:

- the first stability run was stopped before any COMPLETE trial because it
  launched four Optuna trials while each LightGBM trial inherited
  `OMP_NUM_THREADS=32`, causing an outer x inner oversubscription risk;
- abort evidence is preserved in the remote/GCS summary and log for
  `lgbm_phase5_stability_20260521T035555Z`, with `retrain_exit=137` and no best
  checkpoint;
- `scripts/gcp_stability_retrain.sh` now defaults to
  `OPTUNA_N_JOBS_REMOTE=8` and `OMP_NUM_THREADS_REMOTE=4`, and refuses
  `OPTUNA_N_JOBS_REMOTE * OMP_NUM_THREADS_REMOTE > REMOTE_MAX_THREADS`;
- `run_p0b_lambdamart_v6.py` also caps inner LightGBM threads inside
  `run_optuna()` so a bad env var cannot silently oversubscribe future runs.

2026-05-21 stability retrain objective abort note:

- the second stability run `lgbm_phase5_stability_20260521T042830Z` was also
  stopped before any COMPLETE trial after code review found the LambdaMART branch
  did not feed per-window RankIC values into the requested stability penalty;
- evidence is preserved locally and in GCS:
  `data/reports/stability_retrain/lgbm_phase5_stability_20260521T042830Z_stability_retrain_20260521T042822Z.{json,log}`;
- the summary has `retrain_exit=137`, `prediction_rows=0`,
  `train_log_found=false`, and `best_artifact=null`, so no completed reusable
  result was lost;
- future stability relaunch requires a local test proving LambdaMART collects
  window RankIC before applying `rank_ic_stability_penalty`.

Post-run prediction import should also avoid full DuckDB transfer:

```bash
CHUNKYMONKEY_GCP_EXPLICIT_OK=1 \
MODEL_ID=<completed_stability_model_id> \
bash scripts/gcp_export_model_predictions.sh

CHUNKYMONKEY_GCP_EXPLICIT_OK=1 \
gcloud storage cp --recursive \
  gs://chunkymonkey-data-0517/phase5/stability_retrain/<completed_stability_model_id>/predictions \
  data/phase5_exports/<completed_stability_model_id>

PYTHONPATH=backend python backend/scripts/import_phase5_remote_predictions.py \
  --local-db data/smartmoney.duckdb \
  --remote-parquet-dir data/phase5_exports/<completed_stability_model_id>/predictions \
  --model-id <completed_stability_model_id> \
  --mirror-lambdamart-to-oos

PYTHONPATH=backend python backend/scripts/import_model_train_log_artifact.py \
  --local-db data/smartmoney.duckdb \
  --artifact-json data/reports/stability_retrain/<train-log-artifact>.json \
  --model-id <completed_stability_model_id>
```

The export wrapper refuses to export while the same `MODEL_ID` retrain process is
still running unless `ALLOW_RUNNING_EXPORT=1` is explicitly set.

## Resume / Reuse Rule

Before relaunching a failed, stopped, or preempted long job, inspect existing
local or remote checkpoints. Do not full-rerun work that has a complete,
hash-matching, boundary-matching checkpoint.

For LambdaMART train-log replay:

- launch with `--train-log-only --resume-train-log`;
- each completed replay window is committed to `fact_model_train_log_window`;
- a checkpoint is reusable only when `model_id`, `replay_id`, `params_hash`,
  `window_key`, train/test boundaries, positive row counts, and metrics JSON all
  match the current replay;
- `fact_model_train_log` is written only after the verified window count equals
  the expected window count;
- after Spot preemption, restart the same wrapper and let it skip verified
  windows rather than starting from window 1.

If a job cannot prove completion at the unit level, fix the checkpoint contract
locally before spending more GCP time on another full run.

## BestChoice Rule

BestChoice GCP expansion is not the first step. Follow
`/Users/dp/Documents/M/stock/bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md`:
freeze/hash artifacts, import namespaced challenger data, build a daily candidate
feed, run local main-project paper_sim, register the result, then decide whether
GCP expansion is justified.
