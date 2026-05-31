# ChunkyMonkey GCP Entry Policy

GCP is available for expensive work, but cloud execution must be deliberate,
bounded, and reproducible. Read `AGENTS.md` before running anything in this
directory.

Use GCP when local execution is materially worse, such as:

- large Optuna or parameter searches;
- long model replays / train-log backfills;
- main-project plus BestChoice integrated optimization;
- repeated validation runs that would block local development.

Before starting a GCP workload, state the objective, expected wall time, rough
cost/risk, input snapshot, output paths, artifact preservation plan, and
monitor/stop/rollback plan.

Every command that touches GCP must include `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`.
This safety latch applies to `gcloud`, `gsutil`, `gcp/*`, GCS sync, VM SSH,
billing/cost checks, monitor/probe scripts, and start/stop/resume/inspect
operations.

Historical GCP artifacts that are already present on local disk may be read
locally. Refreshing or replacing them from cloud counts as GCP work and must
follow the controlled-use policy.

Operational checklist: follow `docs/engineering_governance.md` for
wrapper scripts, shutdown cancellation, pid/log/artifact markers, small artifact
export, GCS upload, monitoring, and stop handling. Do not launch expensive jobs
with fragile one-line SSH commands.
