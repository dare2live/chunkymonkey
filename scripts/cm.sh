#!/usr/bin/env bash
# cm — ChunkyMonkey 单一 CLI entry (criteria #7 UI/UX P0c).
#
# Usage:
#   bash scripts/cm.sh <subcommand> [args]
#   alias cm='bash /Users/dp/Documents/M/stock/chunkymonkey/scripts/cm.sh'
#
# Subcommands:
#   today      — 今日推荐 + 当前持仓 + sync status + KPI 表 (md render)
#   holdings   — 当前持仓 + 盈亏 (paper_sim 实测)
#   kpi        — KPI 矩阵 (mart_paper_sim_kpi all runs)
#   sync       — 各 source health (mart_data_source_watermark)
#   retrain    — retrain 触发条件预览 (alpha_decay + IS_QUARTER_START)
#   promote    — list P3 PASS candidate
#   resume     — alias bash scripts/cm_resume.sh (中断恢复)
#   status     — 综合状态 (session_snapshot)
#   cache      — paper_sim cache hit-rate + lineage chain stats (criteria #10 incremental mgmt)
#   help       — 显示帮助

set -e
cd "$(dirname "$0")/.."

CMD="${1:-help}"
shift 2>/dev/null || true

REPO_ROOT="$(pwd)"
DB_PATH="$REPO_ROOT/data/smartmoney.duckdb"

