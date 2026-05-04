"""
tdx_affair_client.py — 通达信 gpcw 财务文件同步

数据源：tdxhub Affair (gpcw 二进制财务文件)
内容：每季度全 A 股的 585 字段财务数据（三大报表 + 机构持仓 + 业绩预告）
存储：raw_gpcw_detail（只追加，按 report_date 分期）

单点计算原则：
机构持股明细（基金/险资/社保/QFII 等）和股东集中度只在本模块入库，
其他模块（holdings.py、scoring.py）只读取 raw_gpcw_detail 表。
"""

import logging
import os
import tempfile
import json
from typing import Any, Optional

from services.tdx_source import get_tdx_affair_class

logger = logging.getLogger("cm-api")

# gpcw 文件中我们关注的字段 → 入库列名映射
# 保持原始中文列名作为 key，英文短名作为 db column
_FIELD_MAP = {
    # ── 每股指标 ──
    "基本每股收益": "eps",
    "扣除非经常性损益每股收益": "eps_deducted",
    "每股净资产": "nav_per_share",
    "每股资本公积金": "capital_reserve_per_share",
    "每股经营现金流量": "ocf_per_share",
    "每股未分配利润": "undistributed_profit_per_share",
    "净资产收益率": "roe",
    "加权净资产收益率(每股指标)": "roe_weighted",
    # ── 利润表核心 ──
    "营业收入": "revenue",
    "营业利润": "operating_profit",
    "归属于母公司所有者的净利润": "net_profit",
    "扣除非经常性损益后的净利润": "net_profit_deducted",
    # ── 现金流 ──
    "经营活动产生的现金流量净额": "operating_cashflow",
    "投资活动产生的现金流量净额": "investing_cashflow",
    "筹资活动产生的现金流量净额": "financing_cashflow",
    # ── 资产负债 ──
    "资产总计": "total_assets",
    "流动资产合计": "current_assets",
    "非流动资产合计": "non_current_assets",
    "负债合计": "total_liabilities",
    "流动负债合计": "current_liabilities",
    "存货": "inventory",
    "货币资金": "cash",
    "应收账款": "accounts_receivable",
    # 合同负债（新会计准则后的"预收账款"，B2B 领先营收的关键前瞻指标）
    # 探测确认字段名是 "合同负债(万元)"（带单位后缀），单位万元
    "合同负债(万元)": "contract_liabilities_wan",
    "预收款项": "advance_receipts",  # 2017 前老科目，保留便于与 contract_liab 合并使用
    # ── 股本结构 ──
    "总股本": "total_shares",
    "已上市流通A股": "float_a_shares",
    "自由流通股(股)": "free_float_shares",
    "受限流通A股(股)": "restricted_a_shares",
    # ── 股东 ──
    "股东人数(户)": "holder_count",
    "第一大股东的持股数量": "top1_holder_shares",
    "十大流通股东持股数量合计(股)": "top10_float_holder_shares",
    "十大股东持股数量合计(股)": "top10_holder_shares",
    # ── 机构持股明细（核心） ──
    "机构总量（家）": "inst_total_count",
    "机构持股总量(股)": "inst_total_shares",
    "QFII机构数": "qfii_count",
    "QFII持股量": "qfii_shares",
    "券商机构数": "broker_count",
    "券商持股量": "broker_shares",
    "保险机构数": "insurance_count",
    "保险持股量": "insurance_shares",
    "基金机构数": "fund_count",
    "基金持股量": "fund_shares",
    "社保机构数": "social_security_count",
    "社保持股量": "social_security_shares",
    "私募机构数": "private_equity_count",
    "私募持股量": "private_equity_shares",
    "财务公司机构数": "finance_company_count",
    "财务公司持股量": "finance_company_shares",
    "年金机构数": "annuity_count",
    "年金持股量": "annuity_shares",
    "银行机构数(家)(机构持股)": "bank_count",
    "银行持股量(股)(机构持股)": "bank_shares",
    "一般法人机构数(家)(机构持股)": "general_corp_count",
    "一般法人持股量(股)(机构持股)": "general_corp_shares",
    "信托机构数(家)(机构持股)": "trust_count",
    "信托持股量(股)(机构持股)": "trust_shares",
    "特殊法人机构数(家)(机构持股)": "special_corp_count",
    "特殊法人持股量(股)(机构持股)": "special_corp_shares",
    "国家队持股数量（万股)": "national_team_shares_wan",
    # ── 业绩预告 ──
    "业绩预告-本期净利润同比增幅下限%": "forecast_profit_yoy_low",
    "业绩预告-本期净利润同比增幅上限%": "forecast_profit_yoy_high",
    "业绩预告-本期净利润下限(万元)": "forecast_profit_low_wan",
    "业绩预告-本期净利润上限(万元)": "forecast_profit_high_wan",
    "业绩预告公告日期 ": "forecast_announce_date",
    # ── 业绩快报 ──
    "归母净利润（业绩快报）": "express_net_profit",
    "扣非净利润（业绩快报）": "express_net_profit_deducted",
    "每股收益（业绩快报）": "express_eps",
    "摊薄净资产收益率（业绩快报）": "express_roe_diluted",
    # ── TTM / 近一年 ──
    "最近一年营业收入（万元）": "revenue_ttm_wan",
    "近一年归母净利润（万元）": "net_profit_ttm_wan",
    "近一年经营活动现金流净额": "ocf_ttm",
    "营业总收入TTM(万元)": "total_revenue_ttm_wan",
    # ── 其它 ──
    "员工总数(人)": "employee_count",
    "财报公告日期": "report_announce_date",
}

