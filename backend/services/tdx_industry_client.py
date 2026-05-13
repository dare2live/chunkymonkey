"""
tdx_industry_client.py — 通达信行业分类同步（已彻底取代申万 SW 三级分类，Phase η++ 2026-05-12 清理）

数据源：TDX 行情协议远程文件 `tdxhy.cfg`
  - 文件大小 ~150KB，5604 只股票
  - 每行 6 字段：市场|代码|通达信 T 码|||(忽略第6列旧申万码)
  - **每只股票唯一对应一个 T 码**（唯一性已实测验证）
  - T 码三级结构：
      T + 2 位 → 一级（13 个）
      T + 4 位 → 二级（56 个）
      T + 6 位 → 三级（76 个）

拉取方式：
  走 `call_tdx_quotes_with_retry` 自动重试 117 台服务器。
  tdxhub 的 `.block()` 只支持 block_*.dat，拉不到 .cfg，
  必须直接调底层 `client.get_block_info_meta()` + `client.get_block_info()`。

表：dim_stock_tdx_industry（PK: stock_code）
  stock_code   股票代码
  tdx_l1       一级代码（T + 2 位）
  tdx_l2       二级代码（T + 4 位）
  tdx_l3       三级代码（T + 6 位）
  tdx_l1_name  一级中文名
  tdx_l2_name  二级中文名
  tdx_l3_name  三级中文名
  updated_at   TIMESTAMP
"""

from __future__ import annotations

import logging
import hashlib
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger("cm-api")

_TDXHY_FILE = "tdxhy.cfg"
_ONE_CHUNK = 0x7530  # 30000 bytes per chunk (TDX protocol limit)


# ─────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────

