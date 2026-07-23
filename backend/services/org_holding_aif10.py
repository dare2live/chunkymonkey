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
# PLANNABLE_LAG_DAYS 已删 2026-06-28: latest_plannable 改用 disclosure_deadline (监管硬约束) 取代固定130天滞后


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


def next_period_unlock(report_date: str) -> tuple[Optional[str], Optional[str]]:
    """给定已 plannable 的季度末, 返回 *下一个* 季度末及其法定披露截止日。

    org_holding 是季报派生 (by-period ~830k)。日常增量当最新 plannable 期已在库时
    正确 skip; 但「skip」易被误读为「永久冻结在某期」。本函数暴露「下一期何时解锁」,
    让日志/审计能证明 planner 是随披露日历自进 (2026-03-31 已在库 → 下一期
    2026-06-30 于其披露截止 2026-08-31 解锁), 而非 hard-frozen。纯日期算, 无 I/O。
    """
    iso = _normalize_date(report_date)
    if not iso:
        return (None, None)
    d = date.fromisoformat(iso)
    ordered = [f"{d.year}-{md}" for md in QUARTER_ENDS] + [
        f"{d.year + 1}-{md}" for md in QUARTER_ENDS
    ]
    for cand in ordered:
        cd = date.fromisoformat(cand)
        if cd > d:
            return (cand, disclosure_deadline(cand))
    return (None, None)


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
    """相对 today 最近"法定披露截止已过"的季度末 (= 数据应已可得)。

    2026-06-28 修: 改用 disclosure_deadline (监管硬约束) 取代旧 PLANNABLE_LAG_DAYS=130 固定滞后 —
    130 天把 Q1(截止 04-30) 卡到 8 月才进窗口, 源端早有 Q1 却不抓 (实测 today=06-28 旧逻辑返 Q4 2025,
    新逻辑返 Q1 2026-03-31)。取截止日 <= today 的最新季度末。
    """
    today = today or datetime.now(timezone.utc).date()  # rule-compliance: ok evidence=季报增量默认now
    latest = None
    for year in (today.year, today.year - 1):
        for md in QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            dl = disclosure_deadline(d.strftime("%Y-%m-%d"))
            if dl and date.fromisoformat(dl) <= today and (latest is None or d > latest):
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
    # 注意: DuckDB 对带 offset 的字符串→TIMESTAMP 是"剥 offset 不换算" (实测 '+08:00' 串
    # 落墙钟值非 UTC 换算值) — 此处恒 '+00:00' (UTC) 才安全, 换写法前先实测落库值。
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
# fetched_at 口径 = UTC (家族约定, 与 holders_aif10._utc_now 一致; data_health_snapshot 按 UTC 算新鲜度)。
# 2026-07-10 修双时区漂移(全栈审计MEDIUM): 原 _INSERT_COLS 漏 fetched_at → _normalize_rows 算好的
# UTC 被静默丢弃, INSERT 落 DDL DEFAULT CURRENT_TIMESTAMP(北京墙钟)、UPDATE 落 now()(北京墙钟),
# 同列两口径且全非 UTC → 新鲜度虚高 8h。统一改走 _normalize_rows 的 UTC 值。
_INSERT_COLS = (
    "report_date, available_date, stock_code, stock_name, org_type_code, org_type_name, "
    "holder_code, holder_name, fund_code, fund_derivecode, fund_manager, fund_type, "
    "total_shares, hold_value, total_shares_ratio, free_shares_ratio, free_market_cap, "
    "free_shares, fsr_change, fsr_rate_change, change_type, source, source_tier, fetched_at"
)
_INSERT_KEYS = [c.strip() for c in _INSERT_COLS.split(",")]


