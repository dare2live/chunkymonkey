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
     DAILY_METRIC_FILTER_SQL 挡在两个日频消费方之外。
  过滤放发布层不放消费方: 「什么是单日榜/什么是类别行」只在这里判一次 (backend/config/
  lhb_board_class.yaml, 红线 11 fail-closed); 消费方 (S6: market_pulse / institution_profile)
  import DAILY_METRIC_FILTER_SQL, 不各写一遍判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import yaml

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect

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
# here, not re-derived per consumer (r2b §1 D2/D3).
DAILY_METRIC_FILTER_SQL = "board_window = 'single_day' AND seat_kind <> 'investor_category'"

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
    key all raise ValueError rather than silently defaulting.
    """
    p = Path(path) if path is not None else _DEFAULT_BOARD_CLASS_YAML
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
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
    """Read-only scan of ``table`` for ``reason`` strings not registered in
    lhb_board_class.yaml. Returns (reason, row_count) sorted by count desc
    (ties broken by reason). Empty list = everything known.

    Meant to run once after a full relanding of top_inst and before a full
    rebuild of fact_top_inst_seat_daily, so new vendor strings get a one-shot
    verdict into the YAML rather than tripping the fail-closed publisher
    mid-rebuild (r2b §2.4).
    """
    bc = load_board_class()
    known = list(bc.reasons.keys())
    placeholders = ",".join(["?"] * len(known)) if known else "NULL"
    rows = conn.execute(
        f"""
        SELECT reason, COUNT(*) AS c
        FROM {table}
        WHERE reason IS NOT NULL AND reason NOT IN ({placeholders})
        GROUP BY reason
        ORDER BY c DESC, reason
        """,
        known,
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows]


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


def _register_reason_class(con, bc: BoardClass) -> None:
    """Materialize lhb_board_class.yaml's reasons map as a temp table so the
    fold/classification SQL can JOIN instead of re-deriving the mapping.
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
    con.executemany(
        "INSERT INTO lhb_reason_class VALUES (?, ?, ?)",
        [(reason, window, cat) for reason, (window, cat) in bc.reasons.items()],
    )


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
    """Register lhb_reason_class (from YAML) then run the four fail-closed
    cross-checks (r2b §2.2/§2.3) against the current window's raw rows.
    Raises ValueError with a message naming the violated check.
    """
    _register_reason_class(con, bc)

    null_reason = con.execute(
        f"SELECT COUNT(*) FROM tr.{SOURCE_TABLE} r WHERE {where} AND r.reason IS NULL",
        params,
    ).fetchone()[0]
    if null_reason:
        raise ValueError(
            f"{int(null_reason)} 行 reason 为空 (NULL), fail-closed —— 分类判据必须能读到理由字符串"
        )

    unknown = con.execute(
        f"""
        SELECT r.reason, COUNT(*) AS c
        FROM tr.{SOURCE_TABLE} r
        LEFT JOIN lhb_reason_class c ON c.reason = r.reason
        WHERE {where} AND r.reason IS NOT NULL AND c.reason IS NULL
        GROUP BY r.reason
        ORDER BY c DESC, r.reason
        """,
        params,
    ).fetchall()
    if unknown:
        detail = ", ".join(f"{reason!r}x{int(count)}" for reason, count in unknown)
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
    "DAILY_METRIC_FILTER_SQL",
    "BoardClass",
    "load_board_class",
    "audit_unknown_reasons",
    "top_inst_seat_available_at",
    "ensure_schema",
    "publish_fact_top_inst_seat_daily",
]
