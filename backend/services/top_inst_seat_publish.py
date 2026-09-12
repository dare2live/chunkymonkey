"""B2 top_inst seat publication — fact_top_inst_seat_daily strangler.

Owns pulse/institution seat grain
(trade_date x ts_code x exalter x buy x sell x board_window x event_seq)
with typed available_at (trade_date 18:00 Asia/Shanghai, matching
sync_registry top_inst available_after) and lineage (source_table +
built_at).

Source = raw_tushare_top_inst (landing residual). Not a formal accept plane;
publication is derived/materialized so DataAccess can leave the raw leaf.
Does not invent DC membership PIT. Episode/mart aggregates are not this grain.

Grain contract (r2b, 业主批准 2026-09-11):
  D1 同股同日同席位买卖金额完全相同的多榜记录按一笔计 (匿名/机构专用同样处理, 注明可能少算)。
  D2 投资者类别行 (自然人/中小投资者/其他自然人/机构投资者/深股通投资者) 不计入日频指标
     (lhb_inst_net / c3_lhb), 但发布面全收并贴 seat_kind=investor_category 标签 —— 展示 != 指标。
  D3 日频指标只计单日榜 (board_window=single_day); 多日窗口榜金额仍发布, 只是被
     daily_metric_filter_sql() 挡在两个日频消费方之外。
  D4 (业主批准 2026-09-12): 日频指标只算项目股票池内的证券 (ts_code 前缀 60/00/30/68,
     见 services.universe.UNIVERSE_POLICY)。妙想重落全史后发布表出现旧源没有的可转债/
     北交所/B股证券, 正在灌进 lhb_inst_net 与 c3_lhb —— 这三类交易所确实披露了, 发布面
     仍全收 (事实层不删行), 只在指标口径层把它们挡在项目股票池之外。前缀白名单不
     hardcode 在本文件, 每次调用都读 services.universe 当前的 policy。
  过滤放发布层不放消费方: 「什么是单日榜/什么是类别行/什么是项目股票池」只在这里判一次
  (backend/config/lhb_board_class.yaml D1-D3, backend/config/universe_rules.yaml D4,
  红线 11 fail-closed); 消费方 (S6: market_pulse / institution_profile) import
  daily_metric_filter_sql(), 不各写一遍判断。

lhb_board_class.yaml version 2 (2026-09, r1 登记形态 (b), 见 scratchpad/lhb_reason_class_r1.md
§3.1): 582 个供货商理由串收成 119 个 YAML 键 (108 精确 + 11 模板)。归一规则不进 YAML (它是
比较逻辑不是数据), 是这里的 canonical_reason(): 把理由串里每个 [+-]?[0-9]+\\.[0-9]+ (只认半角
ASCII 数字) 替换成占位符 "{v}"; 全角数字/整数阈值/标点/全半角括号一律不归一 —— 它们是规则身份,
新阈值/新标点必须有人登记, 不会被静默吸收。YAML 的 reasons 键本身必须已经是规范形 (loader 拒绝
键内残留半角小数), 且键里出现的 "{"/"}" 只允许构成字面量 "{v}" (loader 拒绝任何其它占位符写法);
YAML 也拒绝重复键 (PyYAML SafeLoader 默认对重复 mapping 键静默取最后一个写入的值, 这里用
_UniqueKeySafeLoader 改成 fail-closed)。三条防线叠在一起保证「一个理由串在运行时只能命中 0 或 1
条登记, 不可能命中 2 条」: 查表本身是字典查找 (0 或 1 条), ≥2 条的可能性全部被推到加载时拒绝。
折叠 SQL / 四条交叉核 / 发布表的 reasons 列一律继续用原始理由字符串 JOIN —— 归一只发生在这一处
Python 代码里 (_register_reason_class / audit_unknown_reasons), 不在 DuckDB 里重复实现一遍。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect
from services.universe import sql_where_active_a_share

TABLE = "fact_top_inst_seat_daily"
SOURCE_TABLE = "raw_tushare_top_inst"
_SH = ZoneInfo("Asia/Shanghai")
_PUBLISH_AT = time(18, 0)  # sync_registry top_inst available_after

# Test hooks (production reads database_manifest via resolver.db_path).
RAW_DB: Path | None = None
SMARTMONEY_DB: Path | None = None

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_BOARD_CLASS_YAML = _BACKEND_DIR / "config" / "lhb_board_class.yaml"

# Two daily-metric consumers (market_pulse.lhb_inst, institution_profile.c3_lhb)
# share this predicate so "what counts as a daily seat event" is judged once,
# here, not re-derived per consumer (r2b §1 D2/D3; D4 业主 2026-09-12 批准).
_DAILY_METRIC_BOARD_SEAT_FILTER = (
    "board_window = 'single_day' AND seat_kind <> 'investor_category'"
)


def daily_metric_filter_sql(ts_code_column: str = "ts_code") -> str:
    """两个日频消费方共用的口径判据 (D2/D3/D4, 单一计算点, CLAUDE.md 规则 5)。

    D2/D3 不依赖列名, 直接嵌入; D4 (项目股票池) 依赖 ts_code 所在列名 —— 两个
    调用点里这一列都无歧义地可以不加表前缀 (market_pulse 的 CTE 单表 FROM 无
    别名; institution_profile 的 JOIN 里只有 top_inst 一张表有 ts_code 列),
    所以默认参数 "ts_code" 够用, 保留参数是为了不排除未来需要显式限定列名的调用点。

    前缀白名单不 hardcode 在这里: sql_where_active_a_share() 直接读
    services.universe.UNIVERSE_POLICY 派生出的 ACTIVE_A_SHARE_PREFIXES,
    是这份策略今天实际生效的取法 (universe.py 已有的单一计算点), 本函数只复用它,
    不重新拼一份 SUBSTR(...) IN (...)。ts_code 为 NULL 的行: SUBSTR(NULL,1,2)
    是 NULL, `NULL IN (...)` 求值为 NULL 非 TRUE, 在 WHERE/JOIN 条件下等价于假 ——
    缺失只能传播为缺失(红线 3), 不会被当成"在池内"。
    """
    return f"{_DAILY_METRIC_BOARD_SEAT_FILTER} AND {sql_where_active_a_share(ts_code_column)}"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    trade_date VARCHAR NOT NULL,
    ts_code VARCHAR NOT NULL,
    exalter VARCHAR NOT NULL,
    buy DOUBLE NOT NULL,
    sell DOUBLE NOT NULL,
    event_seq INTEGER NOT NULL,
    net_buy DOUBLE,
    sides VARCHAR NOT NULL,
    board_count INTEGER NOT NULL,
    reasons VARCHAR NOT NULL,
    board_window VARCHAR NOT NULL,
    seat_kind VARCHAR NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    source_table VARCHAR NOT NULL,
    built_at TIMESTAMPTZ NOT NULL
)
"""

