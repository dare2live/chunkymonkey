"""十大流通股东 — 东方财富妙想 aif10 数据源服务 (主源, 2026-06-24).

源决策: analysis/miaoxiang_aif10_source_decision_20260624.md (用户拍板).
按新数据模块分层 (获取/清洗/加工/存储 各司其职), 接入 pipeline acquire stage
(范例 = _sync_institution_survey)。本模块内部亦按阶段分函数:

  ① 获取 acquire  : _fetch_raw       — aif10 datacenter JSON API 拉某股全期 (纯采集)
  ② 清洗 clean    : _clean           — 字段映射 + change 解析 + share_class + K线范围过滤
  ③ 加工 process  : _derive_exits    — period-diff 推导退出行 (跟踪机构投资周期)
  ④ 存储 store    : sync_holders_aif10 — formal land→accept → canonical
                    (fact_top10_holder_period DROPPED 2026-07-26)

历史范围: 跟 K 线周期一致 (price_kline_qfq_tushare 2019-01-02 起) → 只回到覆盖它的
年报期 20181231; 更早无 K 线无法回测, 不抓 (用户 2026-06-24)。
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

# aif10_scraper (东财妙想) 在姊妹项目 ../miaoxiang
_MIAOXIANG = Path(__file__).resolve().parents[2] / "miaoxiang"
if str(_MIAOXIANG) not in sys.path:
    sys.path.insert(0, str(_MIAOXIANG))

REPORT_FREE = "RPT_F10_EH_FREEHOLDERS"   # 十大流通股东
PAGE_SIZE = 500                          # SQL LIMIT-like 单页上限 (东财 datacenter 支持)
SOURCE = "miaoxiang"                     # provider source tag on canonical rows
SOURCE_TIER = 1                          # evidence: 2026-06-24 用户裁决 aif10 提主源 (替 tdxhub)
# K线对齐: price_kline_qfq_tushare 2019-01-02 起 → holder 回到覆盖它的年报 20181231
DEFAULT_START_PERIOD = "20181231"        # evidence: K线起点 2019-01-02, 不抓更早 (用户 2026-06-24)

HOLDER_COLUMNS = (
    "stock_code, stock_name, market, report_date, holder_set, "
    "holder_rank, row_seq, holder_name, holder_name_norm, share_class, "
    "is_secondary_class, is_exit_row, "
    "shares_text, shares_approx, shares_precision, hold_amount, "
    "hold_ratio_float, hold_ratio_total, hold_ratio, "
    "hold_market_cap, holder_type, share_nature, "
    "change_status, change_shares_text, change_shares_approx, "
    "hold_change, hold_change_num, "
    "notice_date, effective_date, page_update_date, "
    "source, source_tier, raw_hash, fetched_at, created_at"
)
_COL_KEYS = [c.strip() for c in HOLDER_COLUMNS.split(",")]


# ── helpers ──────────────────────────────────────────────────────────
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_text(v):
    if v in (None, ""):
        return None
    t = str(v).strip()
    return t or None


def _safe_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    f = _safe_float(v)
    return int(round(f)) if f is not None else None


def _compact_date(v):
    t = _safe_text(v)
    if not t:
        return None
    digits = "".join(ch for ch in t if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _secucode(symbol: str) -> str:
    if "." in symbol:
        return symbol
    return f"{symbol}.SH" if symbol.startswith(("60", "68", "5", "11", "9")) else f"{symbol}.SZ"


def _share_class(shares_type) -> str:
    t = _safe_text(shares_type) or ""
    if "H" in t:
        return "H"
    if "B" in t:
        return "B"
    if "A" in t:
        return "A"
    return "_"


def _parse_change(raw):
    """HOLD_NUM_CHANGE 多态: '新进'/'不变'/正数(增持)/负数(减持) → (status, signed_shares)."""
    t = _safe_text(raw)
    if t is None:
        return "未知", None
    if t in ("新进", "新增"):
        return "新进", None
    if t == "不变":
        return "不变", 0
    n = _safe_int(raw)
    if n is None:
        return t, None
    if n > 0:
        return "增持", n
    if n < 0:
        return "减持", n
    return "不变", 0


# ── ① 获取 acquire ───────────────────────────────────────────────────
def _fetch_raw(client, symbol: str) -> list[dict]:
    """纯采集: aif10 datacenter 拉某股全期流通股东 (无计算)."""
    from aif10_scraper import fetch_all_pages
    return fetch_all_pages(REPORT_FREE, secucode=_secucode(symbol),
                           page_size=PAGE_SIZE, max_pages=0, client=client) or []


# ── ② 清洗 clean ─────────────────────────────────────────────────────
def _clean(raw: list[dict], *, start_period: str) -> list[dict]:
    """字段映射 → schema + change 解析 + share_class; 过滤 report_date < start_period (K线对齐)."""
    out = []
    fetched_at = _utc_now()
    for idx, row in enumerate(raw, start=1):
        stock_code = _safe_text(row.get("SECURITY_CODE"))
        report_date = _compact_date(row.get("END_DATE"))
        holder_name = _safe_text(row.get("HOLDER_NAME"))
        if not stock_code or not report_date or not holder_name:
            continue
        if report_date < start_period:          # K线范围外, 不抓 (用户 2026-06-24)
            continue
        shares = _safe_int(row.get("HOLD_NUM"))
        ratio = _safe_float(row.get("HOLD_RATIO"))   # 占流通比 %
        chg_status, chg_shares = _parse_change(row.get("HOLD_NUM_CHANGE"))
        holder_type = _safe_text(row.get("HOLDER_TYPE")) or _safe_text(row.get("HOLDER_NEWTYPE"))
        upd = _compact_date(row.get("UPDATE_DATE"))
        out.append({
            "stock_code": stock_code,
            "stock_name": _safe_text(row.get("SECURITY_NAME_ABBR")) or "",
            "market": "",
            "report_date": report_date,
            "holder_set": "free",
            "holder_rank": _safe_int(row.get("HOLDER_RANK")) or idx,
            "row_seq": 1,
            "holder_name": holder_name,
            "holder_name_norm": holder_name,
            "share_class": _share_class(row.get("SHARES_TYPE")),
            "is_secondary_class": False,
            "is_exit_row": False,
            "shares_text": None,
            "shares_approx": shares,
            "shares_precision": None,
            "hold_amount": float(shares) if shares is not None else None,
            "hold_ratio_float": ratio,
            "hold_ratio_total": None,
            "hold_ratio": ratio,                     # free → float
            "hold_market_cap": _safe_float(row.get("HOLDER_MARKET_CAP")),
            "holder_type": holder_type,
            "share_nature": _safe_text(row.get("SHARES_TYPE")),
            "change_status": chg_status,
            "change_shares_text": None,
            "change_shares_approx": chg_shares,
            "hold_change": "" if chg_status == "不变" else chg_status,
            "hold_change_num": float(chg_shares) if chg_shares is not None else None,
            "notice_date": upd,
            "effective_date": None,
            "page_update_date": upd,
            # PIT 可用日锚: 披露日(UPDATE_DATE)即可用日 → event_engine 据此算 available date+1
            "availability_source": "page_update_date" if upd else "fetched_at_observed",
            "source": SOURCE,
            "source_tier": SOURCE_TIER,
            "raw_hash": None,
            "fetched_at": fetched_at,
            "created_at": fetched_at,
        })
    from services.data_sources.holders_top10_schema import (
        assign_unique_holders_row_seq,
    )

    return assign_unique_holders_row_seq(out)


# ── ③ 加工 process ───────────────────────────────────────────────────
def _derive_exits(clean_rows: list[dict]) -> list[dict]:
    """period-diff: 上期在榜/本期不在 = 退出. 跟踪机构投资周期 (用户目的)."""
    from collections import defaultdict
    by_period: dict[str, dict] = defaultdict(dict)
    for r in clean_rows:
        by_period[r["report_date"]][r["holder_name"]] = r
    periods = sorted(by_period.keys())
    exits = []
    fetched_at = _utc_now()
    for i in range(1, len(periods)):
        cur, prev = periods[i], periods[i - 1]
        cur_names = set(by_period[cur].keys())
        # 退出在本期(cur)被获知 → 可用日=本期披露日 (任一本期在榜行的 page_update_date)
        cur_upd = next(iter(by_period[cur].values())).get("page_update_date")
        rank = 0
        for name, prev_row in by_period[prev].items():
            if name in cur_names:
                continue
            rank += 1
            e = dict(prev_row)
            e.update({
                "report_date": cur,
                "is_exit_row": True,
                "holder_rank": rank,
                "change_status": "退出",
                "change_shares_approx": -(prev_row.get("shares_approx") or 0),
                "hold_change": "退出",
                "hold_change_num": float(-(prev_row.get("shares_approx") or 0)),
                "notice_date": cur_upd,
                "page_update_date": cur_upd,
                "availability_source": "page_update_date" if cur_upd else "fetched_at_observed",
                "fetched_at": fetched_at,
                "created_at": fetched_at,
            })
            exits.append(e)
    return exits


def build_rows(client, symbol: str, *, start_period: str = DEFAULT_START_PERIOD) -> list[dict]:
    """获取→清洗→加工: 返回某股可写 canonical 的全部行 (含退出)."""
    raw = _fetch_raw(client, symbol)
    base = _clean(raw, start_period=start_period)
    if not base:
        return []
    return base + _derive_exits(base)


# ── ④ 存储 store ─────────────────────────────────────────────────────


def _write_legacy_direct(
    conn, rows: list[dict], *, as_mirror: bool = True
) -> int:
    """Retired: ``fact_top10_holder_period`` DROPped 2026-07-26.

    Formal path is land→accept only. Mirror / naked legacy writes fail closed.
    """
    del conn, rows, as_mirror
    raise RuntimeError(
        "holders_compat_retired: fact_top10_holder_period dropped; "
        "legacy mirror / direct write forbidden"
    )


def _write(conn, rows: list[dict]) -> int:
    """幂等写: formal land→accept by notice_date (formal_only; no legacy mirror).

    Canonical merges per notice_date so other stocks on the same partition are
    not wiped.  Enrichment columns ride on canonical.
    """
    if not rows:
        return 0
    from services.data_sources.disclosure_dual_write import (
        write_holders_top10_formal_then_mirror,
    )

    outcome = write_holders_top10_formal_then_mirror(conn, rows)
    return int(outcome.canonical_rows)


def accept_holders_top10_partition_from_legacy(conn, notice_date: str):
    """Retired: no fact plane to land from (2026-07-26 DROP)."""
    del conn, notice_date
    raise RuntimeError(
        "holders_compat_retired: accept_from_legacy forbidden after "
        "fact_top10_holder_period DROP; use provider forward land"
    )


def sync_holders_aif10(
    conn,
    *,
    symbols: Optional[Iterable[str]] = None,
    start_period: str = DEFAULT_START_PERIOD,
    limit: int = 0,
    progress_every: int = 200,
) -> dict:
    """编排 获取→清洗→加工→存储, formal land→accept → canonical (source='miaoxiang').

    symbols=None → 全 active universe; 否则只跑指定股 (调试/增量)。
    """
    from aif10_scraper import default_client
    client = default_client  # 模块级实例 (非工厂)

    if symbols is None:
        from services.universe import get_active_universe
        # holder=参考数据含活跃ST股 (沿用 da799268); conn=smart DB (ST名映射真相源)
        symbols = sorted(get_active_universe(conn, include_st=True))
    else:
        symbols = [s.strip() for s in symbols if s and s.strip()]
    if limit:
        symbols = symbols[:limit]

    t0 = time.time()
    ok = fail = total_rows = total_exits = 0
    errors: list[str] = []
    for i, sym in enumerate(symbols, 1):
        try:
            rows = build_rows(client, sym, start_period=start_period)
            if not rows:
                fail += 1
                continue
            total_exits += sum(1 for r in rows if r["is_exit_row"])
            total_rows += _write(conn, rows)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            if len(errors) < 20:
                errors.append(f"{sym}: {type(e).__name__}: {str(e)[:60]}")
        if progress_every and i % progress_every == 0:
            print(f"  [aif10-holders] {i}/{len(symbols)} ok={ok} fail={fail} "
                  f"rows={total_rows} ({time.time()-t0:.0f}s)")
    return {
        "ok": ok, "fail": fail, "rows_written": total_rows,
        "exit_rows": total_exits, "elapsed_s": round(time.time() - t0, 1),
        "start_period": start_period, "errors": errors,
    }


def fetch_holders_top10_by_notice_date(notice_date: str) -> list[dict]:
    """Full-market by UPDATE_DATE (= notice_date). Formal-shaped acquire for E0 land.

    Evidence 2026-07-21: ``RPT_F10_EH_FREEHOLDERS`` +
    ``(UPDATE_DATE='YYYY-MM-DD')`` returns ~10–120 provider rows/day
    (not mass). Preserves provider response (incl. BSE); no universe exclude.
    Exit rows are process-derived elsewhere — land path returns raw clean only
    (``is_exit_row=False``). Contrasts by_ts_code per-stock sync.
    """
    digits = "".join(ch for ch in str(notice_date or "") if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"notice_date must be YYYYMMDD; got {notice_date!r}")
    part = digits[:8]
    try:
        datetime.strptime(part, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"notice_date must be YYYYMMDD; got {notice_date!r}") from exc
    iso = f"{part[:4]}-{part[4:6]}-{part[6:8]}"
    from aif10_scraper import default_client, fetch_all_pages

    raw = fetch_all_pages(
        REPORT_FREE,
        page_size=PAGE_SIZE,
        max_pages=0,
        extra_filters=[f"(UPDATE_DATE='{iso}')"],
        client=default_client,
    ) or []
    cleaned = _clean(raw, start_period=DEFAULT_START_PERIOD)
    return [row for row in cleaned if row.get("notice_date") == part]


def _affected_stocks_since(client, since_date: str) -> list[str]:
    """市场级按披露日 UPDATE_DATE>=since 拉, 返回近期有新披露的股票代码 (日常增量)."""
    codes: set[str] = set()
    page = 1
    while True:
        r = client.get_v1(REPORT_FREE, page=page, page_size=PAGE_SIZE,
                          filter_expr=f"(UPDATE_DATE>='{since_date}')",
                          extra_params={"sortColumns": "UPDATE_DATE", "sortTypes": "-1"})
        data = r.get("data") or []
        for x in data:
            c = x.get("SECURITY_CODE")
            if c:
                codes.add(str(c))
        if not data or page >= (r.get("pages") or 1):
            break
        page += 1
    return sorted(codes)


def _provider_newest_update_date(
    client, *, since_yyyymmdd: str | None = None
) -> Optional[str]:
    """Newest provider UPDATE_DATE as YYYYMMDD (1-row probe). None if empty/error.

    Eastmoney datacenter returns 0 rows with empty filter; bound the sort probe
    with UPDATE_DATE>= floor derived from DEFAULT_START_PERIOD (measured 2026-07-22).
    """
    digits = "".join(ch for ch in str(since_yyyymmdd or DEFAULT_START_PERIOD) if ch.isdigit())
    if len(digits) < 8:
        digits = DEFAULT_START_PERIOD
    since_iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        r = client.get_v1(
            REPORT_FREE,
            page=1,
            page_size=1,
            filter_expr=f"(UPDATE_DATE>='{since_iso}')",  # rule-compliance: ok evidence=DEFAULT_START_PERIOD floor; empty filter returns 0 (measured 20260722)
            extra_params={"sortColumns": "UPDATE_DATE", "sortTypes": "-1"},
        )
    except Exception:  # noqa: BLE001 — probe only; fall through to full incremental
        return None
    data = r.get("data") or []
    if not data:
        return None
    raw = str(data[0].get("UPDATE_DATE") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


INCREMENT_SAFETY_DAYS = 7   # evidence: 水位回退重扫边界 catch 同日晚披露 + 东财修正 (幂等无害)

CANONICAL_TABLE = "canonical_top10_float_holders_period"

# Re-export provider forward fill (+ retired catchup stubs for test imports).
from services.holders_notice_catchup import (  # noqa: E402
    NOTICE_PARTITION_CATCHUP_MAX,
    catchup_missing_holders_notice_partitions,
    land_holders_notice_partitions_forward,
    list_missing_notice_partitions_from_fact,
)


def _table_present(conn, name: str) -> bool:
    try:
        r = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return r is not None
    except Exception:  # noqa: BLE001
        return False


def formal_holders_watermark(conn) -> tuple[Optional[str], str]:
    """Freshness watermark for holders = **formal accepted notice frontier**.

    SSOT = ``canonical_top10_float_holders_period.notice_date``. Legacy fact
    plane retired 2026-07-26 — no fallback.

    Returns ``(watermark_yyyymmdd_or_none, watermark_source)``.
    """
    if _table_present(conn, CANONICAL_TABLE):
        row = conn.execute(
            f"SELECT MAX(notice_date) FROM {CANONICAL_TABLE}"
        ).fetchone()
        if row and row[0]:
            return str(row[0]), "canonical_notice_frontier"
    return None, "empty"


def _net_new_notice_since(conn, pre_wm: Optional[str]) -> tuple[int, int]:
    """Split ops counters: net-new notice rows / partitions since ``pre_wm``.

    ``rows_written`` from the sync is per-stock full-history **rewrite
    amplification**, not net new disclosures.  This measures the honest
    net-new plane on the formal canonical frontier: rows whose
    ``notice_date > pre_wm``, and the distinct notice partitions they touch.
    Returns ``(net_new_notice_rows, notice_partitions_touched)``.
    """
    if not _table_present(conn, CANONICAL_TABLE):
        return 0, 0
    bound = pre_wm or "00000000"
    row = conn.execute(
        f"""
        SELECT COUNT(*), COUNT(DISTINCT notice_date)
          FROM {CANONICAL_TABLE}
         WHERE notice_date > ?
        """,
        [bound],
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _yyyymmdd_to_iso(yyyymmdd: str) -> str:
    digits = "".join(ch for ch in str(yyyymmdd or "") if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"expected YYYYMMDD, got {yyyymmdd!r}")
    part = digits[:8]
    return f"{part[:4]}-{part[4:6]}-{part[6:8]}"


def _local_stock_codes_for_notice_date(conn, notice_date: str) -> set[str]:
    """Formal-canonical codes already landed for ``notice_date`` (YYYYMMDD).

    Empty when canonical absent — fail-closed to treat all provider codes as
    missing so same-day sparse probe still runs.
    """
    if not _table_present(conn, CANONICAL_TABLE):
        return set()
    digits = "".join(ch for ch in str(notice_date or "") if ch.isdigit())
    if len(digits) < 8:
        return set()
    rows = conn.execute(
        f"""
        SELECT DISTINCT stock_code
          FROM {CANONICAL_TABLE}
         WHERE notice_date = ?
        """,
        [digits[:8]],
    ).fetchall()
    return {str(r[0]) for r in rows if r and r[0]}


def _incremental_skip_result(
    *,
    wm: Optional[str],
    wm_source: str,
    provider_max: Optional[str],
    since_date: str,
    skip_reason: str,
) -> dict:
    return {
        "ok": 0,
        "fail": 0,
        "rows_written": 0,
        "exit_rows": 0,
        "affected_stocks": 0,
        "net_new_notice_rows": 0,
        "notice_partitions_touched": 0,
        "rewrite_amplification_rows": 0,
        "watermark": wm,
        "watermark_source": wm_source,
        "provider_max_update_date": provider_max,
        "since_date": since_date,
        "skipped": True,
        "skip_reason": skip_reason,
        "errors": [],
    }


def sync_holders_aif10_incremental(
    conn, *, start_period: str = DEFAULT_START_PERIOD,
    safety_days: int = INCREMENT_SAFETY_DAYS,
    fallback_since: str = DEFAULT_START_PERIOD,
) -> dict:
    """日常增量 (水位驱动): 水位 = **formal accepted notice frontier** (canonical),
    扫 UPDATE_DATE >= 水位-safety 的股, 对这些股 per-stock 抓全期 → 退出推导 → 幂等覆盖.

    水位口径 (0r.5b): 盯 canonical ``notice_date`` (land→accept 形式前沿), 非 legacy
    fact ``page_update_date`` (formal_only sync 后滞后)。为何盯披露日 (非报告期): 报告期新必带
    新披露日 (盯披露⊇盯报告期); 东财修正旧期会刷披露日但报告期不变 → 披露日是充分水位。
    mythos §10: 应有(新披露)−实有(MAX披露日)=缺口, 自适应手动不规则运行。

    Skip rule (coverage alignment 2026-07-23):
    - ``provider_max < wm`` → skip (``watermark_unchanged``); provider behind formal.
    - ``provider_max == wm`` → same-day sparse: ``UPDATE_DATE>=wm`` codes missing that
      notice locally; sync only those. Empty miss → skip
      (``same_day_coverage_complete``). Never permanent-skip equal wm.
    - ``provider_max > wm`` (or probe None) → safety-window affected incremental.

    Ops 计数分栏 (audit §5 #2): ``rows_written`` = per-stock 全史 rewrite 放大量,
    **不是**净新增披露; 另报 ``net_new_notice_rows`` / ``notice_partitions_touched``
    (canonical notice_date > 同步前水位) 与 ``affected_stocks`` (窗口内股票数) 区分。

    Notice-axis (2026-07-26): from-fact catchup retired with fact DROP. When
    provider is ahead, forward-fill by ``UPDATE_DATE`` cross-section day-by-day.
    Per-stock path remains for revisions / same-day sparse.
    """
    from aif10_scraper import default_client
    client = default_client
    from services.data_sources.frontier_decision import decide_frontier

    # From-fact notice catchup retired with fact_top10 DROP (2026-07-26).
    # Provider forward fill covers tip advancement; hole repair = land_holders_notice_partitions_forward.

    wm, wm_source = formal_holders_watermark(conn)   # YYYYMMDD, formal frontier
    base = wm or fallback_since                        # 存量空 (未backfill) → 回退起点
    since_date = (datetime.strptime(base, "%Y%m%d") - timedelta(days=safety_days)).strftime("%Y-%m-%d")
    provider_max = _provider_newest_update_date(client)
    frontier = decide_frontier(
        axis="notice_date",
        local_max=wm,
        target_max=provider_max,
    )
    # Strictly behind formal: nothing new to probe (re-click heartbeat).
    if frontier.outcome == "skip_behind":
        print(
            f"holders_aif10: skip watermark_unchanged wm={wm} "
            f"provider_max={provider_max} source={wm_source}"
        )
        out = _incremental_skip_result(
            wm=wm,
            wm_source=wm_source,
            provider_max=provider_max,
            since_date=since_date,
            skip_reason="watermark_unchanged",
        )
        return out
    # Equal frontier: early filers already advanced wm; late same-day notices
    # still need a sparse miss probe (not safety-window mass rewrite).
    if frontier.outcome == "equal_day_population_gap":
        same_day_iso = _yyyymmdd_to_iso(wm)
        provider_codes = _affected_stocks_since(client, same_day_iso)
        local_codes = _local_stock_codes_for_notice_date(conn, wm)
        missing = sorted(code for code in provider_codes if code not in local_codes)
        if not missing:
            print(
                f"holders_aif10: skip same_day_coverage_complete wm={wm} "
                f"provider_max={provider_max} provider_codes={len(provider_codes)} "
                f"source={wm_source}"
            )
            out = _incremental_skip_result(
                wm=wm,
                wm_source=wm_source,
                provider_max=provider_max,
                since_date=same_day_iso,
                skip_reason="same_day_coverage_complete",
            )
            out["same_day_provider_codes"] = len(provider_codes)
            out["same_day_missing_codes"] = 0
            return out
        print(
            f"holders_aif10: same-day late-filer sparse "
            f"missing={len(missing)}/{len(provider_codes)} "
            f"wm={wm} provider_max={provider_max} since={same_day_iso}"
        )
        result = sync_holders_aif10(
            conn,
            symbols=missing,
            start_period=start_period,
            progress_every=10,
        )
        net_new_rows, notice_parts = _net_new_notice_since(conn, wm)
        result["affected_stocks"] = len(missing)
        result["net_new_notice_rows"] = net_new_rows
        result["notice_partitions_touched"] = notice_parts
        result["rewrite_amplification_rows"] = result.get("rows_written", 0)
        result["watermark"] = wm
        result["watermark_source"] = wm_source
        result["provider_max_update_date"] = provider_max
        result["since_date"] = same_day_iso
        result["same_day_sparse"] = True
        result["same_day_provider_codes"] = len(provider_codes)
        result["same_day_missing_codes"] = len(missing)
        return result
    # Provider ahead: day-by-day by_notice before per-stock rewrite (ann axis).
    forward = {"landed_partitions": [], "empty_partitions": [], "errors": []}
    if wm and provider_max and provider_max > wm:
        forward = land_holders_notice_partitions_forward(
            conn, from_exclusive=wm, to_inclusive=provider_max
        )
    affected = _affected_stocks_since(client, since_date)
    if not affected:
        return {"ok": 0, "fail": 0, "rows_written": 0, "exit_rows": 0,
                "affected_stocks": 0, "net_new_notice_rows": 0,
                "notice_partitions_touched": 0, "rewrite_amplification_rows": 0,
                "watermark": wm, "watermark_source": wm_source,
                "provider_max_update_date": provider_max,
                "since_date": since_date, "errors": [],
                "notice_partition_forward": forward}
    print(
        f"holders_aif10: start incremental affected={len(affected)} "
        f"wm={wm} provider_max={provider_max} since={since_date}"
    )
    result = sync_holders_aif10(
        conn,
        symbols=affected,
        start_period=start_period,
        progress_every=10,
    )
    net_new_rows, notice_parts = _net_new_notice_since(conn, wm)
    result["affected_stocks"] = len(affected)
    result["net_new_notice_rows"] = net_new_rows
    result["notice_partitions_touched"] = notice_parts
    # ``rows_written`` kept for back-compat but is rewrite amplification, not net new.
    result["rewrite_amplification_rows"] = result.get("rows_written", 0)
    result["watermark"] = wm
    result["watermark_source"] = wm_source
    result["provider_max_update_date"] = provider_max
    result["since_date"] = since_date
    result["notice_partition_forward"] = forward
    return result
