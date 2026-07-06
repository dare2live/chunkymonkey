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
from services.universe import ACTIVE_A_SHARE_PREFIXES, classify_exclusion   # 单一真相源 (板块前缀身份/排除规则)

logger = logging.getLogger(__name__)


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "data" / "reports" / "data_audit_latest.json"
SMART_DB_PATH = ROOT / "data" / "smartmoney.duckdb"
MARKET_DB_PATH = ROOT / "data" / "market.duckdb"
TUSHARE_RAW_DB_PATH = ROOT / "data" / "tushare_raw.duckdb"  # rule-compliance: ok evidence=audit模块固定DB常量(同SMART/MARKET_DB_PATH), data_audit在connect_policy豁免名单; kline_completeness clean-vs-source口径源
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
            # smartmoney_freshness check 已删 2026-06-28: 唯二配置表 fact_risk_factors(U4)/
            #   mart_stock_survey_activity(U5) 已物删, 且无 daily-fresh smartmoney 派生表; 各域新鲜度
            #   由 update_watermark_sla SLA gate 按正确 per-domain 窗全覆盖 (去重非丢覆盖)。
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
        # smartmoney_freshness fallback config 已删 2026-06-28 (check 整除, 见 audit_rules 注释)
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
                    ],
                },
                # inactive_still_trading 规则 2026-07-07 整段退役 (同 data_audit_rules.yaml 头注): 依赖已
                # 物删的 dim_all_ever_listed 判"标记 inactive 但仍在交易", 该表无存活 writer 且外部
                # is_active 声明源本身已被判定为不再需要 (universe.py K 线活跃真相源原则)。
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
    if TUSHARE_RAW_DB_PATH.exists():  # kline_completeness clean-vs-source 口径需 raw 源
        conn.execute(f"ATTACH '{TUSHARE_RAW_DB_PATH}' AS tushare_raw (READ_ONLY)")
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
    from services.data_access import resolver
    idx: dict = {}
    c, own = resolver.dim_read_conn(conn, "dim_trading_calendar")  # rule-compliance: ok evidence=dim迁reference, conn有表用过渡dual否则fall reference
    try:
        rows = c.execute("SELECT trade_date FROM dim_trading_calendar WHERE is_trading=1 ORDER BY trade_date").fetchall()
    finally:
        if own:
            c.close()
    for i, (d,) in enumerate(rows):
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
    """M2(clean)阶段完整性门: clean serving K线 是否无损保住 source(raw 源)的所有交易行。

    2026-06-24 cry-wolf 修复: 旧口径拿 clean 比"全交易日历"(隐含假设每股每交易日都有数据)→ 停牌/退市股
    被当缺口, 实测 1711股/31.5% 误报 (感知死: 门长期红被无视, 真缺口溜过 = 真金白银隐患)。
    正解 = clean-vs-source: M2 的职责是无损变换 raw_tushare_daily→qfq, 该验的是"clean 丢了 source 有的行吗",
    与停牌/退市/日历无关 (源本就没有停牌日 = 合法非缺口)。实测新口径 0 丢失行。
    M1(acquire) 的"是否拉全 tushare" 由 sync watermark/drain 守 (另一阶段的门), 不在此 calendar 比对。
    口径证据: analysis/kline_completeness_crywolf_fix_20260624.md
    """
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")   # clean (serving) 表
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    date_col = _to_str(cfg.get("date_column"), "date")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    sample_limit = _to_int(cfg.get("sample_limit"), 5)
    src_table = _to_str(cfg.get("source_raw_table"), "raw_tushare_daily")   # raw 源 (clean 上游)
    src_code = _to_str(cfg.get("source_raw_code_column"), "ts_code")
    src_date = _to_str(cfg.get("source_raw_date_column"), "trade_date")

    # lost = source 有但 clean 没有的 (code, date) = clean 丢了 source 行 (M2 变换不无损)。
    # split_part 去 ts_code 后缀 (.SZ/.SH) 对齐 clean.code; clean.date(VARCHAR '2026-01-02') 去横线对齐 raw(YYYYMMDD)。
    # 只比 clean 宇宙内的股 (源含未建 clean 的股=universe 问题非 clean-loss, 不在此门)。
    try:
        rows = conn.execute(f"""
            WITH src AS (
                SELECT split_part({src_code}, '.', 1) AS code, {src_date} AS d
                FROM tushare_raw.{src_table}
            ),
            cln AS (
                SELECT {code_col} AS code, replace(CAST({date_col} AS VARCHAR), '-', '') AS d
                FROM {table} WHERE freq=? AND adjust=?
            ),
            lost AS (
                SELECT s.code, s.d FROM src s
                WHERE s.code IN (SELECT DISTINCT code FROM cln)
                EXCEPT
                SELECT code, d FROM cln
            )
            SELECT code, COUNT(*) AS lost FROM lost GROUP BY code ORDER BY lost DESC
        """, [freq, adjust]).fetchall()
    except Exception as exc:
        return CheckResult("kline_completeness", "FAIL", f"query failed (source raw 不可达?): {exc}")

    if not rows:
        return CheckResult("kline_completeness", "PASS", "clean lossless vs source (无 source 行被丢)")
    total_codes = len(rows)
    total_lost = sum(int(r[1] or 0) for r in rows)
    sample = ", ".join(f"{code}:{lost}" for code, lost in rows[:sample_limit])
    return CheckResult(
        "kline_completeness", "FAIL",
        f"{total_codes} stock(s) lost {total_lost} source row(s) in clean (M2 非无损); sample: {sample}",
    )


