#!/usr/bin/env python3
"""Phase 1 子任务 2: tdxhub Affair.parse 季度财务全量入库 → fact_fundamental_quarterly

数据源: tdxhub.Affair(filename=gpcw<YYYYMMDD>.zip)
  - gpcw<YYYYMMDD>.zip 按季末日期命名 (每季度 1 份)
  - 通达信官方财务服务器 120.76.152.87 聚合三大报表 + 机构持仓 + 业绩预告
  - 583 列, 我们只保留对建模有价值的 ~35 个核心列

覆盖范围:
  - 文件: 1988-12 ~ 2026-09 共 147 份, ~3.8 份/年
  - 最新完整披露季度: 2024-12 (2025-Q1 起逐步披露)
  - 只入库 2020-03 起的季度 (与 price_kline_tdxhub 覆盖对齐, 2019-08+)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

STOCK_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(STOCK_ROOT / "tdxhub"))

from tdxhub.affair import Affair

from services.db import get_conn

logger = logging.getLogger("fundamental_quarterly")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


# 从 583 列里选建模核心字段 (按主题分组)
CORE_FIELDS = {
    # 每股指标
    "基本每股收益": "eps_basic",
    "扣除非经常性损益每股收益": "eps_deducted",
    "每股未分配利润": "undist_profit_per_share",
    "每股净资产": "book_value_per_share",
    "每股经营现金流量": "ocf_per_share",
    # 盈利能力
    "净资产收益率": "roe",
    "加权净资产收益率(每股指标)": "roe_weighted",
    # 股东 / 机构持股 (重点!)
    "股东人数(户)": "shareholder_count",
    "机构总量（家）": "inst_count",
    "机构持股总量(股)": "inst_holding_shares",
    "QFII机构数": "qfii_count",
    "QFII持股量": "qfii_shares",
    "基金机构数": "fund_count",
    "基金持股量": "fund_shares",
    "社保机构数": "ssf_count",
    "社保持股量": "ssf_shares",
    "保险机构数": "insurance_count",
    "保险持股量": "insurance_shares",
    "私募机构数": "pe_count",
    "私募持股量": "pe_shares",
    "券商机构数": "broker_count",
    "券商持股量": "broker_shares",
    "第一大股东的持股数量": "top1_shareholder_shares",
    "十大股东持股数量合计(股)": "top10_shareholder_shares",
    "十大流通股东持股数量合计(股)": "top10_float_shareholder_shares",
    "国家队持股数量（万股)": "national_team_shares_10k",
    # 业绩预告 (重点!)
    "业绩预告-本期净利润同比增幅下限%": "yjyg_lower_pct",
    "业绩预告-本期净利润同比增幅上限%": "yjyg_upper_pct",
    # 业绩快报 (重点!)
    "每股收益（业绩快报）": "yjkb_eps",
    "归母净利润（业绩快报）": "yjkb_net_profit",
    "扣非净利润（业绩快报）": "yjkb_net_profit_deducted",
    "加权净资产收益率（业绩快报）": "yjkb_roe_weighted",
    # 股本与流通
    "总股本": "total_shares",
    "已上市流通A股": "float_a_shares",
    "自由流通股(股)": "free_float_shares",
    # 利润表关键
    "营业收入(万元)": "revenue_10k",
    "净利润(万元)": "net_profit_10k",
    "归母净利润（扣非）": "net_profit_deducted_parent",
    # 资产负债表关键
    "资产总计(万元)": "total_assets_10k",
    "负债合计(万元)": "total_liab_10k",
    "归属于母公司股东权益(资产负债表)": "equity_parent",
    # 现金流
    "经营活动产生的现金流量净额(万元)": "ocf_10k",
}


DDL = """
CREATE TABLE IF NOT EXISTS fact_fundamental_quarterly (
    stock_code TEXT NOT NULL,
    report_date TEXT NOT NULL,
    {extra_cols}
    built_at TEXT,
    PRIMARY KEY (stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_ffq_code ON fact_fundamental_quarterly(stock_code);
CREATE INDEX IF NOT EXISTS idx_ffq_date ON fact_fundamental_quarterly(report_date);
"""

MANIFEST_DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_file_manifest (
    filename          TEXT PRIMARY KEY,
    report_date       TEXT,
    source_name       TEXT DEFAULT 'tdxhub_gpcw',
    source_tier       SMALLINT DEFAULT 1,
    file_size         BIGINT,
    file_list_hash    TEXT,
    download_sha256   TEXT,
    parser_version    TEXT DEFAULT 'tdxhub_affair_v1',
    parse_status      TEXT,
    row_count         INTEGER DEFAULT 0,
    last_checked_at   TEXT,
    downloaded_at     TEXT,
    parsed_at         TEXT,
    error_message     TEXT,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_manifest_report
    ON mart_tdx_gpcw_file_manifest(report_date);
CREATE INDEX IF NOT EXISTS idx_tdx_gpcw_manifest_status
    ON mart_tdx_gpcw_file_manifest(parse_status);
"""


def build_ddl() -> str:
    extra = ",\n    ".join(f"{v} REAL" for v in CORE_FIELDS.values())
    return DDL.format(extra_cols=extra + ",\n    ")


def ensure_manifest_schema(conn) -> None:
    conn.executescript(MANIFEST_DDL)


def _report_date_from_filename(filename: str) -> str:
    return filename[4:12]


def _file_list_hash(filename: str, file_size: int | None) -> str:
    payload = f"{filename}|{int(file_size or 0)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(conn) -> dict[str, dict]:
    ensure_manifest_schema(conn)
    return {
        row["filename"]: dict(zip(row.keys(), row))
        for row in conn.execute("SELECT * FROM mart_tdx_gpcw_file_manifest").fetchall()
    }


def should_process_file(file_info: dict, manifest: dict[str, dict], *, force: bool = False) -> bool:
    if force:
        return True
    filename = file_info["filename"]
    existing = manifest.get(filename)
    if not existing:
        return True
    return not (
        existing.get("parse_status") == "success"
        and existing.get("file_list_hash") == _file_list_hash(filename, file_info.get("filesize"))
    )


def upsert_manifest(
    conn,
    *,
    filename: str,
    file_size: int | None,
    file_list_hash: str,
    parse_status: str,
    row_count: int = 0,
    download_sha256: str | None = None,
    error_message: str | None = None,
    downloaded_at: str | None = None,
    parsed_at: str | None = None,
) -> None:
    ensure_manifest_schema(conn)
    now = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_tdx_gpcw_file_manifest (
            filename, report_date, source_name, source_tier, file_size,
            file_list_hash, download_sha256, parser_version, parse_status,
            row_count, last_checked_at, downloaded_at, parsed_at,
            error_message, updated_at
        ) VALUES (?, ?, 'tdxhub_gpcw', 1, ?, ?, ?, 'tdxhub_affair_v1', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            _report_date_from_filename(filename),
            int(file_size or 0),
            file_list_hash,
            download_sha256,
            parse_status,
            int(row_count or 0),
            now,
            downloaded_at,
            parsed_at,
            error_message,
            now,
        ),
    )


def _is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value) -> float | None:
    if _is_missing(value) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _records_from_table(payload) -> list[dict]:
    if payload is None or bool(getattr(payload, "empty", False)):
        return []
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            rows_by_index = to_dict("index")
        except TypeError:
            rows_by_index = None
        if isinstance(rows_by_index, dict):
            return [
                {"__index__": index, **dict(row)}
                for index, row in rows_by_index.items()
            ]
        try:
            return [dict(row) for row in to_dict("records")]
        except TypeError:
            pass
    try:
        return [dict(row) for row in payload]
    except TypeError:
        return []


