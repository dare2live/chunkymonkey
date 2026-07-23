"""Build the current TuShare qfq analysis series and run post-build sanity checks.

This output is a derived serving/research input. It is not nominal execution-price truth.
Method = latest-factor rebase qfq; each rebuild stamps batch_id / ingested_at /
factor_as_of so consumers can pin a rebuild snapshot (historical levels rewrite when
latest adj_factor changes — typed method, not missing lineage).

daily_update Step 2.96 rebuilds price_kline_qfq_tushare in the market DuckDB.
Default mode = incremental when the table exists:
  - stocks whose latest adj_factor (factor_as_of) changed → rewrite full history
  - unchanged stocks → append only new trade_dates
  - never leave stale pre-rebase levels (silent wrong history banned)
Full DROP+CTAS remains available via --full (and is used when the table is missing).
Post-full-CTAS compact reclaim stays in-module (escape: --no-compact /
CHUNKY_QFQ_SKIP_COMPACT=1). Incremental path skips compact (no DROP free-block storm).

前复权 (qfq rebased to latest): qfq = nominal × adj_factor / adj_factor_latest_per_stock。
  nominal (S7 default): accepted canonical_nominal_ohlcv_daily only.
  nominal (--allow-legacy-fill): canonical ∪ legacy raw_tushare_daily
  返回 (收益) = qfq[t]/qfq[t-1] = 含分红总收益 (PIT: f[t] 除权日即知)。
  单位: volume 手×100=股, amount 千元×1000=元。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.duck_adapter import connect  # noqa: E402

MARKET_DB = "data/market.duckdb"  # rule-compliance: ok evidence=回测K线库 (与 experiment_l0_baseline _db('market') 同库, 一次性 build 脚本)
TUSHARE_DB = str(REPO / "data" / "tushare_raw.duckdb")  # rule-compliance: ok evidence=tushare raw 源库 (ATTACH read-only, 一次性 build 脚本)
TARGET = "price_kline_qfq_tushare"
START = "20190101"  # rule-compliance: ok evidence=raw_tushare_daily 实测起点 2019-01-02 (全量回测窗起点)
# DROP+CTAS leaves ~half the file as free blocks; CHECKPOINT does not shrink.
# Default post-full-build compact reclaim (escape: --no-compact / CHUNKY_QFQ_SKIP_COMPACT=1).
_COMPACT_SCRIPT = REPO / "backend" / "scripts" / "db_compact.py"

BuildMode = Literal["full", "incremental", "auto"]

# Accepted canonical wins on overlap; legacy raw fills pre-canary history only.
# Project-universe whitelist: qfq is 沪深A analysis surface — never copy BJ/B.
from services.universe import sql_where_active_a_share as _sql_ashare

_NOMINAL_SOURCE_CTE = f"""
nominal AS (
    SELECT
        c.ts_code,
        strftime(c.trade_date, '%Y%m%d') AS trade_date,
        c.open, c.high, c.low, c.close, c.vol, c.amount
    FROM tr.canonical_nominal_ohlcv_daily c
    WHERE c.trade_date >= DATE '2019-01-01'
      AND {_sql_ashare("c.ts_code")}
    UNION ALL
    SELECT
        r.ts_code, r.trade_date, r.open, r.high, r.low, r.close, r.vol, r.amount
    FROM tr.raw_tushare_daily r
    WHERE r.trade_date >= '{START}'
      AND {_sql_ashare("r.ts_code")}
      AND NOT EXISTS (
          SELECT 1
          FROM tr.canonical_nominal_ohlcv_daily c
          WHERE c.ts_code = r.ts_code
            AND strftime(c.trade_date, '%Y%m%d') = r.trade_date
      )
)
"""

# S5: accepted-only derive — no legacy raw nominal fill (adj_factor still from raw table).
_NOMINAL_SOURCE_CTE_FROM_ACCEPTED = f"""
nominal AS (
    SELECT
        c.ts_code,
        strftime(c.trade_date, '%Y%m%d') AS trade_date,
        c.open, c.high, c.low, c.close, c.vol, c.amount
    FROM tr.canonical_nominal_ohlcv_daily c
    WHERE c.trade_date >= DATE '2019-01-01'
      AND {_sql_ashare("c.ts_code")}
)
"""


def nominal_source_cte(*, from_accepted: bool = True) -> str:
    """Return nominal CTE; default skips legacy raw fill (S7)."""

    if from_accepted:
        return _NOMINAL_SOURCE_CTE_FROM_ACCEPTED
    return _NOMINAL_SOURCE_CTE


def _default_batch_id(*, from_accepted: bool, ingested_at: str, mode: str) -> str:
    src = "from_accepted" if from_accepted else "legacy_fill"
    stamp = (
        ingested_at.replace("-", "")
        .replace(":", "")
        .replace("T", "")
        .replace("Z", "")
    )
    return f"qfq:{stamp}:{src}:{mode}"


def _factor_as_of_expr(alias: str = "l") -> str:
    return (
        f"substr(replace(CAST({alias}.factor_as_of_ymd AS VARCHAR), '-', ''), 1, 4)||'-'"
        f"||substr(replace(CAST({alias}.factor_as_of_ymd AS VARCHAR), '-', ''), 5, 2)||'-'"
        f"||substr(replace(CAST({alias}.factor_as_of_ymd AS VARCHAR), '-', ''), 7, 2)"
    )


def _qfq_select_sql(
    *,
    bid_sql: str,
    built_sql: str,
    from_accepted: bool,
    code_predicate: str | None = None,
    date_gt_iso: str | None = None,
) -> str:
    """Shared SELECT body for full / rewrite / append slices."""

    nominal_cte = nominal_source_cte(from_accepted=from_accepted)
    preds = [
        f"d.trade_date >= '{START}'",
        "d.close > 0",
        "a.adj_factor > 0",
        "l.f_latest > 0",
    ]
    if code_predicate:
        preds.append(code_predicate)
    if date_gt_iso:
        # trade_date is YYYYMMDD; compare to ISO date as compact.
        compact = date_gt_iso.replace("-", "")
        preds.append(f"d.trade_date > '{compact}'")
    where = " AND ".join(preds)
    return f"""
        WITH {nominal_cte},
        latest AS (
            SELECT ts_code, adj_factor AS f_latest, trade_date AS factor_as_of_ymd FROM (
                SELECT ts_code, adj_factor, trade_date,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM tr.raw_tushare_adj_factor) WHERE rn = 1
        )
        SELECT
            substr(d.ts_code, 1, 6) AS code,
            substr(d.trade_date,1,4)||'-'||substr(d.trade_date,5,2)||'-'||substr(d.trade_date,7,2) AS date,
            d.open  * a.adj_factor / l.f_latest AS open,
            d.high  * a.adj_factor / l.f_latest AS high,
            d.low   * a.adj_factor / l.f_latest AS low,
            d.close * a.adj_factor / l.f_latest AS close,
            d.vol * 100.0 AS volume,
            d.amount * 1000.0 AS amount,
            '{bid_sql}' AS batch_id,
            CAST('{built_sql}' AS TIMESTAMP) AS ingested_at,
            {_factor_as_of_expr("l")} AS factor_as_of
        FROM nominal d
        JOIN tr.raw_tushare_adj_factor a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
        JOIN latest l ON d.ts_code = l.ts_code
        WHERE {where}
    """


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'main' AND table_name = ?
         LIMIT 1
        """,
        [name],
    ).fetchone()
    return row is not None