_GRAIN_COLS = ["trade_date", "ts_code", "exalter", "buy", "sell", "board_window", "event_seq"]


# ---------------------------------------------------------------------------
# lhb_board_class.yaml — typed, fail-closed loader (红线 11)
# ---------------------------------------------------------------------------

_ALLOWED_TOP_KEYS = {"version", "windows", "seat_kinds", "checks", "reasons"}
_ALLOWED_SEAT_KIND_KEYS = {"investor_category", "anonymous_inst"}
_REQUIRED_CHECK_KEYS = {
    "single_day_statistics_days_must_be_null",
    "investor_category_names_only_on_category_boards",
    "category_boards_only_investor_category_names",
}

# Reason-string canonicalisation (lhb_reason_class_r1.md §3.1). Only ASCII
# halfwidth decimals are absorbed -- fullwidth digit variants (e.g. "３０.４７")
# are left untouched and read as an unknown reason (fail-closed by design:
# the vendor has 0 historical rows using fullwidth digits, so seeing one is
# itself a signal worth a human look, not silently swallowing it).
_DECIMAL_RE = re.compile(r"[+-]?[0-9]+\.[0-9]+")
REASON_PLACEHOLDER = "{v}"


def canonical_reason(s: str) -> str:
    """Replace every ASCII halfwidth decimal number in ``s`` with the
    ``{v}`` placeholder. Integer thresholds, punctuation, and full/halfwidth
    brackets are never touched -- they are part of a reason's identity, not
    an observed value (lhb_reason_class_r1.md §3.1)."""
    return _DECIMAL_RE.sub(REASON_PLACEHOLDER, s)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """yaml.SafeLoader that raises ValueError on any duplicate mapping key.

    PyYAML's default SafeLoader silently keeps the *last* value for a
    repeated key (measured, PyYAML 6.0.3: ``{k: a, k: b}`` -> ``{k: b}``).
    lhb_board_class.yaml's ``reasons`` map depends on "at most one
    registration per canonical reason" (r1.md §3.2) -- a duplicate key must
    fail loudly at load time instead of silently dropping a registration.
    """

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise ValueError(
                    f"duplicate key {key!r} in YAML mapping "
                    f"(line {key_node.start_mark.line + 1})"
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load_yaml_reject_duplicate_keys(path: Path) -> Any:
    """``yaml.safe_load`` equivalent that rejects duplicate mapping keys
    (see ``_UniqueKeySafeLoader``) instead of silently keeping the last one.
    """
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)


