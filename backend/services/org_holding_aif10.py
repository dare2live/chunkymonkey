"""org_holding_aif10.py — 机构持仓明细 (东方财富妙想 aif10 RPT_MAIN_ORGHOLDDETAIL)

源决策: 用户 2026-06-24 拍板 — 退役冻结的 tdx F10 fact_common_major_holder_stock,
改接 aif10 机构持仓明细 (更优: 3624条深/ORG_TYPE分桶/基金级, vs tdx 仅3期浅)。
这是 §4.3 "tushare唯一" 的 aif10 例外扩展 (理由: tushare 无机构持仓明细等价, 实测确认)。

按数据模块分层 (获取/清洗/加工/存储 各司其职), 范例 = qfii_client (同报告期形态, 已 aif10 接):
  ① 获取 acquire : _fetch_period       — aif10 datacenter 拉某报告期全市场机构持仓 (by-period batch)
  ② 清洗 clean   : _normalize_rows     — 字段映射 + 报告期/可用日锚 + grain
  ③ 存储 store   : _upsert_rows        — 幂等 upsert raw_org_holding_aif10

PIT 锚: MAIN_ORGHOLDDETAIL 只有 REPORT_DATE, 无 NOTICE_DATE. 回测已知日 =
同股同期定期报告首次公告 (income.f_ann_date, 不足则 holders notice_date),
不是报告期末, 也不是法定披露截止. 截止日只量 completeness.
available_date 落公告日 (已公开) 或 first-seen (尚无公告 JOIN); 禁未来分区.

历史范围: 跟 K 线周期一致 (price_kline_qfq_tushare 2019-01-02 起) → 回到覆盖它的年报 20181231,
更早无 K 线无法回测 (用户 2026-06-24, 同 holders_aif10)。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from services.data_sources.sibling_repos import ensure_import_path

ensure_import_path("miaoxiang")

logger = logging.getLogger("cm-api")

REPORT_NAME = "RPT_MAIN_ORGHOLDDETAIL"     # 机构持仓明细 (按机构)
SOURCE = "miaoxiang"                        # aif10 datacenter
SOURCE_TIER = 1                             # evidence: 2026-06-24 用户裁决 aif10 例外扩展 (替 tdx F10)
# 全市场机构持仓单期 ~83万行; fetch 见 org_holding_fetch (East Money 100-page cap)
from services.org_holding_fetch import (  # noqa: E402
    EASTMONEY_MAX_PAGES,
    PAGE_SIZE,
    fetch_period as _fetch_period,
    probe_period_count as _probe_period_count,
)
# K线对齐: price_kline_qfq_tushare 2019-01-02 起 → 机构持仓回到覆盖它的年报 20181231
DEFAULT_START_PERIOD = "2018-12-31"         # evidence: K线起点 2019-01-02 (用户 2026-06-24)
QUARTER_ENDS = ("03-31", "06-30", "09-30", "12-31")
# PLANNABLE_LAG_DAYS 已删 2026-06-28. 采集闸 = 报告期已结束; accepted / PIT =
# 公司公告日 (JOIN), 不是法定截止.


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
    """报告期 → 法定披露截止日 (completeness 钟, 不是 PIT 已知日).

    单一计算点: ``periodic_report_calendar`` (证监会令第226号第十三条 + 沪深上市规则季报条款).
    """
    from services.data_sources.periodic_report_calendar import disclosure_deadline_iso

    return disclosure_deadline_iso(report_date)


def next_period_unlock(report_date: str) -> tuple[Optional[str], Optional[str]]:
    """给定已结束的季度末, 返回 *下一个* 季度末及其法定披露截止日。

    下一期采集窗口 = 那个季度末当天 (源端随公告增长); 返回的截止日是
    completeness 钟, 不是 PIT 已知日。纯日期算, 无 I/O。
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
    """相对 today 最近已经结束的季度末 (= 采集窗口打开).

    东财机构持仓明细随公司公告更新。采集闸 = 报告期末; accepted = 已公开的
    公告日分区 (JOIN income/holders, 否则 first-seen ≤ today)。
    """
    today = today or datetime.now(timezone.utc).date()  # rule-compliance: ok evidence=季报增量默认now
    latest = None
    for year in (today.year, today.year - 1):
        for md in QUARTER_ENDS:
            d = date.fromisoformat(f"{year}-{md}")
            if d <= today and (latest is None or d > latest):
                latest = d
    return latest.strftime("%Y-%m-%d") if latest else None


def latest_accept_unlocked_report_date(today: Optional[date] = None) -> Optional[str]:
    """Ended report period can be accepted (PIT axis = announcement, not deadline)."""
    return latest_plannable_report_date(today=today)


def accept_unlocked(report_date: str, today: Optional[date] = None) -> bool:
    """True when the report period has ended (announcement-dated accept is safe)."""
    iso = _normalize_date(report_date)
    if not iso:
        return False
    today = today or datetime.now(timezone.utc).date()  # rule-compliance: ok evidence=报告期末采集闸用日历日, 非 trade_date
    return date.fromisoformat(iso) <= today


