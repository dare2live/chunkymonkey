"""Post-data-fetch audit for ChunkyMonkey sync checkpoints."""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from services.calendar import latest_completed_trade_date
from services.universe import ACTIVE_A_SHARE_PREFIXES   # 单一真相源 (排除股白名单前缀, 替硬编码第二真相源)

logger = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "data" / "reports" / "data_audit_latest.json"
SMART_DB_PATH = ROOT / "data" / "smartmoney.duckdb"
MARKET_DB_PATH = ROOT / "data" / "market.duckdb"
CONFIG_PATH = ROOT / "backend" / "config" / "data_audit_rules.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load data_audit_rules.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    value_str = str(value).strip().lower()
    if value_str in {"1", "true", "yes", "y", "on"}:
        return True
    if value_str in {"0", "false", "no", "off"}:
        return False
    return default


def _as_list(value: Any, default: list[Any] | tuple[Any, ...] = ()) -> list[Any]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _rule_enabled(rule: Any, default: bool = True) -> bool:
    if isinstance(rule, dict):
        return _to_bool(rule.get("enabled"), default=default)
    return default


def _first_matching_rule(rules: list[Any], name: str) -> dict[str, Any] | None:
    for rule in rules:
        if isinstance(rule, dict) and str(rule.get("name", "")).strip() == name:
            return rule
    return None


def _load_audit_config() -> dict[str, Any]:
    loaded = _load_yaml(CONFIG_PATH)
    if not loaded:
        logger.warning("data_audit_rules.yaml missing or empty; using embedded fallback values")
    return {
        "audit_rules": [
            "kline_completeness",
            "kline_consistency",
            "board_coverage",
            "date_range",
            "volume_sanity",
            "smartmoney_freshness",
            "cross_table_consistency",
        ],
        "kline_checks": {
            "source_table": "market.v_price_kline_qfq",
            "freq": "daily",
            "adjust": "qfq",
            "date_column": "date",
            "stock_code_column": "code",
            "active_table": "dim_active_a_stock",  # rule-compliance: ok evidence=audit-config-reference
            "active_code_column": "stock_code",
            "completeness_threshold": 0.0,
            "gap_max_days": 5,
            "board_prefixes": list(ACTIVE_A_SHARE_PREFIXES),   # services.universe 单一真相源
            "min_start_date": "2019-01-01",   # rule-compliance: ok evidence=canonical tushare K线起点(raw_tushare_daily实测2019-01-02), fallback镜像 data_audit_rules.yaml kline_checks.min_start_date
            "date_range_tolerance_days": 1,
            "sample_limit": 5,
            "gap_sample_limit": 8,
        },
        "smartmoney_freshness": {
            "default_max_lag_days": 3,
            "sample_limit": 5,
            # 2026-06-22 P1-2: 删 4 张 reset 已删表 (fact_sector_momentum_daily/capital_flow_pit/
            # sniper_score/institution_score) — 与 data_audit_rules.yaml 对齐 (YAML 已清), 否则 YAML
            # 缺失时此 fallback 重引入死表 → _scalar 抛 CatalogError 复发死门 (P0-1批已修 YAML+防御)。
            "tables": [
                {"table": "fact_risk_factors", "date_column": "calc_date", "max_lag_days": 3},
                {"table": "mart_stock_survey_activity", "date_column": "as_of_date", "max_lag_days": 3},
            ],
        },
        "cross_table_consistency": {
            "sample_limit": 5,
            "rules": [
                {
                    "name": "kline_universe_coverage",
                    "enabled": True,
                    "kline_source_table": "market.v_price_kline_qfq",
                    "kline_stock_code_column": "code",
                    "universe_tables": [
                        {"table": "dim_active_a_stock", "stock_code_column": "stock_code"},  # rule-compliance: ok evidence=audit-config-reference
                        {"table": "dim_all_ever_listed", "stock_code_column": "stock_code"},
                    ],
                },
                {
                    "name": "inactive_still_trading",
                    "enabled": True,
                    "kline_source_table": "market.v_price_kline_qfq",
                    "kline_date_column": "date",
                    "kline_stock_code_column": "code",
                    "recent_days": 10,
                    "inactive_table": "dim_all_ever_listed",
                    "inactive_code_column": "stock_code",
                    "is_active_column": "is_active",
                    "inactive_value": 0,
                },
            ],
        },
    } | loaded


AUDIT_RULES = _load_audit_config()


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _open_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(SMART_DB_PATH), read_only=True)
    conn.execute(f"ATTACH '{MARKET_DB_PATH}' AS market (READ_ONLY)")
    return conn


