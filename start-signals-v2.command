#!/bin/bash
# start-signals-v2.command
# 与 start.command 并行运行——main 分支跑 8000（老工作台），
# worktree 分支跑 8001（signals v2 新 UI），数据库共享。
# 关掉这个窗口会自动停。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PORT=8001

cd "$ROOT_DIR"

# 共用主项目的 data/ 目录
if [[ ! -e "$ROOT_DIR/data" ]]; then
  ln -s /Users/dp/Documents/M/stock/data "$ROOT_DIR/data"
fi

# 清理旧进程
existing_pid=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true)
if [[ -n "${existing_pid:-}" ]]; then
  echo "[signals-v2] 清理端口 $PORT 上的旧进程 $existing_pid"
  kill "$existing_pid" 2>/dev/null || true
  sleep 2
fi

cd "$BACKEND_DIR"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  signals v2 (worktree: claude/affectionate-wing-5ce5ce)"
echo "  浏览器打开：http://127.0.0.1:$PORT/"
echo "  默认 tab 就是「信号 v2」"
echo "  (同时你的主 start.command 可继续在 8000 跑，数据共享)"
echo "════════════════════════════════════════════════════════════════"
echo ""

exec python3 -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
