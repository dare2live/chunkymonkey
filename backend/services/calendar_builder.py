"""calendar_builder — raw_tushare_trade_cal → reference.dim_trading_calendar 增量传导.

R1 根因 3 修复 (owner=analysis/data_foundation_root_causes_20260703.md):
dim_trading_calendar 唯一 writer 曾是一次性迁移脚本 migrate_reference_db.py (其源表
sm.dim_trading_calendar 已在 §9 Stage E 物删 = 破坏性死路径, 已封存); raw 侧 trade_cal 域
full_refresh 日刷正常 (max=20261231) 但零传导 → 日历 horizon 倒计时 (审计 2026-07-03 时点剩
123 交易日, 耗尽后 sync end_d 钉死 20261231 = 静默停摆非响断)。本模块 = dim 的生产刷新契约:
pipeline acquire 在 sync_registry drain (trade_cal 增量) 之后每日调 build_latest。

语义裁决 (2026-07-03, 消费方实测 rg is_trading 全仓):
- dim 现表 5343 行全 is_trading=1 (只存交易日)。全部生产消费方 (services/calendar.py /
  sync_runner._trading_days / technical_states / rally_gt / data_audit /
  data_health_snapshot / check_continuity_integrity.check_calendar_horizon) 均
  WHERE is_trading=1 (或 truthy) 过滤, 零路径读非交易日行
  → 保持"只存交易日"语义: 只插 raw is_open=1 行, is_trading 恒 1。
- 增量 = watermark 语义 (ISO trade_date > MAX(dim.trade_date)), 非 NOT IN 全集
  (CLAUDE §4.5 2026-07-02 反例: NOT IN 噪音; 且 raw 史回溯 19901219 而 dim 契约起点
  2005-01-04 — NOT IN 会静默回填 1990-2004 改变 dim 覆盖范围)。中段历史空洞 (理论不应有,
  日历只向未来延伸) 走人工 rebuild, 不在增量语义内。dim 空表 (重建) → 全量拷贝 raw。
- 只取 exchange='SSE' (raw 为多交易所行 SSE/SZSE/...; A 股统一上交所日历口径,
  与 dim 现存 5343 行 = 迁移前 smartmoney 口径一致)。
- 格式转换: raw cal_date compact YYYYMMDD (VARCHAR) → dim trade_date ISO YYYY-MM-DD
  (VARCHAR, PRIMARY KEY); strptime 顺带校验日期合法性。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

RAW_TABLE = "raw_tushare_trade_cal"
DIM_TABLE = "dim_trading_calendar"

# dim 重建 DDL (与 reference 现表实测 schema 一致: trade_date VARCHAR PK + is_trading BIGINT);
# 正常路径表已在, 仅 bootstrap (库重建) 时触发。
_DIM_DDL = (
    f"CREATE TABLE IF NOT EXISTS {DIM_TABLE} "
    "(trade_date VARCHAR NOT NULL, is_trading BIGINT, PRIMARY KEY(trade_date))"
)

# raw → ISO 交易日行 (SSE + is_open=1): TRY_CAST 双层兼容 is_open 落库为 VARCHAR/BIGINT/DOUBLE
_RAW_TRADING_ISO = (
    "SELECT DISTINCT strftime(strptime(CAST(cal_date AS VARCHAR), '%Y%m%d'), '%Y-%m-%d') AS trade_date "
    "FROM {raw} WHERE exchange = 'SSE' AND TRY_CAST(TRY_CAST(is_open AS DOUBLE) AS INTEGER) = 1"
)


def _db(alias: str) -> str:
    from services.database_manifest import get_database_manifest

    return str(get_database_manifest().path_for(alias))


def _raw_rel(con) -> str:
    """raw 表定位: conn 自带 (测试 fixture) 用之; 否则 ATTACH tushare_raw 只读 (生产路径,
    与 market_pulse/technical_states 同模式)。"""
    has = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1", [RAW_TABLE]
    ).fetchone()
    if has:
        return RAW_TABLE
    con.execute(f"ATTACH IF NOT EXISTS '{_db('tushare_raw')}' AS tr (READ_ONLY)")
    return f"tr.{RAW_TABLE}"


def build_latest(conn=None) -> dict[str, Any]:
    """raw_tushare_trade_cal → dim_trading_calendar 增量 MERGE (幂等; 无新日 = no-op).

    conn=None → 自管 reference RW 连接 + ATTACH tushare_raw RO; 注入 conn (测试) 时
    调用方保证两表可解析。raw 缺失/空 → raise (fail loud, 拒绝静默 no-op 假装刷新)。
    返回 {inserted, watermark_before, dim_max, dim_rows, raw_max_trading}。
    """
    own = conn is None
    if own:
        from services.data_access import resolver

        con = resolver.connect_rw("reference")
    else:
        con = conn
    try:
        raw_rel = _raw_rel(con)
        raw_src = _RAW_TRADING_ISO.format(raw=raw_rel)
        raw_row = con.execute(
            f"SELECT COUNT(*), MAX(trade_date) FROM ({raw_src})"
        ).fetchone()
        if not raw_row or not raw_row[0]:
            raise RuntimeError(
                f"{RAW_TABLE} 无 SSE is_open=1 行 — trade_cal sync 未跑或表缺失; "
                "拒绝静默 no-op (dim 会停止延伸, horizon 门会 FAIL)"
            )
        con.execute(_DIM_DDL)
        wm = con.execute(
            f"SELECT COALESCE(MAX(CAST(trade_date AS VARCHAR)), '') FROM {DIM_TABLE}"
        ).fetchone()[0]
        r = con.execute(
            f"INSERT INTO {DIM_TABLE} (trade_date, is_trading) "
            f"SELECT trade_date, 1 AS is_trading FROM ({raw_src}) "
            "WHERE trade_date > ? ORDER BY 1",
            [wm],
        ).fetchone()
        inserted = int(r[0]) if r else 0
        dim_max, dim_rows = con.execute(
            f"SELECT MAX(trade_date), COUNT(*) FROM {DIM_TABLE}"
        ).fetchone()
        if own:
            con.commit()
        out = {
            "inserted": inserted,
            "watermark_before": wm or None,
            "dim_max": dim_max,
            "dim_rows": int(dim_rows),
            "raw_max_trading": raw_row[1],
        }
        logger.info("[calendar_builder] build_latest: %s", out)
        return out
    finally:
        if own:
            con.close()


__all__ = ["build_latest", "RAW_TABLE", "DIM_TABLE"]
