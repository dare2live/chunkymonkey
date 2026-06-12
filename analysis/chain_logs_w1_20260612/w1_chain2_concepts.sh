#!/bin/bash
# Chain2: 概念域历史回填 — 等 chain1 (W1 回填链) 完成后串行跑 (API 限频全局共享)
set -a; source /Users/dp/Documents/M/stock/chunkymonkey/.env; set +a
cd /Users/dp/Documents/M/stock/chunkymonkey
PY=.venv/bin/python

# 等 chain1 的全部 sync_runner 退出
while pgrep -f "sync_runner --domain" > /dev/null; do sleep 60; done
echo "=== chain1 完成, 启动概念域历史回填 $(date '+%T') ==="

for d in dc_member dc_index moneyflow_ind_dc limit_cpt_list; do
  echo "=== 回填 $d $(date '+%T') ==="
  PYTHONPATH=backend $PY -m services.data_sources.sync_runner --domain "$d" --backfill 2>&1 | tail -3
done

# ths_member 全量 (394 概念循环, 带 in_date/out_date 可 PIT) — 一次性, 存 parquet 供入库评估
$PY - <<'PYEOF'
import os, time
import pandas as pd, tushare as ts
token=os.environ["TUSHARE_TOKEN"]
pro=ts.pro_api(token); pro._DataApi__token=token; pro._DataApi__http_url=os.environ["TUSHARE_HTTP_URL"]
idx = pro.ths_index(type="N")
frames=[]
for code in idx["ts_code"].tolist():
    for t in range(2):
        try:
            d = pro.ths_member(ts_code=code)
            if d is not None and len(d): frames.append(d); break
        except Exception: pass
        time.sleep(1.5)
    time.sleep(0.4)
if frames:
    out = pd.concat(frames, ignore_index=True)
    os.makedirs("data/concept_snapshots/_full", exist_ok=True)
    out.to_parquet("data/concept_snapshots/_full/ths_member_full.parquet")
    print(f"ths_member 全量: {len(out)} 行, out_date 非空 {out.out_date.notna().sum()}")
PYEOF
echo "=== chain2 概念域全部完成 $(date '+%T') ==="