@dataclass(frozen=True)
class BoardClass:
    windows: tuple[str, ...]
    reasons: Mapping[str, tuple[str, bool]]  # reason -> (window, investor_category_board)
    investor_category_names: frozenset[str]
    anonymous_inst_names: frozenset[str]
    checks: Mapping[str, bool]


def load_board_class(path: Path | None = None) -> BoardClass:
    """Load + validate backend/config/lhb_board_class.yaml. Fail-closed (红线 11):

    unknown top-level keys, an unlisted window value, a non-bool
    investor_category_board, a seat_kinds key outside {investor_category,
    anonymous_inst}, empty/cross-kind-duplicate names, or a missing checks
    key all raise ValueError rather than silently defaulting. Version 2
    (r1.md §3.2) adds three more fail-closed checks on every ``reasons``
    key, all raising ValueError: (1) a duplicate key anywhere in the YAML
    file (caught by ``_load_yaml_reject_duplicate_keys``, not PyYAML's
    default silent last-write-wins); (2) a key whose ``canonical_reason()``
    is not itself -- i.e. a key that still contains a raw ASCII decimal
    observation instead of the registered template; (3) a key containing a
    "{"/"}" that does not form the literal placeholder "{v}". Together these
    make "a raw reason string matches at most one registered key" true by
    construction, not by convention.
    """
    p = Path(path) if path is not None else _DEFAULT_BOARD_CLASS_YAML
    doc = _load_yaml_reject_duplicate_keys(p)
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: root must be a mapping, got {type(doc).__name__}")

    unknown_top = set(doc.keys()) - _ALLOWED_TOP_KEYS
    if unknown_top:
        raise ValueError(f"{p}: unknown top-level keys {sorted(unknown_top)}")

    windows_raw = doc.get("windows")
    if (
        not isinstance(windows_raw, list)
        or not windows_raw
        or not all(isinstance(w, str) and w for w in windows_raw)
    ):
        raise ValueError(f"{p}: windows must be a non-empty list of non-empty strings")
    windows = tuple(windows_raw)
    windows_set = set(windows)

    seat_kinds_raw = doc.get("seat_kinds")
    if not isinstance(seat_kinds_raw, dict) or not seat_kinds_raw:
        raise ValueError(f"{p}: seat_kinds must be a non-empty mapping")
    unknown_kinds = set(seat_kinds_raw.keys()) - _ALLOWED_SEAT_KIND_KEYS
    if unknown_kinds:
        raise ValueError(f"{p}: seat_kinds has unknown keys {sorted(unknown_kinds)}")

    names_by_kind: dict[str, frozenset[str]] = {}
    owner_of_name: dict[str, str] = {}
    for kind, body in seat_kinds_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"{p}: seat_kinds.{kind} must be a mapping")
        names = body.get("names")
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(n, str) and n for n in names)
        ):
            raise ValueError(
                f"{p}: seat_kinds.{kind}.names must be a non-empty list of non-empty strings"
            )
        unknown_body_keys = set(body.keys()) - {"names"}
        if unknown_body_keys:
            raise ValueError(f"{p}: seat_kinds.{kind} has unknown keys {sorted(unknown_body_keys)}")
        for n in names:
            other = owner_of_name.get(n)
            if other is not None and other != kind:
                raise ValueError(
                    f"{p}: name {n!r} appears in both seat_kinds.{other} and seat_kinds.{kind}"
                )
            owner_of_name[n] = kind
        names_by_kind[kind] = frozenset(names)

    investor_category_names = names_by_kind.get("investor_category", frozenset())
    anonymous_inst_names = names_by_kind.get("anonymous_inst", frozenset())

    checks_raw = doc.get("checks")
    if not isinstance(checks_raw, dict):
        raise ValueError(f"{p}: checks must be a mapping")
    missing_checks = _REQUIRED_CHECK_KEYS - set(checks_raw.keys())
    if missing_checks:
        raise ValueError(f"{p}: checks missing keys {sorted(missing_checks)}")
    unknown_checks = set(checks_raw.keys()) - _REQUIRED_CHECK_KEYS
    if unknown_checks:
        raise ValueError(f"{p}: checks has unknown keys {sorted(unknown_checks)}")
    checks: dict[str, bool] = {}
    for key in _REQUIRED_CHECK_KEYS:
        value = checks_raw[key]
        if not isinstance(value, bool):
            raise ValueError(f"{p}: checks.{key} must be a bool, got {type(value).__name__}")
        checks[key] = value

    reasons_raw = doc.get("reasons")
    if not isinstance(reasons_raw, dict) or not reasons_raw:
        raise ValueError(f"{p}: reasons must be a non-empty mapping")
    reasons: dict[str, tuple[str, bool]] = {}
    for reason, body in reasons_raw.items():
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"{p}: reasons keys must be non-empty strings, got {reason!r}")
        canon_key = canonical_reason(reason)
        if canon_key != reason:
            raise ValueError(
                f"{p}: reasons key {reason!r} is not canonical (canonical form is "
                f"{canon_key!r}); register the template, not a raw observed instance "
                "(r1.md §3.2)"
            )
        placeholder_stripped = reason.replace(REASON_PLACEHOLDER, "")
        if "{" in placeholder_stripped or "}" in placeholder_stripped:
            raise ValueError(
                f"{p}: reasons key {reason!r} uses an illegal placeholder; "
                f"only {REASON_PLACEHOLDER!r} is allowed"
            )
        if not isinstance(body, dict):
            raise ValueError(f"{p}: reasons[{reason!r}] must be a mapping")
        unknown_reason_keys = set(body.keys()) - {"window", "investor_category_board"}
        if unknown_reason_keys:
            raise ValueError(
                f"{p}: reasons[{reason!r}] has unknown keys {sorted(unknown_reason_keys)}"
            )
        window = body.get("window")
        if window not in windows_set:
            raise ValueError(
                f"{p}: reasons[{reason!r}].window {window!r} not in windows {windows}"
            )
        cat_flag = body.get("investor_category_board", False)
        if not isinstance(cat_flag, bool):
            raise ValueError(
                f"{p}: reasons[{reason!r}].investor_category_board must be a bool"
            )
        reasons[reason] = (window, cat_flag)

    return BoardClass(
        windows=windows,
        reasons=reasons,
        investor_category_names=investor_category_names,
        anonymous_inst_names=anonymous_inst_names,
        checks=checks,
    )


