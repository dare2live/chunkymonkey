"""dim_holder_identity —— 一码一实体的展示名（纯读 canonical 派生）。

# 它解决什么

`canonical_top10_float_holders_period` 里同一个 `holder_code` 会带多种 `holder_name` 写法。
实测(2026-09-07 回填后, 1,789,253 行): **4,467 个 code 用过多个名字**, 其中
code 10671586「香港中央结算」有 9 种写法(含繁体「結算」、(A股)/(沪股通) 后缀、"中心"/"公司"),
code 510500 有 9 种(其中一种把连字符写成汉字「一」)。
机构档案要按实体聚合, 就需要「这个码叫什么」有一个确定答案。

# 这是**当前快照**, 不是 PIT 维度 —— 用错会撞红线 1

`display_name` 取的是 **notice_date 最新那次**的原始 `holder_name`。所以:

  **禁止**把它 JOIN 进历史行去渲染。拿 2026 年的名字去显示 2021 年的持仓,
  就是「用之后的状态改写历史上某天的归属」。

股票页的十大股东列表**按公告原样展示**(业主明确要求), 用的是 canonical 自己的 `holder_name`,
不经过本表。本表只服务**机构档案页**那种「这个实体, 现在」的场景。
同款先例见 `data_layers.yaml` 的 `dim_stock_dc_industry`(DC 当前快照, 带同类警告)。

# 边界

- 只收 `holder_code IS NOT NULL` 的行 —— 个人无码(供应商只给机构发码, 实测 783,737 行 100% 无码),
  个人没有稳定身份键, 不进本表。牛散那条线是独立的弱身份表, 见 `n1_niusan_design.md`。
- `holder_code` 是**混合粒度**: 8/11 位是法人码(香港中央结算 10671586 / 合肥产投兴巢…),
  6 位是**基金产品代码**(实测 3,097 个码 / 159,605 行)。所以「财通基金」这家公司拼不出来,
  只能有它 125 只产品各自的一行。管理人层要从名字前缀推导(红线 5: 可推导的不入库), 不在本表。
- 同一 (股, 报告期, 码) 可以合法地有多行(同一机构经自有资金与 QFII 两个通道各持一仓,
  实测 62 组 / 其中 50 组名字带通道后缀)。本表按 code 聚合, 这些行会被算进同一实体 —— 这是对的。

用法: PYTHONPATH=backend python backend/scripts/publish_holder_identity.py [--db PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.db import get_conn  # noqa: E402
from services.data_sources.holders_top10_schema import CANONICAL_TABLE  # noqa: E402

TABLE = "dim_holder_identity"
WRITER_ID = "backend/scripts/publish_holder_identity.py"

DDL = f"""
CREATE OR REPLACE TABLE {TABLE} AS
WITH ranked AS (
    SELECT
        holder_code,
        holder_name,
        holder_type,
        notice_date,
        -- 确定性排序: notice_date 最新; 同日再按 report_date、名字排, 避免同分并列时结果不稳定
        -- (drift 门要求可重跑 hash 相等)。
        ROW_NUMBER() OVER (
            PARTITION BY holder_code
            ORDER BY notice_date DESC, report_date DESC, holder_name DESC
        ) AS rn
    FROM {CANONICAL_TABLE}
    WHERE holder_code IS NOT NULL AND NOT is_exit_row
),
agg AS (
    SELECT
        holder_code,
        COUNT(DISTINCT holder_name) AS n_name_variants,
        MIN(notice_date)            AS first_notice_date,
        MAX(notice_date)            AS last_notice_date,
        COUNT(DISTINCT stock_code)  AS n_stocks,
        COUNT(*)                    AS n_rows
    FROM {CANONICAL_TABLE}
    WHERE holder_code IS NOT NULL AND NOT is_exit_row
    GROUP BY 1
)
SELECT
    a.holder_code,
    r.holder_name  AS display_name,
    r.holder_type  AS holder_type_latest,
    a.n_name_variants,
    a.first_notice_date,
    a.last_notice_date,
    a.n_stocks,
    a.n_rows
FROM agg a
JOIN ranked r ON r.holder_code = a.holder_code AND r.rn = 1
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="写入目标库 (默认走 services.db)")
    args = ap.parse_args()
    if args.db:
        import services.db as _db

        target = Path(args.db).resolve()
        _db.DB_PATH = target
        _db.DB_DIR = target.parent

    conn = get_conn()
    try:
        conn.execute(DDL)
        n = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        src = conn.execute(
            f"SELECT COUNT(DISTINCT holder_code) FROM {CANONICAL_TABLE} "
            "WHERE holder_code IS NOT NULL AND NOT is_exit_row"
        ).fetchone()[0]
        multi = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE n_name_variants > 1"
        ).fetchone()[0]
    finally:
        conn.close()

    print(f"[holder-identity] {TABLE}: {n:,} 行 (canonical 不同 code {src:,})")
    print(f"[holder-identity] 其中 {multi:,} 个码用过多种名字写法")
    if n != src:
        print(
            f"[holder-identity] FAILED 行数 {n:,} != canonical 不同 code {src:,} "
            "—— 一码一行的契约被破坏",
            file=sys.stderr,
        )
        return 1
    if n == 0:
        print("[holder-identity] FAILED 零行 —— canonical 没有带 code 的行?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
