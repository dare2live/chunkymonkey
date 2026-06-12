#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PORT=8000
RELOAD_MODE="${CM_RELOAD:-0}"
# 默认只绑 loopback (127.0.0.1): 本系统是单机本地量化工具, 写端点零鉴权,
# 绑 0.0.0.0 会把写接口暴露到整个局域网. 需跨机访问时显式 export CM_HOST=0.0.0.0.
HOST="${CM_HOST:-127.0.0.1}"
export PATH="/opt/homebrew/opt/python@3.13/libexec/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
PYTHON_BIN="${PYTHON_BIN:-python}"

find_port_pids() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

pid_belongs_to_project() {
  local pid="$1"
  local cwd
  local cmd
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  if [[ "$cwd" == "$ROOT_DIR" || "$cwd" == "$BACKEND_DIR" ]]; then
    return 0
  fi

  cmd="$(ps -ww -p "$pid" -o command= 2>/dev/null | head -n 1)"
  [[ "$cmd" == *"$BACKEND_DIR"* || "$cmd" == *"$ROOT_DIR/start.command"* ]]
}

stop_project_server() {
  local matched=()
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if pid_belongs_to_project "$pid"; then
      matched+=("$pid")
    fi
  done < <(find_port_pids)

  if [[ ${#matched[@]} -eq 0 ]]; then
    return 0
  fi

  echo "检测到旧的 Chunky Monkey 实例正在占用 $PORT 端口，准备重启..."
  kill "${matched[@]}" 2>/dev/null || true

  for _ in {1..20}; do
    sleep 0.3
    local still_running=0
    for pid in "${matched[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        still_running=1
        break
      fi
    done
    [[ $still_running -eq 0 ]] && return 0
  done

  echo "旧实例未能及时退出，强制结束..."
  kill -9 "${matched[@]}" 2>/dev/null || true
}

check_port_conflict() {
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if ! pid_belongs_to_project "$pid"; then
      printf '端口 %s 已被其他程序占用（PID: %s），未自动处理。\n' "$PORT" "$pid"
      echo "请先关闭占用该端口的程序，或改用其他端口启动。"
      exit 1
    fi
  done < <(find_port_pids)
}

cd "$BACKEND_DIR"

stop_project_server
check_port_conflict

# ---- akshare 依赖检查 (改成查 pip metadata, 不真 import) ----
# 之前用 import akshare 触发 mini_racer V8 init, 在 macOS 14+ 崩 (Python quit unexpectedly)
# 现在用 pip metadata 查版本, 不 import → 不触发 V8
current_v="$("$PYTHON_BIN" - <<'PY' 2>/dev/null || true
try:
    from importlib.metadata import version
    print(version('akshare'))
except Exception:
    print("")
PY
)"
if [[ -n "$current_v" ]]; then
  echo "akshare: 本地版本 v${current_v} (metadata 查, 未 import → 避 V8 崩溃)"
else
  echo "akshare: 未安装; TDX 主链路可启动, akshare 兜底接口会不可用"
fi

# V8 flags 防 mini_racer 在 macOS 上 partition_alloc 崩溃
# (akshare → mini_racer → V8 已知 issue: https://github.com/bpcreech/PyMiniRacer)
export V8_FLAGS="${V8_FLAGS:---no-randomize-hashes --no-sandbox}"

# 传给 FastAPI 进程: CORS 默认 origin 用实际端口拼 (main.py::_resolve_cors_origins)
export CM_PORT="$PORT"

echo "========================================"
echo "  ChunkyMonkey 启动中..."
echo "  地址: http://localhost:$PORT  (/ → /v3 设计稿)"
echo "  API:  http://localhost:$PORT/docs"
echo "  Python: $($PYTHON_BIN --version 2>&1)"
if [[ "$RELOAD_MODE" == "1" ]]; then
  echo "  模式: 开发热重载 (CM_RELOAD=1)"
else
  echo "  模式: 稳定运行（默认，不启用热重载）"
fi
echo "  按 Ctrl+C 停止"
echo "========================================"

# ---- 后端就绪后自动打开 v3 前端 ----
# 设置 CM_OPEN_BROWSER=0 可禁用 (headless 跑批场景)
open_frontend_when_ready() {
  # 60 次 × 0.5s = 30s 总等待
  local max_attempts=60
  local i
  for ((i=1; i<=max_attempts; i++)); do
    if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
      echo "✓ 后端就绪 (尝试 ${i} 次), 打开浏览器 http://localhost:$PORT/v3"
      open "http://localhost:$PORT/" 2>/dev/null || true
      return 0
    fi
    sleep 0.5
  done
  echo "⚠ 后端 30s 内未就绪, 跳过自动打开浏览器"
  echo "  请手动访问: http://localhost:$PORT/v3"
}

if [[ "${CM_OPEN_BROWSER:-1}" == "1" ]]; then
  # 后台等待 + 打开浏览器, 主进程继续执行 uvicorn
  open_frontend_when_ready &
fi

if [[ "$RELOAD_MODE" == "1" ]]; then
  exec "$PYTHON_BIN" -m uvicorn main:app --host "$HOST" --port "$PORT" --reload --reload-dir "$BACKEND_DIR"
fi

exec "$PYTHON_BIN" -m uvicorn main:app --host "$HOST" --port "$PORT"
