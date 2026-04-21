"""
sw_industry_client.py — 申万宏源行业分类同步（取代 TDX 行业三级分类）

数据源：akshare.stock_industry_clf_hist_sw()
  - 申万宏源研究·行业分类历史变更全表
  - ~12717 行历史变更记录，覆盖 5805 只股票
  - 每股取 start_date 最新一条作为当前生效分类
  - 6 位 industry_code = L1(2) + L2(2) + L3(2)
  - **L3 覆盖 100%**（取代 TDX 51% 覆盖率的关键收益）

表：dim_stock_sw_industry（PK: stock_code）
  stock_code     股票代码
  sw_l1          一级代码（前 2 位）
  sw_l2          二级代码（前 4 位）
  sw_l3          三级代码（6 位）
  sw_l1_name     一级名称（来自 sw_index_first_info 反推，31 个活跃 L1）
  sw_l2_name     二级名称（暂留 NULL，由 build 脚本渐进补全）
  sw_l3_name     三级名称（暂留 NULL，由 build 脚本渐进补全）
  start_date     该分类生效起始日
  source_update  申万 update_time
  updated_at     本地落库时间

与 TDX 共存策略：
  Phase 1（本步）：仅新建 SW 表，TDX 表保留不动
  Phase 2：mart 派生层切到 SW，TDX 字段标记 deprecated
  Phase 3：TDX 表 + client 整体下线
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger("cm-api")


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dim_stock_sw_industry (
            stock_code     TEXT PRIMARY KEY,
            sw_l1          TEXT,
            sw_l2          TEXT,
            sw_l3          TEXT,
            sw_l1_name     TEXT,
            sw_l2_name     TEXT,
            sw_l3_name     TEXT,
            start_date     TEXT,
            source_update  TEXT,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sw_industry_l1 ON dim_stock_sw_industry(sw_l1);
        CREATE INDEX IF NOT EXISTS idx_sw_industry_l2 ON dim_stock_sw_industry(sw_l2);
        CREATE INDEX IF NOT EXISTS idx_sw_industry_l3 ON dim_stock_sw_industry(sw_l3);
    """)
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Parse industry_code
# ─────────────────────────────────────────────────────────────────────

def _split_sw_code(code: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """6 位 industry_code → (L1=前2, L2=前4, L3=6)。

    短于 6 位时降级（理论上申万源 100% 是 6 位，但容错）。
    """
    if code is None:
        return (None, None, None)
    s = str(code).strip()
    if not s.isdigit():
        return (None, None, None)
    n = len(s)
    l1 = s[:2] if n >= 2 else None
    l2 = s[:4] if n >= 4 else None
    l3 = s[:6] if n >= 6 else None
    return (l1, l2, l3)


# ─────────────────────────────────────────────────────────────────────
# Fetch + parse via akshare
# ─────────────────────────────────────────────────────────────────────

_ParsedRow = tuple[
    str,                                # stock_code
    Optional[str], Optional[str], Optional[str],   # l1/l2/l3
    Optional[str], Optional[str], Optional[str],   # name l1/l2/l3
    Optional[str], Optional[str],                  # start_date / source_update
]


def _fetch_and_parse() -> list[_ParsedRow]:
    """拉申万分类全表，每股取 start_date 最新一条，解析为标准行。"""
    import akshare as ak
    from services.sw_industry_names import get_sw_l1_name, get_sw_l2_name, get_sw_l3_name

    df = ak.stock_industry_clf_hist_sw()
    if df is None or len(df) == 0:
        raise RuntimeError("akshare.stock_industry_clf_hist_sw 返回空")

    # 标准化 symbol：6 位定长字符串
    df = df.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)

    # 每股取 start_date 最新一条
    latest = df.sort_values("start_date").drop_duplicates("symbol", keep="last")

    rows: list[_ParsedRow] = []
    for _, r in latest.iterrows():
        code = str(r.get("symbol") or "").strip()
        if not code:
            continue
        ind_code = str(r.get("industry_code") or "").strip()
        l1, l2, l3 = _split_sw_code(ind_code)
        rows.append((
            code,
            l1, l2, l3,
            get_sw_l1_name(l1),
            get_sw_l2_name(l2),
            get_sw_l3_name(l3),
            str(r.get("start_date") or "") or None,
            str(r.get("update_time") or "") or None,
        ))
    return rows


