"""org_holding_aif10.py — 机构持仓明细 (东方财富妙想 aif10 RPT_MAIN_ORGHOLDDETAIL)

源决策: 用户 2026-06-24 拍板 — 退役冻结的 tdx F10 fact_common_major_holder_stock,
改接 aif10 机构持仓明细 (更优: 3624条深/ORG_TYPE分桶/基金级, vs tdx 仅3期浅)。
这是 §4.3 "tushare唯一" 的 aif10 例外扩展 (理由: tushare 无机构持仓明细等价, 实测确认)。

按数据模块分层 (获取/清洗/加工/存储 各司其职), 范例 = qfii_client (同报告期形态, 已 aif10 接):
  ① 获取 acquire : _fetch_period       — aif10 datacenter 拉某报告期全市场机构持仓 (by-period batch)
  ② 清洗 clean   : _normalize_rows     — 字段映射 + 报告期/可用日锚 + grain
  ③ 存储 store   : _upsert_rows        — 幂等 upsert raw_org_holding_aif10

PIT 锚 (关键, 真金白银): MAIN_ORGHOLDDETAIL 只有 REPORT_DATE (报告期), 无 NOTICE_DATE.
机构持仓是季报派生 → 可用日 = 该报告期的法定披露截止日 (保守上界, 数据不晚于此可得):
  Q1(03-31)→04-30 同年 / H1(06-30)→08-31 同年 / Q3(09-30)→10-31 同年 / 年报(12-31)→次年04-30.
这是监管硬上界 (非估计), event_engine/特征层 JOIN 必用 available_date 防穿越 (报告期 != 可用日).

历史范围: 跟 K 线周期一致 (price_kline_qfq_tushare 2019-01-02 起) → 回到覆盖它的年报 20181231,
更早无 K 线无法回测 (用户 2026-06-24, 同 holders_aif10)。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# aif10_scraper (东财妙想) 在姊妹项目 ../miaoxiang
_MIAOXIANG = Path(__file__).resolve().parents[2] / "miaoxiang"
if str(_MIAOXIANG) not in sys.path:
    sys.path.insert(0, str(_MIAOXIANG))

logger = logging.getLogger("cm-api")

REPORT_NAME = "RPT_MAIN_ORGHOLDDETAIL"     # 机构持仓明细 (按机构)
SOURCE = "miaoxiang"                        # aif10 datacenter
SOURCE_TIER = 1                             # evidence: 2026-06-24 用户裁决 aif10 例外扩展 (替 tdx F10)
# 全市场机构持仓单期 ~83万行 (2025年报实测) → page_size 2000 减页数 (417页 vs 1665页@500),
# 配高 retry+长 timeout 抗深翻页连接掐断 ("Response ended prematurely" 实测 @500/depth 224)
PAGE_SIZE = 2000                            # evidence: API 实测接受 2000/页, count=832896 时 417 页
FETCH_RETRY = 5                             # evidence: 深翻页易断, 高重试退避 (qfii 窄子集无此问题)
FETCH_TIMEOUT = 60
# K线对齐: price_kline_qfq_tushare 2019-01-02 起 → 机构持仓回到覆盖它的年报 20181231
DEFAULT_START_PERIOD = "2018-12-31"         # evidence: K线起点 2019-01-02 (用户 2026-06-24)
QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")
# 季报派生数据距报告期末足量披露的保守滞后 (latest_plannable 用); 法定截止最长 ~4个月 (年报)
PLANNABLE_LAG_DAYS = 130


# ── helpers ──────────────────────────────────────────────────────────
def _normalize_date(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"} or text in {"--", "-"}:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        digits = digits[:8]
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _parse_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return None if value != value else float(value)  # NaN guard
        except Exception:  # noqa: BLE001
            return None
    text = str(value).strip().replace(",", "").replace("%", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none", "null"} or text in {"--", "-"}:
        return None
    try:
        return float(text)
    except Exception:  # noqa: BLE001
        return None


def _normalize_stock_code(value) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[-6:].zfill(6) if digits else None


def disclosure_deadline(report_date: str) -> Optional[str]:
    """报告期 → 法定披露截止日 (PIT 保守可用日上界, 监管硬约束非估计).

    A股: Q1(03-31)→04-30 同年 / H1(06-30)→08-31 同年 / Q3(09-30)→10-31 同年 / 年报(12-31)→次年04-30.
    数据在该日之前必已可得 → 用它当 available_date 是 PIT-conservative (绝不超前可见).
    """
    nd = _normalize_date(report_date)
    if not nd:
        return None
    y, md = nd[:4], nd[5:]
    deadline = {
        "03-31": f"{y}-04-30",
        "06-30": f"{y}-08-31",
        "09-30": f"{y}-10-31",
        "12-31": f"{int(y) + 1}-04-30",
    }.get(md)
    return deadline


def enumerate_quarter_ends(start_date: str, end_date: str) -> list[str]:
    """返回 [start_date, end_date] 区间所有季度末 (YYYY-MM-DD)。"""
    start = date.fromisoformat(_normalize_date(start_date))
    end = date.fromisoformat(_normalize_date(end_date))
    out = []
    for year in range(start.year, end.year + 1):
        for md in QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            if start <= d <= end:
                out.append(d.strftime("%Y-%m-%d"))
    return out


def latest_plannable_report_date(today: Optional[date] = None) -> Optional[str]:
    """相对 today 最近"足量披露"的季度末 (距今 >= PLANNABLE_LAG_DAYS 天)。"""
    today = today or datetime.now(timezone.utc).date()  # rule-compliance: ok evidence=季报增量默认now
    from datetime import timedelta
    cutoff = today - timedelta(days=PLANNABLE_LAG_DAYS)
    latest = None
    for year in (cutoff.year, cutoff.year - 1):
        for md in QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            if d <= cutoff and (latest is None or d > latest):
                latest = d
    return latest.strftime("%Y-%m-%d") if latest else None


# ── ④ 存储 store: schema ─────────────────────────────────────────────
_DDL = (
    """
    CREATE TABLE IF NOT EXISTS raw_org_holding_aif10 (
        report_date          TEXT NOT NULL,   -- 报告期 YYYY-MM-DD
        available_date       TEXT,            -- PIT 可用日 (法定披露截止, 防穿越锚)
        stock_code           TEXT NOT NULL,
        stock_name           TEXT,
        org_type_code        TEXT,            -- ORG_TYPE (00合计/07法人/...)
        org_type_name        TEXT,            -- F9_ORGTYPE_NAME (基金/保险/券商/QFII/...)
        holder_code          TEXT NOT NULL,
        holder_name          TEXT,
        fund_code            TEXT,
        fund_derivecode      TEXT NOT NULL,   -- 同机构多基金产品区分 (null→'')
        fund_manager         TEXT,
        fund_type            TEXT,
        total_shares         REAL,            -- 持股数
        hold_value           REAL,            -- 持仓市值
        total_shares_ratio   REAL,            -- 占总股本 %
        free_shares_ratio    REAL,            -- 占流通股 %
        free_market_cap      REAL,
        free_shares          REAL,
        fsr_change           REAL,            -- 流通持股变动 (FSR_CHANGE)
        fsr_rate_change      REAL,            -- 流通持股变动比例
        change_type          TEXT,            -- 增/减/新进/不变
        source               TEXT,
        source_tier          INTEGER,
        fetched_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (report_date, stock_code, holder_code, fund_derivecode)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_roha_stock     ON raw_org_holding_aif10(stock_code)",
    "CREATE INDEX IF NOT EXISTS idx_roha_report    ON raw_org_holding_aif10(report_date)",
    "CREATE INDEX IF NOT EXISTS idx_roha_available ON raw_org_holding_aif10(available_date)",
    "CREATE INDEX IF NOT EXISTS idx_roha_orgtype   ON raw_org_holding_aif10(org_type_name)",
)


