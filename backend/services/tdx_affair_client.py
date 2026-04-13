"""
tdx_affair_client.py — 通达信 gpcw 财务文件同步

数据源：mootdx Affair (gpcw 二进制财务文件)
内容：每季度全 A 股的 585 字段财务数据（三大报表 + 机构持仓 + 业绩预告）
存储：raw_gpcw_detail（只追加，按 report_date 分期）

单点计算原则：
机构持股明细（基金/险资/社保/QFII 等）和股东集中度只在本模块入库，
其他模块（holdings.py、scoring.py）只读取 raw_gpcw_detail 表。
"""

import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

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

_SELECTED_GPCW_COLUMNS = ("report_date",) + tuple(_FIELD_MAP.keys())

# DB 列定义（除 stock_code, report_date, ingested_at 外全部为 REAL）
_DB_COLUMNS = ["stock_code TEXT NOT NULL", "report_date TEXT NOT NULL"] + \
              [f"{v} REAL" for v in _FIELD_MAP.values()] + \
              ["ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"]


def _ensure_table(conn: sqlite3.Connection):
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


def sync_gpcw_files(
    conn: sqlite3.Connection,
    quarters: int = 4,
    downdir: Optional[str] = None,
) -> dict:
    """
    同步最近 N 个季度的 gpcw 文件到 raw_gpcw_detail 表。

    Parameters
    ----------
    conn : 数据库连接（smartmoney.db）
    quarters : 要同步的最近季度数，默认 4（一年）
    downdir : gpcw 文件下载目录，默认系统临时目录

    Returns
    -------
    dict : { 'files_synced': int, 'rows_upserted': int, 'errors': list }
    """
    Affair = get_tdx_affair_class()
    if Affair is None:
        logger.error("[gpcw] mootdx 未安装，无法同步 gpcw 数据")
        return {"files_synced": 0, "rows_upserted": 0, "errors": ["mootdx not installed"]}

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

    # 检查已入库的 report_date 列表
    existing_dates = set()
    try:
        rows = conn.execute("SELECT DISTINCT report_date FROM raw_gpcw_detail").fetchall()
        existing_dates = {r[0] for r in rows}
    except Exception:
        pass

    db_col_names = ["stock_code", "report_date"] + list(_FIELD_MAP.values())
    placeholders = ",".join(["?"] * len(db_col_names))
    col_list = ",".join(db_col_names)
    upsert_sql = f"""
        INSERT OR REPLACE INTO raw_gpcw_detail ({col_list})
        VALUES ({placeholders})
    """

    result = {"files_synced": 0, "rows_upserted": 0, "errors": []}

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
                columns=_SELECTED_GPCW_COLUMNS,
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
                for cn_name in _FIELD_MAP:
                    values.append(_safe_float(row.get(cn_name)))
                rows_batch.append(tuple(values))

            conn.executemany(upsert_sql, rows_batch)
            conn.commit()

            result["files_synced"] += 1
            result["rows_upserted"] += len(rows_batch)
            logger.info(f"[gpcw] {filename}: {len(rows_batch)} stocks synced")

        except Exception as exc:
            logger.error(f"[gpcw] {filename} 同步失败: {exc}")
            result["errors"].append(f"{filename}: {exc}")

    return result


def get_latest_institutional_holdings(
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
    stock_code: str,
    limit: int = 8,
) -> list[dict]:
    """
    获取某只股票最近 N 期的 gpcw 财务快照（按 report_date 倒序）。
    """
    _ensure_table(conn)

    cols = ["stock_code", "report_date"] + list(_FIELD_MAP.values())
    col_list = ", ".join(cols)
    rows = conn.execute(
        f"SELECT {col_list} FROM raw_gpcw_detail WHERE stock_code = ? ORDER BY report_date DESC LIMIT ?",
        (stock_code, limit),
    ).fetchall()

    return [dict(zip(cols, row)) for row in rows]
