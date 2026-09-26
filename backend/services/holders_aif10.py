"""十大流通股东 — 东方财富妙想 aif10 数据源服务 (主源, 2026-06-24; 刀 B1 重写 2026-09-26).

源决策: git log --grep miaoxiang_aif10_source_decision (用户拍板)。
坐实报告: sandbox/p1_specs_20260925/spec_holders_pagination.md (刀 B1, §4)。

按新数据模块分层 (获取/清洗/加工/存储 各司其职):

  ① 获取 acquire  : _fetch_raw / fetch_holders_notice_day — 分别是按股全史与
                    按公告日整市场两条采集路径 (后者经严格翻页引擎
                    aif10_scraper.pagination.fetch_pages_strict)
  ② 清洗 clean    : _clean           — 字段映射 + change 解析 + share_class + K线范围过滤
  ③ 加工 process  : diff_notice_day / _derive_exits(_against_canonical) — 差集与退出行派生
  ④ 存储 store    : sync_holders_aif10（按股全史）/ recheck_notice_day（按日, 日更与回补共用）

历史范围: 跟 K 线周期一致 (price_kline_qfq_tushare 2019-01-02 起) → 只回到覆盖它的
年报期 20181231; 更早无 K 线无法回测, 不抓 (用户 2026-06-24)。

── 按公告日的观测模型 (刀 B1, spec §4.1 三条规则) ──────────────────────────

1. **每次取数是一个带 landed_at 的观察, 只追加。** canonical 每个 GRAIN
   (stock_code, report_date, notice_date, holder_set, holder_rank, row_seq,
   is_exit_row) 是它第一次被接受的观测; 此后任何取数都不更新、不删除观测行
   (is_exit_row=FALSE)。供应商后来多给的行 (missing) 是新观察, 插进来;
   供应商后来改了内容的行 (revised) 落 landing 记账, canonical 不动 (保留
   D 日的原始观测); 供应商后来不给的行 (surplus/moved) 只报不动。

2. **两个时间轴都声明** (红线 1): notice_date (供应商 UPDATE_DATE, 市场可见日;
   available_at = D 18:00 Asia/Shanghai) 与 ingest_batch.landed_at (我们何时
   知道)。经 merge_new_grains 写入的 is_exit_row=FALSE 行满足 as-of 承诺:
   `SELECT ... JOIN ingest_batch ON ingest_batch_id = batch_id WHERE landed_at
   <= t_known` 在固定 t_known 下不因后续取数而改变已在其中的行的批次归属
   (B31)。这条承诺**不**延伸到派生行/去重/手动全史替换 —— 退出行在有新观测
   时会被整组重算替换 (走这条重算路径本身是 as-of 安全的: 它只用
   notice_date <= D 已公开的证据); B2 dedup_local 与 sync_holders_aif10 的
   按股全史替换路径各自的删除都记 mart_data_deletion_record, 不在本条承诺
   覆盖范围内 (opus 复核 U3)。`available_at` 不是"无消费方"的孤儿列 ——
   institution_follow_b4_measure.py / stock_dossier.py 都读它当市场可见时刻
   用 (opus 复核 V7, 修订原 docstring 的过度声明)。

3. **派生行可再生** (红线 4 / 契约 origin: derived): 退出行 is_exit_row=TRUE
   按"批内有新观测行的 (stock_code, report_date) 组"整组重算并替换; 重算只用
   notice_date <= D 已公开的上一期与那一版 (R4)。没有新观测的组 (held-only,
   即本次只发现 revised/surplus/dup) 不重算, 旧退出行原样保留。

到期集合改由账本驱动 (services/holders_notice_ledger.py): 「日历
[exposure_start .. provider_max] 减去已 settled 的日子」取代旧的
「MAX(notice_date) 水位 + 前向/同日两分支」——失败的日子永远留在到期集合里,
谁都盖不掉谁 (R1/R3)。
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo


log = logging.getLogger(__name__)


REPORT_FREE = "RPT_F10_EH_FREEHOLDERS"   # 十大流通股东
SOURCE = "miaoxiang"                     # provider source tag on canonical rows
SOURCE_TIER = 1                          # evidence: 2026-06-24 用户裁决 aif10 提主源 (替 tdxhub)
# K线对齐: price_kline_qfq_tushare 2019-01-02 起 → holder 回到覆盖它的年报 20181231
DEFAULT_START_PERIOD = "20181231"        # evidence: K线起点 2019-01-02, 不抓更早 (用户 2026-06-24)

from services.data_sources.aif10_pagination_rules import (  # noqa: E402
    load_aif10_pagination_rules,
    page_size_for,
    policy_for,
)
from services.holders_notice_catchup import MAX_DUE_DAYS_PER_RUN  # noqa: E402  (V9: 单一定义处)

# 全部在 import 时求值 —— YAML 坏了 import 就炸 (与刀 A §3.5 fail-closed 一致)。
_RULES = load_aif10_pagination_rules()
PAGE_SIZE = page_size_for(REPORT_FREE, rules=_RULES)          # 保留名字, ingest_holders_raw.py 导入它
_POLICY = policy_for(REPORT_FREE, rules=_RULES)
_RECHECK = _RULES.holders_notice_recheck
EXPOSURE_START = _RULES.audits["holders_notice_pagination"].exposure_start


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


def _drop_vendor_excluded(raw: list[dict]) -> list[dict]:
    """Drop vendor rows for a range-outside category (B股, owner ruling
    2026-09-12) before any cleaning/dedup — see ``vendor_scope.yaml``
    ``aif10.holders_top10`` (刀 B2, 2026-09-19). ``VendorScopeError`` is
    deliberately not caught here: an unregistered acquiring path is an
    unaddressed unknown, not "nothing to exclude" (宪法红线3: 缺失只能传播为
    缺失), same fail-closed contract as ``sources/miaoxiang.py::fetch_raw``.
    """
    from services.data_sources.vendor_scope import apply_response_excludes, vendor_exclusions

    exclusions = vendor_exclusions("aif10", "holders_top10")
    filtered, excluded = apply_response_excludes(raw, exclusions)
    # Deliberate: only log when excluded>0 (same convention as miaoxiang.py) —
    # a zero-exclusion call has no observable state change to report.
    if excluded:
        log.info("aif10.holders_top10 excluded %d rows by vendor_scope", excluded)
    return filtered


# ── ① 获取 acquire (按股全史; 手动 backfill 路径, 未改) ───────────────────
def _fetch_raw(client, symbol: str) -> list[dict]:
    """纯采集: aif10 datacenter 拉某股全期流通股东 (无计算)."""
    from aif10_scraper import fetch_all_pages
    raw = fetch_all_pages(REPORT_FREE, secucode=_secucode(symbol),
                          page_size=PAGE_SIZE, max_pages=0, client=client) or []
    return _drop_vendor_excluded(raw)


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
            # PIT 可用日锚: 披露日(UPDATE_DATE)即可用日 → event_engine 据此算可用日+1
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
    """period-diff: 上期在榜/本期不在 = 退出. 跟踪机构投资周期 (用户目的)。

    只用于**按股全史**路径 (build_rows → sync_holders_aif10): 一次拿到某只股
    的全部历史期, 在内存里整体 diff, 不查库。按公告日的日更/回补路径走
    ``_derive_exits_against_canonical`` (对着 canonical 上一期查, 只处理批内
    真正有新观测的组)。
    """
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
    """获取→清洗→加工: 返回某股可写 canonical 的全部行 (含退出)。按股全史路径专用。"""
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


def _write_with_outcome(
    conn,
    rows: list[dict],
    *,
    delete_scope: str = "partition",
    derive_exits_from_canonical: bool = True,
    touched_groups: frozenset = frozenset(),
    acquire_evidence: Mapping[str, Any],
):
    """幂等写: formal land→accept by notice_date (formal_only; no legacy mirror)。

    返回完整 ``DisclosureDualWriteOutcome`` (含 inserted_rows / held_rows /
    exit_rows_replaced / batch_ids); ``_write`` 是它的窄接口 (只返回行数,
    向后兼容既有调用方)。``rows`` 为空时返回 ``None`` (无批次产生)。

    ``acquire_evidence`` 必填, 透传进 ``request_json`` (B9; 键不得覆盖
    ``api``/``notice_date``/``source``, 由 ``disclosure_dual_write`` 校验)。
    """
    if not rows:
        return None
    # 只有 merge_new_grains 路径 (日更/回补) 才需要按 touched_groups 派生退出;
    # 按股全史路径 (derive_exits_from_canonical=False) 已经在内存算过 (_derive_exits),
    # 传空 touched_groups 时本函数不产出任何退出行 (fail-safe 默认, 不是「自动发现」)。
    extra_exits = (
        _derive_exits_against_canonical(conn, rows, touched_groups=touched_groups)
        if derive_exits_from_canonical else []
    )
    if extra_exits:
        from services.data_sources.holders_top10_schema import assign_unique_holders_row_seq

        rows = list(rows) + assign_unique_holders_row_seq(extra_exits)
    from services.data_sources.disclosure_dual_write import (
        write_holders_top10_formal_then_mirror,
    )

    return write_holders_top10_formal_then_mirror(
        conn, rows, delete_scope=delete_scope, request_extra=acquire_evidence,
    )


def _write(
    conn,
    rows: list[dict],
    *,
    delete_scope: str = "partition",
    derive_exits_from_canonical: bool = True,
    touched_groups: frozenset = frozenset(),
    acquire_evidence: Mapping[str, Any],
) -> int:
    """``_write_with_outcome`` 的窄接口: 只返回写入后 canonical 分区行数
    (既有调用方期望的返回类型不变)。
    """
    outcome = _write_with_outcome(
        conn,
        rows,
        delete_scope=delete_scope,
        derive_exits_from_canonical=derive_exits_from_canonical,
        touched_groups=touched_groups,
        acquire_evidence=acquire_evidence,
    )
    return int(outcome.canonical_rows) if outcome is not None else 0


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
    """编排 获取→清洗→加工→存储, formal land→accept → canonical (source='miaoxiang')。

    symbols=None → 全 active universe; 否则只跑指定股 (调试/增量)。

    ``delete_scope`` 默认 ``"stocks_in_batch"`` 而不是 ``_write`` 的 ``"partition"``:
    本函数**结构上就是逐股**的 (``for sym in symbols`` 里一次写一只股的行), 拿到的批次
    永远不是某个 notice_date 的完整内容。用 ``"partition"`` 会在写第二只股时把第一只股
    刚写进同一公告日的行删掉, 跑完只剩最后一只 —— 参数留在签名上只为让调用方能显式覆盖,
    不是让它有第二个合理取值。

    这是**唯一**保留的替换路径, 只由 ``ingest_holders_aif10.py --symbols/--backfill``
    手动触发 —— 替换 = 重观测整只股的全史, 不是日更路径; 日更与回补 (``recheck_notice_day``
    / ``reland_stock_in_day``) 一律 ``merge_new_grains`` 只增不删 (刀 B1)。

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
            # 不需要也不应该再去 canonical 跟上一期 diff —— 见 _write_with_outcome 说明。
            total_rows += _write(
                conn, rows, delete_scope=delete_scope, derive_exits_from_canonical=False,
                acquire_evidence={
                    "acquire_path": "by_stock", "run_kind": "backfill",
                    "observation_kind": "full_history",
                },
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


# ── 按公告日整市场取数 (刀 B1; spec §4.2) ─────────────────────────────────


@dataclass(frozen=True)
class NoticeDayFetch:
    rows: list[dict]
    ledger: Any  # aif10_scraper.pagination.PageLedger


def _validate_notice_date(notice_date: str) -> str:
    digits = "".join(ch for ch in str(notice_date or "") if ch.isdigit())
    if len(digits) < 8:
        raise ValueError(f"notice_date must be YYYYMMDD; got {notice_date!r}")
    part = digits[:8]
    try:
        datetime.strptime(part, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"notice_date must be YYYYMMDD; got {notice_date!r}") from exc
    return part


def fetch_holders_notice_day(notice_date: str, *, client) -> NoticeDayFetch:
    """按公告日整市场翻页取一天 (严格引擎, 唯一入口, 与 ``sources/miaoxiang.py``
    同一姿态)。``client`` 必填 (N9: 脚本/调用方必须显式构造客户端, 不许悄悄退回
    某个默认单例)。

    完整性判定 (总行数=去重后行数=供应商声明总数、翻页中途漂移整日重取一次)
    发生在引擎**内部**, 在 ``_drop_vendor_excluded`` 与 ``_clean`` 之前
    (B2_integrity_judged_before_exclusion_and_clean): 供应商声明的总数在
    B股排除/K线范围过滤之前就必须精确对上, 排除/过滤是引擎判完之后的下一步,
    不能反过来靠"排除后数字对上了"掩盖翻页本身漏行。
    """
    from aif10_scraper.pagination import fetch_pages_strict

    part = _validate_notice_date(notice_date)
    iso = f"{part[:4]}-{part[4:6]}-{part[6:8]}"

    raw, ledger = fetch_pages_strict(
        client,
        REPORT_FREE,
        page_size=PAGE_SIZE,
        policy=_POLICY,
        extra_filters=[f"(UPDATE_DATE='{iso}')"],
    )
    filtered = _drop_vendor_excluded(raw)
    cleaned = _clean(filtered, start_period=DEFAULT_START_PERIOD)
    same_day = [row for row in cleaned if row.get("notice_date") == part]
    return NoticeDayFetch(rows=same_day, ledger=ledger)


_DAILY_CLIENT = None  # lazy module-level singleton (惰性, 避免 import 时建 requests.Session)


def _daily_client():
    global _DAILY_CLIENT
    if _DAILY_CLIENT is None:
        from aif10_scraper import AIF10Client

        _DAILY_CLIENT = AIF10Client(retry=3, rate_limit=1.6, timeout=20)
    return _DAILY_CLIENT


def fetch_holders_top10_by_notice_date(notice_date: str) -> list[dict]:
    """Full-market by UPDATE_DATE (= notice_date)。保留给 ``disclosure_transport.py``
    (provider land-only CLI 路径) 与既有测试用; 生产日更/回补一律走
    :func:`fetch_holders_notice_day` 并显式传 client (N9)。"""
    return fetch_holders_notice_day(notice_date, client=_daily_client()).rows


class HoldersProviderProbeError(RuntimeError):
    """探针失败: 不是"code 0 且有行", 或早报的日期超出合理上界 (T5) ——
    fail-closed, 不吞异常继续跑到期集合 (R2)。``AIF10BlockedError`` 不在此列,
    原样上抛 (调用方据此整条 sync 停止)。"""


def _provider_newest_update_date(client) -> str:
    """探针取供应商最新 UPDATE_DATE (YYYYMMDD)。

    只接受"code 0 且有行"; 客户端抛出的任何异常 (含 ``code=9501`` 的
    ``AIF10ApiError``、未知码的 ``AIF10UnknownCodeError``) 都转型
    ``HoldersProviderProbeError``, 除了 ``AIF10BlockedError`` 原样上抛
    (R2, B8/B8b)。结果超出上海今天 + 2 天视为不可信 (T5: 长假前供应商偶发
    提前打未来日期), 同样抛 ``HoldersProviderProbeError`` (B8c)。
    """
    from aif10_scraper import AIF10BlockedError

    since_iso = f"{EXPOSURE_START[:4]}-{EXPOSURE_START[4:6]}-{EXPOSURE_START[6:8]}"
    try:
        r = client.get_v1(
            REPORT_FREE,
            page=1,
            page_size=1,
            filter_expr=f"(UPDATE_DATE>='{since_iso}')",  # rule-compliance: ok evidence=EXPOSURE_START floor; empty filter returns 0 (measured 20260722)
            extra_params={"sortColumns": "UPDATE_DATE", "sortTypes": "-1"},
        )
    except AIF10BlockedError:
        raise
    except Exception as exc:  # noqa: BLE001 — 探针只认"成功", 其余一律转型 fail-closed
        raise HoldersProviderProbeError(f"{type(exc).__name__}: {exc}") from exc
    data = (r or {}).get("data") or []
    if not data:
        raise HoldersProviderProbeError("provider probe returned no rows (code!=0 already raised above)")
    raw = str(data[0].get("UPDATE_DATE") or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8:
        raise HoldersProviderProbeError(f"provider probe UPDATE_DATE unparsable: {raw!r}")
    provider_max = digits[:8]
    today_shanghai = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    upper_bound = (today_shanghai + timedelta(days=2)).strftime("%Y%m%d")
    if provider_max > upper_bound:
        raise HoldersProviderProbeError(
            f"provider probe UPDATE_DATE={provider_max} exceeds upper bound "
            f"{upper_bound} (今天+2, T5)"
        )
    return provider_max


CANONICAL_TABLE = "canonical_top10_float_holders_period"


def _table_present(conn, name: str) -> bool:
    try:
        r = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return r is not None
    except Exception:  # noqa: BLE001
        return False


# ── 本地索引与差集 (刀 B1 重写; spec §4.2) ────────────────────────────────

_LOCAL_OBSERVATION_COLUMNS = (
    "stock_code", "report_date", "holder_set", "holder_rank", "row_seq",
    "holder_name", "holder_code", "is_holder_org", "hold_ratio_float",
    "shares_approx", "change_status", "hold_change_num", "holder_type",
    "share_class",
)

# 观测列 (revised 判定用; spec §4.1 表): 六个观测列逐一比, 本地 is_holder_org
# 为 NULL (v2 遗留行, 身份未记录) 时再比 holder_code/is_holder_org, 否则也比
# (身份修订也是修订)。
_OBSERVATION_COLUMNS = (
    "hold_ratio_float", "shares_approx", "change_status", "hold_change_num",
    "holder_type", "share_class",
)
_FLOAT_OBSERVATION_COLUMNS = frozenset({"hold_ratio_float"})


def _holder_key(row: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(row.get("stock_code") or ""),
        str(row.get("report_date") or ""),
        int(row.get("holder_rank") or 0),
        str(row.get("holder_name") or ""),
    )


@dataclass(frozen=True)
class LocalRow:
    row_seq: int
    holder_name: str
    holder_code: Optional[str]
    is_holder_org: Optional[bool]
    hold_ratio_float: Optional[float]
    shares_approx: Optional[int]
    change_status: Optional[str]
    hold_change_num: Optional[float]
    holder_type: Optional[str]
    share_class: Optional[str]


@dataclass(frozen=True)
class LocalIndex:
    by_key: Mapping[tuple, tuple[LocalRow, ...]]
    group_max_seq: Mapping[tuple, int]


def _local_observation_index(conn, notice_date: str, *, stock_code: str | None = None) -> LocalIndex:
    """一次查询取某个 notice_date 分区当前的非退出观测行, 按 :func:`_holder_key` 建索引。

    ``stock_code`` 非空时只取该股 (``reland_stock_in_day`` 按股回补用)。
    """
    query = (
        f"SELECT {', '.join(_LOCAL_OBSERVATION_COLUMNS)} FROM {CANONICAL_TABLE} "
        "WHERE notice_date = ? AND is_exit_row = FALSE"
    )
    params: list[Any] = [notice_date]
    if stock_code is not None:
        query += " AND stock_code = ?"
        params.append(stock_code)
    query += " ORDER BY row_seq"
    rows = conn.execute(query, params).fetchall() if _table_present(conn, CANONICAL_TABLE) else []

    by_key: dict[tuple, list[LocalRow]] = defaultdict(list)
    group_max_seq: dict[tuple, int] = {}
    for r in rows:
        (stock, report, holder_set, holder_rank, row_seq, holder_name, holder_code,
         is_holder_org, hold_ratio_float, shares_approx, change_status,
         hold_change_num, holder_type, share_class) = r
        key = (str(stock), str(report), int(holder_rank), str(holder_name))
        by_key[key].append(
            LocalRow(
                row_seq=int(row_seq), holder_name=str(holder_name), holder_code=holder_code,
                is_holder_org=is_holder_org, hold_ratio_float=hold_ratio_float,
                shares_approx=shares_approx, change_status=change_status,
                hold_change_num=hold_change_num, holder_type=holder_type, share_class=share_class,
            )
        )
        gkey = (str(stock), str(report), str(holder_set), int(holder_rank), False)
        group_max_seq[gkey] = max(group_max_seq.get(gkey, 0), int(row_seq))
    return LocalIndex(
        by_key={k: tuple(v) for k, v in by_key.items()},
        group_max_seq=group_max_seq,
    )


def _local_keys_elsewhere(conn, notice_date: str, keys: Iterable[tuple]) -> frozenset:
    """一次查询: 给定键集合里, 哪些键在**另一个** notice_date 下也存在 (moved 判定)。"""
    keys = list(keys)
    if not keys or not _table_present(conn, CANONICAL_TABLE):
        return frozenset()
    stock_codes = sorted({k[0] for k in keys})
    marks = ", ".join("?" for _ in stock_codes)
    rows = conn.execute(
        f"""
        SELECT stock_code, report_date, holder_rank, holder_name
          FROM {CANONICAL_TABLE}
         WHERE notice_date != ? AND is_exit_row = FALSE
           AND stock_code IN ({marks})
        """,
        [notice_date, *stock_codes],
    ).fetchall()
    elsewhere = {(str(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in rows}
    return frozenset(k for k in keys if k in elsewhere)


def _floats_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 1e-9


def _observation_differs(prov_row: Mapping[str, Any], local_row: LocalRow) -> bool:
    """六个观测列逐一比; 浮点容差 1e-9, 两边都 None 视为同。本地 is_holder_org
    为 NULL (v2 遗留行) 时不比身份列, 否则也比 (身份修订也是修订)。"""
    for field in _OBSERVATION_COLUMNS:
        prov_value = prov_row.get(field)
        local_value = getattr(local_row, field)
        if field in _FLOAT_OBSERVATION_COLUMNS:
            if not _floats_equal(prov_value, local_value):
                return True
        elif (prov_value or None) != (local_value or None):
            return True
    if local_row.is_holder_org is not None:
        if (prov_row.get("holder_code") or None) != (local_row.holder_code or None):
            return True
        if prov_row.get("is_holder_org") != local_row.is_holder_org:
            return True
    return False


@dataclass(frozen=True)
class NoticeDayDiff:
    notice_date: str
    provider_rows: tuple[dict, ...]
    local_rows: int
    local_keys: frozenset
    missing_keys: frozenset
    revised_keys: frozenset
    surplus_keys: frozenset
    moved_keys: frozenset
    dup_groups: int
    dup_rows: int
    touched_groups: frozenset
    missing_stocks: frozenset
    partial_stocks: frozenset


def diff_notice_day(
    provider_rows: list[dict], local: LocalIndex, *, keys_elsewhere: Iterable[tuple]
) -> NoticeDayDiff:
    """四种"事后"形态的差集 (spec §4.1 表): missing / revised / surplus(+moved) / dup。

    ``prov`` 按 :func:`_holder_key` 建索引, 键数必须等于行数 —— 引擎已经在
    ``fetch_holders_notice_day`` 里保证了供应商声明的身份键唯一, 违反 (即清洗后
    出现"清洗后键不唯一", 例如两个原始名字只差首尾空白) 是数据矛盾, 抛
    ``RuntimeError`` (V14: 调用方归为账本 ``failed reason=clean_key_collision``,
    只让这一天失败, 不拖垮其它日期)。
    """
    prov: dict[tuple, dict] = {}
    for row in provider_rows:
        key = _holder_key(row)
        if key in prov:
            raise RuntimeError(f"diff_notice_day: 清洗后键不唯一 (clean_key_collision): {key!r}")
        prov[key] = row

    local_keys = frozenset(local.by_key.keys())
    prov_keys = frozenset(prov.keys())

    missing_keys = prov_keys - local_keys
    revised_keys = frozenset(
        k for k in (prov_keys & local_keys) if _observation_differs(prov[k], local.by_key[k][0])
    )
    surplus_keys = local_keys - prov_keys
    moved_keys = surplus_keys & frozenset(keys_elsewhere)

    dup_groups = 0
    dup_rows = 0
    for rows_at_key in local.by_key.values():
        if len(rows_at_key) > 1:
            dup_groups += 1
            dup_rows += len(rows_at_key) - 1

    touched_groups = frozenset((k[0], k[1]) for k in missing_keys)

    local_stocks = {k[0] for k in local_keys}
    missing_by_stock: dict[str, int] = defaultdict(int)
    for k in missing_keys:
        missing_by_stock[k[0]] += 1
    missing_stocks = frozenset(s for s in missing_by_stock if s not in local_stocks)
    partial_stocks = frozenset(s for s in missing_by_stock if s in local_stocks)

    notice_date = ""
    if provider_rows:
        notice_date = str(provider_rows[0].get("notice_date") or "")

    return NoticeDayDiff(
        notice_date=notice_date,
        provider_rows=tuple(provider_rows),
        local_rows=sum(len(v) for v in local.by_key.values()),
        local_keys=local_keys,
        missing_keys=missing_keys,
        revised_keys=revised_keys,
        surplus_keys=surplus_keys,
        moved_keys=moved_keys,
        dup_groups=dup_groups,
        dup_rows=dup_rows,
        touched_groups=touched_groups,
        missing_stocks=missing_stocks,
        partial_stocks=partial_stocks,
    )


def _rows_to_land(
    diff: NoticeDayDiff, provider_rows_by_key: Mapping[tuple, dict], local: LocalIndex
) -> list[dict]:
    """missing 行按组续号 (同组内按 holder_name 排序, 从 ``group_max_seq`` 之后接续);
    revised 行沿用本地已有的 row_seq (让 accept 按 GRAIN 判定"已存在"而持有)。

    **不**调用 ``assign_unique_holders_row_seq`` —— 它从 1 重编号, 会让同名次的
    新持有人撞上已有 row_seq (B25)。
    """
    out: list[dict] = []

    by_group: dict[tuple, list[tuple[str, tuple]]] = defaultdict(list)
    for key in diff.missing_keys:
        stock_code, report_date, holder_rank, holder_name = key
        row = provider_rows_by_key[key]
        holder_set = str(row.get("holder_set") or "")
        group = (stock_code, report_date, holder_set, holder_rank, False)
        by_group[group].append((holder_name, key))

    for group, members in by_group.items():
        members.sort(key=lambda item: item[0])
        base_seq = local.group_max_seq.get(group, 0)
        for offset, (_holder_name, key) in enumerate(members, start=1):
            row = dict(provider_rows_by_key[key])
            row["row_seq"] = base_seq + offset
            out.append(row)

    for key in diff.revised_keys:
        row = dict(provider_rows_by_key[key])
        row["row_seq"] = local.by_key[key][0].row_seq
        out.append(row)

    return out


# ── 退出行派生 (按日路径; 只对 touched_groups 重算, B26/B26b/B27) ─────────

# 退出派生因「上一期身份未记录」而跳过的记录。规则 12: 运行时计数不写进手写文件,
# 但也不能只活在 log 里 —— 调用方要能拿到它并放进自己的 result dict。
# 退出行要从上一期**原样带出**的持仓字段(不置空)。理由见 _derive_exits_against_canonical
# 里 null_fields 处的长注释: 它们的定义就是「退出前最后一次真实披露的值」。
#
# 2026-09-18: 这里必须只列 canonical **实际持久化**的列。CARRY_FIELDS 曾经还包含
# hold_amount / hold_market_cap —— 那两个字段确实存在于 _clean() 产出的内存行里
# (build_rows/_derive_exits 的全内存路径能看到它们), 但从未进过 canonical 的 schema
# (holders_top10_schema.CANONICAL_ROW_FIELDS = PROVIDER_FIELDS + ENRICHMENT_FIELDS,
# 23 列里没有这两个)。_derive_exits_against_canonical 的非同批分支要对着**真实**
# canonical 表跑 `SELECT ...CARRY_FIELDS... FROM {CANONICAL_TABLE}`, 列不存在时
# DuckDB 直接 BinderException —— 2026-09-18 生产实测: 这条路径连续 13 天在每个命中
# 的 notice_date 上重复炸, sync_holders_aif10_incremental 把它收进 result["errors"]
# 却从未有人转成 degraded (见 pipeline/acquire.py:_sync_holders_aif10), 于是日更
# exit 0、十大股东静默停摆 13 天。修法: CARRY_FIELDS 收窄到真实列; 新增的
# ``_assert_carry_fields_in_canonical`` 对再次漂移 fail-closed (CLAUDE.md §11),
# 一次性抛清楚缺哪列, 不再让 DuckDB 的 BinderException 逐日期重复炸。
CARRY_FIELDS: tuple[str, ...] = ("hold_ratio_float", "shares_approx")

_EXIT_DERIVE_SKIPS: list[dict] = []


class HoldersCarryFieldSchemaError(RuntimeError):
    """CARRY_FIELDS 声明要带出的字段不在 canonical 实际列集合里 —— fail-closed
    (CLAUDE.md §11): 一次性抛清楚缺哪列, 不让 DuckDB BinderException 在每个命中的
    日期上重复炸 (2026-09-18 holders_aif10 静默 13 天的根因)。"""


def _assert_carry_fields_in_canonical(
    conn, fields: Optional[tuple[str, ...]] = None
) -> None:
    """CARRY_FIELDS 每个字段都必须是 ``CANONICAL_TABLE`` 的真实列, 调用查询前先查清楚。

    ``fields`` 缺省时读**当前**模块级 ``CARRY_FIELDS`` (不是 def 时绑定的默认参数) ——
    否则 monkeypatch 模块属性来测漂移场景会静默失效: 默认参数值只在函数定义那一刻
    求值一次, 之后 patch 模块属性也换不掉它。
    """
    if fields is None:
        fields = CARRY_FIELDS
    actual = {
        str(row[0])
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [CANONICAL_TABLE],
        ).fetchall()
    }
    missing = [f for f in fields if f not in actual]
    if missing:
        raise HoldersCarryFieldSchemaError(
            f"{CANONICAL_TABLE} missing CARRY_FIELDS column(s) {missing!r}; "
            "CARRY_FIELDS must only list fields persisted on canonical "
            "(see holders_top10_schema.CANONICAL_ROW_FIELDS)"
        )


def take_exit_derive_skips() -> list[dict]:
    """取走并清空跳过记录 (调用方在一轮开始前清、结束后取)。"""
    out = list(_EXIT_DERIVE_SKIPS)
    _EXIT_DERIVE_SKIPS.clear()
    return out


def _derive_exits_against_canonical(
    conn, rows: list[dict], *, touched_groups: frozenset = frozenset()
) -> list[dict]:
    """按日路径的退出派生: 只对 ``touched_groups`` (批内真正有新观测行 GRAIN 的组,
    即 diff 的 ``missing_keys`` 所在组) 重算; held-only 的组 (本次批次里这个
    (股,期) 一行新观测都没有, 只有 revised/无关行) **不**重算, 旧退出行原样保留
    (B26b —— 一次只发现修订/撤回的取数不该让整组持有人突然被判"退出")。

    组内"今天还在"的判据 = 批内该组非退出行 ∪ canonical 该组
    ``notice_date = D`` (本批的公告日) 的非退出行 (B26: 一行迟到的行不能让
    同组已经在 canonical 里的其它九个持有人都被判退出 —— 它们不在本批 ``rows``
    里, 只在 canonical 里)。code 与 name 分开记两个集合, 不并成单一
    "code-or-name" token (B17c 返修): 同一组在不同批次里可能混着两种身份口径
    写法 —— D 分区里若这个机构是 v2 遗留行 (``holder_code`` 为 NULL, 身份当时
    只记了名字), 而它在上一期是带 ``holder_code`` 的 v3/v4 行, 上一期持有人
    只要 code 或 name 任一还在今天出现过就不算退出, 不靠两边身份口径一致。

    上一期 = ``notice_date <= D`` 的最近一期**与那一版** (R4, B17/B17b):
    先按 ``report_date < 当前 AND notice_date <= D`` 找最近的 ``(report_date,
    notice_date)`` 组合, 再按这**三键**精确取那一版的持有人 —— 不是"该
    report_date 下所有版本的并集" (旧 bug: 一个 report_date 有多版时会把 D
    之后才公开的重述版也算进来, 见 B17b)。``batch_prev`` (同批双期) 逻辑保留。
    """
    if not rows or not touched_groups:
        return []
    by_stock_period: dict[tuple[str, str], list[dict]] = defaultdict(list)
    notice_by_group: dict[tuple[str, str], str] = {}
    for r in rows:
        if r.get("is_exit_row"):
            continue
        key = (str(r.get("stock_code") or ""), str(r.get("report_date") or ""))
        if key not in touched_groups:
            continue
        by_stock_period[key].append(r)
        nd = str(r.get("notice_date") or "")
        if nd:
            notice_by_group[key] = nd
    if not by_stock_period or not _table_present(conn, CANONICAL_TABLE):
        return []
    # 一次性查清楚 CARRY_FIELDS 是否都是 canonical 真实列 —— 不通过就 fail-closed
    # 抛出去, 不进下面的循环让 SELECT 对每个命中的 (股,期) 重复炸 BinderException。
    _assert_carry_fields_in_canonical(conn)

    null_fields = ("share_class", "shares_text", "shares_precision",
                   "hold_ratio_total", "hold_ratio",
                   "change_shares_text", "change_shares_approx",
                   "hold_change_num", "effective_date")
    derived: list[dict] = []
    fetched_at = _utc_now()
    for (stock_code, report_date), cur_rows in by_stock_period.items():
        cur_nd = notice_by_group.get((stock_code, report_date))

        # cur_identities: 批内该组非退出行 ∪ canonical 该组 notice_date=D 的非退出行。
        # 分开记 code 与 name 两个集合, 不并成单一 "code-or-name" token (返修
        # blocking finding, B17c): 同一 (股,期) 组在不同 notice_date 批次里可能
        # 混着两种身份口径写法 —— D 分区里这个机构若是 v2 遗留行 (holder_code 为
        # NULL, 身份当时只记了名字), 而它在上一期是带 holder_code 的 v3/v4 行,
        # 单一 token 比较 ("C1" vs "甲") 永远对不上, 会把仍在榜的机构误判成
        # gone。改成: 上一期持有人只要 code 或 name 任一还在今天出现过就不算退出。
        canon_cur_rows: list[tuple] = []
        if cur_nd:
            canon_cur_rows = conn.execute(
                f"SELECT holder_name, holder_code FROM {CANONICAL_TABLE} "
                "WHERE stock_code=? AND report_date=? AND notice_date=? AND is_exit_row=FALSE",
                [stock_code, report_date, cur_nd],
            ).fetchall()
        cur_codes = {
            str(r.get("holder_code")) for r in cur_rows if r.get("holder_code")
        } | {str(c[1]) for c in canon_cur_rows if c[1]}
        cur_names = {
            str(r.get("holder_name")) for r in cur_rows if r.get("holder_name")
        } | {str(c[0]) for c in canon_cur_rows if c[0]}

        # 上一期: notice_date <= D (cur_nd) 的最近 (report_date, notice_date) 组合。
        prev = conn.execute(
            f"SELECT report_date, notice_date FROM {CANONICAL_TABLE} WHERE stock_code=? "
            "AND is_exit_row=FALSE AND report_date<? AND notice_date<=? "
            "ORDER BY report_date DESC, notice_date DESC LIMIT 1",
            [stock_code, report_date, cur_nd],
        ).fetchone()
        db_prev_period, db_prev_notice = (prev[0], prev[1]) if prev else (None, None)

        # 2026-09-08: 同批双期链式派生。
        # 上面那句只看 **canonical 已接受**的上一期, 完全看不到同一批次里更早的那期。
        # 年报 + 一季报同日披露时(staging 实测 15,881 个 (股,UPDATE_DATE) 对同日双期,
        # 约 2,500/年, 占相邻期对 10.8%), 后一期永远拿 canonical 里更老的那期当基准,
        # 于是「上一期在榜、本期不在」的那些持有人的退出被整个吞掉。
        batch_prev = max(
            (k[1] for k in by_stock_period
             if k[0] == stock_code and k[1] < report_date),
            default=None,
        )
        use_batch = batch_prev is not None and (db_prev_period is None or batch_prev > db_prev_period)
        prev_period = batch_prev if use_batch else db_prev_period
        if not prev_period:
            continue  # 该股在 D 之前没有已公开的更早期(库里和批内都没有), 没有基准可 diff

        if use_batch:
            # 批内那期的行自带身份与持仓字段, 不必回查表(它还没落库)。
            prev_rows = [
                (r.get("holder_name"), r.get("holder_code"), r.get("is_holder_org"),
                 *(r.get(f) for f in CARRY_FIELDS))
                for r in by_stock_period[(stock_code, prev_period)]
            ]
        else:
            # R4/B17b: 按"三键"精确取那一版, 不是该 report_date 下所有版本的并集。
            prev_rows = conn.execute(
                f"SELECT DISTINCT holder_name, holder_code, is_holder_org, "
                f"{', '.join(CARRY_FIELDS)} FROM {CANONICAL_TABLE} "
                "WHERE stock_code=? AND report_date=? AND notice_date=? AND is_exit_row=FALSE",
                [stock_code, db_prev_period, db_prev_notice]).fetchall()
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
        gone = sorted(
            identity for identity, src in prev_by_identity.items()
            if not (
                (src["holder_code"] and src["holder_code"] in cur_codes)
                or (src["holder_name"] and src["holder_name"] in cur_names)
            )
        )
        # 上一期是 v2 遗留行时 is_holder_org 为 NULL(当时这一列不存在, 身份未记录)。
        # 造不出合规的 v3 退出行 —— accept 侧要求它必须是 bool (INVALID_HOLDER_ORG_FLAG)。
        skipped_unknown = [i for i in gone if prev_by_identity[i]["is_holder_org"] is None]
        if skipped_unknown:
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
                "holder_code": src["holder_code"],
                "is_holder_org": src["is_holder_org"],
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

    只作展示 (T3: 到期集合不再由它驱动) —— SSOT = ``canonical_top10_float_holders_period.notice_date``。
    Legacy fact plane retired 2026-07-26 — no fallback.

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
    """展示计数: 本轮跑之前的水位 ``pre_wm`` 之后新增了多少行/分区。

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


# ── 按公告日的日更循环 + 按股回补 (刀 B1; spec §4.2) ───────────────────────


def recheck_notice_day(
    conn, notice_date: str, *, client, run_kind: str, write: bool, now_fn
) -> dict:
    """日更、按日回补、dry-run 共用的唯一步骤 (spec §4.2)。

    1. 取数 (``fetch_holders_notice_day``);
    2. 与本地做差集 (``diff_notice_day``);
    3. ``day.rows`` 为空 → outcome ``empty``; 否则按差集续号落地
       (``_rows_to_land``), 非空且 ``write`` 时 ``merge_new_grains`` 写入,
       outcome ``complete``;
    4. ``write=True`` 才 ``append_ledger`` (``fetched_at`` 必须 tz-aware);
       ``write=False``: 不写库、不写账本、不落 landing (S2, dry-run);
    5. 异常分层: ``AIF10BlockedError`` → 账本 ``failed reason="blocked"``
       (仅 ``write=True``) 后原样上抛; 其它任何异常 (含清洗后键冲突
       ``clean_key_collision`` 与 accept 拒批变成的 ``DisclosureDualWriteError``)
       → 账本 ``failed reason=<类名或原因>``, 返回 ``{"error": ...}``, 调用方
       append 进 errors 并继续下一天 (重试由到期集合保证)。
    """
    from aif10_scraper import AIF10BlockedError
    from services.holders_notice_ledger import LedgerRow, append_ledger, ensure_holders_notice_ledger

    nd = _validate_notice_date(notice_date)
    if write:
        # dry-run (write=False) 可能拿到 read_only=True 的连接 (S2) —— DDL 会炸,
        # 且 dry-run 本来就不许有任何写副作用, 所以只在 write=True 时才建表。
        ensure_holders_notice_ledger(conn)

    def _failed(reason: str, **ledger_extra: Any) -> None:
        if write:
            append_ledger(
                conn,
                LedgerRow(
                    ledger_id=uuid4().hex, notice_date=nd, fetched_at=now_fn(),
                    run_kind=run_kind, scope="day", outcome="failed",
                    reason=str(reason)[:200], **ledger_extra,
                ),
            )

    try:
        day = fetch_holders_notice_day(nd, client=client)
    except AIF10BlockedError:
        _failed("blocked")
        raise
    except Exception as exc:  # noqa: BLE001 — 取数层任何失败都归 failed, 由到期集合下次重试
        _failed(type(exc).__name__)
        return {"error": f"{type(exc).__name__}: {exc}"}

    local = _local_observation_index(conn, nd)
    local_rows_before = sum(len(v) for v in local.by_key.values())

    try:
        keys_elsewhere = _local_keys_elsewhere(conn, nd, local.by_key.keys())
        diff = diff_notice_day(day.rows, local, keys_elsewhere=keys_elsewhere)
    except RuntimeError as exc:
        # V14 (spec §17.3): 清洗后键不唯一 → clean_key_collision, 只让这一天失败,
        # 其它日期继续 (归入 failed reason, 不中断整次日更)。
        _failed(
            "clean_key_collision",
            count_declared=day.ledger.count_declared,
            pages_declared=day.ledger.pages_declared,
            raw_rows=day.ledger.raw_rows,
            unique_rows=day.ledger.unique_rows,
            passes=day.ledger.passes,
            local_rows_before=local_rows_before,
        )
        return {"error": f"clean_key_collision: {exc}"}

    ledger_common = dict(
        count_declared=day.ledger.count_declared,
        pages_declared=day.ledger.pages_declared,
        raw_rows=day.ledger.raw_rows,
        unique_rows=day.ledger.unique_rows,
        passes=day.ledger.passes,
        local_rows_before=local_rows_before,
        missing_rows=len(diff.missing_keys),
        revised_rows=len(diff.revised_keys),
        surplus_rows=len(diff.surplus_keys),
        moved_rows=len(diff.moved_keys),
        dup_rows=diff.dup_rows,
    )

    if not day.rows:
        if write:
            append_ledger(
                conn,
                LedgerRow(
                    ledger_id=uuid4().hex, notice_date=nd, fetched_at=now_fn(),
                    run_kind=run_kind, scope="day", outcome="empty",
                    rows_inserted=0, held_rows=0, exit_rows_replaced=0,
                    **ledger_common,
                ),
            )
        return {
            "outcome": "empty", "notice_date": nd, "rows_inserted": 0,
            "held_rows": 0, "exit_rows_replaced": 0, "diff": diff, **ledger_common,
        }

    rows = _rows_to_land(diff, {_holder_key(r): r for r in day.rows}, local)
    acquire_evidence = {
        "acquire_path": "by_notice_date",
        "run_kind": run_kind,
        "observation_kind": "delta_vs_canonical",
        "sort_columns": day.ledger.sort_columns,
        "provider_count": day.ledger.count_declared,
        "pages": day.ledger.pages_declared,
        "raw_rows": day.ledger.raw_rows,
        "unique_rows": day.ledger.unique_rows,
        "passes": day.ledger.passes,
        "rows_new": len(diff.missing_keys),
        "rows_revised": len(diff.revised_keys),
    }

    rows_inserted = held_rows = exit_rows_replaced = 0
    batch_ids: tuple[str, ...] = ()
    if rows and write:
        try:
            outcome = _write_with_outcome(
                conn, rows, delete_scope="merge_new_grains",
                derive_exits_from_canonical=True, touched_groups=diff.touched_groups,
                acquire_evidence=acquire_evidence,
            )
        except AIF10BlockedError:
            _failed("blocked", **ledger_common)
            raise
        except Exception as exc:  # noqa: BLE001 — 含 accept 拒批变成的 DisclosureDualWriteError
            _failed(type(exc).__name__, **ledger_common)
            return {"error": f"{type(exc).__name__}: {exc}"}
        if outcome is not None:
            rows_inserted = int(outcome.inserted_rows)
            held_rows = int(outcome.held_rows)
            exit_rows_replaced = int(outcome.exit_rows_replaced)
            batch_ids = tuple(outcome.batch_ids)

    if write:
        append_ledger(
            conn,
            LedgerRow(
                ledger_id=uuid4().hex, notice_date=nd, fetched_at=now_fn(),
                run_kind=run_kind, scope="day", outcome="complete",
                rows_inserted=rows_inserted, held_rows=held_rows,
                exit_rows_replaced=exit_rows_replaced,
                batch_ids=",".join(batch_ids) if batch_ids else None,
                **ledger_common,
            ),
        )
    return {
        "outcome": "complete", "notice_date": nd, "rows_inserted": rows_inserted,
        "held_rows": held_rows, "exit_rows_replaced": exit_rows_replaced,
        "batch_ids": batch_ids, "diff": diff, **ledger_common,
    }


def reland_stock_in_day(
    conn, notice_date: str, symbol: str, *, client, write: bool, now_fn
) -> dict:
    """按股回补一天 (B2 ``by_stock`` 用; 定义留在本文件因为它复用日更同一套
    差集/写入机制 —— B2 依赖 B1 的 ``diff_notice_day``/``_rows_to_land``/
    ``_write_with_outcome``/账本, spec §4.0)。

    ``_fetch_raw`` → ``_drop_vendor_excluded`` → ``_clean`` → 只留
    ``notice_date == nd``, 对该股在 ``nd`` 的本地行做同一个 ``diff_notice_day``,
    只落 ``missing ∪ revised``, ``merge_new_grains``; 账本 ``scope='stock',
    stock_code=symbol``。
    """
    from aif10_scraper import AIF10BlockedError
    from services.holders_notice_ledger import LedgerRow, append_ledger, ensure_holders_notice_ledger

    nd = _validate_notice_date(notice_date)
    symbol = str(symbol or "").strip()
    if not symbol:
        raise ValueError("reland_stock_in_day: symbol must be non-empty")
    if write:
        ensure_holders_notice_ledger(conn)

    def _failed(reason: str) -> None:
        if write:
            append_ledger(
                conn,
                LedgerRow(
                    ledger_id=uuid4().hex, notice_date=nd, fetched_at=now_fn(),
                    run_kind="reland", scope="stock", stock_code=symbol,
                    outcome="failed", reason=str(reason)[:200],
                ),
            )

    try:
        raw = _fetch_raw(client, symbol)
    except AIF10BlockedError:
        _failed("blocked")
        raise
    except Exception as exc:  # noqa: BLE001
        _failed(type(exc).__name__)
        return {"error": f"{type(exc).__name__}: {exc}"}

    cleaned = _clean(raw, start_period=DEFAULT_START_PERIOD)
    day_rows = [row for row in cleaned if row.get("notice_date") == nd]

    local = _local_observation_index(conn, nd, stock_code=symbol)
    local_rows_before = sum(len(v) for v in local.by_key.values())

    try:
        keys_elsewhere = _local_keys_elsewhere(conn, nd, local.by_key.keys())
        diff = diff_notice_day(day_rows, local, keys_elsewhere=keys_elsewhere)
    except RuntimeError as exc:
        _failed("clean_key_collision")
        return {"error": f"clean_key_collision: {exc}"}

    ledger_common = dict(
        local_rows_before=local_rows_before,
        missing_rows=len(diff.missing_keys),
        revised_rows=len(diff.revised_keys),
        surplus_rows=len(diff.surplus_keys),
        moved_rows=len(diff.moved_keys),
        dup_rows=diff.dup_rows,
    )

    rows_inserted = held_rows = exit_rows_replaced = 0
    batch_ids: tuple[str, ...] = ()
    outcome_kind = "complete" if day_rows else "empty"

    if day_rows:
        rows = _rows_to_land(diff, {_holder_key(r): r for r in day_rows}, local)
        if rows and write:
            acquire_evidence = {
                "acquire_path": "by_stock",
                "run_kind": "reland",
                "observation_kind": "by_stock_delta",
                "rows_new": len(diff.missing_keys),
                "rows_revised": len(diff.revised_keys),
            }
            try:
                outcome = _write_with_outcome(
                    conn, rows, delete_scope="merge_new_grains",
                    derive_exits_from_canonical=True, touched_groups=diff.touched_groups,
                    acquire_evidence=acquire_evidence,
                )
            except AIF10BlockedError:
                _failed("blocked")
                raise
            except Exception as exc:  # noqa: BLE001
                _failed(type(exc).__name__)
                return {"error": f"{type(exc).__name__}: {exc}"}
            if outcome is not None:
                rows_inserted = int(outcome.inserted_rows)
                held_rows = int(outcome.held_rows)
                exit_rows_replaced = int(outcome.exit_rows_replaced)
                batch_ids = tuple(outcome.batch_ids)

    if write:
        append_ledger(
            conn,
            LedgerRow(
                ledger_id=uuid4().hex, notice_date=nd, fetched_at=now_fn(),
                run_kind="reland", scope="stock", stock_code=symbol,
                outcome=outcome_kind, rows_inserted=rows_inserted, held_rows=held_rows,
                exit_rows_replaced=exit_rows_replaced,
                batch_ids=",".join(batch_ids) if batch_ids else None,
                **ledger_common,
            ),
        )
    return {
        "outcome": outcome_kind, "notice_date": nd, "stock_code": symbol,
        "rows_inserted": rows_inserted, "held_rows": held_rows,
        "exit_rows_replaced": exit_rows_replaced, "diff": diff, **ledger_common,
    }


def sync_holders_aif10_incremental(conn, *, now_fn=None) -> dict:
    """日常增量 (刀 B1 重写): 账本驱动的到期集合, 取代 MAX(notice_date) 水位。

    五步: ``ensure_holders_notice_ledger`` → 探针 (``_provider_newest_update_date``,
    fail-closed) → ``plan_due_notice_days`` (账本 settled 集合的补集, floor 守卫
    抛 ``HoldersLedgerFloorError``) → 逐日 ``recheck_notice_day(...,
    run_kind="daily", write=True)`` (经 ``run_due_notice_days`` 循环体, blocked
    直接冒出停止, 其它错误 append 进 errors 继续) → 汇总。

    结果 dict 键: ``watermark, watermark_source``(``formal_holders_watermark``,
    只作展示), ``net_new_notice_rows, notice_partitions_touched, errors,
    provider_max_update_date, due_days, rechecked, landed_partitions,
    empty_partitions, failed_partitions, settled_after_run, rows_inserted,
    rows_revised_recorded``; ``skipped=True`` 时 ``skip_reason ∈
    {"nothing_due", "provider_probe_failed"}``。
    """
    from aif10_scraper import AIF10BlockedError
    from services.holders_notice_catchup import plan_due_notice_days, run_due_notice_days
    from services.holders_notice_ledger import ensure_holders_notice_ledger, settled_notice_days

    if now_fn is None:
        now_fn = lambda: datetime.now(timezone.utc)  # noqa: E731

    client = _daily_client()
    ensure_holders_notice_ledger(conn)

    pre_wm, wm_source = formal_holders_watermark(conn)

    def _base_result(**overrides: Any) -> dict:
        base = {
            "watermark": pre_wm,
            "watermark_source": wm_source,
            "net_new_notice_rows": 0,
            "notice_partitions_touched": 0,
            "errors": [],
            "provider_max_update_date": None,
            "due_days": 0,
            "rechecked": 0,
            "landed_partitions": 0,
            "empty_partitions": 0,
            "failed_partitions": 0,
            "settled_after_run": 0,
            "rows_inserted": 0,
            "rows_revised_recorded": 0,
        }
        base.update(overrides)
        return base

    try:
        provider_max = _provider_newest_update_date(client)
    except AIF10BlockedError:
        raise
    except HoldersProviderProbeError as exc:
        return _base_result(
            errors=[f"probe:{exc}"], skipped=True, skip_reason="provider_probe_failed",
        )

    due = plan_due_notice_days(
        conn,
        provider_max=provider_max,
        settle_days=_RECHECK.settle_days,
        floor=EXPOSURE_START,
        max_days=MAX_DUE_DAYS_PER_RUN,
    )

    if not due:
        wm, wm_source_now = formal_holders_watermark(conn)
        net_new_rows, notice_parts = _net_new_notice_since(conn, pre_wm)
        return _base_result(
            watermark=wm, watermark_source=wm_source_now,
            net_new_notice_rows=net_new_rows, notice_partitions_touched=notice_parts,
            provider_max_update_date=provider_max,
            skipped=True, skip_reason="nothing_due",
        )

    loop_result = run_due_notice_days(conn, due, client=client, run_kind="daily", now_fn=now_fn)

    wm, wm_source_now = formal_holders_watermark(conn)
    net_new_rows, notice_parts = _net_new_notice_since(conn, pre_wm)
    settled_after = settled_notice_days(conn, settle_days=_RECHECK.settle_days)
    settled_after_run = sum(1 for d in due if d in settled_after)

    return _base_result(
        watermark=wm, watermark_source=wm_source_now,
        net_new_notice_rows=net_new_rows, notice_partitions_touched=notice_parts,
        errors=loop_result["errors"],
        provider_max_update_date=provider_max,
        due_days=len(due),
        rechecked=len(due),
        landed_partitions=len(loop_result["landed_partitions"]),
        empty_partitions=len(loop_result["empty_partitions"]),
        failed_partitions=len(loop_result["failed_partitions"]),
        settled_after_run=settled_after_run,
        rows_inserted=loop_result["rows_inserted"],
        rows_revised_recorded=loop_result["rows_revised_recorded"],
    )