def audit_unknown_reasons(
    conn, *, table: str = "raw_tushare_top_inst"
) -> list[tuple[str, int]]:
    """Read-only scan of ``table`` for ``reason`` strings whose
    ``canonical_reason()`` is not registered in lhb_board_class.yaml.
    Returns (raw_reason, row_count) sorted by count desc (ties broken by
    raw_reason asc). Empty list = everything known.

    Normalisation happens in Python, one reason string at a time, after a
    plain ``GROUP BY`` fetches the distinct raw strings -- not as a SQL
    ``NOT IN (?...)`` against the raw (un-normalised) registered keys, since
    a raw row's decimal observation would never literally match a
    registered template (r1.md §3.3).

    Meant to run once after a full relanding of top_inst and before a full
    rebuild of fact_top_inst_seat_daily, so new vendor strings get a one-shot
    verdict into the YAML rather than tripping the fail-closed publisher
    mid-rebuild (r2b §2.4).
    """
    bc = load_board_class()
    rows = conn.execute(
        f"""
        SELECT reason, COUNT(*) AS c
        FROM {table}
        WHERE reason IS NOT NULL
        GROUP BY reason
        """
    ).fetchall()
    unknown = [
        (str(reason), int(count))
        for reason, count in rows
        if canonical_reason(str(reason)) not in bc.reasons
    ]
    unknown.sort(key=lambda t: (-t[1], t[0]))
    return unknown


