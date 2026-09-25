"""East Money v1 pagination integrity.

Two layers live in this file:

- The pre-existing 100-page shard planner (``plan_security_code_shards`` /
  ``_probe_v1``) for reports whose page-1 count exceeds a single query's page
  cap (``RPT_MAIN_ORGHOLDDETAIL`` and peers) — unchanged by this cut (org's
  own registration is a separate knife, see ``aif10_pagination.yaml``).
- The strict pagination engine (``fetch_pages_strict`` / ``PaginationPolicy`` /
  ``PageLedger`` / ``PaginationIntegrityError``) added 2026-09-25
  (spec_holders_pagination.md 刀 A): a translation-invariant sort key alone is
  not enough — a *tied* sort key (rows sharing every declared sort column)
  still lets the provider's internal order drift page-to-page, silently
  duplicating one row and dropping another while ``count``/``pages`` stay
  stable throughout (probe_holders_pagination.md §3.2/§3.3: real 08-28/04-30
  default-sort lands dropped 36.4%/47.1% of rows while ``landed_rows==count``
  the whole time — the old ``assess_pagination_land`` heuristic below cannot
  see this because the *count* it compares was never wrong, only the
  *content* was). The engine below requires an identity key that is provably
  unique (checked at construction, not assumed) and treats any deviation from
  "every page exactly ``page_size`` rows, last page exact remainder, zero
  content duplicates" as an error, not a warning.
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Literal, Sequence

from .client import KNOWN_RESPONSE_CODES, AIF10UnknownCodeError

logger = logging.getLogger("aif10_scraper")

# Live probe 2026-07-24: page 101+ → pages=0 for same filter.
DEFAULT_MAX_PAGES_PER_QUERY = 100

# 东财 v1 顶层 code 里的"空结果" (client.KNOWN_RESPONSE_CODES 的一个成员; 这里只是
# 给引擎自己的判据起个语义名字, 不再另抄一份数字 —— 数字的唯一来源是 client.py)。
EASTMONEY_EMPTY_RESULT_CODE = 9201
assert EASTMONEY_EMPTY_RESULT_CODE in KNOWN_RESPONSE_CODES


@dataclass(frozen=True)
class PaginationProbe:
    provider_count: int
    provider_pages: int
    page_size: int
    max_pages_per_query: int

    @property
    def max_rows_per_query(self) -> int:
        return self.max_pages_per_query * self.page_size

    @property
    def exceeds_page_cap(self) -> bool:
        if self.provider_pages > self.max_pages_per_query:
            return True
        if self.provider_count > self.max_rows_per_query:
            return True
        return False


@dataclass(frozen=True)
class PaginationLandResult:
    expected_count: int
    landed_rows: int
    truncated: bool
    reasons: tuple[str, ...]


def security_code_range_filter(lo: int, hi: int) -> str:
    """Inclusive 6-digit SECURITY_CODE numeric range (no quotes — API accepts)."""
    return f"(SECURITY_CODE>={lo:06d})(SECURITY_CODE<={hi:06d})"


def _probe_v1(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    max_pages_per_query: int,
) -> PaginationProbe:
    head = client.get_v1(
        report_name,
        page=1,
        page_size=page_size,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_filters=extra_filters,
        extra_params=extra_params,
    )
    return PaginationProbe(
        provider_count=int(head.get("count") or 0),
        provider_pages=int(head.get("pages") or 0),
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
    )


def plan_security_code_shards(
    client: Any,
    report_name: str,
    *,
    base_filters: Sequence[str],
    page_size: int,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    secucode: str | None = None,
    extra_params: dict[str, Any] | None = None,
    lo: int = 0,
    hi: int = 999_999,
) -> list[list[str]]:
    """Return extra_filters lists (base + range) each fitting max_pages."""
    filters = list(base_filters)
    probe = _probe_v1(
        client,
        report_name,
        page_size=page_size,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_filters=filters if filters else None,
        extra_params=extra_params,
        max_pages_per_query=max_pages_per_query,
    )
    if probe.provider_count <= 0:
        return [list(filters)]
    if not probe.exceeds_page_cap:
        return [list(filters)]

    if lo >= hi:
        raise RuntimeError(
            f"cannot shard SECURITY_CODE range {lo}-{hi}: "
            f"count={probe.provider_count} pages={probe.provider_pages}"
        )
    mid = (lo + hi) // 2
    left = plan_security_code_shards(
        client,
        report_name,
        base_filters=[*filters, security_code_range_filter(lo, mid)],
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_params=extra_params,
        lo=lo,
        hi=mid,
    )
    right = plan_security_code_shards(
        client,
        report_name,
        base_filters=[*filters, security_code_range_filter(mid + 1, hi)],
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_params=extra_params,
        lo=mid + 1,
        hi=hi,
    )
    return left + right


# ---------------------------------------------------------------------------
# 严格翻页引擎 (2026-09-25, spec_holders_pagination.md 刀 A §3.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaginationPolicy:
    """一张报表的翻页完整性策略 (typed YAML 的运行时形态, 见
    ``services/data_sources/aif10_pagination_rules.py``)。

    构造时校验 (违反即 ``ValueError``, fail-closed):
    - ``identity_columns`` 必须是 ``sort_columns`` 的子集 —— 身份键不在排序键里,
      并列组就可能跨页漂移 (probe_holders_pagination.md §3.2 的机制正是如此)。
    - ``duplicates == "allow"`` 时 ``identity_columns`` 必须为空 (身份判定与
      "允许整行重复" 是互斥的两件事, 都开等于自相矛盾)。
    - ``row_tolerance_rows >= 0``、``drift_refetch >= 0``。
    """

    sort_columns: str
    sort_types: str
    identity_columns: tuple[str, ...]
    row_tolerance_rows: int
    duplicates: Literal["error", "allow"]
    drift_refetch: int

    def __post_init__(self) -> None:
        sort_cols = {c.strip() for c in self.sort_columns.split(",") if c.strip()}
        missing = [c for c in self.identity_columns if c not in sort_cols]
        if missing:
            raise ValueError(
                f"PaginationPolicy: identity_columns {missing} 不是 "
                f"sort_columns={self.sort_columns!r} 的子集 —— 身份键必须在排序键里, "
                "否则并列组可能跨页漂移。"
            )
        if self.duplicates == "allow" and self.identity_columns:
            raise ValueError(
                "PaginationPolicy: duplicates='allow' 时 identity_columns 必须为空。"
            )
        if self.row_tolerance_rows < 0:
            raise ValueError("PaginationPolicy: row_tolerance_rows 必须 >= 0")
        if self.drift_refetch < 0:
            raise ValueError("PaginationPolicy: drift_refetch 必须 >= 0")


def STRICT_POLICY_FOR(sort_columns: str, sort_types: str) -> PaginationPolicy:
    """无身份键、容差 0、重复即错、不重取 —— 未登记 YAML 报表的缺省策略。"""
    return PaginationPolicy(
        sort_columns=sort_columns,
        sort_types=sort_types,
        identity_columns=(),
        row_tolerance_rows=0,
        duplicates="error",
        drift_refetch=0,
    )


@dataclass(frozen=True)
class PageLedger:
    """一次 ``fetch_pages_strict`` 调用 (可能含多遍漂移重取) 的完整证据。"""

    sort_columns: str
    sort_types: str
    page_size: int
    count_declared: int
    pages_declared: int
    page_sizes: tuple[int, ...]
    counts_seen: tuple[int, ...]
    pages_seen: tuple[int, ...]
    codes_seen: tuple[int, ...]
    raw_rows: int
    identical_duplicates: int
    identity_conflicts: int
    unique_rows: int
    partial_by_caller: bool
    passes: int
    reasons: tuple[str, ...]


class PaginationIntegrityError(RuntimeError):
    """严格翻页引擎的判据失败。``reason`` 闭合取值 (11 个)。

    携带 ``ledger`` (到失败为止收集到的证据, 供调用方入账) 与 ``rows`` (到失败
    为止已经取到的行 —— 保住 ``fetch_pages_for_filters`` 薄壳调用方"拿到
    truncated 信号自己处置"的既有契约, 不因改抛错就丢了已取到的数据引用)。
    """

    REASONS = frozenset(
        {
            "empty_code_mid_fetch",
            "pages_count_inconsistent",
            "count_drift",
            "pages_drift",
            "short_page",
            "last_page_size_mismatch",
            "raw_rows_ne_count",
            "identical_duplicates",
            "identity_conflict",
            "identity_column_missing",
            "page_cap_exceeded",
        }
    )

    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        ledger: PageLedger | None = None,
        rows: list[dict] | None = None,
    ):
        if reason not in self.REASONS:
            raise ValueError(
                f"PaginationIntegrityError: 未登记的 reason {reason!r}, "
                f"闭合集合是 {sorted(self.REASONS)}"
            )
        self.reason = reason
        self.ledger = ledger
        self.rows = list(rows) if rows is not None else []
        super().__init__(message or reason)


def _stable_json(row: Any) -> str:
    return json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)


# 触发"整日重取一次"的三个 reason (spec §3.2 修订 1 N6): 供应商这一天还在进行,
# 不是翻页坏了。第 4/6/7 步的 reason (页长/总数/重复/身份) 不在这个集合里 ——
# 它们是翻页/数据缺陷, 重取只会把判据变成"多试几次总有一次过"。
_DRIFT_RETRY_REASONS = frozenset({"empty_code_mid_fetch", "count_drift", "pages_drift"})


def fetch_pages_strict(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    policy: PaginationPolicy,
    columns: str = "ALL",
    secucode: str | None = None,
    extra_filters: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    max_pages: int = 0,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> tuple[list[dict], PageLedger]:
    """严格分页取全量, 按 ``policy`` 判定完整性 (§3.2)。

    一遍 (pass) 内: 页长必须精确、总数必须精确 (容差 0 时)、整行重复与身份冲突
    按策略处置。``empty_code_mid_fetch``/``count_drift``/``pages_drift`` 这三种
    "供应商这一天还在进行" 的漂移形态, 会在 ``policy.drift_refetch`` 允许的次数
    内整日重取 (从第 1 页重来, 丢弃本遍已取到的行); 其它 reason 从不重取。
    """
    accumulated_reasons: list[str] = []
    passes = 0
    # 跨遍已知非空证据 (blocking finding, 2026-09-25 返修): 若某一遍已经证实
    # 这一天 count_1>0 (哪怕这一遍随后因漂移被丢弃重取), 后面重取的那一遍如果
    # 第 1 页反而给 9201, 就不再是"合法空" —— 是漂移的另一种形态, 见
    # _fetch_one_pass 里 known_nonempty_count 的用法。
    known_nonempty_count = 0
    while True:
        passes += 1
        try:
            return _fetch_one_pass(
                client,
                report_name,
                page_size=page_size,
                policy=policy,
                columns=columns,
                secucode=secucode,
                extra_filters=extra_filters,
                extra_params=extra_params,
                max_pages=max_pages,
                max_pages_per_query=max_pages_per_query,
                progress_callback=progress_callback,
                passes=passes,
                reasons_so_far=accumulated_reasons,
                known_nonempty_count=known_nonempty_count,
            )
        except PaginationIntegrityError as exc:
            if exc.ledger is not None and exc.ledger.count_declared > 0:
                known_nonempty_count = exc.ledger.count_declared
            if exc.reason in _DRIFT_RETRY_REASONS and passes < 1 + policy.drift_refetch:
                accumulated_reasons.append(f"refetch_after_{exc.reason}")
                continue
            raise


def _fetch_one_pass(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    policy: PaginationPolicy,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    max_pages: int,
    max_pages_per_query: int,
    progress_callback: Callable[[int, int, int], None] | None,
    passes: int,
    reasons_so_far: list[str],
    known_nonempty_count: int = 0,
) -> tuple[list[dict], PageLedger]:
    reasons = list(reasons_so_far)
    page_rows: dict[int, list[dict]] = {}

    def _call(page_num: int) -> dict[str, Any]:
        return client.get_v1(
            report_name,
            page=page_num,
            page_size=page_size,
            sort_columns=policy.sort_columns,
            sort_types=policy.sort_types,
            columns=columns,
            secucode=secucode,
            extra_filters=extra_filters,
            extra_params=extra_params,
        )

    def _code_of(resp: Any) -> int:
        code = resp.get("code") if isinstance(resp, dict) else None
        # 引擎对每页 code 断言 ∈ KNOWN_RESPONSE_CODES, 对假客户端也 fail-closed
        # (真客户端在 get_v1 出口就已经把 9501/未知码变成异常, 这里是第二道防线)。
        if code not in KNOWN_RESPONSE_CODES:
            raise AIF10UnknownCodeError(code, str((resp or {}).get("message") or ""))
        return code

    def _partial_rows() -> list[dict]:
        return [row for p in sorted(page_rows) for row in page_rows[p]]

    def _ledger(**overrides: Any) -> PageLedger:
        page_sizes = tuple(len(page_rows[p]) for p in sorted(page_rows))
        base: dict[str, Any] = dict(
            sort_columns=policy.sort_columns,
            sort_types=policy.sort_types,
            page_size=page_size,
            count_declared=0,
            pages_declared=0,
            page_sizes=page_sizes,
            counts_seen=(),
            pages_seen=(),
            codes_seen=(),
            raw_rows=len(_partial_rows()),
            identical_duplicates=0,
            identity_conflicts=0,
            unique_rows=0,
            partial_by_caller=False,
            passes=passes,
            reasons=tuple(reasons),
        )
        base.update(overrides)
        return PageLedger(**base)

    def _raise(reason: str, message: str, **ledger_overrides: Any) -> None:
        raise PaginationIntegrityError(
            reason, message, ledger=_ledger(**ledger_overrides), rows=_partial_rows()
        )

    def _check_page_length(page_num: int, *, pages_1: int, count_1: int, partial_by_caller: bool) -> None:
        """步骤 4 (页长), 在每页刚取到时**立即**检查 —— 不是等全部页都取完再回头查。

        这样一页坏了 (非末页长度不对 / 超出 ``max_pages_per_query`` 之类)
        不会诱使引擎继续把后面的页也取一遍才报错 (E9/E13/E22: 只违反一个
        原语的隔离用例断言假客户端只被调用到刚好够判定为止的次数)。第 1 页
        在 ``pages_1 > 1`` 时也按非末页检, 与末页判据用同一份逻辑。
        """
        rows_p = page_rows[page_num]
        is_true_last_page = (page_num == pages_1) and not partial_by_caller
        if not is_true_last_page:
            if len(rows_p) != page_size:
                if policy.row_tolerance_rows > 0:
                    reasons.append("short_page")
                else:
                    _raise(
                        "short_page",
                        f"{report_name}: 第 {page_num} 页 {len(rows_p)} 行 != page_size={page_size}",
                        count_declared=count_1,
                        pages_declared=pages_1,
                        counts_seen=tuple(counts_seen),
                        pages_seen=tuple(pages_seen),
                        codes_seen=tuple(codes_seen),
                    )
        else:
            expected_last_len = count_1 - (pages_1 - 1) * page_size
            if len(rows_p) != expected_last_len:
                if policy.row_tolerance_rows > 0:
                    reasons.append("last_page_size_mismatch")
                else:
                    _raise(
                        "last_page_size_mismatch",
                        f"{report_name}: 末页 (第 {page_num} 页) {len(rows_p)} 行 != "
                        f"预期 count-{(pages_1-1)}*page_size={expected_last_len}",
                        count_declared=count_1,
                        pages_declared=pages_1,
                        counts_seen=tuple(counts_seen),
                        pages_seen=tuple(pages_seen),
                        codes_seen=tuple(codes_seen),
                    )

    # ---- step 1: 第 1 页 ----
    head = _call(1)
    code_1 = _code_of(head)
    if code_1 == EASTMONEY_EMPTY_RESULT_CODE:
        if known_nonempty_count > 0:
            # 前一遍已经证实这一天 count>0 (§1.1「第 1 页 9201=空」的判据只在
            # 单遍、毫无先验证据时成立), 这一遍第 1 页却给 9201 —— 不是真空,
            # 是取数期间供应商又抽风了一次; 按 empty_code_mid_fetch 处置
            # (与"中途 9201"同一族判据, 不新增 reason)。
            _raise(
                "empty_code_mid_fetch",
                f"{report_name}: 第 {passes} 遍第 1 页返回空结果码, 但前一遍已证实 "
                f"该日非空 (count={known_nonempty_count}) —— 不是合法空, 是漂移",
                codes_seen=(code_1,),
            )
        return [], _ledger(codes_seen=(code_1,))

    count_1 = int(head.get("count") or 0)
    pages_1 = int(head.get("pages") or 0)
    page_rows[1] = list(head.get("data") or [])
    counts_seen = [count_1]
    pages_seen = [pages_1]
    codes_seen = [code_1]

    if progress_callback:
        try:
            progress_callback(1, pages_1, len(page_rows[1]))
        except Exception:  # noqa: BLE001 — 进度回调是显示层, 挂了不该中断取数
            logger.warning("progress_callback 抛异常, 已忽略(不影响已取到的行)", exc_info=True)

    # ---- step 2: count/pages 一致性 + 页数上限 ----
    expected_pages = math.ceil(count_1 / page_size) if page_size > 0 else 0
    if pages_1 != expected_pages:
        _raise(
            "pages_count_inconsistent",
            f"{report_name}: pages={pages_1} != ceil(count={count_1}/page_size={page_size})={expected_pages}",
            count_declared=count_1,
            pages_declared=pages_1,
            counts_seen=tuple(counts_seen),
            pages_seen=tuple(pages_seen),
            codes_seen=tuple(codes_seen),
        )
    if pages_1 > max_pages_per_query and not max_pages:
        _raise(
            "page_cap_exceeded",
            f"{report_name}: pages={pages_1} > max_pages_per_query={max_pages_per_query} "
            "(且调用方未指定 max_pages 分片) —— 该分片",
            count_declared=count_1,
            pages_declared=pages_1,
            counts_seen=tuple(counts_seen),
            pages_seen=tuple(pages_seen),
            codes_seen=tuple(codes_seen),
        )

    stop_page = pages_1
    partial_by_caller = False
    if max_pages and max_pages < pages_1:
        stop_page = max_pages
        partial_by_caller = True

    # ---- step 4 (第 1 页): 页长立即检查 (在取第 2 页之前) ----
    _check_page_length(1, pages_1=pages_1, count_1=count_1, partial_by_caller=partial_by_caller)

    # ---- step 3+4: 第 2..stop_page 页 (每页取到后立即做 3 的 code/漂移判据
    #      与 4 的页长判据, 不攒到最后再回头查 —— 见 _check_page_length 注释) ----
    for page_num in range(2, stop_page + 1):
        resp = _call(page_num)
        code_p = _code_of(resp)
        codes_seen.append(code_p)
        if code_p == EASTMONEY_EMPTY_RESULT_CODE:
            _raise(
                "empty_code_mid_fetch",
                f"{report_name}: 第 {page_num} 页 (共 {pages_1} 页) 中途返回空结果码",
                count_declared=count_1,
                pages_declared=pages_1,
                counts_seen=tuple(counts_seen),
                pages_seen=tuple(pages_seen),
                codes_seen=tuple(codes_seen),
            )
        count_p = int(resp.get("count") or 0)
        pages_p = int(resp.get("pages") or 0)
        counts_seen.append(count_p)
        pages_seen.append(pages_p)
        page_rows[page_num] = list(resp.get("data") or [])

        if count_p != count_1:
            if policy.row_tolerance_rows > 0:
                reasons.append("count_drift")
            else:
                _raise(
                    "count_drift",
                    f"{report_name}: 第 {page_num} 页 count={count_p} != 第 1 页 count={count_1}",
                    count_declared=count_1,
                    pages_declared=pages_1,
                    counts_seen=tuple(counts_seen),
                    pages_seen=tuple(pages_seen),
                    codes_seen=tuple(codes_seen),
                )
        if pages_p != pages_1:
            if policy.row_tolerance_rows > 0:
                reasons.append("pages_drift")
            else:
                _raise(
                    "pages_drift",
                    f"{report_name}: 第 {page_num} 页 pages={pages_p} != 第 1 页 pages={pages_1}",
                    count_declared=count_1,
                    pages_declared=pages_1,
                    counts_seen=tuple(counts_seen),
                    pages_seen=tuple(pages_seen),
                    codes_seen=tuple(codes_seen),
                )

        _check_page_length(page_num, pages_1=pages_1, count_1=count_1, partial_by_caller=partial_by_caller)

        if progress_callback:
            try:
                progress_callback(page_num, pages_1, len(_partial_rows()))
            except Exception:  # noqa: BLE001 — 进度回调是显示层, 挂了不该中断取数
                logger.warning("progress_callback 抛异常, 已忽略(不影响已取到的行)", exc_info=True)

    all_rows = _partial_rows()

    # ---- step 5: 调用方声明的部分取数, 跳过 6~7 ----
    if partial_by_caller:
        return all_rows, _ledger(
            count_declared=count_1,
            pages_declared=pages_1,
            counts_seen=tuple(counts_seen),
            pages_seen=tuple(pages_seen),
            codes_seen=tuple(codes_seen),
            raw_rows=len(all_rows),
            partial_by_caller=True,
        )

    # ---- step 6: 总行数 vs 声明 count (容差) ----
    raw_rows = len(all_rows)
    if abs(raw_rows - count_1) > policy.row_tolerance_rows:
        _raise(
            "raw_rows_ne_count",
            f"{report_name}: raw_rows={raw_rows} 与 count={count_1} 的差超出容差 "
            f"{policy.row_tolerance_rows}",
            count_declared=count_1,
            pages_declared=pages_1,
            counts_seen=tuple(counts_seen),
            pages_seen=tuple(pages_seen),
            codes_seen=tuple(codes_seen),
            raw_rows=raw_rows,
        )

    # ---- step 7a: 整行重复 ----
    sig_counts = Counter(_stable_json(r) for r in all_rows)
    identical_duplicates = sum(c - 1 for c in sig_counts.values() if c > 1)
    if identical_duplicates > 0 and policy.duplicates == "error":
        _raise(
            "identical_duplicates",
            f"{report_name}: {identical_duplicates} 行整行重复 (duplicates=error)",
            count_declared=count_1,
            pages_declared=pages_1,
            counts_seen=tuple(counts_seen),
            pages_seen=tuple(pages_seen),
            codes_seen=tuple(codes_seen),
            raw_rows=raw_rows,
            identical_duplicates=identical_duplicates,
            unique_rows=raw_rows - identical_duplicates,
        )

    # ---- step 7b: 身份键 (键存在且值非空; 组内内容一致) ----
    identity_conflicts = 0
    if policy.identity_columns:
        groups: dict[tuple[Any, ...], list[dict]] = {}
        for row in all_rows:
            key_parts: list[Any] = []
            for col in policy.identity_columns:
                value = row.get(col) if isinstance(row, dict) else None
                missing = (
                    not isinstance(row, dict)
                    or col not in row
                    or value is None
                    or (isinstance(value, str) and value.strip() == "")
                )
                if missing:
                    _raise(
                        "identity_column_missing",
                        f"{report_name}: 身份列 {col!r} 缺失或为空",
                        count_declared=count_1,
                        pages_declared=pages_1,
                        counts_seen=tuple(counts_seen),
                        pages_seen=tuple(pages_seen),
                        codes_seen=tuple(codes_seen),
                        raw_rows=raw_rows,
                        identical_duplicates=identical_duplicates,
                        unique_rows=raw_rows - identical_duplicates,
                    )
                key_parts.append(value)
            groups.setdefault(tuple(key_parts), []).append(row)
        for members in groups.values():
            sigs = {_stable_json(m) for m in members}
            if len(sigs) > 1:
                identity_conflicts += 1
        if identity_conflicts > 0:
            _raise(
                "identity_conflict",
                f"{report_name}: {identity_conflicts} 个身份组内容不一致",
                count_declared=count_1,
                pages_declared=pages_1,
                counts_seen=tuple(counts_seen),
                pages_seen=tuple(pages_seen),
                codes_seen=tuple(codes_seen),
                raw_rows=raw_rows,
                identical_duplicates=identical_duplicates,
                identity_conflicts=identity_conflicts,
                unique_rows=raw_rows - identical_duplicates,
            )

    unique_rows = raw_rows - identical_duplicates
    return all_rows, _ledger(
        count_declared=count_1,
        pages_declared=pages_1,
        counts_seen=tuple(counts_seen),
        pages_seen=tuple(pages_seen),
        codes_seen=tuple(codes_seen),
        raw_rows=raw_rows,
        identical_duplicates=identical_duplicates,
        identity_conflicts=identity_conflicts,
        unique_rows=unique_rows,
    )


def fetch_pages_for_filters(
    client: Any,
    report_name: str,
    *,
    page_size: int,
    max_pages: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    progress_callback: Callable[[int, int, int], None] | None,
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
) -> tuple[list[dict], PaginationLandResult]:
    """薄壳: 调 ``fetch_pages_strict``, 捕 ``PaginationIntegrityError`` 后返回
    ``(已取到的行, PaginationLandResult(truncated=True, reasons=(exc.reason,)))``。

    保留这两个内部调用方 (``backend/scripts/ingest_holders_raw.py``、
    ``fetch_all_pages_sharded``) "拿到 truncated 信号自己处置" 的既有契约 ——
    它们不在本刀允许改的文件清单里, 签名与返回契约不变。策略固定用
    ``STRICT_POLICY_FOR`` (registry 排序键、容差 0), 不读 ``aif10_pagination.yaml``
    (未登记报表调 ``policy_for`` 会 fail-closed, 而这条路径服务 org / qfii /
    Phase A 取数器等未登记调用方, 不能让它们因为没登记就取不到数)。
    """
    policy = STRICT_POLICY_FOR(sort_columns, sort_types)
    try:
        rows, ledger = fetch_pages_strict(
            client,
            report_name,
            page_size=page_size,
            policy=policy,
            columns=columns,
            secucode=secucode,
            extra_filters=extra_filters,
            extra_params=extra_params,
            max_pages=max_pages,
            max_pages_per_query=max_pages_per_query,
            progress_callback=progress_callback,
        )
    except PaginationIntegrityError as exc:
        expected = exc.ledger.count_declared if exc.ledger is not None else 0
        return list(exc.rows), PaginationLandResult(
            expected_count=expected,
            landed_rows=len(exc.rows),
            truncated=True,
            reasons=(exc.reason,),
        )
    return rows, PaginationLandResult(
        expected_count=ledger.count_declared,
        landed_rows=len(rows),
        truncated=False,
        reasons=(),
    )
