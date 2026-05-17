#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${JOB_CONFIG_URI:-}" ]]; then
  echo "JOB_CONFIG_URI is required, for example gs://YOUR_BUCKET_NAME/chunkymonkey/jobs/BATCH_ID/experiment_000001.json" >&2
  exit 2
fi

WORKDIR_ROOT="${WORKDIR_ROOT:-/tmp/chunkymonkey-batch}"
mkdir -p "${WORKDIR_ROOT}"

python /app/gcp/run_job.py \
  --job-config-uri "${JOB_CONFIG_URI}" \
  --workdir-root "${WORKDIR_ROOT}"
