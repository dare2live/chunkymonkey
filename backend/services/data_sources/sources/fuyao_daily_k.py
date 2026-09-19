"""fuyao dump (OHLCV) + baostock (pre_close reference) adapter for the daily
nominal OHLCV domain.

See ``sandbox/patch_loop_review_20260916/fable_spec_daily_cutover.md`` §1-§3
and its "刀2" section for the design this module implements. Summary:

- The seven OHLCV columns always come from the fuyao Parquet dump (``daily-k``
  full history / ``daily-k-10d`` rolling window) — already reused via
  ``sources/fuyao.py::dump_downloader``/``dump_kinds`` (no separate HTTP
  client here).
- ``pre_close`` (and the ``change``/``pct_chg`` derived from it) has no dump
  column at all (F4). For SH/SZ pool codes it comes from baostock's
  configured query endpoint (``rules.reference_api``, currently
  ``query_history_k_data_plus`` — reused via ``sources/baostock.py::
  BaostockSource`` — this module never imports the ``baostock`` package or
  logs in itself). For 北交所 (BJ) codes baostock structurally has no
  coverage — those rows get ``pre_close_origin=unknown_no_reference_bj``
  without ever touching the network.
- Every legal value ``pre_close_origin`` can take, the SH/SZ unknown-row
  tolerance, the dump incremental floor, the dump cache directory, and all of
  the acquisition parameters this module reads (baostock endpoint name,
  column mapping, unit divisors, timezone, baostock field list,
  exchange→baostock-prefix map, prefetch window, rounding precision) live in
  ``backend/config/nominal_ohlcv_acquire.yaml`` and are loaded/validated by
  ``nominal_ohlcv_acquire_rules.py`` — nothing in this module hardcodes a
  literal copy of any of them.

Dispatch entry point: ``FuyaoSource.fetch_raw("daily_k_dump", trade_date=t)``
(see ``sources/fuyao.py``) calls :func:`fetch_daily_k_dump_rows` in this
module, passing the ``FuyaoSource`` instance itself so this module can attach
its own cross-call state (dump-kind cache, baostock reference cache, the
baostock circuit-breaker flag) to that one long-lived instance without
``FuyaoSource.__init__`` needing to know anything about this domain.
:func:`fetch_daily_k_dump_rows` returns a :class:`~services.data_sources.
security_day_capture.ProviderPage` (rows + the dump's release identity/sha256/
reference-source as ``request_meta``) — 返修 (blocking 发现 #1 修复, 2026-09-16):
this page-level provenance now actually reaches ``ingest_batch.request_json``
in production. Making this module return ``ProviderPage`` alone was not
sufficient — ``security_day_acquire.py`` and two ``sync_runner.py`` closures
in between used to flatten whatever ``fetch_rows`` returned into a plain
``list``/``tuple`` before it ever reached ``capture_security_day_provider_rows``
(the one place that already knew how to unwrap a ``ProviderPage``), so those
were changed too — see the 返修 comments at
``security_day_acquire.acquire_security_day_provider`` and at the two
``_acquired_rows`` closures in ``sync_runner.py`` (``_publish_security_day_
accepted_partition`` / ``_land_security_day_partition``).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.data_sources.baostock_daily_k_reservoir import ReservoirRow
from services.data_sources.nominal_ohlcv_acquire_rules import (
    NominalOhlcvAcquireRules,
    load_nominal_ohlcv_acquire_rules,
)
from services.data_sources.security_day_capture import ProviderPage
from services.data_sources.sources.fuyao import TUSHARE_MARKET_BJ_LABEL, dashed_date
from services.universe import classify_exclusion, is_active_a_share

# backend/services/data_sources/sources/fuyao_daily_k.py — four parents up is
# the repo root. The cache dir itself (``rules.dump_cache_dir``, repo-relative)
# is a config value, not a literal here — 业主 09-16 明令路径/文件名模板是参数;
# see ``nominal_ohlcv_acquire.yaml``'s ``dump.cache_dir`` for why it lives
# there and not duplicated as a module constant.
_REPO_ROOT = Path(__file__).resolve().parents[4]

_UNKNOWN_NO_REFERENCE_BJ = "unknown_no_reference_bj"
_UNKNOWN_REFERENCE_UNAVAILABLE = "unknown_reference_unavailable"
_UNKNOWN_REFERENCE_MISMATCH = "unknown_reference_mismatch"
_PROVIDER_BAOSTOCK = "provider_baostock"

# 返修 (blocking 发现 #2 修复): 负缓存哨兵 —— 区分"这个 (码,日) 还没查过"(键不存在)
# 与"已经查过 baostock 两次 (预取窗口 + 单日退化), 确认没有这一行"(值是这个哨兵)。
# 没有这条区分时, 同一个 miss 在一次 fetch_rows 内部不会重复查 (每个 (码,日) 只在
# rows 里出现一次), 但跨越 _fetch_with_retry 对同一 trade_date 的重试次数
# (max_attempts=3) 或跨越同一 chunkyctl sync 运行内对同一天的重跑, adapter 状态
# (含这份缓存) 都留在同一个 FuyaoSource 实例上, 会把已经问过、已确认没有的码重新
# 问一遍服务端。
_BAOSTOCK_REFERENCE_MISS = object()


class FuyaoDailyKError(RuntimeError):
    """Adapter-level failure: unusable dump/baostock response, floor breach,
    unknown-row threshold breach, or unexpected out-of-population ts_code."""


class BaostockCircuitOpenError(FuyaoDailyKError):
    """A baostock session-level failure already happened on this adapter
    instance this run; per 刀2 spec, it is not retried — every subsequent
    attempt to touch baostock raises immediately without touching the
    network again."""


def _round_half_up(value: Decimal, *, digits: int) -> Decimal:
    """Quantize an already-``Decimal`` value with ROUND_HALF_UP — 业主 09-16
    明令金融数值禁用内置 round() (banker's rounding).

    Takes and returns ``Decimal``, not ``float``: the caller must build every
    operand as ``Decimal(str(x))`` and do the subtraction/division in Decimal
    *before* this is called, never in float first. Quantizing a float
    expression's result (via ``Decimal(str(already_computed_float))``) bakes
    float binary error into the value before rounding ever sees it — e.g.
    ``(6.41 - 6.40) / 6.40 * 100`` in float is ``0.15624999999999667``, one
    ULP short of the exact ``0.15625``, so ROUND_HALF_UP on that string reads
    0.1562 instead of the correct 0.1563. Only ``float(quantized_result)`` at
    the very end, for the row dict, is safe."""

    quant = Decimal(1).scaleb(-digits)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _shift_yyyymmdd(yyyymmdd: str, days: int) -> str:
    d = date(int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    return (d + timedelta(days=days)).strftime("%Y%m%d")


def _date_ms_to_yyyymmdd(date_ms: Any, *, tz_name: str) -> str:
    from datetime import datetime

    dt = datetime.fromtimestamp(int(date_ms) / 1000, tz=ZoneInfo(tz_name))
    return dt.strftime("%Y%m%d")


def _shanghai_midnight_ms(yyyymmdd: str, *, tz_name: str) -> int:
    """Inverse of :func:`_date_ms_to_yyyymmdd`: the vendor's ``date_ms`` value
    for a given trade date is that date's midnight in ``tz_name`` (see
    ``sources/fuyao.py::shanghai_midnight_ms`` — same semantics, reimplemented
    against the *configured* timezone rather than a fixed ``+08:00`` literal,
    since this module must not hardcode the timezone parameter)."""
    from datetime import datetime

    d = date(int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    midnight = datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz_name))
    return int(midnight.timestamp() * 1000)


def _sha256_of_file(path: Path, *, chunk_bytes: int = 1 << 20) -> str:
    """Stream-hash a downloaded dump file's real bytes (红线14: 缺 lineage =
    UNTRUSTED — a kind label or release tag is not a content hash). Reads in
    fixed-size chunks rather than ``path.read_bytes()`` because the full
    ``daily-k`` dump is multi-year history of unknown size (F11: HEAD probes
    return 403) — never materialize the whole file in memory just to hash it.
    """

    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_parquet_rows_for_date(
    path: Path, *, date_ms: int, adjusted_filter_value: str
) -> list[dict[str, Any]]:
    """Read exactly the rows for one ``date_ms`` value out of a dump parquet.

    Filtering happens inside the SQL (predicate pushdown) rather than reading
    the whole file into Python — the full ``daily-k`` dump is multi-year
    history (F11: size unknown, HEAD probes return 403), so a per-date
    adapter call must never materialize more than one day's rows.
    """

    import duckdb

    # Ephemeral in-memory DuckDB used purely as a local Parquet reader for one
    # dump file — no project-tracked .duckdb file, no manifest routing, no
    # lock (mirrors fuyao_kline_recon.py's own pattern of taking a bare `con`
    # and running read_parquet() against it).
    conn = duckdb.connect(":memory:")  # rule-compliance: ok evidence=ephemeral parquet-only scratch conn, not a project db
    try:
        cur = conn.execute(
            "SELECT * FROM read_parquet(?) WHERE date_ms = ? AND adjusted = ?",
            [str(path), int(date_ms), adjusted_filter_value],
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        conn.close()


def _map_dump_row(raw_row: Mapping[str, Any], rules: NominalOhlcvAcquireRules) -> dict[str, Any]:
    """Vendor column names -> canonical column names, plus unit conversion.

    Builds a **fresh** dict containing only ``rules.dump_column_map``'s
    targets (ts_code/trade_date/open/high/low/close/vol/amount) — any other
    vendor-native column (``currency``/``interval``/``adjusted``) is dropped
    by construction, not filtered after the fact.
    """

    mapped: dict[str, Any] = {}
    for src_col, dst_col in rules.dump_column_map.items():
        mapped[dst_col] = raw_row.get(src_col)
    mapped["ts_code"] = str(mapped["ts_code"] or "").strip().upper()
    mapped["trade_date"] = _date_ms_to_yyyymmdd(mapped["trade_date"], tz_name=rules.dump_timezone)
    for name in ("open", "high", "low", "close"):
        mapped[name] = float(mapped[name])
    mapped["vol"] = float(mapped["vol"]) / rules.dump_vol_divisor
    mapped["amount"] = float(mapped["amount"]) / rules.dump_amount_divisor
    return mapped


def _is_bj_ts_code(ts_code: str) -> bool:
    """True for 北交所 codes, using the repo's registered universe/board
    definition (``services.universe``) rather than a hand-rolled prefix
    check — 修订4 明令"以仓库的universe定义为准，不自己写死"。Raises for any
    ts_code that is neither in the SH/SZ pool nor classified as 北交所: the
    fuyao dump's population is scoped to A-share SH/SZ/BJ only (vendor_scope
    ``population_disjoint``, no B-share/ETF/三板 rows observed — F2/F4), so an
    out-of-population code here is unexpected and must fail loud, not be
    silently guessed at.
    """

    if is_active_a_share(ts_code):
        return False
    reason = classify_exclusion(ts_code) or ""
    if TUSHARE_MARKET_BJ_LABEL in reason:
        return True
    raise FuyaoDailyKError(
        f"fuyao daily_k_dump: ts_code={ts_code!r} 既不在沪深股票池也不是北交所 "
        f"(classify_exclusion={reason!r}) —— dump 总体应只含 A 股 SH/SZ/BJ "
        "(vendor_scope population_disjoint 已实测无 900/200 前缀), 这是未预期的越界"
        "代码, 不静默归类为北交所或忽略"
    )


def _exchange_prefix_code(ts_code: str, rules: NominalOhlcvAcquireRules) -> str:
    suffix = ts_code.split(".")[-1].upper()
    prefix_map = rules.exchange_suffix_to_baostock_prefix
    if suffix not in prefix_map:
        raise FuyaoDailyKError(
            f"fuyao daily_k_dump: ts_code={ts_code!r} 交易所后缀 {suffix!r} 不在 "
            f"reference.exchange_suffix_to_baostock_prefix {sorted(prefix_map)} 里 "
            "(股票池成员理论上只有 SH/SZ)"
        )
    numeric = ts_code.split(".")[0]
    return f"{prefix_map[suffix]}{numeric}"


def _is_per_code_baostock_failure(exc: BaseException) -> bool:
    """True when ``exc`` is specific to the one code just queried (this
    row's reference is unavailable) rather than a session-level failure that
    would also break every other pool row this run. Reuses
    ``sources/baostock.py``'s own failure classification — this module never
    duplicates that taxonomy.

    返修 (blocking 发现 #2 修复): 之前这里把"除 ACCOUNT_PERMISSION 外一律单码"
    (``!= ACCOUNT_PERMISSION``) 当判据, 于是 baostock 0.9.3 把 socket
    超时/断连吞成的 ``BSERR_RECVSOCK_FAIL`` 等 (分类 TRANSIENT_NETWORK) 和系统性
    异常 (分类 CLIENT_PARSE) 全被当成"这一个码没有参考"逐行吞掉, 一次服务端中途
    挂掉要等几千个码全部超时一遍才会被 §3 的 unknown 阈值门拦下。只有
    CALLER_ERROR (10004xxx 参数/代码类, 真的只影响这一个码的请求) 才是单码失败；
    TRANSIENT_NETWORK/CLIENT_PARSE/未识别错误码都是会话级或服务端级故障, 必须
    立即熔断, 不能继续拿后面的码去撞同一个已经出问题的服务端。
    """

    from services.data_sources.sources.baostock import (
        CALLER_ERROR,
        BaostockQueryError,
        classify_baostock_failure,
    )

    if not isinstance(exc, BaostockQueryError):
        return False
    return classify_baostock_failure(exc) == CALLER_ERROR


class _BaostockReferenceCache:
    """Per-``FuyaoSource``-instance cache of baostock ``query_history_k_data_
    plus`` rows, keyed by baostock code, plus the session circuit breaker.

    ``baostock_source`` is duck-typed to ``fetch_raw(api, **params) ->
    list[dict]`` — production passes a real ``BaostockSource``; tests pass
    either a real ``BaostockSource(bs_module=<fake>)`` (to exercise the real
    login/lock/retry machinery, e.g. for the circuit-breaker assertion) or a
    bare fake object (for pure row-classification assertions).
    """

    def __init__(self, baostock_source: Any, rules: NominalOhlcvAcquireRules) -> None:
        self._baostock = baostock_source
        self._rules = rules
        self._broken = False
        self._by_code: dict[str, dict[str, Mapping[str, Any]]] = {}
        # 2026-09-18 (ST 契约 v2 刀2): 每次真实 baostock 响应行的证据副本, 供
        # sync_runner 在 daily 落库时一并落进 raw_baostock_daily_k 水库 (见
        # drain())。负缓存哨兵 (_BAOSTOCK_REFERENCE_MISS) 不是行, 不进这里。
        self._pending: list[ReservoirRow] = []

    @property
    def broken(self) -> bool:
        return self._broken

    def _to_baostock_code(self, ts_code: str) -> str:
        return _exchange_prefix_code(ts_code, self._rules)

    def _query(self, code: str, start_compact: str, end_compact: str) -> list[dict[str, Any]]:
        try:
            return list(
                self._baostock.fetch_raw(
                    self._rules.reference_api,
                    code=code,
                    fields=self._rules.baostock_fields_csv,
                    start_date=dashed_date(start_compact),
                    end_date=dashed_date(end_compact),
                )
                or []
            )
        except Exception as exc:  # noqa: BLE001 — reclassified below, never swallowed silently
            if _is_per_code_baostock_failure(exc):
                return []
            self._broken = True
            raise BaostockCircuitOpenError(
                "fuyao daily_k_dump: baostock 会话级失败, 本实例本次运行后续调用直接 "
                f"拒绝再触网 (code={code}): {exc}"
            ) from exc

    def _absorb(
        self,
        ts_code: str,
        code: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        fetched_at: datetime,
        request_start: str,
        request_end: str,
        fetch_context: str,
    ) -> None:
        by_date = self._by_code.setdefault(code, {})
        for row in rows:
            compact = str(row.get("date") or "").replace("-", "")
            if compact:
                by_date[compact] = row
                self._pending.append(
                    ReservoirRow(
                        ts_code=ts_code,
                        trade_date=compact,
                        fetched_at=fetched_at,
                        baostock_code=code,
                        fields_csv=self._rules.baostock_fields_csv,
                        payload=dict(row),
                        fetch_context=fetch_context,
                        request_start=request_start,
                        request_end=request_end,
                    )
                )

    def drain(self) -> list[ReservoirRow]:
        """返回并清空本次运行至今积累的证据副本 —— sync_runner 在 daily 一次
        分区落库之后调用它, 把这些行落进 raw_baostock_daily_k 水库 (§3.3)。
        再次调用 (没有新增查询) 返回 ``[]``。"""

        pending, self._pending = self._pending, []
        return pending

    def lookup(self, ts_code: str, trade_date: str) -> Mapping[str, Any] | None:
        code = self._to_baostock_code(ts_code)
        by_date = self._by_code.get(code, {})
        if trade_date in by_date:
            cached = by_date[trade_date]
            return None if cached is _BAOSTOCK_REFERENCE_MISS else cached
        if self._broken:
            raise BaostockCircuitOpenError(
                "fuyao daily_k_dump: baostock 会话已在本次运行中失败过一次, 不再重试触网 "
                f"(ts_code={ts_code})"
            )
        fetch_context = f"daily_adapter:{trade_date}"
        window_end = _shift_yyyymmdd(trade_date, self._rules.prefetch_window_days)
        self._absorb(
            ts_code, code, self._query(code, trade_date, window_end),
            fetched_at=datetime.now(timezone.utc), request_start=trade_date,
            request_end=window_end, fetch_context=fetch_context,
        )
        by_date = self._by_code.get(code, {})
        if trade_date in by_date:
            return by_date[trade_date]
        # Prefetch window missed t (e.g. this code has almost no history yet)
        # — degrade to one single-day query before giving up.
        self._absorb(
            ts_code, code, self._query(code, trade_date, trade_date),
            fetched_at=datetime.now(timezone.utc), request_start=trade_date,
            request_end=trade_date, fetch_context=fetch_context,
        )
        by_date = self._by_code.setdefault(code, {})
        if trade_date in by_date:
            return by_date[trade_date]
        # 返修 (blocking 发现 #2 修复): 两次查询都确认没有这一行 —— 写负缓存哨兵,
        # 而不是留空(不存在的键在下一次 lookup 里和"还没查过"分不清), 这样同一个
        # (码,日) 的 miss 无论是在同一次 fetch_rows 内(不会发生, 一码一行)还是跨
        # 一次 sync 运行内的重试/重跑, 都只真正问服务端一次。负缓存哨兵不是行,
        # 不进 drain() 的证据副本。
        by_date[trade_date] = _BAOSTOCK_REFERENCE_MISS
        return None


class _DumpCache:
    """Per-``FuyaoSource``-instance cache of downloaded dumps (path + real
    release identity), one per :class:`marketdb.providers.dump.DownloadKind`.
    Downloads (signs + fetches) each kind at most once per adapter lifetime
    ("进程内缓存一次" — the adapter instance lives as long as the
    ``FuyaoSource`` singleton, i.e. one ``chunkyctl sync`` run).

    返修 (blocking 发现修复): 之前这里只留 ``.path``, 把下载器已经给出的
    ``release_tag``/``release_key`` (presigned URL 里的真实发布身份, 见
    ``marketdb.providers.dump.DumpDownloader.fetch``) 就地丢弃, 调用方只能拿
    dump *kind* (哪个文件) 冒充 release 身份 (哪一次发布) —— 违反红线 14
    (缺 lineage = UNTRUSTED)。现在整份 ``DownloadedDump`` 都缓存下来,
    :meth:`release_info_for` 把真实值原样交回。

    返修 (blocking 发现 #1 修复): ``sha256_for`` 对已落地的 parquet 文件本身
    流式现算 sha256 (不是猜供应商响应头有没有这个字段, F11 说未验证) ——
    每个 kind 在一次 adapter 生命周期内只下载一次也只算一次, 结果缓存在
    ``self._sha256``。"""

    def __init__(self, downloader: Any) -> None:
        self._downloader = downloader
        self._dumps: dict[Any, Any] = {}
        self._sha256: dict[Any, str] = {}

    def _dump_for(self, kind: Any) -> Any:
        dumped = self._dumps.get(kind)
        if dumped is None:
            dumped = self._downloader.fetch(kind)
            self._dumps[kind] = dumped
        return dumped

    def rows_for_date(
        self, kind: Any, trade_date: str, rules: NominalOhlcvAcquireRules
    ) -> list[dict[str, Any]]:
        path = Path(self._dump_for(kind).path)
        date_ms = _shanghai_midnight_ms(trade_date, tz_name=rules.dump_timezone)
        return _read_parquet_rows_for_date(
            path, date_ms=date_ms, adjusted_filter_value=rules.dump_adjusted_filter_value
        )

    def sha256_for(self, kind: Any) -> str | None:
        """Real content hash of the already-downloaded dump file for ``kind``,
        computed once and cached. ``None`` only when this kind was never
        fetched this run (defensive; callers only ask about a kind that
        already produced rows via :meth:`rows_for_date`)."""

        dumped = self._dumps.get(kind)
        if dumped is None:
            return None
        cached = self._sha256.get(kind)
        if cached is None:
            cached = _sha256_of_file(Path(dumped.path))
            self._sha256[kind] = cached
        return cached

    def release_info_for(self, kind: Any) -> tuple[str | None, str | None]:
        """Real ``(release_tag, release_key)`` for a kind already fetched this
        run (via :meth:`rows_for_date`) — read straight off the downloader's
        own ``DownloadedDump``, never derived or guessed from the kind itself.
        ``(None, None)`` if this kind was never fetched (defensive; callers
        only ever ask about a kind that already produced rows)."""

        dumped = self._dumps.get(kind)
        if dumped is None:
            return None, None
        return dumped.release_tag, dumped.release_key


@dataclass
class FuyaoDailyKDeps:
    """Everything :func:`fetch_daily_k_dump_rows` needs beyond pure config.
    Production builds this lazily via :func:`default_deps`; tests build it
    directly with fakes — this is the sole test-injection seam."""

    downloader: Any
    dump_kinds: Any  # DownloadKind-like: has .DAILY_K / .DAILY_K_10D members
    baostock_source: Any
    rules: NominalOhlcvAcquireRules
    # B1 修法 (2026-09-19 返修, ST 契约 v2 刀2/3 联动): required(D) 取数——D 日
    # dump 里缺席但 ST 覆盖判据需要它的沪深代码 (典型: 当天停牌的 ST 股, fuyao
    # dump 结构上不含无成交行, F4)。``Callable[[str], frozenset[str]]``, 输入
    # trade_date (compact), 输出 required(D) 代码集合; ``None`` (测试默认) =
    # "不补查" (与生产默认 :func:`_default_required_st_codes_for_date` 分开,
    # 后者自开自关只读连接, 拿不到连接时退化为空集合, 不崩)。
    required_st_codes_provider: Any | None = None


class FuyaoDailyKAdapter:
    """All cross-call state for one ``FuyaoSource`` instance's ``daily_k_dump``
    usage: the dump-kind cache, the baostock reference cache (with its
    circuit breaker), and the deps themselves (built lazily on first use)."""

    def __init__(self, deps_factory) -> None:
        self._deps_factory = deps_factory
        self._deps: FuyaoDailyKDeps | None = None
        self._dump_cache: _DumpCache | None = None
        self._baostock_cache: _BaostockReferenceCache | None = None

    def _ensure_deps(self) -> FuyaoDailyKDeps:
        if self._deps is None:
            self._deps = self._deps_factory()
            self._dump_cache = _DumpCache(self._deps.downloader)
            self._baostock_cache = _BaostockReferenceCache(
                self._deps.baostock_source, self._deps.rules
            )
        return self._deps

    def fetch_rows(self, trade_date: str) -> list[dict[str, Any]]:
        deps = self._ensure_deps()
        assert self._dump_cache is not None and self._baostock_cache is not None
        return _build_rows_for_date(
            trade_date,
            rules=deps.rules,
            dump_cache=self._dump_cache,
            baostock_cache=self._baostock_cache,
            dump_kinds=deps.dump_kinds,
            required_st_codes_provider=deps.required_st_codes_provider,
        )

    def build_page(self, trade_date: str) -> ProviderPage:
        """Same as :meth:`fetch_rows` but also returns the page-level
        ``request_meta`` (dump release identity, sha256, reference source,
        unknown-row counts) as a :class:`ProviderPage`.

        返修 (blocking 发现 #1 修复): this IS now what ``FuyaoSource.fetch_raw``
        dispatches to (see :func:`fetch_daily_k_dump_rows` below) — the
        previous cut kept that dispatch on the plain-``list`` :meth:`fetch_rows`
        instead, so ``request_meta`` never survived past this adapter. Making
        this the dispatch target alone was not sufficient (实测 2026-09-16):
        ``security_day_acquire.acquire_security_day_provider`` used to do
        ``tuple(fetch_rows(...) or ())``, which raises ``TypeError`` on a
        ``ProviderPage`` (no ``__iter__``), and ``sync_runner``'s land-only /
        land-then-accept closures rebuilt a *plain* ``list(acquired.rows)``
        from the acquire result before calling into
        ``capture_security_day_provider_rows`` — silently dropping
        ``request_meta`` at that second boundary even once the first stopped
        crashing. Both of those were changed too (see their own 返修 comments)
        so a ``ProviderPage`` returned here actually reaches
        ``ingest_batch.request_json`` in production, not just in this
        module's own offline tests."""

        deps = self._ensure_deps()
        assert self._dump_cache is not None and self._baostock_cache is not None
        rows, meta = _build_rows_and_meta_for_date(
            trade_date,
            rules=deps.rules,
            dump_cache=self._dump_cache,
            baostock_cache=self._baostock_cache,
            dump_kinds=deps.dump_kinds,
            required_st_codes_provider=deps.required_st_codes_provider,
        )
        return ProviderPage(rows=rows, request_meta=meta)

    def drain_baostock_daily_k_rows(self) -> list[ReservoirRow]:
        """委托给内部 ``_BaostockReferenceCache.drain()``。``_baostock_cache``
        为 ``None`` 时 (本次运行还没查过 baostock, deps 是惰性构造的) 返回
        ``[]`` —— 不强行触发 ``_ensure_deps()`` (那会去连真实 baostock/下载器,
        drain 只该读已经发生过的查询留下的证据副本)。"""

        if self._baostock_cache is None:
            return []
        return self._baostock_cache.drain()


def _classify_and_fill_reference(
    row: dict[str, Any], *, rules: NominalOhlcvAcquireRules, baostock_cache: _BaostockReferenceCache
) -> None:
    """Mutates ``row`` in place, adding pre_close/change/pct_chg/pre_close_origin."""

    ts_code = row["ts_code"]
    if _is_bj_ts_code(ts_code):
        row["pre_close"] = None
        row["change"] = None
        row["pct_chg"] = None
        row["pre_close_origin"] = _UNKNOWN_NO_REFERENCE_BJ
        return

    reference = baostock_cache.lookup(ts_code, row["trade_date"])
    if reference is None:
        row["pre_close"] = None
        row["change"] = None
        row["pct_chg"] = None
        row["pre_close_origin"] = _UNKNOWN_REFERENCE_UNAVAILABLE
        return

    bs_close_raw = reference.get("close")
    try:
        bs_close = float(bs_close_raw) if bs_close_raw not in (None, "") else None
    except (TypeError, ValueError):
        bs_close = None
    dump_close = row["close"]
    if bs_close is None or abs(bs_close - dump_close) > rules.close_tolerance:
        row["pre_close"] = None
        row["change"] = None
        row["pct_chg"] = None
        row["pre_close_origin"] = _UNKNOWN_REFERENCE_MISMATCH
        return

    pre_close_raw = reference.get("preclose")
    try:
        pre_close = float(pre_close_raw) if pre_close_raw not in (None, "") else None
    except (TypeError, ValueError):
        pre_close = None
    if pre_close is None or pre_close == 0:
        row["pre_close"] = None
        row["change"] = None
        row["pct_chg"] = None
        row["pre_close_origin"] = (
            _UNKNOWN_REFERENCE_UNAVAILABLE if pre_close is None else _UNKNOWN_REFERENCE_MISMATCH
        )
        return

    digits = rules.change_pct_round_digits
    # 全程 Decimal (业主 09-16 明令): dump_close/pre_close 先各自转 Decimal(str(..)),
    # change 用量化后的 Decimal 值(不是原始浮点差)去算 pct_chg, 最后才转 float —— 见
    # _round_half_up 的 docstring, 6.41/6.40 这类半值边界曾因中途转 float 被错量化。
    dump_close_decimal = Decimal(str(dump_close))
    pre_close_decimal = Decimal(str(pre_close))
    change_decimal = _round_half_up(dump_close_decimal - pre_close_decimal, digits=digits)
    pct_chg_decimal = _round_half_up(change_decimal / pre_close_decimal * 100, digits=digits)
    row["pre_close"] = pre_close
    row["change"] = float(change_decimal)
    row["pct_chg"] = float(pct_chg_decimal)
    row["pre_close_origin"] = _PROVIDER_BAOSTOCK


def _rows_for_date_from_dump(
    trade_date: str,
    *,
    rules: NominalOhlcvAcquireRules,
    dump_cache: _DumpCache,
    dump_kinds: Any,
) -> tuple[list[dict[str, Any]], Any | None]:
    """Dump-kind selection (钉死顺序): 10d first, full daily-k only if 10d
    doesn't cover ``trade_date``. Returns ``(rows, kind_used)`` — ``kind_used``
    is the actual dump-kind object (e.g. ``dump_kinds.DAILY_K_10D``), not just
    its ``.value`` string, so the caller can look up that kind's real release
    identity via ``dump_cache.release_info_for`` (返修 blocking 发现修复: 之前
    这里提前把 kind 塌缩成字符串, 调用方就再也拿不到真实 release_tag/release_key
    了). ``None`` when neither kind has the date (caller returns ``[]``, the
    existing zero_rows path — not fabricated)."""

    ten_day_rows = dump_cache.rows_for_date(dump_kinds.DAILY_K_10D, trade_date, rules)
    if ten_day_rows:
        return ten_day_rows, dump_kinds.DAILY_K_10D
    full_rows = dump_cache.rows_for_date(dump_kinds.DAILY_K, trade_date, rules)
    if full_rows:
        return full_rows, dump_kinds.DAILY_K
    return [], None


def _supplement_required_st_codes(
    trade_date: str,
    *,
    dump_codes: set[str],
    baostock_cache: _BaostockReferenceCache,
    required_st_codes_provider: Any | None,
) -> dict[str, Any]:
    """B1 修法 (2026-09-19 返修): dump 结构上不含无成交行 (F4/U7) —— 当天停牌
    的 ST 股永远不在 ``dump_codes`` 里, daily 顺手灌水库时就永远查不到它的
    isST, ST 派生器覆盖判据 (``stock_st_derive.required_codes_for_date``) 就
    永远缺它一行, 假阴漏判。这里对 required(D) 里不在 dump 中的沪深代码也向
    baostock 查一次 D 这一天——**同一个会话、同一把锁** (复用已传入的
    ``baostock_cache``, 不新开 ``BaostockSource``, 见 sources/baostock.py 坑5)、
    **同一套 fields** (``baostock_cache._query`` 恒用 ``rules.baostock_fields_csv``,
    已含 isST)。查到的行经 ``.lookup()`` 的 ``_absorb()`` 照常进
    ``baostock_cache._pending``, 随下一次 ``drain()`` 落进水库——不合成任何
    OHLCV 输出行 (不伪造停牌股当天的成交, 红线 3)。

    没有 provider (测试默认) 或它算不出 required(D) (自己已经 fail-closed 到
    ``(frozenset(), "no_connection:...")``, 不在这里再包一层 try) → 视同"无额外
    代码", 只在 meta 里记一句, 不许崩这一天的 daily 落地。"""

    if required_st_codes_provider is None:
        return {"status": "not_configured", "codes_required": 0, "codes_missing_from_dump": 0, "codes_queried": 0}

    required, status = required_st_codes_provider(trade_date)
    missing = sorted(frozenset(required) - dump_codes)
    queried = 0
    if missing and not baostock_cache.broken:
        for code in missing:
            baostock_cache.lookup(code, trade_date)
            queried += 1
    return {
        "status": status,
        "codes_required": len(required),
        "codes_missing_from_dump": len(missing),
        "codes_queried": queried,
    }


def _build_rows_and_meta_for_date(
    trade_date: str,
    *,
    rules: NominalOhlcvAcquireRules,
    dump_cache: _DumpCache,
    baostock_cache: _BaostockReferenceCache,
    dump_kinds: Any,
    required_st_codes_provider: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if trade_date < rules.dump_incremental_floor:
        raise FuyaoDailyKError(
            f"fuyao daily_k_dump: trade_date={trade_date} 早于 dump.incremental_floor="
            f"{rules.dump_incremental_floor} —— 历史重生成走 landing_tushare_daily, "
            "不重放 dump (dump 只当增量源)"
        )

    raw_rows, kind_used = _rows_for_date_from_dump(
        trade_date, rules=rules, dump_cache=dump_cache, dump_kinds=dump_kinds
    )
    if not raw_rows:
        st_backfill_supplement = _supplement_required_st_codes(
            trade_date, dump_codes=set(), baostock_cache=baostock_cache,
            required_st_codes_provider=required_st_codes_provider,
        )
        return [], {
            "dump_release_key": None,
            "dump_release_tag": None,
            "dump_kind": None,
            "dump_sha256": None,
            "reference_source": rules.reference_source,
            "unknown_rows_sh_sz": 0,
            "unknown_rows_bj": 0,
            "st_backfill_supplement": st_backfill_supplement,
        }

    # 返修 (blocking 发现 #2 修复): 阈值门逐行累加、超限立即 raise 不再处理剩余行 ——
    # 之前是"全表分类完再求和判阈值", 一天几千个池内代码时, 无论是真系统性故障
    # (未分类为熔断的失败) 还是单纯零星缺参考多, 都要等最后一行分类完才会被这道门
    # 拦下; 逐行累加后, 池内 unknown 一超过阈值就立刻停, 最多只多问阈值+1 个码的
    # baostock, 不会再拿剩下的几千个码去撞同一个可能已经出问题的服务端。
    rows = [_map_dump_row(raw, rules) for raw in raw_rows]
    unknown_rows_sh_sz = 0
    unknown_rows_bj = 0
    for processed, row in enumerate(rows, start=1):
        _classify_and_fill_reference(row, rules=rules, baostock_cache=baostock_cache)
        if row["pre_close_origin"] == _UNKNOWN_NO_REFERENCE_BJ:
            unknown_rows_bj += 1
            continue
        if row["pre_close_origin"] != _PROVIDER_BAOSTOCK:
            unknown_rows_sh_sz += 1
            if unknown_rows_sh_sz > rules.sh_sz_max_unknown_rows:
                raise FuyaoDailyKError(
                    f"fuyao daily_k_dump: trade_date={trade_date} 股票池内 unknown 行数="
                    f"{unknown_rows_sh_sz} 在处理到第 {processed}/{len(rows)} 行时已超过 "
                    f"reference.sh_sz_max_unknown_rows={rules.sh_sz_max_unknown_rows}, "
                    "立即停止不再处理剩余行, 整天拒收不落地 (不许无界放行 NULL)"
                )

    dump_codes = {str(r["ts_code"]).strip().upper() for r in rows}
    st_backfill_supplement = _supplement_required_st_codes(
        trade_date, dump_codes=dump_codes, baostock_cache=baostock_cache,
        required_st_codes_provider=required_st_codes_provider,
    )

    release_tag, release_key = dump_cache.release_info_for(kind_used)
    meta = {
        "dump_release_key": release_key,
        "dump_release_tag": release_tag,
        "dump_kind": str(kind_used.value),
        # 返修 (blocking 发现 #1 修复): 实测下载落地的 parquet 现算 sha256, 不再
        # 恒为 None —— 见 _DumpCache.sha256_for。
        "dump_sha256": dump_cache.sha256_for(kind_used),
        "reference_source": rules.reference_source,
        "unknown_rows_sh_sz": unknown_rows_sh_sz,
        "unknown_rows_bj": unknown_rows_bj,
        "st_backfill_supplement": st_backfill_supplement,
    }
    return rows, meta


def _build_rows_for_date(
    trade_date: str,
    *,
    rules: NominalOhlcvAcquireRules,
    dump_cache: _DumpCache,
    baostock_cache: _BaostockReferenceCache,
    dump_kinds: Any,
    required_st_codes_provider: Any | None = None,
) -> list[dict[str, Any]]:
    rows, _meta = _build_rows_and_meta_for_date(
        trade_date,
        rules=rules,
        dump_cache=dump_cache,
        baostock_cache=baostock_cache,
        dump_kinds=dump_kinds,
        required_st_codes_provider=required_st_codes_provider,
    )
    return rows


def _default_required_st_codes_for_date(trade_date: str) -> tuple[frozenset[str], str]:
    """``FuyaoDailyKDeps.required_st_codes_provider`` 的生产默认实现 (B1 修法,
    2026-09-19 返修): 自开自关只读连接 (与 ``stock_st_derive._DefaultReservoirReader``
    同型), 复用 ``stock_st_derive.required_codes_for_date`` (同一个判定函数,
    不重复定义第二套集合运算——B1/B4 教训)。

    拿不到连接 (测试环境 / 库不存在 / 权限问题) → ``(frozenset(), "no_connection:
    <异常类名>")``, **不许崩**——required(D) 补查是 daily 顺手灌水库的增强功能,
    它算不出分母不该让 daily 主链路的行落地失败。"""

    try:
        from services.data_access.resolver import connect_ro
        from services.data_sources.sources.stock_st_derive import required_codes_for_date
        from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

        day = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))
        conn = connect_ro("tushare_raw")
        try:
            codes = required_codes_for_date(conn, day, load_stock_st_acquire_rules())
        finally:
            conn.close()
        return codes, "ok"
    except Exception as exc:  # rule-compliance: ok evidence=required(D)-补查拿不到连接时(测试/无库)按无额外代码处理不许让daily主链路崩
        return frozenset(), f"no_connection:{type(exc).__name__}"


def default_deps(rules: NominalOhlcvAcquireRules | None = None) -> FuyaoDailyKDeps:
    """Production dependency factory: real dump downloader + real
    ``BaostockSource`` (which itself lazily imports the ``baostock`` package —
    this module never imports it directly, per 修订2)."""

    from services.data_sources.sources.baostock import BaostockSource
    from services.data_sources.sources.fuyao import dump_downloader, dump_kinds, resolve_api_key

    resolved_rules = rules if rules is not None else load_nominal_ohlcv_acquire_rules()
    api_key = resolve_api_key()
    if not api_key:
        raise FuyaoDailyKError(
            "fuyao daily_k_dump: HITHINK_FINANCE_API_KEY / credentials.env missing"
        )
    downloader = dump_downloader(
        api_key=api_key, cache_dir=_REPO_ROOT / resolved_rules.dump_cache_dir
    )
    return FuyaoDailyKDeps(
        downloader=downloader,
        dump_kinds=dump_kinds(),
        baostock_source=BaostockSource(),
        rules=resolved_rules,
        required_st_codes_provider=_default_required_st_codes_for_date,
    )


@dataclass(frozen=True)
class SurvivorGateResult:
    legitimate_suspension: tuple[str, ...]
    real_gap: tuple[str, ...]
    unverified: tuple[str, ...]


def compute_survivor_gate(
    *,
    canonical_codes: Sequence[str],
    dump_codes: Sequence[str],
    baostock_source: Any,
    rules: NominalOhlcvAcquireRules,
    window_dates: Sequence[str],
) -> SurvivorGateResult:
    """§1 的幸存者门: ``S = canonical_codes - dump_codes`` —— 某个 t 日的 dump 相对
    08-31 canonical 漏掉了哪些代码。SH/SZ 成员逐个查 baostock ``window_dates`` 区间的
    ``tradestatus``/``volume``: 全程 ``tradestatus=0`` 视为合法停牌; 任一日
    ``tradestatus=1`` 且 ``volume>0`` 视为真漏 (FAIL, raise 停下来问业主, 不静默放行,
    不进刀3)。BJ 成员 baostock 结构上查不到, 标 ``unverified`` (既不是"合法停牌"也不是
    "真漏" —— 这条腿根本核证不了它)。单个代码的 baostock 查询失败 (per-code, 非会话级)
    同样标 ``unverified``, 不当真漏也不当合法停牌处理 (查不出来 != 合法停牌)。

    只读、不落库、不改任何 accepted 分区; 调用方 (主循环, 本刀不跑) 负责把
    ``canonical_codes``/``dump_codes`` 两个集合现查出来再传进来 —— 本函数不打开也不该
    知道任何 DuckDB 连接。
    """

    survivors = sorted(set(canonical_codes) - set(dump_codes))
    legitimate: list[str] = []
    real_gap: list[str] = []
    unverified: list[str] = []
    if not window_dates:
        raise FuyaoDailyKError("fuyao daily_k_dump 幸存者门: window_dates 不能为空")
    start, end = window_dates[0], window_dates[-1]
    for ts_code in survivors:
        if _is_bj_ts_code(ts_code):
            unverified.append(ts_code)
            continue
        code = _exchange_prefix_code(ts_code, rules)
        try:
            rows = (
                baostock_source.fetch_raw(
                    rules.reference_api,
                    code=code,
                    fields=rules.baostock_fields_csv,
                    start_date=dashed_date(start),
                    end_date=dashed_date(end),
                )
                or []
            )
        except Exception as exc:  # noqa: BLE001 — reclassified below, never swallowed silently
            if _is_per_code_baostock_failure(exc):
                unverified.append(ts_code)
                continue
            raise BaostockCircuitOpenError(
                f"fuyao daily_k_dump 幸存者门: baostock 会话级失败 (code={code}): {exc}"
            ) from exc
        by_date = {str(r.get("date") or "").replace("-", ""): r for r in rows}
        is_real_gap = False
        for d in window_dates:
            row = by_date.get(d)
            if row is None:
                continue
            raw_status = row.get("tradestatus")
            tradestatus = str(raw_status) if raw_status is not None else ""
            volume_raw = row.get("volume")
            try:
                volume_val = float(volume_raw) if volume_raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                volume_val = 0.0
            if tradestatus == "1" and volume_val > 0:
                is_real_gap = True
                break
        (real_gap if is_real_gap else legitimate).append(ts_code)

    if real_gap:
        raise FuyaoDailyKError(
            f"fuyao daily_k_dump 幸存者门: {len(real_gap)} 只真漏 (窗口内某日 "
            "tradestatus=1 且 volume>0 但已从 dump 消失), 停下来问业主, 不进刀3: "
            f"{sorted(real_gap)[:10]}"
        )
    return SurvivorGateResult(
        legitimate_suspension=tuple(legitimate),
        real_gap=tuple(real_gap),
        unverified=tuple(unverified),
    )


def fetch_daily_k_dump_rows(source: Any, **params: Any) -> ProviderPage:
    """``FuyaoSource.fetch_raw("daily_k_dump", **params)`` dispatch target.

    ``source`` is the calling ``FuyaoSource`` instance (a long-lived singleton
    across one ``chunkyctl sync`` run per ``sync_runner._adapter`` — see that
    module's ``_FUYAO_SOURCE`` cache) — this function attaches a
    :class:`FuyaoDailyKAdapter` to it (plain dynamic attribute; ``FuyaoSource``
    defines no ``__slots__``) so the dump-kind cache and the baostock circuit
    breaker survive across separate ``fetch_raw`` calls for different
    ``trade_date`` values within the same run, without ``FuyaoSource.
    __init__`` needing to know anything about this domain.

    Returns a :class:`ProviderPage` (返修 blocking 发现 #1 修复: previously a
    plain ``list[dict]`` via ``adapter.fetch_rows``, which lost the dump
    release identity/sha256/reference-source metadata this adapter already
    computes — see ``build_page``'s docstring). ``ProviderPage.__len__``/
    ``__bool__`` keep the existing generic ``sync_runner._fetch_with_retry``
    truthiness/emptiness contract (``if rows: return rows`` / ``return []`` on
    exhaustion) working unchanged for this ``(source, api)`` — no caller along
    the way needs to special-case this adapter to keep functioning, and the
    two callers that previously discarded page-level metadata while
    unwrapping (``security_day_acquire.acquire_security_day_provider`` and
    ``sync_runner``'s land-only/land-then-accept closures) now preserve it
    instead.
    """

    trade_date = str(params.get("trade_date") or "").strip()
    if not trade_date:
        raise FuyaoDailyKError("fuyao daily_k_dump: trade_date is required")
    adapter = getattr(source, "_daily_k_adapter", None)
    if adapter is None:
        adapter = FuyaoDailyKAdapter(default_deps)
        source._daily_k_adapter = adapter  # noqa: SLF001 — intentional cross-module cache seam
    return adapter.build_page(trade_date)


__all__ = [
    "BaostockCircuitOpenError",
    "FuyaoDailyKAdapter",
    "FuyaoDailyKDeps",
    "FuyaoDailyKError",
    "SurvivorGateResult",
    "compute_survivor_gate",
    "default_deps",
    "fetch_daily_k_dump_rows",
]
