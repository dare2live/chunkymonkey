"""十大流通股东 Phase A — 供应商原行 refetch → staging (只读验证器取数刀).

背景 (`.git/cm_worklog/ingest_holders_raw/` 三份 fable 研判定的第一刀):
canonical 610,414 行有两个内容缺陷 (18,036 行日更翻页重复 + 退出行系统性空洞,
详见本文件调用方的任务说明), 生产库「按股重写整分区」的现有写路径重采全市场要写
1.9 亿行 (312x 放大)。Phase A 只做**取数到 staging + 只读验证**, 不碰生产库、
不动 schema/写路径 —— 回退 = 删 staging 文件。

本文件只做「取」这一半: 按股全期从妙想拉**供应商原行** (47 字段, 不清洗, 不做
K线范围过滤 —— Phase A 要的正是完整供应商史, 用来跟 canonical 做验证比对), 落进
``data/scratch/ingest_holders_raw/<run_id>.duckdb`` 的两张表:

  - ``raw_fetch``: 每股一行的取数台账 (status/n_rows/truncated/error/耗时)。
  - ``raw_rows`` : 供应商原始行, 列名/类型取
    ``services.data_sources.holders_top10_schema.RAW_FIELDS`` (另一 agent 维护;
    47 个供应商字段 + ``(fetch_id, row_ordinal)`` 两个搬运键, 不多不少 —— 列集合
    是本模块的钉死断言, unknown-key sidecar / row_hash 等更丰富的 writer 语义留给
    「DDL/writer 在别的任务」那句话所指的下一步, 不在 Phase A 取数器里抢跑)。

为什么不直接调用 ``services.holders_aif10._fetch_raw``:
调用形态 (``REPORT_FREE`` / ``secucode`` 转换 / ``PAGE_SIZE``) 完全复用那个函数,
但 ``_fetch_raw`` 内部经 ``aif10_scraper.fetch_all_pages`` 取数 ——
该函数会算出 ``PaginationLandResult`` (含 ``truncated``), 但只拿它打一条 warning
日志就丢弃, 不回传。Phase A 的验证器要的恰恰是「这只股truncated没」的真实信号,
不是一行日志, 所以这里直接调 ``fetch_all_pages`` 本身也调用的
``aif10_scraper.pagination.fetch_pages_for_filters`` (同样的 sort_columns/
sort_types 注册表查找、同样的分页循环、同样的 truncation 判据), 只是把
``land.truncated`` 接住而不是丢掉 —— 不是另起一套分页算法, 只是换一个已存在的、
更靠内层的入口拿到已经算好的结果。

不改 ``services/holders_aif10.py`` / ``holders_top10_schema.py`` / 任何
acceptance / dual_write 文件 —— 只 import。

用法:
    python backend/scripts/ingest_holders_raw.py fetch --limit 20
    python backend/scripts/ingest_holders_raw.py fetch --symbols 600388,000001 --resume
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 顺序有意义: 触发 services.holders_aif10 顶部的
# ``ensure_import_path("miaoxiang")`` 副作用, 后面对 aif10_scraper.* 的 import
# 才保证能找到那个 sibling checkout (不自己重复一遍路径拼接逻辑)。
from services.holders_aif10 import PAGE_SIZE, REPORT_FREE, _secucode  # noqa: E402
from services.data_sources.holders_top10_schema import (  # noqa: E402
    RAW_FIELDS,
    RawFetch,
)

from aif10_scraper.pagination import (  # noqa: E402
    DEFAULT_MAX_PAGES_PER_QUERY,
    fetch_pages_for_filters,
)
from aif10_scraper.registry import get_report  # noqa: E402

FULL_MARKET_SIZE = 5448  # 外推分母 (任务口径 A 股活跃 universe 量级), 非现查


class StagingPathError(RuntimeError):
    """staging db 路径落在 ``data/scratch/`` 之外 —— 防手滑写生产库。"""


# ── 路径守卫 ─────────────────────────────────────────────────────────
def _scratch_root() -> Path:
    from services.db_connection import DB_DIR

    return (DB_DIR / "scratch").resolve()


def default_staging_path(run_id: str) -> Path:
    # staging 库是一次性的 (run 完即可删, 回退就是 rm), 按设计不进 database_manifest ——
    # manifest 管的是有 owner/retention/writer 约束的生产库。写入前必过
    # validate_staging_path(): 任何不在 scratch 下的路径直接退出非 0 (有测试)。
    # evidence: 一次性 staging 文件名, 非生产库路径; 守卫见 validate_staging_path
    return _scratch_root() / "ingest_holders_raw" / f"{run_id}.duckdb"


def validate_staging_path(path: Path) -> Path:
    """纯路径检查 (不碰文件系统): 必须落在 ``data/scratch/`` 之下, 否则拒绝。"""
    resolved = Path(path).resolve()
    scratch_root = _scratch_root()
    try:
        resolved.relative_to(scratch_root)
    except ValueError:
        raise StagingPathError(
            f"staging db 路径必须在 {scratch_root} 之下 (防写生产库); 收到 {resolved}"
        ) from None
    return resolved


# ── staging schema (raw_fetch / raw_rows) ───────────────────────────
_RAW_FETCH_DDL = """
CREATE TABLE IF NOT EXISTS raw_fetch (
    fetch_id VARCHAR PRIMARY KEY,
    stock_code VARCHAR NOT NULL,
    requested_at VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    n_rows INTEGER NOT NULL,
    truncated BOOLEAN NOT NULL,
    error VARCHAR,
    elapsed_s DOUBLE NOT NULL
)
"""

_RAW_ROW_NAMES: tuple[str, ...] = tuple(name for name, _ in RAW_FIELDS)


def _raw_rows_ddl() -> str:
    cols = ["fetch_id VARCHAR NOT NULL", "row_ordinal INTEGER NOT NULL"]
    cols += [f'"{name}" {duckdb_type}' for name, duckdb_type in RAW_FIELDS]
    body = ",\n    ".join(cols)
    return (
        "CREATE TABLE IF NOT EXISTS raw_rows (\n    "
        + body
        + ",\n    PRIMARY KEY (fetch_id, row_ordinal)\n)"
    )


_RAW_ROWS_INSERT_SQL = (
    "INSERT INTO raw_rows (fetch_id, row_ordinal, "
    + ", ".join(f'"{n}"' for n in _RAW_ROW_NAMES)
    + ") VALUES ("
    + ", ".join(["?"] * (2 + len(_RAW_ROW_NAMES)))
    + ")"
)


def ensure_staging_tables(conn) -> None:
    conn.execute(_RAW_FETCH_DDL)
    conn.execute(_raw_rows_ddl())


def _coerce_value(value: Any, duckdb_type: str) -> Any:
    """按 RAW_FIELDS 声明类型做最小转换 —— 忠实镜像, 不做业务清洗。"""
    if value is None:
        return None
    if duckdb_type == "VARCHAR":
        return str(value)
    if duckdb_type in ("INTEGER", "BIGINT"):
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None
    if duckdb_type == "DOUBLE":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    raise ValueError(f"unsupported RAW_FIELDS duckdb_type {duckdb_type!r}")


def _already_ok(conn, fetch_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM raw_fetch WHERE fetch_id = ? AND status = 'ok'", [fetch_id]
    ).fetchone()
    return row is not None


def _write_stock_result(
    conn,
    fetch: RawFetch,
    *,
    status: str,
    truncated: bool,
    error: str | None,
    elapsed_s: float,
) -> None:
    """幂等落一只股的取数结果: 先清同 fetch_id 旧行, 再写新台账+新行.

    先清后写让「非 --resume 重跑」和「上次跑到一半报错的股后来重试」都不会在
    raw_rows 里累积重复行; --resume 是否跳过在调用方 (run_fetch) 判, 这里只管
    「决定要写就必须写干净」。
    """
    requested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("DELETE FROM raw_rows WHERE fetch_id = ?", [fetch.fetch_id])
    conn.execute("DELETE FROM raw_fetch WHERE fetch_id = ?", [fetch.fetch_id])
    conn.execute(
        "INSERT INTO raw_fetch (fetch_id, stock_code, requested_at, status, "
        "n_rows, truncated, error, elapsed_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            fetch.fetch_id,
            fetch.stock_code,
            requested_at,
            status,
            len(fetch.rows),
            bool(truncated),
            error,
            elapsed_s,
        ],
    )
    if not fetch.rows:
        return
    params = [
        [fetch.fetch_id, ordinal]
        + [_coerce_value(row.get(name), duckdb_type) for name, duckdb_type in RAW_FIELDS]
        for ordinal, row in enumerate(fetch.rows, start=1)
    ]
    conn.executemany(_RAW_ROWS_INSERT_SQL, params)


# ── ① 获取 acquire (truncation-aware) ────────────────────────────────
def fetch_stock_raw(client, stock_code: str) -> tuple[list[dict], bool]:
    """某股全期供应商原行 (不清洗). 见模块 docstring: 为何不直接调 fetch_all_pages."""
    try:
        spec = get_report(REPORT_FREE)
        sort_columns, sort_types = spec.sort_columns, spec.sort_types
    except KeyError:
        sort_columns, sort_types = "", ""
    rows, land = fetch_pages_for_filters(
        client,
        REPORT_FREE,
        page_size=PAGE_SIZE,
        max_pages=0,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns="ALL",
        secucode=_secucode(stock_code),
        extra_filters=None,
        extra_params=None,
        progress_callback=None,
        max_pages_per_query=DEFAULT_MAX_PAGES_PER_QUERY,
    )
    return list(rows), bool(land.truncated)


# ── 编排 ─────────────────────────────────────────────────────────────
def resolve_symbols(explicit: Sequence[str] | None, *, limit: int = 0) -> list[str]:
    """稳定顺序 (code 排序) 的候选股票列表; --limit 在排序后截前 N (便于复现).

    explicit 给了就只在其内排序去重 (调试/测试用, 不碰任何生产 DB);
    否则退到全 active universe (production truth, 现查).
    """
    if explicit:
        codes = sorted({s.strip() for s in explicit if s and s.strip()})
    else:
        from services.db import get_conn
        from services.universe import get_active_universe

        conn = get_conn()
        try:
            codes = sorted(get_active_universe(conn, include_st=True))
        finally:
            conn.close()
    if limit:
        codes = codes[:limit]
    return codes


def run_fetch(
    conn,
    *,
    symbols: Sequence[str],
    client,
    resume: bool = False,
    progress_every: int = 0,
) -> dict:
    """串行按股取数落 staging. 单股异常不中断整批 (记 status='error' 继续)."""
    ensure_staging_tables(conn)
    ok = fail = truncated_n = resume_skipped = 0
    t0 = time.time()
    for i, code in enumerate(symbols, 1):
        fetch_id = code
        if resume and _already_ok(conn, fetch_id):
            resume_skipped += 1
            continue
        request = {
            "report_name": REPORT_FREE,
            "secucode": _secucode(code),
            "page_size": PAGE_SIZE,
        }
        t_stock = time.time()
        try:
            rows, truncated = fetch_stock_raw(client, code)
            elapsed = time.time() - t_stock
            fetch = RawFetch(
                fetch_id=fetch_id, stock_code=code, request=request, rows=tuple(rows)
            )
            _write_stock_result(
                conn, fetch, status="ok", truncated=truncated, error=None, elapsed_s=elapsed
            )
            ok += 1
            if truncated:
                truncated_n += 1
        except Exception as exc:  # noqa: BLE001 -- per-stock isolation, batch must continue
            elapsed = time.time() - t_stock
            fetch = RawFetch(fetch_id=fetch_id, stock_code=code, request=request, rows=())
            err_text = f"{type(exc).__name__}: {exc}"[:500]
            _write_stock_result(
                conn, fetch, status="error", truncated=False, error=err_text, elapsed_s=elapsed
            )
            fail += 1
        if progress_every and i % progress_every == 0:
            print(
                f"  [ingest_holders_raw] {i}/{len(symbols)} ok={ok} error={fail} "
                f"truncated={truncated_n} resume_skipped={resume_skipped} "
                f"({time.time()-t0:.0f}s)"
            )
    return {
        "ok": ok,
        "error": fail,
        "truncated": truncated_n,
        "resume_skipped": resume_skipped,
        "n_symbols": len(symbols),
        "elapsed_s": round(time.time() - t0, 3),
    }


def _print_summary(summary: dict) -> None:
    print(
        "[ingest_holders_raw] fetch DONE "
        f"ok={summary['ok']} error={summary['error']} truncated={summary['truncated']} "
        f"resume_skipped={summary['resume_skipped']} n_symbols={summary['n_symbols']} "
        f"elapsed_s={summary['elapsed_s']:.1f} db={summary.get('db_path', '?')}"
    )
    fetched = summary["ok"] + summary["error"]
    if fetched:
        avg = summary["elapsed_s"] / fetched
        print(
            f"[ingest_holders_raw] avg={avg:.3f}s/只 -> 外推 full_market"
            f"({FULL_MARKET_SIZE}只)~={avg * FULL_MARKET_SIZE / 60:.1f}min "
            "(外推, 非实测; 仅供预估下一步全市场耗时)"
        )


# ── CLI ──────────────────────────────────────────────────────────────
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ingest_holders_raw",
        description="Phase A: 十大流通股东供应商原行 -> staging (不写生产库).",
    )
    sub = ap.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("fetch", help="按股全期取供应商原行, 落 staging DuckDB")
    fp.add_argument("--symbols", default="", help="逗号分隔股票代码; 空=全 active universe")
    fp.add_argument("--limit", type=int, default=0, help="只取前 N 只 (code 排序, 便于复现)")
    fp.add_argument("--resume", action="store_true", help="跳过已 status=ok 的股 (0 次网络请求)")
    fp.add_argument("--run-id", default="", help="staging 文件名 (默认 UTC 时间戳)")
    fp.add_argument("--db-path", default="", help="显式 staging db 路径 (仍须在 data/scratch/ 下)")
    fp.add_argument("--progress-every", type=int, default=200)
    return ap


def _cmd_fetch(args: argparse.Namespace) -> int:
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = Path(args.db_path) if args.db_path else default_staging_path(run_id)
    try:
        db_path = validate_staging_path(raw_path)
    except StagingPathError as exc:
        print(f"ingest_holders_raw: refused: {exc}", file=sys.stderr)
        return 2

    explicit = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    symbols = resolve_symbols(explicit, limit=args.limit)
    if not symbols:
        print("ingest_holders_raw: empty symbol set (--symbols / universe)", file=sys.stderr)
        return 2

    db_path.parent.mkdir(parents=True, exist_ok=True)
    from aif10_scraper import default_client
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(str(db_path))
    try:
        summary = run_fetch(
            conn,
            symbols=symbols,
            client=default_client,
            resume=args.resume,
            progress_every=args.progress_every,
        )
    finally:
        conn.close()
    summary["db_path"] = str(db_path)
    _print_summary(summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "fetch":
        return _cmd_fetch(args)
    print(f"ingest_holders_raw: unknown command {args.command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
