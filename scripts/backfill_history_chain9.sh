#!/usr/bin/env bash
# chain9 — 历史回填 + 数据面收口链 (2026-06-12 总指挥终版)
#
# 前提 (链首自检): dim_trading_calendar 已扩展 (2005-01-04, 七项验证 PASS commit 在档);
#   registry 手术已落 (min_rows 3000 / dc 系 page_limit 5000, eba69422); fina_mainbz
#   attach 修复已落盘; 无其他 writer。
# 跑法: nohup bash scripts/backfill_history_chain9.sh > /tmp/w1_chain9.log 2>&1 &
# 量级: 核心 ~4.6k 调用 (LHB 解锁) + dc_member 重拉 (页数探后定) + 9.5 补丁 ~1.4k
#   + fina_mainbz ~5.3k; 实测吞吐 ~10 调用/分 → 跑进周六 (06-13 休市, 无窗口冲突)。
# 价值排序: LHB gate (T1 8.0 分) → LF V0 原料 (T0 7.8 分) → 增量/转正 → 7 域补丁 → L1 产业链。
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
set -a; [ -f .env ] && source .env; set +a

log(){ echo "[$(date '+%F %T')] [chain9] $*"; }
ALERT_FLAG="/tmp/chunkymonkey_ALERT_chain9.flag"
rm -f "$ALERT_FLAG"
FAILED_STEPS=""

fail_alert(){
    FAILED_STEPS="$FAILED_STEPS | $*"
    echo "$(date '+%F %T') chain9 FAIL: $FAILED_STEPS" > "$ALERT_FLAG"
    osascript -e "display notification \"chain9 失败: $*\" with title \"ChunkyMonkey\"" 2>/dev/null || true
    log "FAIL: $*"
}

run_dom(){  # run_dom <说明> <runner 参数...>
    local desc="$1"; shift
    log "--- $desc ---"
    PYTHONPATH=backend python -m services.data_sources.sync_runner "$@" || fail_alert "$desc"
}

# ---- 0. 前提自检 ----
log "=== chain9 启动: 前提自检 ==="
PYTHONPATH=backend python - <<'PY' || { echo "前提自检 FAIL" > "$ALERT_FLAG"; exit 1; }
import duckdb, sys
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
cal_min = con.execute("SELECT CAST(min(trade_date) AS VARCHAR) FROM dim_trading_calendar").fetchone()[0]
con.close()
assert cal_min <= '2018-01-02', f"日历起点 {cal_min} 未扩展, 拒跑 (防静默 clamp)"
from services.data_sources.sync_runner import load_registry, _domain_spec
reg = load_registry()
for dom in ('daily', 'adj_factor'):
    mr = _domain_spec(reg, dom)['min_rows_per_batch']
    assert mr <= 3000, f"{dom} min_rows={mr} 未修, 2019-2020 段会白跑灌爆队列, 拒跑"
assert _domain_spec(reg, 'dc_member').get('page_limit') == 5000, "dc_member page_limit 未配, 重拉仍截断, 拒跑"
print(f"自检 PASS: 日历起点 {cal_min}, min_rows/page_limit 已修")
PY
# 写锁探测
PYTHONPATH=backend python -c "
import duckdb
duckdb.connect('data/smartmoney.duckdb').close()
duckdb.connect('data/tushare_raw.duckdb').close()" || { fail_alert "写锁被占, 有 writer 在跑"; exit 1; }
log "写锁空闲 OK"

# ---- 1. 探底 (单发实弹, 无重试; 0 行不下结论 — 重复一发对照) ----
log "--- 1. 探底: top_list/top_inst 2018 段 + dc_member 历史地板 ---"
PYTHONPATH=backend python - <<'PY'
import time
from services.data_sources.sync_runner import load_registry, _domain_spec, _adapter
reg = load_registry()
for dom, dates in (("top_list", ("20180102","20190102","20200102")),
                   ("top_inst", ("20180102","20200102")),
                   ("dc_member", ("20220104","20230104","20241231"))):
    spec = _domain_spec(reg, dom); ad = _adapter(spec["source"])
    for d in dates:
        for attempt in (1, 2):  # 0 行重复核证 (网关间歇空响应反例)
            try:
                rows = ad.fetch_raw(spec["api"], trade_date=d, **(spec.get("fixed_params") or {}))
                n = len(rows or [])
                print(f"probe {dom} {d} #{attempt}: {n} rows")
                if n > 0: break
            except Exception as e:
                print(f"probe {dom} {d} #{attempt}: ERROR {str(e)[:90]}")
            time.sleep(3)
        time.sleep(1)
PY

# ---- 2. LHB gate 解锁四件套 (T1 主判决数据) ----
run_dom "2a. top_list 2018-2022 回填 (~1216 日)"  --domain top_list  --backfill --start 20180102 --end 20221231
run_dom "2b. top_inst 2018-2022 回填 (~1216 日 x 分页)" --domain top_inst --backfill --start 20180102 --end 20221231
run_dom "2c. daily 2019-2022 回填 (~973 日)"      --domain daily      --backfill --start 20190101 --end 20221231
run_dom "2d. adj_factor 2019-2022 回填 (~973 日)" --domain adj_factor --backfill --start 20190101 --end 20221231

# ---- 3. 2022 段补窗 (W2/W3 判决实验原料) ----
run_dom "3a. moneyflow 2022 段 (~242 日)" --domain moneyflow --backfill --start 20220104 --end 20221231
run_dom "3b. stk_limit 2022 段"           --domain stk_limit --backfill --start 20220104 --end 20221231
run_dom "3c. stock_st 2022 段"            --domain stock_st  --backfill --start 20220104 --end 20221231

# ---- 4. dc_member 全量重拉 (分页修复后, 截断根治; DELETE+INSERT 全替换无幻影残留) ----
run_dom "4. dc_member 20250102 起全量重拉 (页数由探底定)" --domain dc_member --backfill

# ---- 5. 增量 + 转正自愈 (今天 06-12 各域增量 + 6 域 watermark 补转正; 深夜网关快) ----
run_dom "5. 全域 drain (增量+补洞+转正)" --all-due --drain --max-dates 40

# ---- 6. chain9.5: 7 域转正补丁 ----
run_dom "6a. suspend_d 全段回填 (~1080 日, allow_empty)" --domain suspend_d --backfill
run_dom "6b. dc_index 回填 (20250102 起, 补 20250529 断点)" --domain dc_index --backfill
run_dom "6c. ths_hot 残日重试" --domain ths_hot --drain --max-dates 3

# ---- 7. fina_mainbz 全市场回填 (L1 产业链, attach 修复后首跑, ~5300 调用殿后) ----
run_dom "7. fina_mainbz 全市场 (~5300 by_ts_code)" --domain fina_mainbz --backfill

# ---- 8. 链尾自验闭环 ----
log "--- 8. data-status + 实验 gate + 弹仓 ---"
bash scripts/chunkyctl data-status || true
/Users/dp/.local/bin/sherpa gates --repo . lhb_exit || log "lhb_exit gate 未全过 (见上)"
/Users/dp/.local/bin/sherpa gates --repo . lf_v0 || log "lf_v0 gate 未全过 (G3/G4 需 build_concept_events 重建后才绿, 预期)"
/Users/dp/.local/bin/moth assert --repo . || log "moth 弹仓未全绿 (见上)"

if [[ -n "$FAILED_STEPS" ]]; then
    log "=== chain9 完成但有失败步骤: $FAILED_STEPS ==="
    exit 1
fi
log "=== chain9 全部完成 ==="