# ─────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────

def sync_sw_industry(conn: sqlite3.Connection) -> dict:
    """拉取申万分类全表并 upsert。

    Returns
    -------
    dict {
        'rows_fetched':   解析行数,
        'rows_upserted':  写库行数,
        'l1_count':       唯一 L1 数,
        'l2_count':       唯一 L2 数,
        'l3_count':       唯一 L3 数,
        'l3_coverage':    L3 非空率（应接近 100%），
        'fetched_at':     下载时间,
        'errors':         list[str],
    }
    """
    _ensure_table(conn)
    result: dict = {
        "rows_fetched": 0,
        "rows_upserted": 0,
        "l1_count": 0,
        "l2_count": 0,
        "l3_count": 0,
        "l3_coverage": 0.0,
        "fetched_at": None,
        "errors": [],
    }

    try:
        parsed = _fetch_and_parse()
    except Exception as exc:
        logger.error(f"[sw_industry] 拉取/解析失败: {exc}")
        result["errors"].append(f"fetch failed: {exc}")
        return result

    result["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["rows_fetched"] = len(parsed)

    if not parsed:
        result["errors"].append("parsed empty")
        return result

    l1_set = {r[1] for r in parsed if r[1]}
    l2_set = {r[2] for r in parsed if r[2]}
    l3_set = {r[3] for r in parsed if r[3]}
    l3_filled = sum(1 for r in parsed if r[3])

    result["l1_count"] = len(l1_set)
    result["l2_count"] = len(l2_set)
    result["l3_count"] = len(l3_set)
    result["l3_coverage"] = round(l3_filled / max(len(parsed), 1), 4)

    upsert_sql = """
        INSERT INTO dim_stock_sw_industry
          (stock_code, sw_l1, sw_l2, sw_l3,
           sw_l1_name, sw_l2_name, sw_l3_name,
           start_date, source_update, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code) DO UPDATE SET
          sw_l1         = excluded.sw_l1,
          sw_l2         = excluded.sw_l2,
          sw_l3         = excluded.sw_l3,
          sw_l1_name    = excluded.sw_l1_name,
          sw_l2_name    = excluded.sw_l2_name,
          sw_l3_name    = excluded.sw_l3_name,
          start_date    = excluded.start_date,
          source_update = excluded.source_update,
          updated_at    = CURRENT_TIMESTAMP
    """
    conn.executemany(upsert_sql, parsed)
    conn.commit()
    result["rows_upserted"] = len(parsed)

    logger.info(
        f"[sw_industry] upsert 完成: {len(parsed)} 行, "
        f"L1={result['l1_count']} / L2={result['l2_count']} / L3={result['l3_count']}, "
        f"L3 覆盖={result['l3_coverage']*100:.1f}%"
    )
    return result


# ─────────────────────────────────────────────────────────────────────
# Lookup helpers (供 resolver / mart 调用)
# ─────────────────────────────────────────────────────────────────────

def get_sw_industry(conn: sqlite3.Connection, stock_code: str) -> Optional[dict]:
    """查询单只股票的申万行业三级分类（含中文名）。"""
    row = conn.execute(
        """SELECT sw_l1, sw_l2, sw_l3, sw_l1_name, sw_l2_name, sw_l3_name,
                  start_date, source_update
             FROM dim_stock_sw_industry WHERE stock_code = ?""",
        (stock_code,),
    ).fetchone()
    if not row:
        return None
    return {
        "sw_l1": row[0], "sw_l2": row[1], "sw_l3": row[2],
        "sw_l1_name": row[3], "sw_l2_name": row[4], "sw_l3_name": row[5],
        "start_date": row[6], "source_update": row[7],
    }


def load_sw_industry_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """批量加载全量股票→申万行业映射（含中文名）。"""
    rows = conn.execute(
        """SELECT stock_code, sw_l1, sw_l2, sw_l3,
                  sw_l1_name, sw_l2_name, sw_l3_name,
                  start_date, source_update
             FROM dim_stock_sw_industry"""
    ).fetchall()
    return {
        r[0]: {
            "sw_l1": r[1], "sw_l2": r[2], "sw_l3": r[3],
            "sw_l1_name": r[4], "sw_l2_name": r[5], "sw_l3_name": r[6],
            "start_date": r[7], "source_update": r[8],
        }
        for r in rows
    }
