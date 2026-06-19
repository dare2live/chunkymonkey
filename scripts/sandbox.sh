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
    echo "created $SB/$name/  (脚本放这 + notes.md + results/); scratch DB = $SB/scratch.duckdb"
    ;;
  list)
    echo "探索 (sandbox/, gitignored 用完删):"
    ls -1d "$SB"/*/ 2>/dev/null | sed 's#^#  #' || echo "  (空)"
    ;;
  wipe)
    name="${2:?usage: sandbox.sh wipe <exp-name>}"
    rm -rf "${SB:?}/${name:?}"
    echo "wiped $SB/$name — 主代码/文档/git 0 残留 (裁决若已 record_verdict 仍在 experiment_store)"
    ;;
  wipe-all)
    find "$SB" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} + 2>/dev/null || true
    echo "wiped all sandbox (留 README) — 主代码/文档/git 0 残留"
    ;;
  check)
    # 漏检: 探索 runner 漏进 backend/scripts? (沙盒门本地同检, 同 moth exploration-isolated)
    n=$(ls backend/scripts/experiment_*.py backend/scripts/analyze_*.py 2>/dev/null | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then
      echo "[OK] backend/scripts 0 探索 runner (探索都在 sandbox/)"
    else
      echo "[FAIL] $n 个探索 runner 漏进 backend/scripts/ — 该移到 sandbox/<exp>/:"
      ls backend/scripts/experiment_*.py backend/scripts/analyze_*.py 2>/dev/null | sed 's#^#  #'
      exit 1
    fi
    ;;
  *)
    echo "usage: sandbox.sh {new <name>|list|wipe <name>|wipe-all|check}"; exit 1
    ;;
esac
