"""十大流通股东 — 东方财富妙想 aif10 数据源服务 (主源, 2026-06-24).

源决策: git log --grep miaoxiang_aif10_source_decision (用户拍板).
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

import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from services.data_sources.sibling_repos import ensure_import_path

ensure_import_path("miaoxiang")

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
    except Exception:  # noqa: BLE001 — probe only; caller must not mass-rewrite
        return None
    data = r.get("data") or []
    if not data:
        return None
    raw = str(data[0].get("UPDATE_DATE") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


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

    Honest net-new plane on the formal canonical frontier: rows whose
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


def _notice_row_stock_codes(rows: list[dict]) -> list[str]:
    codes: set[str] = set()
    for row in rows:
        code = str(row.get("stock_code") or "").strip()
        if code:
            codes.add(code)
    return sorted(codes)


def sync_holders_aif10_incremental(
    conn, *, start_period: str = DEFAULT_START_PERIOD,
    fallback_since: str = DEFAULT_START_PERIOD,
) -> dict:
    """日常增量: canonical notice frontier + exact-day ``UPDATE_DATE='YYYY-MM-DD'``.

    Formal acquire grain is by_notice_date (MASTER §5.7 / disclosure_transport).
    Daily path never selects stocks via ``UPDATE_DATE>=`` then per-stock full
    history — that rewrite lands every historical notice partition as a full-day
    snapshot (2026-08-26: 538 names → 26M landing rows). Per-stock full history
    stays on explicit ``ingest_holders_aif10.py --symbols/--backfill`` only.

    - ``provider_max < wm`` → skip ``watermark_unchanged``
    - ``provider_max == wm`` → exact-day by_notice; skip if local codes cover
      provider codes; else re-land that one notice_date
    - ``provider_max > wm`` → ``land_holders_notice_partitions_forward`` only
    - empty canonical / unknown provider_max → skip no-mass (no bootstrap rewrite)
    """
    del start_period  # daily path does not per-stock fetch; kept so callers stay stable
    from aif10_scraper import default_client
    client = default_client
    from services.data_sources.frontier_decision import decide_frontier

    wm, wm_source = formal_holders_watermark(conn)
    since_date = _yyyymmdd_to_iso(wm or fallback_since)
    provider_max = _provider_newest_update_date(client)
    frontier = decide_frontier(
        axis="notice_date",
        local_max=wm,
        target_max=provider_max,
    )
    if frontier.outcome == "skip_behind":
        print(
            f"holders_aif10: skip watermark_unchanged wm={wm} "
            f"provider_max={provider_max} source={wm_source}"
        )
        return _incremental_skip_result(
            wm=wm,
            wm_source=wm_source,
            provider_max=provider_max,
            since_date=since_date,
            skip_reason="watermark_unchanged",
        )
    if frontier.outcome == "equal_day_population_gap":
        same_day_iso = _yyyymmdd_to_iso(wm)
        day_rows = fetch_holders_top10_by_notice_date(wm)
        provider_codes = _notice_row_stock_codes(day_rows)
        local_codes = _local_stock_codes_for_notice_date(conn, wm)
        missing = [code for code in provider_codes if code not in local_codes]
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
            f"holders_aif10: same-day late-filer by_notice "
            f"missing={len(missing)}/{len(provider_codes)} "
            f"wm={wm} provider_max={provider_max}"
        )
        written = _write(conn, day_rows)
        net_new_rows, notice_parts = _net_new_notice_since(conn, wm)
        return {
            "ok": 1 if written else 0,
            "fail": 0,
            "rows_written": written,
            "exit_rows": 0,
            "affected_stocks": 0,
            "net_new_notice_rows": net_new_rows,
            "notice_partitions_touched": notice_parts,
            "rewrite_amplification_rows": 0,
            "watermark": wm,
            "watermark_source": wm_source,
            "provider_max_update_date": provider_max,
            "since_date": same_day_iso,
            "same_day_sparse": True,
            "same_day_provider_codes": len(provider_codes),
            "same_day_missing_codes": len(missing),
            "errors": [],
        }
    if not wm:
        print(
            "holders_aif10: skip empty_canonical_no_mass "
            "(explicit ingest --backfill, not daily per-stock)"
        )
        return _incremental_skip_result(
            wm=wm,
            wm_source=wm_source,
            provider_max=provider_max,
            since_date=since_date,
            skip_reason="empty_canonical_no_mass",
        )
    if not provider_max:
        print(
            f"holders_aif10: skip provider_max_unknown_no_mass wm={wm} "
            f"source={wm_source}"
        )
        return _incremental_skip_result(
            wm=wm,
            wm_source=wm_source,
            provider_max=provider_max,
            since_date=since_date,
            skip_reason="provider_max_unknown_no_mass",
        )
    print(
        f"holders_aif10: advance by_notice wm={wm} provider_max={provider_max} "
        f"source={wm_source}"
    )
    forward = land_holders_notice_partitions_forward(
        conn, from_exclusive=wm, to_inclusive=provider_max
    )
    net_new_rows, notice_parts = _net_new_notice_since(conn, wm)
    landed = list(forward.get("landed_partitions") or [])
    errors = list(forward.get("errors") or [])
    return {
        "ok": len(landed),
        "fail": len(errors),
        "rows_written": 0,
        "exit_rows": 0,
        "affected_stocks": 0,
        "net_new_notice_rows": net_new_rows,
        "notice_partitions_touched": notice_parts,
        "rewrite_amplification_rows": 0,
        "watermark": wm,
        "watermark_source": wm_source,
        "provider_max_update_date": provider_max,
        "since_date": since_date,
        "errors": errors,
        "notice_partition_forward": forward,
    }
