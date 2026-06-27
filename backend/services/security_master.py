"""
当前可交易 A 股主数据服务

职责：
- 拉取当前可交易 A 股代码清单
- 缓存到本地表 dim_active_a_stock (rule-compliance: ok evidence=table-writer-itself)
- 为更新链路提供统一的“有效股票宇宙”入口

设计原则：
- 历史十大流通股东 fact_top10_holder_period (替代 P7 退役的 market_raw_holdings) 只代表"曾经出现过的代码"
- 可跟踪/可同步行情的股票，必须再经过“当前有效 A 股主数据”校验
- 退市、失效、非股票代码不应进入 inst_holdings / K 线同步主链路
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Set

logger = logging.getLogger("cm-api")

ACTIVE_STOCK_MIN_ROWS = 3000

# §9 拆库 (2026-06-27): 旧 cache 簇 (_parse_iso/_load_cached_codes/_cache_is_fresh/get_active_a_stock_codes
#   + ACTIVE_STOCK_CACHE_HOURS) 已删 — get_active_a_stock_codes 0 live 调用方 (git grep 实证), 且其直读
#   dim_active_a_stock 不走 reference 路由 = Stage E 物删后会炸的 dead 代码。现读路统一走 active_codes()/
#   active_stock_name_map() (resolver.dim_read_conn auto-fallback reference)。

# dim_active_a_stock reference 表 schema (与 schema_core 定义一致; §9 后 reference 是真相源, writer 自带兜底)
_DIM_ACTIVE_DDL = """
CREATE TABLE IF NOT EXISTS dim_active_a_stock ( -- rule-compliance: ok evidence=schema-definition (table-writer-itself, reference 真相源兜底)
    stock_code       TEXT PRIMARY KEY,
    stock_name       TEXT,
    market           TEXT,
    source           TEXT,
    updated_at       TEXT
)
"""


def refresh_active_a_stock_master(conn) -> int:
    """刷新当前可交易 A 股主数据缓存 (2026-06-19: akshare → tushare stock_basic 身份真相源)。

    源 = raw_tushare_stock_basic (sync_registry stock_basic 域, list_status='L' 在市股)。
    身份真相源切换 (替旧 akshare bare码 + _market_from_code 前缀猜市场):
    - stock_code = symbol (6位, 与 K线/消费侧口径一致)
    - market 列 = ts_code 后缀 (.SH→'SH' / .SZ→'SZ', 权威交易所非前缀猜)
    - 排北交所 (market='北交所' = exchange BSE, universe 政策); 退市生存者宇宙走 PIT 历史另案
    schema/列语义不变 → 18/19 消费者零改动 (仅 ingest_holders_tdxhub.py 读 market 列, 仍 'SH'/'SZ')。
    """
    import duckdb
    from services.database_manifest import get_database_manifest

    raw_path = get_database_manifest().path_for("tushare_raw")
    rconn = duckdb.connect(str(raw_path), read_only=True)  # rule-compliance: ok evidence=只读跨库读身份真相源 raw_tushare_stock_basic (写侧=传入 smartmoney conn), 非业务阈值
    try:
        raw_rows = rconn.execute(
            """
            SELECT symbol, name, ts_code
            FROM raw_tushare_stock_basic
            WHERE market != '北交所'      -- 排北交所 (= exchange BSE), universe 政策
              AND symbol IS NOT NULL
            """  # rule-compliance: ok evidence=read-tushare-identity-truth-source
        ).fetchall()
    finally:
        rconn.close()

    if not raw_rows or len(raw_rows) < ACTIVE_STOCK_MIN_ROWS:
        raise RuntimeError(
            f"raw_tushare_stock_basic 行数不足 ({len(raw_rows)} < {ACTIVE_STOCK_MIN_ROWS}); "
            "先 sync stock_basic (services.data_sources.sync_runner --domain stock_basic)"
        )

    now = datetime.now().isoformat()
    rows = []
    for symbol, name, ts_code in raw_rows:
        code = str(symbol or "").strip().zfill(6)
        if not code.isdigit() or len(code) != 6:
            continue
        suffix = str(ts_code or "").split(".")[-1].upper()
        market = "SH" if suffix == "SH" else "SZ"   # 北交所(BJ)已 WHERE 排除
        rows.append((code, str(name or "").strip(), market, "tushare_stock_basic", now))

    # §9 reference 拆库 (2026-06-27): dim_active_a_stock 迁 reference 库。过渡期 dual-write —
    #   reference (新真相源) + smartmoney (旧副本, 供尚未迁的直读消费者), 全 reader 迁完后 smartmoney 侧物删 (Stage E)。
    _write_dim_active(conn, rows)  # smartmoney 旧副本 (过渡)
    from services.data_access import resolver
    ref = resolver.connect_rw("reference")
    try:
        _write_dim_active(ref, rows)  # reference 新真相源
    finally:
        ref.close()

    logger.info(f"[主数据] 刷新当前A股主数据(tushare stock_basic): {len(rows)} 只 (排北交所; dual-write reference+smartmoney)")
    return len(rows)


def _write_dim_active(conn, rows) -> None:
    """DELETE+INSERT 全量重写主数据表 (事务内, table-writer-itself)。§9 dual-write 复用 (reference + smartmoney)。"""
    conn.execute(_DIM_ACTIVE_DDL)  # §9: writer 自带 schema 兜底 (reference 库可能尚无表; CREATE IF NOT EXISTS 幂等)
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM dim_active_a_stock")  # rule-compliance: ok evidence=table-writer-itself
        conn.executemany(
            """
            INSERT INTO dim_active_a_stock -- rule-compliance: ok evidence=table-writer-itself
            (stock_code, stock_name, market, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _dim_read_conn(conn):
    """§9 dim 读路由 (主数据表); 委托 resolver.dim_read_conn 通用实现。"""
    from services.data_access import resolver
    return resolver.dim_read_conn(conn, "dim_active_a_stock")  # rule-compliance: ok evidence=dim-read-router (非universe取数, 路由探测)


def active_codes(conn=None) -> Set[str]:
    """active A 股 code 集 (§9 拆库 dim 真相源; 读路由 _dim_read_conn auto-fallback)。

    replace 散落主数据表 code 直读 (universe identity 交集等) 为单一读路。
    """
    c, own = _dim_read_conn(conn)
    try:
        rows = c.execute(
            "SELECT stock_code FROM dim_active_a_stock WHERE stock_code IS NOT NULL"  # rule-compliance: ok evidence=identity-truth-source
        ).fetchall()
    finally:
        if own:
            c.close()
    return {str(r[0]).strip() for r in rows if r[0]}


def active_stock_name_map(codes=None, conn=None) -> Dict[str, str]:
    """code→name 映射 (§9 拆库 dim 真相源)。读路由 _dim_read_conn (conn 有表用它/否则 reference)。

    codes=None → 全量; 否则按 codes 过滤。replace 散落主数据表 name-lookup
    (recommendation/screening/...) 为单一读路 (不变量#2: Stage E 后 reader 落 reference 不撞 smartmoney 写锁)。
    """
    c, own = _dim_read_conn(conn)
    try:
        if codes:
            rows = c.execute(
                "SELECT stock_code, stock_name FROM dim_active_a_stock WHERE stock_code = ANY(?)",  # rule-compliance: ok evidence=code-to-name-mapping
                (list(codes),),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT stock_code, stock_name FROM dim_active_a_stock WHERE stock_code IS NOT NULL"  # rule-compliance: ok evidence=code-to-name-mapping
            ).fetchall()
    finally:
        if own:
            c.close()
    return {str(r[0]): r[1] for r in rows}
