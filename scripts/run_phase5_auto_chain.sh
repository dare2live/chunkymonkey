#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
#
# The old Phase 5 auto chain mixed local parquet export, GCS sync, remote
# git-pull, retrain, pull, post-retrain, and audit in one long mutable flow. It
# is no longer allowed under the current controlled GCP and checkpoint-reuse
# rules.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "scripts/run_phase5_auto_chain.sh"

cat >&2 <<'MSG'
[phase5] BLOCKED: scripts/run_phase5_auto_chain.sh is deprecated.

Use smaller controlled stages instead:
  - Existing-model true train-log replay:
      CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/gcp_train_log_replay.sh
  - Stability-aware LambdaMART retrain:
      CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/gcp_stability_retrain.sh
  - Local post-retrain validation after artifacts are imported:
      bash scripts/run_phase5_post_retrain.sh <new_model_id>

Do not launch mixed remote git-pull + retrain chains from this repo.
MSG

exit 4
