#!/bin/bash
# Chain6: 筹码胜率 cyq_perf 回填 (排 chain5 后) + 概念事件 reconstructed 首跑 + E1 数据就绪通知
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
while pgrep -f "sync_runner --domain" > /dev/null || pgrep -f "w1_chain[45]" > /dev/null; do sleep 120; done
echo "=== chain5 完成, chain6 cyq_perf 回填启动 $(date '+%T') ==="
PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain cyq_perf --backfill 2>&1 | tail -3
echo "=== 概念事件 reconstructed 首跑 (raw 锁已释放) ==="
PYTHONPATH=backend .venv/bin/python backend/scripts/build_concept_events.py --source both 2>&1 | tail -8
echo "=== chain6 完成 $(date '+%T') — E1/筹码 alpha 数据全就绪 ==="
