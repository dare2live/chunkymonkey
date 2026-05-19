#!/usr/bin/env bash
# codex_monitor.sh — 主动检查 + auto-cancel idle Codex 防 companion misreport 浪费
#
# 用 cron 每 15 min 跑一次 (launchd / cron):
#   */15 * * * * /Users/dp/Documents/M/stock/chunkymonkey/scripts/codex_monitor.sh
#
# 检查:
# - 每个 running Codex thread 的 idle 时间
# - 若 idle > IDLE_THRESHOLD (默认 30 min), 自动 cancel
# - 输出 + log 到 /tmp/codex_monitor.log

set -euo pipefail

# launchd 默认 PATH 不含 /opt/homebrew/bin → 用 absolute paths
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/python@3.13/libexec/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

IDLE_THRESHOLD_MIN="${IDLE_THRESHOLD_MIN:-30}"
COMPANION="/Users/dp/.claude/plugins/cache/openai-codex/codex/1.0.4/scripts/codex-companion.mjs"
LOG="/tmp/codex_monitor.log"
NODE_BIN="${NODE_BIN:-/opt/homebrew/bin/node}"

if [[ ! -f "$COMPANION" ]]; then
    echo "Codex companion not found: $COMPANION" | tee -a "$LOG"
    exit 0  # not error, just inactive
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) codex_monitor scan ===" | tee -a "$LOG"

# Get status JSON
status_json=$("$NODE_BIN" "$COMPANION" status --json 2>&1) || {
    echo "Failed to get codex status" | tee -a "$LOG"
    exit 0
}

# Parse: find idle > threshold
cancel_list=$(echo "$status_json" | python3 -c "
import json, sys
from datetime import datetime, timezone
data = json.load(sys.stdin)
now = datetime.now(timezone.utc)
threshold_min = int('${IDLE_THRESHOLD_MIN}')
for t in data.get('running', []):
    updated = datetime.fromisoformat(t['updatedAt'].replace('Z', '+00:00'))
    idle_min = (now - updated).total_seconds() / 60
    if idle_min > threshold_min:
        print(f\"{t['id']} idle={idle_min:.0f}min elapsed={t['elapsed']}\")
")

if [[ -z "$cancel_list" ]]; then
    n_running=$(echo "$status_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('running', [])))")
    echo "OK: $n_running running, none idle > ${IDLE_THRESHOLD_MIN}min" | tee -a "$LOG"
    exit 0
fi

echo "Detected stuck Codex:" | tee -a "$LOG"
echo "$cancel_list" | tee -a "$LOG"

# Cancel each
echo "$cancel_list" | while read line; do
    tid=$(echo "$line" | awk '{print $1}')
    [[ -z "$tid" ]] && continue
    echo "  Cancelling $tid..." | tee -a "$LOG"
    "$NODE_BIN" "$COMPANION" cancel "$tid" --json 2>&1 | grep -E "status|jobId" | head -2 | tee -a "$LOG"
done

echo "=== done ===" | tee -a "$LOG"
