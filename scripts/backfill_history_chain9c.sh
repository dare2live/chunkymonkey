#!/usr/bin/env bash
# chain9c — dc_member 薄日重拉 + LF V0 判决链 (2026-06-13 立项, 配额重置后发射)
#
# 前情: 06-13 网关日配额耗尽 (~10k 调用), 12 薄日重拉中断。薄日 = 行数 < 邻域中位 80%
#   (概念数筛法抓不到的成员级残缺), 含 20260518-26 连 6 日段 — 平滑窗 3 跨不过,
#   是残余 churn 790/日的主源。已知: 20251128 类薄日重拉可恢复 (8000→66,774),
#   20250110 类是 vendor 端薄 (重拉同值) — 本链重拉后按实测裁决 g4-churn。
# 跑法: nohup bash scripts/backfill_history_chain9c.sh > /tmp/w1_chain9c.log 2>&1 &
# 量级: 12 日 x ~18 页 ≈ 250 调用 + 重建 + gate + (GO 则) LF 实验, 全程 ~30 分钟。
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
set -a; [ -f .env ] && source .env; set +a

log(){ echo "[$(date '+%F %T')] [chain9c] $*"; }
ALERT_FLAG="/tmp/chunkymonkey_ALERT_chain9c.flag"
rm -f "$ALERT_FLAG"
FAILED_STEPS=""
fail_alert(){
    FAILED_STEPS="$FAILED_STEPS | $*"
    echo "$(date '+%F %T') chain9c FAIL: $FAILED_STEPS" > "$ALERT_FLAG"
    osascript -e "display notification \"chain9c 失败: $*\" with title \"ChunkyMonkey\"" 2>/dev/null || true
    log "FAIL: $*"
}

# ---- 0. 自检: 配额是否已重置 (单发探针) + 写锁 ----
log "=== chain9c 自检 ==="
PYTHONPATH=backend python - <<'PY' || { fail_alert "配额未重置或网关不可用, 拒跑"; exit 1; }
from services.data_sources.sync_runner import load_registry, _domain_spec, _adapter
reg = load_registry(); spec = _domain_spec(reg, "dc_member"); ad = _adapter(spec["source"])
rows = ad.fetch_raw(spec["api"], trade_date="20260612", limit=100, offset=0)
n = len(rows or [])
assert n > 0, f"探针 0 行 (配额未重置或网关坏), 拒跑"
print(f"配额探针 OK: {n} 行")
PY
PYTHONPATH=backend python -c "
import duckdb
duckdb.connect('data/tushare_raw.duckdb').close()" || { fail_alert "写锁被占"; exit 1; }

# ---- 1. 12 薄日定点重拉 (行数 < 邻域中位 80% 清单, 2026-06-13 实测) ----
for d in 20250106 20250424 20250618 20250619 20251029 20260409 20260518 20260520 20260521 20260522 20260525 20260526; do
    log "--- 1. dc_member 薄日 $d 重拉 ---"
    PYTHONPATH=backend python -m services.data_sources.sync_runner --domain dc_member --backfill --start "$d" --end "$d" || fail_alert "1. 薄日 $d"
done

# ---- 2. 概念事件重建 (平滑窗 3, prereg 修订 2 语义) ----
log "--- 2. 概念事件重建 ---"
PYTHONPATH=backend python backend/scripts/build_concept_events.py --source raw --rebuild || { fail_alert "2. 重建"; exit 1; }

# ---- 3. 复测 + gate ----
log "--- 3. flicker/churn 复测 + lf_v0 gate ---"
PYTHONPATH=backend python - <<'PY'
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute("""
SELECT count(*) FILTER (WHERE event_type IN ('concept_born','concept_dead')) * 1.0 / count(DISTINCT event_date),
       count(*) FILTER (WHERE event_type IN ('member_add','member_drop')) * 1.0 / count(DISTINCT event_date)
FROM fact_concept_event WHERE as_of_mode='reconstructed'""").fetchone()
print(f"BORN+DEAD/日={r[0]:.2f} (gate <5) | churn/日={r[1]:.1f} (gate <=240)")
con.close()
PY
if /Users/dp/.local/bin/sherpa gates --repo . lf_v0; then
    # ---- 4. LF V0 主判决 (gate GO 才到这里; 实验自身还会再跑一次 gate 硬门) ----
    log "--- 4. LF V0 主判决 ---"
    PYTHONPATH=backend python backend/scripts/experiment_lf_v0.py || fail_alert "4. LF V0 实验"
    osascript -e 'display notification "LF V0 判决已出, 看 analysis/lf_v0_verdict_*.json" with title "ChunkyMonkey alpha"' 2>/dev/null || true
else
    log "lf_v0 gate NO-GO — 复测数字在上, 等总指挥裁决 g4-churn (重拉后仍超 = vendor 端薄日, 240 基线来自截断面板需重立法), 不自动放宽"
    fail_alert "3. lf_v0 gate NO-GO (按设计停, 人工裁决)"
fi

if [ -n "$FAILED_STEPS" ]; then
    log "=== chain9c 完成但有失败步骤: $FAILED_STEPS ==="
    exit 1
fi
log "=== chain9c 全部完成 ==="
osascript -e 'display notification "chain9c 完成" with title "ChunkyMonkey"' 2>/dev/null || true
