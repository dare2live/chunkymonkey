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
from datetime import datetime, timedelta
from typing import Dict, Set

logger = logging.getLogger("cm-api")

ACTIVE_STOCK_CACHE_HOURS = 24
ACTIVE_STOCK_MIN_ROWS = 3000


def _parse_iso(ts: str):
    if not ts:
        return None
    text = str(ts).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _load_cached_codes(conn) -> Set[str]:
    rows = conn.execute(
        "SELECT stock_code FROM dim_active_a_stock WHERE stock_code IS NOT NULL"  # rule-compliance: ok evidence=table-writer-itself
    ).fetchall()
    return {str(r["stock_code"]).strip() for r in rows if r["stock_code"]}


def _cache_is_fresh(conn, max_age_hours: int = ACTIVE_STOCK_CACHE_HOURS) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt, MAX(updated_at) AS latest FROM dim_active_a_stock"  # rule-compliance: ok evidence=table-writer-itself
    ).fetchone()
    if not row or (row["cnt"] or 0) < ACTIVE_STOCK_MIN_ROWS or not row["latest"]:
        return False
    latest = _parse_iso(row["latest"])
    if latest is None:
        return False
    return latest >= datetime.now() - timedelta(hours=max_age_hours)


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
    """§9 dim 读路由: conn 有主数据表 (测试 fixture / 过渡期 smartmoney dual 副本) → 用它;
    否则开 reference RO (Stage E 物删 smartmoney 副本后, 全 reader 原子 fall 到 reference)。返 (conn, own_flag)。"""
    if conn is not None:
        try:
            has = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='dim_active_a_stock' LIMIT 1"  # rule-compliance: ok evidence=table-existence-probe (dim 读路由探测, 非 universe 取数)
            ).fetchone()
        except Exception:
            has = None
        if has:
            return conn, False
    from services.data_access import resolver
    return resolver.connect_ro("reference"), True


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


def get_active_a_stock_codes(conn, max_age_hours: int = ACTIVE_STOCK_CACHE_HOURS) -> Set[str]:
    """返回当前可交易 A 股代码集合；优先用缓存，必要时刷新。"""
    if _cache_is_fresh(conn, max_age_hours=max_age_hours):
        return _load_cached_codes(conn)

    cached = _load_cached_codes(conn)
    try:
        refresh_active_a_stock_master(conn)
        return _load_cached_codes(conn)
    except Exception as e:
        if cached:
            logger.warning(f"[主数据] 刷新当前A股主数据失败，回退旧缓存: {e}")
            return cached
        raise
