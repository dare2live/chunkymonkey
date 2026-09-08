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

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional


log = logging.getLogger(__name__)


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


def _is_holder_org(v) -> bool:
    """IS_HOLDORG -> bool。供应商给 1/0 (或其字符串形态), 实测零缺失。

    2026-09-07: 它是 holder_code 那一列 NULL 的**唯一解释项** —— 供应商只给机构编码,
    个人恒空 (实测 2018-12-31 起: 机构 829,249 行空 0 条; 个人 620,073 行空 620,073 条,
    边界无例外)。没有这一列, canonical 上一个 NULL 的 holder_code 就分不清
    「个人本来就没有」和「机构但没取到」, 违红线 3 (缺失必须可辨识)。
    所以这里**不做兜底猜测**: 认不出的取值直接抛, 不静默当 False。
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if int(v) in (0, 1):
            return bool(int(v))
        raise ValueError(f"IS_HOLDORG 数值越界: {v!r}")
    text = str(v).strip() if v is not None else ""
    if text in ("1", "true", "True", "TRUE"):
        return True
    if text in ("0", "false", "False", "FALSE"):
        return False
    raise ValueError(f"IS_HOLDORG 无法判定: {v!r}")


class UnknownHolderChangeStatusError(ValueError):
    """HOLD_NUM_CHANGE 不在闭合取值集(新进/不变/增持/减持,+派生退出)里 —— fail-closed
    (CLAUDE.md §11): 视为供应商 schema drift, 抛错而不是原样存进 canonical。"""


def _parse_change(raw):
    """HOLD_NUM_CHANGE 多态: '新进'/'不变'/正数(增持)/负数(减持) → (status, signed_shares).
    未知取值抛 ``UnknownHolderChangeStatusError``, 不再原样存成新状态。"""
    t = _safe_text(raw)
    if t is None:
        return "未知", None
    if t in ("新进", "新增"):
        return "新进", None
    if t == "不变":
        return "不变", 0
    n = _safe_int(raw)
    if n is None:
        raise UnknownHolderChangeStatusError(f"HOLD_NUM_CHANGE={raw!r} not in closed set")
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
        # 实测零例外 COALESCE(HOLDER_CODE,HOLDER_NAME) — process 阶段身份键, 落地白名单投影天然丢弃, 不改契约。
        holder_code = _safe_text(row.get("HOLDER_CODE"))
        holder_new = holder_code or holder_name
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
            # 2026-09-07 提升上 canonical (schema v3 / contract v4)。此前只做
            # process 阶段身份键、落地时被白名单投影丢弃, 于是 canonical 上只有名字 ——
            # 而名字在 35.2% 的行上不是稳定身份 (同码改名 4,467 个 code)。
            "holder_code": holder_code or None,
            "is_holder_org": _is_holder_org(row.get("IS_HOLDORG")),
            "holder_new": holder_new,  # extra key: process-stage identity only, see above
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
def _holder_identity(row: dict) -> str:
    """期间对比「同一人」键: holder_new, 无则退回 holder_name (兼容老数据)。按
    holder_name 判会把机构多种写法/更名(国泰君安→国泰海通)记成假「退出+新进」两条。"""
    return row.get("holder_new") or row.get("holder_name")


def _derive_exits(clean_rows: list[dict]) -> list[dict]:
    """period-diff: 上期在榜/本期不在 = 退出. 跟踪机构投资周期 (用户目的)."""
    from collections import defaultdict
    by_period: dict[str, dict] = defaultdict(dict)
    for r in clean_rows:
        by_period[r["report_date"]][_holder_identity(r)] = r
    periods = sorted(by_period.keys())
    exits = []
    fetched_at = _utc_now()
    for i in range(1, len(periods)):
        cur, prev = periods[i], periods[i - 1]
        cur_identities = set(by_period[cur].keys())
        # 退出在本期(cur)被获知 → 可用日=本期披露日 (任一本期在榜行的 page_update_date)
        cur_upd = next(iter(by_period[cur].values())).get("page_update_date")
        rank = 0
        for identity, prev_row in by_period[prev].items():
            if identity in cur_identities:
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


def _write(conn, rows: list[dict], *, delete_scope: str = "partition",
           derive_exits_from_canonical: bool = True) -> int:
    """幂等写: formal land→accept by notice_date (formal_only; no legacy mirror).

    同一 notice_date 上其他股票不会被抹掉 —— 两种做法结果相同, 代价差 590 倍:

    - ``delete_scope="partition"``(默认, 日更): accept 删整个分区, 上游先把分区里
      其他股票的行读出来拼进批次。日更按公告日全市场拉, 一个批次本就覆盖整天, 代价为零。
    - ``delete_scope="stocks_in_batch"``(按股回填): accept 只删本批的 stock_code,
      上游不必重读整个分区。**历史回填必须用这个** —— 2026-09-07 实测: 128,498 次
      (股,分区) 写入、目标 1,449,322 行, 按 "partition" 做法要实际写 854,850,658 行。

    Enrichment 列随 canonical 走。所有落地路径的收口点, 退出行派生补在这一步
    (见 ``_derive_exits_against_canonical``): 日更批次天生 is_exit_row=False 由此补上;
    全量按股重跑路径已在内存算过, no-op 不冲突。
    """
    if not rows:
        return 0
    # 2026-09-07 (fable 审查 Q2, 根因): 按股路径**根本不该**走 canonical 派生。
    # _derive_exits_against_canonical 的 `covered` 粒度是 (股,期), 而它的设计意图是
    # 「调用方已经按股整体算过了就别再算」。零退出期 + 首期 (仿真: 24,838 次 = 17.2%)
    # 因此漏网, 跑去跟 canonical 里的 v2 上一期比 —— 那才是 1,002 只股拒批的根因,
    # 上一个提交的「跳过 NULL 身份」只是把它的产物扔掉 (守卫, 不是修根因)。
    # 仿真实测: 按股路径关掉它之后, 输出**一行不差**(canonical 路径在 v2 基线产 0 行、
    # v3 基线产 0 行 0 skip); 少 24,838 次 ×2 次对 180 万行无索引表的查询。
    extra_exits = (
        _derive_exits_against_canonical(conn, rows) if derive_exits_from_canonical else []
    )
    if extra_exits:
        from services.data_sources.holders_top10_schema import assign_unique_holders_row_seq

        rows = list(rows) + assign_unique_holders_row_seq(extra_exits)
    from services.data_sources.disclosure_dual_write import (
        write_holders_top10_formal_then_mirror,
    )

    outcome = write_holders_top10_formal_then_mirror(
        conn, rows, delete_scope=delete_scope
    )
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
    delete_scope: str = "stocks_in_batch",
) -> dict:
    """编排 获取→清洗→加工→存储, formal land→accept → canonical (source='miaoxiang').

    symbols=None → 全 active universe; 否则只跑指定股 (调试/增量)。

    ``delete_scope`` 默认 ``"stocks_in_batch"`` 而不是 ``_write`` 的 ``"partition"``:
    本函数**结构上就是逐股**的 (``for sym in symbols`` 里一次写一只股的行), 拿到的批次
    永远不是某个 notice_date 的完整内容。用 ``"partition"`` 会在写第二只股时把第一只股
    刚写进同一公告日的行删掉, 跑完只剩最后一只 —— 参数留在签名上只为让调用方能显式覆盖,
    不是让它有第二个合理取值。

    2026-09-07: 本行此前写 ``delete_scope=delete_scope`` 却没有这个参数, 即
    ``NameError``。它没被任何测试抓到, 因为 ``sync_holders_aif10`` 在测试里**只作为
    monkeypatch 的目标**出现 (``test_holders_aif10.py`` 五处 setattr), 从未被真正执行。
    更坏的是失败形态: 异常被逐股 catch 进 ``errors`` (上限 20 条), 函数正常返回,
    CLI 打印 ``DONE`` 并退出 0 —— 5,447 只股全失败与全成功在退出码上一模一样。
    故本次同时加 ``ok == 0 and fail > 0`` 的显式失败 (见 ingest_holders_aif10.py)
    与一个真正调用本函数的测试。
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

    take_exit_derive_skips()  # 清掉上一轮残留, 本轮计数从零开始
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
            # rows 已含该股全史 + _derive_exits 内存派生 (由证据直接推出, 身份完整),
            # 不需要也不应该再去 canonical 跟上一期 diff —— 见 _write 里的说明。
            total_rows += _write(
                conn, rows, delete_scope=delete_scope, derive_exits_from_canonical=False
            )
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            if len(errors) < 20:
                errors.append(f"{sym}: {type(e).__name__}: {str(e)[:60]}")
        if progress_every and i % progress_every == 0:
            print(f"  [aif10-holders] {i}/{len(symbols)} ok={ok} fail={fail} "
                  f"rows={total_rows} ({time.time()-t0:.0f}s)")
    skips = take_exit_derive_skips()
    return {
        "ok": ok, "fail": fail, "rows_written": total_rows,
        "exit_rows": total_exits, "elapsed_s": round(time.time() - t0, 1),
        "start_period": start_period, "errors": errors,
        # 按股路径关掉了 canonical 派生, 所以这里应当恒为 0 —— 不为 0 说明有调用方
        # 传了 derive_exits_from_canonical=True。日更路径的同名计数在它自己的 result 里。
        "exit_derive_skipped": sum(x["n"] for x in skips),
        "exit_derive_skip_detail": skips[:20],
    }