def _check_kline_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    cfg = AUDIT_RULES.get("kline_checks", {})
    table = _to_str(cfg.get("source_table"), "market.v_price_kline_qfq")
    freq = _to_str(cfg.get("freq"), "daily")
    adjust = _to_str(cfg.get("adjust"), "qfq")
    date_col = _to_str(cfg.get("date_column"), "date")
    code_col = _to_str(cfg.get("stock_code_column"), "code")
    sample_limit = _to_int(cfg.get("gap_sample_limit"), 8)

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

    # 2026-06-24 cry-wolf 修复: 移除"交易日 gap > N 天"判定 — 跨 gap 是停牌(合法), 与全交易日历比 gap
    # 对停牌股必误报 (实测 000004 +10/+36天=停牌)。clean 丢 source 行已由 kline_completeness(clean-vs-source)守,
    # 此处不再 calendar 比对。保留: (a) 重复(code,date) (b) clean 行落在非交易日 (clean 不该有非交易日行=真不一致)。
    rows = conn.execute(f"""
        SELECT DISTINCT {code_col}, {date_col}
        FROM {table}
        WHERE freq=? AND adjust=?
    """, [freq, adjust]).fetchall()
    if not rows:
        return CheckResult("kline_consistency", "FAIL", f"{table} is empty")

    non_trading: list[str] = []
    for code, d in rows:
        di = _to_date(d)
        if di is None or di not in idx:
            non_trading.append(f"{code}:{d}")

    if non_trading:
        return CheckResult("kline_consistency", "FAIL",
                           f"{len(non_trading)} row(s) on non-trading days; sample: {', '.join(non_trading[:sample_limit])}")
    return CheckResult("kline_consistency", "PASS", "no duplicate rows, no rows on non-trading days")


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
    neg = int(_scalar(conn, f"""
        SELECT COUNT(*)
        FROM {table}
        WHERE freq=? AND adjust=?
          AND (COALESCE(volume,0) < 0 OR COALESCE(amount,0) < 0)
    """, [freq, adjust]) or 0)
    if neg:
        return CheckResult("volume_sanity", "FAIL", f"{neg} rows with negative volume/amount")

    # §9 cross-db JOIN 重构: 旧 INNER JOIN {table} x dim_active_a_stock 是 facts(market)xdim cross-db,
    # dim 迁 reference 后无法直接 JOIN。改先取 active 码集 (security_master.active_codes 内部已 dim_read_conn
    # auto-fallback reference) 再 WHERE code IN (?) 避 cross-db。
    from services.security_master import active_codes
    active_set = active_codes(conn)  # rule-compliance: ok evidence=dim_active迁reference, helper内部dim_read_conn路由
    if active_set:
        placeholders = ",".join("?" for _ in active_set)
        zero_active = int(_scalar(conn, f"""
            SELECT COUNT(*)
            FROM {table} p
            WHERE p.freq=? AND p.adjust=?
              AND COALESCE(p.volume,0)=0 AND COALESCE(p.amount,0)=0
              AND p.{code_col} IN ({placeholders})
        """, [freq, adjust, *sorted(active_set)]) or 0)
    else:
        zero_active = 0
    if zero_active:
        return CheckResult("volume_sanity", "FAIL", f"{zero_active} all-zero rows for active stocks")
    return CheckResult("volume_sanity", "PASS", "no negative and no active all-zero rows")


# _check_smartmoney_freshness 已删 2026-06-28: 配置表 fact_risk_factors(U4)/mart_stock_survey_activity(U5)
#   物删 + 无 daily-fresh smartmoney 派生表; 新鲜度由 update_watermark_sla SLA gate 按 per-domain 窗全覆盖。


def _check_cross_table_consistency(conn: duckdb.DuckDBPyConnection) -> CheckResult:
    kline_cfg = AUDIT_RULES.get("kline_checks", {})
    kline_code_col = _to_str(kline_cfg.get("stock_code_column"), "code")
    cross_cfg = AUDIT_RULES.get("cross_table_consistency", {})
    cross_rules = _as_list(cross_cfg.get("rules"), [])

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

    # 2026-06-24 cry-wolf 修复: kline 码合法性 = 板块前缀身份真相源 (services.universe.classify_exclusion),
    # 非 dim 表枚举。旧口径"kline ⊆ dim_active∪dim_all_ever_listed"违项目第一性原理 (K线=真相源;
    # universe.py 明示"不需要 dim_all_ever_listed"): 退市 A股有真实 K线+合法板块但不在 current stock_basic →
    # 被误报 (实测 209 全是合法 00/60/30/68 退市股=cry-wolf)。退市由 universe 规则3(K线近90天无交易) PIT 排除,
    # 不靠 dim 表枚举。此 coverage 仅守"非A股板块(北交所83x/三板/指数) leak 进 A股 K线"。
    extras = sorted(c for c in kline_codes if classify_exclusion(c) is not None)

    # inactive_still_trading 子检查 2026-07-07 整段退役 (owner=PROJECT_INDEX.md 决策收口): 原逻辑靠
    # dim_all_ever_listed 声明的 is_active 标记去比对"是否仍在交易", 该表已物删(无存活 writer, 冻结
    # 10+ 周的快照, 且 universe.py 已确立 K 线本身即活跃真相源) — 少了外部 is_active 声明源, 这个
    # 检查会退化为拿 K 线跟自己比对的空转; 保留仅剩这里的 kline_universe_coverage(北交所/非A股板块 leak)。
    if extras:
        return CheckResult("cross_table_consistency", "FAIL", f"{len(extras)} kline codes not in universe tables")
    return CheckResult("cross_table_consistency", "PASS", "kline codes consistent with universe board-prefix truth source")


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
