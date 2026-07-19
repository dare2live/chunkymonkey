"""十大流通股东 — 东方财富妙想 aif10 数据源服务 (主源, 2026-06-24).

源决策: analysis/miaoxiang_aif10_source_decision_20260624.md (用户拍板).
按新数据模块分层 (获取/清洗/加工/存储 各司其职), 接入 pipeline acquire stage
(范例 = _sync_institution_survey)。本模块内部亦按阶段分函数:

  ① 获取 acquire  : _fetch_raw       — aif10 datacenter JSON API 拉某股全期 (纯采集)
  ② 清洗 clean    : _clean           — 字段映射 + change 解析 + share_class + K线范围过滤
  ③ 加工 process  : _derive_exits    — period-diff 推导退出行 (跟踪机构投资周期)
  ④ 存储 store    : sync_holders_aif10 — 幂等写 fact_top10_holder_period (source='miaoxiang')

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
SOURCE = "miaoxiang"                     # from yaml: schema_core fact_top10_holder_period.source 枚举
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
    return out


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
    """获取→清洗→加工: 返回某股可写 fact_top10_holder_period 的全部行 (含退出)."""
    raw = _fetch_raw(client, symbol)
    base = _clean(raw, start_period=start_period)
    if not base:
        return []
    return base + _derive_exits(base)


# ── ④ 存储 store ─────────────────────────────────────────────────────
_AVAIL_COL_CACHE: dict = {}


def _has_availability_col(conn) -> bool:
    """availability_source 列经迁移加入 (非 schema_core 基表 CREATE), 条件写匹配 tdxhub writer."""
    if "v" not in _AVAIL_COL_CACHE:
        try:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='fact_top10_holder_period'").fetchall()}
            _AVAIL_COL_CACHE["v"] = "availability_source" in cols
        except Exception:  # noqa: BLE001
            _AVAIL_COL_CACHE["v"] = False
    return _AVAIL_COL_CACHE["v"]


def _write(conn, rows: list[dict]) -> int:
    """幂等写: 单股全量重写 (rows 为同一股全期). 按 (stock, source) 一次性删旧再插,
    避免 per-row DELETE (backfill 1.4M 次→5400 次)。build_rows 返该股全期, 故整体替换正确.

    E0: direct fact write is NONCONFORMING (no landing→accept).  Formal/accepted
    claims fail closed via authorize_nonconforming_direct_write.
    """
    if not rows:
        return 0
    from services.data_sources.disclosure_boundaries import (
        authorize_nonconforming_direct_write,
    )

    authorize_nonconforming_direct_write("holders_top10", conformity="NONCONFORMING")
    stock = rows[0]["stock_code"]
    conn.execute(
        "DELETE FROM fact_top10_holder_period WHERE stock_code=? AND source=?",
        (stock, SOURCE),
    )
    keys = list(_COL_KEYS)
    cols = HOLDER_COLUMNS
    if _has_availability_col(conn):   # PIT 可用日锚, event_engine 据此算 available date
        keys.append("availability_source")
        cols = f"{HOLDER_COLUMNS}, availability_source"
    placeholders = ", ".join("?" for _ in keys)
    insert_sql = f"INSERT INTO fact_top10_holder_period({cols}) VALUES ({placeholders})"
    conn.executemany(insert_sql, [tuple(r.get(k) for k in keys) for r in rows])
    return len(rows)


def sync_holders_aif10(
    conn,
    *,
    symbols: Optional[Iterable[str]] = None,
    start_period: str = DEFAULT_START_PERIOD,
    limit: int = 0,
    progress_every: int = 200,
) -> dict:
    """编排 获取→清洗→加工→存储, 写 fact_top10_holder_period (source='miaoxiang').

    symbols=None → 全 active universe; 否则只跑指定股 (调试/增量)。
    幂等: 重跑只覆盖 source='miaoxiang' 行, 不动其它源。
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


INCREMENT_SAFETY_DAYS = 7   # evidence: 水位回退重扫边界 catch 同日晚披露 + 东财修正 (幂等无害)


def sync_holders_aif10_incremental(
    conn, *, start_period: str = DEFAULT_START_PERIOD,
    safety_days: int = INCREMENT_SAFETY_DAYS,
    fallback_since: str = DEFAULT_START_PERIOD,
) -> dict:
    """日常增量 (水位驱动): 水位 = 存量 MAX(披露日 page_update_date),
    扫 UPDATE_DATE >= 水位-safety 的股, 对这些股 per-stock 抓全期 → 退出推导 → 幂等覆盖.

    为何盯披露日 (非报告期): 报告期新必带新披露日 (盯披露⊇盯报告期); 东财修正旧期会刷披露日但
    报告期不变 (盯报告期会漏修正) → 披露日是充分水位。mythos §10: 应有(新披露)−实有(MAX披露日)=缺口,
    自适应手动不规则运行 (间隔越长水位自动回退越远, 不会因固定窗漏)。
    """
    from aif10_scraper import default_client
    client = default_client
    row = conn.execute(
        "SELECT MAX(page_update_date) FROM fact_top10_holder_period WHERE source=?",
        (SOURCE,)).fetchone()
    wm = row[0] if row and row[0] else None   # YYYYMMDD
    base = wm or fallback_since                 # 存量空 (未backfill) → 回退起点
    since_date = (datetime.strptime(base, "%Y%m%d") - timedelta(days=safety_days)).strftime("%Y-%m-%d")
    affected = _affected_stocks_since(client, since_date)
    if not affected:
        return {"ok": 0, "fail": 0, "rows_written": 0, "exit_rows": 0,
                "affected_stocks": 0, "watermark": wm, "since_date": since_date, "errors": []}
    result = sync_holders_aif10(conn, symbols=affected, start_period=start_period,
                                progress_every=0)
    result["affected_stocks"] = len(affected)
    result["watermark"] = wm
    result["since_date"] = since_date
    return result
