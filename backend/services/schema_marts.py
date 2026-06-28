"""Mart table creation DDL."""

from __future__ import annotations

MART_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS mart_data_health (
                table_name         TEXT NOT NULL,
                snapshot_at        TIMESTAMP NOT NULL,
                row_count          BIGINT,
                last_data_date     TEXT,                      -- MAX(date_column) 实测
                last_writer_at     TIMESTAMP,                 -- 最近 writer 成功时间 (推: step_status)
                null_rate_pct      DOUBLE,                    -- 关键字段 NULL 比例
                source_tier_dist   TEXT,                      -- JSON: {1: 5179, 2: 21, 3: 0}
                freshness_hours    DOUBLE,                    -- 数据距 now() 时长
                freshness_ok       BOOLEAN,                   -- 是否在 SLA 内
                severity           TEXT NOT NULL,             -- 'green' / 'yellow' / 'red'
                issue_summary      TEXT,                      -- 红/黄时填具体原因
                PRIMARY KEY (table_name, snapshot_at)
            );

            CREATE TABLE IF NOT EXISTS mart_pipeline_run_manifest (
                run_id                TEXT PRIMARY KEY,
                pipeline_name         TEXT NOT NULL,
                status                TEXT NOT NULL,          -- success / failed / skipped / running
                started_at            TIMESTAMP,
                ended_at              TIMESTAMP,
                duration_s            DOUBLE,
                commit_sha            TEXT,
                command               TEXT,
                cwd                   TEXT,
                input_tables_json     TEXT,
                output_tables_json    TEXT,
                input_row_counts_json TEXT,
                output_row_counts_json TEXT,
                model_id              TEXT,
                feature_group         TEXT,
                label_name            TEXT,
                holding_period        INTEGER,
                gate_result           TEXT,
                blockers_json         TEXT,
                perf_summary_json     TEXT,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS mart_data_source_watermark (
                data_domain          TEXT NOT NULL,
                source_name          TEXT NOT NULL,
                source_tier          SMALLINT NOT NULL,
                last_success_at      TIMESTAMP,
                last_data_date       TEXT,
                last_raw_hash        TEXT,
                next_check_at        TIMESTAMP,
                consecutive_failures INTEGER DEFAULT 0,
                fallback_active      BOOLEAN DEFAULT FALSE,
                fallback_reason      TEXT,
                row_count            BIGINT,
                parser_version       TEXT,
                updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (data_domain, source_name, source_tier)
            );

            CREATE TABLE IF NOT EXISTS mart_lineage (
                lineage_id         TEXT PRIMARY KEY,            -- e.g. 'mart_daily_recommendation/topk_v1'
                output_table       TEXT NOT NULL,
                input_tables       TEXT,                        -- JSON 数组
                sql_text           TEXT,                        -- 完整 SQL (或脚本入口)
                sql_hash           TEXT,                        -- sha256(sql_text)[:16] — 变更检测
                version            TEXT DEFAULT 'v1',
                owner              TEXT,                        -- 模块路径或责任人
                description        TEXT,
                last_run_at        TIMESTAMP,
                last_row_count     BIGINT,
                last_status        TEXT,                        -- 'ok' / 'failed' / 'pending'
                last_error         TEXT,
                last_runtime_s     DOUBLE,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIEW IF NOT EXISTS mart_data_lineage AS
            SELECT lineage_id,
                   output_table AS mart_table,
                   output_table,
                   input_tables,
                   sql_text,
                   sql_hash,
                   version,
                   owner,
                   description,
                   last_run_at,
                   last_row_count,
                   last_status,
                   last_error,
                   last_runtime_s,
                   created_at,
                   updated_at
              FROM mart_lineage;

            CREATE TABLE IF NOT EXISTS mart_data_deprecation_record (
                record_id        TEXT PRIMARY KEY,
                table_name       TEXT NOT NULL,
                deprecation_status TEXT NOT NULL,
                replacement_table TEXT,
                reason           TEXT,
                recorded_at      TEXT NOT NULL,
                dry_run          BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS mart_data_deletion_record (
                record_id TEXT PRIMARY KEY,
                deletion_run_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                delete_scope TEXT NOT NULL,
                key_column TEXT,
                key_value TEXT,
                deleted_rows BIGINT DEFAULT 0,
                deleted_files BIGINT DEFAULT 0,
                deleted_bytes BIGINT DEFAULT 0,
                reason TEXT NOT NULL,
                verification_json TEXT,
                deleted_at TEXT NOT NULL
            );"""

MART_SCHEMA_MIGRATIONS = [
]

__all__ = ["ensure_mart_schema", "ensure_schema"]


def ensure_mart_schema(conn) -> None:
    from .schema_layer_filter import filter_schema_sql, keep_stmt
    _sql = filter_schema_sql(MART_SCHEMA_SQL)
    if hasattr(conn, "executescript"):
        conn.executescript(_sql)
    else:
        for stmt in _sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
    for stmt in MART_SCHEMA_MIGRATIONS:
        if keep_stmt(stmt):  # layer 门控: 跳过非活层表 ALTER (防引用已删表报错)
            conn.execute(stmt)


def ensure_schema(conn) -> None:
    ensure_mart_schema(conn)
