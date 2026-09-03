"""Stock-universe policy and legacy helpers.

第一性原理: 名义 K 线 + 交易日历是发布 eligibility 的事实基础，
不能用今天的快照重写历史。正式口径是 ``traded_on_observation_date``：
观察日有名义日 K 线且 board∈沪深A 白名单才进入当日项目股票池（**含 ST/*ST**）。
``stock_st`` 是独立 PIT membership 证据（谁在何时是 ST），不是 denylist。
``get_active_universe`` 与 ``assert_universe_clean`` 只是迁移前的当前态/静态前缀
helper，不构成完整 PIT 发布门；正式路径必须消费同一次执行绑定的 UniversePolicy
与 population scope。

项目口径排除规则 (owner 2026-07-22):
  1. 前缀不是 60/00/30/68 → 排除 (B股/北交所BJ/三板/ETF)
  2. 观察日无名义日 K 线 → 排除 (停牌/已退市/尚未上市)
  不按 ST/*ST 名称或 stock_st 成员踢出沪深A。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Literal, final

import yaml


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "universe_rules.yaml"
_POLICY_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_BOARD_PREFIX = re.compile(r"[0-9]{2}\Z")
_EXCLUDED_PREFIX = re.compile(r"[0-9]{1,2}\Z")
_EXCHANGE_ID = re.compile(r"[A-Z][A-Z0-9_]{1,15}\Z")
_TS_SUFFIX = re.compile(r"[A-Z]{2}\Z")
_SOURCE_REF = re.compile(r"[a-z][a-z0-9_.]*\Z")


class UniverseDataError(RuntimeError):
    """Raised when a required universe truth source is unavailable."""


class UniverseContaminationError(RuntimeError):
    """Raised when a stock set contains excluded (non-whitelist) codes.

    2026-06-17 用户决议: universe 升到交易日历级硬真相源 — 排除股进任何
    验证/回测/GT = 硬错, 不是 warning。任何最终股票集必过 assert_universe_clean()。
    """


@dataclass(frozen=True)
class SecurityVenueRule:
    board_prefix: str
    exchange_id: str
    ts_suffix: str


@final
@dataclass(frozen=True, init=False)
class UniversePolicy:
    """Factory-owned, validated snapshot of the formal universe policy."""

    policy_id: str
    policy_version: int
    allowed_board_prefixes: tuple[str, ...]
    allowed_exchange_ids: tuple[str, ...]
    venue_rules: tuple[SecurityVenueRule, ...]
    eligibility_rule: Literal["traded_on_observation_date"]
    calendar_exchange_id: str
    nominal_kline_source: str
    st_membership_source: str
    trading_calendar_source: str
    excluded_boards: tuple[tuple[str, str], ...]
    limit_up_pct: tuple[tuple[str, float], ...]
    config_hash: str

    def __new__(cls, *_args: Any, **_kwargs: Any):
        raise TypeError("use load_universe_policy()")


@dataclass(frozen=True)
class CurrentEnumerationPolicy:
    """Legacy current-state request enumeration; never a historical PIT input."""

    identity_source: str
    st_name_patterns: tuple[str, ...]
    no_recent_kline_days: int


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ValueError(f"{field} missing keys: {missing}")
    if unknown:
        raise ValueError(f"{field} unknown keys: {unknown}")


def _non_empty_text(value: Any, field: str, *, pattern: re.Pattern | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{field} contains malformed value {value!r}")
    return value


def _unique_texts(
    value: Any,
    field: str,
    *,
    pattern: re.Pattern | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    items: list[str] = []
    for raw in value:
        item = _non_empty_text(raw, field, pattern=pattern)
        if item in items:
            raise ValueError(f"{field} has duplicate value {item!r}")
        items.append(item)
    return tuple(items)


def _semantic_hash(
    *,
    policy_id: str,
    policy_version: int,
    board_prefixes: tuple[str, ...],
    exchange_ids: tuple[str, ...],
    venue_rules: tuple[SecurityVenueRule, ...],
    eligibility_rule: str,
    calendar_exchange_id: str,
    nominal_kline_source: str,
    st_membership_source: str,
    trading_calendar_source: str,
    excluded_boards: tuple[tuple[str, str], ...],
    limit_up_pct: tuple[tuple[str, float], ...],
) -> str:
    payload = {
        "policy_id": policy_id,
        "policy_version": policy_version,
        "allowed_board_prefixes": sorted(board_prefixes),
        "allowed_exchange_ids": sorted(exchange_ids),
        "venue_rules": [
            {
                "board_prefix": rule.board_prefix,
                "exchange_id": rule.exchange_id,
                "ts_suffix": rule.ts_suffix,
            }
            for rule in sorted(venue_rules, key=lambda item: item.board_prefix)
        ],
        "eligibility": {
            "rule": eligibility_rule,
            "calendar_exchange_id": calendar_exchange_id,
        },
        "truth_sources": {
            "nominal_kline": nominal_kline_source,
            "st_membership": st_membership_source,
            "trading_calendar": trading_calendar_source,
        },
        "excluded_boards": dict(sorted(excluded_boards)),
        "limit_up_pct": dict(sorted(limit_up_pct)),
    }
    blob = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(blob).hexdigest()


def load_universe_policy(config_path: Path | str | None = None) -> UniversePolicy:
    """Load one strict policy snapshot; missing or malformed config fails closed."""

    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    try:
        raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "root")
        policy = _mapping(raw.get("policy"), "policy")
        include = _mapping(raw.get("include"), "include")
        exclude = _mapping(raw.get("exclude"), "exclude")
        eligibility = _mapping(raw.get("eligibility"), "eligibility")
        sources = _mapping(raw.get("truth_source"), "truth_source")
        current = _mapping(raw.get("current_enumeration"), "current_enumeration")

        _exact_keys(
            raw,
            {
                "policy",
                "include",
                "eligibility",
                "exclude",
                "limit_up_pct",
                "truth_source",
                "current_enumeration",
            },
            "root",
        )
        _exact_keys(policy, {"id", "version"}, "policy")
        _exact_keys(
            include,
            {"board_prefixes", "exchange_ids", "venue_by_prefix"},
            "include",
        )
        _exact_keys(exclude, {"excluded_boards"}, "exclude")
        _exact_keys(
            eligibility,
            {"rule", "calendar_exchange_id"},
            "eligibility",
        )
        _exact_keys(
            sources,
            {"nominal_kline", "st_membership", "trading_calendar"},
            "truth_source",
        )
        _exact_keys(
            current,
            {"identity_source", "st_name_patterns", "no_recent_kline_days"},
            "current_enumeration",
        )

        policy_id = _non_empty_text(policy.get("id"), "policy.id", pattern=_POLICY_ID)
        policy_version = policy.get("version")
        if (
            isinstance(policy_version, bool)
            or not isinstance(policy_version, int)
            or policy_version <= 0
        ):
            raise ValueError("policy.version must be a positive integer")

        board_prefixes = _unique_texts(
            include.get("board_prefixes"),
            "include.board_prefixes",
            pattern=_BOARD_PREFIX,
        )
        exchange_ids = _unique_texts(
            include.get("exchange_ids"),
            "include.exchange_ids",
            pattern=_EXCHANGE_ID,
        )
        venue_raw = _mapping(include.get("venue_by_prefix"), "include.venue_by_prefix")
        if set(venue_raw) != set(board_prefixes):
            raise ValueError(
                "include.venue_by_prefix keys must exactly match include.board_prefixes"
            )
        venue_rules: list[SecurityVenueRule] = []
        for prefix, raw_rule in venue_raw.items():
            rule = _mapping(raw_rule, f"include.venue_by_prefix[{prefix!r}]")
            _exact_keys(
                rule,
                {"exchange_id", "ts_suffix"},
                f"include.venue_by_prefix[{prefix!r}]",
            )
            exchange_id = _non_empty_text(
                rule.get("exchange_id"),
                f"include.venue_by_prefix[{prefix!r}].exchange_id",
                pattern=_EXCHANGE_ID,
            )
            if exchange_id not in exchange_ids:
                raise ValueError(
                    f"include.venue_by_prefix[{prefix!r}].exchange_id is not allowed"
                )
            venue_rules.append(
                SecurityVenueRule(
                    board_prefix=str(prefix),
                    exchange_id=exchange_id,
                    ts_suffix=_non_empty_text(
                        rule.get("ts_suffix"),
                        f"include.venue_by_prefix[{prefix!r}].ts_suffix",
                        pattern=_TS_SUFFIX,
                    ),
                )
            )
        if {rule.exchange_id for rule in venue_rules} != set(exchange_ids):
            raise ValueError("every include.exchange_ids value must own at least one prefix")

        eligibility_rule = _non_empty_text(
            eligibility.get("rule"), "eligibility.rule"
        )
        if eligibility_rule != "traded_on_observation_date":
            raise ValueError(
                "eligibility.rule must be 'traded_on_observation_date'"
            )
        calendar_exchange_id = _non_empty_text(
            eligibility.get("calendar_exchange_id"),
            "eligibility.calendar_exchange_id",
            pattern=_EXCHANGE_ID,
        )
        if calendar_exchange_id not in exchange_ids:
            raise ValueError(
                "eligibility.calendar_exchange_id must be an allowed exchange"
            )

        nominal_kline_source = _non_empty_text(
            sources.get("nominal_kline"),
            "truth_source.nominal_kline",
            pattern=_SOURCE_REF,
        )
        st_membership_source = _non_empty_text(
            sources.get("st_membership"),
            "truth_source.st_membership",
            pattern=_SOURCE_REF,
        )
        trading_calendar_source = _non_empty_text(
            sources.get("trading_calendar"),
            "truth_source.trading_calendar",
            pattern=_SOURCE_REF,
        )
        _non_empty_text(
            current.get("identity_source"),
            "current_enumeration.identity_source",
            pattern=_SOURCE_REF,
        )
        _unique_texts(
            current.get("st_name_patterns"),
            "current_enumeration.st_name_patterns",
        )
        no_recent_kline_days = current.get("no_recent_kline_days")
        if (
            isinstance(no_recent_kline_days, bool)
            or not isinstance(no_recent_kline_days, int)
            or no_recent_kline_days <= 0
        ):
            raise ValueError(
                "current_enumeration.no_recent_kline_days must be a positive integer"
            )

        excluded_raw = _mapping(exclude.get("excluded_boards"), "exclude.excluded_boards")
        if not excluded_raw:
            raise ValueError("exclude.excluded_boards must be non-empty")
        excluded_boards = tuple(
            (
                _non_empty_text(prefix, "exclude.excluded_boards key", pattern=_EXCLUDED_PREFIX),
                _non_empty_text(label, f"exclude.excluded_boards[{prefix!r}]"),
            )
            for prefix, label in excluded_raw.items()
        )
        overlap = sorted(
            (allowed, excluded)
            for allowed in board_prefixes
            for excluded, _label in excluded_boards
            if allowed.startswith(excluded) or excluded.startswith(allowed)
        )
        if overlap:
            raise ValueError(
                f"include.board_prefixes overlaps exclude.excluded_boards: {overlap}"
            )

        limits_raw = _mapping(raw.get("limit_up_pct"), "limit_up_pct")
        if set(limits_raw) != set(board_prefixes):
            raise ValueError("limit_up_pct keys must exactly match include.board_prefixes")
        limits: list[tuple[str, float]] = []
        for prefix, raw_value in limits_raw.items():
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not 0 < float(raw_value) <= 1
            ):
                raise ValueError(f"limit_up_pct[{prefix!r}] must be in (0, 1]")
            limits.append((str(prefix), float(raw_value)))
        limit_up_pct = tuple(limits)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise UniverseDataError(f"invalid universe policy {path}: {exc}") from exc

    config_hash = _semantic_hash(
        policy_id=policy_id,
        policy_version=policy_version,
        board_prefixes=board_prefixes,
        exchange_ids=exchange_ids,
        venue_rules=tuple(venue_rules),
        eligibility_rule=eligibility_rule,
        calendar_exchange_id=calendar_exchange_id,
        nominal_kline_source=nominal_kline_source,
        st_membership_source=st_membership_source,
        trading_calendar_source=trading_calendar_source,
        excluded_boards=excluded_boards,
        limit_up_pct=limit_up_pct,
    )
    snapshot = object.__new__(UniversePolicy)
    for field, value in (
        ("policy_id", policy_id),
        ("policy_version", policy_version),
        ("allowed_board_prefixes", board_prefixes),
        ("allowed_exchange_ids", exchange_ids),
        ("venue_rules", tuple(venue_rules)),
        ("eligibility_rule", eligibility_rule),
        ("calendar_exchange_id", calendar_exchange_id),
        ("nominal_kline_source", nominal_kline_source),
        ("st_membership_source", st_membership_source),
        ("trading_calendar_source", trading_calendar_source),
        ("excluded_boards", excluded_boards),
        ("limit_up_pct", limit_up_pct),
        ("config_hash", config_hash),
    ):
        object.__setattr__(snapshot, field, value)
    return snapshot


def load_current_enumeration_policy(
    config_path: Path | str | None = None,
) -> CurrentEnumerationPolicy:
    """Load the explicitly non-PIT current request-enumeration policy."""

    path = Path(config_path) if config_path is not None else _CONFIG_PATH
    try:
        raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "root")
        current = _mapping(raw.get("current_enumeration"), "current_enumeration")
        _exact_keys(
            current,
            {"identity_source", "st_name_patterns", "no_recent_kline_days"},
            "current_enumeration",
        )
        identity_source = _non_empty_text(
            current.get("identity_source"),
            "current_enumeration.identity_source",
            pattern=_SOURCE_REF,
        )
        st_name_patterns = _unique_texts(
            current.get("st_name_patterns"),
            "current_enumeration.st_name_patterns",
        )
        no_recent_kline_days = current.get("no_recent_kline_days")
        if (
            isinstance(no_recent_kline_days, bool)
            or not isinstance(no_recent_kline_days, int)
            or no_recent_kline_days <= 0
        ):
            raise ValueError(
                "current_enumeration.no_recent_kline_days must be a positive integer"
            )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise UniverseDataError(
            f"invalid current universe enumeration policy {path}: {exc}"
        ) from exc
    return CurrentEnumerationPolicy(
        identity_source=identity_source,
        st_name_patterns=st_name_patterns,
        no_recent_kline_days=no_recent_kline_days,
    )


def verify_universe_policy(policy: UniversePolicy) -> UniversePolicy:
    """Recompute the semantic hash so even a forged/replaced snapshot fails closed."""

    if type(policy) is not UniversePolicy:
        raise UniverseDataError("formal universe policy must be factory-owned")
    expected = _semantic_hash(
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        board_prefixes=policy.allowed_board_prefixes,
        exchange_ids=policy.allowed_exchange_ids,
        venue_rules=policy.venue_rules,
        eligibility_rule=policy.eligibility_rule,
        calendar_exchange_id=policy.calendar_exchange_id,
        nominal_kline_source=policy.nominal_kline_source,
        st_membership_source=policy.st_membership_source,
        trading_calendar_source=policy.trading_calendar_source,
        excluded_boards=policy.excluded_boards,
        limit_up_pct=policy.limit_up_pct,
    )
    if policy.config_hash != expected:
        raise UniverseDataError("formal universe policy semantic hash mismatch")
    return policy


UNIVERSE_POLICY = load_universe_policy()
CURRENT_ENUMERATION_POLICY = load_current_enumeration_policy()
ACTIVE_A_SHARE_PREFIXES: tuple[str, ...] = UNIVERSE_POLICY.allowed_board_prefixes
ST_NAME_PREFIXES: tuple[str, ...] = CURRENT_ENUMERATION_POLICY.st_name_patterns
NO_RECENT_KLINE_DAYS: int = CURRENT_ENUMERATION_POLICY.no_recent_kline_days


def is_active_a_share(stock_code: str) -> bool:
    """Stock code 是否属于活跃 A 股个人散户 universe (60/00/30/68 前缀).

    Note: 不查交易状态; 调用方必须用 K 线 truth source 检查近期有交易.
    本函数只看前缀.
    """
    if not stock_code or len(stock_code) < 2:
        return False
    return stock_code[:2] in ACTIVE_A_SHARE_PREFIXES


def is_st_stock(stock_name: str) -> bool:
    """Check if stock_name indicates ST/*ST status.

    2026-05-22 audit: V4 top-10 picks 中 19.31% 是 ST/*ST (834/4320).
    """
    if not stock_name:
        return False
    return any(stock_name.startswith(p) for p in ST_NAME_PREFIXES)


def filter_active_a_share(stock_codes) -> list[str]:
    """过滤 stock_code 列表, 只留活跃 A 股 universe (前缀过滤, 不查 delisted)."""
    return [c for c in stock_codes if is_active_a_share(c)]


def sql_where_active_a_share(column: str = "stock_code") -> str:
    """生成 SQL WHERE 子句 (前缀过滤). 调用方可叠加 delisted 过滤.

    Example:
        sql = f"SELECT * FROM xxx WHERE {sql_where_active_a_share()}"
        # 输出: WHERE SUBSTR(stock_code, 1, 2) IN ('60','00','30','68')
    """
    prefixes = ",".join(f"'{p}'" for p in ACTIVE_A_SHARE_PREFIXES)
    return f"SUBSTR({column}, 1, 2) IN ({prefixes})"


def _sql_like_any_prefix(column: str, prefixes: tuple[str, ...]) -> str:
    if not prefixes:
        return "FALSE"
    likes = []
    for prefix in prefixes:
        escaped = prefix.replace("'", "''")
        likes.append(f"{column} LIKE '{escaped}%'")
    return "(" + " OR ".join(likes) + ")"


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone() is not None


def sql_where_no_st(stock_name_column: str = "stock_name") -> str:
    """SQL WHERE 子句排除 ST/*ST stock names.

    Example:
        sql = f"... LEFT JOIN dim_active_a_stock d ON ... WHERE {sql_where_no_st('d.stock_name')}"
        # 输出: (d.stock_name IS NULL OR NOT (...configured ST patterns...))
    """
    return f"({stock_name_column} IS NULL OR NOT {_sql_like_any_prefix(stock_name_column, ST_NAME_PREFIXES)})"


# === 2026-05-23 SINGLE SOURCE OF TRUTH for batch task universe ===
# 用户 push '做一个专用的工具'. 所有 batch tasks 必须 调用 get_active_universe().

def get_active_universe(
    conn=None,
    *,
    include_st: bool = True,
    market_conn=None,
) -> set[str]:
    """K 线有交易 + 前缀白名单 = 活跃沪深A universe（默认含 ST/*ST）.

    ``include_st=False`` 是策略侧可选收窄（涨跌停规则不同），不是产品白名单真相。
    正式 PIT 人口见 ``resolve_traded_on_observation_date``（亦不按 ST denylist）。
    """
    mkt = market_conn
    should_close = False
    if mkt is None:
        try:
            from services.market_db import get_market_conn
            mkt = get_market_conn()
            should_close = True
        except Exception as exc:
            raise UniverseDataError("K-line market DB is required for active universe truth") from exc

    try:
        from services.market_read import get_analysis_kline_qfq_relation
        kline_relation = get_analysis_kline_qfq_relation()
        no_trade_days = int(NO_RECENT_KLINE_DAYS)
        codes = {r[0] for r in mkt.execute(
            f"SELECT DISTINCT code FROM {kline_relation} "
            "WHERE freq='daily' "
            f"AND CAST(date AS DATE) >= CURRENT_DATE - INTERVAL '{no_trade_days} days'"  # rule-compliance: ok evidence=活跃liveness粗启发(近N日历日有K线=在交易), 日历天足够判退市/长停, 非PIT决策锚
        ).fetchall()}
    finally:
        if should_close:
            mkt.close()

    stocks = {c for c in codes if len(c) >= 2 and c[:2] in ACTIVE_A_SHARE_PREFIXES}

    # 2026-06-19 身份真相源交集: 只留 dim_active_a_stock (tushare stock_basic 真股清单) 内的码,
    #   剔除 K线里的指数 benchmark (沪深300=000300 等与 00 前缀共号段者直读 K线漏入 universe)。
    #   前缀仍作 defense-in-depth 预筛; conn=None 回退纯前缀。
    # §9 拆库: identity 读 reference dim (security_master active_codes, auto-fallback);
    #   conn 守卫语义保留 (conn is not None 才 intersect; conn=None legacy 纯前缀)。
    if conn is not None:
        from services.security_master import active_codes
        identity = active_codes(conn)
        if identity:
            stocks &= identity

    if not include_st:
        if conn is None:
            raise UniverseDataError("smart DB connection is required for ST name mapping")
        from services.security_master import active_stock_name_map
        name_map = active_stock_name_map(conn=conn)
        if not name_map:
            raise UniverseDataError("dim_active_a_stock (reference) is required for ST name mapping")
        st_codes = {c for c, n in name_map.items() if is_st_stock(n)}
        stocks -= st_codes

    return stocks


_LIMIT_PCT_MAP = dict(UNIVERSE_POLICY.limit_up_pct)


def get_limit_up_pct(stock_code: str) -> float:
    """按板块返回涨停幅度. 从 universe_rules.yaml 读取."""
    if not stock_code or len(stock_code) < 2:
        return 0.10
    prefix = stock_code[:2]
    return float(_LIMIT_PCT_MAP.get(prefix, 0.10))


def build_limit_up_pct_map(stock_codes) -> dict[str, float]:
    """批量构建 {stock_code: limit_up_pct} 映射, 避免逐只查询."""
    return {code: get_limit_up_pct(code) for code in stock_codes}


# audit_strategy_universe_contamination() 2026-07-07 整段退役 (git log --grep dim_all_ever_listed
# 决策收口): 审计"策略预测表"(strategy predictions table)是否混入排除股, 但策略/serving/scoring 层已
# 于 2026-06-28 纯数据平台重建整体退役, 项目里已不存在这类预测表可审; 生产 0 调用方(仅
# backend/tests/test_universe.py 测试自身), 且其退市码集来源 dim_all_ever_listed 本身也已物删。

# =====================================================================
# 硬真相源门 (2026-06-17 用户决议: universe 升到交易日历级)
# 排除列表里的股票永不进任何验证/回测/GT/选股。任何最终股票集必过
# assert_universe_clean()。前缀级判定 (无 DB, 快), 报错带板块归类。
# =====================================================================

_EXCLUDED_BOARDS: dict[str, str] = dict(UNIVERSE_POLICY.excluded_boards)


def classify_exclusion(stock_code: str) -> str | None:
    """返回排除原因 (板块名); 若在白名单内返回 None.

    白名单 = include.board_prefixes (60/00/30/68)。补集按 excluded_boards
    taxonomy 归类 (北交所/三板/ETF), 兜底 '非白名单(前缀)'。
    """
    if not stock_code or len(stock_code) < 2:
        return "代码畸形"
    p2 = stock_code[:2]
    if p2 in ACTIVE_A_SHARE_PREFIXES:
        return None
    # 先查 2 位, 再查 1 位 taxonomy
    if p2 in _EXCLUDED_BOARDS:
        return _EXCLUDED_BOARDS[p2]
    if stock_code[:1] in _EXCLUDED_BOARDS:
        return _EXCLUDED_BOARDS[stock_code[:1]]
    return f"非白名单({p2}x)"


def assert_universe_clean(stock_codes, *, context: str = "") -> bool:
    """硬门: 若 stock_codes 含任何排除股, raise UniverseContaminationError.

    交易日历级真相源 — 就像非交易日不能下单, 排除股不能进 universe。
    GT/回测/实验/选股的最终股票集必调本函数。前缀级, 无 DB。
    """
    bad: dict[str, list[str]] = {}
    for code in stock_codes:
        reason = classify_exclusion(code)
        if reason is not None:
            bad.setdefault(reason, []).append(str(code))
    if bad:
        n_bad = sum(len(v) for v in bad.values())
        parts = "; ".join(f"{r}: {len(v)}只(如{v[:3]})" for r, v in sorted(bad.items()))
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe 污染{ctx}: {n_bad} 只排除股混入 — {parts}. "
            f"修: 股票集先过 services.universe.assert_universe_clean / get_active_universe."
        )
    return True


def assert_project_exchange_ids_allowed(
    exchange_ids,
    *,
    policy: UniversePolicy,
    context: str = "",
) -> bool:
    """Static venue sub-gate for project-universe output, not raw/external data."""

    try:
        verify_universe_policy(policy)
    except (AttributeError, UniverseDataError) as exc:
        raise UniverseContaminationError(
            "project exchange gate requires an explicit factory-owned UniversePolicy snapshot"
        ) from exc

    if isinstance(exchange_ids, (str, bytes)):
        malformed = repr(exchange_ids)
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange gate{ctx}: expected a non-empty collection, got {malformed}"
        )
    try:
        values = tuple(exchange_ids)
    except TypeError as exc:
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange gate{ctx}: exchange_ids is not iterable"
        ) from exc
    if not values:
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange gate{ctx}: exchange_ids must be non-empty"
        )

    malformed = [
        value
        for value in values
        if not isinstance(value, str) or _EXCHANGE_ID.fullmatch(value) is None
    ]
    if malformed:
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange gate{ctx}: malformed exchange IDs {malformed!r}"
        )
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange gate{ctx}: duplicate exchange IDs {duplicates}"
        )

    allowed = frozenset(policy.allowed_exchange_ids)
    excluded = sorted(value for value in values if value not in allowed)
    if excluded:
        ctx = f" @ {context}" if context else ""
        raise UniverseContaminationError(
            f"universe exchange contamination: {excluded}{ctx}; allowed={sorted(allowed)}; "
            f"policy={policy.policy_id}@{policy.policy_version}"
        )
    return True


def load_st_calendar(raw_conn) -> dict[str, set[str]]:
    """PIT ST 日历: {code(6位): set(YYYYMMDD)} — 某股某日是否被 ST 标记的真相源.

    源: canonical_stock_st_daily (data_source.st_calendar)。旧源 raw_tushare_stock_st
    已冻结 (20260716 起无更新), calendar_identity_recon.py 已把它列入
    BANNED_ST_BASELINE, ACCEPTED_ST_TABLE 声明的是 canonical_stock_st_daily —— 本函数
    跟上 recon 层已经做出的裁决。用于历史 t 的 PIT ST 判定 (旧 dim_active_a_stock 只有
    当前名字, 非 PIT); PIT 判据仍用 trade_date (非 available_at) —— 是否切换到更严格的
    available_at 是另一个未做的决策, 不在本次改动范围内。单一计算点: GT/回测共用本函数,
    不各自内联 ST 查询。
    """
    if not _table_exists(raw_conn, "canonical_stock_st_daily"):
        raise UniverseDataError("canonical_stock_st_daily (PIT ST 真相源) 不存在")
    rows = raw_conn.execute(
        "SELECT DISTINCT SUBSTR(ts_code,1,6) AS code, strftime(trade_date, '%Y%m%d') AS d "
        "FROM canonical_stock_st_daily"
    ).fetchall()
    cal: dict[str, set[str]] = {}
    for code, d in rows:
        cal.setdefault(code, set()).add(d)
    return cal


def is_st_on(stock_code: str, yyyymmdd: str, st_calendar: dict[str, set[str]]) -> bool:
    """PIT: stock_code 在 yyyymmdd (无横杠) 当日是否 ST."""
    return yyyymmdd in st_calendar.get(stock_code, ())