def ensure_tables(conn: Any) -> None:
    # 逐句 execute (兼容裸 duckdb 连接, 不依赖 executescript 包装)
    for stmt in _DDL:
        conn.execute(stmt)
    conn.commit()


# ── ① 获取 acquire ───────────────────────────────────────────────────
_CLIENT = None


def _robust_client():
    """高 retry + 长 timeout 的 aif10 client (全市场深翻页抗连接掐断). 模块级复用 session."""
    global _CLIENT
    if _CLIENT is None:
        from aif10_scraper.client import AIF10Client
        _CLIENT = AIF10Client(retry=FETCH_RETRY, timeout=FETCH_TIMEOUT)
    return _CLIENT


def _fetch_period(report_date_iso: str) -> list[dict]:
    """纯采集: aif10 datacenter 拉某报告期全市场机构持仓明细 (by-period batch, 无计算).

    全市场单期 ~83万行/417页 → 大 page_size + 高 retry client 抗深翻页掐断.
    """
    from aif10_scraper import fetch_all_pages
    return fetch_all_pages(
        report_name=REPORT_NAME,
        page_size=PAGE_SIZE,
        max_pages=0,
        sort_columns="REPORT_DATE,SECURITY_CODE",
        sort_types="-1,1",
        extra_filters=[f"(REPORT_DATE='{report_date_iso}')"],
        client=_robust_client(),
    ) or []


