"""
tdx_industry_client.py — 通达信行业分类同步（取代申万 SW 三级分类）

数据源：TDX 行情协议远程文件 `tdxhy.cfg`
  - 文件大小 ~150KB，5604 只股票
  - 每行 6 字段：市场|代码|通达信 T 码|||申万 X 码
  - **每只股票唯一对应一个 T 码**（唯一性已实测验证）
  - T 码三级结构：
      T + 2 位 → 一级（13 个）
      T + 4 位 → 二级（56 个）
      T + 6 位 → 三级（76 个）

拉取方式：
  走 `call_tdx_quotes_with_retry` 自动重试 117 台服务器。
  mootdx 的 `.block()` 只支持 block_*.dat，拉不到 .cfg，
  必须直接调底层 `client.get_block_info_meta()` + `client.get_block_info()`。

表：dim_stock_tdx_industry（PK: stock_code）
  stock_code   股票代码
  tdx_l1       一级代码（T + 2 位）
  tdx_l2       二级代码（T + 4 位）
  tdx_l3       三级代码（T + 6 位）
  sw_x_legacy  申万 X 码（从 tdxhy.cfg 同一行提取，仅作审计/对照，不参与业务）
  updated_at   TIMESTAMP
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger("cm-api")

_TDXHY_FILE = "tdxhy.cfg"
_ONE_CHUNK = 0x7530  # 30000 bytes per chunk (TDX protocol limit)


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dim_stock_tdx_industry (
            stock_code    TEXT PRIMARY KEY,
            tdx_l1        TEXT,
            tdx_l2        TEXT,
            tdx_l3        TEXT,
            tdx_l1_name   TEXT,
            tdx_l2_name   TEXT,
            tdx_l3_name   TEXT,
            sw_x_legacy   TEXT,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l1 ON dim_stock_tdx_industry(tdx_l1);
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l2 ON dim_stock_tdx_industry(tdx_l2);
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l3 ON dim_stock_tdx_industry(tdx_l3);
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(dim_stock_tdx_industry)").fetchall()}
    for col in ("tdx_l1_name", "tdx_l2_name", "tdx_l3_name"):
        if col not in existing:
            conn.execute(f"ALTER TABLE dim_stock_tdx_industry ADD COLUMN {col} TEXT")
            logger.info(f"[tdx_industry] ALTER TABLE: 新增列 {col}")
    conn.commit()


# ─────────────────────────────────────────────────────────────────────
# Fetch tdxhy.cfg via tdxhub
# ─────────────────────────────────────────────────────────────────────

def _fetch_tdxhy_bytes() -> tuple[bytes, str]:
    """通过 tdxhub 117 台服务器重试下载 tdxhy.cfg。返回 (bytes, source_label)。"""
    from backend.services.tdx_source import call_tdx_quotes_with_retry

    def _op(client):
        meta = client.client.get_block_info_meta(_TDXHY_FILE)
        if not meta or not meta.get("size"):
            raise RuntimeError(f"tdxhy.cfg meta 为空: {meta}")

        size = int(meta["size"])
        chunks = size // _ONE_CHUNK
        buf = bytearray()
        for seg in range(chunks):
            buf.extend(client.client.get_block_info(_TDXHY_FILE, seg * _ONE_CHUNK, _ONE_CHUNK))
        remainder = size - chunks * _ONE_CHUNK
        if remainder > 0:
            buf.extend(client.client.get_block_info(_TDXHY_FILE, chunks * _ONE_CHUNK, remainder))

        if len(buf) != size:
            raise RuntimeError(f"tdxhy.cfg 下载不完整: expected={size} got={len(buf)}")
        return bytes(buf)

    data, source = call_tdx_quotes_with_retry(_op, action_name="fetch_tdxhy_cfg")
    return data, source


# ─────────────────────────────────────────────────────────────────────
# Parse
# ─────────────────────────────────────────────────────────────────────

def _split_tdx_code(code: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """拆分 T 码为三级。

    规则：
      空 / 'T00' → (None, None, None)   — 无分类或占位
      T + 2 位   → (l1, None, None)      — 只到一级
      T + 4 位   → (l1, l2, None)        — 到二级
      T + 6 位   → (l1, l2, l3)          — 三级完整
    """
    if not code or not code.startswith("T"):
        return (None, None, None)
    body = code[1:]
    if not body or not body.isdigit():
        return (None, None, None)
    if body == "00":
        return (None, None, None)

    n = len(body)
    if n >= 2:
        l1 = "T" + body[:2]
    else:
        l1 = None
    l2 = "T" + body[:4] if n >= 4 else None
    l3 = "T" + body[:6] if n >= 6 else None
    return (l1, l2, l3)


_ParsedRow = tuple[str, Optional[str], Optional[str], Optional[str],
                   Optional[str], Optional[str], Optional[str], Optional[str]]


def _parse_tdxhy(data: bytes) -> list[_ParsedRow]:
    """解析 tdxhy.cfg 字节流，返回
    [(stock_code, tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name, sw_x_legacy), ...]。
    """
    from backend.services.tdx_industry_names import get_tdx_industry_name

    text = data.decode("gbk", errors="ignore")
    rows: list[_ParsedRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 6:
            continue
        stock_code = parts[1].strip()
        tdx_raw = parts[2].strip()
        sw_x = parts[5].strip() or None

        if not stock_code:
            continue
        l1, l2, l3 = _split_tdx_code(tdx_raw)
        rows.append((
            stock_code,
            l1, l2, l3,
            get_tdx_industry_name(l1),
            get_tdx_industry_name(l2),
            get_tdx_industry_name(l3),
            sw_x,
        ))
    return rows


# ─────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────

def sync_tdx_industry(conn: sqlite3.Connection) -> dict:
    """拉取 tdxhy.cfg 并全量 upsert 到 dim_stock_tdx_industry。

    Returns
    -------
    dict {
        'rows_fetched':    从 tdxhy.cfg 解析出的行数,
        'rows_upserted':   实际写库行数（过滤掉 code 为空的）,
        'l1_count':        一级行业唯一值数,
        'l2_count':        二级唯一数,
        'l3_count':        三级唯一数,
        'source':          拉取来源服务器,
        'fetched_at':      下载时间,
        'errors':          list[str],
    }
    """
    _ensure_table(conn)
    result: dict = {
        "rows_fetched": 0,
        "rows_upserted": 0,
        "l1_count": 0,
        "l2_count": 0,
        "l3_count": 0,
        "source": None,
        "fetched_at": None,
        "errors": [],
    }

    try:
        data, source = _fetch_tdxhy_bytes()
    except Exception as exc:
        logger.error(f"[tdx_industry] 下载 tdxhy.cfg 失败: {exc}")
        result["errors"].append(f"fetch failed: {exc}")
        return result

    result["source"] = source
    result["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[tdx_industry] 下载 tdxhy.cfg 成功，{len(data)} bytes，来源 {source}")

    parsed = _parse_tdxhy(data)
    result["rows_fetched"] = len(parsed)

    if not parsed:
        result["errors"].append("parsed empty")
        return result

    l1_set = {r[1] for r in parsed if r[1]}
    l2_set = {r[2] for r in parsed if r[2]}
    l3_set = {r[3] for r in parsed if r[3]}
    result["l1_count"] = len(l1_set)
    result["l2_count"] = len(l2_set)
    result["l3_count"] = len(l3_set)

    upsert_sql = """
        INSERT INTO dim_stock_tdx_industry
          (stock_code, tdx_l1, tdx_l2, tdx_l3,
           tdx_l1_name, tdx_l2_name, tdx_l3_name,
           sw_x_legacy, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code) DO UPDATE SET
          tdx_l1      = excluded.tdx_l1,
          tdx_l2      = excluded.tdx_l2,
          tdx_l3      = excluded.tdx_l3,
          tdx_l1_name = excluded.tdx_l1_name,
          tdx_l2_name = excluded.tdx_l2_name,
          tdx_l3_name = excluded.tdx_l3_name,
          sw_x_legacy = excluded.sw_x_legacy,
          updated_at  = CURRENT_TIMESTAMP
    """
    conn.executemany(upsert_sql, parsed)
    conn.commit()
    result["rows_upserted"] = len(parsed)

    logger.info(
        f"[tdx_industry] upsert 完成: {len(parsed)} 行, "
        f"L1={result['l1_count']} / L2={result['l2_count']} / L3={result['l3_count']}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────
# Lookup helpers (轻量查询，供 resolver 调用)
# ─────────────────────────────────────────────────────────────────────

def get_tdx_industry(conn: sqlite3.Connection, stock_code: str) -> Optional[dict]:
    """查询单只股票的通达信行业三级代码（含中文名）。"""
    row = conn.execute(
        """SELECT tdx_l1, tdx_l2, tdx_l3,
                  tdx_l1_name, tdx_l2_name, tdx_l3_name, sw_x_legacy
             FROM dim_stock_tdx_industry WHERE stock_code=?""",
        (stock_code,),
    ).fetchone()
    if not row:
        return None
    return {
        "tdx_l1": row[0],
        "tdx_l2": row[1],
        "tdx_l3": row[2],
        "tdx_l1_name": row[3],
        "tdx_l2_name": row[4],
        "tdx_l3_name": row[5],
        "sw_x_legacy": row[6],
    }


def load_tdx_industry_map(conn: sqlite3.Connection) -> dict[str, dict]:
    """批量加载全量股票→行业映射（含中文名）。"""
    rows = conn.execute(
        """SELECT stock_code, tdx_l1, tdx_l2, tdx_l3,
                  tdx_l1_name, tdx_l2_name, tdx_l3_name, sw_x_legacy
             FROM dim_stock_tdx_industry"""
    ).fetchall()
    return {
        r[0]: {
            "tdx_l1": r[1], "tdx_l2": r[2], "tdx_l3": r[3],
            "tdx_l1_name": r[4], "tdx_l2_name": r[5], "tdx_l3_name": r[6],
            "sw_x_legacy": r[7],
        }
        for r in rows
    }
