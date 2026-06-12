#!/bin/bash
# Chain4: 旧源退役批 6 域回填 (排在 chain3 之后, gateway 并发 2 串行纪律)
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
while pgrep -f "sync_runner --domain" > /dev/null || pgrep -f "w1_chain[123]" > /dev/null; do sleep 120; done
echo "=== chain3 完成, chain4 旧源退役批启动 $(date '+%T') ==="
# 顺序: 小表先行快速见效 (北向1行/日, 热榜2024起), 大回填殿后 (龙虎榜/复权因子 2005 起)
for d in moneyflow_hsgt ths_hot dividend report_rc top_list top_inst adj_factor; do
    echo "--- chain4 域 $d $(date '+%T') ---"
    PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain "$d" --backfill 2>&1 | tail -3
done
echo "=== chain4 完成 $(date '+%T') ==="
