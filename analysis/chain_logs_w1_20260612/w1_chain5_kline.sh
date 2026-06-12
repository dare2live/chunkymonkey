#!/bin/bash
# Chain5: K 线主战场回填 (daily + daily_basic; adj_factor 在 chain4)
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
while pgrep -f "sync_runner --domain" > /dev/null || pgrep -f "w1_chain[1234]" > /dev/null; do sleep 120; done
echo "=== chain4 完成, chain5 K线三件套回填启动 $(date '+%T') ==="
for d in daily daily_basic; do
    echo "--- chain5 域 $d $(date '+%T') ---"
    PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain "$d" --backfill 2>&1 | tail -3
done
echo "=== chain5 完成 $(date '+%T') ==="