# ── ② 清洗 clean ─────────────────────────────────────────────────────
def _normalize_rows(raw: list[dict] | None) -> list[dict]:
    """字段映射 + 报告期/可用日锚 + grain (剔除缺主键行)."""
    if not raw:
        return []
    out: list[dict] = []
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in raw:
        stock_code = _normalize_stock_code(r.get("SECURITY_CODE"))
        report_date = _normalize_date(r.get("REPORT_DATE"))
        holder_code = (str(r.get("HOLDER_CODE")).strip() if r.get("HOLDER_CODE") not in (None, "") else None)
        if not stock_code or not report_date or not holder_code:
            continue
        out.append({
            "report_date": report_date,
            "available_date": disclosure_deadline(report_date),
            "stock_code": stock_code,
            "stock_name": (str(r.get("SECURITY_NAME_ABBR")).strip() or None) if r.get("SECURITY_NAME_ABBR") else None,
            "org_type_code": (str(r.get("ORG_TYPE")).strip() or None) if r.get("ORG_TYPE") else None,
            "org_type_name": (str(r.get("F9_ORGTYPE_NAME")).strip() or None) if r.get("F9_ORGTYPE_NAME") else None,
            "holder_code": holder_code,
            "holder_name": (str(r.get("HOLDER_NAME")).strip() or None) if r.get("HOLDER_NAME") else None,
            "fund_code": (str(r.get("FUND_CODE")).strip() or None) if r.get("FUND_CODE") else None,
            "fund_derivecode": (str(r.get("FUND_DERIVECODE")).strip() if r.get("FUND_DERIVECODE") not in (None, "") else ""),
            "fund_manager": (str(r.get("FUND_MANAGER")).strip() or None) if r.get("FUND_MANAGER") else None,
            "fund_type": (str(r.get("FUND_TYPE")).strip() or None) if r.get("FUND_TYPE") else None,
            "total_shares": _parse_float(r.get("TOTAL_SHARES")),
            "hold_value": _parse_float(r.get("HOLD_VALUE")),
            "total_shares_ratio": _parse_float(r.get("TOTALSHARES_RATIO")),
            "free_shares_ratio": _parse_float(r.get("FREESHARES_RATIO")),
            "free_market_cap": _parse_float(r.get("FREE_MARKET_CAP")),
            "free_shares": _parse_float(r.get("FREE_SHARES")),
            "fsr_change": _parse_float(r.get("FSR_CHANGE")),
            "fsr_rate_change": _parse_float(r.get("FSR_RATE_CHANGE")),
            "change_type": (str(r.get("CHANGE_TYPE")).strip() or None) if r.get("CHANGE_TYPE") else None,
            "source": SOURCE,
            "source_tier": SOURCE_TIER,
            "fetched_at": fetched_at,
        })
    return out