# 通达信对少数字段返回的是新口径列名或 colXXX 原始编号，需要在这里并口。
_FIELD_ALIASES_BY_DB_COLUMN = {
    db_column: (source_name,)
    for source_name, db_column in _FIELD_MAP.items()
}
_FIELD_ALIASES_BY_DB_COLUMN.update({
    "contract_liabilities": ("合同负债(万元)", "预收款项"),
    "revenue": ("营业收入", "其中：营业收入"),
    "operating_cost": ("其中：营业成本",),
    "operating_cost_single_quarter": ("col328",),
})

_NUMERIC_DB_COLUMNS = tuple(_FIELD_ALIASES_BY_DB_COLUMN.keys())
_SELECTED_GPCW_COLUMNS = ("report_date",) + tuple(
    dict.fromkeys(
        source_name
        for source_names in _FIELD_ALIASES_BY_DB_COLUMN.values()
        for source_name in source_names
    )
)

# DB 列定义（除 stock_code, report_date, ingested_at 外全部为 REAL）
_DB_COLUMNS = ["stock_code TEXT NOT NULL", "report_date TEXT NOT NULL"] + \
              [f"{column} REAL" for column in _NUMERIC_DB_COLUMNS] + \
              ["ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"]


def _ensure_table(conn: Any):
    cols_sql = ",\n    ".join(_DB_COLUMNS)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS raw_gpcw_detail (
            {cols_sql},
            PRIMARY KEY (stock_code, report_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_gpcw_report
        ON raw_gpcw_detail(report_date)
    """)
    # 前向兼容：如果 _FIELD_MAP / _FIELD_ALIASES_BY_DB_COLUMN 新增了字段但表已存在，
    # 自动 ALTER TABLE ADD COLUMN (覆盖全部 DB 列, 不只 _FIELD_MAP.values())
    existing = {
        row["column_name"] if hasattr(row, "keys") else row[0]
        for row in conn.execute("DESCRIBE raw_gpcw_detail").fetchall()
    }
    for col in _NUMERIC_DB_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE raw_gpcw_detail ADD COLUMN {col} REAL")
            logger.info(f"[gpcw] ALTER TABLE: 新增字段 {col}")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_tdx_gpcw_wide (
            stock_code TEXT NOT NULL,
            report_date TEXT NOT NULL,
            source_file TEXT,
            field_values_json TEXT NOT NULL,
            parser_version TEXT DEFAULT 'tdxhub_gpcw_v1',
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, report_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_raw_tdx_gpcw_wide_report
        ON raw_tdx_gpcw_wide(report_date)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dim_tdx_gpcw_field (
            field_key TEXT PRIMARY KEY,
            field_index INTEGER,
            zh_name TEXT NOT NULL,
            db_column TEXT,
            unit TEXT,
            field_family TEXT,
            model_candidate BOOLEAN DEFAULT FALSE,
            verified BOOLEAN DEFAULT FALSE,
            notes TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _upsert_gpcw_field_dict(conn)
    conn.commit()


def _normalize_report_date(val) -> Optional[str]:
    """将 gpcw 的 report_date (float like 20250930.0) 转为 '2025-09-30' 格式."""
    if val is None:
        return None
    try:
        s = str(int(float(val)))
        if len(s) == 8:
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    except (ValueError, TypeError):
        pass
    return None


def _safe_float(val) -> Optional[float]:
    """安全转 float，NaN / inf → None. 如果遇到 Series（重复列名），取第一个值。"""
    if val is None:
        return None
    try:
        import math
        # pandas Series (duplicate column names in gpcw)
        if hasattr(val, 'iloc'):
            val = val.iloc[0]
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _pick_first_numeric_value(row, source_names: tuple[str, ...]) -> Optional[float]:
    for source_name in source_names:
        value = _safe_float(row.get(source_name))
        if value is not None:
            return value
    return None


def _load_financial_columns() -> list[str]:
    try:
        from tdxhub.financial.columns import columns as financial_columns

        return list(financial_columns)
    except Exception:
        return list(_SELECTED_GPCW_COLUMNS)


def _unit_for_field(name: str) -> Optional[str]:
    if "%" in name:
        return "%"
    if "万元" in name:
        return "万元"
    if "万股" in name:
        return "万股"
    if "股" in name or "股本" in name or "持股量" in name:
        return "股"
    if "人" in name or "户" in name or "家" in name:
        return "count"
    return None


def _family_for_field(name: str) -> str:
    if any(k in name for k in ("股东", "持股", "机构", "QFII", "基金", "社保", "国家队")):
        return "ownership"
    if any(k in name for k in ("预告", "快报")):
        return "forecast_express"
    if any(k in name for k in ("现金流", "合同", "应收", "存货", "负债", "资产")):
        return "fundamental_quality"
    if any(k in name for k in ("收入", "利润", "收益", "ROE", "净资产收益率")):
        return "profit_growth"
    return "other"


_STRONG_MODEL_FIELDS = {
    "股东人数(户)",
    "十大流通股东持股数量合计(股)",
    "十大股东持股数量合计(股)",
    "机构持股总量(股)",
    "国家队持股数量（万股)",
    "QFII持股量",
    "基金持股量",
    "合同负债(万元)",
    "预收款项",
    "营业收入",
    "其中：营业收入",
    "经营活动产生的现金流量净额",
    "归属于母公司所有者的净利润",
    "业绩预告-本期净利润同比增幅下限%",
    "业绩预告-本期净利润同比增幅上限%",
}


def _upsert_gpcw_field_dict(conn: Any) -> None:
    mapped_by_source = dict(_FIELD_MAP)
    for db_col, source_names in _FIELD_ALIASES_BY_DB_COLUMN.items():
        for source_name in source_names:
            mapped_by_source[source_name] = db_col

    rows = []
    for idx, name in enumerate(_load_financial_columns()):
        if name == "report_date":
            continue
        rows.append((
            f"f{idx:03d}",
            idx,
            name,
            mapped_by_source.get(name),
            _unit_for_field(name),
            _family_for_field(name),
            name in _STRONG_MODEL_FIELDS,
            name in mapped_by_source,
            "tdxhub financial.columns",
        ))
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO dim_tdx_gpcw_field
        (field_key, field_index, zh_name, db_column, unit, field_family,
         model_candidate, verified, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _jsonable_value(value: Any) -> Any:
    numeric = _safe_float(value)
    if numeric is not None:
        return numeric
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if hasattr(value, "iloc"):
        value = value.iloc[0]
    text = str(value).strip()
    return text or None


def _insert_wide_rows(conn: Any, df: Any, filename: str, fallback_report_date: Optional[str]) -> int:
    if df is None or df.empty:
        return 0
    rows = []
    for code in df.index:
        row = df.loc[code]
        rd = _normalize_report_date(row.get("report_date")) or fallback_report_date
        if not rd:
            continue
        payload = {}
        for col in df.columns:
            if col == "report_date":
                continue
            val = _jsonable_value(row.get(col))
            if val is not None:
                payload[str(col)] = val
        rows.append((
            str(code),
            rd,
            filename,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ))
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO raw_tdx_gpcw_wide
            (stock_code, report_date, source_file, field_values_json)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def backfill_gpcw_wide_from_detail(conn: Any) -> dict:
    """Populate raw_tdx_gpcw_wide from existing raw_gpcw_detail without network IO.

    This is a compatibility backfill. Future ``sync_gpcw_files`` calls store the
    wider TDX parse payload directly.
    """

    _ensure_table(conn)
    cols = ["stock_code", "report_date", *list(_NUMERIC_DB_COLUMNS)]
    cursor = conn.execute(f"SELECT {', '.join(cols)} FROM raw_gpcw_detail")
    rows = cursor.fetchall()
    payload_rows = []
    for row in rows:
        if hasattr(row, "keys"):
            rec = {k: row[k] for k in row.keys()}
        else:
            rec = dict(zip(cols, row))
        payload = {
            col: _jsonable_value(rec.get(col))
            for col in cols[2:]
            if _jsonable_value(rec.get(col)) is not None
        }
        payload_rows.append((
            rec["stock_code"],
            rec["report_date"],
            "raw_gpcw_detail_backfill",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ))
    if payload_rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO raw_tdx_gpcw_wide
            (stock_code, report_date, source_file, field_values_json)
            VALUES (?, ?, ?, ?)
            """,
            payload_rows,
        )
        conn.commit()
    return {"rows_upserted": len(payload_rows), "source": "raw_gpcw_detail_backfill"}


def sync_gpcw_files(
    conn: Any,
    quarters: int = 4,
    downdir: Optional[str] = None,
    force_resync: bool = False,
    persist_wide: bool = True,
) -> dict:
    """
    同步最近 N 个季度的 gpcw 文件到 raw_gpcw_detail 表。

    Parameters
    ----------
    conn : 当前 DuckDB 业务库连接
    quarters : 要同步的最近季度数，默认 4（一年）
    downdir : gpcw 文件下载目录，默认系统临时目录
    force_resync : True 时忽略 existing_dates 跳过逻辑，对所有 target 季度重新下载并
                   INSERT OR REPLACE 已有行（用于 _FIELD_MAP 新增字段后回填历史数据）

    Returns
    -------
    dict : { 'files_synced': int, 'rows_upserted': int, 'errors': list }
    """
    Affair = get_tdx_affair_class()
    if Affair is None:
        logger.error("[gpcw] tdxhub 未安装，无法同步 gpcw 数据")
        return {"files_synced": 0, "rows_upserted": 0, "errors": ["tdxhub not installed"]}

    _ensure_table(conn)

    if downdir is None:
        downdir = os.path.join(tempfile.gettempdir(), "gpcw_cache")
    os.makedirs(downdir, exist_ok=True)

    # 获取文件列表，按文件名倒序排，取最近 N 个有实际数据的（size > 50KB）
    all_files = Affair.files()
    real_files = [f for f in all_files if f.get("filesize", 0) > 50_000]
    real_files.sort(key=lambda x: x["filename"], reverse=True)
    target_files = real_files[:quarters]

    logger.info(f"[gpcw] 准备同步 {len(target_files)} 个季度文件")

    # 检查已入库的 report_date 列表（force_resync=True 时跳过此检查，对所有目标季度重拉）
    existing_dates = set()
    if not force_resync:
        try:
            rows = conn.execute("SELECT DISTINCT report_date FROM raw_gpcw_detail").fetchall()
            existing_dates = {r[0] for r in rows}
        except Exception:
            pass
    else:
        logger.info("[gpcw] force_resync=True：将重新下载所有目标季度文件")

    db_col_names = ["stock_code", "report_date"] + list(_NUMERIC_DB_COLUMNS)
    placeholders = ",".join(["?"] * len(db_col_names))
    col_list = ",".join(db_col_names)
    upsert_sql = f"""
        INSERT OR REPLACE INTO raw_gpcw_detail ({col_list})
        VALUES ({placeholders})
    """

    result = {"files_synced": 0, "rows_upserted": 0, "wide_rows_upserted": 0, "errors": []}

    for file_info in target_files:
        filename = file_info["filename"]
        # 从文件名推断 report_date: gpcw20250930.zip → 2025-09-30
        date_part = filename.replace("gpcw", "").replace(".zip", "").replace(".dat", "")
        report_date = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:8]}" if len(date_part) == 8 else None

        if report_date and report_date in existing_dates:
            logger.info(f"[gpcw] {filename} (report_date={report_date}) 已存在，跳过")
            continue

        try:
            logger.info(f"[gpcw] 下载并解析 {filename} ...")
            df = Affair.parse(
                downdir=downdir,
                filename=filename,
                columns=None if persist_wide else _SELECTED_GPCW_COLUMNS,
            )

            if df is None or df.empty:
                logger.warning(f"[gpcw] {filename} 解析为空")
                result["errors"].append(f"{filename}: empty")
                continue

            rows_batch = []
            for code in df.index:
                row = df.loc[code]
                rd = _normalize_report_date(row.get("report_date")) or report_date
                values = [str(code), rd]
                for source_names in _FIELD_ALIASES_BY_DB_COLUMN.values():
                    values.append(_pick_first_numeric_value(row, source_names))
                rows_batch.append(tuple(values))

            conn.executemany(upsert_sql, rows_batch)
            if persist_wide:
                result["wide_rows_upserted"] += _insert_wide_rows(
                    conn, df, filename, report_date
                )
            conn.commit()

            result["files_synced"] += 1
            result["rows_upserted"] += len(rows_batch)
            logger.info(f"[gpcw] {filename}: {len(rows_batch)} stocks synced")

        except Exception as exc:
            logger.error(f"[gpcw] {filename} 同步失败: {exc}")
            result["errors"].append(f"{filename}: {exc}")

    return result