def audit_unknown_reasons_from_raw(
    *, table: str = "raw_tushare_top_inst"
) -> list[tuple[str, int]]:
    """Open the raw db read-only and delegate to :func:`audit_unknown_reasons`.

    The connection lives here rather than in the CLI script because the
    SERVE read-layer door D1 only lets registered data-module members hold
    an inline connection (``data_module_members.yaml``); the script is a
    thin argv/printing shell over this function. Raises
    ``FileNotFoundError`` when the raw db is absent -- an audit that cannot
    read raw must not look like "nothing unknown".
    """
    raw_path = _raw_db_path()
    if not raw_path.is_file():
        raise FileNotFoundError(f"missing raw db: {raw_path}")
    con = duck_connect(str(raw_path), read_only=True)
    try:
        return audit_unknown_reasons(con, table=table)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def top_inst_seat_available_at(trade_date: str) -> datetime:
    """Consumer publication clock for one top_inst seat partition day."""
    day = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    if len(day) != 8:
        raise ValueError(f"trade_date must be YYYYMMDD; got {trade_date!r}")
    d = datetime.strptime(day, "%Y%m%d").date()
    return datetime.combine(d, _PUBLISH_AT, tzinfo=_SH)


def ensure_schema(conn) -> None:
    conn.execute(DDL)
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_{TABLE}_grain
        ON {TABLE} ({", ".join(_GRAIN_COLS)})
        """
    )


def _raw_db_path() -> Path:
    return Path(RAW_DB) if RAW_DB is not None else Path(resolver.db_path("tushare_raw"))


def _smartmoney_db_path() -> Path:
    return (
        Path(SMARTMONEY_DB)
        if SMARTMONEY_DB is not None
        else Path(resolver.db_path("smartmoney"))
    )


def _compact(value: str) -> str:
    day = "".join(ch for ch in str(value) if ch.isdigit())[:8]
    if len(day) != 8:
        raise ValueError(f"expected YYYYMMDD; got {value!r}")
    datetime.strptime(day, "%Y%m%d")  # validate
    return day


def _register_reason_class(
    con, bc: BoardClass, where: str, params: list[Any]
) -> list[tuple[str, str, int]]:
    """Materialize lhb_board_class.yaml's reasons map, keyed by the RAW
    reason string observed under ``where``, as a temp table so the
    fold/classification SQL can JOIN on ``r.reason = c.reason`` without
    re-deriving canonicalisation in SQL (r1.md §3.3): canonicalisation
    happens exactly once, here, in Python.

    Reads the distinct (reason, COUNT(*)) pairs under ``where``, then for
    each one looks up ``canonical_reason(reason)`` in ``bc.reasons``. Known
    raw reasons are inserted into the temp table keyed by their raw string
    (so downstream exact-string JOINs are unaffected by normalisation).
    Unknown ones are returned as (raw_reason, canonical_reason, row_count)
    triples, sorted by row_count desc then raw_reason asc -- an empty
    return means every distinct raw reason under ``where`` is known.
    """
    con.execute("DROP TABLE IF EXISTS temp.lhb_reason_class")
    con.execute(
        """
        CREATE TEMP TABLE lhb_reason_class (
            reason VARCHAR PRIMARY KEY,
            board_window VARCHAR NOT NULL,
            investor_category_board BOOLEAN NOT NULL
        )
        """
    )
    distinct_rows = con.execute(
        f"""
        SELECT reason, COUNT(*) AS c
        FROM tr.{SOURCE_TABLE} r
        WHERE {where} AND r.reason IS NOT NULL
        GROUP BY reason
        """,
        params,
    ).fetchall()

    known_rows: list[tuple[str, str, bool]] = []
    unknown: list[tuple[str, str, int]] = []
    for raw_reason, count in distinct_rows:
        raw_reason = str(raw_reason)
        canon = canonical_reason(raw_reason)
        cls = bc.reasons.get(canon)
        if cls is None:
            unknown.append((raw_reason, canon, int(count)))
        else:
            window, cat = cls
            known_rows.append((raw_reason, window, cat))

    if known_rows:
        con.executemany(
            "INSERT INTO lhb_reason_class VALUES (?, ?, ?)",
            known_rows,
        )

    unknown.sort(key=lambda t: (-t[2], t[0]))
    return unknown


def _assert_board_rank_present(con, where: str, params: list[Any]) -> None:
    bad = con.execute(
        f"SELECT COUNT(*) FROM tr.{SOURCE_TABLE} r WHERE {where} AND r.board_rank IS NULL",
        params,
    ).fetchone()[0]
    if bad:
        raise ValueError(
            f"raw 含 board_rank NULL 行 (旧契约), 先重落 ({int(bad)} 行, source={SOURCE_TABLE})"
        )


def _classify_and_assert(con, where: str, params: list[Any], bc: BoardClass) -> None:
    """Register lhb_reason_class (from YAML, via canonical_reason) then run
    the four fail-closed cross-checks (r2b §2.2/§2.3) against the current
    window's raw rows. Raises ValueError with a message naming the violated
    check.
    """
    unknown = _register_reason_class(con, bc, where, params)

    null_reason = con.execute(
        f"SELECT COUNT(*) FROM tr.{SOURCE_TABLE} r WHERE {where} AND r.reason IS NULL",
        params,
    ).fetchone()[0]
    if null_reason:
        raise ValueError(
            f"{int(null_reason)} 行 reason 为空 (NULL), fail-closed —— 分类判据必须能读到理由字符串"
        )

    if unknown:
        detail = ", ".join(
            f"{raw!r} -> {canon!r}x{count}" for raw, canon, count in unknown
        )
        raise ValueError(
            f"{SOURCE_TABLE} 含未在 lhb_board_class.yaml 登记的理由 (fail-closed, 红线 11): {detail}"
        )

    if bc.checks.get("single_day_statistics_days_must_be_null"):
        bad = con.execute(
            f"""
            SELECT COUNT(*)
            FROM tr.{SOURCE_TABLE} r
            JOIN lhb_reason_class c ON c.reason = r.reason
            WHERE {where} AND c.board_window = 'single_day' AND r.stat_days IS NOT NULL
            """,
            params,
        ).fetchone()[0]
        if bad:
            raise ValueError(
                f"{int(bad)} 行单日榜 (board_window=single_day) 的 stat_days 非空, "
                "违反 lhb_board_class.yaml checks.single_day_statistics_days_must_be_null"
            )

    if bc.checks.get("investor_category_names_only_on_category_boards"):
        names = list(bc.investor_category_names)
        names_ph = ",".join(["?"] * len(names)) if names else "NULL"
        bad = con.execute(
            f"""
            SELECT COUNT(*)
            FROM tr.{SOURCE_TABLE} r
            JOIN lhb_reason_class c ON c.reason = r.reason
            WHERE {where} AND r.exalter IN ({names_ph}) AND c.investor_category_board = FALSE
            """,
            [*params, *names],
        ).fetchone()[0]
        if bad:
            raise ValueError(
                f"{int(bad)} 行 investor_category 名单里的 exalter 出现在非类别榜 "
                "(investor_category_board=false), 违反 lhb_board_class.yaml checks."
                "investor_category_names_only_on_category_boards"
            )

    if bc.checks.get("category_boards_only_investor_category_names"):
        names = list(bc.investor_category_names)
        names_ph = ",".join(["?"] * len(names)) if names else "NULL"
        bad = con.execute(
            f"""
            SELECT COUNT(*)
            FROM tr.{SOURCE_TABLE} r
            JOIN lhb_reason_class c ON c.reason = r.reason
            WHERE {where} AND c.investor_category_board = TRUE AND r.exalter NOT IN ({names_ph})
            """,
            [*params, *names],
        ).fetchone()[0]
        if bad:
            raise ValueError(
                f"{int(bad)} 行类别榜 (investor_category_board=true, category_board) 含非 "
                "investor_category 名单的 exalter, 违反 lhb_board_class.yaml checks."
                "category_boards_only_investor_category_names"
            )


def _fold_cte_sql(where: str) -> str:
    """Shared WITH-clause body for both the net-consistency check and the
    final INSERT SELECT. Requires lhb_reason_class to already be registered
    on ``con`` (see _register_reason_class).

    Fold key (r2b §1.4 rule 2, grain 七列): (trade_date, ts_code, exalter,
    buy, sell, board_window, event_seq). event_seq is assigned per (trade_date,
    ts_code, reason, side, exalter, buy, sell) partition ordered by
    board_rank (rule 1) -- board_window is a pure function of reason so it
    does not change that partitioning, but it IS part of the final fold key
    so a single-day and a multi-day board with identical (exalter, buy, sell)
    stay two rows (P3 isolation).
    """
    return f"""
    r AS (
        SELECT r.trade_date, r.ts_code, r.reason, r.side, r.board_rank,
               r.exalter, r.buy, r.sell, r.net_buy
        FROM tr.{SOURCE_TABLE} r
        WHERE {where}
          AND r.ts_code IS NOT NULL AND r.trade_date IS NOT NULL
          AND r.exalter IS NOT NULL AND r.side IS NOT NULL AND r.reason IS NOT NULL
    ),
    w AS (
        SELECT r.*, c.board_window,
               ROW_NUMBER() OVER (
                   PARTITION BY r.trade_date, r.ts_code, r.reason, r.side,
                                r.exalter, r.buy, r.sell
                   ORDER BY r.board_rank
               ) AS event_seq
        FROM r
        JOIN lhb_reason_class c ON c.reason = r.reason
    ),
    side_distinct AS (
        SELECT DISTINCT trade_date, ts_code, exalter, buy, sell, board_window,
                         event_seq, side
        FROM w
    ),
    sides_agg AS (
        SELECT trade_date, ts_code, exalter, buy, sell, board_window, event_seq,
               STRING_AGG(side, ',' ORDER BY side) AS sides
        FROM side_distinct
        GROUP BY 1, 2, 3, 4, 5, 6, 7
    ),
    reason_distinct AS (
        SELECT DISTINCT trade_date, ts_code, exalter, buy, sell, board_window,
                         event_seq, reason
        FROM w
    ),
    reason_agg AS (
        SELECT trade_date, ts_code, exalter, buy, sell, board_window, event_seq,
               STRING_AGG(reason, '|' ORDER BY reason) AS reasons,
               COUNT(*) AS board_count
        FROM reason_distinct
        GROUP BY 1, 2, 3, 4, 5, 6, 7
    ),
    net_agg AS (
        SELECT trade_date, ts_code, exalter, buy, sell, board_window, event_seq,
               MIN(net_buy) AS net_min, MAX(net_buy) AS net_max
        FROM w
        GROUP BY 1, 2, 3, 4, 5, 6, 7
    )
    """


def _assert_net_consistency(con, where: str, params: list[Any]) -> None:
    """Fold groups (grain 七列) must agree on net_buy (r2b §1.4 rule 3)."""
    sql = f"""
    WITH {_fold_cte_sql(where)}
    SELECT COUNT(*) FROM net_agg WHERE net_min IS DISTINCT FROM net_max
    """
    bad = con.execute(sql, params).fetchone()[0]
    if bad:
        raise ValueError(
            f"{int(bad)} 个折叠组 (grain 七列相同) 内 net_buy 不一致 (MIN<>MAX), fail-closed"
        )


def _seat_kind_case_sql(bc: BoardClass, *, column: str) -> tuple[str, list[Any]]:
    inv = list(bc.investor_category_names)
    anon = list(bc.anonymous_inst_names)
    inv_ph = ",".join(["?"] * len(inv)) if inv else "NULL"
    anon_ph = ",".join(["?"] * len(anon)) if anon else "NULL"
    sql = (
        "CASE "
        f"WHEN {column} IN ({inv_ph}) THEN 'investor_category' "
        f"WHEN {column} IN ({anon_ph}) THEN 'anonymous_inst' "
        "ELSE 'seat' END"
    )
    return sql, [*inv, *anon]


def publish_fact_top_inst_seat_daily(
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Materialize seat-event LHB rows from landing raw into smartmoney.

    When start/end are set, replace only that closed partition window.
    When omitted, full rebuild (DROP+CREATE) from all raw rows.

    Fail-closed before any INSERT (r2b §1.4/§2.2/§2.3): unknown reason
    string, a single-day-board row with non-null stat_days, an
    investor_category name outside a category board, a category board
    containing a non-category name, a raw row with board_rank still NULL
    (pre-relanding contract), or a fold group whose net_buy does not agree.
    """
    start_d = _compact(start) if start else None
    end_d = _compact(end) if end else None
    if (start_d is None) ^ (end_d is None):
        raise ValueError("start and end must both be set or both omitted")
    if start_d and end_d and start_d > end_d:
        raise ValueError(f"start {start_d} > end {end_d}")

    bc = load_board_class()

    raw_path = _raw_db_path().resolve()
    sm_path = _smartmoney_db_path().resolve()
    if not raw_path.is_file():
        raise FileNotFoundError(f"missing raw db: {raw_path}")
    sm_path.parent.mkdir(parents=True, exist_ok=True)

    built_at = datetime.now(tz=_SH)
    con = duck_connect(str(sm_path), read_only=False)
    try:
        raw_esc = str(raw_path).replace("'", "''")
        con.execute(f"ATTACH '{raw_esc}' AS tr (READ_ONLY)")
        if start_d is None:
            where = "TRUE"
            params: list[Any] = []
        else:
            where = "trade_date >= ? AND trade_date <= ?"
            params = [start_d, end_d]

        _assert_board_rank_present(con, where, params)
        _classify_and_assert(con, where, params, bc)
        _assert_net_consistency(con, where, params)

        if start_d is None:
            con.execute(f"DROP TABLE IF EXISTS {TABLE}")
            ensure_schema(con)
        else:
            ensure_schema(con)
            con.execute(
                f"DELETE FROM {TABLE} WHERE trade_date >= ? AND trade_date <= ?",
                [start_d, end_d],
            )

        seat_kind_sql, seat_kind_params = _seat_kind_case_sql(bc, column="n.exalter")

        con.execute(
            f"""
            INSERT INTO {TABLE} (
                trade_date, ts_code, exalter, buy, sell, event_seq, net_buy,
                sides, board_count, reasons, board_window, seat_kind,
                available_at, source_table, built_at
            )
            WITH {_fold_cte_sql(where)}
            SELECT
                n.trade_date, n.ts_code, n.exalter, n.buy, n.sell, n.event_seq,
                n.net_min AS net_buy,
                s.sides, ra.board_count, ra.reasons, n.board_window,
                {seat_kind_sql} AS seat_kind,
                timezone(
                    'Asia/Shanghai',
                    CAST(strptime(n.trade_date, '%Y%m%d') AS TIMESTAMP)
                    + INTERVAL 18 HOUR
                ) AS available_at,
                ? AS source_table,
                ? AS built_at
            FROM net_agg n
            JOIN sides_agg s USING (trade_date, ts_code, exalter, buy, sell, board_window, event_seq)
            JOIN reason_agg ra USING (trade_date, ts_code, exalter, buy, sell, board_window, event_seq)
            """,
            [*params, *seat_kind_params, SOURCE_TABLE, built_at],
        )
        rows = con.execute(
            f"SELECT COUNT(*) FROM {TABLE}"
            + (" WHERE trade_date >= ? AND trade_date <= ?" if start_d else ""),
            [start_d, end_d] if start_d else [],
        ).fetchone()[0]
        return {
            "table": TABLE,
            "source_table": SOURCE_TABLE,
            "rows": int(rows),
            "start": start_d,
            "end": end_d,
            "built_at": built_at.isoformat(),
            "grain": list(_GRAIN_COLS),
            "mode": "window" if start_d else "full_rebuild",
        }
    finally:
        con.close()


__all__ = [
    "TABLE",
    "SOURCE_TABLE",
    "daily_metric_filter_sql",
    "BoardClass",
    "canonical_reason",
    "load_board_class",
    "audit_unknown_reasons",
    "top_inst_seat_available_at",
    "ensure_schema",
    "publish_fact_top_inst_seat_daily",
]
