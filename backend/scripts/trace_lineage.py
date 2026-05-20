#!/usr/bin/env python3
"""Trace paper_sim KPI, model, panel, or asset lineage as Markdown."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.db import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402
from services.pipeline_manifest import git_commit_sha  # noqa: E402

PRED_TABLES = ["mart_p0b_lambdamart_v6_predictions", "mart_p0b_oos_predictions"]
ALIASES = {
    "fact_alpha158_panel": "fact_feature_panel",
    "fact_industry_pit": "mart_stock_industry_pit",
    "fact_capital_flow_pit": "fact_capital_flow_pit_daily",
    "raw_kline": "v_price_kline_qfq",
}
PARENTS = {
    "mart_paper_sim_kpi": ["fact_paper_sim_nav", "fact_paper_sim_position", "fact_paper_sim_trade"],
    "mart_p0b_lambdamart_v6_predictions": ["mart_p0a_feature_label_panel_v4"],
    "mart_p0b_oos_predictions": ["mart_p0a_feature_label_panel_v3"],
    "mart_p0a_feature_label_panel_v4": [
        "fact_feature_panel", "mart_p0a_label_panel",
        "mart_stock_industry_pit", "fact_capital_flow_pit_daily",
    ],
    "mart_p0a_feature_label_panel_v3": ["fact_feature_panel", "mart_p0a_label_panel"],
    "fact_feature_panel": [
        "v_price_kline_qfq", "fact_financial_pit_daily", "fact_top10_holder_period",
        "fact_lhb_event", "raw_institution_surveys", "raw_aif10_forecast_consensus",
        "raw_aif10_peer_valuation",
    ],
    "mart_p0a_label_panel": ["v_price_kline_qfq", "dim_trading_calendar"],
    "mart_stock_industry_pit": [
        "dim_stock_tdx_industry_history", "dim_stock_tdx_industry",
        "raw_tdx_industry_file_snapshot",
    ],
    "fact_capital_flow_pit_daily": [
        "raw_lhb_daily", "raw_executive_trade", "fact_top10_holder_period",
        "raw_capital_allotment_detail", "raw_capital_dividend_detail",
        "raw_capital_repurchase", "raw_capital_unlock",
    ],
    "fact_lhb_event": ["raw_lhb_daily"],
    "fact_top10_holder_period": ["raw_tdx_f10_holder_research"],
    "fact_financial_pit_daily": ["raw_gpcw_detail", "raw_aif10_financial_history"],
}
COMMANDS = {
    "mart_paper_sim_kpi": "PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py",
    "mart_p0b_lambdamart_v6_predictions": "PYTHONPATH=backend python backend/scripts/retrain_lambdamart_v6.py",
    "mart_p0b_oos_predictions": "PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v4.py",
    "mart_p0a_feature_label_panel_v4": "PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v4.py",
    "mart_p0a_feature_label_panel_v3": "PYTHONPATH=backend python backend/scripts/build_p0a_feature_panel_v3.py",
    "mart_p0a_label_panel": "PYTHONPATH=backend python backend/scripts/rebuild_p0a_label_panel.py",
    "fact_feature_panel": "PYTHONPATH=backend python backend/scripts/build_feature_panel_duck.py",
    "mart_stock_industry_pit": "PYTHONPATH=backend python backend/scripts/build_industry_pit.py",
    "fact_capital_flow_pit_daily": "PYTHONPATH=backend python backend/scripts/backfill_capital_flow_pit.py",
    "fact_lhb_event": "PYTHONPATH=backend python backend/scripts/build_lhb_events.py",
    "backend/config/feature_registry.yaml": "config file, versioned in git",
    "mart_p1_optuna_trials": "PYTHONPATH=backend python backend/scripts/run_p0b_lightgbm_optuna_v4.py",
}
WM = {
    "v_price_kline_qfq": ["kline_daily"],
    "fact_feature_panel": ["kline_daily", "financial_gpcw_8q", "holders_top10_float", "lhb_daily"],
    "fact_financial_pit_daily": ["financial_gpcw_8q"],
    "raw_gpcw_detail": ["financial_gpcw_8q"],
    "fact_top10_holder_period": ["holders_top10_float"],
    "raw_tdx_f10_holder_research": ["holders_top10_float"],
    "fact_lhb_event": ["lhb_daily"],
    "raw_lhb_daily": ["lhb_daily"],
    "fact_capital_flow_pit_daily": ["lhb_daily", "holders_top10_float"],
    "mart_stock_industry_pit": ["industry_sw", "stock_blocks"],
    "raw_tdx_industry_file_snapshot": ["industry_sw", "stock_blocks"],
}


def rowdict(row: Any) -> dict[str, Any]:
    return {} if row is None else {k: row[k] for k in row.keys()}


def valid_table(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def exists(conn: Any, table: str) -> bool:
    if not valid_table(table):
        return False
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1", [table]
    ).fetchone() is not None


def columns(conn: Any, table: str) -> set[str]:
    if not exists(conn, table):
        return set()
    return {r[0] for r in conn.execute(f"DESCRIBE {table}").fetchall()}


def count_rows(conn: Any, table: str) -> int | None:
    if not exists(conn, table):
        return None
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return None


def panel_table(version: str | None) -> str | None:
    if not version:
        return None
    if version.startswith("mart_"):
        return version
    m = re.fullmatch(r"p0a_v(\d+)", version)
    return f"mart_p0a_feature_label_panel_v{m.group(1)}" if m else version


def table_lineage(conn: Any, asset: str) -> tuple[list[str], dict[str, Any]]:
    if exists(conn, "mart_data_lineage"):
        cols = columns(conn, "mart_data_lineage")
        name_col = "asset_name" if "asset_name" in cols else "asset_id"
        wanted = [
            c for c in ("parent_asset_id", "build_command", "git_commit_hash",
                        "built_at", "pit_cutoff", "source_records_count", "notes")
            if c in cols
        ]
        row = conn.execute(
            f"SELECT {', '.join(wanted)} FROM mart_data_lineage "
            f"WHERE {name_col}=? ORDER BY built_at DESC NULLS LAST LIMIT 1",
            [asset],
        ).fetchone() if wanted else None
        data = rowdict(row)
        if data:
            raw = data.get("parent_asset_id")
            if isinstance(raw, list):
                return [str(x) for x in raw], data
            if isinstance(raw, str):
                try:
                    return [str(x) for x in json.loads(raw)], data
                except Exception:
                    return [p.strip() for p in raw.split(",") if p.strip()], data
            return [], data
    if exists(conn, "mart_lineage"):
        row = conn.execute(
            """
            SELECT input_tables, owner, sql_hash, last_row_count, last_status
              FROM mart_lineage
             WHERE output_table=?
             ORDER BY updated_at DESC NULLS LAST
             LIMIT 1
            """,
            [asset],
        ).fetchone()
        data = rowdict(row)
        try:
            return [str(x) for x in json.loads(data.get("input_tables") or "[]")], data
        except (json.JSONDecodeError, TypeError, ValueError):
            # explicit narrow exceptions: input_tables 字段可能 malformed JSON / NULL / 类型异常
            # fall back to static PARENTS map; don't lose silent on unexpected exceptions
            pass
    return list(PARENTS.get(asset, [])), {}


def asset_meta(conn: Any, asset: str) -> dict[str, Any]:
    if not exists(conn, "dim_data_asset"):
        return {}
    row = conn.execute(
        """
        SELECT writer_module, pit_policy, quality_gate_level
          FROM dim_data_asset
         WHERE table_name=?
         LIMIT 1
        """,
        [asset],
    ).fetchone()
    return rowdict(row)


def kpi_row(conn: Any, sim_run_id: str) -> dict[str, Any]:
    if not exists(conn, "mart_paper_sim_kpi"):
        raise ValueError("mart_paper_sim_kpi table is missing")
    cols = columns(conn, "mart_paper_sim_kpi")
    wanted = [
        c for c in (
            "sim_run_id", "variant", "period_start", "period_end", "n_days",
            "annual_return", "max_dd", "sharpe", "monthly_win_rate",
            "config_snapshot", "lineage_url", "built_at",
        ) if c in cols
    ]
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM mart_paper_sim_kpi WHERE sim_run_id=?",
        [sim_run_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown sim_run_id: {sim_run_id}")
    return rowdict(row)


def model_from_sim(conn: Any, sim_run_id: str, kpi: dict[str, Any]) -> tuple[str | None, str | None]:
    if exists(conn, "mart_paper_sim_lambdamart_v6_kpi_compare"):
        row = conn.execute(
            "SELECT model_id, prediction_table FROM mart_paper_sim_lambdamart_v6_kpi_compare "
            "WHERE sim_run_id=? LIMIT 1",
            [sim_run_id],
        ).fetchone()
        if row:
            return row["model_id"], row["prediction_table"]
    try:
        selection = (json.loads(kpi.get("config_snapshot") or "{}") or {}).get("selection") or {}
    except Exception:
        selection = {}
    return selection.get("ml_score_model_id"), selection.get("ml_score_prediction_table")


def prediction_row(conn: Any, model_id: str, preferred: str | None) -> dict[str, Any]:
    candidates = ([preferred] if preferred else []) + [t for t in PRED_TABLES if t != preferred]
    for table in candidates:
        if not table or not exists(conn, table) or "model_id" not in columns(conn, table):
            continue
        row = conn.execute(
            f"""
            SELECT COUNT(*) row_count, MIN(signal_date) min_signal_date,
                   MAX(signal_date) max_signal_date, MIN(train_start) min_train_start,
                   MAX(train_end) max_train_end, ANY_VALUE(model_version) model_version,
                   ANY_VALUE(feature_version) feature_version,
                   ANY_VALUE(label_version) label_version, MAX(built_at) built_at
              FROM {table}
             WHERE model_id=?
            """,
            [model_id],
        ).fetchone()
        data = rowdict(row)
        if int(data.get("row_count") or 0) > 0:
            data.update({"asset": table, "model_id": model_id})
            return data
    raise ValueError(f"unknown model_id: {model_id}")


def panel_summary(conn: Any, table: str, pred: dict[str, Any]) -> dict[str, Any]:
    if not exists(conn, table):
        return {"asset": table, "row_count": None, "missing": True}
    cols = columns(conn, table)
    where, params = [], []
    if {"signal_date"}.issubset(cols) and pred.get("min_signal_date") and pred.get("max_signal_date"):
        where.append("signal_date BETWEEN ? AND ?")
        params += [pred["min_signal_date"], pred["max_signal_date"]]
    if "feature_version" in cols and pred.get("feature_version"):
        where.append("feature_version=?")
        params.append(pred["feature_version"])
    sql_where = "WHERE " + " AND ".join(where) if where else ""
    built = "MAX(built_at)" if "built_at" in cols else "NULL"
    mind = "MIN(signal_date)" if "signal_date" in cols else "NULL"
    maxd = "MAX(signal_date)" if "signal_date" in cols else "NULL"
    out = rowdict(conn.execute(
        f"SELECT COUNT(*) row_count, {mind} min_signal_date, {maxd} max_signal_date, "
        f"{built} built_at FROM {table} {sql_where}",
        params,
    ).fetchone())
    missing = sorted({"source_event_date", "available_at_date", "source_revision_id"} - cols)
    out.update({"asset": table, "note": "missing PIT columns: " + ", ".join(missing) if missing else None})
    return out


def add(graph: dict[str, dict[str, Any]], asset: str, parents: list[str] | None = None, **attrs: Any) -> None:
    asset = ALIASES.get(asset, asset)
    node = graph.setdefault(asset, {"parents": [], "attrs": {}})
    for p in parents or []:
        p = ALIASES.get(p, p)
        if p not in node["parents"]:
            node["parents"].append(p)
    node["attrs"].update({k: v for k, v in attrs.items() if v is not None})


def build_graph(conn: Any, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    root: dict[str, Any] = {}
    graph: dict[str, dict[str, Any]] = {}
    pred: dict[str, Any] = {}
    model_id, preferred = args.model_id, None
    panel_version = args.panel_version
    if args.sim_run_id:
        kpi = kpi_row(conn, args.sim_run_id)
        root["kpi"] = kpi
        linked_model, preferred = model_from_sim(conn, args.sim_run_id, kpi)
        model_id = model_id or linked_model
        add(graph, "mart_paper_sim_kpi", PARENTS["mart_paper_sim_kpi"], row_count=1,
            built_at=kpi.get("built_at"), pit_cutoff=kpi.get("period_end"))
    if model_id:
        pred = prediction_row(conn, model_id, preferred)
        root["prediction"] = pred
        panel_version = panel_version or pred.get("feature_version")
        ptable = panel_table(panel_version)
        add(graph, pred["asset"], [x for x in [ptable, "backend/config/feature_registry.yaml",
                                                f"model_artifact:{model_id}"] if x],
            model_id=model_id, row_count=pred.get("row_count"), built_at=pred.get("built_at"),
            pit_cutoff=pred.get("max_signal_date"))
        if args.sim_run_id:
            add(graph, "mart_paper_sim_kpi", [pred["asset"]])
        add(graph, f"model_artifact:{model_id}", ["mart_p1_optuna_trials"])
        add(graph, "backend/config/feature_registry.yaml")
    ptable = panel_table(panel_version)
    if ptable:
        panel = panel_summary(conn, ptable, pred)
        root["panel"] = panel
        add(graph, ptable, PARENTS.get(ptable, []), panel_version=panel_version,
            row_count=panel.get("row_count"), built_at=panel.get("built_at"),
            pit_cutoff=panel.get("max_signal_date"), note=panel.get("note"))
    if args.asset_name:
        asset = ALIASES.get(args.asset_name, args.asset_name)
        root["asset"] = asset
        add(graph, asset, PARENTS.get(asset, []))
    if not graph:
        raise ValueError("provide --sim-run-id, --model-id, --panel-version, or --asset-name")
    queue = [(a, 0) for a in list(graph)]
    seen = set(queue)
    while queue:
        asset, depth = queue.pop(0)
        if depth >= args.max_depth:
            continue
        parents, _ = table_lineage(conn, asset)
        add(graph, asset, parents)
        for p in parents:
            p = ALIASES.get(p, p)
            if (p, depth + 1) not in seen:
                seen.add((p, depth + 1))
                graph.setdefault(p, {"parents": [], "attrs": {}})
                queue.append((p, depth + 1))
    return root, graph


def describe(conn: Any, asset: str, attrs: dict[str, Any], commit: str | None) -> dict[str, Any]:
    _, lin = table_lineage(conn, asset)
    meta = asset_meta(conn, asset)
    row_count = attrs.get("row_count") or lin.get("source_records_count") or lin.get("last_row_count")
    return {
        "command": lin.get("build_command") or COMMANDS.get(asset) or meta.get("writer_module") or lin.get("owner") or "NA",
        "commit": lin.get("git_commit_hash") or commit or "NA",
        "rows": row_count if row_count is not None else count_rows(conn, asset),
        "pit": attrs.get("pit_cutoff") or lin.get("pit_cutoff") or "NA",
        "built": attrs.get("built_at") or lin.get("built_at") or "NA",
        "note": attrs.get("note") or lin.get("notes") or meta.get("pit_policy"),
    }


def pct(v: Any) -> str:
    return "NA" if v is None else f"{float(v) * 100:+.2f}%"


def render(conn: Any, root: dict[str, Any], graph: dict[str, dict[str, Any]], elapsed: float) -> str:
    commit = git_commit_sha(REPO)
    out = ["# Data Lineage Trace", ""]
    kpi, pred = root.get("kpi") or {}, root.get("prediction") or {}
    if kpi:
        out += ["## Root KPI", "",
                f"- sim_run_id: {kpi.get('sim_run_id')}",
                f"- variant: {kpi.get('variant', 'NA')}",
                f"- ann_ret: {pct(kpi.get('annual_return'))}",
                f"- max_dd: {pct(kpi.get('max_dd'))}",
                f"- sharpe: {kpi.get('sharpe', 'NA')}",
                f"- period: {kpi.get('period_start')}..{kpi.get('period_end')}",
                f"- lineage_url: {kpi.get('lineage_url') or 'lineage://paper-sim/' + str(kpi.get('sim_run_id'))}", ""]
    if pred:
        out += ["## Prediction Batch", "",
                f"- model_id: {pred.get('model_id')}",
                f"- prediction_table: {pred.get('asset')}",
                f"- model_version: {pred.get('model_version')}",
                f"- feature_version: {pred.get('feature_version')}",
                f"- label_version: {pred.get('label_version')}",
                f"- signal_date_range: {pred.get('min_signal_date')}..{pred.get('max_signal_date')}",
                f"- train_window: {pred.get('min_train_start')}..{pred.get('max_train_end')}",
                f"- rows: {pred.get('row_count')}", ""]
    if exists(conn, "mart_p1_optuna_trials"):
        row = rowdict(conn.execute(
            "SELECT run_id, trial_number, value, params_json FROM mart_p1_optuna_trials "
            "ORDER BY built_at DESC NULLS LAST, trial_number DESC LIMIT 1"
        ).fetchone())
        if row:
            out += ["## Model Training Evidence", "",
                    f"- optuna_run_id: {row.get('run_id')}",
                    f"- trial_number: {row.get('trial_number')}",
                    f"- value: {row.get('value')}",
                    f"- params_json: {row.get('params_json')}",
                    "- linkage: inferred from latest mart_p1_optuna_trials row unless exact model linkage exists", ""]
    roots = ["mart_paper_sim_kpi"] if kpi else [pred["asset"]] if pred else [root.get("panel", {}).get("asset") or root.get("asset")]
    out += ["## Dependency Tree", ""]
    rendered: set[tuple[str, int]] = set()

    def walk(asset: str, depth: int) -> None:
        if not asset:
            return
        node = graph.get(asset, {"parents": [], "attrs": {}})
        d = describe(conn, asset, node["attrs"], commit)
        ind = "  " * depth
        status = "ok" if exists(conn, asset) or asset.startswith(("model_artifact:", "backend/")) else "missing"
        out.extend([
            f"{ind}- {asset} [{status}]",
            f"{ind}  build_command: {d['command']}",
            f"{ind}  commit_hash: {d['commit']}",
            f"{ind}  row_count: {d['rows'] if d['rows'] is not None else 'NA'}",
            f"{ind}  pit_cutoff: {d['pit']}",
        ])
        if d["built"] != "NA":
            out.append(f"{ind}  built_at: {d['built']}")
        if d["note"]:
            out.append(f"{ind}  note: {d['note']}")
        if (asset, depth) in rendered:
            return
        rendered.add((asset, depth))
        for parent in node["parents"]:
            walk(parent, depth + 1)

    for r in roots:
        walk(r, 0)
    domains = sorted({d for asset in graph for d in WM.get(asset, [])})
    if domains and exists(conn, "mart_data_source_watermark"):
        ph = ", ".join(["?"] * len(domains))
        rows = [rowdict(r) for r in conn.execute(
            f"SELECT data_domain, source_name, source_tier, last_success_at, last_data_date, "
            f"row_count, fallback_active FROM mart_data_source_watermark WHERE data_domain IN ({ph}) "
            f"ORDER BY data_domain, source_tier",
            domains,
        ).fetchall()]
        out += ["", "## Source Freshness", "",
                "| domain | source | tier | last_success_at | last_data_date | rows | fallback |",
                "|---|---|---:|---|---|---:|---|"]
        out += [
            f"| {r['data_domain']} | {r['source_name']} | {r['source_tier']} | {r['last_success_at']} | "
            f"{r['last_data_date']} | {r['row_count']} | {r['fallback_active']} |"
            for r in rows
        ]
    gaps = sorted({n["attrs"]["note"] for n in graph.values() if n["attrs"].get("note")})
    if gaps:
        out += ["", "## Gaps", ""] + [f"- [MISSING] {g}" for g in gaps]
    out += ["", f"Trace runtime: {elapsed:.3f}s"]
    return "\n".join(out) + "\n"


def trace_markdown(conn: Any, **kwargs: Any) -> str:
    params = {
        "sim_run_id": None,
        "model_id": None,
        "panel_version": None,
        "asset_name": None,
        "db_path": None,
        "output_file": None,
        "max_depth": 5,
    }
    params.update(kwargs)
    args = argparse.Namespace(**params)
    start = time.monotonic()
    root, graph = build_graph(conn, args)
    return render(conn, root, graph, time.monotonic() - start)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace data lineage from a KPI, model, panel, or asset")
    parser.add_argument("--sim-run-id")
    parser.add_argument("--model-id")
    parser.add_argument("--panel-version")
    parser.add_argument("--asset-name")
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)
    try:
        with connect(args.db_path, read_only=True) as conn:
            markdown = trace_markdown(conn, **vars(args))
        if args.output_file:
            output_file = Path(args.output_file)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(markdown, encoding="utf-8")
        else:
            print(markdown, end="")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
