"""
main.py — MACD 金叉选股 FastAPI 服务
"""
from __future__ import annotations

import subprocess
import sys
import threading
import csv
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from compute import (
    ComputeEngine,
    SCRIPTS_DIR,
    get_chart_data,
    get_data_freshness,
    get_latest_data_date,
    invalidate_data_freshness,
)

engine = ComputeEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=engine.start, daemon=True).start()
    yield


app = FastAPI(title="MACD 金叉选股", lifespan=lifespan)

HTML_FILE = Path(__file__).parent / "index.html"
ANALYSIS_DIR = Path(__file__).parent / "analysis"
FORMULA_VARIANT_METRICS = ANALYSIS_DIR / "formula_variant_metrics.csv"
FORMULA_PARAMETER_SUMMARY = ANALYSIS_DIR / "formula_parameter_search_summary.csv"
EXECUTION_MODEL_AUDIT = ANALYSIS_DIR / "execution_model_audit.csv"
FORMULA_SELL_RULE_AUDIT = ANALYSIS_DIR / "formula_sell_rule_audit.csv"
FORMULA_LOCAL_OPTUNA_ADOPTION = ANALYSIS_DIR / "formula_local_optuna_adoption_candidates.csv"
FORMULA_LOCAL_OPTUNA_MERGE_PLAN = ANALYSIS_DIR / "formula_local_optuna_merge_plan.csv"
FORMULA_LOCAL_OPTUNA_REPLACEMENTS = ANALYSIS_DIR / "formula_local_optuna_stock_best_replacements.csv"
FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION = ANALYSIS_DIR / "formula_local_optuna_batch_adoption.csv"
FORMULA_LOCAL_OPTUNA_BATCH_MERGE_PLAN = ANALYSIS_DIR / "formula_local_optuna_batch_merge_plan.csv"
FORMULA_LOCAL_OPTUNA_BATCH_REPLACEMENTS = ANALYSIS_DIR / "formula_local_optuna_batch_stock_best_replacements.csv"
RESEARCH_CACHE_DB = ANALYSIS_DIR / "research_cache.duckdb"
INCREMENTAL_EVAL_DB = ANALYSIS_DIR / "incremental_eval.duckdb"
DRIFT_TRIGGER_DB = ANALYSIS_DIR / "drift_trigger.duckdb"
LOCAL_OPTUNA_REPLACEMENT_FIELDS = [
    "formula_id",
    "variant_id",
    "stock_code",
    "sell_rule",
    "holding_days",
    "signal_count",
    "win_rate",
    "avg_ret",
    "avg_dd",
    "calmar",
    "delay_buy_rate",
    "delay_sell_rate",
    "score",
    "params",
]


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def _artifact_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "path": str(path.relative_to(Path(__file__).parent)),
            "size": 0,
            "mtime_ns": None,
            "row_count": 0,
        }
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path.relative_to(Path(__file__).parent)),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "row_count": _csv_row_count(path) if path.suffix == ".csv" else None,
    }


