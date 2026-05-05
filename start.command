#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PORT=8000
RELOAD_MODE="${CM_RELOAD:-0}"

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

# ---- akshare 启动前依赖检查 ----
# 生产启动只检查本地版本，不在启动链路执行 pip install/upgrade。
# 手动维护升级请运行: ./scripts/upgrade_akshare.sh
current_v="$(python3 - <<'PY' 2>/dev/null || true
try:
    import akshare as ak
    print(getattr(ak, "__version__", "unknown"))
except Exception:
    print("")
PY
)"
if [[ -n "$current_v" ]]; then
  echo "akshare: 本地版本 v${current_v}"
else
  echo "akshare: 未安装或无法导入；TDX 主链路可启动，akshare 兜底接口会不可用。"
  echo "          如需维护升级，请运行 ./scripts/upgrade_akshare.sh"
fi

echo "========================================"
echo "  Chunky Monkey v2 启动中..."
echo "  地址: http://localhost:$PORT"
echo "  API:  http://localhost:$PORT/docs"
if [[ "$RELOAD_MODE" == "1" ]]; then
  echo "  模式: 开发热重载 (CM_RELOAD=1)"
else
  echo "  模式: 稳定运行（默认，不启用热重载）"
fi
echo "  按 Ctrl+C 停止"
echo "========================================"
if [[ "$RELOAD_MODE" == "1" ]]; then
  exec python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload --reload-dir "$BACKEND_DIR"
fi

exec python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT"
