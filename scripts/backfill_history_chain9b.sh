#!/usr/bin/env bash
# chain9b — chain9 善后 + LHB 第一判决链 (2026-06-13 总指挥)
#
# chain9 死因对账 (w1_chain9.log 05:48-05:50): 6a/6b/7 三步死于首批类型推断
#   ConversionException, 而加宽修复 commit 于 05:59:35 — 比三步晚 10 分钟。
#   现在重跑 = 自愈 (修复正是从这三个 traceback 提炼)。
# 工单: (1) daily_basic 2020-2022 回填 (~689 日, LHB g5 关键路径) → (2) gate GO 即跑
#   LHB 判决实验 (串行链内 read-only, 无写锁冲突) → (3) dc_member 6 凹陷日定点重拉
#   (20250106/20251128 行数恰 8000 = 旧硬截断签名; 其余 4 日部分拉取; 同月邻日 418-559
#   概念数证明 vendor 有数据) → (4) top_inst 缺日 drain → (5) 加宽自愈三域 → (6) 链尾自验。
# 不做: ths_hot 20240312 (双夜实证 vendor 真缺, 结案, 不再烧调用)。
# 跑法: nohup bash scripts/backfill_history_chain9b.sh > /tmp/w1_chain9b.log 2>&1 &
# 量级: daily_basic ~689 + dc_member ~80 页 + top_inst ~16 + suspend_d ~1080 (allow_empty)
#   + dc_index ~250 + fina_mainbz ~5300 by_ts_code; 实测吞吐 ~10 调用/分 → 全链 ~12h,
#   但 LHB 判决在前 ~80 分钟内出。
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
set -a; [ -f .env ] && source .env; set +a

log(){ echo "[$(date '+%F %T')] [chain9b] $*"; }
ALERT_FLAG="/tmp/chunkymonkey_ALERT_chain9b.flag"
rm -f "$ALERT_FLAG"
FAILED_STEPS=""

fail_alert(){
    FAILED_STEPS="$FAILED_STEPS | $*"
    echo "$(date '+%F %T') chain9b FAIL: $FAILED_STEPS" > "$ALERT_FLAG"
    osascript -e "display notification \"chain9b 失败: $*\" with title \"ChunkyMonkey\"" 2>/dev/null || true
    log "FAIL: $*"
}

run_dom(){  # run_dom <说明> <runner 参数...>
    local desc="$1"; shift
    log "--- $desc ---"
    PYTHONPATH=backend python -m services.data_sources.sync_runner "$@" || fail_alert "$desc"
}

# ---- 0. 前提自检 ----
log "=== chain9b 启动: 前提自检 ==="
PYTHONPATH=backend python - <<'PY' || { echo "前提自检 FAIL" > "$ALERT_FLAG"; exit 1; }
import sys
# 加宽修复必须在盘 (chain9 三步死因, 没它 5/6a/6b/7 原样复败)
src = open('backend/services/data_sources/sync_runner.py', encoding='utf-8').read()
assert '首批类型推断加宽' in src, "type-widening 修复不在盘, 6a/6b/7 会原样复败, 拒跑"
from services.data_sources.sync_runner import load_registry, _domain_spec
reg = load_registry()
spec = _domain_spec(reg, 'daily_basic')
assert spec['data_start'] == '20200102', f"daily_basic data_start={spec['data_start']} != 20200102, 拒跑"
assert _domain_spec(reg, 'dc_member').get('page_limit') == 5000, "dc_member page_limit 未配, 重拉仍截断, 拒跑"
print("自检 PASS: 加宽修复在盘 + daily_basic 起点 + dc_member page_limit")
PY
# prereg 一致性 (实验跑前最后一道)
PYTHONPATH=backend python backend/scripts/experiment_lhb_exit.py --check-prereg || { fail_alert "prereg 一致性 FAIL, 实验不可跑"; exit 1; }
# 写锁探测
PYTHONPATH=backend python -c "
import duckdb
duckdb.connect('data/tushare_raw.duckdb').close()" || { fail_alert "写锁被占, 有 writer 在跑"; exit 1; }
log "写锁空闲 OK"

# ---- 1. daily_basic 2020-2022 回填 (LHB g5 关键路径, 混淆臂市值桶原料) ----
run_dom "1. daily_basic 20200102-20221107 回填 (~689 日)" --domain daily_basic --backfill --start 20200102 --end 20221107

