#!/usr/bin/env bash
# daily_update.sh — 手动数据底座更新 (瘦 wrapper, 2026-06-23 重设计)
#
# 重设计 (用户 "获取/清洗/加工/存储 各司其职, 清爽简洁"): 旧 469 行 bash 套 python heredoc
# 重组为四阶段 Python 管线 backend/services/pipeline/ (preflight gate → 获取 → 清洗 → 加工 → 存储)。
# 本 wrapper 只做 bash 该做的: 设 PATH + source .env, 然后委托 python 管线。逻辑零改, 只重组。
# 管线真相源 = backend/services/pipeline/{context,preflight,acquire,clean,process,store,run}.py。
#
# Usage:
#   bash scripts/daily_update.sh              # 全流程
#   bash scripts/daily_update.sh --dry        # dry-run, 不写 DB
#   bash scripts/daily_update.sh --skip-sync  # 跳采集 (用现有)
#   bash scripts/daily_update.sh --date 20260708  # 指定 run-date 标签 (透传管线, 防跨午夜错位)
#   (env override 兼容: DRY=1 / SKIP_SYNC=1 bash scripts/daily_update.sh)
#
# 运行方式 (2026-06-13 用户决议: 本地未上云 + 定时不保证开机在线 → 手动跑, 成熟后上云):
#   收盘后 (~17:00 数据 publish 后) 跑。Log: /tmp/chunkymonkey_daily_update_<YYYYMMDD>.log
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# launchd/Homebrew 无裸 python (只 python3) — 前置 venv bin (含 python symlink, 有 FDA 的 python3.13)
export PATH="$REPO_ROOT/.venv/bin:$PATH"

# 统一 env 真相源 = .env (gitignored): TUSHARE token/URL + CM_TDX_SERVERS 可达池
if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a; source "$REPO_ROOT/.env"; set +a
fi

# args + env override → 管线 flag
# args + env override → 管线 flag (普通字符串非数组: bash 3.2 兼容 + 空则无参,
# 不用 "${arr[@]:-}" 因空数组+:- 会展开成一个空字符串 arg → argparse "unrecognized arguments:" 报错)。
# 重复 --dry/--skip-sync 无害: argparse store_true 幂等, 故无需去重。
ARGS=""
[[ "${DRY:-0}" == "1" ]] && ARGS="$ARGS --dry"
[[ "${SKIP_SYNC:-0}" == "1" ]] && ARGS="$ARGS --skip-sync"
EXPECT_DATE=0
for arg in "$@"; do
    if [[ "$EXPECT_DATE" == "1" ]]; then
        # --date 的值: 严格 YYYYMMDD (ARGS 走非引号展开, 只放行纯数字保证安全)
        if [[ ! "$arg" =~ ^[0-9]{8}$ ]]; then
            echo "ERROR: --date 需要 YYYYMMDD, 得到: $arg" >&2; exit 2
        fi
        ARGS="$ARGS --date $arg"; EXPECT_DATE=0; continue
    fi
    case "$arg" in
        --dry) ARGS="$ARGS --dry" ;;
        --skip-sync) ARGS="$ARGS --skip-sync" ;;
        --date) EXPECT_DATE=1 ;;
        --date=*) val="${arg#--date=}"
            if [[ ! "$val" =~ ^[0-9]{8}$ ]]; then
                echo "ERROR: --date 需要 YYYYMMDD, 得到: $val" >&2; exit 2
            fi
            ARGS="$ARGS --date $val" ;;
        *) echo "ERROR: unknown daily_update argument: $arg" >&2; exit 2 ;;
    esac
done
[[ "$EXPECT_DATE" == "1" ]] && { echo "ERROR: --date 缺少值" >&2; exit 2; }

# shellcheck disable=SC2086 — 故意非引号展开: 空 ARGS → 0 参; "--dry" → 1 参 (flag 无空格安全)
exec env PYTHONPATH=backend python -m services.pipeline.run $ARGS
