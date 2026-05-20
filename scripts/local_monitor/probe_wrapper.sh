#!/usr/bin/env bash
# FDA-safe wrapper for phase5 monitor probe + status notification.
# Fix: launchd context PATH 不含 /opt/homebrew/bin (gcloud here), 显式 export.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin:$PATH"

REPO=/Users/dp/Documents/M/stock/chunkymonkey
STATE_DIR="$HOME/.cm_monitor"
mkdir -p "$STATE_DIR"

VM_STATUS=$(gcloud compute instances describe chunkymonkey-optuna --zone=us-central1-a --format='value(status)' 2>/dev/null || echo "UNKNOWN")

LAST_FILE="$STATE_DIR/last_vm_status"
LAST=$(cat "$LAST_FILE" 2>/dev/null || echo "UNKNOWN")

NOW=$(date '+%Y-%m-%d %H:%M:%S')
echo "[$NOW] VM=$VM_STATUS (last=$LAST)" >> "$STATE_DIR/probe.log"

if [[ "$VM_STATUS" != "$LAST" ]]; then
    osascript -e "display notification \"VM status: $LAST -> $VM_STATUS\" with title \"ChunkyMonkey GCP\" sound name \"Glass\"" 2>/dev/null || true
    if [[ "$VM_STATUS" == "TERMINATED" && "$LAST" == "RUNNING" ]]; then
        echo "[$NOW] !!! Possible preempt: was RUNNING now TERMINATED" >> "$STATE_DIR/probe.log"
        osascript -e "display notification \"Possible spot preempt! Run cm_resume.sh\" with title \"ChunkyMonkey ALERT\" sound name \"Sosumi\"" 2>/dev/null || true
    fi
fi
echo "$VM_STATUS" > "$LAST_FILE"

LINES=$(wc -l < "$STATE_DIR/probe.log" 2>/dev/null || echo 0)
if [[ "$LINES" -gt 1000 ]]; then
    tail -n 500 "$STATE_DIR/probe.log" > "$STATE_DIR/probe.log.tmp"
    mv "$STATE_DIR/probe.log.tmp" "$STATE_DIR/probe.log"
fi
