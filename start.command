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

# ---- akshare 自动升级 (官方建议每次使用前升级, 避免接口变动) ----
# 跳过: CM_SKIP_UPGRADE=1 ./start.command
# 失败不阻塞启动 (网络问题、超时、pip 缺失都继续走)
if [[ "${CM_SKIP_UPGRADE:-0}" != "1" ]]; then
  echo "akshare: 每次启动自动检查 + 落后版本自动升级 (临时跳过: 用 CM_SKIP_UPGRADE=1 ./start.command 启动)"
  if command -v pip3 >/dev/null 2>&1; then
    old_v="$(pip3 show akshare 2>/dev/null | awk '/^Version:/ {print $2}')"
    if pip3 install --upgrade akshare --quiet --upgrade-strategy only-if-needed --timeout 20 2>/dev/null; then
      new_v="$(pip3 show akshare 2>/dev/null | awk '/^Version:/ {print $2}')"
      if [[ -z "$old_v" ]]; then
        echo "  → akshare 首次安装完成 (v${new_v:-unknown})"
      elif [[ "$old_v" != "$new_v" ]]; then
        echo "  → akshare 已自动升级: v${old_v} → v${new_v}"
      else
        echo "  → akshare 已是最新版 (v${new_v}), 无需升级"
      fi
    else
      echo "  → akshare 升级失败 (网络/超时/pip 异常), 沿用本地版本 v${old_v:-unknown}"
    fi
  else
    echo "  → pip3 未安装, 跳过 akshare 自动升级"
  fi
else
  echo "akshare: 跳过自动升级检查 (CM_SKIP_UPGRADE=1 已设)"
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