class HoldersDuplicateGrainConflictError(RuntimeError):
    """同去重键两行内容不同(非逐字重复) —— 数据矛盾, 拒绝任选一行落地 (红线3)。"""


def _dedupe_notice_rows_by_grain(rows: list[dict]) -> tuple[list[dict], int]:
    """折叠 by_notice_date 全市场翻页拉重的逐字重复行 (只用于这条路径, 按股探针 0 组重复).

    根因: 排序键(END_DATE,HOLDER_RANK) 每期数百股并列, PAGE_SIZE=500 翻页边界随机、
    分页代码无去重 → 同记录跨页拉重(生产库 18,036 行)。去重键 = GRAIN 去掉 row_seq、
    换回 holder_name: row_seq 由 ``assign_unique_holders_row_seq`` 按到达顺序打号,
    逐字重复两行会被打成不同 row_seq, 满 GRAIN 反而抓不到; 但不换回 holder_name 的话,
    同 rank 两个不同持有人的合法并列(row_seq 正为此存在)会被误判成冲突。组内除
    row_seq 外全同 → 判定重复保留 1 条; 内容不同 → 抛
    ``HoldersDuplicateGrainConflictError`` 不任选一行。返回 (去重后的行, 去掉的行数)。
    """
    from services.data_sources.holders_top10_schema import GRAIN, assign_unique_holders_row_seq

    # 2026-09-08: GRAIN 加了 notice_date(版本轴), 这里同步。**行为不变** ——
    # 本函数只跑在 by_notice_date 的单日批次上 (fetch_holders_top10_by_notice_date 里
    # `same_day` 已按 partition 过滤), notice_date 在批内恒定, 加进键不改变任何分组。
    # 但键必须忠于 GRAIN, 这正是下面那道漂移守卫存在的意义 —— 它今天抓到了我改 GRAIN
    # 却没回头看这里 (fable 的方案表里也漏了这一处)。
    expected = {"stock_code", "report_date", "notice_date", "holder_set", "holder_rank",
                "is_exit_row"}
    if frozenset(GRAIN) - {"row_seq"} != expected:
        # GRAIN 定义漂移: fail-closed 而不是悄悄按旧假设去重 (CLAUDE.md §11)。
        raise RuntimeError(f"holders_top10_schema.GRAIN drifted from {sorted(expected)!r}; "
                           "_dedupe_notice_rows_by_grain's key must be revisited")

    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in rows:
        key = (str(row.get("stock_code") or ""), str(row.get("report_date") or ""),
               str(row.get("notice_date") or ""),
               str(row.get("holder_set") or ""), int(row.get("holder_rank") or 0),
               bool(row.get("is_exit_row")), str(row.get("holder_name") or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    deduped: list[dict] = []
    removed = 0
    for key in order:
        group = groups[key]
        baseline = {k: v for k, v in group[0].items() if k != "row_seq"}
        for other in group[1:]:
            other_content = {k: v for k, v in other.items() if k != "row_seq"}
            if other_content != baseline:
                raise HoldersDuplicateGrainConflictError(
                    f"grain-key={key!r}: {baseline!r} vs {other_content!r}")
        deduped.append(group[0])
        removed += len(group) - 1

    return (assign_unique_holders_row_seq(deduped) if removed else deduped), removed


def fetch_holders_top10_by_notice_date(notice_date: str) -> list[dict]:
    """Full-market by UPDATE_DATE (= notice_date). Formal-shaped acquire for E0 land.

    Evidence 2026-07-21: ``RPT_F10_EH_FREEHOLDERS`` +
    ``(UPDATE_DATE='YYYY-MM-DD')`` returns ~10–120 provider rows/day
    (not mass). Preserves provider response (incl. BSE); no universe exclude.
    Exit rows are process-derived elsewhere — land path returns raw clean only
    (``is_exit_row=False``). Contrasts by_ts_code per-stock sync.

    Pagination here is unstable at (END_DATE, HOLDER_RANK) ties and duplicates
    rows across pages — ``_dedupe_notice_rows_by_grain`` collapses those first.
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
    same_day = [row for row in cleaned if row.get("notice_date") == part]
    deduped, removed = _dedupe_notice_rows_by_grain(same_day)
    if removed:
        print(f"holders_aif10: by_notice_date={part} dropped {removed} paged-duplicate rows")
    return deduped


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


# 退出派生因「上一期身份未记录」而跳过的记录。规则 12: 运行时计数不写进手写文件,
# 但也不能只活在 log 里 —— 调用方要能拿到它并放进自己的 result dict。
# 退出行要从上一期**原样带出**的持仓字段(不置空)。理由见 _derive_exits_against_canonical
# 里 null_fields 处的长注释: 它们的定义就是「退出前最后一次真实披露的值」。
CARRY_FIELDS: tuple[str, ...] = (
    "hold_ratio_float", "shares_approx", "hold_amount", "hold_market_cap",
)

_EXIT_DERIVE_SKIPS: list[dict] = []


def take_exit_derive_skips() -> list[dict]:
    """取走并清空跳过记录 (调用方在一轮开始前清、结束后取)。"""
    out = list(_EXIT_DERIVE_SKIPS)
    _EXIT_DERIVE_SKIPS.clear()
    return out


def _derive_exits_against_canonical(conn, rows: list[dict]) -> list[dict]:
    """日更单日落地退出派生: 查 canonical 该股上一期名单做 period-diff, 只查本批
    (stock_code,report_date), 不做全市场重扫(避免 26M 行放大重演); 跳过批次里已自带
    退出行的组合(全量按股重跑路径已在内存用 holder_new 算过, 更准)。

    2026-09-07 修两处, 都是 canonical 拿到 holder_code 之后才可能修的:

    1. **退出行曾经带着别人的 code**(数据污染, 本次回填实测 21,453 行 = 带码退出行的
       11.2%)。原实现 ``e = dict(template)`` 拿当期**第一行**做模板, 之后只覆盖
       holder_name, 于是 holder_code / is_holder_org 留着模板行的值 ——
       「胡利平」因此挂上了香港中央结算的码 10671586, 那个码下面一度挂着 172 种名字
       (含中信证券、高瓴资本、以及一个自然人)。
       canonical 此前不存 code 时这个模板复制无害; 加了 code 之后它变成污染路径。
       现在退出者带**自己的** code/is_holder_org, 从 canonical 上一期原样取。

    2. **改名防护**。原 docstring 写「canonical 不存 HOLDER_CODE, 比对只能按 holder_name,
       没有 holder_new 的改名防护」—— 那句话在 2026-09-07 之前是对的。现在按
       ``COALESCE(holder_code, holder_name)`` 比对(与内存路径 ``_holder_identity`` 同口径),
       机构改名不再被记成「退出 + 新进」两条假事件。实测同一 code 用过多个名字的有 4,467 个。
    """
    if not rows:
        return []
    covered = {(str(r.get("stock_code") or ""), str(r.get("report_date") or ""))
               for r in rows if r.get("is_exit_row")}
    by_stock_period: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (str(r.get("stock_code") or ""), str(r.get("report_date") or ""))
        if not r.get("is_exit_row") and key[0] and key[1] and key not in covered:
            by_stock_period.setdefault(key, []).append(r)
    if not by_stock_period or not _table_present(conn, CANONICAL_TABLE):
        return []

    # 2026-09-08: hold_ratio_float / shares_approx / hold_amount / hold_market_cap
    # 从这里移出去 —— 它们要带出上一期的值, 不置空。
    #
    # 内存路径 _derive_exits (`e = dict(prev_row)`) 一直是原样带出的; canonical 路径置空
    # 是 2026-09-06 引入本函数时的疏漏(提交信息没提要改这几个字段的语义, null_fields 也没留理由),
    # 不是深思后的重新裁决 —— 结果是同一种派生行两套语义, 在回填边界日翻转。
    #
    # 「退出行的持股比例」有明确含义: **退出前最后一次真实披露的比例**, 是历史事实,
    # 只用过去数据不违红线 1; 也不是红线 3 管的那种缺失(红线 3 管「该观测到却没观测到」,
    # 而退出行本身是派生行, 它这几个字段的定义就是「上一期值」)。
    # 置 0 会撒谎(暗示比例真是 0); 置 NULL 对唯一的消费方是静默降级 ——
    # stock_dossier.py:389-396 注释明文写着契约「hold_ratio_float = 上期在榜占比(最后已知)」,
    # 界面会从有数字变空白。只有原样带出同时满足「不撒谎」与「消费方能用」。
    # 核实过没有消费方把它当「当期真实持仓」加总: institution_follow_b4_measure.py:202
    # 在打分前就把 is_exit_row 整体剔除; institution_profile.py:265 只透传不做运算。
    null_fields = ("share_class", "shares_text", "shares_precision",
                   "hold_ratio_total", "hold_ratio",
                   "change_shares_text", "change_shares_approx",
                   "hold_change_num", "effective_date")
    derived: list[dict] = []
    fetched_at = _utc_now()
    for (stock_code, report_date), cur_rows in by_stock_period.items():
        prev = conn.execute(
            f"SELECT MAX(report_date) FROM {CANONICAL_TABLE} WHERE stock_code=? "
            "AND is_exit_row=FALSE AND report_date<?", [stock_code, report_date]).fetchone()
        db_prev = prev[0] if prev else None

        # 2026-09-08: 同批双期链式派生。
        # 上面那句只看 **canonical 已接受**的上一期, 完全看不到同一批次里更早的那期。
        # 年报 + 一季报同日披露时(staging 实测 15,881 个 (股,UPDATE_DATE) 对同日双期,
        # 约 2,500/年, 占相邻期对 10.8%), 后一期永远拿 canonical 里更老的那期当基准,
        # 于是「上一期在榜、本期不在」的那些持有人的退出被整个吞掉。
        # 复现: canonical 有 20230930{A,B}, 批次含 20231231{A,C} + 20240331{A,B}
        #   修前 -> [(20231231,B)]          20240331 拿 20230930 当基准, C 的退出被吞
        #   修后 -> [(20231231,B), (20240331,C)]
        batch_prev = max(
            (k[1] for k in by_stock_period
             if k[0] == stock_code and k[1] < report_date),
            default=None,
        )
        use_batch = batch_prev is not None and (db_prev is None or batch_prev > db_prev)
        prev_period = batch_prev if use_batch else db_prev
        if not prev_period:
            continue  # 该股没有更早的期(库里和批内都没有), 没有基准可 diff

        if use_batch:
            # 批内那期的行自带身份与持仓字段, 不必回查表(它还没落库)。
            prev_rows = [
                (r.get("holder_name"), r.get("holder_code"), r.get("is_holder_org"),
                 *(r.get(f) for f in CARRY_FIELDS))
                for r in by_stock_period[(stock_code, prev_period)]
            ]
        else:
            prev_rows = conn.execute(
                f"SELECT DISTINCT holder_name, holder_code, is_holder_org, "
                f"{', '.join(CARRY_FIELDS)} FROM {CANONICAL_TABLE} "
                "WHERE stock_code=? AND report_date=? AND is_exit_row=FALSE",
                [stock_code, prev_period]).fetchall()
        # 身份键与内存路径 _holder_identity 同口径: 有 code 用 code, 没有退回 name。
        prev_by_identity = {
            (str(r[1]) if r[1] else str(r[0])): {
                "holder_name": str(r[0]),
                "holder_code": str(r[1]) if r[1] else None,
                "is_holder_org": r[2],
                **{f: r[3 + i] for i, f in enumerate(CARRY_FIELDS)},
            }
            for r in prev_rows if r and r[0]
        }
        cur_identities = {
            str(r.get("holder_code") or "") or str(r.get("holder_name") or "")
            for r in cur_rows
        }
        gone = sorted(set(prev_by_identity) - cur_identities)
        # 上一期是 v2 遗留行时 is_holder_org 为 NULL(当时这一列不存在, 身份未记录)。
        # 造不出合规的 v3 退出行 —— accept 侧要求它必须是 bool (INVALID_HOLDER_ORG_FLAG)。
        # 三条路只有跳过是诚实的:
        #   放宽校验 -> 让「身份未判的行」重新能写进来, 正是那道门要挡的;
        #   按名字猜 org/个人 -> 红线 3 禁止 (缺失不许填,不许 fallback);
        #   跳过 -> 少一条**派生**行, 而派生物可从证据重生成 (红线 4)。
        # 计数不静默: 回填期间上一期几乎都是 v2, 跳过量应当很大且随回填推进归零;
        # 2026-09-08 更正解读: 非 0 **不等于**「有 v2 行没被覆盖到」。
        # 真机制是 accept 侧 DELETE 按 notice_date=partition 划范围, 供应商把同一
        # (股,期) 改派到别的公告日时, 旧行留在一个此后任何批次都不会再落地的分区里 ——
        # 那不是「没轮到覆盖」, 是这条 DELETE 的作用域结构性够不到它, 且日更也会触发,
        # 不是回填期独有的历史欠账。(notice_date 已于 2026-09-08 进 GRAIN, 两版现在共存
        # 不再互相挡路; 但旧版身份未记录时仍会走到这里被跳过。)
        # 非 0 时该查的是「该 identity 在 canonical 里是否横跨多个 notice_date」,
        # 而不是假设「该刷一遍了」。
        skipped_unknown = [i for i in gone if prev_by_identity[i]["is_holder_org"] is None]
        if skipped_unknown:
            # 计数也回传给调用方: 只写 log 时它是静默的 —— 回填脚本与 ingest CLI 都没有
            # logging.basicConfig, root 停在 WARNING, 这行一个字都打不出来 (fable 审查 Q3 末)。
            _EXIT_DERIVE_SKIPS.append(
                {"stock_code": stock_code, "report_date": report_date,
                 "prev_period": prev_period, "n": len(skipped_unknown)}
            )
            log.info(
                "holders exit-derive skip %d holders on %s/%s: prev period %s is pre-v3 "
                "(identity unrecorded)",
                len(skipped_unknown), stock_code, report_date, prev_period,
            )
        gone = [i for i in gone if prev_by_identity[i]["is_holder_org"] is not None]
        if not gone:
            continue
        template = cur_rows[0]
        notice = template.get("notice_date") or template.get("page_update_date")
        for rank, identity in enumerate(gone, start=1):
            src = prev_by_identity[identity]
            e = dict(template)
            e.update(dict.fromkeys(null_fields))
            e.update({
                "holder_name": src["holder_name"], "holder_name_norm": src["holder_name"],
                # 退出者带**自己的**身份, 不是模板行的 —— 模板只提供 stock/notice 这类
                # 与持有人无关的字段。2026-09-07 之前这三行不存在, 于是退出行继承了
                # cur_rows[0] 的 code, 实测污染 21,453 行。
                "holder_code": src["holder_code"],
                "is_holder_org": src["is_holder_org"],
                # 退出前最后一次真实披露的持仓, 见上方 null_fields 处的裁决。
                **{f: src[f] for f in CARRY_FIELDS},
                "holder_new": identity,
                "report_date": report_date, "is_exit_row": True,
                "holder_rank": rank, "row_seq": 1,
                "change_status": "退出", "hold_change": "退出",
                "notice_date": notice, "page_update_date": notice,
                "availability_source": "page_update_date" if notice else "fetched_at_observed",
                "fetched_at": fetched_at, "created_at": fetched_at,
            })
            derived.append(e)
    return derived


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
