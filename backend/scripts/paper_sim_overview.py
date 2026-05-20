#!/usr/bin/env python3
"""Print all paper_sim KPI rows with cache and parent-chain context."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.db import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402


KPI_COLUMNS = [
    "sim_run_id",
    "variant",
    "annual_return",
    "sharpe",
    "max_dd",
    "monthly_win_rate",
    "parent_sim_run_id",
    "param_diff_json",
    "sim_config_hash",
    "built_at",
]


def exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        [table],
    ).fetchone()
    return row is not None


def columns(conn: Any, table: str) -> set[str]:
    if not exists(conn, table):
        return set()
    return {str(row[0]) for row in conn.execute(f"DESCRIBE {table}").fetchall()}


def rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    raise TypeError(f"unsupported row type: {type(row)!r}")


def load_kpi_rows(conn: Any) -> list[dict[str, Any]]:
    cols = columns(conn, "mart_paper_sim_kpi")
    if not cols:
        return []
    wanted = [col for col in KPI_COLUMNS if col in cols]
    order = "built_at DESC NULLS LAST" if "built_at" in cols else "sim_run_id"
    rows = conn.execute(
        f"SELECT {', '.join(wanted)} FROM mart_paper_sim_kpi ORDER BY {order}"
    ).fetchall()
    out = []
    for row in rows:
        item = {col: None for col in KPI_COLUMNS}
        item.update(rowdict(row))
        out.append(item)
    return out


def fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def fmt_num(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_hash(value: Any) -> str:
    if not value:
        return "legacy NULL"
    text = str(value)
    return text if len(text) <= 12 else text[:12]


def md_cell(value: Any) -> str:
    text = "N/A" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def summarize_diff(raw: Any) -> str:
    if not raw:
        return ""
    if isinstance(raw, dict):
        return ", ".join(sorted(str(k) for k in raw.keys()))
    text = str(raw)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:120]
    if isinstance(parsed, dict):
        parts = []
        for key in sorted(parsed.keys()):
            val = parsed[key]
            if isinstance(val, list) and len(val) == 2:
                parts.append(f"{key}: {val[0]} -> {val[1]}")
            else:
                parts.append(str(key))
        return ", ".join(parts)
    return text[:120]


def print_kpi_table(rows: list[dict[str, Any]]) -> None:
    print("## KPI Runs")
    print()
    print("| sim_run_id | ann_ret | sharpe | max_dd | win_rate | config_diff_vs_parent | parent_sim_run_id | sim_config_hash |")
    print("|---|---:|---:|---:|---:|---|---|---|")
    for row in rows:
        print(
            "| "
            + " | ".join(
                [
                    md_cell(row.get("sim_run_id")),
                    fmt_pct(row.get("annual_return")),
                    fmt_num(row.get("sharpe")),
                    fmt_pct(row.get("max_dd")),
                    fmt_pct(row.get("monthly_win_rate")),
                    md_cell(summarize_diff(row.get("param_diff_json"))),
                    md_cell(row.get("parent_sim_run_id")),
                    md_cell(fmt_hash(row.get("sim_config_hash"))),
                ]
            )
            + " |"
        )
    print()


def build_children(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_id = {str(row["sim_run_id"]): row for row in rows if row.get("sim_run_id")}
    children: dict[str, list[str]] = {sim_id: [] for sim_id in by_id}
    for row in rows:
        sim_id = row.get("sim_run_id")
        parent = row.get("parent_sim_run_id")
        if sim_id and parent and parent in children:
            children[str(parent)].append(str(sim_id))
    for child_ids in children.values():
        child_ids.sort()
    return children


def print_lineage_tree(rows: list[dict[str, Any]]) -> None:
    print("## Lineage Tree")
    print()
    if not rows:
        print("No paper_sim KPI rows found.")
        print()
        return
    by_id = {str(row["sim_run_id"]): row for row in rows if row.get("sim_run_id")}
    children = build_children(rows)
    roots = [
        sim_id for sim_id, row in by_id.items()
        if not row.get("parent_sim_run_id") or row.get("parent_sim_run_id") not in by_id
    ]
    roots.sort()
    seen: set[str] = set()

    def walk(sim_id: str, depth: int) -> None:
        if sim_id in seen:
            print(f"{'  ' * depth}- {sim_id} (cycle)")
            return
        seen.add(sim_id)
        row = by_id[sim_id]
        diff = summarize_diff(row.get("param_diff_json"))
        suffix = f" - {diff}" if diff else ""
        print(f"{'  ' * depth}- {sim_id}{suffix}")
        for child_id in children.get(sim_id, []):
            walk(child_id, depth + 1)

    for root in roots:
        walk(root, 0)
    print()


def delta(child: dict[str, Any], parent: dict[str, Any], key: str) -> float | None:
    if child.get(key) is None or parent.get(key) is None:
        return None
    try:
        return float(child[key]) - float(parent[key])
    except (TypeError, ValueError):
        return None


def print_param_impact(rows: list[dict[str, Any]]) -> None:
    print("## Parameter Impact")
    print()
    by_id = {str(row["sim_run_id"]): row for row in rows if row.get("sim_run_id")}
    pairs = [
        (by_id[str(row["parent_sim_run_id"])], row)
        for row in rows
        if row.get("parent_sim_run_id") and str(row["parent_sim_run_id"]) in by_id
    ]
    if not pairs:
        print("No parent-child paper_sim pairs found.")
        print()
        return

    print("| parent_sim_run_id | child_sim_run_id | params_changed | delta_ann_ret | delta_sharpe | delta_max_dd | delta_win_rate |")
    print("|---|---|---|---:|---:|---:|---:|")
    for parent, child in pairs:
        print(
            "| "
            + " | ".join(
                [
                    md_cell(parent.get("sim_run_id")),
                    md_cell(child.get("sim_run_id")),
                    md_cell(summarize_diff(child.get("param_diff_json"))),
                    fmt_pct(delta(child, parent, "annual_return")),
                    fmt_num(delta(child, parent, "sharpe")),
                    fmt_pct(delta(child, parent, "max_dd")),
                    fmt_pct(delta(child, parent, "monthly_win_rate")),
                ]
            )
            + " |"
        )
    print()


def render(rows: list[dict[str, Any]], db_path: Path) -> None:
    print("# Paper Sim Overview")
    print()
    print(f"Database: `{db_path}`")
    print(f"Rows: {len(rows)}")
    print()
    print_kpi_table(rows)
    print_lineage_tree(rows)
    print_param_impact(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print paper_sim KPI overview")
    parser.add_argument("--db-path", default=str(DB_PATH), help="DuckDB path, defaults to production DB")
    args = parser.parse_args(argv)

    db_path = Path(args.db_path)
    conn = connect(str(db_path), read_only=True)
    try:
        if not exists(conn, "mart_paper_sim_kpi"):
            render([], db_path)
            return 0
        rows = load_kpi_rows(conn)
        render(rows, db_path)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
