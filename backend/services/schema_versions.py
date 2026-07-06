"""派生层 schema 版本登记 — P0.1 (2026-04-28).

为什么需要:
- chunkymonkey 有多张派生表和少量可选兼容 view.
- 升级表结构 (加列/改列/重建 view) 时容易踩"schema drift"雷:
    兼容 view 若长期存在, 底表加列后 DuckDB 可能拒绝查询旧 view.
- 没有版本号机制 → 升级 schema 后旧代码读旧 schema, 新代码读新 schema, 沉默崩.

设计:
1. SCHEMA_VERSIONS 字典 (本文件) — 代码声明每张派生表当前期望的版本号
2. dim_schema_version 表 (DuckDB) — 记录每张表实际数据是哪个版本生成的
3. init_db 启动时:
   - ensure_schema_version_table 建表
   - recreate_views 自动 DROP + CREATE 所有 view (防底表 schema drift)
   - detect_drift 报告 expected != actual 的表
4. build_xxx / rebuild_xxx 完成时调 record_actual_version 更新 actual

升级流程:
- 改表结构 → SCHEMA_VERSIONS[table] += 1 (v1 → v2)
- 启动 backend → 看到 [schema drift] WARN
- 工作台 / 系统页 看到 expected≠actual → 触发对应 rebuild
- rebuild_xxx 函数末尾 record_actual_version(conn, table) → drift 消失

注意:
- 不是 ORM, 不强制. 主要是发现机制 + 元数据.
- 没接到所有 build 函数 (70 张表代价大). 用户重算后系统页有"全部标记为最新"按钮.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("cm-api.schema_versions")


# ===========================================================================
# 派生层版本声明 (代码即真相)
# ===========================================================================

# fact_* (事件/快照层, 23 张)
# FACT_VERSIONS: 全部 26 条 fact_* 派生表在纯数据平台重建 (U3/U4/U5 + 白名单裁剪) 已物删,
#   整 dict 清空 2026-06-28 (institution_event/lhb_event/risk_factors/technical_stage/policy_*/
#   feature_panel/chain_alpha_truth/... 均退役)。edge 层重建若引入 fact 派生表再逐条登记。
FACT_VERSIONS: dict[str, str] = {}

# mart_* (集市层): 纯数据平台只留治理/编排 mart, 全部策略/特征/模型/synergy/p0-p3 mart
#   在重建 (白名单裁剪 + U2/U5) 已物删, 2026-06-28 trim 至 10 条 KEEP 治理 mart。
# 2026-07-06 再删 2 条: mart_global_data_quality_gate/detail 随孤儿 data_quality.py
#   (3742行, 零调用方) 整体退役, 唯一物化过的 mart_global_data_quality_gate(128行历史数据)
#   已 db_lifecycle_delete 归档 (data/archive/purge_processed/); mart_feature_null_policy
#   从未被物化(0行, 只在 data_quality.py 自己的 DDL 字符串里存在过)。
MART_VERSIONS = {
    "mart_data_source_failure_queue": "v1",   # 采集失败队列
    "mart_pipeline_lock": "v1",               # 单 writer 锁
    "mart_step_fingerprint": "v1",            # step 指纹 (增量驱动)
    "mart_data_processing_tool_run": "v1",    # 数据处理工具 run
    "mart_data_processing_tool_issue": "v1",  # 数据处理工具 issue
    "mart_data_deletion_record": "v1",        # 删除留痕
    "mart_data_deprecation_record": "v1",     # 退役留痕
}

# dim_* 派生类: 只留交易规则/上市状态等基础设施 dim。策略派生 dim
#   (archetype/quality/stage/turtle/industry_context/stage_days/style_factor latest) 重建已物删,
#   2026-06-28 trim 至 7 条 KEEP infra dim。
DIM_DERIVED_VERSIONS = {
    "dim_price_limit_rules": "v1",           # 涨跌停规则
    "dim_market_segment": "v1",              # 市场细分
    "dim_trading_rule": "v1",                # T+1 / 手数 / tick
    "dim_fee_schedule": "v1",                # 佣金 / 印花税
    "dim_trading_session": "v1",             # 盘口时段
    "dim_liquidity_threshold": "v1",         # 流动性阈值
    "dim_listing_status": "v1",              # 退市状态
}

# 合并: 业务派生表全集 (raw_* / dim_active_a_stock / dim_trading_calendar 等不进, 它们靠 sync_raw 维护)
SCHEMA_VERSIONS = {**FACT_VERSIONS, **MART_VERSIONS, **DIM_DERIVED_VERSIONS}


# ===========================================================================
# View: 启动时 DROP + CREATE OR REPLACE, 防底表 schema drift.
# Keep this list empty unless a current production read path still needs a
# compatibility view. Historical shims should move through architecture cleanup.
# ===========================================================================

RECREATE_VIEWS: dict[str, str] = {}


# ===========================================================================
# DB 操作
# ===========================================================================

def ensure_schema_version_table(conn, commit: bool = True) -> None:
    """幂等建 dim_schema_version 表."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_schema_version (
            table_name        TEXT PRIMARY KEY,
            expected_version  TEXT NOT NULL,
            actual_version    TEXT,
            rebuilt_at        TIMESTAMP,
            notes             TEXT
        )
    """)
    if commit:
        conn.commit()


def recreate_views(conn) -> dict[str, str]:
    """启动时 DROP + CREATE 所有声明的 view. 返回 {view_name: 'ok' | 'fail: msg'}."""
    out = {view_name: "ok" for view_name in RECREATE_VIEWS}
    if not RECREATE_VIEWS:
        conn.commit()
        return out
    script = "\n".join(
        f"DROP VIEW IF EXISTS {view_name};\nCREATE VIEW {view_name} AS {sql};"
        for view_name, sql in RECREATE_VIEWS.items()
    )
    try:
        conn.executescript(script)
    except Exception as exc:
        for view_name in RECREATE_VIEWS:
            out[view_name] = f"fail: {exc}"
        logger.warning(f"[schema_version] view batch rebuild failed: {exc}")
    conn.commit()
    return out


def record_actual_version(conn, table_name: str, version: str | None = None) -> None:
    """升级 dim_schema_version 的 actual_version + rebuilt_at.

    build_xxx / rebuild_xxx 函数末尾调用. version=None 时取 SCHEMA_VERSIONS[table_name].
    """
    expected = SCHEMA_VERSIONS.get(table_name, "v1")
    actual = version or expected
    ensure_schema_version_table(conn, commit=False)
    conn.execute("""
        INSERT INTO dim_schema_version (table_name, expected_version, actual_version, rebuilt_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(table_name) DO UPDATE SET
            expected_version = excluded.expected_version,
            actual_version   = excluded.actual_version,
            rebuilt_at       = excluded.rebuilt_at
    """, [table_name, expected, actual])


def record_all_baselines(conn) -> int:
    """首次启动: 把所有 SCHEMA_VERSIONS 写为 actual=expected (假定当前 DB 数据是 v1).

    用户重算派生层后也调用. 返回写入行数.
    """
    table_names = list(SCHEMA_VERSIONS)
    if not table_names:
        return 0
    placeholders = ", ".join("?" for _ in table_names)
    existing_rows = conn.execute(
        f"""
        SELECT table_name
          FROM information_schema.tables
         WHERE table_name IN ({placeholders})
        """,
        table_names,
    ).fetchall()
    existing = {row[0] for row in existing_rows}
    rows = [
        (table_name, expected, expected)
        for table_name, expected in SCHEMA_VERSIONS.items()
        if table_name in existing
    ]
    if rows:
        ensure_schema_version_table(conn, commit=False)
        conn.executemany(
            """
            INSERT INTO dim_schema_version (table_name, expected_version, actual_version, rebuilt_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(table_name) DO UPDATE SET
                expected_version = excluded.expected_version,
                actual_version   = excluded.actual_version,
                rebuilt_at       = excluded.rebuilt_at
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def detect_drift(conn) -> list[dict]:
    """返回所有 expected != actual 或 actual 为空的表."""
    try:
        db_rows = conn.execute("""
            SELECT table_name, expected_version, actual_version,
                   CAST(rebuilt_at AS VARCHAR) AS rebuilt_at
            FROM dim_schema_version
        """).fetchall()
    except Exception:
        # 表还没建
        return [
            {"table_name": t, "expected": v, "actual": None, "drift_type": "table_missing",
             "rebuilt_at": None}
            for t, v in SCHEMA_VERSIONS.items()
        ]

    db_state = {r[0]: {"expected": r[1], "actual": r[2], "rebuilt_at": r[3]} for r in db_rows}
    drifts = []
    for table, expected in SCHEMA_VERSIONS.items():
        st = db_state.get(table)
        if not st or not st["actual"]:
            # 没有记录: 可能是表还没建, 或者首次启动
            # 看表是否存在 — 存在则报 never_recorded, 否则跳过
            try:
                exists = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                    [table],
                ).fetchone()
            except Exception:
                exists = None
            if exists:
                drifts.append({
                    "table_name": table,
                    "expected": expected,
                    "actual": None,
                    "drift_type": "never_recorded",
                    "rebuilt_at": None,
                })
        elif st["actual"] != expected:
            drifts.append({
                "table_name": table,
                "expected": expected,
                "actual": st["actual"],
                "drift_type": "version_mismatch",
                "rebuilt_at": st["rebuilt_at"],
            })
    return drifts


def list_all_versions(conn) -> list[dict]:
    """系统页 UI 用 — 列出所有 expected 表 + 实际状态."""
    try:
        db_rows = conn.execute("""
            SELECT table_name, expected_version, actual_version,
                   CAST(rebuilt_at AS VARCHAR) AS rebuilt_at
            FROM dim_schema_version
        """).fetchall()
    except Exception:
        db_rows = []
    db_state = {r[0]: r for r in db_rows}

    out = []
    for table, expected in SCHEMA_VERSIONS.items():
        layer = (
            "fact" if table.startswith("fact_")
            else "mart" if table.startswith("mart_")
            else "dim_derived" if table.startswith("dim_")
            else "?"
        )
        st = db_state.get(table)
        actual = st[2] if st else None
        rebuilt_at = st[3] if st else None
        drift = (st is None) or (actual != expected)
        out.append({
            "table_name": table,
            "layer": layer,
            "expected_version": expected,
            "actual_version": actual,
            "rebuilt_at": rebuilt_at,
            "drift": drift,
        })
    return out


# ===========================================================================
# Public summary
# ===========================================================================

def summary() -> dict:
    return {
        "n_fact": len(FACT_VERSIONS),
        "n_mart": len(MART_VERSIONS),
        "n_dim_derived": len(DIM_DERIVED_VERSIONS),
        "n_views": len(RECREATE_VIEWS),
        "total": len(SCHEMA_VERSIONS),
    }
