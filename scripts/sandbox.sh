#!/usr/bin/env bash
# sandbox.sh — 隔离探索区管理 (用完直接删, 不污染主代码/文档)
# 契约见 sandbox/README.md。立 2026-06-17 (用户根治: 探索散进主代码=反复污染根因)。
set -euo pipefail
cd "$(dirname "$0")/.."
SB="sandbox"
cmd="${1:-list}"
case "$cmd" in
  new)
    name="${2:?usage: sandbox.sh new <exp-name>}"
    mkdir -p "$SB/$name/results"
    [ -f "$SB/$name/notes.md" ] || printf '# %s 探索笔记 (sandbox, 用完删; 结论写 experiment_store)\n' "$name" > "$SB/$name/notes.md"
    if [ ! -f "$SB/$name/probe.py" ]; then
      cat > "$SB/$name/probe.py" <<PYEOF
"""$name 探索 (sandbox, 用完删)。
跑: set -a; source .env; set +a; PYTHONPATH=backend .venv/bin/python sandbox/$name/probe.py
"""
from services.sandbox_guard import enable_sandbox_guard, read_only_main, sandbox_scratch

enable_sandbox_guard()  # 此后 read_write 打开主 6 库 = raise (边界水密硬门)

# 读主库 (只读唯一正路):  con = read_only_main("market")
# 写探索数据 (per-exp):    scr = sandbox_scratch("$name")
# 验证走 harness:          from services.experiment_harness import tradability_verdict, kpi_verdict, anomaly_verdict
# walk-forward OOS:        from services.portfolio_walk_forward.oos_ic import cross_sectional_ic
# 含成本回测:              from services.portfolio_execbacktest import run_execution_backtest, ExecConfig
# 裁决留档 (唯一跨删存活): from services.experiment_store import open_store, record_verdict
PYEOF
    fi
    echo "created $SB/$name/ (probe.py 模板已带 guard + notes.md + results/); scratch=$SB/$name/scratch.duckdb (per-exp)"
    ;;
  list)
    echo "探索 (sandbox/, gitignored 用完删):"
    ls -1d "$SB"/*/ 2>/dev/null | sed 's#^#  #' || echo "  (空)"
    ;;
  wipe)
    name="${2:?usage: sandbox.sh wipe <exp-name>}"
    rm -rf "${SB:?}/${name:?}"
    echo "wiped $SB/$name (含 per-exp scratch) — 主代码/文档/git 0 残留 (裁决若已 record_verdict 仍在 experiment_store)"
    ;;
  wipe-all)
    find "$SB" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} + 2>/dev/null || true
    echo "wiped all sandbox (留 README) — 主代码/文档/git 0 残留"
    ;;
  check)
    # 隔离门: 实验室产物只留实验室 — C1 backend引用sandbox / C2 控制面嵌未promote实验结果 / C3 探索runner漏主脚本
    # 完整门 = check_sandbox_isolation.py (覆盖 C1+C2+C3); 不可用时退化到内联 C3 grep
    if PYTHONPATH=backend python backend/scripts/check_sandbox_isolation.py; then
      :
    elif [ $? -eq 127 ] || [ ! -f backend/scripts/check_sandbox_isolation.py ]; then
      n=$( { ls backend/scripts/experiment_*.py backend/scripts/analyze_*.py 2>/dev/null || true; } | wc -l | tr -d ' ')
      [ "$n" = "0" ] && echo "[OK fallback] backend/scripts 0 探索 runner" || { echo "[FAIL] $n 探索 runner 漏进 backend/scripts/"; exit 1; }
    else
      echo "[FAIL] 沙盒隔离门未过 (见上)"; exit 1
    fi
    ;;
  *)
    echo "usage: sandbox.sh {new <name>|list|wipe <name>|wipe-all|check}"; exit 1
    ;;
esac
