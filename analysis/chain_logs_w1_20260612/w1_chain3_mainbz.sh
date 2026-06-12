#!/bin/bash
# Chain3: fina_mainbz 全市场首期回填 (排在 chain2 概念域之后, API 限频串行)
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
while pgrep -f "sync_runner --domain" > /dev/null || pgrep -f "w1_chain2" > /dev/null; do sleep 60; done
echo "=== chain2 完成, 启动 fina_mainbz 全市场回填 $(date '+%T') ==="
PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain fina_mainbz --backfill 2>&1 | tail -5
echo "=== chain3 完成 $(date '+%T') ==="
.venv/bin/python - <<'PYEOF'
import duckdb
con = duckdb.connect("data/tushare_raw.duckdb", read_only=True)
try:
    n, codes, items = con.execute("SELECT COUNT(*), COUNT(DISTINCT ts_code), COUNT(DISTINCT bz_item) FROM raw_tushare_fina_mainbz").fetchone()
    print(f"fina_mainbz: {n} 行 / {codes} 股 / {items} 个产品项")
except Exception as e:
    print("ERR:", e)
con.close()
PYEOF