# ── ④ 存储 store: schema ─────────────────────────────────────────────
_DDL = (
    """
    CREATE TABLE IF NOT EXISTS raw_org_holding_aif10 (
        report_date          TEXT NOT NULL,   -- 报告期 YYYY-MM-DD
        available_date       TEXT,            -- PIT 可用日 (公告日; 无 JOIN 时 first-seen)
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
    from services.org_holding_population import ensure_probe_table

    ensure_probe_table(conn)
    conn.commit()


def _grain_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    rd = _normalize_date(row.get("report_date")) or ""
    return (
        rd,
        str(row.get("stock_code") or ""),
        str(row.get("holder_code") or ""),
        str(row.get("fund_derivecode") or ""),
    )


def _existing_raw_grains(conn: Any, report_date_iso: str) -> set[tuple[str, str, str, str]]:
    compact = report_date_iso.replace("-", "")
    try:
        rows = conn.execute(
            """
            SELECT report_date, stock_code, holder_code, COALESCE(fund_derivecode, '')
              FROM raw_org_holding_aif10
             WHERE report_date IN (?, ?)
            """,
            [report_date_iso, compact],
        ).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    out: set[tuple[str, str, str, str]] = set()
    for report_date, stock, holder, fund in rows:
        iso = _normalize_date(report_date) or ""
        out.add((iso, str(stock or ""), str(holder or ""), str(fund or "")))
    return out


def _existing_canonical_grains(
    conn: Any, report_date_iso: str
) -> set[tuple[str, str, str, str]]:
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    compact = report_date_iso.replace("-", "")
    try:
        rows = conn.execute(
            f"""
            SELECT report_date, stock_code, holder_code, COALESCE(fund_derivecode, '')
              FROM {CANONICAL_TABLE}
             WHERE report_date IN (?, ?)
            """,
            [report_date_iso, compact],
        ).fetchall()
    except Exception:  # noqa: BLE001
        return set()
    out: set[tuple[str, str, str, str]] = set()
    for report_date, stock, holder, fund in rows:
        iso = _normalize_date(report_date) or ""
        out.add((iso, str(stock or ""), str(holder or ""), str(fund or "")))
    return out


def _new_grains_only(
    conn: Any, report_date_iso: str, rows: list[dict]
) -> list[dict]:
    existing = _existing_raw_grains(conn, report_date_iso)
    existing |= _existing_canonical_grains(conn, report_date_iso)
    return [row for row in rows if _grain_key(row) not in existing]


# ── ② 清洗 clean ─────────────────────────────────────────────────────
def _period_announcement_map(report_date: str) -> dict[str, str]:
    """Join income/holders first-announcement day. Empty map → first-seen stamps."""
    from services.data_sources.org_holding_announcement import load_period_announcement_map

    try:
        return load_period_announcement_map(report_date)
    except Exception:  # noqa: BLE001
        return {}


def _normalize_rows(
    raw: list[dict] | None,
    *,
    announcement_by_stock: dict[str, str] | None = None,
    land_date: str | None = None,
    today: str | None = None,
) -> list[dict]:
    """字段映射 + 报告期/公告日锚 + grain (剔除缺主键行)."""
    if not raw:
        return []
    from services.data_sources.org_holding_announcement import (
        land_calendar_date,
        resolve_available_iso,
    )

    land = land_date or land_calendar_date()
    asof = today or land
    deduped: dict[tuple[str, str, str, str], dict] = {}
    # 注意: DuckDB 对带 offset 的字符串→TIMESTAMP 是"剥 offset 不换算" (实测 '+08:00' 串
    # 落墙钟值非 UTC 换算值) — 此处恒 '+00:00' (UTC) 才安全, 换写法前先实测落库值。
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in raw:
        stock_code = _normalize_stock_code(r.get("SECURITY_CODE"))
        report_date = _normalize_date(r.get("REPORT_DATE"))
        holder_code = (str(r.get("HOLDER_CODE")).strip() if r.get("HOLDER_CODE") not in (None, "") else None)
        if not stock_code or not report_date or not holder_code:
            continue
        fund_derive = (str(r.get("FUND_DERIVECODE")).strip() if r.get("FUND_DERIVECODE") not in (None, "") else "")
        row = {
            "report_date": report_date,
            "available_date": resolve_available_iso(
                stock_code=stock_code,
                report_date=report_date,
                announcement_by_stock=announcement_by_stock,
                land_date=land,
                today=asof,
            ),
            "stock_code": stock_code,
            "stock_name": (str(r.get("SECURITY_NAME_ABBR")).strip() or None) if r.get("SECURITY_NAME_ABBR") else None,
            "org_type_code": (str(r.get("ORG_TYPE")).strip() or None) if r.get("ORG_TYPE") else None,
            "org_type_name": (str(r.get("F9_ORGTYPE_NAME")).strip() or None) if r.get("F9_ORGTYPE_NAME") else None,
            "holder_code": holder_code,
            "holder_name": (str(r.get("HOLDER_NAME")).strip() or None) if r.get("HOLDER_NAME") else None,
            "fund_code": (str(r.get("FUND_CODE")).strip() or None) if r.get("FUND_CODE") else None,
            "fund_derivecode": fund_derive,
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
        }
        deduped[(report_date, stock_code, holder_code, fund_derive)] = row
    return list(deduped.values())


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
    stock_codes: Optional[list[str]] = None,
):
    """Land→accept one available_date from existing legacy rows (noop mirror).

    Legacy stays untouched. ``stock_codes`` optionally narrows the partition.
    ``rewrite_legacy`` removed 2026-07-23 — no DELETE→INSERT cargo-cult rewrite.
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

    return write_org_holding_formal_then_mirror(conn, rows, mirror=_noop_mirror)


# ── 编排 ─────────────────────────────────────────────────────────────
class OrgHoldingMassRefreshForbidden(RuntimeError):
    """Fail-closed: refuse full-period ~830k re-pull when local already has the period.

    Daily path: missing period → fetch; present + source count ahead → MERGE
    new grains; present + count unchanged → skip. Never DELETE+re-insert.
    """


def _period_row_count(conn: Any, report_date_iso: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM raw_org_holding_aif10 WHERE report_date = ?",
        (report_date_iso,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _canonical_report_period_row_count(conn: Any, report_date_iso: str) -> int:
    """Rows for this report_date only (not shared available_date partition)."""
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    compact = str(report_date_iso or "").replace("-", "")
    if len(compact) != 8:
        return 0
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*)
              FROM {CANONICAL_TABLE}
             WHERE report_date IN (?, ?)
            """,
            [report_date_iso, compact],
        ).fetchone()
    except Exception:  # noqa: BLE001 — schema may be absent in unit :memory:
        return 0
    return int(row[0] or 0) if row else 0


def sync_period(
    conn: Any,
    report_date: str,
    *,
    allow_existing_refresh: bool = False,
    merge_grains: bool = False,
    raw_only: bool = False,
) -> dict:
    """同步单报告期 (获取→清洗→存储). report_date 形如 '2026-03-31'.

    Default ``allow_existing_refresh=False`` is fail-closed: if the period
    already has local rows, refuse before any provider I/O (no ~830k re-pull).
    ``merge_grains=True`` is the daily exception: page-1 count must be ahead
    of local (+ last reconciled probe), then fetch once and INSERT new grains
    only (no DELETE of the report_date). ``raw_only=True`` lands legacy raw
    without formal accept (explicit ops hatch; daily path accepts announcement
    partitions as companies file). Explicit ``allow_existing_refresh=True`` remains
    ops/truncation-CLI replace — never wired into daily_update.
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
    # Report-period scoped: shared available_date partitions (e.g. 2018-12-31 +
    # 2019-03-31 → 20190430) must not block fetch of the missing quarter.
    canonical_rows = _canonical_report_period_row_count(conn, iso)
    present = existing > 0 or canonical_rows > 0
    if present and not allow_existing_refresh and not merge_grains:
        raise OrgHoldingMassRefreshForbidden(
            f"org_holding refuse mass refresh: period {iso} already has "
            f"{existing} local / {canonical_rows} canonical rows; "
            "incremental-only (fetch if missing or MERGE when source count ahead)"
        )
    from services.org_holding_population import (
        read_reconciled_source_count,
        source_count_ahead,
        write_source_probe,
    )

    source_count: int | None = None
    if merge_grains and present and not allow_existing_refresh:
        try:
            source_count = int(_probe_period_count(iso))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[org-holding-aif10] count probe failed period=%s: %s", iso, exc)
            return {
                "report_date": iso,
                "status": "probe_failed",
                "written_rows": 0,
                "error": str(exc)[:300],
                "local_rows": existing,
            }
        last = read_reconciled_source_count(conn, iso)
        if not source_count_ahead(
            local_rows=existing,
            source_count=source_count,
            last_reconciled_count=last,
        ):
            return {
                "report_date": iso,
                "status": "skipped_probe_current",
                "written_rows": 0,
                "source_count": source_count,
                "local_rows": existing,
                "last_reconciled_count": last,
            }
    try:
        fetched = _fetch_period(iso)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[org-holding-aif10] 报告期 {iso} 拉取失败: {exc}")
        return {"report_date": iso, "status": "source_unavailable", "error": str(exc), "written_rows": 0}
    if fetched.get("truncated"):
        logger.error(
            "[org-holding-aif10] provider pagination truncated period=%s "
            "count=%s landed=%s reasons=%s",
            iso,
            fetched.get("provider_count"),
            fetched.get("fetched_rows"),
            fetched.get("land_reasons"),
        )
        return {
            "report_date": iso,
            "status": "provider_truncated",
            "written_rows": 0,
            "provider_count": fetched.get("provider_count"),
            "fetched_rows": fetched.get("fetched_rows"),
            "truncated": True,
            "land_reasons": fetched.get("land_reasons"),
        }
    raw = fetched.get("rows") or []
    rows = _normalize_rows(
        raw,
        announcement_by_stock=_period_announcement_map(iso),
    )
    merge_now = bool(merge_grains and present and not allow_existing_refresh)
    new_rows = _new_grains_only(conn, iso, rows) if merge_now else rows
    if merge_now and not new_rows:
        write_source_probe(
            conn,
            iso,
            source_count=int(source_count or fetched.get("provider_count") or 0),
            local_rows=existing,
            new_grains=0,
        )
        return {
            "report_date": iso,
            "status": "skipped_no_new_grains",
            "written_rows": 0,
            "source_count": source_count or fetched.get("provider_count"),
            "local_rows": existing,
            "fetched_rows": fetched.get("fetched_rows"),
            "provider_count": fetched.get("provider_count"),
        }
    if raw_only:
        written = _upsert_rows_legacy_direct(conn, new_rows, as_mirror=True)
        if merge_now or existing == 0:
            write_source_probe(
                conn,
                iso,
                source_count=int(source_count or fetched.get("provider_count") or 0),
                local_rows=existing + len(new_rows),
                new_grains=len(new_rows),
            )
        return {
            "report_date": iso,
            "status": "merged_raw" if merge_now else ("ok_raw" if written else "empty"),
            "written_rows": written,
            "raw_rows": len(raw),
            "merged_rows": len(new_rows) if merge_now else len(rows),
            "provider_count": fetched.get("provider_count"),
            "source_count": source_count,
            "fetched_rows": fetched.get("fetched_rows"),
            "truncated": False,
            "shard_count": fetched.get("shard_count"),
            "accepted_partitions": [],
            "legacy_rows_written": written,
        }
    # Incremental land: formal accept + legacy raw mirror so gap checks / research
    # table stay aligned (formal_only-without-mirror left raw empty → false re-fetch).
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )

    outcome = write_org_holding_formal_then_mirror(
        conn,
        new_rows,
        enable_legacy_mirror=True,
        merge_grains=merge_now,
    )
    written = int(outcome.canonical_rows or 0)
    if merge_now:
        write_source_probe(
            conn,
            iso,
            source_count=int(source_count or fetched.get("provider_count") or 0),
            local_rows=existing + len(new_rows),
            new_grains=len(new_rows),
        )
    return {
        "report_date": iso,
        "status": "merged" if merge_now else ("ok" if written else "empty"),
        "written_rows": written,
        "raw_rows": len(raw),
        "merged_rows": len(new_rows) if merge_now else len(rows),
        "provider_count": fetched.get("provider_count"),
        "source_count": source_count,
        "fetched_rows": fetched.get("fetched_rows"),
        "truncated": False,
        "shard_count": fetched.get("shard_count"),
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
    """Deprecated completeness compact date; not the PIT partition."""
    from services.data_sources.org_holding_schema import disclosure_deadline_yyyymmdd

    return disclosure_deadline_yyyymmdd(report_date)


def accepted_has_org_holding_partition(conn: Any, report_date: str) -> bool:
    """True when canonical already has grains for this report_date."""
    return _canonical_report_period_row_count(conn, report_date) > 0


def org_holding_period_gap_report(
    conn: Any,
    *,
    today: Optional[date] = None,
    start_period: str = DEFAULT_START_PERIOD,
) -> dict:
    """Read-only: latest plannable vs local raw + accepted.

    Owner 2026-07-21/23 + 2026-07-24 bounded fill: every manual/`daily_update`
    must *check* incremental gaps (not ignore forever). Latest plannable path
    unchanged; when plannable complete and older quarters missing, fill **oldest**
    one per run (N=1). Mass history and by-date provider invent stay banned.

    Closed-loop 2026-07-23: partition existence ≠ population. Thin/canary accept
    → repair_accept_from_local_raw when dense raw present (no mass provider refresh).
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
    available = None
    next_period, next_unlock = next_period_unlock(target)
    unlocked = accept_unlocked(target, today)
    from services.org_holding_population import (
        decide_org_gap_action,
        population_for_period,
        read_reconciled_source_count,
    )

    population = population_for_period(
        conn,
        report_date=target,
        local_has=local_has,
        accepted_has=accepted_has,
    )
    source_count: int | None = None
    probe_error: str | None = None
    try:
        source_count = int(_probe_period_count(target))
    except Exception as exc:  # noqa: BLE001 — probe fail-closed: never mass-fetch
        probe_error = str(exc)[:300]
        logger.warning("[org-holding-aif10] gap count probe failed period=%s: %s", target, exc)
    last_reconciled = read_reconciled_source_count(conn, target)
    from services.data_sources.periodic_report_calendar import (
        is_past_completeness_deadline,
    )

    today_d = today or datetime.now(timezone.utc).date()  # rule-compliance: ok evidence=completeness deadline vs calendar day, not trade_date
    today_s = today_d.isoformat()
    completeness_due = is_past_completeness_deadline(target, today_s)
    completeness_miss_periods = [
        q for q in missing if is_past_completeness_deadline(q, today_s)
    ]
    action, status = decide_org_gap_action(
        accepted_has=accepted_has,
        local_has=local_has,
        population=population,
        accept_unlocked=unlocked,
        source_count=source_count,
        last_reconciled_count=last_reconciled,
    )
    if status == "plannable_missing" and completeness_due:
        status = "completeness_miss"
        if probe_error:
            completeness_class = "unverified"
        elif source_count and int(source_count) > 0:
            completeness_class = "we_behind_source"
        else:
            completeness_class = "due_local_empty"
    elif completeness_due:
        completeness_class = "due_landed" if local_has else "due"
    else:
        completeness_class = "in_season"
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
    out = {
        "plannable": target,
        "local_has_plannable": local_has,
        "accepted_has_plannable": accepted_has,
        "available_date": available,
        "local_periods": sorted(x for x in local_norm if x),
        "missing_periods": missing,
        "missing_count": len(missing),
        "next_period": next_period,
        "next_period_unlock": next_unlock,
        "accept_unlocked": unlocked,
        "action": action,
        "status": status,
        "population": population,
        "source_count": source_count,
        "last_reconciled_count": last_reconciled,
        "probe_error": probe_error,
        "completeness_due": completeness_due,
        "completeness_class": completeness_class,
        "completeness_miss_periods": completeness_miss_periods,
        "frontier_outcome": frontier.outcome,
        "frontier_reason": frontier.reason,
    }
    if action == "skip_current":
        from services.org_holding_period_catchup import plan_older_org_period_fill

        fill_plan = plan_older_org_period_fill(
            conn,
            plannable=target,
            start_period=start_period,
            today=today,
        )
        out["fill_target_period"] = fill_plan.get("fill_target_period")
        out["older_remaining"] = fill_plan.get("older_remaining")
        out["missing_older_count"] = fill_plan.get("missing_older_count")
        if fill_plan.get("fill_target_period"):
            out["bounded_fill_action"] = "fill_older_period"
    return out


def _load_raw_period_material(conn: Any, report_date: str) -> dict:
    """Load one report_date from local raw. No stamp, no provider I/O."""
    iso = _normalize_date(report_date)
    if not iso:
        return {
            "status": "accept_skipped",
            "error": "unparseable report_date",
            "report_date": report_date,
            "iso": None,
            "rows": [],
        }
    compact = iso.replace("-", "")
    cols = ", ".join(_INSERT_KEYS)
    try:
        raw = conn.execute(
            f"""
            SELECT {cols}
              FROM raw_org_holding_aif10
             WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
            """,
            [compact],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "accept_failed",
            "error": str(exc)[:300],
            "report_date": report_date,
            "iso": iso,
            "rows": [],
        }
    material = [dict(zip(_INSERT_KEYS, row, strict=True)) for row in raw]
    if not material:
        return {
            "status": "accept_skipped",
            "error": "no_local_raw",
            "report_date": report_date,
            "iso": iso,
            "rows": [],
        }
    return {
        "status": "ok",
        "report_date": report_date,
        "iso": iso,
        "rows": material,
    }


def list_raw_org_holding_report_dates(conn: Any) -> list[str]:
    """Distinct raw report_dates as ISO, oldest first. Empty if raw table missing."""
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT replace(CAST(report_date AS VARCHAR), '-', '')
              FROM raw_org_holding_aif10
             ORDER BY 1
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for row in rows:
        iso = _normalize_date(row[0])
        if iso:
            out.append(iso)
    return out


def _stamp_announced_available_dates(
    rows: list[dict],
    announcement_by_stock: dict[str, str],
) -> tuple[list[dict], int]:
    """KEEP grains whose stock is in the announcement map; DROP the rest.

    Stamps ``available_date`` from the map only. Does **not** call
    ``resolve_available_iso`` / ``land_date=today`` — that would mark 2019
    holdings known-at today.
    """
    from services.data_sources.org_holding_announcement import (
        compact_yyyymmdd,
        iso_date,
        normalize_stock_code,
    )

    kept: list[dict] = []
    skipped = 0
    for row in rows:
        code = normalize_stock_code(row.get("stock_code"))
        announced = compact_yyyymmdd(
            announcement_by_stock.get(code)
            or announcement_by_stock.get(str(row.get("stock_code") or ""))
        )
        report = compact_yyyymmdd(row.get("report_date"))
        if announced is None or report is None or announced < report:
            skipped += 1
            continue
        item = dict(row)
        item["available_date"] = iso_date(announced)
        kept.append(item)
    return kept, skipped


def _is_missing_relation(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "does not exist" in msg or "not found" in msg


def _mirror_update_raw_available_date(conn: Any, rows: list[dict]) -> int:
    """Set-based grain UPDATE of raw available_date. Never DELETE+provider re-pull."""
    from services.data_sources.disclosure_boundaries import (
        authorize_legacy_mirror_write,
    )
    from services.data_sources.org_holding_announcement import (
        compact_yyyymmdd,
        iso_date,
        normalize_stock_code,
    )

    authorize_legacy_mirror_write("org_holding", allow_test_escape=True)
    payload: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        avail = iso_date(row.get("available_date"))
        report = compact_yyyymmdd(row.get("report_date"))
        stock = normalize_stock_code(row.get("stock_code"))
        holder = str(row.get("holder_code") or "").strip()
        fund = str(row.get("fund_derivecode") or "").strip()
        if not avail or not report or not stock or not holder:
            continue
        payload.append((report, stock, holder, fund, avail))
    if not payload:
        return 0
    temp = "_k4_org_holding_avail_upd"
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    conn.execute(
        f"""
        CREATE TEMP TABLE {temp} (
            report_date VARCHAR,
            stock_code VARCHAR,
            holder_code VARCHAR,
            fund_derivecode VARCHAR,
            available_date VARCHAR
        )
        """
    )
    conn.executemany(
        f"INSERT INTO {temp} VALUES (?, ?, ?, ?, ?)",
        payload,
    )
    conn.execute(
        f"""
        UPDATE raw_org_holding_aif10
           SET available_date = t.available_date
          FROM {temp} AS t
         WHERE replace(CAST(raw_org_holding_aif10.report_date AS VARCHAR), '-', '')
               = t.report_date
           AND raw_org_holding_aif10.stock_code = t.stock_code
           AND raw_org_holding_aif10.holder_code = t.holder_code
           AND COALESCE(CAST(raw_org_holding_aif10.fund_derivecode AS VARCHAR), '')
               = t.fund_derivecode
        """
    )
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    return len(payload)


def _canonical_available_dates_for_report(conn: Any, report_compact: str) -> list[str]:
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT replace(CAST(available_date AS VARCHAR), '-', '')
              FROM {CANONICAL_TABLE}
             WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
            """,
            [report_compact],
        ).fetchall()
    except Exception as exc:
        if _is_missing_relation(exc):
            return []
        raise
    return sorted(str(row[0]) for row in rows if row and row[0])


def _delete_canonical_report_date(conn: Any, report_compact: str) -> int:
    """Drop poisoned deadline-partition grains for this report_date (any available_date).

    Caller owns the transaction / restore. Missing table → 0. Other errors raise.
    """
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    try:
        before = conn.execute(
            f"""
            SELECT COUNT(*) FROM {CANONICAL_TABLE}
             WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
            """,
            [report_compact],
        ).fetchone()
    except Exception as exc:
        if _is_missing_relation(exc):
            return 0
        raise
    conn.execute(
        f"""
        DELETE FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
        """,
        [report_compact],
    )
    return int(before[0] or 0) if before else 0


def _snapshot_canonical_report_date(
    conn: Any, report_compact: str, snapshot_table: str
) -> bool:
    """TEMP copy of this report_date. False if canonical table is missing."""
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    conn.execute(f"DROP TABLE IF EXISTS {snapshot_table}")
    try:
        conn.execute(
            f"""
            CREATE TEMP TABLE {snapshot_table} AS
            SELECT * FROM {CANONICAL_TABLE}
             WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
            """,
            [report_compact],
        )
    except Exception as exc:
        if _is_missing_relation(exc):
            return False
        raise
    return True


def _restore_canonical_report_date(
    conn: Any, report_compact: str, snapshot_table: str
) -> None:
    from services.data_sources.org_holding_schema import CANONICAL_TABLE

    conn.execute(
        f"""
        DELETE FROM {CANONICAL_TABLE}
         WHERE replace(CAST(report_date AS VARCHAR), '-', '') = ?
        """,
        [report_compact],
    )
    conn.execute(
        f"INSERT INTO {CANONICAL_TABLE} SELECT * FROM {snapshot_table}"
    )


def refresh_org_holding_partition_pointers(
    conn: Any, partitions: list[str]
) -> list[dict]:
    """Recount accepted_partition after grains moved off a deadline date.

    Empty partitions drop the pointer. Remaining siblings keep a full-partition
    hash (same helper as repair_org_holding_accepted_pointers).
    """
    from services.data_sources.accepted_schema import ACCEPTED_TABLE
    from services.data_sources.disclosure_event_partition import (
        partition_accepted_pointer_stats,
    )
    from services.data_sources.org_holding_acceptance import DOMAIN
    from services.data_sources.org_holding_schema import DATASET_ID

    refreshed: list[dict] = []
    seen: set[str] = set()
    for raw in partitions:
        pv = "".join(ch for ch in str(raw or "") if ch.isdigit())[:8]
        if len(pv) != 8 or pv in seen:
            continue
        seen.add(pv)
        try:
            n, content_hash = partition_accepted_pointer_stats(conn, DOMAIN, pv)
        except Exception as exc:  # noqa: BLE001
            refreshed.append(
                {"partition_value": pv, "action": "hash_failed", "error": str(exc)[:200]}
            )
            continue
        if n <= 0:
            conn.execute(
                f"""
                DELETE FROM {ACCEPTED_TABLE}
                 WHERE dataset_id = ?
                   AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?
                """,
                [DATASET_ID, pv],
            )
            refreshed.append({"partition_value": pv, "action": "deleted_empty", "row_count": 0})
            continue
        conn.execute(
            f"""
            UPDATE {ACCEPTED_TABLE}
               SET row_count = ?, content_hash = ?
             WHERE dataset_id = ?
               AND replace(CAST(partition_value AS VARCHAR), '-', '') = ?
            """,
            [n, content_hash, DATASET_ID, pv],
        )
        refreshed.append(
            {"partition_value": pv, "action": "updated", "row_count": n}
        )
    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return refreshed


def reaccept_org_holding_period_announced(
    conn: Any,
    report_date: str,
    *,
    announcement_by_stock: dict[str, str] | None = None,
    dry_run: bool = True,
    refresh_pointers: bool = True,
) -> dict:
    """Historical-safe accept from local raw: PIT = announcement, never today.

    Daily ``_accept_plannable_from_local_raw`` still uses first-seen as a live
    fallback. This helper is the history repair: KEEP announced grains, DROP
    the rest, replace canonical rows for this report_date, UPDATE raw
    ``available_date`` by grain.
    """
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.data_sources.org_holding_announcement import compact_yyyymmdd

    loaded = _load_raw_period_material(conn, report_date)
    iso = loaded.get("iso")
    material = list(loaded.get("rows") or [])
    current_canonical = _canonical_report_period_row_count(conn, iso or "") if iso else 0
    vacated = _canonical_available_dates_for_report(
        conn, (iso or "").replace("-", "")
    ) if iso else []
    base = {
        "report_date": iso or report_date,
        "raw_rows": len(material),
        "with_announcement": 0,
        "skipped_no_announcement": 0,
        "distinct_new_available_dates": [],
        "current_canonical_rows": current_canonical,
        "vacated_available_dates": vacated,
        "canonical_rows_written": 0,
        "legacy_available_dates_updated": 0,
        "partitions": [],
        "batch_ids": [],
        "pointer_refresh": [],
    }
    if loaded["status"] != "ok":
        base["status"] = loaded["status"]
        base["error"] = loaded.get("error")
        return base

    announced_map = (
        dict(announcement_by_stock)
        if announcement_by_stock is not None
        else _period_announcement_map(iso)
    )
    if not announced_map:
        # Empty map = join DBs missing or period has zero filings. Fail closed:
        # do not stamp today, do not DELETE canonical.
        base["status"] = "blocked_empty_announcement_map"
        base["skipped_no_announcement"] = len(material)
        base["error"] = "announcement map empty; refuse historical first-seen stamp"
        return base

    kept, skipped = _stamp_announced_available_dates(material, announced_map)
    new_dates = sorted(
        {
            compact_yyyymmdd(row.get("available_date"))
            for row in kept
            if compact_yyyymmdd(row.get("available_date"))
        }
    )
    base["with_announcement"] = len(kept)
    base["skipped_no_announcement"] = skipped
    base["distinct_new_available_dates"] = new_dates
    if dry_run:
        base["status"] = "dry_run"
        return base

    if not kept:
        # Non-empty map but zero matching raw stocks: do not wipe canonical.
        base["status"] = "skipped_no_announced_grains"
        base["error"] = (
            "announcement map had no matching raw stocks; canonical unchanged"
        )
        return base

    compact = iso.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        base["status"] = "accept_failed"
        base["error"] = f"unparseable report_date compact={compact!r}"
        return base
    snapshot = f"_k4_reaccept_restore_{compact}"
    # land→accept opens its own BEGIN/COMMIT, so an outer txn cannot wrap
    # delete+write. Snapshot the doomed report_date, DELETE, write; ROLLBACK
    # the delete by restoring the snapshot if write/mirror fails.
    had_canonical = _snapshot_canonical_report_date(conn, compact, snapshot)
    keep_snapshot = False
    try:
        if had_canonical:
            _delete_canonical_report_date(conn, compact)
        outcome = write_org_holding_formal_then_mirror(
            conn,
            kept,
            enable_legacy_mirror=True,
            merge_grains=True,
            mirror=_mirror_update_raw_available_date,
        )
    except Exception as exc:  # noqa: BLE001
        if had_canonical:
            try:
                _restore_canonical_report_date(conn, compact, snapshot)
            except Exception as restore_exc:  # noqa: BLE001
                keep_snapshot = True
                base["status"] = "accept_failed"
                base["error"] = (
                    f"{exc}; restore failed: {restore_exc}"
                )[:300]
                return base
        base["status"] = "accept_failed"
        base["error"] = str(exc)[:300]
        return base
    finally:
        if not keep_snapshot:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {snapshot}")
            except Exception:  # noqa: BLE001
                pass

    partitions = list(getattr(outcome, "partitions", None) or [])
    base["status"] = "accepted"
    base["canonical_rows_written"] = int(getattr(outcome, "canonical_rows", 0) or 0)
    base["legacy_available_dates_updated"] = int(
        getattr(outcome, "legacy_rows_written", 0) or 0
    )
    base["partitions"] = partitions
    base["batch_ids"] = list(getattr(outcome, "batch_ids", None) or [])
    base["available_date"] = partitions[0] if len(partitions) == 1 else None

    if refresh_pointers:
        leftover = [pv for pv in vacated if pv not in set(new_dates)]
        base["pointer_refresh"] = refresh_org_holding_partition_pointers(conn, leftover)
    return base


def _accept_plannable_from_local_raw(conn: Any, report_date: str) -> dict:
    """Land→accept one ended period from raw, partitioned by announcement/first-seen."""
    from services.data_sources.disclosure_dual_write import (
        write_org_holding_formal_then_mirror,
    )
    from services.data_sources.org_holding_announcement import (
        land_calendar_date,
        stamp_available_dates,
    )

    loaded = _load_raw_period_material(conn, report_date)
    if loaded["status"] != "ok":
        return {
            "status": loaded["status"],
            "error": loaded.get("error"),
            "report_date": report_date,
        }
    land = land_calendar_date()
    rows = stamp_available_dates(
        loaded["rows"],
        announcement_by_stock=_period_announcement_map(loaded["iso"]),
        land_date=land,
        today=land,
    )
    try:
        outcome = write_org_holding_formal_then_mirror(
            conn,
            rows,
            enable_legacy_mirror=True,
            merge_grains=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "accept_failed",
            "error": str(exc)[:300],
            "report_date": report_date,
        }
    partitions = list(getattr(outcome, "partitions", None) or [])
    return {
        "status": "accepted",
        "report_date": report_date,
        "available_date": partitions[0] if len(partitions) == 1 else None,
        "canonical_rows": getattr(outcome, "canonical_rows", None),
        "partitions": partitions,
        "batch_ids": list(getattr(outcome, "batch_ids", None) or []),
    }


async def sync_org_holding_incremental(conn: Any) -> dict:
    """日常增量 (接 pipeline acquire): check plannable vs local every run.

    Policy (owner 2026-07-21/23 + 2026-07-24 bounded fill):
      - Mass full-history / already-landed period ~830k refresh = BANNED
      - By-date provider land invent = BANNED (no NOTICE_DATE)
      - Every daily_update: check latest plannable; missing raw → fetch then
        accept by announcement/first-seen; raw present but unaccepted → accept
        from local-raw; both ok → page-1 count probe, MERGE new grains when
        source is ahead; else skip OR fill oldest missing quarter (N=1/run)
      - NEVER backfill() in pipeline; never allow_existing_refresh=True here
      - NEVER backfill() in pipeline; never allow_existing_refresh=True here
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
        from services.org_holding_period_catchup import sync_older_org_period_if_due

        fill_result = await sync_older_org_period_if_due(conn, gap)
        if fill_result is not None:
            return fill_result
        missing_older = int(gap.get("missing_older_count") or 0)
        status = str(gap.get("status") or "ok")
        if status == "pending_accept_clock":
            msg = (
                f"check: plannable={target} raw=present; accept deferred "
                f"(period not ended)"
            )
        else:
            msg = (
                f"check: plannable={target} raw=present accepted=present; skip "
                f"(older_missing={missing_older}; bounded fill idle; "
                f"next period {gap.get('next_period')} acquire opens at period end)"
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
    raw_only_actions = {"fetch_raw", "merge_raw"}
    provider_actions = {
        "fetch_then_accept",
        "merge_period",
        "repair_fetch_period",
        *raw_only_actions,
    }
    if action in provider_actions:
        # One-period provider path. merge_* INSERT new grains only.
        # fetch_raw/merge_raw: explicit raw-only hatch (period not ended).
        merge = action in {"merge_period", "repair_fetch_period", "merge_raw"}
        raw_only = action in raw_only_actions
        result = await loop.run_in_executor(
            None,
            lambda: sync_period(
                conn,
                target,
                allow_existing_refresh=False,
                merge_grains=merge,
                raw_only=raw_only,
            ),
        )
        if result.get("status") == "source_unavailable":
            raise RuntimeError(f"org_holding_source_failed:{result.get('error')}")
        if result.get("status") == "provider_truncated":
            raise RuntimeError(
                "org_holding_provider_truncated:"
                f"count={result.get('provider_count')} "
                f"landed={result.get('fetched_rows')} "
                f"reasons={result.get('land_reasons')}"
            )
        written = int(result.get("written_rows") or 0)
        if raw_only:
            return {
                "domain": "org_holding",
                "count": written,
                "status": "completed" if written else "partial",
                "action": action,
                "report_date": target,
                "available_date": gap.get("available_date"),
                "written": written,
                "fetch_status": result.get("status"),
                "accept": {
                    "status": "deferred_accept_clock",
                    "partitions": [],
                    "legacy_rows_written": result.get("legacy_rows_written"),
                },
                "gap": gap,
                "message": (
                    f"check: plannable={target} action={action} "
                    f"raw_wrote={written} accept=deferred (raw_only hatch)"
                ),
            }
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

    # accept_from_local_raw | repair_accept_from_local_raw — no provider I/O.
    accept = await loop.run_in_executor(
        None, _accept_plannable_from_local_raw, conn, target
    )
    accept_ok = accept.get("status") == "accepted"
    return {
        "domain": "org_holding",
        "count": int(accept.get("canonical_rows") or 0),
        "status": "completed" if accept_ok else "partial",
        "action": action,
        "report_date": target,
        "available_date": gap.get("available_date"),
        "written": int(accept.get("canonical_rows") or 0),
        "fetch_status": None,
        "accept": accept,
        "gap": gap,
        "message": (
            f"check: plannable={target} action={action} "
            f"accept={accept.get('status')} rows={accept.get('canonical_rows')} "
            f"(local-raw repair/accept; not full-history / not by-date invent)"
        ),
    }
