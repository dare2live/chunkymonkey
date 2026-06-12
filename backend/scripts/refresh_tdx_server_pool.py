"""TDX 服务器活池刷新 — 协议层握手扫描全候选, 把实测活子集写回 .env CM_TDX_SERVERS.

背景: CM_TDX_SERVERS 2026-06-12 起独占 (死池拖尾被请求级轮转抹平排头优势, xdxr 全军
超时反例) → 活池腐烂没有兜底, 必须可一键重扫。手动新政范式: 本脚本注册为 ops 手动
job (前端按钮), 不进任何自动调度。

纪律 (mythos §2): 代理环境下 TCP connect 永远假成功, 探活必须协议层真实取数
(Quotes.factory + bars_records 拿真 K 线); fail-closed: 活机数低于 --min-alive
不碰 .env (防把池写空), 退出码 1 由 wrapper 送达告警。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.tdx_source import (  # noqa: E402
    _load_hq_hosts,
    get_tdx_quotes_class,
    parse_tdx_server_string,
)

PROBE_SYMBOL = "000001"  # rule-compliance: ok evidence=平安银行, 全市场最稳定存在的探活标的
PROBE_BARS = 5  # rule-compliance: ok evidence=最小真实响应体, 仅证明协议层可取数


def candidate_servers() -> list[tuple[str, int]]:
    """扫描全集 = 当前 env 活池 ∪ HQ_HOSTS (iter_tdx_servers 已独占化, 这里必须自己并全集)."""
    raw = [item.strip() for item in os.environ.get("CM_TDX_SERVERS", "").split(",") if item.strip()]
    custom = [item for item in (parse_tdx_server_string(r) for r in raw) if item is not None]
    seen: set[tuple[str, int]] = set()
    ordered: list[tuple[str, int]] = []
    for server in custom + list(_load_hq_hosts()):
        if server not in seen:
            seen.add(server)
            ordered.append(server)
    return ordered


def probe_server(server: tuple[str, int], timeout: float) -> dict:
    """协议层探活: 建连 + 真实拉 5 根 K 线; 任何异常/空响应 = dead."""
    quotes_cls = get_tdx_quotes_class()
    started = time.monotonic()
    client = None
    try:
        client = quotes_cls.factory(
            market="std", multithread=False, heartbeat=False, server=server, timeout=timeout
        )
        records = client.bars_records(symbol=PROBE_SYMBOL, frequency=9, start=0, offset=PROBE_BARS)
        ok = bool(records is not None and len(records) > 0)
        error = "" if ok else "empty_response"
    except Exception as exc:  # noqa: BLE001 — 探活失败原因要分类展示, 不吞错
        ok, error = False, f"{type(exc).__name__}: {str(exc)[:80]}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    return {"server": server, "ok": ok, "elapsed_s": round(time.monotonic() - started, 2), "error": error}


def render_pool(servers: list[tuple[str, int]]) -> str:
    return ",".join(f"{host}:{port}" for host, port in servers)


def update_env_text(env_text: str, pool_value: str) -> str:
    """替换/追加 CM_TDX_SERVERS 行 (纯函数, 可单测)."""
    lines = env_text.splitlines()
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith("CM_TDX_SERVERS="):
            out.append(f"CM_TDX_SERVERS={pool_value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"CM_TDX_SERVERS={pool_value}")
    return "\n".join(out) + ("\n" if env_text.endswith("\n") or not env_text else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="单服务器协议握手+取数超时秒 (默认 2.0)")
    parser.add_argument("--min-alive", type=int, default=3,
                        help="活机数下限, 低于则拒写 .env (fail-closed, 默认 3)")
    parser.add_argument("--workers", type=int, default=8, help="并发探测数 (默认 8)")
    parser.add_argument("--env-file", default=str(REPO / ".env"))
    parser.add_argument("--dry-run", action="store_true", help="只扫描报告, 不写 .env")
    args = parser.parse_args()

    if get_tdx_quotes_class() is None:
        print("FAIL: tdxhub 未安装, 无法协议层探活")
        return 1

    candidates = candidate_servers()
    old_pool = os.environ.get("CM_TDX_SERVERS", "(未设置)")
    print(f"候选 {len(candidates)} 台 (env 活池 ∪ HQ_HOSTS), 超时 {args.timeout}s, 并发 {args.workers}")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda s: probe_server(s, args.timeout), candidates))

    alive = sorted((r for r in results if r["ok"]), key=lambda r: r["elapsed_s"])
    dead = [r for r in results if not r["ok"]]
    print(f"\n活机 {len(alive)} / 死机 {len(dead)}:")
    for r in alive:
        print(f"  OK   {r['server'][0]}:{r['server'][1]}  {r['elapsed_s']}s")
    for r in dead:
        print(f"  DEAD {r['server'][0]}:{r['server'][1]}  {r['error']}")

    if len(alive) < args.min_alive:
        print(f"\nFAIL: 活机 {len(alive)} < 下限 {args.min_alive}, 拒写 .env (防池写空); "
              f"网络环境可疑 (代理/断网), 人工核查后重跑")
        return 1

    new_pool = render_pool([r["server"] for r in alive])
    print(f"\n旧池: {old_pool}")
    print(f"新池 (按延迟升序): {new_pool}")
    if args.dry_run:
        print("dry-run: 未写 .env")
        return 0

    env_path = Path(args.env_file)
    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    tmp = env_path.with_suffix(".tmp")
    tmp.write_text(update_env_text(env_text, new_pool), encoding="utf-8")
    tmp.replace(env_path)
    print(f"已写入 {env_path} (原子替换); 对后续启动的任务生效, 运行中的进程不受影响")
    return 0


if __name__ == "__main__":
    sys.exit(main())
