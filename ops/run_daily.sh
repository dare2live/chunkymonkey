#!/bin/bash
# M8.9 每日自动化主入口
#
# 触发: launchd (cn.local.chunky-monkey.daily.plist) Mon-Fri 17:30
# 流程:
#   1. 等 backend daemon 就绪 (uvicorn @ localhost:8000)
#   2. POST /api/update/all 启动智能更新 (17 步主链路)
#   3. 轮询 /api/update/status 直到 running=false (超时 60 分钟)
#   4. 跑 daily topK 双轨 (primary + shadow_dense_v2)
# 日志: ~/Library/Logs/chunky-monkey/daily-{YYYY-MM-DD}.log
# 退出码: 0=成功, 1=网络/超时, 2=topK 失败, 3=update 失败

set -u  # 不 set -e, 让流程能记录失败再退出

# ----- 配置 -----
PROJECT_ROOT="/Users/dp/Documents/M/stock"
BACKEND_URL="http://127.0.0.1:8000"
API_BASE="$BACKEND_URL/api/inst"   # updater router 挂在 /api/inst
LOG_DIR="$HOME/Library/Logs/chunky-monkey"
DATE=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/daily-$DATE.log"
UPDATE_TIMEOUT_SEC=$((60 * 60))   # 60 分钟封顶
POLL_INTERVAL_SEC=15
PYTHON_BIN="${PYTHON_BIN:-python3}"
DRY_RUN="${DRY_RUN:-0}"

# 命令行 --dry-run 也算
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=1
done

mkdir -p "$LOG_DIR"

log() {
    local ts
    ts=$(date "+%Y-%m-%d %H:%M:%S")
    echo "[$ts] $*" | tee -a "$LOG_FILE"
}

log "==== chunky-monkey daily run start ===="
log "PROJECT_ROOT=$PROJECT_ROOT"
log "BACKEND_URL=$BACKEND_URL"

# ----- 1. 检查交易日 (周末跳过, launchd 不区分中国节假日) -----
DOW=$(date +%u)  # 1=Mon ... 7=Sun
if [[ "$DOW" -ge 6 ]]; then
    log "今天是周末 (DOW=$DOW), 跳过. 节假日仍可能跑空但不报错."
    exit 0
fi

# ----- 2. 等 backend daemon 就绪 (最多 30 秒) -----
ready=false
for i in $(seq 1 6); do
    if curl -fsS --max-time 5 "$API_BASE/update/status" > /dev/null 2>&1; then
        ready=true
        break
    fi
    log "backend 未就绪, 等 5s 重试 ($i/6)..."
    sleep 5
done
if ! $ready; then
    log "ERROR: backend daemon 未运行 (curl $API_BASE/update/status 失败). 跳过本次. 请确认 uvicorn main:app --port 8000 已起."
    exit 1
fi

# ----- 3. 触发智能更新 -----
if [[ "$DRY_RUN" == "1" ]]; then
    log "[DRY-RUN] 跳过 POST $API_BASE/update/all (改读 status)"
    trigger_resp="{\"dry_run\": true}"
else
    log "POST $API_BASE/update/all ..."
    trigger_resp=$(curl -fsS -X POST --max-time 10 "$API_BASE/update/all" 2>&1) || {
        log "ERROR: 触发 /update/all 失败: $trigger_resp"
        exit 3
    }
fi
log "触发响应: $trigger_resp"

# ----- 4. 轮询 status 直到 running=false -----
elapsed=0
final_status=""
while [[ $elapsed -lt $UPDATE_TIMEOUT_SEC ]]; do
    status_json=$(curl -fsS --max-time 10 "$API_BASE/update/status" 2>&1) || {
        log "WARN: 轮询失败, 5s 重试..."
        sleep 5
        elapsed=$((elapsed + 5))
        continue
    }
    running=$(echo "$status_json" | $PYTHON_BIN -c "import sys, json; print(json.load(sys.stdin).get('running', False))")
    if [[ "$running" == "False" ]]; then
        final_status="$status_json"
        break
    fi
    sleep $POLL_INTERVAL_SEC
    elapsed=$((elapsed + POLL_INTERVAL_SEC))
    if [[ $((elapsed % 120)) -eq 0 ]]; then
        log "..更新进行中, 已等 ${elapsed}s"
    fi
done

if [[ -z "$final_status" ]]; then
    log "ERROR: 智能更新超时 ${UPDATE_TIMEOUT_SEC}s, 未完成"
    exit 3
fi

# 解析 summary 看有没有 failed step
failed_count=$(echo "$final_status" | $PYTHON_BIN -c "
import sys, json
d = json.load(sys.stdin)
steps = d.get('steps', [])
failed = [s for s in steps if (s.get('status') or '') == 'failed']
print(len(failed))
" 2>/dev/null || echo "0")
log "智能更新结束, 失败步骤数=$failed_count"

# ----- 5. Daily topK 双轨 -----
cd "$PROJECT_ROOT/backend" || { log "ERROR: cd backend 失败"; exit 2; }

if [[ "$DRY_RUN" == "1" ]]; then
    log "[DRY-RUN] 跳过 topK primary / shadow"
else
    log "运行 daily topK primary ..."
    if $PYTHON_BIN -m scripts.run_daily_topk --track-id primary --is-primary >> "$LOG_FILE" 2>&1; then
        log "primary topK 成功"
    else
        log "ERROR: primary topK 失败 (exit=$?)"
        exit 2
    fi

    log "运行 daily topK shadow_dense_v2 ..."
    if $PYTHON_BIN -m scripts.run_daily_topk --track-id shadow_dense_v2 --include-disabled-models >> "$LOG_FILE" 2>&1; then
        log "shadow topK 成功"
    else
        log "WARN: shadow topK 失败 (exit=$?), 不阻塞主线"
    fi
fi

log "==== chunky-monkey daily run end (failed_steps=$failed_count) ===="
exit 0