def _ensure_table(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dim_stock_tdx_industry (
            stock_code    TEXT PRIMARY KEY,
            tdx_l1        TEXT,
            tdx_l2        TEXT,
            tdx_l3        TEXT,
            tdx_l1_name   TEXT,
            tdx_l2_name   TEXT,
            tdx_l3_name   TEXT,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l1 ON dim_stock_tdx_industry(tdx_l1);
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l2 ON dim_stock_tdx_industry(tdx_l2);
        CREATE INDEX IF NOT EXISTS idx_tdx_industry_l3 ON dim_stock_tdx_industry(tdx_l3);

        CREATE TABLE IF NOT EXISTS raw_tdx_industry_file_snapshot (
            snapshot_date TEXT NOT NULL,
            raw_hash TEXT NOT NULL,
            file_name TEXT NOT NULL,
            source_label TEXT,
            fetched_at TIMESTAMP NOT NULL,
            bytes_len INTEGER NOT NULL,
            raw_bytes BLOB NOT NULL,
            parser_version TEXT NOT NULL DEFAULT 'tdxhy_v1',
            PRIMARY KEY (snapshot_date, raw_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_raw_tdx_industry_snapshot_date
            ON raw_tdx_industry_file_snapshot(snapshot_date);
    """)
    existing = {
        row["column_name"] if hasattr(row, "keys") else row[0]
        for row in conn.execute("DESCRIBE dim_stock_tdx_industry").fetchall()
    }
    for col in ("tdx_l1_name", "tdx_l2_name", "tdx_l3_name"):
        if col not in existing:
            conn.execute(f"ALTER TABLE dim_stock_tdx_industry ADD COLUMN {col} TEXT")
            logger.info(f"[tdx_industry] ALTER TABLE: 新增列 {col}")
    conn.commit()


def _record_raw_tdxhy_snapshot(
    conn: Any,
    *,
    data: bytes,
    source: str,
    fetched_at: datetime,
    snapshot_date: str,
) -> str:
    raw_hash = hashlib.sha256(data).hexdigest()
    conn.execute(
        """
        INSERT OR REPLACE INTO raw_tdx_industry_file_snapshot (
            snapshot_date, raw_hash, file_name, source_label,
            fetched_at, bytes_len, raw_bytes, parser_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'tdxhy_v1')
        """,
        (snapshot_date, raw_hash, _TDXHY_FILE, source, fetched_at, len(data), data),
    )
    return raw_hash


# ─────────────────────────────────────────────────────────────────────
# Fetch tdxhy.cfg via tdxhub
# ─────────────────────────────────────────────────────────────────────

def _fetch_tdxhy_bytes() -> tuple[bytes, str]:
    """通过 tdxhub 117 台服务器重试下载 tdxhy.cfg。返回 (bytes, source_label)。"""
    from services.tdx_source import call_tdx_quotes_with_retry

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
                   Optional[str], Optional[str], Optional[str]]


def _parse_tdxhy(data: bytes) -> list[_ParsedRow]:
    """解析 tdxhy.cfg 字节流，返回
    [(stock_code, tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name), ...]。

    注: tdxhy.cfg 第 6 列原为申万 X 码; Phase η++ 已废弃, 仅解析 TDX 三级。
    """
    from services.tdx_industry_names import get_tdx_industry_name

    text = data.decode("gbk", errors="ignore")
    rows: list[_ParsedRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        stock_code = parts[1].strip()
        tdx_raw = parts[2].strip()

        if not stock_code:
            continue
        l1, l2, l3 = _split_tdx_code(tdx_raw)
        rows.append((
            stock_code,
            l1, l2, l3,
            get_tdx_industry_name(l1),
            get_tdx_industry_name(l2),
            get_tdx_industry_name(l3),
        ))
    return rows


# ─────────────────────────────────────────────────────────────────────
# Sync
# ─────────────────────────────────────────────────────────────────────

def sync_tdx_industry(conn: Any) -> dict:
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

    fetched_at = datetime.now()
    snapshot_date = fetched_at.strftime("%Y-%m-%d")
    result["source"] = source
    result["fetched_at"] = fetched_at.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[tdx_industry] 下载 tdxhy.cfg 成功，{len(data)} bytes，来源 {source}")
    raw_hash = _record_raw_tdxhy_snapshot(
        conn,
        data=data,
        source=source,
        fetched_at=fetched_at,
        snapshot_date=snapshot_date,
    )
    result["raw_hash"] = raw_hash

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

    # DuckDB: ON CONFLICT DO UPDATE SET 内 CURRENT_TIMESTAMP 会被当作列名 binder
    # 错误, 改为 INSERT OR REPLACE + now() 函数避开.
    upsert_sql = """
        INSERT OR REPLACE INTO dim_stock_tdx_industry
          (stock_code, tdx_l1, tdx_l2, tdx_l3,
           tdx_l1_name, tdx_l2_name, tdx_l3_name,
           updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    now_ts = datetime.now()
    parsed_with_ts = [tuple(row) + (now_ts,) for row in parsed]
    conn.executemany(upsert_sql, parsed_with_ts)

    # 审计 4.4 整改 (Phase V)：每次同步追加一份"当日行业快照"到 history 表，
    # 以便未来 event-time 行业归因可以 JOIN 到事件发生时的行业分类，
    # 而不是总用最新 dim_stock_tdx_industry（会让历史事件被重分类到新行业）。
    # 历史事件在积累足够快照前，仍回退到当前行业（UI 已标注"当前行业口径"）。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_stock_tdx_industry_history (
            stock_code    TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            tdx_l1        TEXT,
            tdx_l2        TEXT,
            tdx_l3        TEXT,
            tdx_l1_name   TEXT,
            tdx_l2_name   TEXT,
            tdx_l3_name   TEXT,
            source_raw_hash TEXT,
            source_label TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (stock_code, snapshot_date)
        )
        """
    )
    existing_history = {
        row["column_name"] if hasattr(row, "keys") else row[0]
        for row in conn.execute("DESCRIBE dim_stock_tdx_industry_history").fetchall()
    }
    for col, ddl_type in (
        ("source_raw_hash", "TEXT"),
        ("source_label", "TEXT"),
        ("fetched_at", "TIMESTAMP"),
    ):
        if col not in existing_history:
            conn.execute(f"ALTER TABLE dim_stock_tdx_industry_history ADD COLUMN {col} {ddl_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tdx_ind_hist_stock ON dim_stock_tdx_industry_history(stock_code)"
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO dim_stock_tdx_industry_history
            (stock_code, snapshot_date, tdx_l1, tdx_l2, tdx_l3,
             tdx_l1_name, tdx_l2_name, tdx_l3_name,
             source_raw_hash, source_label, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r[0],
                snapshot_date,
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                raw_hash,
                source,
                fetched_at,
            )
            for r in parsed
        ],
    )

    conn.commit()
    result["rows_upserted"] = len(parsed)
    result["history_snapshot_date"] = snapshot_date

    logger.info(
        f"[tdx_industry] upsert 完成: {len(parsed)} 行, "
        f"L1={result['l1_count']} / L2={result['l2_count']} / L3={result['l3_count']}, "
        f"history snapshot {snapshot_date}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────
# Lookup helpers (轻量查询，供 resolver 调用)
# ─────────────────────────────────────────────────────────────────────

def get_tdx_industry_at(
    conn: Any, stock_code: str, event_date: str
) -> Optional[dict]:
    """审计 4.4 / Phase V：返回 ``event_date`` 时点该股所属的 TDX 行业（event-time 口径）。

    优先查 dim_stock_tdx_industry_history 中 ≤ event_date 的最新快照；
    若 history 为空或该股无历史快照，回退到 dim_stock_tdx_industry 的当前行业。
    """
    try:
        row = conn.execute(
            """
            SELECT tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name
            FROM dim_stock_tdx_industry_history
            WHERE stock_code = ? AND snapshot_date <= ?
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            (stock_code, event_date),
        ).fetchone()
    except Exception:
        row = None
    if row:
        return {
            "tdx_l1": row[0], "tdx_l2": row[1], "tdx_l3": row[2],
            "tdx_l1_name": row[3], "tdx_l2_name": row[4], "tdx_l3_name": row[5],
            "source": "event_time_snapshot",
        }
    # 回退：当前行业
    return get_tdx_industry(conn, stock_code)


def get_tdx_industry(conn: Any, stock_code: str) -> Optional[dict]:
    """查询单只股票的通达信行业三级代码（含中文名）。"""
    row = conn.execute(
        """SELECT tdx_l1, tdx_l2, tdx_l3,
                  tdx_l1_name, tdx_l2_name, tdx_l3_name
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
    }


def load_tdx_industry_map(conn: Any) -> dict[str, dict]:
    """批量加载全量股票→行业映射（含中文名）。"""
    rows = conn.execute(
        """SELECT stock_code, tdx_l1, tdx_l2, tdx_l3,
                  tdx_l1_name, tdx_l2_name, tdx_l3_name
             FROM dim_stock_tdx_industry"""
    ).fetchall()
    return {
        r[0]: {
            "tdx_l1": r[1], "tdx_l2": r[2], "tdx_l3": r[3],
            "tdx_l1_name": r[4], "tdx_l2_name": r[5], "tdx_l3_name": r[6],
        }
        for r in rows
    }
