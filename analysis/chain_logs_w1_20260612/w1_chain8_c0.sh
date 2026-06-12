#!/bin/bash
# Chain8: C0 筹码口径审计 (等 chain7 释放 raw 锁)
cd /Users/dp/Documents/M/stock/chunkymonkey
while pgrep -f "sync_runner --domain|sync_runner.*--drain|w1_chain7" > /dev/null; do sleep 60; done
echo "=== chain7 完成, C0 审计启动 $(date '+%T') ==="
PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_c0_cyq_audit.py 2>&1 | tail -25
echo "=== C0 完成 $(date '+%T') ==="