def _read_research_cache_status(path: Path) -> dict[str, Any]:
    status = {
        "ready": False,
        "path": str(path.relative_to(Path(__file__).parent)),
        "row_count": 0,
        "stock_count": 0,
        "local_optuna_rows": 0,
        "production_rows": 0,
        "candidate_rows": 0,
        "data_latest_date": "",
        "generated_at": "",
        "stale_reason": "",
    }
    if not path.exists():
        status["stale_reason"] = "research_cache.duckdb not created yet"
        return status
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            row = con.execute(
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
            manifest_rows = con.execute("SELECT key, value FROM cache_manifest").fetchall()
    except Exception as exc:
        status["stale_reason"] = f"failed to read research_cache.duckdb: {type(exc).__name__}: {exc}"
        return status
    manifest = {str(k): str(v) for k, v in manifest_rows}
    status.update(
        {
            "ready": True,
            "row_count": int(row[0] or 0),
            "stock_count": int(row[1] or 0),
            "local_optuna_rows": int(row[2] or 0),
            "production_rows": int(row[3] or 0),
            "candidate_rows": int(row[4] or 0),
            "data_latest_date": manifest.get("data_latest_date", ""),
            "generated_at": manifest.get("generated_at", ""),
            "stale_reason": "" if manifest.get("data_latest_date") else "research cache has no data_latest_date manifest value",
        }
    )
    return status


def _read_incremental_eval_status(path: Path) -> dict[str, Any]:
    status = {
        "ready": False,
        "path": str(path.relative_to(Path(__file__).parent)),
        "row_count": 0,
        "stock_count": 0,
        "clean_count": 0,
        "dirty_count": 0,
        "pending_count": 0,
        "target_data_date": "",
        "generated_at": "",
        "dirty_reason_counts": {},
        "stale_reason": "",
    }
    if not path.exists():
        status["stale_reason"] = "incremental_eval.duckdb not created yet"
        return status
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            row = con.execute(
                """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT stock_code) AS stock_count,
                    SUM(CASE WHEN status = 'clean' THEN 1 ELSE 0 END) AS clean_count,
                    SUM(CASE WHEN status = 'dirty' THEN 1 ELSE 0 END) AS dirty_count,
                    SUM(CASE WHEN status NOT IN ('clean', 'dirty') OR status IS NULL THEN 1 ELSE 0 END) AS pending_count
                FROM incremental_eval_state
                """
            ).fetchone()
            reason_rows = con.execute(
                """
                SELECT dirty_reason, COUNT(*)
                FROM incremental_eval_state
                WHERE dirty_reason <> ''
                GROUP BY dirty_reason
                ORDER BY COUNT(*) DESC, dirty_reason
                """
            ).fetchall()
            manifest_rows = con.execute("SELECT key, value FROM cache_manifest").fetchall()
    except Exception as exc:
        status["stale_reason"] = f"failed to read incremental_eval.duckdb: {type(exc).__name__}: {exc}"
        return status
    manifest = {str(k): str(v) for k, v in manifest_rows}
    status.update(
        {
            "ready": True,
            "row_count": int(row[0] or 0),
            "stock_count": int(row[1] or 0),
            "clean_count": int(row[2] or 0),
            "dirty_count": int(row[3] or 0),
            "pending_count": int(row[4] or 0),
            "target_data_date": manifest.get("target_data_date", ""),
            "generated_at": manifest.get("generated_at", ""),
            "dirty_reason_counts": {str(k): int(v) for k, v in reason_rows},
            "stale_reason": "" if manifest.get("target_data_date") else "incremental eval has no target_data_date manifest value",
        }
    )
    return status


def _read_drift_status(path: Path) -> dict[str, Any]:
    status = {
        "ready": False,
        "path": str(path.relative_to(Path(__file__).parent)),
        "row_count": 0,
        "stock_count": 0,
        "none_count": 0,
        "watch_count": 0,
        "reevaluate_count": 0,
        "reoptimize_count": 0,
        "disable_candidate_count": 0,
        "action_counts": {},
        "check_date": "",
        "latest_data_date": "",
        "generated_at": "",
        "stale_reason": "",
    }
    if not path.exists():
        status["stale_reason"] = "drift_trigger.duckdb not created yet"
        return status
    try:
        import duckdb

        with duckdb.connect(str(path), read_only=True) as con:
            row = con.execute(
                """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT(DISTINCT stock_code) AS stock_count,
                    SUM(CASE WHEN drift_level = 'none' THEN 1 ELSE 0 END) AS none_count,
                    SUM(CASE WHEN drift_level = 'watch' THEN 1 ELSE 0 END) AS watch_count,
                    SUM(CASE WHEN drift_level = 'reevaluate' THEN 1 ELSE 0 END) AS reevaluate_count,
                    SUM(CASE WHEN drift_level = 'reoptimize' THEN 1 ELSE 0 END) AS reoptimize_count,
                    SUM(CASE WHEN drift_level = 'disable_candidate' THEN 1 ELSE 0 END) AS disable_candidate_count
                FROM drift_trigger
                """
            ).fetchone()
            action_rows = con.execute(
                """
                SELECT trigger_action, COUNT(*)
                FROM drift_trigger
                GROUP BY trigger_action
                ORDER BY COUNT(*) DESC, trigger_action
                """
            ).fetchall()
            manifest_rows = con.execute("SELECT key, value FROM cache_manifest").fetchall()
    except Exception as exc:
        status["stale_reason"] = f"failed to read drift_trigger.duckdb: {type(exc).__name__}: {exc}"
        return status
    manifest = {str(k): str(v) for k, v in manifest_rows}
    status.update(
        {
            "ready": True,
            "row_count": int(row[0] or 0),
            "stock_count": int(row[1] or 0),
            "none_count": int(row[2] or 0),
            "watch_count": int(row[3] or 0),
            "reevaluate_count": int(row[4] or 0),
            "reoptimize_count": int(row[5] or 0),
            "disable_candidate_count": int(row[6] or 0),
            "action_counts": {str(k): int(v) for k, v in action_rows},
            "check_date": manifest.get("check_date", ""),
            "latest_data_date": manifest.get("latest_data_date", ""),
            "generated_at": manifest.get("generated_at", ""),
            "stale_reason": "" if manifest.get("latest_data_date") else "drift trigger has no latest_data_date manifest value",
        }
    )
    return status


def _optional_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _read_local_optuna_merge_summary(plan_path: Path, replacement_path: Path) -> dict:
    merge_rows = []
    if plan_path.exists():
        with plan_path.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                merge_rows.append(
                    {
                        "stock_code": r.get("stock_code"),
                        "formula_id": r.get("formula_id"),
                        "merge_decision": r.get("merge_decision"),
                        "merge_reason": r.get("merge_reason"),
                        "old_variant_id": r.get("old_variant_id"),
                        "new_variant_id": r.get("new_variant_id"),
                        "old_sell_rule": r.get("old_sell_rule"),
                        "new_sell_rule": r.get("new_sell_rule"),
                        "old_score": _optional_float(r.get("old_score")),
                        "new_score": _optional_float(r.get("new_score")),
                        "score_delta": _optional_float(r.get("score_delta")),
                        "old_validation_score": _optional_float(r.get("old_validation_score")),
                        "new_validation_score": _optional_float(r.get("new_validation_score")),
                        "validation_score_delta": _optional_float(r.get("validation_score_delta")),
                        "new_signal_count": _optional_int(r.get("new_signal_count")),
                        "new_validation_signal_count": _optional_int(r.get("new_validation_signal_count")),
                        "new_win_rate": _optional_float(r.get("new_win_rate")),
                        "new_validation_win_rate": _optional_float(r.get("new_validation_win_rate")),
                        "new_avg_ret": _optional_float(r.get("new_avg_ret")),
                        "new_validation_avg_ret": _optional_float(r.get("new_validation_avg_ret")),
                        "trials": _optional_int(r.get("trials")),
                        "validation_ratio": _optional_float(r.get("validation_ratio")),
                    }
                )
    replacement_count = 0
    replacement_fields_ok = False
    if replacement_path.exists():
        with replacement_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            replacement_fields_ok = reader.fieldnames == LOCAL_OPTUNA_REPLACEMENT_FIELDS
            replacement_count = sum(1 for _ in reader)
    replace_rows = [r for r in merge_rows if r.get("merge_decision") == "replace"]
    return {
        "source_row_count": len(merge_rows),
        "row_count": len(merge_rows),
        "replacement_count": len(replace_rows),
        "rejected_count": len(merge_rows) - len(replace_rows),
        "replacement_schema_rows": replacement_count,
        "replacement_fields_ok": replacement_fields_ok,
        "dry_run": True,
        "replacements": sorted(
            replace_rows,
            key=lambda x: x["score_delta"] if x["score_delta"] is not None else float("-inf"),
            reverse=True,
        )[:8],
    }


def _count_local_optuna_status(rows: list[dict]) -> dict:
    counts = {}
    for row in rows:
        for key in ("baseline_status", "optuna_status"):
            status = row.get(key)
            if status and status != "ok":
                counts[status] = counts.get(status, 0) + 1
    return counts


def _count_local_optuna_rejections(rows: list[dict]) -> dict:
    counts = {}
    for row in rows:
        if row.get("adoption_decision") == "candidate":
            continue
        for reason in str(row.get("adoption_reason") or "").split("; "):
            if reason:
                counts[reason] = counts.get(reason, 0) + 1
    return counts


def _count_local_optuna_missing_investigations(rows: list[dict]) -> dict:
    counts = {}
    for row in rows:
        for status_key, investigation_key in (
            ("baseline_status", "baseline_investigation"),
            ("optuna_status", "optuna_investigation"),
        ):
            status = row.get(status_key)
            if not status or status == "ok":
                continue
            investigation = row.get(investigation_key) or '{"reason": "missing investigation detail"}'
            key = f"{status}: {investigation}"
            counts[key] = counts.get(key, 0) + 1
    return counts


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_FILE.read_text(encoding="utf-8")


@app.get("/api/status")
async def api_status(strategy: Optional[str] = None):
    try:
        s = engine.status(strategy)
    except KeyError:
        raise HTTPException(404, f"未找到策略: {strategy}")
    freshness = get_data_freshness()
    s.update(freshness)
    cached = engine.data_for_profile(s.get("profile_id"))
    cached_latest = cached.get("latest_data_date") if cached else None
    cached_global_latest = cached.get("global_latest_data_date") if cached else None
    stale_reasons = []
    if cached and cached_latest != freshness.get("latest_data_date"):
        stale_reasons.append("主覆盖日期已变化")
    if cached and cached_global_latest != freshness.get("global_latest_data_date"):
        stale_reasons.append("全局最新日期已变化")
    s.update(
        {
            "computed_latest_data_date": cached_latest,
            "computed_global_latest_data_date": cached_global_latest,
            "data_stale": bool(cached and stale_reasons),
            "stale_reasons": stale_reasons,
        }
    )
    return s


@app.get("/api/strategies")
async def api_strategies():
    return {
        "profiles": engine.profiles(),
        "active_profile_id": engine.active_profile_id(),
        "default_profile_id": engine.default_profile_id(),
    }


@app.get("/api/data")
async def api_data(strategy: Optional[str] = None):
    if strategy:
        try:
            engine.ensure_profile(strategy)
        except KeyError:
            raise HTTPException(404, f"未找到策略: {strategy}")

    d = engine.data_for_profile(strategy)
    if d is None:
        raise HTTPException(503, "数据尚未就绪，请稍候")
    return JSONResponse(d)


@app.get("/api/unified")
async def api_unified():
    d = engine.unified_data()
    if d is None:
        raise HTTPException(503, "统一股票池尚未就绪，请稍候")
    if not d.get("ready"):
        return JSONResponse(d, status_code=202)
    return JSONResponse(d)


@app.get("/api/ready/{strategy}")
async def api_ready(strategy: str):
    """Check if a specific strategy's data is ready (cached in memory)."""
    profiles = engine.profiles()
    if strategy not in profiles:
        raise HTTPException(404, f"未找到策略: {strategy}")
    d = engine.data_for_profile(strategy)
    return {"ready": d is not None, "strategy": strategy}


@app.get("/api/chart/{code}")
async def api_chart(code: str, strategy: Optional[str] = None):
    try:
        profile = engine.active_profile() if strategy is None else engine.profiles()[strategy]
    except KeyError:
        raise HTTPException(404, f"未找到策略: {strategy}")

    return JSONResponse(get_chart_data(code, profile))


@app.get("/api/parameter-search")
async def api_parameter_search():
    if not FORMULA_VARIANT_METRICS.exists():
        return {"ready": False, "formulas": [], "message": "参数搜索结果不存在"}

    summary_by_formula = {}
    if FORMULA_PARAMETER_SUMMARY.exists():
        with FORMULA_PARAMETER_SUMMARY.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                fid = r.get("formula_id")
                if not fid:
                    continue
                summary_by_formula[fid] = {
                    "strategy_id": r.get("strategy_id"),
                    "display_name": r.get("display_name"),
                    "cache_ready": str(r.get("cache_ready") or "").lower() == "true",
                    "stock_count": int(float(r.get("stock_count") or 0)),
                    "stocks_with_signal": int(float(r.get("stocks_with_signal") or 0)),
                    "avg_signal_count": float(r.get("avg_signal_count") or 0),
                    "avg_win_rate": float(r.get("avg_win_rate") or 0),
                    "avg_ret": float(r.get("avg_ret") or 0),
                    "avg_dd": float(r.get("avg_dd") or 0),
                    "avg_calmar": float(r.get("avg_calmar") or 0),
                    "avg_untradable_rate": float(r.get("avg_untradable_rate") or 0),
                    "execution_model": r.get("execution_model"),
                }

    execution_by_strategy: dict[str, dict[str, int]] = {}
    if EXECUTION_MODEL_AUDIT.exists():
        with EXECUTION_MODEL_AUDIT.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                sid = r.get("strategy_id")
                metric = r.get("metric")
                if not sid or not metric:
                    continue
                execution_by_strategy.setdefault(sid, {})[metric] = int(float(r.get("count") or 0))

    sell_rules_by_formula: dict[str, list[dict[str, Any]]] = {}
    if FORMULA_SELL_RULE_AUDIT.exists():
        with FORMULA_SELL_RULE_AUDIT.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                fid = r.get("formula_id")
                if not fid:
                    continue
                sell_rules_by_formula.setdefault(fid, []).append(
                    {
                        "variant_id": r.get("variant_id"),
                        "sell_rule": r.get("sell_rule"),
                        "holding_days": int(float(r.get("holding_days") or 0)),
                        "trade_count": int(float(r.get("trade_count") or 0)),
                        "win_rate": float(r.get("win_rate") or 0),
                        "avg_ret": float(r.get("avg_ret") or 0),
                        "avg_dd": float(r.get("avg_dd") or 0),
                        "calmar": float(r.get("calmar") or 0),
                        "delay_buy_rate": float(r.get("delay_buy_rate") or 0),
                        "delay_sell_rate": float(r.get("delay_sell_rate") or 0),
                        "score": float(r.get("score") or 0),
                    }
                )

    local_optuna_rows = []
    if FORMULA_LOCAL_OPTUNA_ADOPTION.exists():
        with FORMULA_LOCAL_OPTUNA_ADOPTION.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                local_optuna_rows.append(
                    {
                        "stock_code": r.get("stock_code"),
                        "formula_id": r.get("formula_id"),
                        "baseline_status": r.get("baseline_status"),
                        "baseline_reason": r.get("baseline_reason"),
                        "baseline_investigation": r.get("baseline_investigation"),
                        "baseline_variant_id": r.get("baseline_variant_id"),
                        "baseline_sell_rule": r.get("baseline_sell_rule"),
                        "baseline_source_score": _optional_float(r.get("baseline_source_score")),
                        "baseline_score": _optional_float(r.get("baseline_score")),
                        "baseline_validation_score": _optional_float(r.get("baseline_validation_score")),
                        "baseline_validation_signal_count": _optional_int(r.get("baseline_validation_signal_count")),
                        "baseline_validation_win_rate": _optional_float(r.get("baseline_validation_win_rate")),
                        "baseline_validation_avg_ret": _optional_float(r.get("baseline_validation_avg_ret")),
                        "optuna_status": r.get("optuna_status"),
                        "optuna_reason": r.get("optuna_reason"),
                        "optuna_investigation": r.get("optuna_investigation"),
                        "optuna_sell_rule": r.get("optuna_sell_rule"),
                        "optuna_holding_days": _optional_int(r.get("optuna_holding_days")),
                        "optuna_signal_count": _optional_int(r.get("optuna_signal_count")),
                        "optuna_win_rate": _optional_float(r.get("optuna_win_rate")),
                        "optuna_avg_ret": _optional_float(r.get("optuna_avg_ret")),
                        "optuna_score": _optional_float(r.get("optuna_score")),
                        "optuna_train_score": _optional_float(r.get("optuna_train_score")),
                        "optuna_validation_score": _optional_float(r.get("optuna_validation_score")),
                        "optuna_validation_signal_count": _optional_int(r.get("optuna_validation_signal_count")),
                        "optuna_validation_win_rate": _optional_float(r.get("optuna_validation_win_rate")),
                        "optuna_validation_avg_ret": _optional_float(r.get("optuna_validation_avg_ret")),
                        "score_delta": _optional_float(r.get("score_delta")),
                        "validation_score_delta": _optional_float(r.get("validation_score_delta")),
                        "adoption_decision": r.get("adoption_decision"),
                        "adoption_reason": r.get("adoption_reason"),
                    }
                )

    local_optuna_status_counts = _count_local_optuna_status(local_optuna_rows)
    local_optuna_rejection_counts = _count_local_optuna_rejections(local_optuna_rows)
    local_optuna_missing_investigation_counts = _count_local_optuna_missing_investigations(local_optuna_rows)

    local_optuna_merge_plan = _read_local_optuna_merge_summary(
        FORMULA_LOCAL_OPTUNA_MERGE_PLAN,
        FORMULA_LOCAL_OPTUNA_REPLACEMENTS,
    )
    local_optuna_batch_rows = []
    if FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION.exists():
        with FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION.open("r", encoding="utf-8", newline="") as f:
            local_optuna_batch_rows = list(csv.DictReader(f))
    local_optuna_batch_candidates = [
        r for r in local_optuna_batch_rows if r.get("adoption_decision") == "candidate"
    ]
    local_optuna_batch_status_counts = _count_local_optuna_status(local_optuna_batch_rows)
    local_optuna_batch_rejection_counts = _count_local_optuna_rejections(local_optuna_batch_rows)
    local_optuna_batch_missing_investigation_counts = _count_local_optuna_missing_investigations(
        local_optuna_batch_rows
    )
    local_optuna_batch_merge_plan = _read_local_optuna_merge_summary(
        FORMULA_LOCAL_OPTUNA_BATCH_MERGE_PLAN,
        FORMULA_LOCAL_OPTUNA_BATCH_REPLACEMENTS,
    )

    rows = []
    with FORMULA_VARIANT_METRICS.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                params = json.loads(r.get("params") or "{}")
            except Exception:
                params = {}
            rows.append(
                {
                    "formula_id": r.get("formula_id"),
                    "variant_id": r.get("variant_id"),
                    "sell_rule": r.get("sell_rule") or f"fixed_{int(float(r.get('holding_days') or 0))}",
                    "holding_days": int(float(r.get("holding_days") or 0)),
                    "trade_count": int(float(r.get("trade_count") or 0)),
                    "win_rate": float(r.get("win_rate") or 0),
                    "avg_ret": float(r.get("avg_ret") or 0),
                    "avg_dd": float(r.get("avg_dd") or 0),
                    "calmar": float(r.get("calmar") or 0),
                    "delay_buy_rate": float(r.get("delay_buy_rate") or 0),
                    "delay_sell_rate": float(r.get("delay_sell_rate") or 0),
                    "score": float(r.get("score") or 0),
                    "params": params,
                    "param_count": len(params),
                }
            )

    grouped = {}
    for r in rows:
        grouped.setdefault(r["formula_id"], []).append(r)
    formulas = []
    for fid, items in sorted(grouped.items()):
        top = sorted(items, key=lambda x: x["score"], reverse=True)[:5]
        summary = summary_by_formula.get(fid, {})
        execution = execution_by_strategy.get(str(summary.get("strategy_id") or ""), {})
        sell_rules = sorted(sell_rules_by_formula.get(fid, []), key=lambda x: x["score"], reverse=True)
        formulas.append(
            {
                "formula_id": fid,
                "variant_count": len({x["variant_id"] for x in items}),
                "metric_count": len(items),
                "summary": summary,
                "execution": execution,
                "sell_rules": sell_rules,
                "best_sell_rule": sell_rules[0] if sell_rules else None,
                "variants": sorted(items, key=lambda x: x["score"], reverse=True),
                "top": top,
            }
        )
    local_optuna_candidates = [
        r for r in local_optuna_rows if r.get("adoption_decision") == "candidate"
    ]
    local_optuna_batch_candidates_sorted = sorted(
        local_optuna_batch_candidates,
        key=lambda x: _optional_float(x.get("score_delta")) if _optional_float(x.get("score_delta")) is not None else float("-inf"),
        reverse=True,
    )
    source_files = {
        "local_adoption": str(FORMULA_LOCAL_OPTUNA_ADOPTION.relative_to(Path(__file__).parent)),
        "local_merge_plan": str(FORMULA_LOCAL_OPTUNA_MERGE_PLAN.relative_to(Path(__file__).parent)),
        "local_replacements": str(FORMULA_LOCAL_OPTUNA_REPLACEMENTS.relative_to(Path(__file__).parent)),
        "batch_adoption": str(FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION.relative_to(Path(__file__).parent)),
        "batch_merge_plan": str(FORMULA_LOCAL_OPTUNA_BATCH_MERGE_PLAN.relative_to(Path(__file__).parent)),
        "batch_replacements": str(FORMULA_LOCAL_OPTUNA_BATCH_REPLACEMENTS.relative_to(Path(__file__).parent)),
    }
    artifact_fingerprints = {
        "local_adoption": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_ADOPTION),
        "local_merge_plan": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_MERGE_PLAN),
        "local_replacements": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_REPLACEMENTS),
        "batch_adoption": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_BATCH_ADOPTION),
        "batch_merge_plan": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_BATCH_MERGE_PLAN),
        "batch_replacements": _artifact_fingerprint(FORMULA_LOCAL_OPTUNA_BATCH_REPLACEMENTS),
    }
    total_stock_count = max(
        [int((f.get("summary") or {}).get("stock_count") or 0) for f in formulas] or [0]
    )
    covered_stock_count = (
        len({str(r.get("stock_code") or "") for r in local_optuna_batch_rows if r.get("stock_code")})
        if local_optuna_batch_rows
        else 0
    )
    management = {
        "full_initialization": {
            "status": "in_progress" if total_stock_count and covered_stock_count < total_stock_count else "complete",
            "covered_stock_count": covered_stock_count,
            "total_stock_count": total_stock_count,
            "progress": (covered_stock_count / total_stock_count) if total_stock_count else None,
            "completed_batches": covered_stock_count // 20,
            "total_batches": ((total_stock_count + 19) // 20) if total_stock_count else 0,
            "next_offset": covered_stock_count,
            "dry_run": True,
        },
        "research_cache_status": _read_research_cache_status(RESEARCH_CACHE_DB),
        "incremental_eval_status": _read_incremental_eval_status(INCREMENTAL_EVAL_DB),
        "drift_status": _read_drift_status(DRIFT_TRIGGER_DB),
        "production_merge": {
            "status": "blocked",
            "reason": "full-market coverage and aggregate audit are required before writing stock_formula_best.csv",
        },
    }
    return {
        "ready": True,
        "formula_count": len(formulas),
        "metric_count": len(rows),
        "metric_count_source": "formula_variant_metrics.csv",
        "formulas": formulas,
        "local_optuna": {
            "row_count": len(local_optuna_rows),
            "candidate_count": len(local_optuna_candidates),
            "rejected_count": len(local_optuna_rows) - len(local_optuna_candidates),
            "status_counts": local_optuna_status_counts,
            "rejection_reason_counts": local_optuna_rejection_counts,
            "missing_investigation_counts": local_optuna_missing_investigation_counts,
            "candidates": sorted(
                local_optuna_candidates,
                key=lambda x: x["score_delta"] if x["score_delta"] is not None else float("-inf"),
                reverse=True,
            ),
            "merge_plan": local_optuna_merge_plan,
            "batch": {
                "row_count": len(local_optuna_batch_rows),
                "candidate_count": len(local_optuna_batch_candidates),
                "rejected_count": len(local_optuna_batch_rows) - len(local_optuna_batch_candidates),
                "status_counts": local_optuna_batch_status_counts,
                "rejection_reason_counts": local_optuna_batch_rejection_counts,
                "missing_investigation_counts": local_optuna_batch_missing_investigation_counts,
                "merge_plan": local_optuna_batch_merge_plan,
                "candidates": local_optuna_batch_candidates_sorted[:20],
            },
            "source_files": source_files,
            "artifact_fingerprints": artifact_fingerprints,
            "management": management,
        },
    }


@app.post("/api/refresh")
async def api_refresh(strategy: Optional[str] = None):
    try:
        invalidate_data_freshness()
        engine.restart(strategy, clear_cache=True, activate=(strategy is None))
    except KeyError:
        raise HTTPException(404, f"未找到策略: {strategy}")
    return {"ok": True, "message": "已触发重新计算"}


_optimize_lock = threading.Lock()
_optimize_running = False


@app.post("/api/optimize")
async def api_optimize(job: str = "optuna"):
    """
    后台重新运行优化脚本，完成后自动更新 optuna_best 策略。
    job=optuna  → 重跑 Optuna 参数搜索（~15 min）
    job=gcross  → 重跑 MACD 金叉持股期回测（生成持股期汇总 CSV）
    """
    global _optimize_running
    scripts = {
        "optuna": SCRIPTS_DIR / "macd_optuna_backtest.py",
        "gcross": SCRIPTS_DIR / "macd_golden_cross_backtest.py",
    }
    script = scripts.get(job)
    if not script:
        raise HTTPException(400, f"未知任务: {job}，可选: optuna / gcross")
    if not script.exists():
        raise HTTPException(404, f"脚本不存在: {script}")

    with _optimize_lock:
        if _optimize_running:
            raise HTTPException(409, "已有优化任务在运行，请稍候")
        _optimize_running = True

    def _run():
        global _optimize_running
        try:
            subprocess.run([sys.executable, str(script)], check=False,
                           cwd=str(SCRIPTS_DIR))
            # 优化完成后自动刷新 optuna_best 策略
            if job == "optuna":
                engine.restart("optuna_best", clear_cache=True)
        finally:
            _optimize_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": f"已启动 {job} 优化任务（后台运行），完成后自动更新数据"}
