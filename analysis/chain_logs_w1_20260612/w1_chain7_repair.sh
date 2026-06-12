#!/bin/bash
# Chain7: 全域 drain 补缺转正 (chain4 部分失败域) + top_inst/fina_mainbz 大回填
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
echo "=== chain7 启动 $(date '+%T') ==="
echo "--- 1. 全域 drain (gap 补缺 + watermark 转正; 兼明天 daily_update Step 2.95 预演) ---"
PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --all-due --drain 2>&1 | tail -30
echo "--- 2. top_inst 全史回填 (2005 起, 分页) $(date '+%T') ---"
PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain top_inst --backfill 2>&1 | tail -3
echo "--- 3. fina_mainbz 全市场回填 (by_ts_code ~5300 调用) $(date '+%T') ---"
PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner --domain fina_mainbz --backfill 2>&1 | tail -3
echo "=== chain7 完成 $(date '+%T') ==="
PYTHONPATH=backend .venv/bin/python backend/scripts/data_migration_status.py | head -4