def get_latest_institutional_holdings(
    conn: Any,
    stock_codes: Optional[list] = None,
) -> dict:
    """
    获取最新一期的机构持股明细。

    Returns
    -------
    dict : { stock_code: { inst_total_count, fund_count, fund_shares, ... } }
    """
    _ensure_table(conn)

    inst_cols = [
        "inst_total_count", "inst_total_shares",
        "qfii_count", "qfii_shares",
        "broker_count", "broker_shares",
        "insurance_count", "insurance_shares",
        "fund_count", "fund_shares",
        "social_security_count", "social_security_shares",
        "private_equity_count", "private_equity_shares",
        "trust_count", "trust_shares",
        "bank_count", "bank_shares",
        "national_team_shares_wan",
        "holder_count", "total_shares", "float_a_shares", "free_float_shares",
        "top1_holder_shares", "top10_float_holder_shares",
    ]

    col_list = ", ".join(["stock_code", "report_date"] + inst_cols)

    if stock_codes:
        placeholders = ",".join(["?"] * len(stock_codes))
        sql = f"""
            SELECT {col_list} FROM raw_gpcw_detail
            WHERE report_date = (SELECT MAX(report_date) FROM raw_gpcw_detail)
              AND stock_code IN ({placeholders})
        """
        rows = conn.execute(sql, stock_codes).fetchall()
    else:
        sql = f"""
            SELECT {col_list} FROM raw_gpcw_detail
            WHERE report_date = (SELECT MAX(report_date) FROM raw_gpcw_detail)
        """
        rows = conn.execute(sql).fetchall()

    col_names = ["stock_code", "report_date"] + inst_cols
    result = {}
    for row in rows:
        d = dict(zip(col_names, row))
        code = d.pop("stock_code")
        result[code] = d
    return result


def get_gpcw_financial_snapshot(
    conn: Any,
    stock_code: str,
    limit: int = 8,
) -> list[dict]:
    """
    获取某只股票最近 N 期的 gpcw 财务快照（按 report_date 倒序）。
    """
    _ensure_table(conn)

    cols = ["stock_code", "report_date"] + list(_NUMERIC_DB_COLUMNS)
    col_list = ", ".join(cols)
    rows = conn.execute(
        f"SELECT {col_list} FROM raw_gpcw_detail WHERE stock_code = ? ORDER BY report_date DESC LIMIT ?",
        (stock_code, limit),
    ).fetchall()

    return [dict(zip(cols, row)) for row in rows]