def _upsert_rows_legacy_direct(
    conn: Any, rows: list[dict], *, as_mirror: bool = True
) -> int:
    """Deprecated legacy mirror target / test escape."""
    if not rows:
        return 0
    from services.data_sources.disclosure_boundaries import (
        authorize_legacy_mirror_write,
        authorize_nonconforming_direct_write,
    )

    if as_mirror:
        authorize_legacy_mirror_write("org_holding", allow_test_escape=True)
    else:
        authorize_nonconforming_direct_write(
            "org_holding",
            conformity="NONCONFORMING",
            allow_test_escape=True,
        )
    placeholders = ", ".join("?" for _ in _INSERT_KEYS)
    update_set = ", ".join(
        f"{k} = excluded.{k}" for k in _INSERT_KEYS
        if k not in ("report_date", "stock_code", "holder_code", "fund_derivecode")
    )
    conn.executemany(
        f"INSERT INTO raw_org_holding_aif10 ({_INSERT_COLS}) VALUES ({placeholders}) "
        f"ON CONFLICT(report_date, stock_code, holder_code, fund_derivecode) DO UPDATE SET "
        f"{update_set}",
        [tuple(r.get(k) for k in _INSERT_KEYS) for r in rows],
    )
    conn.commit()
    return len(rows)


def _upsert_rows(conn: Any, rows: list[dict]) -> int:
    """E0 default: formal land→accept by available_date (formal_only)."""
    if not rows:
        return 0
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )

    outcome = write_org_holding_formal_then_mirror(conn, rows)
    return int(outcome.canonical_rows)


def accept_org_holding_partition_from_legacy(
    conn: Any,
    available_date: str,
    *,
    rewrite_legacy: bool = False,
    stock_codes: Optional[list[str]] = None,
):
    """E0 canary: land→accept one available_date from existing legacy rows.

    Default keeps legacy untouched (no-op mirror).  ``stock_codes`` optionally
    narrows to a documented stock subset when a full partition is too large;
    shadow MATCH then requires the same stock filter (see ledger).
    """
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.data_sources.org_holding_schema import PROVIDER_FIELDS

    digits = "".join(ch for ch in str(available_date or "") if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"available_date must be YYYYMMDD, got {available_date!r}")
    partition = digits[:8]
    cols = ", ".join(PROVIDER_FIELDS)
    sql = f"""
        SELECT {cols}
          FROM raw_org_holding_aif10
         WHERE replace(CAST(available_date AS VARCHAR), '-', '') = ?
    """
    params: list[Any] = [partition]
    codes = [c.strip() for c in (stock_codes or []) if c and str(c).strip()]
    if codes:
        placeholders = ", ".join("?" for _ in codes)
        sql += f" AND stock_code IN ({placeholders})"
        params.extend(codes)
    sql += " ORDER BY stock_code, holder_code, fund_derivecode, report_date"
    raw = conn.execute(sql, params).fetchall()
    rows = [dict(zip(PROVIDER_FIELDS, row, strict=True)) for row in raw]
    if not rows:
        raise ValueError(
            f"no legacy org_holding rows for available_date={partition}"
            + (f" stock_codes={codes}" if codes else "")
        )

    def _noop_mirror(_conn, material):
        return len(material)

    if rewrite_legacy:
        return write_org_holding_formal_then_mirror(
            conn,
            rows,
            mirror=_upsert_rows_legacy_direct,
            enable_legacy_mirror=True,
        )
    return write_org_holding_formal_then_mirror(conn, rows, mirror=_noop_mirror)


# ── 编排 ─────────────────────────────────────────────────────────────
class OrgHoldingMassRefreshForbidden(RuntimeError):
    """Fail-closed: refuse full-period ~830k re-pull when local already has the period.

    Owner 2026-07-21 hard constraint: manual update / incremental path is
    check-plannable-vs-local then fetch-only-if-missing. Never "refresh" an
    already-landed period (unbounded page crawl / mass dump).
    """