def _normalize_quarter_rows(payload, report_date: str) -> list[dict]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for source_row in _records_from_table(payload):
        raw_code = source_row.get("__index__") or source_row.get("stock_code")
        if raw_code in (None, ""):
            continue
        code = str(raw_code).zfill(6)
        row = {"stock_code": code, "report_date": report_date}
        for source_col, target_col in CORE_FIELDS.items():
            if source_col in source_row:
                row[target_col] = _to_float(source_row.get(source_col))
        key = (row["stock_code"], row["report_date"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def parse_one_quarter_with_meta(tmpdir: str, filename: str) -> tuple[list[dict], dict]:
    meta = {
        "filename": filename,
        "download_sha256": None,
        "bytes_len": 0,
        "downloaded_at": None,
        "parsed_at": None,
        "status": "unknown",
        "error": None,
    }
    try:
        Affair.fetch(downdir=tmpdir, filename=filename)
    except Exception as e:
        logger.warning("fetch %s 失败: %s", filename, e)
        meta.update({"status": "fetch_failed", "error": str(e)})
        return [], meta
    path = os.path.join(tmpdir, filename)
    if not os.path.exists(path) or os.path.getsize(path) < 10_000:  # 占位或空文件
        meta.update({
            "status": "empty_or_placeholder",
            "bytes_len": os.path.getsize(path) if os.path.exists(path) else 0,
        })
        return [], meta
    meta["bytes_len"] = os.path.getsize(path)
    meta["download_sha256"] = _sha256_file(path)
    meta["downloaded_at"] = datetime.utcnow().isoformat()
    try:
        table = Affair.parse(downdir=tmpdir, filename=filename)
    except Exception as e:
        logger.warning("parse %s 失败: %s", filename, e)
        meta.update({"status": "parse_failed", "error": str(e)})
        return [], meta
    rows = _normalize_quarter_rows(table, _report_date_from_filename(filename))
    meta["parsed_at"] = datetime.utcnow().isoformat()
    meta["status"] = "success" if rows else "no_rows"
    # 清理
    os.remove(path)
    meta["row_count"] = len(rows)
    return rows, meta


def parse_one_quarter(tmpdir: str, filename: str) -> list[dict]:
    rows, _meta = parse_one_quarter_with_meta(tmpdir, filename)
    return rows


def insert_quarter_rows(conn, rows: list[dict], built_at: str) -> int:
    if not rows:
        return 0
    cols = ['stock_code', 'report_date'] + list(CORE_FIELDS.values()) + ['built_at']
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO fact_fundamental_quarterly
        ({', '.join(cols)})
        VALUES ({placeholders})
        """,
        [
            tuple(
                built_at if col == "built_at" else row.get(col)
                for col in cols
            )
            for row in rows
        ],
    )
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='20230101',
                        help='只入库 report_date >= 此值的季度 (默认 2020-03 起)')
    parser.add_argument('--end', default='',
                        help='报告期上限 (默认不限)')
    parser.add_argument('--truncate', action='store_true',
                        help='清空 fact_fundamental_quarterly 后重建')
    parser.add_argument('--limit-files', type=int, default=0,
                        help='调试: 只处理前 N 份')
    parser.add_argument('--force-changed', action='store_true',
                        help='忽略 mart_tdx_gpcw_file_manifest, 强制重新下载/解析命中的文件')
    args = parser.parse_args()

    conn = get_conn()
    conn.executescript(build_ddl())
    ensure_manifest_schema(conn)

    if args.truncate:
        conn.execute("DELETE FROM fact_fundamental_quarterly")
        conn.commit()
        logger.info("fact_fundamental_quarterly 已清空")

    files = Affair.files()
    # 只处理 gpcw<YYYYMMDD>.zip, filesize > 100KB (排除占位 164B)
    manifest = load_manifest(conn)
    queue = []
    skipped = 0
    for f in files:
        name = f['filename']
        if not name.startswith('gpcw'): continue
        if f.get('filesize', 0) < 100_000: continue
        date = name[4:12]
        if date < args.start: continue
        if args.end and date > args.end: continue
        if should_process_file(f, manifest, force=args.force_changed or args.truncate):
            queue.append(f)
        else:
            skipped += 1
    queue.sort(key=lambda item: item["filename"])  # 按日期
    if args.limit_files > 0:
        queue = queue[:args.limit_files]
    logger.info("共 %d 份季报待解析 (>=%s), manifest 跳过 %d 份", len(queue), args.start, skipped)

    tmpdir = tempfile.mkdtemp(prefix='tdxhub_ffq_')
    logger.info("tmpdir: %s", tmpdir)

    built_at = datetime.utcnow().isoformat()
    t0 = time.time()
    n_total = 0
    for i, item in enumerate(queue):
        fname = item["filename"]
        file_size = int(item.get("filesize") or 0)
        list_hash = _file_list_hash(fname, file_size)
        rows, meta = parse_one_quarter_with_meta(tmpdir, fname)
        if not rows:
            upsert_manifest(
                conn,
                filename=fname,
                file_size=file_size,
                file_list_hash=list_hash,
                download_sha256=meta.get("download_sha256"),
                parse_status=meta.get("status") or "no_rows",
                row_count=0,
                error_message=meta.get("error"),
                downloaded_at=meta.get("downloaded_at"),
                parsed_at=meta.get("parsed_at"),
            )
            conn.commit()
            logger.info("%s 无数据或 fetch/parse 失败", fname)
            continue
        inserted = insert_quarter_rows(conn, rows, built_at)
        upsert_manifest(
            conn,
            filename=fname,
            file_size=file_size,
            file_list_hash=list_hash,
            download_sha256=meta.get("download_sha256"),
            parse_status="success",
            row_count=inserted,
            downloaded_at=meta.get("downloaded_at"),
            parsed_at=meta.get("parsed_at"),
        )
        n_total += inserted
        logger.info("[%d/%d] %s rows=%d  累计 %d", i + 1, len(queue), fname, inserted, n_total)
        conn.commit()

    dt = time.time() - t0
    logger.info("=" * 50)
    logger.info("完成 %d 份, 累计 %d 行, 耗时 %.1f 分钟", len(queue), n_total, dt / 60)

    # 全局统计
    row = conn.execute("""
        SELECT MIN(report_date), MAX(report_date),
               COUNT(DISTINCT stock_code), COUNT(DISTINCT report_date)
        FROM fact_fundamental_quarterly
    """).fetchone()
    logger.info("fact_fundamental_quarterly: %s ~ %s, 股票 %d, 季度 %d", *row)

    conn.close()


if __name__ == "__main__":
    main()
