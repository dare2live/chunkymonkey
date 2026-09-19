#!/usr/bin/env python3
"""baostock ``query_history_k_data_plus`` 历史回填 -> ``raw_baostock_daily_k`` 水库
(一次性/按需回填工具, dry-run 默认)。

**为什么需要独立命令而不是重跑 daily** (2026-09-18, ST 契约 v2 刀2/刀3): daily
适配器每天顺手把当天的 baostock 查询灌进水库 (见 ``sync_runner._persist_
baostock_daily_k_evidence``), 但历史那些天的调用已经发生过、结果只在进程内缓存、
早就丢了 (F3)。**不许**为了补历史重跑 daily —— ``re-accept`` 会把那些历史分区的
``accepted_at`` 推到今天, ``usable_at = max(available_at, accepted_at)`` 会让它们
对中间任何决策时点都不可见, 这是 PIT 副作用 (红线 1)。本命令单开一个独立进程,
自己持 baostock 会话, 只灌水库不碰任何 accepted 分区。

**代码池** (三段全部本地只读):
  ``raw_tushare_stock_basic`` 全量 ts_code (身份表, 覆盖沪深京)
  ∪ ``canonical_nominal_ohlcv_daily`` 在 ``[--start, --end]`` 窗口内出现过的 ts_code
  ∪ ``canonical_stock_st_daily`` 最新一个 ``<= --end`` 分区里的 ts_code
再用 ``services.universe.is_active_a_share`` (与 ``fuyao_daily_k._is_bj_ts_code``
同一判定) 过滤成沪深 A 股 —— 北交所与 900/200 B 股一律不请求 (B 股是业主 09-12
明令范围外; 北交所 baostock 结构上无覆盖)。

**三段, 每段各自的失败语义不同**:
  ① 只读连接取代码池, close (与 baostock 会话不重叠, 无 U5 的"读写连接 vs 适配器
     自开只读连接同进程共存"疑虑)。
  ② 一个 ``BaostockSource()`` (进程级会话锁) 逐码 ``fetch_raw``。单码
     ``CALLER_ERROR`` (如参数/代码类错误) 记入 ``codes_skipped`` 继续下一码;
     会话级/网络级失败 (与 ``fuyao_daily_k._is_per_code_baostock_failure`` 同一
     判据) **立即停止取数**, 不重试不换码继续撞同一个已经出问题的服务端 ——
     带着已经取到的行进第③段, 最终以退出码 3 结束。
  ③ ``writer_lock`` + 读写连接 + ``record_baostock_daily_k_rows``, 写完 logout
     释放会话锁。即使②中途熔断, 已取到的行仍在这一段落库 (证据不因熔断而丢)。

**运行纪律** (与 daily_update / chunkyctl 互斥, baostock 单进程 + DuckDB 单写者):
跑前用 ``pgrep -fl "daily_update|chunkyctl|restamp|sync_runner|ingest_baostock"``
确认没有另一个进程在跑; 本脚本不会替你做这个检查 (那是运行时环境探测, 不是
输入校验)。

用法::

    # dry-run: 只打印代码池大小, 不触网不写库
    PYTHONPATH=backend python backend/scripts/ingest_baostock_daily_k_reservoir.py \\
        --start 20260828 --end 20260917

    # 真正回填 (先在生产库副本上验证)
    PYTHONPATH=backend python backend/scripts/ingest_baostock_daily_k_reservoir.py \\
        --start 20260828 --end 20260917 --db-override /tmp/copy.duckdb --execute

退出码: 0 = 全部代码取数成功 (dry-run 或 execute); 2 = 参数错误;
3 = 会话级/网络级失败中途熔断 (已取到的行仍落库)。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from services.data_sources.baostock_daily_k_reservoir import (  # noqa: E402
    ReservoirRow,
    record_baostock_daily_k_rows,
)
from services.data_sources.nominal_ohlcv_acquire_rules import (  # noqa: E402
    load_nominal_ohlcv_acquire_rules,
)
from services.data_sources.sources.fuyao_daily_k import (  # noqa: E402
    _exchange_prefix_code,
    _is_per_code_baostock_failure,
)
from services.universe import is_active_a_share  # noqa: E402
from services.writer_lock import writer_lock  # noqa: E402

_CODES_SOURCE_CHOICES = ("both", "stock_basic", "canonical_daily")


def _dashed(compact: str) -> str:
    text = str(compact)
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def _code_pool(conn: Any, *, start: str, end: str, codes_source: str) -> list[str]:
    """本地只读三段并集, 已用 ``is_active_a_share`` 过滤为沪深 A 股。"""

    codes: set[str] = set()
    if codes_source in ("both", "stock_basic"):
        codes.update(
            str(r[0])
            for r in conn.execute(
                "SELECT ts_code FROM raw_tushare_stock_basic WHERE ts_code IS NOT NULL"
            ).fetchall()
        )
    if codes_source in ("both", "canonical_daily"):
        codes.update(
            str(r[0])
            for r in conn.execute(
                """
                SELECT DISTINCT ts_code FROM canonical_nominal_ohlcv_daily
                 WHERE trade_date BETWEEN ? AND ?
                """,
                [date(int(start[:4]), int(start[4:6]), int(start[6:8])),
                 date(int(end[:4]), int(end[4:6]), int(end[6:8]))],
            ).fetchall()
        )
        latest_st_partition = conn.execute(
            """
            SELECT MAX(partition_value) FROM accepted_partition
             WHERE dataset_id = 'tier0.security_identity.stock_st_daily'
               AND partition_value <= ?
            """,
            [end],
        ).fetchone()
        if latest_st_partition and latest_st_partition[0]:
            st_day = latest_st_partition[0]
            codes.update(
                str(r[0])
                for r in conn.execute(
                    "SELECT ts_code FROM canonical_stock_st_daily WHERE trade_date = ?",
                    [date(int(st_day[:4]), int(st_day[4:6]), int(st_day[6:8]))],
                ).fetchall()
            )
    return sorted(code for code in codes if is_active_a_share(code))


def run(
    *,
    start: str,
    end: str,
    db_override: str | None,
    execute: bool,
    codes_source: str,
) -> tuple[dict[str, Any], int]:
    from services.data_access.resolver import db_path
    from services.data_sources.sources.baostock import BaostockSource
    from services.duck_adapter import connect

    target = db_override or str(db_path("tushare_raw"))
    rules = load_nominal_ohlcv_acquire_rules()

    ro = connect(target, read_only=True)
    try:
        codes = _code_pool(ro, start=start, end=end, codes_source=codes_source)
    finally:
        ro.close()

    result: dict[str, Any] = {
        "codes_requested": len(codes),
        "codes_ok": 0,
        "codes_skipped": [],
        "rows_seen": 0,
        "rows_inserted": 0,
        "rows_unchanged": 0,
        "dates_covered": {},
        "fetch_context": f"fill_script:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    }
    if not execute:
        return result, 0

    fetch_context = result["fetch_context"]
    exit_code = 0
    pending_rows: list[ReservoirRow] = []
    baostock = BaostockSource()
    try:
        for ts_code in codes:
            code = _exchange_prefix_code(ts_code, rules)
            try:
                raw_rows = baostock.fetch_raw(
                    rules.reference_api,
                    code=code,
                    fields=rules.baostock_fields_csv,
                    start_date=_dashed(start),
                    end_date=_dashed(end),
                ) or []
            except Exception as exc:  # noqa: BLE001 — 分类见 _is_per_code_baostock_failure
                if _is_per_code_baostock_failure(exc):
                    result["codes_skipped"].append(
                        {"ts_code": ts_code, "code_class": "caller_error", "err": str(exc)[:200]}
                    )
                    continue
                # 会话级/网络级失败: 立即停止取数, 带着已取到的行进第③段。
                result["codes_skipped"].append(
                    {"ts_code": ts_code, "code_class": "session_level", "err": str(exc)[:200]}
                )
                exit_code = 3
                break
            fetched_at = datetime.now(timezone.utc)
            for row in raw_rows:
                compact = str(row.get("date") or "").replace("-", "")
                if not compact:
                    continue
                pending_rows.append(
                    ReservoirRow(
                        ts_code=ts_code,
                        trade_date=compact,
                        fetched_at=fetched_at,
                        baostock_code=code,
                        fields_csv=rules.baostock_fields_csv,
                        payload=dict(row),
                        fetch_context=fetch_context,
                        request_start=start,
                        request_end=end,
                    )
                )
                result["dates_covered"][compact] = result["dates_covered"].get(compact, 0) + 1
            result["codes_ok"] += 1
    finally:
        try:
            baostock.logout()
        except Exception:  # rule-compliance: ok evidence=登出失败不掩盖上游真异常, 只记不抛
            pass

    with writer_lock("ingest_baostock_daily_k_reservoir"):
        rw = connect(target, read_only=False)
        try:
            outcome = record_baostock_daily_k_rows(rw, pending_rows)
        finally:
            rw.close()
    result["rows_seen"] = outcome.rows_seen
    result["rows_inserted"] = outcome.rows_inserted
    result["rows_unchanged"] = outcome.rows_unchanged
    return result, exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--start", required=True, help="YYYYMMDD (含)")
    parser.add_argument("--end", required=True, help="YYYYMMDD (含)")
    parser.add_argument("--db-override", default=None, help="改写目标库路径")
    parser.add_argument("--execute", action="store_true", help="实际触网+写库 (默认 dry-run)")
    parser.add_argument(
        "--codes-source", choices=_CODES_SOURCE_CHOICES, default="both",
        help="代码池来源 (默认 both: stock_basic ∪ canonical_daily/canonical_stock_st)",
    )
    args = parser.parse_args(argv)

    for label, value in (("--start", args.start), ("--end", args.end)):
        if not (len(value) == 8 and value.isdigit()):
            print(f"ingest_baostock_daily_k_reservoir: {label} 必须是 YYYYMMDD, got {value!r}",
                  file=sys.stderr)
            return 2
    if args.start > args.end:
        print("ingest_baostock_daily_k_reservoir: --start 不得晚于 --end", file=sys.stderr)
        return 2

    result, exit_code = run(
        start=args.start, end=args.end, db_override=args.db_override,
        execute=args.execute, codes_source=args.codes_source,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.execute:
        print("\n(dry-run; 加 --execute 才触网写库)")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
