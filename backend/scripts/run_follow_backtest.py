#!/usr/bin/env python3
"""跟投回测 CLI：对给定 cohort 跑一组参数组合，结果写入 fact_institution_follow_backtest。

用途：§18 单 cohort Grid 验证；§15 漏斗阈值反推的原始证据。

用法示例：
  # 按 L1 行业 + inst_type 分组，对 Top-N 样本 cohort 跑 Grid
  python -m backend.scripts.run_follow_backtest --scheme L1_instgroup --top 5 --grid default

  # 单 cohort
  python -m backend.scripts.run_follow_backtest --inst-type-group 稳健型 --l1 医药 --grid default

  # 只试一组参数
  python -m backend.scripts.run_follow_backtest --inst-type-group 稳健型 --l1 医药 \
      --entry-lag 1 --max-hold 20 --stop-loss -0.08 --take-profit 0.15

表 schema 已经在首次写入时自动 CREATE IF NOT EXISTS。
"""
from __future__ import annotations

import argparse
import itertools
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.event_simulator import simulate_events

logger = logging.getLogger("run_follow_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# §22 发现：原先 INST_TYPE_GROUPS 里的 key（"公募基金/险资/游资/自营"等）在数据库里不存在，
# 真实 inst_type 是：券商、社保、QFII、牛散、基金、保险、私募、国家队、国家大基金、北向。
# 默认 scheme 改为 inst_type_L1（不合并），保留合并 scheme 以兼容旧数据。
INST_TYPE_GROUPS = {
    "稳健型": {"QFII", "社保", "保险", "基金", "国家大基金"},
    "交易型": {"券商", "私募"},
    "另类": {"牛散", "国家队"},
}


def _inst_type_to_group(t: str) -> str:
    for g, members in INST_TYPE_GROUPS.items():
        if t in members:
            return g
    return "未分类"


TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_institution_follow_backtest (
    cohort_scheme     TEXT NOT NULL,
    cohort_key        TEXT NOT NULL,
    backtest_run_at   TEXT NOT NULL,
    split             TEXT NOT NULL,   -- 'all' | 'train' | 'holdout'
    entry_lag         INTEGER NOT NULL,
    max_hold_days     INTEGER NOT NULL,
    stop_loss         REAL,
    take_profit       REAL,
    n_events          INTEGER,
    n_filled          INTEGER,
    avg_pnl           REAL,
    avg_hold_days     REAL,
    win_rate          REAL,
    annual_return     REAL,
    sharpe            REAL,
    avg_position_maxdd REAL,
    p95_position_maxdd REAL,
    exit_reasons_json TEXT,
    event_date_min    TEXT,
    event_date_max    TEXT,
    PRIMARY KEY (cohort_scheme, cohort_key, backtest_run_at, split, entry_lag, max_hold_days, stop_loss, take_profit)
);
"""


def ensure_table(conn):
    conn.executescript(TABLE_DDL)
    conn.commit()


def load_cohort_events(
    conn,
    cohort_scheme: str,
    cohort_key: str,
    exclude_north: bool = True,
) -> pd.DataFrame:
    """按 cohort 方案加载事件。返回列：institution_id, stock_code, notice_date"""
    base_sql = """
        SELECT fe.institution_id, fe.stock_code, fe.notice_date,
               ii.name AS inst_name, ii.type AS inst_type,
               ind.tdx_l1_name AS l1, ind.tdx_l2_name AS l2
        FROM fact_institution_event fe
        LEFT JOIN inst_institutions ii ON fe.institution_id = ii.id
        LEFT JOIN dim_stock_tdx_industry ind ON fe.stock_code = ind.stock_code
        WHERE fe.event_type IN ('new_entry','increase')
          AND fe.notice_date IS NOT NULL AND fe.notice_date != ''
          AND ii.type IS NOT NULL
    """
    # 仅在需要行业分组时强制 l1 非空；institution scheme 不强制
    if cohort_scheme in ("inst_type_L1", "L1_instgroup", "L1", "institution_L1"):
        base_sql += " AND ind.tdx_l1_name IS NOT NULL "
    if cohort_scheme in ("institution_L2", "L2"):
        base_sql += " AND ind.tdx_l2_name IS NOT NULL "
    if exclude_north:
        base_sql += " AND ii.type != '北向' "
    df = pd.read_sql_query(base_sql, conn)
    if df.empty:
        return df

    if cohort_scheme == "institution":
        df = df[df["institution_id"] == cohort_key]
    elif cohort_scheme == "institution_L1":
        df = df[df["institution_id"].astype(str) + "|" + df["l1"].astype(str) == cohort_key]
    elif cohort_scheme == "institution_L2":
        df = df[df["institution_id"].astype(str) + "|" + df["l2"].astype(str) == cohort_key]
    elif cohort_scheme == "L2":
        df = df[df["l2"].astype(str) == cohort_key]
    elif cohort_scheme == "inst_type_L1":
        df = df[df["inst_type"].astype(str) + "|" + df["l1"].astype(str) == cohort_key]
    elif cohort_scheme == "L1_instgroup":
        df["inst_group"] = df["inst_type"].map(_inst_type_to_group)
        df = df[df["inst_group"] + "|" + df["l1"].astype(str) == cohort_key]
    elif cohort_scheme == "L1":
        df = df[df["l1"].astype(str) == cohort_key]
    elif cohort_scheme == "inst_group":
        df["inst_group"] = df["inst_type"].map(_inst_type_to_group)
        df = df[df["inst_group"] == cohort_key]
    elif cohort_scheme == "all":
        pass
    else:
        raise ValueError(f"未知 cohort_scheme: {cohort_scheme}")

    return df[["institution_id", "stock_code", "notice_date"]].reset_index(drop=True)


def list_top_cohorts(
    conn,
    scheme: str,
    top: int,
    min_samples: int,
    exclude_north: bool = True,
) -> list[tuple[str, int]]:
    """列 Top-N cohort，返回 [(cohort_key, n), ...]"""
    base_sql = """
        SELECT fe.institution_id, ii.name AS inst_name, ii.type AS inst_type,
               ind.tdx_l1_name AS l1, ind.tdx_l2_name AS l2
        FROM fact_institution_event fe
        LEFT JOIN inst_institutions ii ON fe.institution_id = ii.id
        LEFT JOIN dim_stock_tdx_industry ind ON fe.stock_code = ind.stock_code
        WHERE fe.event_type IN ('new_entry','increase')
          AND fe.notice_date IS NOT NULL AND fe.notice_date != ''
          AND ii.type IS NOT NULL
    """
    if scheme in ("inst_type_L1", "L1_instgroup", "L1", "institution_L1"):
        base_sql += " AND ind.tdx_l1_name IS NOT NULL "
    if scheme in ("institution_L2", "L2"):
        base_sql += " AND ind.tdx_l2_name IS NOT NULL "
    if exclude_north:
        base_sql += " AND ii.type != '北向' "
    df = pd.read_sql_query(base_sql, conn)

    if scheme == "institution":
        df["cohort_key"] = df["institution_id"].astype(str)
    elif scheme == "institution_L1":
        df["cohort_key"] = df["institution_id"].astype(str) + "|" + df["l1"].astype(str)
    elif scheme == "institution_L2":
        df["cohort_key"] = df["institution_id"].astype(str) + "|" + df["l2"].astype(str)
    elif scheme == "L2":
        df["cohort_key"] = df["l2"].astype(str)
    elif scheme == "inst_type_L1":
        df["cohort_key"] = df["inst_type"].astype(str) + "|" + df["l1"].astype(str)
    elif scheme == "L1_instgroup":
        df["inst_group"] = df["inst_type"].map(_inst_type_to_group)
        df["cohort_key"] = df["inst_group"] + "|" + df["l1"].astype(str)
    elif scheme == "L1":
        df["cohort_key"] = df["l1"].astype(str)
    elif scheme == "inst_group":
        df["inst_group"] = df["inst_type"].map(_inst_type_to_group)
        df["cohort_key"] = df["inst_group"]
    else:
        raise ValueError(f"未知 scheme: {scheme}")

    counts = df.groupby("cohort_key").size().reset_index(name="n")
    counts = counts[counts["n"] >= min_samples].sort_values("n", ascending=False).head(top)
    return list(counts.itertuples(index=False, name=None))


# 默认 Grid 参数空间（§20 修正版 3x3x2）
DEFAULT_GRID = {
    "entry_lag": [1],  # 次日开仓（首版不扫 lag）
    "max_hold_days": [10, 20, 40],
    "stop_loss": [None, -0.08, -0.15],
    "take_profit": [None, 0.20],
}


def _row_for_split(
    events: pd.DataFrame,
    params: dict,
    split: str,
    cohort_scheme: str,
    cohort_key: str,
    run_at: str,
) -> Optional[dict]:
    import json
    result = simulate_events(events, params)
    if result.get("n_filled", 0) == 0:
        return None
    return {
        "cohort_scheme": cohort_scheme,
        "cohort_key": cohort_key,
        "backtest_run_at": run_at,
        "split": split,
        "entry_lag": params["entry_lag"],
        "max_hold_days": params["max_hold_days"],
        "stop_loss": params["stop_loss"],
        "take_profit": params["take_profit"],
        "n_events": result["n_events"],
        "n_filled": result["n_filled"],
        "avg_pnl": result["avg_pnl"],
        "avg_hold_days": result["avg_hold_days"],
        "win_rate": result["win_rate"],
        "annual_return": result["annual_return"],
        "sharpe": result["sharpe"],
        "avg_position_maxdd": result["avg_position_maxdd"],
        "p95_position_maxdd": result["p95_position_maxdd"],
        "exit_reasons_json": json.dumps(result.get("exit_reason_counts", {}), ensure_ascii=False),
        "event_date_min": str(events["notice_date"].min()),
        "event_date_max": str(events["notice_date"].max()),
    }


def run_backtest_for_cohort(
    conn,
    cohort_scheme: str,
    cohort_key: str,
    grid: dict[str, list],
    walk_forward: Optional[float] = None,
    dry_run: bool = False,
) -> pd.DataFrame:
    """参数 walk_forward：None 表示全样本；float in (0,1) 表示按 notice_date 切分，前占 ratio 为 train，后为 holdout。"""
    events = load_cohort_events(conn, cohort_scheme, cohort_key)
    if events.empty:
        logger.warning("[%s | %s] 无事件，跳过", cohort_scheme, cohort_key)
        return pd.DataFrame()
    logger.info("[%s | %s] 加载事件 %d 条", cohort_scheme, cohort_key, len(events))

    run_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    results: list[dict] = []
    param_combos = list(itertools.product(
        grid["entry_lag"], grid["max_hold_days"], grid["stop_loss"], grid["take_profit"]
    ))
    logger.info("  Grid 组合 %d", len(param_combos))

    if walk_forward is not None:
        sorted_ev = events.sort_values("notice_date").reset_index(drop=True)
        cut = int(len(sorted_ev) * walk_forward)
        train_ev = sorted_ev.iloc[:cut].reset_index(drop=True)
        hold_ev = sorted_ev.iloc[cut:].reset_index(drop=True)
        logger.info("  walk-forward: train=%d holdout=%d (cut at %s)", len(train_ev), len(hold_ev), train_ev["notice_date"].max())
        split_pairs = [("train", train_ev), ("holdout", hold_ev)]
    else:
        split_pairs = [("all", events)]

    for entry_lag, max_hold, sl, tp in param_combos:
        params = {"entry_lag": entry_lag, "max_hold_days": max_hold, "stop_loss": sl, "take_profit": tp}
        for split, ev in split_pairs:
            row = _row_for_split(ev, params, split, cohort_scheme, cohort_key, run_at)
            if row is None:
                continue
            results.append(row)
            if split in ("train", "all"):
                logger.info(
                    "  [%s] hold=%d sl=%s tp=%s → n=%d win=%.1f%% pnl=%.2f%% sharpe=%.2f",
                    split, max_hold, sl, tp, row["n_filled"],
                    row["win_rate"] * 100, row["avg_pnl"] * 100, row["sharpe"],
                )

    df = pd.DataFrame(results)
    if df.empty:
        return df
    if not dry_run:
        df.to_sql("fact_institution_follow_backtest", conn, if_exists="append", index=False)
        conn.commit()
        logger.info("  写入 %d 行", len(df))
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheme", default="institution_L2",
                        choices=["institution_L2", "institution_L1", "institution",
                                 "inst_type_L1", "L1_instgroup", "L1", "L2", "inst_group"])
    parser.add_argument("--top", type=int, default=1, help="跑 Top-N 样本最大的 cohort")
    parser.add_argument("--min-samples", type=int, default=300, help="最低样本阈值")
    parser.add_argument("--cohort-key", type=str, help="单 cohort：直接指定 key（格式依 scheme 定义）")
    parser.add_argument("--include-north", action="store_true", help="包含北向机构（默认排除）")
    parser.add_argument("--walk-forward", type=float, default=None,
                        help="按 notice_date 切分 train/holdout，取值 (0,1)，典型 0.7")
    parser.add_argument("--dry-run", action="store_true", help="不写入数据库")
    parser.add_argument("--grid", default="default", choices=["default"])
    args = parser.parse_args()

    conn = get_conn()
    try:
        ensure_table(conn)
        grid = DEFAULT_GRID

        if args.cohort_key:
            cohorts = [(args.cohort_key, -1)]
        else:
            cohorts = list_top_cohorts(
                conn, args.scheme, top=args.top,
                min_samples=args.min_samples,
                exclude_north=not args.include_north,
            )
            if not cohorts:
                logger.error("找不到样本 >= %d 的 cohort", args.min_samples)
                return
            logger.info("Top %d cohorts: %s", len(cohorts), cohorts)

        for cohort_key, n in cohorts:
            logger.info("=== cohort [%s | %s] n=%d ===", args.scheme, cohort_key, n)
            run_backtest_for_cohort(
                conn, args.scheme, cohort_key, grid,
                walk_forward=args.walk_forward,
                dry_run=args.dry_run,
            )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