def _to_date(value: Any) -> Any:
    if value is None:
        return None
    s = str(value).strip()[:10]
    return datetime.fromisoformat(s).date() if len(s) == 10 else None


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = conn.execute(sql, params or []).fetchone()
    return row[0] if row else None


def _trading_index(conn: duckdb.DuckDBPyConnection) -> dict:
    # key 归一为 date 对象 (dim_trading_calendar.trade_date 是 VARCHAR '2026-12-31'; 不归一则与
    # _to_date 产出的 date 对象类型不匹配 → 'date in {str}' 永 False → _trading_lag_days 返 None 误判)。
    # 交易日历=强制前置真相源 (tushare trade_cal→dim_trading_calendar), lag 一律走它, 不自算/不退化日历天。
    idx: dict = {}
    for i, (d,) in enumerate(
        conn.execute("SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1 ORDER BY trade_date").fetchall()
    ):
        dd = _to_date(d)
        if dd is not None:
            idx[dd] = i
    return idx


def _trading_lag_days(index: dict, from_date: Any, to_date: Any) -> int | None:
    if from_date is None or to_date is None:
        return None
    if from_date not in index or to_date not in index:
        return None
    return abs(index[from_date] - index[to_date])


def _check_kline_completeness(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    date_col = _to_str(cfg.get("date_column"), "date")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    threshold = _to_float(cfg.get("completeness_threshold"), 0.0)
    sample_limit = _to_int(cfg.get("sample_limit"), 5)

    try:
        rows = conn.execute(f"""
            SELECT {code_col}, MIN({date_col}), MAX({date_col}), COUNT(DISTINCT {date_col})
            FROM {table}
            WHERE freq=? AND adjust=?
            GROUP BY {code_col}
        """, [freq, adjust]).fetchall()
    except Exception as exc:
        return CheckResult("kline_completeness", "FAIL", f"query failed: {exc}")

    if not rows:
        return CheckResult("kline_completeness", "FAIL", f"{table} is empty")

    misses: list[str] = []
    for code, mn, mx, actual in rows:
        expected = _scalar(
            conn,
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE is_trading=1 AND trade_date BETWEEN ? AND ?",
            [str(mn)[:10], str(mx)[:10]],
        )
        expected = int(expected or 0)
        if expected <= 0:
            continue
        missing_ratio = max(0.0, (expected - int(actual or 0)) / expected)
        if missing_ratio > threshold:
            misses.append(f"{code}: actual={actual} expected={expected}")
    if misses:
        return CheckResult("kline_completeness", "FAIL", f"{len(misses)} stock(s) miss trading days; sample: {', '.join(misses[:sample_limit])}")
    return CheckResult("kline_completeness", "PASS", "no missing trading days")


def _check_kline_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    date_col = _to_str(cfg.get("date_column"), "date")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    gap_max_days = _to_int(cfg.get("gap_max_days"), 5)
    gap_sample_limit = _to_int(cfg.get("gap_sample_limit"), 8)

    dup = conn.execute(f"""
        SELECT {code_col}, {date_col}, COUNT(*)
        FROM {table}
        WHERE freq=? AND adjust=?
        GROUP BY {code_col}, {date_col}
        HAVING COUNT(*) > 1
    """, [freq, adjust]).fetchall()
    if dup:
        return CheckResult("kline_consistency", "FAIL", f"duplicate rows for {len(dup)} (stock,date) pairs")

    idx = _trading_index(conn)
    if not idx:
        return CheckResult("kline_consistency", "FAIL", "trading calendar unavailable")

    rows = conn.execute(f"""
        SELECT {code_col}, {date_col}
        FROM {table}
        WHERE freq=? AND adjust=?
        ORDER BY {code_col}, {date_col}
    """, [freq, adjust]).fetchall()
    if not rows:
        return CheckResult("kline_consistency", "FAIL", f"{table} is empty")

    prev_code: str | None = None
    prev_day_idx: int | None = None
    samples: list[str] = []
    for code, d in rows:
        di = _to_date(d)
        if di is None or di not in idx:
            samples.append(f"{code}:{d}")
            continue
        cur = idx[di]
        if prev_code == code and prev_day_idx is not None and cur - prev_day_idx - 1 > gap_max_days:
            samples.append(f"{code}: +{cur-prev_day_idx-1} missing trading days")
        prev_code, prev_day_idx = code, cur

    if samples:
        return CheckResult("kline_consistency", "FAIL", f"duplicates or gaps >{gap_max_days}; sample: {', '.join(samples[:gap_sample_limit])}")
    return CheckResult("kline_consistency", "PASS", f"no duplicates and no >{gap_max_days} trading-day gaps")


def _check_board_coverage(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    sample_limit = _to_int(cfg.get("sample_limit"), 5)

    rows = conn.execute(f"""
        SELECT DISTINCT {code_col}
        FROM {table}
        WHERE freq=? AND adjust=? AND {code_col} IS NOT NULL
    """, [freq, adjust]).fetchall()
    prefixes = {str(c[0]).zfill(6)[:2] for c in rows}
    expected_prefixes = {str(p) for p in _as_list(cfg.get("board_prefixes"), ACTIVE_A_SHARE_PREFIXES)}
    missing = sorted(expected_prefixes - prefixes)
    if missing:
        return CheckResult("board_coverage", "FAIL", f"missing board prefixes: {', '.join(missing)}; sample: {', '.join(sorted(prefixes)[:sample_limit])}")
    return CheckResult("board_coverage", "PASS", f"all {len(expected_prefixes)} board prefixes present")


def _check_date_range(conn: duckdb.DuckDBPyConnection, calendar_svc=latest_completed_trade_date) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    date_col = _to_str(cfg.get("date_column"), "date")
    min_start = _to_str(cfg.get("min_start_date"), "2022-01-01")
    tolerance = _to_int(cfg.get("date_range_tolerance_days"), 1)

    mn, mx = conn.execute(
        f"""
        SELECT MIN({date_col}), MAX({date_col})
        FROM {table}
        WHERE freq=? AND adjust=?
        """,
        [freq, adjust],
    ).fetchone()
    mn_d, mx_d = _to_date(mn), _to_date(mx)
    if not mn_d or not mx_d:
        return CheckResult("date_range", "FAIL", f"could not read min/max from {table}")

    if mn_d < datetime.fromisoformat(min_start).date():
        return CheckResult("date_range", "FAIL", f"min_date {mn_d} < {min_start}")

    cal_d = _to_date(calendar_svc(conn))
    if not cal_d:
        return CheckResult("date_range", "FAIL", "calendar latest date unavailable")

    # lag 走交易日历真相源 (强制前置, 不自算/不退化日历天): mx_d 落后 cal_d 几个交易日。
    # 日历天会把周末/假日算进虚高 (06-18→06-22 = 4 日历天但仅 1 交易日: 06-19 假 + 周末) = 误报根因。
    idx = _trading_index(conn)
    lag = _trading_lag_days(idx, mx_d, cal_d)
    if lag is None:
        return CheckResult("date_range", "FAIL", f"max_date {mx_d} 或 calendar {cal_d} 不在交易日历 (日历前置缺失)")
    if lag > tolerance:
        return CheckResult(
            "date_range",
            "FAIL",
            f"max_date {mx_d} 落后日历最新 {cal_d} {lag} 交易日 (>容忍 {tolerance})",
        )
    return CheckResult("date_range", "PASS", f"min={mn_d} max={mx_d}, calendar={cal_d}, lag={lag}交易日")


def _check_volume_sanity(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    active_table = _to_str(cfg.get("active_table"), "dim_active_a_stock")  # rule-compliance: ok evidence=audit-config-reference
    active_code_col = _to_str(cfg.get("active_code_column"), "stock_code")
    neg = int(_scalar(conn, f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE freq=? AND adjust=?
          AND (COALESCE(volume,0) < 0 OR COALESCE(amount,0) < 0)
    """, [freq, adjust]) or 0)
    if neg:
        return CheckResult("volume_sanity", "FAIL", f"{neg} rows with negative volume/amount")

    zero_active = int(_scalar(conn, f"""
        SELECT COUNT(*)
        FROM {table} p
        INNER JOIN {active_table} a ON a.{active_code_col}=p.{code_col}
        WHERE p.freq=? AND p.adjust=?
          AND COALESCE(p.volume,0)=0 AND COALESCE(p.amount,0)=0
    """, [freq, adjust]) or 0)
    if zero_active:
        return CheckResult("volume_sanity", "FAIL", f"{zero_active} all-zero rows for active stocks")
    return CheckResult("volume_sanity", "PASS", "no negative and no active all-zero rows")


def _check_smartmoney_freshness(conn: duckdb.DuckDBPyConnection, calendar_svc=latest_completed_trade_date) -> CheckResult:
    cfg = AUDIT_RULES.get("smartmoney_freshness", {})
    rules = _as_list(cfg.get("tables"), [])
    default_max_lag = _to_int(cfg.get("default_max_lag_days"), 3)
    sample_limit = _to_int(cfg.get("sample_limit"), 5)

    cal = _to_date(calendar_svc(conn))
    if not cal:
        return CheckResult("smartmoney_freshness", "FAIL", "calendar latest date unavailable")
    idx = _trading_index(conn)
    if not idx:
        return CheckResult("smartmoney_freshness", "FAIL", "trading calendar unavailable")

    fails: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        table = _to_str(rule.get("table"))
        date_col = _to_str(rule.get("date_column"))
        if not table or not date_col:
            continue
        max_lag_days = _to_int(rule.get("max_lag_days"), default_max_lag)
        # 防御 (2026-06-22): 单条规则的表/列漂移 (reset 删表 / schema 改) 不许崩掉整个 audit —
        # 镜像 _check_kline_completeness 的 try/except, 坏规则记 FAIL 续跑 (mythos§14: 崩溃门=死门审0项)。
        try:
            row = _scalar(conn, f"SELECT MAX({date_col}) FROM {table}")
        except Exception as exc:
            fails.append(f"{table}: query failed ({type(exc).__name__})")
            continue
        latest = _to_date(row)
        if not latest:
            fails.append(f"{table}: no rows")
            continue
        lag = _trading_lag_days(idx, latest, cal)
        if lag is None or lag > max_lag_days:
            fails.append(f"{table}: lag>{lag if lag is not None else '?'} (latest={latest}, calendar={cal})")
    if fails:
        return CheckResult("smartmoney_freshness", "FAIL", f"stale smartmoney tables: {', '.join(fails[:sample_limit])}")
    return CheckResult("smartmoney_freshness", "PASS", "all key smartmoney tables within configured lag")


def _check_cross_table_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    kline_cfg = AUDIT_RULES.get("kline_checks", {})
    kline_code_col = _to_str(kline_cfg.get("stock_code_column"), "code")
    cross_cfg = AUDIT_RULES.get("cross_table_consistency", {})
    cross_rules = _as_list(cross_cfg.get("rules"), [])
    sample_limit = _to_int(cross_cfg.get("sample_limit"), 5)

    kline_coverage_rule = _first_matching_rule(cross_rules, "kline_universe_coverage")
    if kline_coverage_rule is None:
        return CheckResult("cross_table_consistency", "FAIL", "missing cross_table_consistency.kline_universe_coverage rule")
    if not _rule_enabled(kline_coverage_rule, default=True):
        return CheckResult("cross_table_consistency", "PASS", "cross-table consistency rules are disabled")

    kline_source_table = _to_str(kline_coverage_rule.get("kline_source_table"), "market.v_price_kline_qfq")
    kline_source_code_col = _to_str(kline_coverage_rule.get("kline_stock_code_column"), kline_code_col)

    kline_codes = {c for (c,) in conn.execute(f"""
        SELECT DISTINCT {kline_source_code_col}
        FROM {kline_source_table}
        WHERE {kline_source_code_col} IS NOT NULL
    """).fetchall() if c is not None}
    if not kline_codes:
        return CheckResult("cross_table_consistency", "FAIL", "kline table has no stock codes")

    all_codes: set[str] = set()
    for universe in _as_list(kline_coverage_rule.get("universe_tables"), []):
        if not isinstance(universe, dict):
            continue
        table = _to_str(universe.get("table"))
        col = _to_str(universe.get("stock_code_column"), "stock_code")
        if not table or not col:
            continue
        all_codes.update({c for (c,) in conn.execute(f"SELECT {col} FROM {table}").fetchall() if c is not None})

    if not all_codes:
        return CheckResult("cross_table_consistency", "PASS", "no universe rows configured for consistency checks")

    extras = sorted(c for c in kline_codes if c not in all_codes)

    inactive_rule = _first_matching_rule(cross_rules, "inactive_still_trading")
    if inactive_rule is None:
        return CheckResult("cross_table_consistency", "FAIL", "missing cross_table_consistency.inactive_still_trading rule")
    if not _rule_enabled(inactive_rule, default=True):
        if not extras:
            return CheckResult(
                "cross_table_consistency",
                "PASS",
                "cross-table consistency passed with optional stale inactive rule disabled",
            )
        return CheckResult(
            "cross_table_consistency",
            "FAIL",
            f"{len(extras)} kline codes not in universe tables",
        )

    inactive_days = _to_int(inactive_rule.get("recent_days"), 10)
    inactive_table = _to_str(inactive_rule.get("inactive_table"), "dim_all_ever_listed")
    inactive_code_col = _to_str(inactive_rule.get("inactive_code_column"), "stock_code")
    is_active_col = _to_str(inactive_rule.get("is_active_column"), "is_active")
    inactive_value = _to_int(inactive_rule.get("inactive_value"), 0)
    inactive_table_col_date = _to_str(inactive_rule.get("kline_date_column"), "date")
    inactive_source_table = _to_str(inactive_rule.get("kline_source_table"), "market.v_price_kline_qfq")
    inactive_source_code_col = _to_str(inactive_rule.get("kline_stock_code_column"), kline_source_code_col)

    recent_codes = {c for (c,) in conn.execute(f"""
        SELECT DISTINCT {inactive_source_code_col}
        FROM {inactive_source_table}
        WHERE CAST({inactive_table_col_date} AS DATE) >= CURRENT_DATE - INTERVAL '{inactive_days} days'
    """).fetchall() if c is not None}
    inactive_codes = {c for (c,) in conn.execute(
        f"SELECT {inactive_code_col} FROM {inactive_table} WHERE {is_active_col} = {inactive_value}"
    ).fetchall() if c is not None}
    wrongly_inactive = inactive_codes & recent_codes

    issues = []
    if extras:
        issues.append(f"{len(extras)} kline codes not in universe tables")
    if wrongly_inactive:
        issues.append(f"{len(wrongly_inactive)} stocks marked inactive but still trading (inactive_value={inactive_value}, recent_days={inactive_days})")

    if issues:
        sample = ", ".join(sorted(wrongly_inactive)[:sample_limit]) if wrongly_inactive else ""
        if sample:
            issues.append(f"sample: {sample}")
        return CheckResult("cross_table_consistency", "FAIL", "; ".join(issues))
    return CheckResult("cross_table_consistency", "PASS", "kline codes consistent with universe tables, no wrongly-inactive stocks")


def _overall_status(checks: list[CheckResult]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    return "PASS"


def _is_strict(strict: bool) -> bool:
    return bool(strict and os.getenv("AUDIT_STRICT", "1") not in {"0", "false", "False", "FALSE"})


def run_post_sync_audit(step_name: str, strict: bool = True) -> dict[str, Any]:
    check_fns = {
        "kline_completeness": _check_kline_completeness,
        "kline_consistency": _check_kline_consistency,
        "board_coverage": _check_board_coverage,
        "date_range": _check_date_range,
        "volume_sanity": _check_volume_sanity,
        "smartmoney_freshness": _check_smartmoney_freshness,
        "cross_table_consistency": _check_cross_table_consistency,
    }
    configured_rules = AUDIT_RULES.get("audit_rules", [])
    if not isinstance(configured_rules, list):
        configured_rules = [
            "kline_completeness",
            "kline_consistency",
            "board_coverage",
            "date_range",
            "volume_sanity",
            "smartmoney_freshness",
            "cross_table_consistency",
        ]

    checks = []
    with _open_conn() as conn:
        for rule in configured_rules:
            check = check_fns.get(str(rule))
            if check is None:
                checks.append(CheckResult(str(rule), "FAIL", f"unknown audit rule '{rule}'"))
                continue
            checks.append(check(conn))

    result = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "checks": [asdict(c) for c in checks],
        "overall": _overall_status(checks),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if result["overall"] == "FAIL":
        msg = f"post-data-sync audit FAILED at step={step_name}: " + \
              "; ".join(f"{c['name']}={c['status']}" for c in result["checks"] if c["status"] == "FAIL")
        if _is_strict(strict):
            raise RuntimeError(msg)
        logger.warning(msg)
    logger.info("data_audit step=%s overall=%s report=%s", step_name, result["overall"], REPORT_PATH)
    return result


# Backward-compatible exports used by legacy routes.
def audit_all(_conn: Any = None) -> list[dict[str, Any]]:
    out = run_post_sync_audit("legacy", strict=False)["checks"]
    checks = []
    for r in out:
        checks.append({
            "table": r["name"],
            "issues": [{"level": "warn" if r["status"] == "WARN" else "error", "msg": r["detail"]}
                      if r["status"] != "PASS" else []],
            "status": r["status"],
        })
    return checks


def save_audit_report(_conn: Any, results: list[dict[str, Any]]) -> int:
    # keep prior behavior/shape for callers expecting an integer id
    return int(datetime.now().timestamp() * 1000)


def load_last_audit_report(_conn: Any = None) -> dict[str, Any] | None:
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def summary() -> dict[str, Any]:
    configured_rules = AUDIT_RULES.get("audit_rules", [])
    n_checks = len(configured_rules) if isinstance(configured_rules, list) else 7
    return {"n_checks": n_checks, "report_path": str(REPORT_PATH)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True)
    parser.add_argument("--strict", action="store_true", default=True)
    args = parser.parse_args()
    run_post_sync_audit(args.step, strict=args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
