from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ANALYSIS_DIR = ROOT / "analysis"
DEFAULT_DB = ANALYSIS_DIR / "research_cache.duckdb"
DEFAULT_ADOPTION = ANALYSIS_DIR / "formula_local_optuna_batch_adoption.csv"
DEFAULT_MERGE_PLAN = ANALYSIS_DIR / "formula_local_optuna_batch_merge_plan.csv"
DEFAULT_PRODUCTION = ANALYSIS_DIR / "stock_formula_best.csv"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _canonical_json(value: str) -> str:
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(*parts: Any) -> str:
    payload = "|".join(_text(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (ROOT / path).resolve()


def _display_path(path: Path) -> str:
    resolved = _resolve_path(path)
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    path = _resolve_path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _latest_data_date() -> str:
    try:
        from compute import get_latest_data_date

        value = _text(get_latest_data_date())
        if value and value != "未知":
            return value
    except Exception:
        pass
    try:
        from settings import MARKET_DB

        with duckdb.connect(str(MARKET_DB), read_only=True) as con:
            row = con.execute(
                """
                WITH daily AS (
                    SELECT date, COUNT(*) AS n
                    FROM v_price_kline_qfq
                    GROUP BY date
                ),
                stats AS (
                    SELECT MAX(n) AS max_n FROM daily
                )
                SELECT d.date
                FROM daily d
                CROSS JOIN stats s
                WHERE d.n::DOUBLE / NULLIF(s.max_n, 0) >= 0.95
                ORDER BY d.date DESC
                LIMIT 1
                """
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    raise RuntimeError("Unable to determine latest market data date for research cache")


def _version_key(row: dict[str, str], source_type: str) -> str:
    execution_model = row.get("execution_model") or ""
    return _hash_text(source_type, execution_model, "formula_local_optuna_v1")


def _cache_record(
    *,
    row: dict[str, str],
    source_type: str,
    source_artifact: Path,
    data_latest_date: str,
    merge_row: dict[str, str] | None = None,
) -> dict[str, Any]:
    params_json = _canonical_json(
        row.get("optuna_params")
        or row.get("params")
        or (merge_row or {}).get("replacement_params")
        or "{}"
    )
    sell_rule = row.get("optuna_sell_rule") or row.get("sell_rule") or (merge_row or {}).get("new_sell_rule") or ""
    holding_days = _to_int(row.get("optuna_holding_days") or row.get("holding_days"))
    params_hash = _hash_text(params_json, sell_rule, holding_days)
    version_key = _version_key(row, source_type)
    stock_code = _text(row.get("stock_code")).zfill(6)
    formula_id = _text(row.get("formula_id"))
    cache_key = _hash_text(stock_code, formula_id, version_key, data_latest_date, params_hash, source_type)
    source_artifact = _resolve_path(source_artifact)
    stat = source_artifact.stat() if source_artifact.exists() else None
    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "cache_key": cache_key,
        "source_type": source_type,
        "stock_code": stock_code,
        "formula_id": formula_id,
        "variant_id": row.get("baseline_variant_id") if source_type == "local_optuna_batch" else row.get("variant_id"),
        "sell_rule": sell_rule,
        "holding_days": holding_days,
        "params_json": params_json,
        "params_hash": params_hash,
        "version_key": version_key,
        "data_latest_date": data_latest_date,
        "execution_model": row.get("execution_model") or "",
        "trials": _to_int(row.get("trials")),
        "validation_ratio": _to_float(row.get("validation_ratio")),
        "baseline_status": row.get("baseline_status"),
        "optuna_status": row.get("optuna_status") if source_type == "local_optuna_batch" else "production_baseline",
        "adoption_decision": row.get("adoption_decision") if source_type == "local_optuna_batch" else "production_baseline",
        "adoption_reason": row.get("adoption_reason") if source_type == "local_optuna_batch" else "",
        "merge_decision": (merge_row or {}).get("merge_decision", ""),
        "merge_reason": (merge_row or {}).get("merge_reason", ""),
        "signal_count": _to_int(row.get("optuna_signal_count") or row.get("signal_count")),
        "win_rate": _to_float(row.get("optuna_win_rate") or row.get("win_rate")),
        "avg_ret": _to_float(row.get("optuna_avg_ret") or row.get("avg_ret")),
        "avg_dd": _to_float(row.get("optuna_avg_dd") or row.get("avg_dd")),
        "calmar": _to_float(row.get("optuna_calmar") or row.get("calmar")),
        "score": _to_float(row.get("optuna_score") or row.get("score")),
        "validation_signal_count": _to_int(row.get("optuna_validation_signal_count")),
        "validation_win_rate": _to_float(row.get("optuna_validation_win_rate")),
        "validation_avg_ret": _to_float(row.get("optuna_validation_avg_ret")),
        "validation_score": _to_float(row.get("optuna_validation_score")),
        "baseline_score": _to_float(row.get("baseline_score")),
        "baseline_validation_score": _to_float(row.get("baseline_validation_score")),
        "score_delta": _to_float(row.get("score_delta")),
        "validation_score_delta": _to_float(row.get("validation_score_delta")),
        "baseline_investigation": row.get("baseline_investigation", ""),
        "optuna_investigation": row.get("optuna_investigation", ""),
        "source_artifact": _display_path(source_artifact),
        "source_mtime_ns": stat.st_mtime_ns if stat else None,
        "created_at": created_at,
    }


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS research_cache (
            cache_key VARCHAR PRIMARY KEY,
            source_type VARCHAR,
            stock_code VARCHAR,
            formula_id VARCHAR,
            variant_id VARCHAR,
            sell_rule VARCHAR,
            holding_days INTEGER,
            params_json TEXT,
            params_hash VARCHAR,
            version_key VARCHAR,
            data_latest_date VARCHAR,
            execution_model VARCHAR,
            trials INTEGER,
            validation_ratio DOUBLE,
            baseline_status VARCHAR,
            optuna_status VARCHAR,
            adoption_decision VARCHAR,
            adoption_reason TEXT,
            merge_decision VARCHAR,
            merge_reason TEXT,
            signal_count INTEGER,
            win_rate DOUBLE,
            avg_ret DOUBLE,
            avg_dd DOUBLE,
            calmar DOUBLE,
            score DOUBLE,
            validation_signal_count INTEGER,
            validation_win_rate DOUBLE,
            validation_avg_ret DOUBLE,
            validation_score DOUBLE,
            baseline_score DOUBLE,
            baseline_validation_score DOUBLE,
            score_delta DOUBLE,
            validation_score_delta DOUBLE,
            baseline_investigation TEXT,
            optuna_investigation TEXT,
            source_artifact VARCHAR,
            source_mtime_ns BIGINT,
            created_at VARCHAR
        )
        """
    )
    con.execute("CREATE TABLE IF NOT EXISTS cache_manifest (key VARCHAR PRIMARY KEY, value TEXT)")


def _insert_records(con: duckdb.DuckDBPyConnection, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    fields = list(records[0])
    placeholders = ", ".join(["?"] * len(fields))
    con.executemany(
        f"INSERT OR REPLACE INTO research_cache ({', '.join(fields)}) VALUES ({placeholders})",
        [[r.get(f) for f in fields] for r in records],
    )


def build_cache(
    *,
    db_path: Path,
    adoption_path: Path,
    merge_plan_path: Path,
    production_path: Path,
    replace: bool = True,
) -> dict[str, Any]:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _resolve_path(db_path)
    adoption_path = _resolve_path(adoption_path)
    merge_plan_path = _resolve_path(merge_plan_path)
    production_path = _resolve_path(production_path)
    data_latest_date = _latest_data_date()
    adoption_rows = _csv_rows(adoption_path)
    merge_rows = _csv_rows(merge_plan_path)
    production_rows = _csv_rows(production_path)
    merge_by_key = {(r.get("stock_code"), r.get("formula_id")): r for r in merge_rows}
    records = [
        _cache_record(
            row=r,
            source_type="local_optuna_batch",
            source_artifact=adoption_path,
            data_latest_date=data_latest_date,
            merge_row=merge_by_key.get((r.get("stock_code"), r.get("formula_id"))),
        )
        for r in adoption_rows
        if r.get("stock_code") and r.get("formula_id") and r.get("optuna_status") == "ok"
    ]
    records.extend(
        _cache_record(
            row=r,
            source_type="production_baseline",
            source_artifact=production_path,
            data_latest_date=data_latest_date,
        )
        for r in production_rows
        if r.get("stock_code") and r.get("formula_id")
    )
    with duckdb.connect(str(db_path)) as con:
        _ensure_schema(con)
        if replace:
            con.execute("DELETE FROM research_cache")
            con.execute("DELETE FROM cache_manifest")
        _insert_records(con, records)
        con.execute(
            "INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)",
            ("data_latest_date", data_latest_date),
        )
        con.execute(
            "INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)",
            ("generated_at", datetime.now(timezone.utc).isoformat()),
        )
        con.execute(
            "INSERT OR REPLACE INTO cache_manifest VALUES (?, ?)",
            ("source_rows", json.dumps({
                "adoption": len(adoption_rows),
                "merge_plan": len(merge_rows),
                "production": len(production_rows),
            }, sort_keys=True)),
        )
        summary = con.execute(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT stock_code) AS stock_count,
                SUM(CASE WHEN source_type = 'local_optuna_batch' THEN 1 ELSE 0 END) AS local_optuna_rows,
                SUM(CASE WHEN source_type = 'production_baseline' THEN 1 ELSE 0 END) AS production_rows,
                SUM(CASE WHEN adoption_decision = 'candidate' THEN 1 ELSE 0 END) AS candidate_rows
            FROM research_cache
            """
        ).fetchone()
    return {
        "db_path": str(db_path),
        "row_count": int(summary[0] or 0),
        "stock_count": int(summary[1] or 0),
        "local_optuna_rows": int(summary[2] or 0),
        "production_rows": int(summary[3] or 0),
        "candidate_rows": int(summary[4] or 0),
        "data_latest_date": data_latest_date,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build versioned Research Cache from Optuna batch artifacts.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--adoption", type=Path, default=DEFAULT_ADOPTION)
    parser.add_argument("--merge-plan", type=Path, default=DEFAULT_MERGE_PLAN)
    parser.add_argument("--production", type=Path, default=DEFAULT_PRODUCTION)
    parser.add_argument("--append", action="store_true", help="Append/replace by cache_key instead of rebuilding current cache.")
    args = parser.parse_args()
    summary = build_cache(
        db_path=args.db,
        adoption_path=args.adoption,
        merge_plan_path=args.merge_plan,
        production_path=args.production,
        replace=not args.append,
    )
    print(
        "research_cache_build: "
        f"rows={summary['row_count']} stocks={summary['stock_count']} "
        f"local_optuna={summary['local_optuna_rows']} production={summary['production_rows']} "
        f"candidates={summary['candidate_rows']} data_latest_date={summary['data_latest_date']}"
    )


if __name__ == "__main__":
    main()
