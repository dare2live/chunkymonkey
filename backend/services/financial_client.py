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
# FIN_HISTORY_FETCH_LIMIT/FIN_HISTORY_BATCH_SIZE 已删 2026-06-27 (唯一使用点在已删 sync body)
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
    statements = [
        f"ALTER TABLE {table_name} ADD COLUMN {col} {ddl}"
        for col, ddl in columns.items()
        if col not in existing
    ]
    if statements:
        conn.execute(";\n".join(statements))


# ============================================================
# Schema
# ============================================================

def ensure_tables(conn):
    """创建财务数据相关表，并补齐增量演进字段。"""
    conn.executescript("""
        -- raw_gpcw_financial DDL 已删 (2026-06-27 通达信全删 gpcw物删) 派生源已迁 tushare 周期模型
        -- calc_financial_derived 读 raw_tushare_fina_indicator/balancesheet/income, gpcw sync 已退役

        -- 2026-06-28 加工层清空: fact_financial_derived DDL 已删 (财务 derived 退役, raw_tushare 保留)

        -- 2026-06-28 加工层清空: dim_financial_latest DDL 已删 (财务 derived 退役)

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
# [RETIRED 2026-06-27 通达信全删] 以下 gpcw sina/akshare 同步路径全 DEAD:
#   - sync_financial_data + _fetch_*/_parse_finance_record/_upsert_raw_financial 等 0 live caller
#     (daily 财务 sync 走 registry tushare; calc_financial_derived 已迁 tushare 周期模型)
#   - 写的 raw_gpcw_financial 已物删 (DDL 从 ensure_tables 移除)
#   保留仅因: ensure_tables 依赖本段内 _bootstrap_financial_sync_state_phase_columns + 外部模块共享
#   _parse_float/_parse_int 等工具 (lhb/qfii/aif10/capital_client)。整段代码移除 = 后续低风险 follow-up
#   (须保 _bootstrap + 共享 _parse_* 工具, 删 sina/akshare 抓取与 sync_financial_data)。勿调用本段。
# ============================================================

# [RETIRED 2026-06-27 通达信全删 M4] gpcw sina/akshare 抓取/解析/写入 helper 整段物删
#   (_parse_finance_record/_upsert_raw_financial/_update_snapshot_state/_upsert_snapshot_state 等, 0-live-caller; raw_gpcw_financial 表已物删)。

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


# [RETIRED 2026-06-27] _snapshot_stage_status/_history_stage_status dead 包装物删 (仅被已删 sync body 调)。


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


# [RETIRED 2026-06-27 通达信全删 M4] sina/akshare 历史回填 + 快照同步 + sync_financial_data 整段物删
#   (0-live-caller, 见 §309 头注; calc_financial_derived 读 tushare 周期模型替代)。



# ============================================================
# 计算派生指标
# ============================================================

# fina_indicator/balancesheet/income/stk_holdernumber 同一 (ts_code,end_date) 常有多版本行
# (update_flag 0=原始 / 1=更正; restatement). 取最新披露+更正版: ann_date DESC, update_flag DESC, built_at DESC.
# stk_holdernumber 无 update_flag, 仅用 ann_date/built_at. 实测 fina 21467 组 (ts_code,end_date) 有重复.
_FINA_DEDUP_ORDER = "ann_date DESC NULLS LAST, update_flag DESC NULLS LAST, built_at DESC NULLS LAST"
_HOLDER_DEDUP_ORDER = "ann_date DESC NULLS LAST, built_at DESC NULLS LAST"


def calc_financial_derived(conn, *, attach: bool = True, write_suffix: str = "") -> int:
    """从 tushare 周期模型计算派生财务指标，写入 fact + dim 表。

    2026-06-26 通达信全删 单元4: 源从 raw_gpcw_financial (F10 快照模型) 迁移到 tushare 周期模型。
    迁移动因 = 修复 gpcw 数据质量错 (值比对 救出, 见 analysis/tdxhub_full_retire_plan_20260626.md):
      - gpcw `revenue` 虚高 ~15x (茅台显示 1.28 万亿 vs 实际 ~1700 亿) → gross_margin 烂 (茅台 8.7% vs 真实 91%)。
    源映射经 workflow wydf17fu8 对抗验证 + controller 茅台 600519 亲核。两处真金白银修复:
      1. gross_margin <- fina.grossprofit_margin (毛利率%), 绝不用 fina.gross_margin (=毛利【金额】, 同 gpcw 错)。
      2. roe <- fina.roe_yearly (年化), 非季报累计 roe — 5196/5202 股最新期=Q1, 累计 roe≈年化 1/4 跨期不可比,
         而 scoring.py 用绝对阈值 (roe>=0.18→12 分) 按【年度】标定 → 累计口径会把几乎所有股压到最低档。
    其余比率列 (debt/current/net_margin/ocf/yoy) 跨期可比, 直接 /100 (current_ratio 是倍数不除)。
    contract_to_revenue 用 bs+income 共同最新期 (INTERSECT, 取两表都有的 MAX end_date), 匹配已验证映射。
    fact 层 (历史) 的 float_shares/total_shares/holder_count_change_pct 留 NULL: 是 point-in-time/异 grain 量,
    无任何消费方读 fact 这几列 (audit 只 COUNT, watermark 只 MAX(report_date)); dim 层才填这几列 (有消费方)。

    attach=True: ATTACH 真 tushare_raw (READ_ONLY); attach=False: 假设 `tr` 已 attach (单测注合成数据)。
    write_suffix: '' 写 live; '_shadow' 写影子表 (promote 前验证用, 不碰 live)。
    """
    ensure_tables(conn)
    if attach:
        from services.database_manifest import get_database_manifest
        tr_path = get_database_manifest().path_for("tushare_raw")
        conn.execute(f"ATTACH IF NOT EXISTS '{tr_path}' AS tr (READ_ONLY)")

    now = datetime.now().isoformat()
    fact_tbl = f"fact_financial_derived{write_suffix}"
    dim_tbl = f"dim_financial_latest{write_suffix}"
    if write_suffix:
        # 影子表按 live schema 建 (空), 验证用; live 表 ensure_tables 已建
        conn.execute(f"CREATE TABLE IF NOT EXISTS {fact_tbl} AS SELECT * FROM fact_financial_derived WHERE 1=0")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {dim_tbl} AS SELECT * FROM dim_financial_latest WHERE 1=0")

    # 去重后的源 CTE (fact + dim 共用语义, 各自内联以保证单语句原子)
    fina_dedup = f"""
        SELECT ts_code, end_date, roe_yearly, debt_to_assets, current_ratio, grossprofit_margin,
               netprofit_margin, tr_yoy, netprofit_yoy, ocf_to_profit
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY {_FINA_DEDUP_ORDER}) rn
            FROM tr.raw_tushare_fina_indicator
        ) WHERE rn = 1
    """
    bs_dedup = f"""
        SELECT ts_code, end_date, TRY_CAST(contract_liab AS DOUBLE) AS contract_liab
        FROM (
            SELECT ts_code, end_date, contract_liab,
                   ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY {_FINA_DEDUP_ORDER}) rn
            FROM tr.raw_tushare_balancesheet
        ) WHERE rn = 1
    """
    inc_dedup = f"""
        SELECT ts_code, end_date, revenue
        FROM (
            SELECT ts_code, end_date, revenue,
                   ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY {_FINA_DEDUP_ORDER}) rn
            FROM tr.raw_tushare_income
        ) WHERE rn = 1
    """
    # end_date 'YYYYMMDD' -> report_date 'YYYY-MM-DD' (与旧 gpcw 格式一致 + watermark 字符串比较正确)
    _fmt_rd = "substr({c}.end_date,1,4)||'-'||substr({c}.end_date,5,2)||'-'||substr({c}.end_date,7,2)"
    _season = ("CASE substr({c}.end_date,5,2) WHEN '03' THEN 'Q1' WHEN '06' THEN 'Q2' "
               "WHEN '09' THEN 'Q3' WHEN '12' THEN 'Q4' ELSE '' END")

    # ---- fact_financial_derived: 周期历史 (一行/ts_code×end_date), fina 为期网格 ----
    conn.execute(f"DELETE FROM {fact_tbl}")
    conn.execute(f"""
        INSERT INTO {fact_tbl}
        (stock_code, report_date, report_season, roe, debt_ratio, current_ratio,
         gross_margin, net_margin, revenue_yoy, profit_yoy, ocf_to_profit,
         contract_to_revenue, holder_count_change_pct, float_shares, total_shares, updated_at)
        WITH fina AS ({fina_dedup}), bs AS ({bs_dedup}), inc AS ({inc_dedup})
        SELECT
            substr(f.ts_code,1,6)            AS stock_code,
            {_fmt_rd.format(c='f')}          AS report_date,
            {_season.format(c='f')}          AS report_season,
            f.roe_yearly        / 100.0      AS roe,
            f.debt_to_assets    / 100.0      AS debt_ratio,
            f.current_ratio                  AS current_ratio,
            f.grossprofit_margin/ 100.0      AS gross_margin,
            f.netprofit_margin  / 100.0      AS net_margin,
            f.tr_yoy            / 100.0      AS revenue_yoy,
            f.netprofit_yoy     / 100.0      AS profit_yoy,
            f.ocf_to_profit     / 100.0      AS ocf_to_profit,
            -- contract_to_revenue 仅在年报期(1231)算: contract_liab=时点余额, revenue=累计YTD,
            -- 仅 FY 期 revenue=完整12个月与余额同口径; 非 FY 期(Q1/H1/Q3) revenue 是部分年度→比率虚高跨期不可比(对抗验证 wuxnownvm BLOCKER)。
            CASE WHEN substr(f.end_date,5,4) = '1231'
                 THEN bs.contract_liab / NULLIF(inc.revenue, 0) ELSE NULL END AS contract_to_revenue,
            NULL AS holder_count_change_pct,
            NULL AS float_shares,
            NULL AS total_shares,
            ?    AS updated_at
        FROM fina f
        LEFT JOIN bs  ON bs.ts_code  = f.ts_code AND bs.end_date  = f.end_date
        LEFT JOIN inc ON inc.ts_code = f.ts_code AND inc.end_date = f.end_date
    """, (now,))
    fact_count = conn.execute(f"SELECT COUNT(*) FROM {fact_tbl}").fetchone()[0]

    # ---- dim_financial_latest: 每股最新快照 ----
    conn.execute(f"DELETE FROM {dim_tbl}")
    conn.execute(f"""
        INSERT INTO {dim_tbl}
        (stock_code, latest_report_date, roe, debt_ratio, current_ratio, gross_margin,
         net_margin, revenue_yoy, profit_yoy, ocf_to_profit, contract_to_revenue,
         holder_count, holder_count_change_pct, float_shares, total_shares, history_rows, updated_at)
        WITH fina AS ({fina_dedup}), bs AS ({bs_dedup}), inc AS ({inc_dedup}),
        fina_latest AS (
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) rn2 FROM fina
            ) WHERE rn2 = 1
        ),
        fina_hist AS (
            SELECT ts_code, COUNT(DISTINCT end_date) AS history_rows FROM fina GROUP BY ts_code
        ),
        common_period AS (   -- contract: 锁【最新年报期 FY/1231】(bs∩income 都有值的 MAX 1231 期)
            -- 修复期间口径混合 BLOCKER (对抗验证 wuxnownvm HIGH): contract_liab=时点余额, revenue=累计YTD;
            -- 只取 FY 期保证分母=完整12个月, 跨股可比, 无季节偏差。茅台共同期本就落 20251231 故值不变(0.047);
            -- 保险股(601628 Q1→FY)从虚高 70.93 修正回真实 ~10。代价: contract_liab 用 FY 期(可能比最新季稍旧), 但比率口径正确优先。
            SELECT b.ts_code, MAX(b.end_date) AS end_date
            FROM bs b JOIN inc i ON b.ts_code = i.ts_code AND b.end_date = i.end_date
            WHERE b.contract_liab IS NOT NULL AND i.revenue IS NOT NULL AND i.revenue <> 0
              AND substr(b.end_date,5,4) = '1231'
            GROUP BY b.ts_code
        ),
        contract AS (
            SELECT c.ts_code, b.contract_liab / NULLIF(i.revenue, 0) AS contract_to_revenue
            FROM common_period c
            JOIN bs  b ON b.ts_code = c.ts_code AND b.end_date = c.end_date
            JOIN inc i ON i.ts_code = c.ts_code AND i.end_date = c.end_date
        ),
        hn AS (   -- 户数: 先 (ts_code,end_date) 去重, 再取最新期 + LEAD 上一期算环比
            SELECT ts_code, end_date, TRY_CAST(holder_num AS BIGINT) AS holder_num
            FROM (
                SELECT ts_code, end_date, holder_num,
                       ROW_NUMBER() OVER (PARTITION BY ts_code, end_date ORDER BY {_HOLDER_DEDUP_ORDER}) rn
                FROM tr.raw_tushare_stk_holdernumber
            ) WHERE rn = 1
        ),
        holder AS (
            SELECT ts_code, holder_num AS holder_count,
                   CASE WHEN prev IS NULL OR prev = 0 THEN NULL
                        ELSE (holder_num - prev) * 1.0 / prev END AS holder_count_change_pct
            FROM (
                SELECT ts_code, holder_num,
                       LEAD(holder_num) OVER (PARTITION BY ts_code ORDER BY end_date DESC) AS prev,
                       ROW_NUMBER()    OVER (PARTITION BY ts_code ORDER BY end_date DESC) rk
                FROM hn
            ) WHERE rk = 1
        ),
        db_latest AS (   -- 流通/总股本: daily_basic 最新日 (万股 -> 股 ×10000)
            SELECT ts_code, float_share, total_share FROM (
                SELECT ts_code, float_share, total_share,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM tr.raw_tushare_daily_basic
            ) WHERE rn = 1
        )
        SELECT
            substr(f.ts_code,1,6)            AS stock_code,
            {_fmt_rd.format(c='f')}          AS latest_report_date,
            f.roe_yearly        / 100.0      AS roe,
            f.debt_to_assets    / 100.0      AS debt_ratio,
            f.current_ratio                  AS current_ratio,
            f.grossprofit_margin/ 100.0      AS gross_margin,
            f.netprofit_margin  / 100.0      AS net_margin,
            f.tr_yoy            / 100.0      AS revenue_yoy,
            f.netprofit_yoy     / 100.0      AS profit_yoy,
            f.ocf_to_profit     / 100.0      AS ocf_to_profit,
            c.contract_to_revenue            AS contract_to_revenue,
            h.holder_count                   AS holder_count,
            h.holder_count_change_pct        AS holder_count_change_pct,
            db.float_share * 10000.0         AS float_shares,
            db.total_share * 10000.0         AS total_shares,
            hist.history_rows                AS history_rows,
            ?                                AS updated_at
        FROM fina_latest f
        LEFT JOIN contract  c    ON c.ts_code    = f.ts_code
        LEFT JOIN holder    h    ON h.ts_code    = f.ts_code
        LEFT JOIN db_latest db   ON db.ts_code   = f.ts_code
        LEFT JOIN fina_hist hist ON hist.ts_code = f.ts_code
    """, (now,))

    conn.commit()
    dim_count = conn.execute(f"SELECT COUNT(*) FROM {dim_tbl}").fetchone()[0]
    logger.info(f"[财务] 派生计算完成 (tushare 周期模型{'/'+write_suffix if write_suffix else ''}): "
                f"{fact_count} 条事实, {dim_count} 条最新快照")
    return fact_count