cm_help() {
    cat <<EOF
ChunkyMonkey cm CLI (单一 entry)

Usage:
  cm today              今日推荐 + 持仓 + sync + KPI
  cm holdings           当前持仓
  cm kpi [--limit N]    KPI 矩阵 (default 10 latest)
  cm sync               sync status (各 source)
  cm retrain --dry      retrain trigger 条件预览
  cm promote            P3 PASS candidate list
  cm resume             中断恢复 (bash cm_resume.sh)
  cm status             综合状态 (session_snapshot)
  cm cache              paper_sim cache hit-rate + lineage chain (criteria #10)
  cm impact [<id>]      param impact curve (Δ KPI vs param_diff, default variant=champion)
  cm help               本帮助

Resilience:
  cm install            install cron + launchd (一次性)
  cm install --status   verify install state
EOF
}

cm_today() {
    LATEST=$(ls -t "$REPO_ROOT/data/reports/daily_"*.json 2>/dev/null | head -1)
    if [ -z "$LATEST" ]; then
        echo "[cm today] 无 daily_YYYYMMDD.json — 先跑 bash scripts/daily_update.sh"
        return 1
    fi
    echo "[cm today] render markdown from: $LATEST"
    PYTHONPATH=backend python "$REPO_ROOT/backend/scripts/gen_report.py" \
        --format markdown --input "$LATEST" --output /dev/stdout
}

cm_holdings() {
    PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('$DB_PATH', read_only=True)
try:
    r = con.execute(\"SELECT stock_code, formula_id, open_date, days_held, optimal_hp, expected_target_pct, pnl_pct FROM fact_paper_sim_position WHERE is_open = TRUE ORDER BY open_date DESC LIMIT 30\").fetchall()
    if not r:
        print('当前 0 持仓 (paper_sim 全 closed)')
    else:
        print(f'{\"stock\":<10} {\"formula\":<16} {\"open\":<12} {\"days\":>5} {\"hp\":>4} {\"target\":>7} {\"pnl\":>7}')
        for x in r:
            print(f'{x[0]:<10} {(x[1] or \"\")[:16]:<16} {x[2]:<12} {x[3] or 0:>5} {x[4] or 0:>4} {(x[5] or 0)*100:>6.2f}% {(x[6] or 0)*100:>6.2f}%')
except Exception as e: print(f'err: {e}')
con.close()
"
}

cm_kpi() {
    LIMIT=10
    [[ "$1" == "--limit" ]] && LIMIT="$2"
    PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('$DB_PATH', read_only=True)
r = con.execute(f\"SELECT sim_run_id, annual_return, max_dd, sharpe, monthly_win_rate, annual_turnover, avg_holding_days, user_criteria_pass, built_at FROM mart_paper_sim_kpi ORDER BY built_at DESC LIMIT $LIMIT\").fetchall()
print(f'{\"sim_run_id (truncated)\":<45} {\"ann\":>8} {\"dd\":>8} {\"sh\":>6} {\"win\":>6} {\"turn\":>7} {\"hold\":>5} {\"pass\":>5}')
print('-' * 105)
for x in r:
    sr=(x[0] or '')[:42]; ann=x[1] or 0; dd=x[2] or 0; sh=x[3] or 0; mw=x[4] or 0; tn=x[5] or 0; ah=x[6] or 0
    print(f'{sr:<45} {ann:>7.1%} {dd:>7.1%} {sh:>6.2f} {mw:>5.1%} {tn:>6.1f}x {ah:>4.1f}d {str(x[7]):>5}')
con.close()
"
}

cm_sync() {
    PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('$DB_PATH', read_only=True)
try:
    r = con.execute(\"\"\"SELECT source_name, source_tier, last_data_date, consecutive_failures, fallback_active FROM mart_data_source_watermark ORDER BY source_tier, source_name\"\"\").fetchall()
    print(f'{\"tag\":<6} {\"source\":<28} {\"tier\":>4} {\"last_data\":<13} {\"fail\":>4} {\"fallback\":>8}')
    print('-' * 75)
    for x in r:
        src=x[0] or ''; tier=x[1] or 0; ld=str(x[2] or '')[:13]; fc=x[3] or 0; fb=x[4]
        tag = 'RED' if fc >= 3 or not ld else ('YELLOW' if fc > 0 or fb else 'GREEN')
        print(f'{tag:<6} {src:<28} {tier:>4} {ld:<13} {fc:>4} {str(fb):>8}')
except Exception as e: print(f'err: {e}')
con.close()
"
}

cm_retrain() {
    DRY=0
    [[ "$1" == "--dry" ]] && DRY=1
    if [ "$DRY" = "1" ]; then
        echo "=== retrain trigger 条件预览 (dry-run) ==="
        DOM=$(date +%-d)
        MONTH=$(date +%-m)
        DOW=$(date +%u)
        IS_QUARTER_START=0
        [[ "$DOM" == "1" && ( "$MONTH" == "1" || "$MONTH" == "4" || "$MONTH" == "7" || "$MONTH" == "10" ) ]] && IS_QUARTER_START=1
        echo "  DOM=$DOM MONTH=$MONTH DOW=$DOW"
        echo "  IS_QUARTER_START=$IS_QUARTER_START (1=quarterly fallback fire)"
        echo ""
        echo "  Trigger 1 (event-driven): alpha_decay 检测 (rank_ic 最近 4 windows 连降)"
        echo "  Trigger 2 (quarterly): DOM=1 of Jan/Apr/Jul/Oct"
        echo ""
        echo "  先登记 job plan:"
        echo "    scripts/chunkyctl jobs --family model_training --model-id lgbm_<yyyymmdd> \\"
        echo "      --input-snapshot smartmoney.duckdb@<date> --objective '<why>' --rollback-plan '<stop/discard plan>' \\"
        echo "      --gate-evidence leakage_audit=<artifact> --gate-evidence train_log_integrity=<artifact> --gate-evidence phase4_gate=<artifact>"
        echo "  手工触发:"
        echo "    先确认 job plan 的 required gates / artifact_contracts，再运行对应训练脚本；cm 不自动触发 retrain。"
        return
    fi
    echo "[cm retrain] 当前不支持自动 trigger, 用 --dry 看条件"
    echo "先登记: scripts/chunkyctl jobs --family model_training --model-id lgbm_<yyyymmdd> --input-snapshot smartmoney.duckdb@<date> --objective '<why>' --rollback-plan '<stop/discard plan>' --gate-evidence leakage_audit=<artifact> --gate-evidence train_log_integrity=<artifact> --gate-evidence phase4_gate=<artifact>"
}

cm_promote() {
    PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('$DB_PATH', read_only=True)
try:
    r = con.execute(\"\"\"SELECT sim_run_id, annual_return, max_dd, sharpe, monthly_win_rate, user_criteria_pass FROM mart_paper_sim_kpi WHERE user_criteria_pass = TRUE ORDER BY sharpe DESC NULLS LAST LIMIT 10\"\"\").fetchall()
    if not r:
        print('当前 0 P3 PASS candidate (user_criteria_pass = TRUE)')
    else:
        print(f'{\"sim_run_id\":<40} {\"ann\":>8} {\"dd\":>8} {\"sh\":>6} {\"win\":>6}')
        for x in r:
            sr=(x[0] or '')[:40]; ann=x[1] or 0; dd=x[2] or 0; sh=x[3] or 0; mw=x[4] or 0
            print(f'{sr:<40} {ann:>7.1%} {dd:>7.1%} {sh:>6.2f} {mw:>5.1%}')
except Exception as e: print(f'err: {e}')
con.close()
"
}

cm_resume() {
    bash "$REPO_ROOT/scripts/cm_resume.sh"
}

cm_status() {
    bash "$REPO_ROOT/scripts/session_snapshot.sh" > /dev/null 2>&1 || true
    cat "$REPO_ROOT/SESSION_HANDOFF.md" | head -50
}

cm_install() {
    bash "$REPO_ROOT/scripts/install_resilience.sh" "$@"
}

cm_impact() {
    # criteria #10 P1: param impact curve
    if [ -z "$1" ]; then
        PYTHONPATH=backend python "$REPO_ROOT/backend/scripts/param_impact_curve.py" --variant champion
    else
        PYTHONPATH=backend python "$REPO_ROOT/backend/scripts/param_impact_curve.py" --sim-run-id "$1"
    fi
}

cm_cache() {
    # criteria #10 incremental mgmt: paper_sim cache + lineage stats
    PYTHONPATH=backend python "$REPO_ROOT/backend/scripts/incremental_cache_status.py"
}

case "$CMD" in
    today|t)        cm_today ;;
    holdings|h)     cm_holdings ;;
    kpi|k)          cm_kpi "$@" ;;
    sync|s)         cm_sync ;;
    retrain|r)      cm_retrain "$@" ;;
    promote|p)      cm_promote ;;
    resume)         cm_resume ;;
    status|st)      cm_status ;;
    cache|c)        cm_cache ;;
    impact)         cm_impact "$@" ;;
    install|i)      cm_install "$@" ;;
    help|--help|-h|"") cm_help ;;
    *)              echo "[cm] unknown subcommand: $CMD"; cm_help; exit 1 ;;
esac
