"""
financial_client.py — 财务数据同步与计算

当前财务底座分为两层：
1. tdxhub finance() 提供最新一期稳定快照
2. AKShare/Sina 财报接口提供历史报表序列

数据流：
    tdxhub + akshare/sina
        -> raw_gpcw_financial
        -> fact_financial_derived
        -> dim_financial_latest

单点计算原则：
所有财务指标和历史同比逻辑只在本模块计算，其他模块只读取结果表。
"""

import asyncio
import copy
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional

from services.tdx_source import call_tdx_quotes_with_retry, iter_tdx_servers

logger = logging.getLogger("cm-api")

FIN_HISTORY_TARGET_ROWS = 8
FIN_HISTORY_FETCH_LIMIT = 12
FIN_HISTORY_BATCH_SIZE = 24
FIN_HISTORY_RETRY_COOLDOWN_HOURS = 6
FIN_HISTORY_SOURCE_RETRY_ATTEMPTS = 3
FIN_HISTORY_SOURCE_RETRY_BASE_DELAY_SECONDS = 0.75
FIN_SNAPSHOT_BATCH_SIZE = 50
FIN_SNAPSHOT_RECENT_HOURS = 24
FIN_SNAPSHOT_PROGRESS_EVERY = 250
FIN_SNAPSHOT_BATCH_CONCURRENCY = max(4, min(12, max(1, len(iter_tdx_servers())) * 2))

_FIN_SNAPSHOT_EXECUTOR = ThreadPoolExecutor(
    max_workers=FIN_SNAPSHOT_BATCH_CONCURRENCY,
    thread_name_prefix="financial-snapshot",
)

RAW_FINANCIAL_COLUMNS = [
    "stock_code",
    "report_date",
    "notice_date",
    "report_type",
    "is_audited",
    "total_assets",
    "total_liabilities",
    "net_assets",
    "current_assets",
    "current_liabilities",
    "revenue",
    "operating_profit",
    "net_profit",
    "operating_cashflow",
    "total_shares",
    "float_shares",
    "holder_count",
    "contract_liabilities",
    "eps",
    "nav_per_share",
    "gross_profit",
    "inventory",
    "undistributed_profit",
    "source_file",
    "ingested_at",
]


def _table_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(f"DESCRIBE {table_name}").fetchall()
    return {row["column_name"] if hasattr(row, "keys") else row[0] for row in rows}


def _ensure_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table_name)
    for col, ddl in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {ddl}")


# ============================================================
# Schema
# ============================================================

def ensure_tables(conn):
    """创建财务数据相关表，并补齐增量演进字段。"""
    conn.executescript("""
        -- 原始层：最新快照 + 历史报表整合后的关键字段（只追加）
        CREATE TABLE IF NOT EXISTS raw_gpcw_financial (
            stock_code           TEXT NOT NULL,
            report_date          TEXT NOT NULL,
            total_assets         REAL,
            total_liabilities    REAL,
            net_assets           REAL,
            current_assets       REAL,
            current_liabilities  REAL,
            revenue              REAL,
            operating_profit     REAL,
            net_profit           REAL,
            operating_cashflow   REAL,
            total_shares         REAL,
            float_shares         REAL,
            holder_count         INTEGER,
            contract_liabilities REAL,
            eps                  REAL,
            nav_per_share        REAL,
            gross_profit         REAL,
            inventory            REAL,
            undistributed_profit REAL,
            source_file          TEXT,
            ingested_at          TEXT,
            PRIMARY KEY (stock_code, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_rgf_report ON raw_gpcw_financial(report_date);
        CREATE INDEX IF NOT EXISTS idx_rgf_stock_report ON raw_gpcw_financial(stock_code, report_date);

        -- 事实层：派生财务指标（可重算）
        CREATE TABLE IF NOT EXISTS fact_financial_derived (
            stock_code              TEXT NOT NULL,
            report_date             TEXT NOT NULL,
            report_season           TEXT,
            roe                     REAL,
            debt_ratio              REAL,
            current_ratio           REAL,
            gross_margin            REAL,
            net_margin              REAL,
            revenue_yoy             REAL,
            profit_yoy              REAL,
            ocf_to_profit           REAL,
            contract_to_revenue     REAL,
            holder_count_change_pct REAL,
            float_shares            REAL,
            total_shares            REAL,
            updated_at              TEXT,
            PRIMARY KEY (stock_code, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_ffd_report ON fact_financial_derived(report_date);

        -- 维度层：每只股票最新财务快照
        CREATE TABLE IF NOT EXISTS dim_financial_latest (
            stock_code              TEXT PRIMARY KEY,
            latest_report_date      TEXT,
            roe                     REAL,
            debt_ratio              REAL,
            current_ratio           REAL,
            gross_margin            REAL,
            revenue_yoy             REAL,
            profit_yoy              REAL,
            ocf_to_profit           REAL,
            contract_to_revenue     REAL,
            holder_count            INTEGER,
            holder_count_change_pct REAL,
            float_shares            REAL,
            total_shares            REAL,
            updated_at              TEXT
        );

        -- 系统层：财务同步状态
        CREATE TABLE IF NOT EXISTS financial_sync_state (
            stock_code        TEXT PRIMARY KEY,
            history_rows      INTEGER DEFAULT 0,
            last_report_date  TEXT,
            last_snapshot_at  TEXT,
            last_history_at   TEXT,
            history_status    TEXT,
            history_error     TEXT,
            snapshot_status   TEXT,
            snapshot_error    TEXT,
            status            TEXT DEFAULT 'pending',
            error             TEXT,
            updated_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fss_status ON financial_sync_state(status);
    """)

    _ensure_columns(conn, "raw_gpcw_financial", {
        "notice_date": "TEXT",
        "report_type": "TEXT",
        "is_audited": "INTEGER",
    })
    _ensure_columns(conn, "dim_financial_latest", {
        "net_margin": "REAL",
        "history_rows": "INTEGER DEFAULT 0",
    })
    _ensure_columns(conn, "financial_sync_state", {
        "history_status": "TEXT",
        "history_error": "TEXT",
        "snapshot_status": "TEXT",
        "snapshot_error": "TEXT",
    })
    _bootstrap_financial_sync_state_phase_columns(conn)
    conn.commit()


# ============================================================
# 基础解析
# ============================================================

