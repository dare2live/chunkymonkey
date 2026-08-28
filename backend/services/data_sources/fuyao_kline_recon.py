"""Read-only Fuyao dump vs accepted nominal daily OHLCV reconciliation.

Baseline is ``canonical_nominal_ohlcv_daily`` (plus dividend / adj_factor
sampling). ``raw_tushare_daily`` is a stopped fill table and is rejected as
baseline. ``price_kline_qfq_tushare`` is derived qfq and is not SSOT. This module does not change ``kline_daily.primary`` or write
accepted partitions.

Dump kinds are probed independently: one HTTP 404 is not "API offline".
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ACCEPTED_K_TABLE = "canonical_nominal_ohlcv_daily"
BANNED_BASELINE_TABLES = frozenset({"raw_tushare_daily", "price_kline_qfq_tushare"})
DUMP_KIND_VALUES = ("daily-k", "daily-k-10d", "adjustment-factors")
PRICE_ABS_TOL = 0.011
DOCUMENTED_VOL_SCALE = 100.0  # Fuyao share / TuShare lot
DOCUMENTED_AMOUNT_SCALE = 1000.0  # Fuyao CNY / TuShare CNY_thousand
VOL_RATIO_CONFIRM = (80.0, 120.0)
AMOUNT_RATIO_CONFIRM = (800.0, 1200.0)
SHANGHAI_OFFSET_MS = 8 * 60 * 60 * 1000
SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class DumpKindProbe:
    kind: str
    outcome: str
    message: str = ""
    http_hint: str = ""


def shanghai_date_sql(column: str) -> str:
    return f"CAST(epoch_ms({column} + {SHANGHAI_OFFSET_MS}) AS DATE)"


def code_prefix_sql(ts_code_expr: str) -> str:
    return (
        f"substr(split_part({ts_code_expr}, '.', 1), 1, 3) || '.' || "
        f"split_part({ts_code_expr}, '.', 2)"
    )


def as_date_sql(expr: str) -> str:
    """DATE or YYYYMMDD / dashed varchar → DATE."""
    return (
        f"CASE"
        f" WHEN {expr} IS NULL THEN NULL"
        f" WHEN typeof({expr}) = 'DATE' THEN CAST({expr} AS DATE)"
        f" WHEN length(trim(CAST({expr} AS VARCHAR))) = 8 "
        f"  THEN try_strptime(trim(CAST({expr} AS VARCHAR)), '%Y%m%d')::DATE"
        f" ELSE try_cast(trim(CAST({expr} AS VARCHAR)) AS DATE) END"
    )


def _sql_str(path: Path) -> str:
    return str(path).replace("'", "''")


def reject_banned_baseline(table: str) -> str:
    name = str(table).split(".")[-1].strip('"')
    if name in BANNED_BASELINE_TABLES:
        raise ValueError(
            f"banned baseline {table!r}; use {ACCEPTED_K_TABLE} "
            "(legacy_raw_plane: raw_tushare_daily is fill, not recon truth)"
        )
    return table


def classify_dump_failure(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc)
    lower = msg.lower()
    if name == "DumpAuthError":
        return "auth"
    if name == "DumpNotReadyError":
        return "not_ready"
    if "http 404" in lower or "status_code=404" in lower:
        return "http_404"
    if "http 403" in lower:
        return "http_403"
    if name == "DumpInternalError":
        return "internal"
    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if "connection" in lower or "nameresolution" in lower:
        return "transport"
    return "error"


def probe_dump_kinds(
    sign_fn: Callable[[str], Mapping[str, Any]],
    kinds: Iterable[str] = DUMP_KIND_VALUES,
) -> list[DumpKindProbe]:
    """Sign every dump kind. Failures are recorded; later kinds still run."""
    probes: list[DumpKindProbe] = []
    for kind in kinds:
        try:
            envelope = sign_fn(kind)
            url = str((envelope or {}).get("presigned_url") or "")
            probes.append(
                DumpKindProbe(
                    kind=kind,
                    outcome="ok",
                    message="signed",
                    http_hint="presigned" if url else "missing_url",
                )
            )
        except Exception as exc:  # noqa: BLE001 — classify, don't swallow catalog
            probes.append(
                DumpKindProbe(
                    kind=kind,
                    outcome=classify_dump_failure(exc),
                    message=str(exc)[:240],
                )
            )
    return probes


def dump_catalog_status(probes: Iterable[DumpKindProbe]) -> str:
    outcomes = {p.kind: p.outcome for p in probes}
    if outcomes and all(v == "ok" for v in outcomes.values()):
        return "ready"
    if any(v == "ok" for v in outcomes.values()):
        return "partial_or_ready"
    if any(v == "auth" for v in outcomes.values()):
        return "auth"
    if all(v == "not_ready" for v in outcomes.values()) and outcomes:
        return "not_ready"
    if outcomes and all(v == "http_404" for v in outcomes.values()):
        return "all_http_404"
    return "unavailable"


def load_fuyao_kline(con: Any, parquet_path: Path, *, table: str = "fuyao_k") -> None:
    path = _sql_str(Path(parquet_path))
    date_expr = shanghai_date_sql("date_ms")
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT
            thscode AS ts_code,
            {date_expr} AS trade_date,
            open_price AS open,
            high_price AS high,
            low_price AS low,
            close_price AS close,
            volume AS volume_share,
            turnover AS turnover_cny,
            adjusted
        FROM read_parquet('{path}')
        WHERE COALESCE(adjusted, 'none') = 'none'
          AND thscode IS NOT NULL
          AND date_ms IS NOT NULL
        """
    )