def _period_row_count(conn: Any, report_date_iso: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE report_date = ?",
        (report_date_iso,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def sync_period(
    conn: Any,
    report_date: str,
    *,
    allow_existing_refresh: bool = False,
) -> dict:
    """同步单报告期 (获取→清洗→存储). report_date 形如 '2026-03-31'.

    Default ``allow_existing_refresh=False`` is fail-closed: if the period
    already has local rows, refuse before any provider I/O (no ~830k re-pull).
    Explicit historical backfill may pass ``allow_existing_refresh=True`` only
    when the caller intentionally opts into a mass refresh — never from
    ``daily_update`` / ``sync_org_holding_incremental``.
    """
    ensure_tables(conn)
    iso = _normalize_date(report_date)
    if not iso:
        return {
            "report_date": report_date,
            "status": "invalid_period",
            "written_rows": 0,
            "error": "unparseable report_date",
        }
    existing = _period_row_count(conn, iso)
    if existing > 0 and not allow_existing_refresh:
        raise OrgHoldingMassRefreshForbidden(
            f"org_holding refuse mass refresh: period {iso} already has "
            f"{existing} local rows; incremental-only (fetch if missing)"
        )
    # Already accepted for this period's available_date → refuse re-pull (mass ban).
    if accepted_has_org_holding_partition(conn, iso) and not allow_existing_refresh:
        raise OrgHoldingMassRefreshForbidden(
            f"org_holding refuse mass refresh: period {iso} already accepted "
            f"(available_date={_plannable_available_yyyymmdd(iso)}); "
            "incremental-only (fetch if missing)"
        )
    try:
        raw = _fetch_period(iso)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[org-holding-aif10] 报告期 {iso} 拉取失败: {exc}")
        return {"report_date": iso, "status": "source_unavailable", "error": str(exc), "written_rows": 0}
    rows = _normalize_rows(raw)
    # Incremental land: formal accept + legacy raw mirror so gap checks / research
    # table stay aligned (formal_only-without-mirror left raw empty → false re-fetch).
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )

    outcome = write_org_holding_formal_then_mirror(
        conn, rows, enable_legacy_mirror=True
    )
    written = int(outcome.canonical_rows or 0)
    return {
        "report_date": iso,
        "status": "ok" if written else "empty",
        "written_rows": written,
        "raw_rows": len(raw),
        "accepted_partitions": list(outcome.partitions or ()),
        "legacy_rows_written": int(outcome.legacy_rows_written or 0),
    }


def backfill(conn: Any, *, start_period: str = DEFAULT_START_PERIOD,
             end_period: Optional[str] = None, progress: bool = True) -> dict:
    """Explicit multi-period backfill (NOT daily_update).

    Skips periods that already exist (same fail-closed as sync_period default).
    Must never be wired into pipeline acquire / manual update click.
    """
    ensure_tables(conn)
    end_period = _normalize_date(end_period) if end_period else latest_plannable_report_date()
    if not end_period:
        return {"status": "no_plannable_quarter", "written_rows": 0, "quarters": []}
    quarters = enumerate_quarter_ends(start_period, end_period)
    total = 0
    detail = []
    for q in quarters:
        if _period_row_count(conn, q) > 0:
            detail.append(
                {
                    "report_date": q,
                    "status": "skipped_existing",
                    "written_rows": 0,
                }
            )
            if progress:
                print(f"  [org-holding-aif10] {q}: skipped_existing (no mass refresh)")
            continue
        d = sync_period(conn, q, allow_existing_refresh=False)
        detail.append(d)
        total += int(d.get("written_rows") or 0)
        if progress:
            print(f"  [org-holding-aif10] {q}: {d.get('status')} rows={d.get('written_rows')}")
    return {"status": "ok", "start_period": start_period, "end_period": end_period,
            "quarters": quarters, "written_rows": total, "detail": detail}


def _plannable_available_yyyymmdd(report_date: str) -> Optional[str]:
    """Report period → available_date partition (disclosure deadline, YYYYMMDD)."""
    from services.data_sources.org_holding_schema import disclosure_deadline_yyyymmdd

    return disclosure_deadline_yyyymmdd(report_date)


def accepted_has_org_holding_partition(conn: Any, report_date: str) -> bool:
    """True when accepted_partition already has the plannable period's available_date."""
    from services.data_sources.org_holding_schema import DATASET_ID

    partition = _plannable_available_yyyymmdd(report_date)
    if not partition:
        return False
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM accepted_partition
             WHERE dataset_id = ?
               AND partition_value = ?
             LIMIT 1
            """,
            [DATASET_ID, partition],
        ).fetchone()
    except Exception:  # noqa: BLE001 — schema may be absent in unit :memory:
        return False
    return row is not None


def org_holding_period_gap_report(
    conn: Any,
    *,
    today: Optional[date] = None,
    start_period: str = DEFAULT_START_PERIOD,
) -> dict:
    """Read-only: latest plannable vs local raw + accepted.

    Owner 2026-07-21/23: every manual/`daily_update` must *check* incremental
    gaps (not ignore forever). Fetch stays latest-period-only; mass history and
    by-date provider invent stay banned. Intermediate holes = log-not-fill.

    Closed-loop 2026-07-23: partition existence ≠ population. Thin/canary accept
    → status under_populated_accepted (still no mass refresh).
    """
    ensure_tables(conn)
    target = latest_plannable_report_date(today=today)
    if not target:
        return {
            "plannable": None,
            "local_has_plannable": False,
            "accepted_has_plannable": False,
            "available_date": None,
            "missing_periods": [],
            "action": "none",
            "status": "no_plannable",
        }
    local_rows = conn.execute(
        "SELECT DISTINCT report_date FROM raw_org_holding_aif10"
    ).fetchall()
    local = {str(r[0])[:10] for r in local_rows if r and r[0]}
    # Normalize ISO / compact
    local_norm = {(_normalize_date(d) if d else d) for d in local}
    quarters = enumerate_quarter_ends(start_period, target)
    missing = [q for q in quarters if q not in local_norm]
    local_has = target in local_norm
    # Accepted is independent of raw — formal land may publish without mirror.
    accepted_has = accepted_has_org_holding_partition(conn, target)
    available = _plannable_available_yyyymmdd(target)
    next_period, next_unlock = next_period_unlock(target)
    from services.org_holding_population import population_for_period

    population = population_for_period(
        conn,
        report_date=target,
        local_has=local_has,
        accepted_has=accepted_has,
    )
    if accepted_has:
        action = "skip_current"
        if population.get("under_populated"):
            status = "under_populated_accepted"
        else:
            status = "ok"
    elif local_has:
        action = "accept_from_local_raw"
        status = "plannable_raw_unaccepted"
    else:
        action = "fetch_then_accept"
        status = "plannable_missing"
    # Optional typed frontier hook for future repair tooling only.
    # Period equal → skip_behind remap (existence), never by-date population invent.
    from services.data_sources.frontier_decision import org_holding_period_frontier_hook

    local_max_period = target if local_has else (
        max(local_norm) if local_norm else None
    )
    frontier = org_holding_period_frontier_hook(
        local_max_period=local_max_period,
        plannable_period=target,
    )
    return {
        "plannable": target,
        "local_has_plannable": local_has,
        "accepted_has_plannable": accepted_has,
        "available_date": available,
        "local_periods": sorted(x for x in local_norm if x),
        "missing_periods": missing,
        "missing_count": len(missing),
        "next_period": next_period,
        "next_period_unlock": next_unlock,
        "action": action,
        "status": status,
        "population": population,
        "frontier_outcome": frontier.outcome,
        "frontier_reason": frontier.reason,
    }


def _accept_plannable_from_local_raw(conn: Any, report_date: str) -> dict:
    """Land→accept the plannable period from raw (by-period incremental, not mass history)."""
    available = _plannable_available_yyyymmdd(report_date)
    if not available:
        return {
            "status": "accept_skipped",
            "error": "no_available_date",
            "report_date": report_date,
        }
    try:
        outcome = accept_org_holding_partition_from_legacy(
            conn, available, rewrite_legacy=False
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "accept_failed",
            "error": str(exc)[:300],
            "report_date": report_date,
            "available_date": available,
        }
    return {
        "status": "accepted",
        "report_date": report_date,
        "available_date": available,
        "canonical_rows": getattr(outcome, "canonical_rows", None),
        "partitions": list(getattr(outcome, "partitions", None) or []),
        "batch_ids": list(getattr(outcome, "batch_ids", None) or []),
    }


async def sync_org_holding_incremental(conn: Any) -> dict:
    """日常增量 (接 pipeline acquire): check plannable vs local every run.

    Policy (owner 2026-07-21/23):
      - Mass full-history / already-landed period ~830k refresh = BANNED
      - By-date provider land invent = BANNED (no NOTICE_DATE)
      - Every daily_update: check latest plannable; missing raw → fetch one
        period; raw present but unaccepted → accept from local-raw; both ok →
        skip with next-period unlock reason (disclosure clock, not eternal freeze)
      - Intermediate historical gaps: log-not-fill (explicit backfill knife only)
    """
    ensure_tables(conn)
    gap = org_holding_period_gap_report(conn)
    target = gap.get("plannable")
    if not target:
        return {
            "domain": "org_holding",
            "count": 0,
            "status": "skipped",
            "action": "none",
            "gap": gap,
            "message": "尚无足量披露季度末",
        }
    action = str(gap.get("action") or "skip_current")
    if action == "skip_current":
        missing_older = [p for p in (gap.get("missing_periods") or []) if p != target]
        pop = dict(gap.get("population") or {})
        if str(gap.get("status") or "") == "under_populated_accepted":
            msg = (
                f"under_populated_accepted: plannable={target} partition exists but "
                f"accepted_stocks={pop.get('accepted_stocks')} "
                f"raw_stocks={pop.get('raw_stocks')} "
                f"reasons={pop.get('reasons')}; "
                f"skip mass refresh; repair knife required "
                f"(older_missing={len(missing_older)}; next period "
                f"{gap.get('next_period')} unlocks {gap.get('next_period_unlock')})"
            )
            return {
                "domain": "org_holding",
                "count": 0,
                "status": "under_populated_accepted",
                "action": action,
                "report_date": target,
                "available_date": gap.get("available_date"),
                "next_period": gap.get("next_period"),
                "next_period_unlock": gap.get("next_period_unlock"),
                "gap": gap,
                "message": msg,
            }
        msg = (
            f"check: plannable={target} raw=present accepted=present; skip "
            f"(older_missing={len(missing_older)}; not auto mass-filled; "
            f"next period {gap.get('next_period')} unlocks "
            f"{gap.get('next_period_unlock')})"
        )
        return {
            "domain": "org_holding",
            "count": 0,
            "status": "skipped",
            "action": action,
            "report_date": target,
            "available_date": gap.get("available_date"),
            "next_period": gap.get("next_period"),
            "next_period_unlock": gap.get("next_period_unlock"),
            "gap": gap,
            "message": msg,
        }

    loop = asyncio.get_running_loop()
    if action == "fetch_then_accept":
        # sync_period: provider fetch → formal accept + raw mirror (one period).
        result = await loop.run_in_executor(None, sync_period, conn, target)
        if result.get("status") == "source_unavailable":
            raise RuntimeError(f"org_holding_source_failed:{result.get('error')}")
        written = int(result.get("written_rows") or 0)
        accept_ok = bool(result.get("accepted_partitions")) or written > 0
        return {
            "domain": "org_holding",
            "count": written,
            "status": "completed" if accept_ok else "partial",
            "action": action,
            "report_date": target,
            "available_date": gap.get("available_date"),
            "written": written,
            "fetch_status": result.get("status"),
            "accept": {
                "status": "accepted" if accept_ok else "accept_failed",
                "partitions": result.get("accepted_partitions") or [],
                "legacy_rows_written": result.get("legacy_rows_written"),
            },
            "gap": gap,
            "message": (
                f"check: plannable={target} action={action} "
                f"fetch_wrote={written} accept="
                f"{'accepted' if accept_ok else 'failed'} "
                f"(incremental-by-period; not full-history / not by-date invent)"
            ),
        }

    # raw present, accepted missing → accept from local-raw only (no provider I/O).
    accept = await loop.run_in_executor(
        None, _accept_plannable_from_local_raw, conn, target
    )
    accept_ok = accept.get("status") == "accepted"
    return {
        "domain": "org_holding",
        "count": 0,
        "status": "completed" if accept_ok else "partial",
        "action": action,
        "report_date": target,
        "available_date": gap.get("available_date"),
        "written": 0,
        "fetch_status": None,
        "accept": accept,
        "gap": gap,
        "message": (
            f"check: plannable={target} action={action} "
            f"accept={accept.get('status')} "
            f"(incremental-by-period; not full-history / not by-date invent)"
        ),
    }
