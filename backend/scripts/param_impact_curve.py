"""param_impact_curve — paper_sim 参数变化对 KPI 的 impact curve (criteria #10 P1).

用法:
  PYTHONPATH=backend python backend/scripts/param_impact_curve.py --sim-run-id <id>
  PYTHONPATH=backend python backend/scripts/param_impact_curve.py --variant champion_minhold15

输出: markdown 表 — 每个 parent->child 跳, 显示 Δ sharpe / Δ ann / Δ dd / Δ turnover / param_diff_json

依赖: mart_paper_sim_kpi schema 含 parent_sim_run_id + param_diff_json (commit a2281696).

PIT: 纯 read-only, 不写 DB, 不改 model/strategy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("ERROR: duckdb not installed", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "smartmoney.duckdb"


def fetch_chain(conn, sim_run_id: str) -> list[dict]:
    """Walk parent_sim_run_id chain from leaf to root."""
    chain = []
    seen = set()
    current = sim_run_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute(
            """
            SELECT sim_run_id, parent_sim_run_id, sim_config_hash, param_diff_json,
                   annual_return, max_dd, sharpe, monthly_win_rate,
                   annual_turnover, avg_holding_days, built_at, variant
              FROM mart_paper_sim_kpi WHERE sim_run_id = ?
            """,
            [current],
        ).fetchone()
        if not row:
            break
        rec = {
            "sim_run_id": row[0],
            "parent_sim_run_id": row[1],
            "sim_config_hash": row[2],
            "param_diff_json": row[3],
            "annual_return": row[4],
            "max_dd": row[5],
            "sharpe": row[6],
            "monthly_win_rate": row[7],
            "annual_turnover": row[8],
            "avg_holding_days": row[9],
            "built_at": row[10],
            "variant": row[11],
        }
        chain.append(rec)
        current = rec["parent_sim_run_id"]
    return chain


def fetch_variant_chain(conn, variant_pattern: str) -> list[dict]:
    """Fallback: pull all runs with matching variant 模糊 LIKE (legacy no parent chain)."""
    rows = conn.execute(
        """
        SELECT sim_run_id, parent_sim_run_id, sim_config_hash, param_diff_json,
               annual_return, max_dd, sharpe, monthly_win_rate,
               annual_turnover, avg_holding_days, built_at, variant
          FROM mart_paper_sim_kpi
         WHERE variant LIKE ?
         ORDER BY built_at ASC
        """,
        [f"%{variant_pattern}%"],
    ).fetchall()
    return [
        {
            "sim_run_id": r[0],
            "parent_sim_run_id": r[1],
            "sim_config_hash": r[2],
            "param_diff_json": r[3],
            "annual_return": r[4],
            "max_dd": r[5],
            "sharpe": r[6],
            "monthly_win_rate": r[7],
            "annual_turnover": r[8],
            "avg_holding_days": r[9],
            "built_at": r[10],
            "variant": r[11],
        }
        for r in rows
    ]


def render_curve(chain: list[dict]) -> str:
    """Render Δ KPI curve sorted by built_at."""
    if not chain:
        return "# Param Impact Curve\n\n[EMPTY] 0 rows.\n"
    chain_sorted = sorted(chain, key=lambda r: r["built_at"] or "")
    out = [
        "# Param Impact Curve",
        "",
        f"Total runs: {len(chain_sorted)}",
        "",
        "## KPI Timeline",
        "",
        "| # | variant[:24] | ann | dd | sharpe | win | turn | hold | built_at | parent? |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for i, r in enumerate(chain_sorted):
        v = (r["variant"] or "")[:24]
        ann = (r["annual_return"] or 0) * 100
        dd = (r["max_dd"] or 0) * 100
        sh = r["sharpe"] or 0
        win = (r["monthly_win_rate"] or 0) * 100
        turn = r["annual_turnover"] or 0
        hold = r["avg_holding_days"] or 0
        bp = "yes" if r["parent_sim_run_id"] else "(root)"
        out.append(
            f"| {i} | {v} | {ann:.2f}% | {dd:.2f}% | {sh:.2f} | {win:.2f}% "
            f"| {turn:.1f}x | {hold:.1f}d | {r['built_at']} | {bp} |"
        )
    out += ["", "## Δ Impact (vs previous run)", ""]
    out += [
        "| # | Δ ann | Δ dd | Δ sharpe | Δ win | Δ turn | param_diff_json |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    prev = None
    for i, r in enumerate(chain_sorted):
        if prev is None:
            out.append(f"| {i} | baseline | baseline | baseline | baseline | baseline | (root) |")
        else:
            d_ann = ((r["annual_return"] or 0) - (prev["annual_return"] or 0)) * 100
            d_dd = ((r["max_dd"] or 0) - (prev["max_dd"] or 0)) * 100
            d_sh = (r["sharpe"] or 0) - (prev["sharpe"] or 0)
            d_wn = ((r["monthly_win_rate"] or 0) - (prev["monthly_win_rate"] or 0)) * 100
            d_tn = (r["annual_turnover"] or 0) - (prev["annual_turnover"] or 0)
            diff = r["param_diff_json"] or "(none)"
            if len(str(diff)) > 60:
                diff = str(diff)[:60] + "..."
            out.append(
                f"| {i} | {d_ann:+.2f}% | {d_dd:+.2f}% | {d_sh:+.2f} | {d_wn:+.2f}% | {d_tn:+.1f}x | `{diff}` |"
            )
        prev = r
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sim-run-id", help="leaf sim_run_id 走 parent_sim_run_id 链 walk")
    p.add_argument("--variant", help="跨 sim_run_id 按 variant LIKE 聚合 (legacy fallback 无 parent chain)")
    p.add_argument("--db-path", default=str(DB_PATH))
    p.add_argument("--output-file")
    args = p.parse_args(argv)
    if not args.sim_run_id and not args.variant:
        p.error("provide --sim-run-id or --variant")
    conn = duckdb.connect(args.db_path, read_only=True)
    try:
        if args.sim_run_id:
            chain = fetch_chain(conn, args.sim_run_id)
        else:
            chain = fetch_variant_chain(conn, args.variant)
    finally:
        conn.close()
    md = render_curve(chain)
    if args.output_file:
        out = Path(args.output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