# ── ④ 存储 store: upsert ─────────────────────────────────────────────
_INSERT_COLS = (
    "report_date, available_date, stock_code, stock_name, org_type_code, org_type_name, "
    "holder_code, holder_name, fund_code, fund_derivecode, fund_manager, fund_type, "
    "total_shares, hold_value, total_shares_ratio, free_shares_ratio, free_market_cap, "
    "free_shares, fsr_change, fsr_rate_change, change_type, source, source_tier"
)
_INSERT_KEYS = [c.strip() for c in _INSERT_COLS.split(",")]


def _upsert_rows(conn: Any, rows: list[dict]) -> int:
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in _INSERT_KEYS)
    update_set = ", ".join(
        f"{k} = excluded.{k}" for k in _INSERT_KEYS
        if k not in ("report_date", "stock_code", "holder_code", "fund_derivecode")
    )
    conn.executemany(
        f"INSERT INTO raw_org_holding_aif10 ({_INSERT_COLS}) VALUES ({placeholders}) "
        f"ON CONFLICT(report_date, stock_code, holder_code, fund_derivecode) DO UPDATE SET "
        f"{update_set}, fetched_at = now()",
        [tuple(r.get(k) for k in _INSERT_KEYS) for r in rows],
    )
    conn.commit()
    return len(rows)


# ── 编排 ─────────────────────────────────────────────────────────────
def sync_period(conn: Any, report_date: str) -> dict:
    """同步单报告期 (获取→清洗→存储). report_date 形如 '2026-03-31'."""
    ensure_tables(conn)
    iso = _normalize_date(report_date)
    try:
        raw = _fetch_period(iso)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[org-holding-aif10] 报告期 {iso} 拉取失败: {exc}")
        return {"report_date": iso, "status": "source_unavailable", "error": str(exc), "written_rows": 0}
    rows = _normalize_rows(raw)
    written = _upsert_rows(conn, rows)
    return {"report_date": iso, "status": "ok" if written else "empty",
            "written_rows": written, "raw_rows": len(raw)}


def backfill(conn: Any, *, start_period: str = DEFAULT_START_PERIOD,
             end_period: Optional[str] = None, progress: bool = True) -> dict:
    """回填 [start_period, end_period] 全部季度末 (end 省略=latest_plannable). K线对齐默认 2018Q4 起."""
    ensure_tables(conn)
    end_period = _normalize_date(end_period) if end_period else latest_plannable_report_date()
    if not end_period:
        return {"status": "no_plannable_quarter", "written_rows": 0, "quarters": []}
    quarters = enumerate_quarter_ends(start_period, end_period)
    total = 0
    detail = []
    for q in quarters:
        d = sync_period(conn, q)
        detail.append(d)
        total += int(d.get("written_rows") or 0)
        if progress:
            print(f"  [org-holding-aif10] {q}: {d.get('status')} rows={d.get('written_rows')}")
    return {"status": "ok", "start_period": start_period, "end_period": end_period,
            "quarters": quarters, "written_rows": total, "detail": detail}


async def sync_org_holding_incremental(conn: Any) -> dict:
    """日常增量 (接 pipeline acquire): 只同步"最近一个足量披露的季度末"且 DB 缺该季时拉.

    机构持仓是季报派生 (季度更新), 增量 = 看最新 plannable 季度是否已入库, 缺则拉。
    """
    ensure_tables(conn)
    target = latest_plannable_report_date()
    if not target:
        return {"count": 0, "status": "skipped", "message": "尚无足量披露季度末"}
    row = conn.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE report_date = ?", (target,)
    ).fetchone()
    existing = int(row[0] or 0) if row else 0
    if existing > 0:
        return {"count": 0, "status": "skipped", "existing": existing, "report_date": target,
                "message": f"季度 {target} 已有 {existing} 条, 跳过"}
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, sync_period, conn, target)
    if result.get("status") == "source_unavailable":
        raise RuntimeError(f"org_holding_source_failed:{result.get('error')}")
    written = int(result.get("written_rows") or 0)
    return {"count": written, "status": "completed", "report_date": target, "written": written,
            "message": f"季度 {target} 写入 {written} 条"}
