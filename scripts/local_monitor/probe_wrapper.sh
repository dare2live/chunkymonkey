#!/usr/bin/env bash
# FDA-safe wrapper for phase5 monitor probe + status notification.
# launchd 直接跑 ~/Documents/ 下 script 会 Operation not permitted, 改 wrapper 在 ~ 顶层.
# 注意 cd 进 chunkymonkey 仍 fail (FDA). 改 use python subprocess inheriting fewer perms.

REPO=/Users/dp/Documents/M/stock/chunkymonkey
STATE_DIR="$HOME/.cm_monitor"
mkdir -p "$STATE_DIR"

# Get VM status (gcloud CLI 不受 FDA 限)
VM_STATUS=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a --format='value(status)' 2>/dev/null || echo "UNKNOWN")

# Compare to last known status (state file in ~/.cm_monitor, NOT in repo)
LAST_FILE="$STATE_DIR/last_vm_status"
LAST=$(cat "$LAST_FILE" 2>/dev/null || echo "UNKNOWN")

NOW=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$NOW] VM=$VM_STATUS (last=$LAST)" >> "$STATE_DIR/probe.log"

# Detect state change
if [[ "$VM_STATUS" != "$LAST" ]]; then
    # State changed — surface via macos notification (proactive!)
    osascript -e "display notification \"VM status: $LAST -> $VM_STATUS\" with title \"ChunkyMonkey GCP\" sound name \"Glass\"" 2>/dev/null || true

    # If TERMINATED 且 之前 RUNNING — 可能 preempt, log 详情
    if [[ "$VM_STATUS" == "TERMINATED" && "$LAST" == "RUNNING" ]]; then
        echo "[$NOW] !!! Possible preempt: was RUNNING now TERMINATED" >> "$STATE_DIR/probe.log"
        osascript -e "display notification \"Possible spot preempt! Run cm_resume.sh\" with title \"ChunkyMonkey ALERT\" sound name \"Sosumi\"" 2>/dev/null || true
    fi
fi

echo "$VM_STATUS" > "$LAST_FILE"

# Rotate probe.log if too long
LINES=$(wc -l < "$STATE_DIR/probe.log" 2>/dev/null || echo 0)
if [[ "$LINES" -gt 1000 ]]; then
    tail -n 500 "$STATE_DIR/probe.log" > "$STATE_DIR/probe.log.tmp"
    mv "$STATE_DIR/probe.log.tmp" "$STATE_DIR/probe.log"
fi