def _parse_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return None if value != value else float(value)
        except Exception:
            return None

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"} or text in {"--", "-", "不适用"}:
        return None

    text = text.replace(",", "").replace("%", "").replace("元", "").replace("股", "")
    text = text.replace("万元", "").replace("亿元", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except Exception:
        return None


def _parse_int(value):
    parsed = _parse_float(value)
    return int(parsed) if parsed is not None else None


def _normalize_date(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"} or text in {"--", "-"}:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        digits = digits[:8]
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(text) == 10 and "-" in text:
        return text
    return None


def _parse_audited(value) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if text in {"是", "已审计", "审计"}:
        return 1
    if text in {"否", "未审计"}:
        return 0
    return None


def _infer_report_date_from_notice_date(notice_date: Optional[str]) -> Optional[str]:
    notice = _normalize_date(notice_date)
    if not notice:
        return None
    try:
        dt = datetime.strptime(notice, "%Y-%m-%d")
    except ValueError:
        return None

    month = dt.month
    day = dt.day
    quarter_ends = {"03-31", "06-30", "09-30", "12-31"}
    if notice[5:] in quarter_ends:
        return notice
    if month <= 3:
        return f"{dt.year - 1}-12-31"
    if month == 4:
        return f"{dt.year - 1}-12-31" if day <= 20 else f"{dt.year}-03-31"
    if month in {5, 6}:
        return f"{dt.year}-03-31" if month == 5 else f"{dt.year}-06-30"
    if month in {7, 8}:
        return f"{dt.year}-06-30"
    if month in {9, 10}:
        return f"{dt.year}-09-30"
    return f"{dt.year}-09-30"


def _pick_value(row: dict, keys: Iterable[str]):
    for key in keys:
        if key in row:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in {"--", "-", "nan", "None", "null"}:
                return value
    return None


def _normalize_stock_code(code: str) -> str:
    return str(code or "").strip()


def _to_sina_symbol(stock_code: str) -> Optional[str]:
    code = _normalize_stock_code(stock_code)
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    return None


def _report_season(report_date: str) -> str:
    """从报告日期推断季度 (Q1/Q2/Q3/Q4)"""
    if not report_date:
        return ""
    month = report_date[5:7] if len(report_date) >= 7 else ""
    return {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(month, "")


def _safe_div(a, b):
    """安全除法，避免除零"""
    if b is None or b == 0:
        return None
    if a is None:
        return None
    return a / b


# ============================================================
# tdxhub 最新快照
# ============================================================

def _parse_finance_record(fin_row: dict) -> dict:
    """从 tdxhub finance_records() 的单行结果提取关键字段。"""
    return {
        "total_assets": _parse_float(fin_row.get("zongzichan")),
        "total_liabilities": (_parse_float(fin_row.get("liudongfuzhai")) or 0) + (_parse_float(fin_row.get("changqifuzhai")) or 0),
        "net_assets": _parse_float(fin_row.get("jingzichan")),
        "current_assets": _parse_float(fin_row.get("liudongzichan")),
        "current_liabilities": _parse_float(fin_row.get("liudongfuzhai")),
        "revenue": _parse_float(fin_row.get("zhuyingshouru")),
        "operating_profit": _parse_float(fin_row.get("yingyelirun")),
        "net_profit": _parse_float(fin_row.get("jinglirun")),
        "operating_cashflow": _parse_float(fin_row.get("jingyingxianjinliu")),
        "total_shares": _parse_float(fin_row.get("zongguben")),
        "float_shares": _parse_float(fin_row.get("liutongguben")),
        "holder_count": _parse_int(fin_row.get("gudongrenshu")),
        "eps": _parse_float(fin_row.get("meigushouyi")) or _parse_float(fin_row.get("meigujingzichan")),
        "nav_per_share": _parse_float(fin_row.get("meigujingzichan")),
        "inventory": _parse_float(fin_row.get("cunhuo")),
        "undistributed_profit": _parse_float(fin_row.get("weifenpeilirun")),
        "gross_profit": _parse_float(fin_row.get("zhuyinglirun")),
    }


def _upsert_raw_financial(conn, record: dict) -> None:
    placeholders = ",".join("?" for _ in RAW_FINANCIAL_COLUMNS)
    update_cols = [col for col in RAW_FINANCIAL_COLUMNS if col not in {"stock_code", "report_date"}]
    update_clause = ", ".join(
        f"{col} = COALESCE(excluded.{col}, raw_gpcw_financial.{col})"
        for col in update_cols
    )
    conn.execute(
        f"""
        INSERT INTO raw_gpcw_financial ({",".join(RAW_FINANCIAL_COLUMNS)})
        VALUES ({placeholders})
        ON CONFLICT(stock_code, report_date) DO UPDATE SET
            {update_clause}
        """,
        tuple(record.get(col) for col in RAW_FINANCIAL_COLUMNS),
    )


def _update_snapshot_state(conn, stock_codes: Iterable[str], snapshot_at: str) -> None:
    for code in stock_codes:
        _upsert_snapshot_state(conn, code, updated_at=snapshot_at, snapshot_at=snapshot_at, status="ok")


def _upsert_snapshot_state(
    conn,
    stock_code: str,
    updated_at: str,
    *,
    snapshot_at: Optional[str] = None,
    status: str,
    error: Optional[str] = None,
) -> None:
    code = _normalize_stock_code(stock_code)
    if not code:
        return

    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt, MAX(report_date) AS latest_report
        FROM raw_gpcw_financial
        WHERE stock_code = ?
        """,
        (code,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO financial_sync_state
        (stock_code, history_rows, last_report_date, last_snapshot_at,
         snapshot_status, snapshot_error, status, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_code) DO UPDATE SET
            history_rows = excluded.history_rows,
            last_report_date = COALESCE(excluded.last_report_date, financial_sync_state.last_report_date),
            last_snapshot_at = COALESCE(excluded.last_snapshot_at, financial_sync_state.last_snapshot_at),
            snapshot_status = excluded.snapshot_status,
            snapshot_error = excluded.snapshot_error,
            status = excluded.status,
            error = excluded.error,
            updated_at = excluded.updated_at
        """,
        (
            code,
            row["cnt"] if row else 0,
            row["latest_report"] if row else None,
            snapshot_at,
            status,
            error,
            status,
            error,
            updated_at,
        ),
    )


def _parse_sync_timestamp(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    for parser in (
        lambda current: datetime.fromisoformat(current),
        lambda current: datetime.strptime(current, "%Y-%m-%d %H:%M:%S"),
        lambda current: datetime.strptime(current, "%Y-%m-%d %H:%M:%S.%f"),
    ):
        try:
            parsed = parser(normalized)
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def _normalize_history_stage_status(status: Optional[str], history_rows: int) -> str:
    current = str(status or "").strip()
    if current == "ok" and history_rows < FIN_HISTORY_TARGET_ROWS:
        return "partial"
    if current:
        return current
    return "ok" if history_rows >= FIN_HISTORY_TARGET_ROWS else "partial"


def _state_field_value(state, field: str):
    if state is None:
        return None
    if isinstance(state, dict):
        return state.get(field)
    if hasattr(state, "keys"):
        try:
            if field in state.keys():
                return state[field]
        except Exception:
            return None
    return None


def _snapshot_stage_status(state) -> str:
    return str(
        _state_field_value(state, "snapshot_status")
        or _state_field_value(state, "status")
        or ""
    ).strip()


def _history_stage_status(state, history_rows: int) -> str:
    return _normalize_history_stage_status(
        _state_field_value(state, "history_status") or _state_field_value(state, "status"),
        history_rows,
    )


def _bootstrap_financial_sync_state_phase_columns(conn) -> None:
    try:
        rows = conn.execute(
            """
            SELECT stock_code, history_rows, last_history_at, last_snapshot_at,
                   status, error, history_status, history_error,
                   snapshot_status, snapshot_error
            FROM financial_sync_state
            """
        ).fetchall()
    except Exception:
        return

    updates = []
    for row in rows:
        history_rows = int(row["history_rows"] or 0)
        history_status = row["history_status"]
        history_error = row["history_error"]
        snapshot_status = row["snapshot_status"]
        snapshot_error = row["snapshot_error"]

        if history_status is None and row["last_history_at"]:
            history_status = _normalize_history_stage_status(row["status"], history_rows)
            if history_error is None and str(row["status"] or "").strip() in {"failed", "empty", "partial"}:
                history_error = row["error"]

        if snapshot_status is None and row["last_snapshot_at"]:
            snapshot_status = str(row["status"] or "").strip() or "ok"
            if snapshot_error is None:
                snapshot_error = row["error"]

        if (
            history_status != row["history_status"]
            or history_error != row["history_error"]
            or snapshot_status != row["snapshot_status"]
            or snapshot_error != row["snapshot_error"]
        ):
            updates.append((history_status, history_error, snapshot_status, snapshot_error, row["stock_code"]))

    if updates:
        conn.executemany(
            """
            UPDATE financial_sync_state
            SET history_status = ?, history_error = ?,
                snapshot_status = ?, snapshot_error = ?
            WHERE stock_code = ?
            """,
            updates,
        )


def summarize_history_gap_state(
    conn,
    stock_codes: Optional[list] = None,
    *,
    cooldown_hours: int = FIN_HISTORY_RETRY_COOLDOWN_HOURS,
) -> dict:
    params: list = []
    in_clause = ""
    if stock_codes:
        normalized = [_normalize_stock_code(code) for code in stock_codes if _normalize_stock_code(code)]
        if not normalized:
            return {
                "total_gap": 0,
                "retryable_gap": 0,
                "cooling_gap": 0,
                "recent_failed_gap": 0,
                "recent_empty_gap": 0,
                "recent_partial_gap": 0,
                "cooldown_hours": cooldown_hours,
            }
        placeholders = ",".join("?" for _ in normalized)
        in_clause = f" WHERE t.stock_code IN ({placeholders}) "
        params.extend(normalized)

    history_status_select = "NULL AS history_status"
    try:
        if "history_status" in _table_columns(conn, "financial_sync_state"):
            history_status_select = "s.history_status AS history_status"
    except Exception:
        pass

    base_sql = f"""
        WITH fin AS (
            SELECT stock_code, COUNT(*) AS history_rows
            FROM raw_gpcw_financial
            GROUP BY stock_code
        )
        SELECT t.stock_code,
               COALESCE(f.history_rows, 0) AS history_rows,
               s.last_history_at,
               {history_status_select}
        FROM mart_stock_trend t
        LEFT JOIN fin f ON f.stock_code = t.stock_code
        LEFT JOIN financial_sync_state s ON s.stock_code = t.stock_code
        {in_clause}
    """
    fallback_sql = f"""
        WITH fin AS (
            SELECT stock_code, COUNT(*) AS history_rows
            FROM raw_gpcw_financial
            GROUP BY stock_code
        )
        SELECT t.stock_code,
               COALESCE(f.history_rows, 0) AS history_rows,
               NULL AS last_history_at,
               NULL AS history_status
        FROM mart_stock_trend t
        LEFT JOIN fin f ON f.stock_code = t.stock_code
        {in_clause}
    """
    try:
        rows = conn.execute(base_sql, params).fetchall()
    except Exception:
        rows = conn.execute(fallback_sql, params).fetchall()

    cutoff = datetime.now() - timedelta(hours=cooldown_hours)
    summary = {
        "total_gap": 0,
        "retryable_gap": 0,
        "cooling_gap": 0,
        "recent_failed_gap": 0,
        "recent_empty_gap": 0,
        "recent_partial_gap": 0,
        "cooldown_hours": cooldown_hours,
    }

    for row in rows:
        history_rows = int(row["history_rows"] or 0)
        if history_rows >= FIN_HISTORY_TARGET_ROWS:
            continue

        summary["total_gap"] += 1
        last_history_at = _parse_sync_timestamp(row["last_history_at"])
        if last_history_at and last_history_at >= cutoff:
            summary["cooling_gap"] += 1
            history_status = _normalize_history_stage_status(row["history_status"], history_rows)
            if history_status == "failed":
                summary["recent_failed_gap"] += 1
            elif history_status == "empty":
                summary["recent_empty_gap"] += 1
            elif history_status == "partial":
                summary["recent_partial_gap"] += 1
        else:
            summary["retryable_gap"] += 1

    return summary


def _select_snapshot_candidates(
    conn,
    stock_codes: list[str],
    snapshot_now: datetime,
    cooldown_hours: int = FIN_SNAPSHOT_RECENT_HOURS,
) -> tuple[list[str], int]:
    if not stock_codes:
        return [], 0

    cutoff = snapshot_now - timedelta(hours=cooldown_hours)
    state_rows = conn.execute(
        """
        SELECT stock_code, last_snapshot_at, status, snapshot_status
        FROM financial_sync_state
        """
    ).fetchall()
    state_by_code = {row["stock_code"]: row for row in state_rows}
    candidates = []

    for code in stock_codes:
        state = state_by_code.get(code)
        last_snapshot_at = _parse_sync_timestamp(state["last_snapshot_at"]) if state else None
        if state and _snapshot_stage_status(state) == "ok" and last_snapshot_at and last_snapshot_at >= cutoff:
            continue
        candidates.append(code)

    return candidates, len(stock_codes) - len(candidates)


def _resolve_snapshot_report_date(conn, stock_code: str, notice_date: Optional[str]) -> Optional[str]:
    notice = _normalize_date(notice_date)
    if notice:
        exact = conn.execute(
            """
            SELECT report_date
            FROM raw_gpcw_financial
            WHERE stock_code = ?
              AND report_type != 'latest_snapshot'
              AND notice_date = ?
            ORDER BY report_date DESC
            LIMIT 1
            """,
            (stock_code, notice),
        ).fetchone()
        if exact and exact["report_date"]:
            return exact["report_date"]

        nearby = conn.execute(
            """
            SELECT report_date
            FROM raw_gpcw_financial
            WHERE stock_code = ?
              AND report_type != 'latest_snapshot'
              AND notice_date IS NOT NULL
              AND notice_date <= ?
            ORDER BY notice_date DESC, report_date DESC
            LIMIT 1
            """,
            (stock_code, notice),
        ).fetchone()
        if nearby and nearby["report_date"]:
            return nearby["report_date"]

    inferred = _infer_report_date_from_notice_date(notice)
    if inferred:
        return inferred

    latest = conn.execute(
        """
        SELECT report_date
        FROM raw_gpcw_financial
        WHERE stock_code = ?
        ORDER BY report_date DESC
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if latest and latest["report_date"]:
        return latest["report_date"]

    return None


def _cleanup_snapshot_stub(conn, stock_code: str, notice_date: Optional[str], report_date: Optional[str]) -> None:
    notice = _normalize_date(notice_date)
    if not notice or not report_date:
        return
    conn.execute(
        """
        DELETE FROM raw_gpcw_financial
        WHERE stock_code = ?
          AND report_type = 'latest_snapshot'
          AND notice_date = ?
          AND report_date != ?
        """,
        (stock_code, notice, report_date),
    )


def _fetch_latest_snapshot_batch(codes):
    def _fetch_on_client(client):
        results = {}
        for code in codes:
            try:
                records = client.finance_records(symbol=code)
                if records:
                    results[code] = records[0]
            except Exception:
                continue
        if not results:
            raise ValueError("empty finance batch")
        return results

    try:
        results, _source = call_tdx_quotes_with_retry(
            _fetch_on_client,
            action_name=f"finance[{len(codes)}]",
        )
        return results
    except ImportError:
        logger.warning("[财务] tdxhub 未安装，跳过最新快照同步")
    except Exception as exc:
        logger.error(f"[财务] 最新快照同步失败: {exc}")
    return {}


# ============================================================
# AKShare/Sina 历史财报回填
# ============================================================

def _extract_balance_rows(df, source_name: str) -> list[dict]:
    records = []
    if df is None or df.empty:
        return records

    for row in df.to_dict("records"):
        report_date = _normalize_date(row.get("报告日"))
        if not report_date:
            continue
        report_type = str(row.get("类型") or "").strip()
        if report_type and "合并" not in report_type:
            continue
        records.append({
            "report_date": report_date,
            "notice_date": _normalize_date(row.get("公告日期")),
            "report_type": report_type or None,
            "is_audited": _parse_audited(row.get("是否审计")),
            "total_assets": _parse_float(_pick_value(row, ["资产总计"])),
            "total_liabilities": _parse_float(_pick_value(row, ["负债合计"])),
            "net_assets": _parse_float(_pick_value(row, [
                "归属于母公司股东权益合计",
                "归属于母公司股东权益",
                "股东权益合计(净资产)",
                "所有者权益(或股东权益)合计",
            ])),
            "current_assets": _parse_float(_pick_value(row, ["流动资产合计"])),
            "current_liabilities": _parse_float(_pick_value(row, ["流动负债合计"])),
            "total_shares": _parse_float(_pick_value(row, ["实收资本(或股本)", "股本", "实收资本"])),
            "contract_liabilities": _parse_float(_pick_value(row, ["合同负债"])),
            "inventory": _parse_float(_pick_value(row, ["存货"])),
            "undistributed_profit": _parse_float(_pick_value(row, ["未分配利润"])),
            "source_file": source_name,
        })
    return records


def _extract_income_rows(df, source_name: str) -> list[dict]:
    records = []
    if df is None or df.empty:
        return records

    for row in df.to_dict("records"):
        report_date = _normalize_date(row.get("报告日"))
        if not report_date:
            continue
        report_type = str(row.get("类型") or "").strip()
        if report_type and "合并" not in report_type:
            continue

        revenue = _parse_float(_pick_value(row, ["营业总收入", "营业收入"]))
        operating_cost = _parse_float(_pick_value(row, ["营业成本"]))
        records.append({
            "report_date": report_date,
            "notice_date": _normalize_date(row.get("公告日期")),
            "report_type": report_type or None,
            "is_audited": _parse_audited(row.get("是否审计")),
            "revenue": revenue,
            "operating_profit": _parse_float(_pick_value(row, ["营业利润"])),
            "net_profit": _parse_float(_pick_value(row, [
                "归属于母公司所有者的净利润",
                "归属于母公司股东的净利润",
                "归属于母公司净利润",
                "净利润",
            ])),
            "eps": _parse_float(_pick_value(row, ["基本每股收益"])),
            "gross_profit": (revenue - operating_cost) if revenue is not None and operating_cost is not None else None,
            "source_file": source_name,
        })
    return records


def _extract_cashflow_rows(df, source_name: str) -> list[dict]:
    records = []
    if df is None or df.empty:
        return records

    for row in df.to_dict("records"):
        report_date = _normalize_date(row.get("报告日"))
        if not report_date:
            continue
        report_type = str(row.get("类型") or "").strip()
        if report_type and "合并" not in report_type:
            continue
        records.append({
            "report_date": report_date,
            "notice_date": _normalize_date(row.get("公告日期")),
            "report_type": report_type or None,
            "is_audited": _parse_audited(row.get("是否审计")),
            "operating_cashflow": _parse_float(_pick_value(row, ["经营活动产生的现金流量净额"])),
            "source_file": source_name,
        })
    return records


def _merge_history_records(stock_code: str, *parts: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for rows in parts:
        for row in rows:
            report_date = row.get("report_date")
            if not report_date:
                continue
            target = merged.setdefault(report_date, {
                "stock_code": stock_code,
                "report_date": report_date,
                "notice_date": None,
                "report_type": None,
                "is_audited": None,
                "source_file": None,
                "ingested_at": datetime.now().isoformat(),
            })
            for key, value in row.items():
                if key == "report_date":
                    continue
                if value is None:
                    continue
                target[key] = value

    records = list(merged.values())
    records.sort(key=lambda item: item["report_date"], reverse=True)
    return records[:FIN_HISTORY_FETCH_LIMIT]


def _is_retryable_history_fetch_error(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    retryable_fragments = (
        "expecting value",
        "char 0",
        "jsondecodeerror",
        "read timed out",
        "timed out",
        "connection aborted",
        "connection reset",
        "remote end closed connection",
        "max retries exceeded",
        "temporarily unavailable",
        "503",
        "504",
    )
    return any(fragment in text for fragment in retryable_fragments)


def _fetch_sina_statement_with_retry(ak_module, symbol: str, statement: str):
    last_exc: Optional[Exception] = None
    for attempt in range(1, FIN_HISTORY_SOURCE_RETRY_ATTEMPTS + 1):
        try:
            return ak_module.stock_financial_report_sina(stock=symbol, symbol=statement)
        except Exception as exc:
            last_exc = exc
            if attempt >= FIN_HISTORY_SOURCE_RETRY_ATTEMPTS or not _is_retryable_history_fetch_error(exc):
                raise
            delay = FIN_HISTORY_SOURCE_RETRY_BASE_DELAY_SECONDS * attempt
            logger.warning(
                f"[财务] {symbol} {statement} 第 {attempt} 次抓取失败，{delay:.2f}s 后重试: {str(exc)[:120]}"
            )
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"history_fetch_unexpected_empty_retry_loop:{symbol}:{statement}")


def _fetch_sina_history_batch(stock_codes: list[str]) -> tuple[list[dict], dict[str, dict]]:
    try:
        import akshare as ak
    except ImportError:
        logger.warning("[财务] akshare 未安装，跳过历史财务回填")
        return [], {code: {"status": "failed", "error": "akshare 未安装"} for code in stock_codes}

    all_records: list[dict] = []
    states: dict[str, dict] = {}

    for code in stock_codes:
        symbol = _to_sina_symbol(code)
        if not symbol:
            states[code] = {"status": "skipped", "error": "当前财报历史接口暂不支持该市场"}
            continue

        statement_failures = []

        def _fetch_statement(statement: str):
            try:
                return _fetch_sina_statement_with_retry(ak, symbol, statement)
            except Exception as exc:
                statement_failures.append(f"{statement}:{str(exc)[:120]}")
                return None

        try:
            balance_df = _fetch_statement("资产负债表")
            income_df = _fetch_statement("利润表")
            cashflow_df = _fetch_statement("现金流量表")
            merged = _merge_history_records(
                code,
                _extract_balance_rows(balance_df, "akshare_sina_balance"),
                _extract_income_rows(income_df, "akshare_sina_income"),
                _extract_cashflow_rows(cashflow_df, "akshare_sina_cashflow"),
            )
            if not merged:
                if statement_failures:
                    states[code] = {"status": "failed", "error": "; ".join(statement_failures)[:300]}
                else:
                    states[code] = {"status": "empty", "error": "未获取到历史财报"}
                continue

            all_records.extend(merged)
            state = {
                "status": "ok",
                "history_rows": len(merged),
                "last_report_date": merged[0]["report_date"],
            }
            if statement_failures:
                state["status"] = "partial"
                state["error"] = "; ".join(statement_failures)[:300]
            states[code] = state
        except Exception as exc:
            states[code] = {"status": "failed", "error": str(exc)[:300]}

    return all_records, states


def _select_history_candidates(conn, stock_codes: Optional[list] = None, limit: int = FIN_HISTORY_BATCH_SIZE) -> list[str]:
    ensure_tables(conn)

    history_cutoff = (datetime.now() - timedelta(hours=FIN_HISTORY_RETRY_COOLDOWN_HOURS)).isoformat()
    params: list = [FIN_HISTORY_TARGET_ROWS, history_cutoff]
    in_clause = ""
    if stock_codes:
        normalized = [_normalize_stock_code(code) for code in stock_codes if _normalize_stock_code(code)]
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        in_clause = f" AND a.stock_code IN ({placeholders}) "
        params.extend(normalized)

    params.append(limit)
    rows = conn.execute(
        f"""
        WITH fin AS (
            SELECT stock_code, COUNT(*) AS history_rows, MAX(report_date) AS latest_report_date
            FROM raw_gpcw_financial
            GROUP BY stock_code
        )
        SELECT a.stock_code
        FROM dim_active_a_stock a
        LEFT JOIN excluded_stocks e ON e.stock_code = a.stock_code
        LEFT JOIN fin f ON f.stock_code = a.stock_code
        LEFT JOIN financial_sync_state s ON s.stock_code = a.stock_code
        LEFT JOIN mart_current_relationship m ON m.stock_code = a.stock_code
        LEFT JOIN mart_stock_trend t ON t.stock_code = a.stock_code
        WHERE e.stock_code IS NULL
             AND COALESCE(f.history_rows, 0) < ?
             AND (
                     s.last_history_at IS NULL
                 OR s.last_history_at < ?
             )
          {in_clause}
        GROUP BY a.stock_code
        ORDER BY
            CASE
                WHEN ANY_VALUE(m.stock_code) IS NOT NULL THEN 0
                WHEN ANY_VALUE(t.stock_code) IS NOT NULL THEN 1
                ELSE 2
            END,
            COALESCE(ANY_VALUE(f.history_rows), 0) ASC,
            CASE WHEN ANY_VALUE(s.last_history_at) IS NULL THEN 0 ELSE 1 END,
            COALESCE(ANY_VALUE(s.last_history_at), ''),
            a.stock_code
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row["stock_code"] for row in rows]


def _resolve_history_candidate_limit(conn, stock_codes: Optional[list]) -> int:
    requested_count = len(stock_codes or [])
    if requested_count <= 0:
        return FIN_HISTORY_BATCH_SIZE
    return min(requested_count, FIN_HISTORY_BATCH_SIZE)


def _apply_history_backfill(conn, stock_codes: list[str], records: list[dict], states: dict[str, dict], synced_at: str) -> int:
    inserted = 0
    touched = set()
    for record in records:
        row = {col: record.get(col) for col in RAW_FINANCIAL_COLUMNS}
        row["ingested_at"] = synced_at
        _upsert_raw_financial(conn, row)
        touched.add(record["stock_code"])
        inserted += 1

    for code in stock_codes:
        count_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt, MAX(report_date) AS latest_report
            FROM raw_gpcw_financial
            WHERE stock_code = ?
            """,
            (code,),
        ).fetchone()
        state = states.get(code, {})
        history_rows = count_row["cnt"] if count_row else 0
        last_report_date = count_row["latest_report"] if count_row else None
        history_status = _history_stage_status(state, history_rows)
        conn.execute(
            """
            INSERT INTO financial_sync_state
            (stock_code, history_rows, last_report_date, last_history_at,
             history_status, history_error, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                history_rows = excluded.history_rows,
                last_report_date = excluded.last_report_date,
                last_history_at = excluded.last_history_at,
                history_status = excluded.history_status,
                history_error = excluded.history_error,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                code,
                history_rows,
                last_report_date,
                synced_at,
                history_status,
                state.get("error"),
                history_status,
                state.get("error"),
                synced_at,
            ),
        )
    return inserted


# ============================================================
# 公共同步入口
# ============================================================

async def sync_financial_data(
    conn,
    stock_codes: Optional[list] = None,
    *,
    progress_callback: Optional[Callable[[dict], None]] = None,
    should_stop=None,
    include_history: bool = True,
    include_snapshot: bool = True,
    include_capital: bool = True,
    include_indicator: bool = True,
    history_batch_limit: Optional[int] = None,
) -> int:
    """同步最新快照，并增量回填历史财务序列。"""
    ensure_tables(conn)

    if not stock_codes:
        rows = conn.execute(
            "SELECT DISTINCT a.stock_code "
            "FROM dim_active_a_stock a "
            "LEFT JOIN excluded_stocks e ON e.stock_code = a.stock_code "
            "WHERE e.stock_code IS NULL"
        ).fetchall()
        stock_codes = [r["stock_code"] for r in rows]
    else:
        stock_codes = [_normalize_stock_code(code) for code in stock_codes if _normalize_stock_code(code)]

    if not stock_codes:
        logger.warning("[财务] dim_active_a_stock 为空，请先跑「数据获取 → 同步十大股东」拉取主数据")
        return 0

    def _check_stop() -> None:
        if should_stop:
            should_stop()

    progress = {
        "summary": {
            "status": "running",
            "records": 0,
            "history_rows": 0,
            "snapshot_rows": 0,
            "capital_rows": 0,
            "indicator_rows": 0,
            "quality_stocks": 0,
            "archetype_stocks": 0,
        },
        "history_backfill": {
            "status": "pending",
            "candidate_codes": 0,
            "done_codes": 0,
            "success_codes": 0,
            "partial_codes": 0,
            "failed_codes": 0,
            "rows": 0,
            "target_reports": FIN_HISTORY_FETCH_LIMIT,
        },
        "snapshot_sync": {
            "status": "pending",
            "candidate_codes": 0,
            "done_codes": 0,
            "success_codes": 0,
            "failed_codes": 0,
            "rows": 0,
            "skipped_recent": 0,
            "batch_size": FIN_SNAPSHOT_BATCH_SIZE,
        },
        "capital_behavior": {
            "status": "pending",
            "rows": 0,
        },
        "financial_indicator": {
            "status": "pending",
            "rows": 0,
        },
    }

    def _resolve_stage_status(*, candidates: int, success: int = 0, partial: int = 0, failed: int = 0) -> str:
        if candidates <= 0:
            return "skipped"
        if failed and not (success or partial):
            return "failed"
        if failed or partial:
            return "partial"
        return "success"

    def _refresh_summary(status: Optional[str] = None) -> None:
        history_rows = progress["history_backfill"]["rows"]
        snapshot_rows = progress["snapshot_sync"]["rows"]
        capital_rows = progress["capital_behavior"]["rows"]
        indicator_rows = progress["financial_indicator"]["rows"]
        progress["summary"].update({
            "records": history_rows + snapshot_rows + capital_rows + indicator_rows,
            "history_rows": history_rows,
            "snapshot_rows": snapshot_rows,
            "capital_rows": capital_rows,
            "indicator_rows": indicator_rows,
        })
        if status:
            progress["summary"]["status"] = status

    def _emit_progress(status: Optional[str] = None) -> None:
        _refresh_summary(status)
        if not progress_callback:
            return
        try:
            progress_callback(copy.deepcopy(progress))
        except Exception as exc:
            logger.warning(f"[财务] 进度回调失败: {exc}")

    _emit_progress()

    loop = asyncio.get_running_loop()

    async def _run_local_db_stage(task_fn):
        from services.db import get_conn as _get_conn

        def _worker():
            worker_conn = _get_conn(timeout=120)
            try:
                return task_fn(worker_conn)
            finally:
                worker_conn.close()

        return await loop.run_in_executor(None, _worker)

    now = datetime.now().isoformat()
    resolved_history_batch_limit = (
        min(int(history_batch_limit), FIN_HISTORY_BATCH_SIZE)
        if history_batch_limit is not None
        else _resolve_history_candidate_limit(conn, stock_codes)
    )
    history_candidates = (
        _select_history_candidates(conn, stock_codes=stock_codes, limit=resolved_history_batch_limit)
        if include_history and resolved_history_batch_limit > 0
        else []
    )
    progress["history_backfill"].update({
        "status": "running" if history_candidates else "skipped",
        "candidate_codes": len(history_candidates),
        "done_codes": 0,
        "success_codes": 0,
        "partial_codes": 0,
        "failed_codes": 0,
        "rows": 0,
        "target_reports": FIN_HISTORY_FETCH_LIMIT,
        "batch_limit": resolved_history_batch_limit,
        "skip_reason": None if include_history else "daily critical sync skips historical backfill",
    })
    _emit_progress()
    history_upserts = 0
    if history_candidates:
        _check_stop()
        logger.info(
            f"[财务] 开始回填历史财报: 候选 {len(history_candidates)} 只"
            f"（批次上限 {resolved_history_batch_limit}），目标每只最多 {FIN_HISTORY_FETCH_LIMIT} 期"
        )
        records, states = await loop.run_in_executor(None, _fetch_sina_history_batch, history_candidates)
        _check_stop()
        history_upserts = _apply_history_backfill(conn, history_candidates, records, states, now)
        conn.commit()
        success_count = sum(1 for state in states.values() if state.get("status") == "ok")
        partial_count = sum(1 for state in states.values() if state.get("status") == "partial")
        failed_count = len(history_candidates) - success_count - partial_count
        progress["history_backfill"].update({
            "status": _resolve_stage_status(
                candidates=len(history_candidates),
                success=success_count,
                partial=partial_count,
                failed=failed_count,
            ),
            "candidate_codes": len(history_candidates),
            "done_codes": len(history_candidates),
            "success_codes": success_count,
            "partial_codes": partial_count,
            "failed_codes": failed_count,
            "rows": history_upserts,
            "target_reports": FIN_HISTORY_FETCH_LIMIT,
            "batch_limit": resolved_history_batch_limit,
        })
        _emit_progress()
        logger.info(
            f"[财务] 历史回填完成: {history_upserts} 条记录, 成功 {success_count}, 未满目标 {partial_count}, 失败/空结果 {failed_count}"
        )
    else:
        if include_history:
            logger.info("[财务] 历史财报覆盖已达当前批次目标，无需回填")
        else:
            logger.info("[财务] 每日关键同步跳过历史财报回填")

    latest_upserts = 0
    snapshot_now = datetime.now()
    snapshot_candidates, skipped_recent = (
        _select_snapshot_candidates(conn, stock_codes, snapshot_now)
        if include_snapshot
        else ([], 0)
    )
    progress["snapshot_sync"].update({
        "status": "running" if snapshot_candidates else "skipped",
        "candidate_codes": len(snapshot_candidates),
        "done_codes": 0,
        "success_codes": 0,
        "failed_codes": 0,
        "rows": 0,
        "skipped_recent": skipped_recent,
        "batch_size": FIN_SNAPSHOT_BATCH_SIZE,
        "skip_reason": None if include_snapshot else "snapshot sync disabled",
    })
    _emit_progress()
    if snapshot_candidates:
        logger.info(
            f"[财务] 开始同步 {len(snapshot_candidates)} 只股票的最新财务快照"
            + (f"，跳过最近已成功 {skipped_recent} 只" if skipped_recent else "")
        )
    else:
        logger.info(f"[财务] 最新快照最近已完成，跳过 {skipped_recent} 只股票")

    snapshot_failures = 0
    snapshot_processed = 0

    if snapshot_candidates:
        _check_stop()
        batches = [
            snapshot_candidates[index:index + FIN_SNAPSHOT_BATCH_SIZE]
            for index in range(0, len(snapshot_candidates), FIN_SNAPSHOT_BATCH_SIZE)
        ]

        async def _run_snapshot_batch(batch: list[str]) -> tuple[list[str], dict[str, dict]]:
            result = await loop.run_in_executor(_FIN_SNAPSHOT_EXECUTOR, _fetch_latest_snapshot_batch, batch)
            return batch, result

        tasks = [asyncio.create_task(_run_snapshot_batch(batch)) for batch in batches]
        try:
            for task in asyncio.as_completed(tasks):
                _check_stop()
                batch, batch_results = await task
                batch_synced_at = datetime.now().isoformat()
                batch_success_codes = []

                for code in batch:
                    raw = batch_results.get(code)
                    if not raw:
                        _upsert_snapshot_state(
                            conn,
                            code,
                            updated_at=batch_synced_at,
                            status="failed",
                            error="snapshot_empty",
                        )
                        snapshot_failures += 1
                        continue

                    parsed = _parse_finance_record(raw)
                    notice_date = _normalize_date(raw.get("updated_date"))
                    report_date = _resolve_snapshot_report_date(conn, code, notice_date)
                    if not report_date:
                        _upsert_snapshot_state(
                            conn,
                            code,
                            updated_at=batch_synced_at,
                            status="failed",
                            error="missing_snapshot_report_date",
                        )
                        snapshot_failures += 1
                        continue

                    record = {
                        "stock_code": code,
                        "report_date": report_date,
                        "notice_date": notice_date,
                        "report_type": "latest_snapshot",
                        "is_audited": None,
                        "total_assets": parsed.get("total_assets"),
                        "total_liabilities": parsed.get("total_liabilities"),
                        "net_assets": parsed.get("net_assets"),
                        "current_assets": parsed.get("current_assets"),
                        "current_liabilities": parsed.get("current_liabilities"),
                        "revenue": parsed.get("revenue"),
                        "operating_profit": parsed.get("operating_profit"),
                        "net_profit": parsed.get("net_profit"),
                        "operating_cashflow": parsed.get("operating_cashflow"),
                        "total_shares": parsed.get("total_shares"),
                        "float_shares": parsed.get("float_shares"),
                        "holder_count": parsed.get("holder_count"),
                        "contract_liabilities": None,
                        "eps": parsed.get("eps"),
                        "nav_per_share": parsed.get("nav_per_share"),
                        "gross_profit": parsed.get("gross_profit"),
                        "inventory": parsed.get("inventory"),
                        "undistributed_profit": parsed.get("undistributed_profit"),
                        "source_file": "tdxhub_finance",
                        "ingested_at": batch_synced_at,
                    }
                    _upsert_raw_financial(conn, record)
                    _cleanup_snapshot_stub(conn, code, notice_date, report_date)
                    batch_success_codes.append(code)
                    latest_upserts += 1

                if batch_success_codes:
                    _update_snapshot_state(conn, batch_success_codes, batch_synced_at)
                conn.commit()

                snapshot_processed += len(batch)
                progress["snapshot_sync"].update({
                    "status": "running",
                    "candidate_codes": len(snapshot_candidates),
                    "done_codes": snapshot_processed,
                    "success_codes": latest_upserts,
                    "failed_codes": snapshot_failures,
                    "rows": latest_upserts,
                    "skipped_recent": skipped_recent,
                    "batch_size": FIN_SNAPSHOT_BATCH_SIZE,
                })
                if (
                    snapshot_processed == len(snapshot_candidates)
                    or snapshot_processed % FIN_SNAPSHOT_PROGRESS_EVERY == 0
                ):
                    logger.info(
                        f"[财务] 最新快照已处理 {snapshot_processed}/{len(snapshot_candidates)}"
                        f"，成功 {latest_upserts}，失败 {snapshot_failures}"
                    )
                    _emit_progress()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        if latest_upserts == 0:
            logger.warning("[财务] 未获取到任何可落库的最新财务快照")

    snapshot_status = _resolve_stage_status(
        candidates=len(snapshot_candidates),
        success=latest_upserts,
        failed=snapshot_failures,
    )
    if snapshot_status == "failed" and skipped_recent > 0:
        snapshot_status = "partial"
    progress["snapshot_sync"].update({
        "status": snapshot_status,
        "candidate_codes": len(snapshot_candidates),
        "done_codes": snapshot_processed,
        "success_codes": latest_upserts,
        "failed_codes": snapshot_failures,
        "rows": latest_upserts,
        "skipped_recent": skipped_recent,
        "batch_size": FIN_SNAPSHOT_BATCH_SIZE,
    })
    _emit_progress()

    logger.info(
        f"[财务] 最新快照同步完成: {latest_upserts} 条"
        + (f"，失败 {snapshot_failures} 只" if snapshot_failures else "")
        + (f"，跳过最近已成功 {skipped_recent} 只" if skipped_recent else "")
    )

    capital_total = 0
    if include_capital:
        progress["capital_behavior"]["status"] = "running"
        _emit_progress()
        try:
            _check_stop()
            from services.capital_client import sync_capital_behavior_data
            capital_total = await sync_capital_behavior_data(conn, stock_codes=stock_codes)
            progress["capital_behavior"].update({
                "status": "success",
                "rows": capital_total,
            })
        except Exception as exc:
            progress["capital_behavior"].update({
                "status": "failed",
                "rows": 0,
                "error": str(exc)[:200],
            })
            logger.warning(f"[财务] 资本行为增强同步失败，跳过本轮: {exc}")
    else:
        progress["capital_behavior"].update({
            "status": "skipped",
            "rows": 0,
            "skip_reason": "daily critical sync skips capital behavior",
        })
    _emit_progress()

    indicator_total = 0
    if include_indicator:
        progress["financial_indicator"]["status"] = "running"
        _emit_progress()
        try:
            _check_stop()
            from services.financial_indicator_client import sync_financial_indicator_data
            indicator_total = await sync_financial_indicator_data(conn, stock_codes=stock_codes)
            progress["financial_indicator"].update({
                "status": "success",
                "rows": indicator_total,
            })
        except Exception as exc:
            progress["financial_indicator"].update({
                "status": "failed",
                "rows": 0,
                "error": str(exc)[:200],
            })
            logger.warning(f"[财务] 扩展财务指标同步失败，跳过本轮: {exc}")
    else:
        progress["financial_indicator"].update({
            "status": "skipped",
            "rows": 0,
            "skip_reason": "daily critical sync skips extended indicators",
        })
    _emit_progress()

    total = latest_upserts + history_upserts
    logger.info(
        f"[财务] 同步结束: 最新 {latest_upserts} 条, 历史 {history_upserts} 条, "
        f"资本行为 {capital_total} 条, 扩展指标 {indicator_total} 条"
    )
    _emit_progress("completed")
    return total + capital_total + indicator_total


# ============================================================
# 计算派生指标
# ============================================================

def calc_financial_derived(conn) -> int:
    """从 raw_gpcw_financial 计算派生指标，写入 fact + dim 表。"""
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT * FROM raw_gpcw_financial ORDER BY stock_code, report_date
    """).fetchall()

    if not rows:
        logger.info("[财务] 无原始数据，跳过派生计算")
        return 0

    now = datetime.now().isoformat()
    count = 0

    by_stock = defaultdict(list)
    for row in rows:
        by_stock[row["stock_code"]].append(dict(row))

    conn.execute("DELETE FROM fact_financial_derived")

    for code, records in by_stock.items():
        records.sort(key=lambda item: item["report_date"])

        for i, rec in enumerate(records):
            rd = rec["report_date"]
            season = _report_season(rd)

            roe = _safe_div(rec.get("net_profit"), rec.get("net_assets"))
            debt_ratio = _safe_div(rec.get("total_liabilities"), rec.get("total_assets"))
            current_ratio = _safe_div(rec.get("current_assets"), rec.get("current_liabilities"))
            gross_margin = _safe_div(rec.get("gross_profit"), rec.get("revenue"))
            net_margin = _safe_div(rec.get("net_profit"), rec.get("revenue"))
            ocf_to_profit = _safe_div(rec.get("operating_cashflow"), rec.get("net_profit"))
            contract_to_revenue = _safe_div(rec.get("contract_liabilities"), rec.get("revenue"))

            revenue_yoy = None
            profit_yoy = None
            holder_count_change = None

            target_year = int(rd[:4]) - 1 if rd and len(rd) >= 4 else None
            target_date = f"{target_year}{rd[4:]}" if target_year else None
            if target_date:
                prev_same_q = next((prev for prev in records[:i] if prev["report_date"] == target_date), None)
                if prev_same_q:
                    revenue_yoy = _safe_div(
                        (rec.get("revenue") or 0) - (prev_same_q.get("revenue") or 0),
                        abs(prev_same_q.get("revenue") or 0) or None,
                    )
                    profit_yoy = _safe_div(
                        (rec.get("net_profit") or 0) - (prev_same_q.get("net_profit") or 0),
                        abs(prev_same_q.get("net_profit") or 0) or None,
                    )

            if i > 0:
                prev_rec = records[i - 1]
                if rec.get("holder_count") and prev_rec.get("holder_count"):
                    holder_count_change = _safe_div(
                        rec["holder_count"] - prev_rec["holder_count"],
                        prev_rec["holder_count"],
                    )

            conn.execute("""
                INSERT OR REPLACE INTO fact_financial_derived
                (stock_code, report_date, report_season, roe, debt_ratio, current_ratio,
                 gross_margin, net_margin, revenue_yoy, profit_yoy, ocf_to_profit,
                 contract_to_revenue, holder_count_change_pct, float_shares, total_shares, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                code,
                rd,
                season,
                roe,
                debt_ratio,
                current_ratio,
                gross_margin,
                net_margin,
                revenue_yoy,
                profit_yoy,
                ocf_to_profit,
                contract_to_revenue,
                holder_count_change,
                rec.get("float_shares"),
                rec.get("total_shares"),
                now,
            ))
            count += 1

    conn.execute("DELETE FROM dim_financial_latest")
    conn.execute("""
        INSERT INTO dim_financial_latest
        (stock_code, latest_report_date, roe, debt_ratio, current_ratio, gross_margin,
         net_margin, revenue_yoy, profit_yoy, ocf_to_profit, contract_to_revenue,
         holder_count, holder_count_change_pct, float_shares, total_shares, history_rows, updated_at)
        SELECT
            f.stock_code,
            f.report_date,
            f.roe,
            f.debt_ratio,
            f.current_ratio,
            f.gross_margin,
            f.net_margin,
            f.revenue_yoy,
            f.profit_yoy,
            f.ocf_to_profit,
            f.contract_to_revenue,
            r.holder_count,
            f.holder_count_change_pct,
            f.float_shares,
            f.total_shares,
            hist.history_rows,
            ?
        FROM fact_financial_derived f
        JOIN raw_gpcw_financial r
          ON f.stock_code = r.stock_code AND f.report_date = r.report_date
        JOIN (
            SELECT stock_code, COUNT(*) AS history_rows
            FROM raw_gpcw_financial
            GROUP BY stock_code
        ) hist
          ON hist.stock_code = f.stock_code
        WHERE f.report_date = (
            SELECT MAX(f2.report_date)
            FROM fact_financial_derived f2
            WHERE f2.stock_code = f.stock_code
        )
    """, (now,))

    conn.commit()
    dim_count = conn.execute("SELECT COUNT(*) FROM dim_financial_latest").fetchone()[0]
    logger.info(f"[财务] 派生计算完成: {count} 条事实, {dim_count} 条最新快照")
    return count