def _has_lineage_columns(conn) -> bool:
    cols = {
        str(r[0] if not hasattr(r, "keys") else r["column_name"]).lower()
        for r in conn.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'main' AND table_name = ?
            """,
            [TARGET],
        ).fetchall()
    }
    return {"batch_id", "ingested_at", "factor_as_of", "code", "date", "close"} <= cols


def build_full(
    conn,
    *,
    from_accepted: bool = True,
    batch_id: str | None = None,
    ingested_at: str | None = None,
) -> dict[str, Any]:
    """DROP+CTAS full rebuild (latest-adj semantics)."""

    built_at = ingested_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bid = batch_id or _default_batch_id(
        from_accepted=from_accepted, ingested_at=built_at, mode="full"
    )
    bid_sql = bid.replace("'", "''")
    built_sql = built_at.replace("'", "''")

    conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")
    conn.execute(f"DROP TABLE IF EXISTS {TARGET}")
    conn.execute(
        f"CREATE TABLE {TARGET} AS {_qfq_select_sql(bid_sql=bid_sql, built_sql=built_sql, from_accepted=from_accepted)}"
    )
    n = int(conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0])
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET}_cd ON {TARGET}(code, date)")
    return {
        "mode": "full",
        "rows": n,
        "batch_id": bid,
        "rewritten_codes": None,
        "appended_rows": None,
    }


def build_incremental(
    conn,
    *,
    from_accepted: bool = True,
    batch_id: str | None = None,
    ingested_at: str | None = None,
) -> dict[str, Any]:
    """Incremental rebuild with correct latest-adj semantics.

    - factor_as_of change for a stock → DELETE all rows for that code, reinsert
      full history (prevents silent stale pre-rebase levels).
    - unchanged factor → append only dates after local max(date).
    - new codes → insert full history.
    Falls back to full when the table is missing or lacks lineage columns.
    """

    if not _table_exists(conn, TARGET) or not _has_lineage_columns(conn):
        return build_full(
            conn,
            from_accepted=from_accepted,
            batch_id=batch_id,
            ingested_at=ingested_at,
        )

    built_at = ingested_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bid = batch_id or _default_batch_id(
        from_accepted=from_accepted, ingested_at=built_at, mode="incremental"
    )
    bid_sql = bid.replace("'", "''")
    built_sql = built_at.replace("'", "''")

    conn.execute(f"ATTACH IF NOT EXISTS '{TUSHARE_DB}' AS tr (READ_ONLY)")

    conn.execute("DROP TABLE IF EXISTS _qfq_latest_factor")
    conn.execute(
        f"""
        CREATE TEMP TABLE _qfq_latest_factor AS
        SELECT
            substr(ts_code, 1, 6) AS code,
            f_latest,
            {_factor_as_of_expr("x")} AS factor_as_of
        FROM (
            SELECT ts_code, adj_factor AS f_latest, trade_date AS factor_as_of_ymd,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
            FROM tr.raw_tushare_adj_factor
        ) x
        WHERE rn = 1
        """
    )

    conn.execute("DROP TABLE IF EXISTS _qfq_existing")
    conn.execute(
        f"""
        CREATE TEMP TABLE _qfq_existing AS
        SELECT code,
               max(factor_as_of) AS factor_as_of,
               max(date) AS max_date
          FROM {TARGET}
         GROUP BY code
        """
    )

    # f_latest value at the previously stamped factor_as_of (value drift ⇒ rewrite).
    conn.execute("DROP TABLE IF EXISTS _qfq_prior_f")
    conn.execute(
        """
        CREATE TEMP TABLE _qfq_prior_f AS
        SELECT e.code, a.adj_factor AS f_prior
          FROM _qfq_existing e
          JOIN tr.raw_tushare_adj_factor a
            ON substr(a.ts_code, 1, 6) = e.code
           AND substr(replace(CAST(a.trade_date AS VARCHAR), '-', ''), 1, 8)
               = replace(e.factor_as_of, '-', '')
        """
    )

    conn.execute("DROP TABLE IF EXISTS _qfq_rewrite_codes")
    conn.execute(
        """
        CREATE TEMP TABLE _qfq_rewrite_codes AS
        SELECT l.code
          FROM _qfq_latest_factor l
          LEFT JOIN _qfq_existing e ON e.code = l.code
          LEFT JOIN _qfq_prior_f p ON p.code = l.code
         WHERE e.code IS NULL
            OR p.f_prior IS NULL
            OR abs(l.f_latest - p.f_prior) > 1e-12
        """
    )
    rewritten = int(
        conn.execute("SELECT count(*) FROM _qfq_rewrite_codes").fetchone()[0]
    )

    if rewritten:
        conn.execute(
            f"""
            DELETE FROM {TARGET}
             WHERE code IN (SELECT code FROM _qfq_rewrite_codes)
            """
        )
        pred = (
            "substr(d.ts_code, 1, 6) IN (SELECT code FROM _qfq_rewrite_codes)"
        )
        conn.execute(
            f"""
            INSERT INTO {TARGET}
            {_qfq_select_sql(
                bid_sql=bid_sql,
                built_sql=built_sql,
                from_accepted=from_accepted,
                code_predicate=pred,
            )}
            """
        )

    # Append new dates for factor-stable existing codes (set-based; date-gated).
    before_n = int(conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0])
    conn.execute("DROP TABLE IF EXISTS _qfq_stable")
    conn.execute(
        """
        CREATE TEMP TABLE _qfq_stable AS
        SELECT e.code, e.max_date,
               replace(e.max_date, '-', '') AS max_ymd,
               l.factor_as_of AS new_factor_as_of
          FROM _qfq_existing e
          JOIN _qfq_latest_factor l ON l.code = e.code
         WHERE e.code NOT IN (SELECT code FROM _qfq_rewrite_codes)
        """
    )
    stable_n = int(conn.execute("SELECT count(*) FROM _qfq_stable").fetchone()[0])
    if stable_n:
        # Lineage: advance factor_as_of when latest date moved but f_latest value same.
        conn.execute(
            f"""
            UPDATE {TARGET} t
               SET factor_as_of = s.new_factor_as_of,
                   batch_id = '{bid_sql}',
                   ingested_at = CAST('{built_sql}' AS TIMESTAMP)
              FROM _qfq_stable s
             WHERE t.code = s.code
               AND t.factor_as_of IS DISTINCT FROM s.new_factor_as_of
            """
        )
        nominal_cte = nominal_source_cte(from_accepted=from_accepted)
        conn.execute(
            f"""
            INSERT INTO {TARGET}
            WITH {nominal_cte},
            latest AS (
                SELECT ts_code, adj_factor AS f_latest, trade_date AS factor_as_of_ymd FROM (
                    SELECT ts_code, adj_factor, trade_date,
                           ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                    FROM tr.raw_tushare_adj_factor) WHERE rn = 1
            )
            SELECT
                substr(d.ts_code, 1, 6) AS code,
                substr(d.trade_date,1,4)||'-'||substr(d.trade_date,5,2)||'-'||substr(d.trade_date,7,2) AS date,
                d.open  * a.adj_factor / l.f_latest AS open,
                d.high  * a.adj_factor / l.f_latest AS high,
                d.low   * a.adj_factor / l.f_latest AS low,
                d.close * a.adj_factor / l.f_latest AS close,
                d.vol * 100.0 AS volume,
                d.amount * 1000.0 AS amount,
                '{bid_sql}' AS batch_id,
                CAST('{built_sql}' AS TIMESTAMP) AS ingested_at,
                {_factor_as_of_expr("l")} AS factor_as_of
            FROM nominal d
            JOIN _qfq_stable st ON st.code = substr(d.ts_code, 1, 6)
            JOIN tr.raw_tushare_adj_factor a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date
            JOIN latest l ON d.ts_code = l.ts_code
            WHERE d.trade_date >= '{START}'
              AND d.trade_date > st.max_ymd
              AND d.close > 0 AND a.adj_factor > 0 AND l.f_latest > 0
            """
        )
    after_n = int(conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0])
    appended = max(0, after_n - before_n)

    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TARGET}_cd ON {TARGET}(code, date)")
    n = int(conn.execute(f"SELECT count(*) FROM {TARGET}").fetchone()[0])
    return {
        "mode": "incremental",
        "rows": n,
        "batch_id": bid,
        "rewritten_codes": rewritten,
        "appended_rows": appended,
    }


def build(
    conn,
    *,
    from_accepted: bool = True,
    batch_id: str | None = None,
    ingested_at: str | None = None,
    mode: BuildMode = "auto",
) -> int:
    """Build qfq table. Returns row count (compat). See build_detail for mode stats."""

    detail = build_detail(
        conn,
        from_accepted=from_accepted,
        batch_id=batch_id,
        ingested_at=ingested_at,
        mode=mode,
    )
    build.last_detail = detail  # type: ignore[attr-defined]
    return int(detail["rows"])


def build_detail(
    conn,
    *,
    from_accepted: bool = True,
    batch_id: str | None = None,
    ingested_at: str | None = None,
    mode: BuildMode = "auto",
) -> dict[str, Any]:
    """Build with explicit mode accounting."""

    resolved: BuildMode = mode
    if mode == "auto":
        resolved = (
            "incremental"
            if _table_exists(conn, TARGET) and _has_lineage_columns(conn)
            else "full"
        )
    if resolved == "incremental":
        return build_incremental(
            conn,
            from_accepted=from_accepted,
            batch_id=batch_id,
            ingested_at=ingested_at,
        )
    return build_full(
        conn,
        from_accepted=from_accepted,
        batch_id=batch_id,
        ingested_at=ingested_at,
    )


# measured: 2026-07-02 实测基线 8,319,172 行 / 5,431 股; floor 留 ~10% 缓冲防日常波动误报
MIN_ROWS = 7_500_000
# S7 from-accepted after daily-only expand to 20190102: same span as legacy fill.
MIN_ROWS_FROM_ACCEPTED = 7_500_000
MIN_CODES = 5_000


def cross_check(conn) -> dict:
    """重建后自完整性 sanity (2026-07-02 批7 重写 — 死闸修复)。"""
    row = conn.execute(f"""
        SELECT count(*)                                        AS n_rows,
               count(DISTINCT code)                            AS n_codes,
               max(date)                                       AS max_date,
               sum(CASE WHEN close IS NULL OR close <= 0
                         OR high < low THEN 1 ELSE 0 END)      AS n_bad_price,
               sum(CASE WHEN batch_id IS NULL OR ingested_at IS NULL
                         OR factor_as_of IS NULL THEN 1 ELSE 0 END) AS n_missing_lineage
        FROM {TARGET}
    """).fetchone()
    return {
        "n_rows": row[0],
        "n_codes": row[1],
        "max_date": row[2],
        "n_bad_price": row[3],
        "n_missing_lineage": row[4],
    }


def _load_db_compact():
    spec = importlib.util.spec_from_file_location("db_compact", _COMPACT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load db_compact at {_COMPACT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compact_market_after_ctas(*, remove_bak: bool = True) -> int:
    """Reclaim free blocks after DROP+CTAS. Caller must close market writers first.

    Only runs against the production market path from database_manifest (skip if
    MARKET_DB was redirected, e.g. tests/tmp).
    """

    compact = _load_db_compact()
    prod = Path(compact._db_path("market")).resolve()
    market_path = Path(MARKET_DB)
    if not market_path.is_absolute():
        market_path = (REPO / market_path).resolve()
    else:
        market_path = market_path.resolve()
    if market_path != prod:
        print(
            f"[compact] skip — MARKET_DB={market_path} != production {prod}",
            flush=True,
        )
        return 0
    rc = int(compact.run("market", execute=True))
    if rc != 0:
        return rc
    if remove_bak:
        bak = prod.with_name(f"{prod.stem}_precompact_bak{prod.suffix}")
        if bak.exists():
            bak.unlink()
            print(f"[compact] removed {bak}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true")  # rule-compliance: ok evidence=只对账不重建
    ap.add_argument(
        "--no-compact",
        action="store_true",
        help="Skip post-CTAS market db_compact (default: compact after successful full rebuild)",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="Force DROP+CTAS full rebuild (default: incremental when table exists)",
    )
    ap.add_argument(
        "--incremental",
        action="store_true",
        help="Force incremental path (falls back to full if table missing)",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--from-accepted",
        action="store_true",
        help=(
            "S7 default: rebuild qfq from accepted canonical_nominal_ohlcv_daily only "
            "(no legacy raw_tushare_daily fill). Does not run inside accept."
        ),
    )
    mode.add_argument(
        "--allow-legacy-fill",
        action="store_true",
        help="S7 escape: canonical ∪ legacy raw_tushare_daily fill (pre-accepted history)",
    )
    args = ap.parse_args(argv)
    from_accepted = not bool(args.allow_legacy_fill)
    if args.full and args.incremental:
        print("[verdict] FAIL mutually exclusive --full and --incremental", flush=True)
        return 2
    build_mode: BuildMode = "auto"
    if args.full:
        build_mode = "full"
    elif args.incremental:
        build_mode = "incremental"

    rebuilt = False
    used_mode = "none"
    detail: dict[str, Any] = {}
    conn = connect(MARKET_DB, read_only=False)
    try:
        if not args.check_only:
            detail = build_detail(conn, from_accepted=from_accepted, mode=build_mode)
            used_mode = str(detail["mode"])
            rebuilt = True
            mode_name = "from_accepted" if from_accepted else "canonical_plus_legacy_fill"
            r = conn.execute(
                f"SELECT min(date),max(date),count(DISTINCT code), "
                f"count(DISTINCT batch_id), min(factor_as_of), max(factor_as_of) "
                f"FROM {TARGET}"
            ).fetchone()
            print(
                f"[build] {TARGET}: {detail['rows']:,} 行 | {r[0]}~{r[1]} | {r[2]} 股 | "
                f"mode={mode_name}/{used_mode} | batches={r[3]} | "
                f"factor_as_of={r[4]}~{r[5]} | "
                f"rewritten_codes={detail.get('rewritten_codes')} "
                f"appended_rows={detail.get('appended_rows')}",
                flush=True,
            )
        cc = cross_check(conn)
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    print(
        f"[sanity] {TARGET}: rows={cc['n_rows']:,} codes={cc['n_codes']:,} "
        f"max_date={cc['max_date']} bad_price={cc['n_bad_price']:,} "
        f"missing_lineage={cc['n_missing_lineage']:,}"
    )
    min_rows = MIN_ROWS_FROM_ACCEPTED if from_accepted else MIN_ROWS
    ok = (
        cc["n_rows"] >= min_rows
        and cc["n_codes"] >= MIN_CODES
        and cc["n_bad_price"] == 0
        and cc["n_missing_lineage"] == 0
    )
    print(
        f"[verdict] {'PASS 自完整性检查通过' if ok else 'REVIEW 行数/覆盖缩水或含非法价格/缺 lineage, 先查再消费'}"
    )
    if not ok:
        return 2
    skip_compact = bool(args.no_compact) or os.environ.get(
        "CHUNKY_QFQ_SKIP_COMPACT", ""
    ).strip() in {"1", "true", "TRUE", "yes", "YES"}
    # Compact only after full DROP+CTAS (free-block reclaim). Incremental skips.
    if rebuilt and used_mode == "full" and not skip_compact:
        crc = compact_market_after_ctas(remove_bak=True)
        if crc != 0:
            print(
                f"[compact] FAIL rc={crc} — qfq rows OK but free-block residual remains",
                flush=True,
            )
            return 3
    elif rebuilt and used_mode == "full" and skip_compact:
        print("[compact] skipped (--no-compact or CHUNKY_QFQ_SKIP_COMPACT)", flush=True)
    elif rebuilt and used_mode == "incremental":
        print("[compact] skipped (incremental — no DROP+CTAS free-block)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
