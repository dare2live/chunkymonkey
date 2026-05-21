#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
#
# The old Phase 5 extended retrain script used remote git-pull plus a fragile
# one-line SSH/nohup command. That launch pattern is no longer allowed under the
# current GCP controlled execution rules.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

source scripts/lib/gcp_guard.sh
require_gcp_explicit_ok "scripts/run_phase5_extended_retrain.sh"

cat >&2 <<'MSG'
[phase5] BLOCKED: scripts/run_phase5_extended_retrain.sh is deprecated.

Use the controlled wrappers instead:
  - Existing-model true train-log replay:
      CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/gcp_train_log_replay.sh
  - Stability-aware LambdaMART retrain:
      CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash scripts/gcp_stability_retrain.sh

Before launching, state objective, expected wall time/cost, input snapshot,
artifact paths, monitor plan, and stop/rollback plan.
MSG

exit 4
