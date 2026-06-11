"""sync_runner — sync_registry.yaml 驱动的通用数据域同步器 (架构稿 §3.3).

一个 registry 条目 = 一个数据域, 零域专属代码。职责:
  1. 按 batch_mode 切批 (交易日历驱动, 不 hardcode 日期)
  2. 调 source adapter fetch_raw (api 字段镜像, 不加工)
  3. 写 raw 表 (target_db 库, MERGE on grain, 加 built_at) — 幂等重跑
  4. watermark (mart_data_source_watermark) + 失败入队 (failure_queue) — 复用既有服务
  5. 0 行 = 失败重试 (宪法 v2 第 6 条; allow_empty_batch 条目除外)

写锁纪律: raw 表写 tushare_raw.duckdb (manifest 注册), 与 smartmoney 主库锁解耦;
watermark/failure_queue 在 smartmoney (既有表), 写入窗口短。

用法:
    PYTHONPATH=backend python -m services.data_sources.sync_runner \
        --domain moneyflow --backfill              # 从 data_start 回填到最新
    ... --domain moneyflow                          # 增量 (watermark 之后)
    ... --all-due                                   # 全部到期域 (daily_update 集成)
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("sync_runner")

_REPO = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO / "backend" / "config" / "sync_registry.yaml"
SOURCE_TIER_TUSHARE = 2  # from yaml: tdx_data_need_coverage need_027 source_tier


def load_registry(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or _REGISTRY_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "domains" not in raw:
        raise ValueError("sync_registry.yaml: 缺 domains")
    return raw


def _domain_spec(registry: dict[str, Any], domain: str) -> dict[str, Any]:
    spec = dict(registry["defaults"] or {})
    entry = registry["domains"].get(domain)
    if entry is None:
        raise KeyError(f"sync_registry: 未注册的数据域 '{domain}' — 新域必须先加 registry 条目 (宪法 v2 第 7/9 条)")
    spec.update(entry)
    spec["domain"] = domain
    return spec


def _adapter(source_name: str):
    from services.data_sources import get_registry  # 延迟 import 防环

    src = get_registry().get_source(source_name)
    if src is None:
        raise KeyError(f"data_sources registry: 未注册 source '{source_name}'")
    return src


def _target_conn(spec: dict[str, Any]):
    """raw 库写连接 (manifest 解析路径); 表不存在由首批数据建."""
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect

    db_alias = spec.get("target_db", "tushare_raw")
    path = get_database_manifest().path_for(db_alias)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return connect(str(path), read_only=False)


def _smartmoney_conn():
    from services.db import get_conn

    return get_conn()


def _trading_days(start: str, end: str | None = None) -> list[str]:
    """交易日列表 (YYYYMMDD), 真相源 = 项目交易日历 (L0)."""
    from services.utils import latest_completed_trade_date

    conn = _smartmoney_conn()
    try:
        end_d = end or latest_completed_trade_date(conn).replace("-", "")
        rows = conn.execute(
            """
            SELECT replace(CAST(trade_date AS VARCHAR), '-', '') AS d
            FROM dim_trading_calendar
            WHERE is_trading AND replace(CAST(trade_date AS VARCHAR), '-', '') BETWEEN ? AND ?
            ORDER BY 1
            """,
            [start, end_d],
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _fetch_with_retry(adapter, spec: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]] | None:
    """0 行/异常 → 退避重试; 终败返回 None (调用方入 failure_queue)."""
    retry = spec.get("retry") or {}
    attempts = int(retry.get("max_attempts", 3))
    backoffs = list(retry.get("backoff_seconds", [5, 30, 120]))
    allow_empty = bool(spec.get("allow_empty_batch"))
    last_err: str | None = None
    for i in range(attempts):
        try:
            rows = adapter.fetch_raw(spec["api"], **params)
            if rows:
                return rows
            if allow_empty:
                return []
            last_err = "zero_rows"
        except Exception as exc:  # noqa: BLE001 — 重试边界
            last_err = str(exc)[:200]
        if i < attempts - 1:
            time.sleep(backoffs[min(i, len(backoffs) - 1)])
    log.warning("fetch 终败 domain=%s params=%s err=%s", spec["domain"], params, last_err)
    return None


def _write_batch(conn, spec: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    """MERGE on grain: DELETE 同 grain 旧行 + INSERT, 加 built_at (幂等)."""
    if not rows:
        return 0
    import pandas as pd

    df = pd.DataFrame(rows)
    df["built_at"] = datetime.now(timezone.utc).isoformat()
    table = spec["target_table"]
    grain: list[str] = list(spec["grain"])
    missing = [g for g in grain if g not in df.columns]
    if missing:
        raise ValueError(f"{table}: api 返回缺 grain 列 {missing} — registry 条目或上游 schema 变了")

    # duck_adapter 包装层挡住 DataFrame replacement scan, 显式注册视图
    raw_con = getattr(conn, "_con", conn)
    raw_con.register("df", df)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} AS SELECT * FROM df LIMIT 0")
    # 列演进: api 新增列时表自动加列 (raw 镜像语义)
    existing = {r[0] for r in conn.execute(
        f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'"
    ).fetchall()}
    for col in df.columns:
        if col not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" VARCHAR')
    key = " AND ".join(f't."{g}" = s."{g}"' for g in grain)
    conn.execute(f"DELETE FROM {table} t WHERE EXISTS (SELECT 1 FROM df s WHERE {key})")
    cols = ", ".join(f'"{c}"' for c in df.columns)
    conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM df")
    raw_con.unregister("df")
    return len(df)


def _last_watermark_date(domain: str, source: str) -> str | None:
    conn = _smartmoney_conn()
    try:
        from services.source_watermarks import ensure_source_watermark_schema

        ensure_source_watermark_schema(conn)
        row = conn.execute(
            "SELECT last_data_date FROM mart_data_source_watermark WHERE data_domain = ? AND source_name = ?",
            [f"sync:{domain}", source],
        ).fetchone()
        return str(row[0]).replace("-", "") if row and row[0] else None
    finally:
        conn.close()


def _record_outcome(spec: dict[str, Any], *, ok: bool, last_date: str | None,
                    rows: int, error: str | None = None) -> None:
    from services.source_watermarks import record_source_failure, resolve_source_failures, upsert_watermark

    conn = _smartmoney_conn()
    try:
        domain_key = f"sync:{spec['domain']}"
        if ok:
            upsert_watermark(conn, {
                "data_domain": domain_key,
                "source_name": spec["source"],
                "source_tier": SOURCE_TIER_TUSHARE,
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "last_data_date": last_date,
                "row_count": rows,
                "parser_version": "sync_runner_v1",
            })
            resolve_source_failures(conn, data_domain=domain_key, source_name=spec["source"], commit=True)
        else:
            record_source_failure(
                conn,
                data_domain=domain_key,
                source_name=spec["source"],
                source_tier=SOURCE_TIER_TUSHARE,
                error_type="sync_batch_failed",
                last_error=error,
                commit=True,
            )
        try:
            conn.commit()
        except Exception:  # noqa: BLE001 — duckdb autocommit 兼容
            pass
    finally:
        conn.close()


def run_domain(domain: str, *, backfill: bool = False, start: str | None = None,
               end: str | None = None, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步单个数据域. 返回 {domain, batches, rows, failed_batches}."""
    reg = registry or load_registry()
    spec = _domain_spec(reg, domain)
    adapter = _adapter(spec["source"])

    if spec["batch_mode"] == "full_refresh":
        batches: list[dict[str, Any]] = [{}]
    elif spec["batch_mode"] == "by_date_range":
        # 小表 (如大盘资金流 1 行/日) 一次调用拉全范围; 单次上限由 API 决定 (registry 选用前确认)
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        from services.utils import latest_completed_trade_date

        conn0 = _smartmoney_conn()
        try:
            end_d = end or latest_completed_trade_date(conn0).replace("-", "")
        finally:
            conn0.close()
        batches = [{"start_date": start_d, "end_date": end_d}]
    elif spec["batch_mode"] == "by_trade_date":
        if backfill:
            start_d = start or spec["data_start"]
        else:
            wm = _last_watermark_date(domain, spec["source"])
            start_d = start or wm or spec["data_start"]
        days = _trading_days(start_d, end)
        # 增量模式跳过 watermark 当天 (已写过)
        if not backfill and len(days) > 1 and days[0] == (start or _last_watermark_date(domain, spec["source"]) or ""):
            days = days[1:]
        batches = [{"trade_date": d} for d in days]
    else:
        raise NotImplementedError(f"batch_mode {spec['batch_mode']} 未实现 (by_ts_code/by_month 按需加)")

    conn = _target_conn(spec)
    total_rows, failed, last_ok_date = 0, [], None
    min_rows = int(spec.get("min_rows_per_batch", 0))
    try:
        for params in batches:
            rows = _fetch_with_retry(adapter, spec, params)
            if rows is None:
                failed.append(params)
                continue
            if rows and len(rows) < min_rows:
                log.warning("batch %s 行数 %d < min_rows_per_batch %d (可疑, 仍写入并记 failure)",
                            params, len(rows), min_rows)
                failed.append({**params, "suspect": "below_min_rows"})
            n = _write_batch(conn, spec, rows)
            total_rows += n
            if params.get("trade_date"):
                last_ok_date = params["trade_date"]
            elif params.get("end_date"):
                last_ok_date = params["end_date"]
            time.sleep(0.4)  # rule-compliance: ok evidence=vendor-gateway-conn-refused-backoff-2026-06-11
    finally:
        conn.close()

    ok = not failed or (total_rows > 0 and len(failed) < len(batches))
    _record_outcome(spec, ok=len(failed) == 0, last_date=last_ok_date,
                    rows=total_rows, error=json.dumps(failed[:5]) if failed else None)
    result = {"domain": domain, "batches": len(batches), "rows": total_rows,
              "failed_batches": len(failed), "last_date": last_ok_date, "ok": ok}
    log.info("sync %s", result)
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", help="sync_registry 域名")
    parser.add_argument("--all-due", action="store_true", help="同步全部注册域 (daily_update 集成入口)")
    parser.add_argument("--backfill", action="store_true", help="从 data_start 全量回填")
    parser.add_argument("--start", default=None, help="覆盖起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="覆盖结束日 YYYYMMDD")
    args = parser.parse_args()

    reg = load_registry()
    domains = list(reg["domains"]) if args.all_due else ([args.domain] if args.domain else [])
    if not domains:
        parser.error("--domain 或 --all-due 必选其一")

    results = [run_domain(d, backfill=args.backfill, start=args.start, end=args.end, registry=reg)
               for d in domains]
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if all(r["failed_batches"] == 0 for r in results) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