def load_fuyao_events(con: Any, parquet_path: Path, *, table: str = "fuyao_adj_events") -> None:
    path = _sql_str(Path(parquet_path))
    ex_expr = shanghai_date_sql("ex_date_ms")
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT
            thscode AS ts_code,
            {ex_expr} AS ex_date,
            dividend_per_share,
            per_share_bonus,
            allotment_ratio,
            allotment_price
        FROM read_parquet('{path}')
        WHERE thscode IS NOT NULL
          AND ex_date_ms IS NOT NULL
        """
    )


def _fetchall(con: Any, sql: str, params: Any = None) -> list[Any]:
    cur = con.execute(sql) if params is None else con.execute(sql, params)
    rows = cur.fetchall()
    return list(rows or [])


def _row_map(con: Any, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    row = cur.fetchone()
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(k): row[k] for k in row.keys()}
    cols = [d[0] for d in (cur.description or [])]
    return dict(zip(cols, row))


def compare_kline(
    con: Any,
    *,
    fuyao_table: str = "fuyao_k",
    accepted_table: str = ACCEPTED_K_TABLE,
    documented_vol_scale: float = DOCUMENTED_VOL_SCALE,
    documented_amount_scale: float = DOCUMENTED_AMOUNT_SCALE,
    vol_ratio_confirm: tuple[float, float] = VOL_RATIO_CONFIRM,
    amount_ratio_confirm: tuple[float, float] = AMOUNT_RATIO_CONFIRM,
) -> dict[str, Any]:
    accepted_table = reject_banned_baseline(accepted_table)
    prefix = code_prefix_sql("ts_code")
    window = _row_map(
        con,
        f"SELECT min(trade_date) AS min_d, max(trade_date) AS max_d, "
        f"count(*)::BIGINT AS n FROM {fuyao_table}",
    )
    con.execute("DROP TABLE IF EXISTS _recon_fy")
    con.execute("DROP TABLE IF EXISTS _recon_acc")
    con.execute(
        f"""
        CREATE TABLE _recon_fy AS
        SELECT ts_code, trade_date, open, high, low, close,
               volume_share, turnover_cny, {prefix} AS code_prefix
        FROM {fuyao_table}
        """
    )
    con.execute(
        f"""
        CREATE TABLE _recon_acc AS
        SELECT ts_code, trade_date, open, high, low, close, vol, amount,
               {prefix} AS code_prefix
        FROM {accepted_table}
        WHERE trade_date BETWEEN (SELECT min(trade_date) FROM _recon_fy)
          AND (SELECT max(trade_date) FROM _recon_fy)
        """
    )
    grain = _row_map(
        con,
        """
        SELECT
          (SELECT count(*) FROM _recon_fy) AS fuyao_rows,
          (SELECT count(*) FROM _recon_acc) AS accepted_rows_in_window,
          (SELECT count(*) FROM _recon_fy f
             JOIN _recon_acc a USING (ts_code, trade_date)) AS intersection,
          (SELECT count(*) FROM _recon_fy f
             WHERE NOT EXISTS (
               SELECT 1 FROM _recon_acc a
               WHERE a.ts_code = f.ts_code AND a.trade_date = f.trade_date
             )) AS only_fuyao,
          (SELECT count(*) FROM _recon_acc a
             WHERE NOT EXISTS (
               SELECT 1 FROM _recon_fy f
               WHERE f.ts_code = a.ts_code AND f.trade_date = a.trade_date
             )) AS only_accepted
        """
    )
    by_date = [
        {
            "trade_date": str(r[0]),
            "only_fuyao": int(r[1]),
            "only_accepted": int(r[2]),
            "intersection": int(r[3]),
        }
        for r in _fetchall(
            con,
            """
            WITH u AS (
              SELECT ts_code, trade_date FROM _recon_fy
              UNION
              SELECT ts_code, trade_date FROM _recon_acc
            )
            SELECT
              u.trade_date,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_fy f
                              WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
                  AND NOT EXISTS (SELECT 1 FROM _recon_acc a
                                  WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
              ) AS only_fuyao,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_acc a
                              WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
                  AND NOT EXISTS (SELECT 1 FROM _recon_fy f
                                  WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
              ) AS only_accepted,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_fy f
                              WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
                  AND EXISTS (SELECT 1 FROM _recon_acc a
                              WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
              ) AS intersection
            FROM u
            GROUP BY 1
            ORDER BY 1
            """,
        )
    ]
    by_prefix = [
        {
            "code_prefix": r[0],
            "only_fuyao": int(r[1]),
            "only_accepted": int(r[2]),
            "intersection": int(r[3]),
        }
        for r in _fetchall(
            con,
            """
            WITH keys AS (
              SELECT ts_code, trade_date, code_prefix FROM _recon_fy
              UNION ALL
              SELECT ts_code, trade_date, code_prefix FROM _recon_acc
            ),
            u AS (
              SELECT DISTINCT ts_code, trade_date, code_prefix FROM keys
            )
            SELECT
              u.code_prefix,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_fy f
                              WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
                  AND NOT EXISTS (SELECT 1 FROM _recon_acc a
                                  WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
              ) AS only_fuyao,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_acc a
                              WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
                  AND NOT EXISTS (SELECT 1 FROM _recon_fy f
                                  WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
              ) AS only_accepted,
              count(*) FILTER (
                WHERE EXISTS (SELECT 1 FROM _recon_fy f
                              WHERE f.ts_code = u.ts_code AND f.trade_date = u.trade_date)
                  AND EXISTS (SELECT 1 FROM _recon_acc a
                              WHERE a.ts_code = u.ts_code AND a.trade_date = u.trade_date)
              ) AS intersection
            FROM u
            GROUP BY 1
            ORDER BY (only_fuyao + only_accepted) DESC, code_prefix
            """,
        )
    ]
    ohlc = _row_map(
        con,
        f"""
        SELECT
          count(*) FILTER (
            WHERE abs(f.open - a.open) <= {PRICE_ABS_TOL}
              AND abs(f.high - a.high) <= {PRICE_ABS_TOL}
              AND abs(f.low - a.low) <= {PRICE_ABS_TOL}
              AND abs(f.close - a.close) <= {PRICE_ABS_TOL}
          ) AS ohlc_match,
          count(*) FILTER (
            WHERE abs(f.open - a.open) > {PRICE_ABS_TOL}
               OR abs(f.high - a.high) > {PRICE_ABS_TOL}
               OR abs(f.low - a.low) > {PRICE_ABS_TOL}
               OR abs(f.close - a.close) > {PRICE_ABS_TOL}
          ) AS ohlc_mismatch
        FROM _recon_fy f
        JOIN _recon_acc a USING (ts_code, trade_date)
        """,
    )
    ratios = _row_map(
        con,
        """
        SELECT
          median(f.volume_share / nullif(a.vol, 0)) AS vol_ratio_median,
          median(f.turnover_cny / nullif(a.amount, 0)) AS amount_ratio_median,
          count(*) FILTER (WHERE a.vol > 0) AS vol_ratio_n,
          count(*) FILTER (WHERE a.amount > 0) AS amount_ratio_n
        FROM _recon_fy f
        JOIN _recon_acc a USING (ts_code, trade_date)
        """,
    )
    vol_median = ratios.get("vol_ratio_median")
    amt_median = ratios.get("amount_ratio_median")
    vol_ok = (
        vol_median is not None
        and vol_ratio_confirm[0] <= float(vol_median) <= vol_ratio_confirm[1]
    )
    amt_ok = (
        amt_median is not None
        and amount_ratio_confirm[0] <= float(amt_median) <= amount_ratio_confirm[1]
    )
    scaled = _row_map(
        con,
        f"""
        SELECT
          count(*) FILTER (
            WHERE abs(f.volume_share / {documented_vol_scale} - a.vol)
                  > greatest(0.5, abs(a.vol) * 1e-4)
          ) AS vol_mismatch_after_scale,
          count(*) FILTER (
            WHERE abs(f.turnover_cny / {documented_amount_scale} - a.amount)
                  > greatest(0.05, abs(a.amount) * 1e-4)
          ) AS amount_mismatch_after_scale
        FROM _recon_fy f
        JOIN _recon_acc a USING (ts_code, trade_date)
        """,
    )
    ohlc_samples = [
        {
            "ts_code": r[0],
            "trade_date": str(r[1]),
            "fuyao_close": r[2],
            "accepted_close": r[3],
            "abs_diff": r[4],
        }
        for r in _fetchall(
            con,
            f"""
            SELECT f.ts_code, f.trade_date, f.close, a.close,
                   abs(f.close - a.close) AS abs_diff
            FROM _recon_fy f
            JOIN _recon_acc a USING (ts_code, trade_date)
            WHERE abs(f.close - a.close) > {PRICE_ABS_TOL}
            ORDER BY abs_diff DESC
            LIMIT {SAMPLE_LIMIT}
            """,
        )
    ]
    only_fuyao_samples = [
        {"ts_code": r[0], "trade_date": str(r[1]), "code_prefix": r[2]}
        for r in _fetchall(
            con,
            f"""
            SELECT f.ts_code, f.trade_date, f.code_prefix
            FROM _recon_fy f
            WHERE NOT EXISTS (
              SELECT 1 FROM _recon_acc a
              WHERE a.ts_code = f.ts_code AND a.trade_date = f.trade_date
            )
            ORDER BY f.code_prefix, f.ts_code, f.trade_date
            LIMIT {SAMPLE_LIMIT}
            """,
        )
    ]
    only_acc_samples = [
        {"ts_code": r[0], "trade_date": str(r[1]), "code_prefix": r[2]}
        for r in _fetchall(
            con,
            f"""
            SELECT a.ts_code, a.trade_date, a.code_prefix
            FROM _recon_acc a
            WHERE NOT EXISTS (
              SELECT 1 FROM _recon_fy f
              WHERE f.ts_code = a.ts_code AND f.trade_date = a.trade_date
            )
            ORDER BY a.code_prefix, a.ts_code, a.trade_date
            LIMIT {SAMPLE_LIMIT}
            """,
        )
    ]
    return {
        "window": {
            "min": str(window.get("min_d") or ""),
            "max": str(window.get("max_d") or ""),
        },
        "fuyao_rows": int(grain.get("fuyao_rows") or 0),
        "accepted_rows_in_window": int(grain.get("accepted_rows_in_window") or 0),
        "intersection": int(grain.get("intersection") or 0),
        "only_fuyao": int(grain.get("only_fuyao") or 0),
        "only_accepted": int(grain.get("only_accepted") or 0),
        "by_date": by_date,
        "by_prefix": by_prefix,
        "ohlc_match": int(ohlc.get("ohlc_match") or 0),
        "ohlc_mismatch": int(ohlc.get("ohlc_mismatch") or 0),
        "price_abs_tol": PRICE_ABS_TOL,
        "vol_ratio_median": vol_median,
        "amount_ratio_median": amt_median,
        "vol_ratio_n": int(ratios.get("vol_ratio_n") or 0),
        "amount_ratio_n": int(ratios.get("amount_ratio_n") or 0),
        "documented_vol_scale": documented_vol_scale,
        "documented_amount_scale": documented_amount_scale,
        "vol_scale_hypothesis": "confirmed" if vol_ok else "unconfirmed",
        "amount_scale_hypothesis": "confirmed" if amt_ok else "unconfirmed",
        "vol_mismatch_after_scale": int(scaled.get("vol_mismatch_after_scale") or 0),
        "amount_mismatch_after_scale": int(
            scaled.get("amount_mismatch_after_scale") or 0
        ),
        "ohlc_mismatch_samples": ohlc_samples,
        "only_fuyao_samples": only_fuyao_samples,
        "only_accepted_samples": only_acc_samples,
        "primary_cut": False,
    }


def compare_events(
    con: Any,
    *,
    fuyao_table: str = "fuyao_adj_events",
    dividend_table: str = "raw_tushare_dividend",
    adj_factor_table: str = "raw_tushare_adj_factor",
) -> dict[str, Any]:
    reject_banned_baseline(dividend_table)
    reject_banned_baseline(adj_factor_table)
    div_ex = as_date_sql("ex_date")
    adj_d = as_date_sql("trade_date")
    counts = _row_map(
        con,
        f"""
        WITH div AS (
          SELECT ts_code, {div_ex} AS ex_date, cash_div, cash_div_tax, stk_div
          FROM {dividend_table}
          WHERE trim(coalesce(div_proc, '')) = '实施'
            AND {div_ex} IS NOT NULL
        )
        SELECT
          (SELECT count(*) FROM {fuyao_table}) AS fuyao_events,
          (SELECT count(*) FROM div) AS dividend_implemented,
          (SELECT count(*) FROM {fuyao_table} f
             JOIN div d USING (ts_code, ex_date)) AS matched_ex_date,
          (SELECT count(*) FROM {fuyao_table} f
             WHERE NOT EXISTS (
               SELECT 1 FROM div d
               WHERE d.ts_code = f.ts_code AND d.ex_date = f.ex_date
             )) AS only_fuyao,
          (SELECT count(*) FROM div d
             WHERE NOT EXISTS (
               SELECT 1 FROM {fuyao_table} f
               WHERE f.ts_code = d.ts_code AND f.ex_date = d.ex_date
             )) AS only_dividend,
          (SELECT count(*) FROM {fuyao_table} f
             JOIN div d USING (ts_code, ex_date)
             WHERE abs(coalesce(f.dividend_per_share, 0) - coalesce(d.cash_div, 0))
                   > 0.0015
          ) AS cash_div_mismatch,
          (SELECT count(*) FROM {fuyao_table} f
             JOIN div d USING (ts_code, ex_date)
             WHERE abs(coalesce(f.dividend_per_share, 0) - coalesce(d.cash_div_tax, 0))
                   > 0.0015
          ) AS cash_div_tax_mismatch
        """
    )
    jumps = _row_map(
        con,
        f"""
        WITH ordered AS (
          SELECT ts_code, {adj_d} AS trade_date, adj_factor,
                 lag(adj_factor) OVER (
                   PARTITION BY ts_code ORDER BY {adj_d}
                 ) AS prev_factor
          FROM {adj_factor_table}
        ),
        jumps AS (
          SELECT ts_code, trade_date
          FROM ordered
          WHERE prev_factor IS NOT NULL
            AND abs(adj_factor - prev_factor) > 1e-8
        )
        SELECT
          (SELECT count(*) FROM jumps) AS adj_factor_jumps,
          (SELECT count(*) FROM jumps j
             WHERE EXISTS (
               SELECT 1 FROM {fuyao_table} f
               WHERE f.ts_code = j.ts_code AND f.ex_date = j.trade_date
             )) AS jumps_with_fuyao_event,
          (SELECT count(*) FROM jumps j
             WHERE NOT EXISTS (
               SELECT 1 FROM {fuyao_table} f
               WHERE f.ts_code = j.ts_code AND f.ex_date = j.trade_date
             )) AS jumps_without_fuyao_event,
          (SELECT count(*) FROM {fuyao_table} f
             WHERE NOT EXISTS (
               SELECT 1 FROM jumps j
               WHERE j.ts_code = f.ts_code AND j.trade_date = f.ex_date
             )) AS fuyao_events_without_jump
        """
    )
    return {
        "fuyao_events": int(counts.get("fuyao_events") or 0),
        "dividend_implemented": int(counts.get("dividend_implemented") or 0),
        "matched_ex_date": int(counts.get("matched_ex_date") or 0),
        "only_fuyao": int(counts.get("only_fuyao") or 0),
        "only_dividend": int(counts.get("only_dividend") or 0),
        "cash_div_mismatch": int(counts.get("cash_div_mismatch") or 0),
        "cash_div_tax_mismatch": int(counts.get("cash_div_tax_mismatch") or 0),
        "adj_factor_jumps": int(jumps.get("adj_factor_jumps") or 0),
        "jumps_with_fuyao_event": int(jumps.get("jumps_with_fuyao_event") or 0),
        "jumps_without_fuyao_event": int(jumps.get("jumps_without_fuyao_event") or 0),
        "fuyao_events_without_jump": int(jumps.get("fuyao_events_without_jump") or 0),
        "note": (
            "Fuyao dump is corporate-action events, not TuShare daily adj_factor. "
            "dividend_per_share is pre-tax; compare cash_div_tax before cash_div. "
            "jumps_without_event is expected when cash/bonus/allotment encoding differs."
        ),
    }


def build_report(
    *,
    probes: list[DumpKindProbe],
    kline: dict[str, Any] | None,
    events: dict[str, Any] | None,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "accepted_k_table": ACCEPTED_K_TABLE,
            "banned_baselines": sorted(BANNED_BASELINE_TABLES),
            "kline_daily_primary_untouched": True,
            "dump_kinds_probed": list(DUMP_KIND_VALUES),
        },
        "dump_catalog_status": dump_catalog_status(probes),
        "dump_probe": [asdict(p) for p in probes],
        "kline": kline,
        "events": events,
        "artifacts": dict(artifacts),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(type(value).__name__)


def write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