# ---- 2. LHB 第一判决 (gate 硬门内置; NO-GO 即退出码 1 → 告警) ----
log "--- 2. LHB 退出实验主判决 (prereg FROZEN 2026-06-12 + 修订1) ---"
PYTHONPATH=backend python backend/scripts/experiment_lhb_exit.py || fail_alert "2. LHB 判决实验 (gate NO-GO 或运行错误)"
osascript -e 'display notification "LHB 判决已出, 看 analysis/lhb_exit_verdict_*.json" with title "ChunkyMonkey alpha"' 2>/dev/null || true

# ---- 3. dc_member 凹陷日定点重拉 (6 日, MERGE on grain 覆盖残缺行) ----
for d in 20250106 20250110 20250123 20250211 20251029 20251128; do
    run_dom "3. dc_member 凹陷日 $d 重拉" --domain dc_member --backfill --start "$d" --end "$d"
done

# ---- 4. top_inst 缺日 drain (vs top_list 差 8 日) ----
run_dom "4. top_inst 缺日 drain" --domain top_inst --drain --max-dates 12

# ---- 5. 类型加宽自愈三域 (chain9 6a/6b/7 复跑) ----
run_dom "5a. suspend_d 全段回填 (~1080 日, allow_empty)" --domain suspend_d --backfill
run_dom "5b. dc_index 回填 (补 20250530 起断点)" --domain dc_index --backfill
run_dom "5c. fina_mainbz 全市场 (~5300 by_ts_code)" --domain fina_mainbz --backfill

# ---- 6. 链尾自验 ----
log "--- 6. data-status + 凹陷日复查 + 实验 gate + 弹仓 ---"
PYTHONPATH=backend python backend/scripts/data_migration_status.py 2>/dev/null || true
PYTHONPATH=backend python - <<'PY' || fail_alert "6. 凹陷日复查未全恢复"
import duckdb
con = duckdb.connect('data/tushare_raw.duckdb', read_only=True)
rows = con.execute("""
WITH daily AS (
  SELECT trade_date, count(DISTINCT ts_code) AS n_concepts FROM raw_tushare_dc_member GROUP BY 1
), med AS (
  SELECT substr(trade_date,1,6) AS ym, median(n_concepts) AS med_c FROM daily GROUP BY 1)
SELECT d.trade_date, d.n_concepts, m.med_c FROM daily d
JOIN med m ON substr(d.trade_date,1,6)=m.ym WHERE d.n_concepts < 0.6*m.med_c ORDER BY 1
""").fetchall()
con.close()
print("凹陷日残留:", rows if rows else "无 (全恢复)")
assert not rows, f"凹陷日未全恢复: {rows}"
PY
# 概念事件重建 (dc_member 修复后 fact_concept_event 必须重建, 否则 LF V0 用残缺事件;
# 写 smartmoney.duckdb, 与 raw 回填写锁正交)
log "--- 6.5 概念事件重建 (凹陷日修复后) ---"
PYTHONPATH=backend python backend/scripts/build_concept_events.py --source raw --rebuild || fail_alert "6.5 概念事件重建"
# LF V0 G4 重标定证据: 重建后干净基线 (BORN/DEAD 与 churn 实测, 供 prereg 修订)
PYTHONPATH=backend python - <<'PY' || true
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
m = con.execute("""
WITH e AS (SELECT event_date, count(*) AS c FROM fact_concept_event
           WHERE event_type IN ('member_add','member_drop') AND source='raw' GROUP BY 1)
SELECT round(avg(c),1), median(c), max(c), count(*) FROM e
""").fetchone()
print(f"G4 重标定基线 (修复+重建后): churn avg={m[0]} median={m[1]} max={m[2]} event_dates={m[3]}")
b = con.execute("""
SELECT event_type, count(*) FROM fact_concept_event
WHERE event_type IN ('concept_born','concept_dead') AND source='raw' GROUP BY 1 ORDER BY 1
""").fetchall()
print(f"BORN/DEAD 总量: {b}")
con.close()
PY
/Users/dp/.local/bin/sherpa gates --repo . lhb_exit || log "WARN: lhb_exit gate 复检非 GO (判决已出则仅记录)"
/Users/dp/.local/bin/sherpa gates --repo . lf_v0 || log "WARN: lf_v0 gate 仍 NO-GO (G3/G4 待重标定, 已知)"
/Users/dp/.local/bin/moth assert 2>/dev/null || log "WARN: moth assert 非全绿, 看输出"

if [ -n "$FAILED_STEPS" ]; then
    log "=== chain9b 完成但有失败步骤: $FAILED_STEPS ==="
    exit 1
fi
log "=== chain9b 全部完成 ==="
osascript -e 'display notification "chain9b 全链完成" with title "ChunkyMonkey"' 2>/dev/null || true
