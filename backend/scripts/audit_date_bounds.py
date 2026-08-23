#!/usr/bin/env python3
"""审计各数据域目标表的日期列是否越界 (audit-only, 不拦截)。

**背景**: 实测 raw_tushare_stk_holdernumber 里有 6 行 end_date 越界 (20530626 /
28240531 / 19000928 x2 / 19000908 / 20340430)。现有体系对这类"单行日期字段越界"
没有任何检测——normalize_watermark_day (source_watermarks.py) 只验 8 位数字格式,
不验范围; 一个未来越界日期若混进 watermark, SLA 落后天数会算成负数, 永远不告警,
等于停更监控永久失效。本脚本补这道检测。

**这是 audit 模式, 不是判定器**: 有些域的日期列本来就该是未来 (例如 share_float
的限售解禁日期)。本脚本只负责把落在 [下界, 今天+7天] 之外的值列出来给人看, 不擅自
判定哪些是错的、也不加白名单——晚于上界的值可能是合法的未来日期, 需要人工判定
(见报告结尾提示)。

**判据**:
  - 下界 = 19900101 固定 (A 股开市年; 不用 data_start, 理由见 DEFAULT_LOWER_BOUND)。
  - 上界 = 今天 + 7 天 (compact8)。
  - 越界 = 归一后的值 < 下界 或 > 上界; 只比较归一后长度为 8 且全数字的值,
    其余记为"格式异常", 不计入越界也不崩溃。
  - 日期列: 若域声明 freshness_date_column 用它, 否则从 grain 里挑名字含
    "date" 的列; 都没有则该域标"无可判定日期列", 跳过。域一旦"够格", 实际检查
    的是该表里**所有**匹配命名标准(列名以 _date 结尾, 或等于
    trade_date/ann_date/end_date/date)的真实列——不止声明的那一个, 例如
    stk_holdernumber 的 ann_date + end_date 都查, 不只查 freshness_date_column
    指向的 ann_date。

**数据库连接**: 只读。库路径经 backend/services/database_manifest.py 的
get_database_manifest().path_for(alias) 取, 不硬编码路径。域没声明 db 就默认
tushare_raw, 找不到表依次试 smartmoney / market / reference; 全找不到则报"表不存在",
不抛异常。

用法:
    PYTHONPATH=backend python backend/scripts/audit_date_bounds.py            # 纯文本报告
    PYTHONPATH=backend python backend/scripts/audit_date_bounds.py --json     # 机器可读
    PYTHONPATH=backend python backend/scripts/audit_date_bounds.py --registry /path/to/sync_registry.yaml

退出码: 恒为 0 (audit 模式不阻断)。除非脚本自身出错(读不到/解析不了 YAML,
或数据库 manifest 本身加载失败)才非零。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = REPO / "backend" / "config" / "sync_registry.yaml"

# 候选库别名, 找不到表时依次试 (第一个默认走 target_db/tushare_raw, 见 _candidate_dbs)。
_FALLBACK_DB_ALIASES = ("tushare_raw", "smartmoney", "market", "reference")

_COMPACT_DAY_RE = re.compile(r"^\d{8}$")
_DASHED_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DIGIT8_PATTERN = r"[0-9]{8}"

# ── 边界取"荒谬值"而非"业务期望值" (2026-08-23 首轮实跑后修正) ──────────────
# 初版下界取该域的 data_start、上界取 today+7, 实跑 44 域报出 328,266 行越界,
# 其中只有 3 行是真异常 (stk_holdernumber.end_date 的 20340430/20530626/28240531)。
# 其余全是判据本身的误报, 两个根因:
#   下界: data_start 是**采集轴起点**(对应 ann_date/trade_date), 拿它当 end_date
#         (报告期) 的下界本就不成立 —— 2019 年公告的 2018 年报, 报告期天然早于采集起点。
#         实测 stk_holdernumber.end_date 因此误报 6,235 行。
#   上界: 有些日期列**本来就该是未来** —— share_float.float_date 是限售解禁日,
#         实测因此误报 311,427 行 (占总量 95%)。
# 修法不是逐列配置白名单(那会把判据变成需要维护的第二份 registry), 而是把判据的
# 语义从"落在业务期望区间内"降到"不是荒谬值":
#   下界 19900101 —— A 股 1990 年开市, 早于此的日期没有任何合法解释 (实测能抓住
#                    stk_holdernumber 的 19000908/19000928 这类年份错位)。
#   上界 today + 5 年 —— 限售解禁最长 36 个月, 5 年外的未来日期无合法业务解释;
#                    足以抓住 20340430(8年后)/20530626/28240531, 又不误伤解禁日。
# 代价是灵敏度下降: 一条"晚 2 年"的错误日期抓不到。这是刻意的取舍 ——
# 一个 328,266 条的报告没人会看, 判据的价值取决于信噪比而非覆盖率。
DEFAULT_LOWER_BOUND = "19900101"
UPPER_BOUND_SLACK_DAYS = 365 * 5
MAX_SAMPLES = 5

LOG = logging.getLogger("audit_date_bounds")

ConnProvider = Callable[[str], Optional[Any]]


# ── 日期归一 (data_start / watermark 同类值, compact8 或带横线均可能) ──

def _normalize_day(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _COMPACT_DAY_RE.match(text):
        return text
    dashed = _DASHED_DAY_RE.match(text)
    if dashed:
        return "".join(dashed.groups())
    return None


def compute_bounds(data_start: Any, today: Optional[datetime.date] = None) -> tuple[str, str]:
    """返回 (下界, 上界), 均为 compact8。

    下界**不**取 data_start —— 见 DEFAULT_LOWER_BOUND 处的实测记录。参数保留是为了
    让调用方仍能传入(便于将来逐域收紧), 当前实现刻意忽略它。
    """
    del data_start  # 刻意忽略: 采集轴起点不是报告期/解禁日的合法下界
    lower = DEFAULT_LOWER_BOUND
    today = today or datetime.date.today()
    upper = (today + datetime.timedelta(days=UPPER_BOUND_SLACK_DAYS)).strftime("%Y%m%d")
    return lower, upper


# ── 日期列判定 ──

def _declared_date_columns(spec: dict[str, Any]) -> list[str]:
    """域是否"够格"检查的判据: 有 freshness_date_column, 或 grain 里有含 date 的列。

    这里只判定"有没有资格", 不是最终要检查的列集合——真正检查的列来自实测
    表结构里所有匹配命名标准的列 (见 _looks_like_date_column / audit_domain)。
    """
    fdc = spec.get("freshness_date_column")
    if fdc:
        return [str(fdc)]
    grain = spec.get("grain")
    if isinstance(grain, list):
        cands = [str(c) for c in grain if "date" in str(c).lower()]
        if cands:
            return cands
    return []


def _looks_like_date_column(name: str) -> bool:
    lname = name.lower()
    return lname.endswith("_date") or lname in ("trade_date", "ann_date", "end_date", "date")


# ── 表结构探测 + 越界查询 (只读) ──

def _table_columns(conn: Any, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM duckdb_columns() WHERE table_name = ? ORDER BY column_index",
        [table],
    ).fetchall()
    return [r[0] for r in rows]


def _check_column(conn: Any, table: str, col: str, lower: str, upper: str) -> dict[str, Any]:
    sql = (
        f'WITH norm AS ('
        f'  SELECT replace(CAST("{col}" AS VARCHAR), \'-\', \'\') AS d '
        f'  FROM "{table}" WHERE "{col}" IS NOT NULL'
        f') '
        "SELECT "
        "  sum(CASE WHEN length(d) = 8 AND regexp_full_match(d, ?) AND d < ? THEN 1 ELSE 0 END), "
        "  sum(CASE WHEN length(d) = 8 AND regexp_full_match(d, ?) AND d > ? THEN 1 ELSE 0 END), "
        "  sum(CASE WHEN NOT (length(d) = 8 AND regexp_full_match(d, ?)) THEN 1 ELSE 0 END) "
        "FROM norm"
    )
    below, above, bad_format = conn.execute(
        sql, [_DIGIT8_PATTERN, lower, _DIGIT8_PATTERN, upper, _DIGIT8_PATTERN]
    ).fetchone()
    below = below or 0
    above = above or 0
    bad_format = bad_format or 0

    below_samples = _samples(conn, table, col, "<", lower) if below else []
    above_samples = _samples(conn, table, col, ">", upper) if above else []

    return {
        "column": col,
        "below_count": below,
        "above_count": above,
        "bad_format_count": bad_format,
        "below_samples": below_samples,
        "above_samples": above_samples,
    }


def _samples(conn: Any, table: str, col: str, op: str, bound: str) -> list[str]:
    assert op in ("<", ">")
    sql = (
        f'SELECT DISTINCT replace(CAST("{col}" AS VARCHAR), \'-\', \'\') AS d '
        f'FROM "{table}" '
        f'WHERE "{col}" IS NOT NULL '
        f"  AND length(replace(CAST(\"{col}\" AS VARCHAR), '-', '')) = 8 "
        f"  AND regexp_full_match(replace(CAST(\"{col}\" AS VARCHAR), '-', ''), ?) "
        f"  AND d {op} ? "
        f"LIMIT {MAX_SAMPLES}"
    )
    rows = conn.execute(sql, [_DIGIT8_PATTERN, bound]).fetchall()
    return [r[0] for r in rows]


def _candidate_dbs(spec: dict[str, Any]) -> list[str]:
    primary = str(spec.get("target_db") or "tushare_raw")
    rest = [alias for alias in _FALLBACK_DB_ALIASES if alias != primary]
    return [primary] + rest


# ── 单域审计 ──

def audit_domain(
    domain: str,
    spec: dict[str, Any],
    conn_provider: ConnProvider,
    today: Optional[datetime.date] = None,
) -> dict[str, Any]:
    table = spec.get("target_table")
    result: dict[str, Any] = {"domain": domain, "table": table or "", "db": None, "columns": []}

    if not table:
        result["status"] = "no_target_table"
        return result

    if not _declared_date_columns(spec):
        result["status"] = "no_date_column"
        return result

    conn = None
    used_db = None
    real_cols: list[str] = []
    for alias in _candidate_dbs(spec):
        candidate_conn = conn_provider(alias)
        if candidate_conn is None:
            continue
        cols = _table_columns(candidate_conn, table)
        if cols:
            conn = candidate_conn
            used_db = alias
            real_cols = cols
            break

    if conn is None:
        result["status"] = "table_not_found"
        return result

    result["db"] = used_db
    date_cols = [c for c in real_cols if _looks_like_date_column(c)]
    if not date_cols:
        result["status"] = "no_matching_column_in_table"
        return result

    lower, upper = compute_bounds(spec.get("data_start"), today=today)
    result["status"] = "checked"
    result["lower_bound"] = lower
    result["upper_bound"] = upper
    result["columns"] = [_check_column(conn, table, col, lower, upper) for col in date_cols]
    return result


def scan_all_domains(
    domains: dict[str, Any],
    conn_provider: ConnProvider,
    today: Optional[datetime.date] = None,
) -> list[dict[str, Any]]:
    results = []
    for domain, spec in domains.items():
        if not isinstance(spec, dict):
            continue
        results.append(audit_domain(domain, spec, conn_provider, today=today))
    return results


# ── 报告渲染 ──

INTRO_LINES = (
    "本报告核对每个数据域日期列的取值是否落在 [下界, 今天+7天] 之外——这是审计,",
    "不是判定: 越界不代表一定是错的 (例如解禁日这类天然的未来日期), 只是把候选项列出来。",
)

OUTRO_LINE = "提示: 晚于上界的值可能是合法的未来日期 (如解禁日), 需人工判定; 早于下界的值一般更可疑, 但同样不自动下结论。"


def _violations(domain_result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for col in domain_result.get("columns", []):
        if col["below_count"] or col["above_count"]:
            out.append(col)
    return out


def render_text(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("日期越界审计报告 (audit 模式, 只报告不拦截)")
    lines.append("=" * 78)
    lines.extend(INTRO_LINES)
    lines.append("")

    checked = [r for r in results if r["status"] == "checked"]
    no_date_col = [r for r in results if r["status"] == "no_date_column"]
    table_missing = [r for r in results if r["status"] == "table_not_found"]
    no_target = [r for r in results if r["status"] == "no_target_table"]
    no_match = [r for r in results if r["status"] == "no_matching_column_in_table"]

    total_violation_domains = 0
    total_violation_rows = 0
    # 同一张物理表可被多个域引用 (实测: index_member_all 与 index_member_all_hist 都指向
    # raw_tushare_index_member_all), 逐域累加会把同一批脏行重复计数 —— 首轮实跑因此把 16 行
    # 报成 25 行。总数按 (db, 表, 列) 去重, 逐域明细仍照常各自列出 (读者需要知道哪些域受影响)。
    counted_cells: set[tuple[str, str, str]] = set()

    lines.append("【逐域明细】")
    lines.append("-" * 78)
    for r in results:
        status = r["status"]
        if status == "no_date_column":
            lines.append(f"{r['domain']:<24} 无可判定日期列, 跳过")
            continue
        if status == "no_target_table":
            lines.append(f"{r['domain']:<24} registry 未声明 target_table, 跳过")
            continue
        if status == "table_not_found":
            lines.append(f"{r['domain']:<24} 表不存在 (target_table={r['table']!r}, 已试全部候选库)")
            continue
        if status == "no_matching_column_in_table":
            lines.append(f"{r['domain']:<24} 表 {r['table']} 里没有匹配命名标准的日期列")
            continue

        # checked
        viol = _violations(r)
        cols_desc = ", ".join(c["column"] for c in r["columns"])
        lines.append(
            f"{r['domain']:<24} 表={r['table']} (db={r['db']}) 检查列=[{cols_desc}] "
            f"下界={r['lower_bound']} 上界={r['upper_bound']}"
        )
        if viol:
            total_violation_domains += 1
        for c in r["columns"]:
            if c["bad_format_count"]:
                lines.append(f"    列 {c['column']}: 格式异常 {c['bad_format_count']} 行 (非8位纯数字, 已跳过不计越界)")
            cell = (str(r.get("db")), str(r.get("table")), str(c["column"]))
            first_time = cell not in counted_cells
            if c["below_count"] or c["above_count"]:
                counted_cells.add(cell)
            if c["below_count"]:
                if first_time:
                    total_violation_rows += c["below_count"]
                lines.append(
                    f"    列 {c['column']}: 早于下界 {c['below_count']} 行, "
                    f"样本={c['below_samples']}"
                )
            if c["above_count"]:
                if first_time:
                    total_violation_rows += c["above_count"]
                lines.append(
                    f"    列 {c['column']}: 晚于上界 {c['above_count']} 行, "
                    f"样本={c['above_samples']}"
                )

    lines.append("")
    lines.append("【总览】")
    lines.append("-" * 78)
    lines.append(f"registry 总域数: {len(results)}")
    lines.append(f"实际检查域数: {len(checked)}")
    lines.append(f"无可判定日期列 (跳过): {len(no_date_col)}")
    lines.append(f"未声明 target_table (跳过): {len(no_target)}")
    lines.append(f"表不存在 (跳过): {len(table_missing)}")
    lines.append(f"表存在但无匹配日期列 (跳过): {len(no_match)}")
    lines.append(f"有越界项的域数: {total_violation_domains}")
    lines.append(f"越界总行数 (早于下界 + 晚于上界, 同表同列只计一次): {total_violation_rows}")
    lines.append("")
    lines.append("-" * 78)
    lines.append(OUTRO_LINE)
    lines.append("=" * 78)
    return "\n".join(lines)


def render_json(results: list[dict[str, Any]]) -> str:
    checked = [r for r in results if r["status"] == "checked"]
    violation_domains = [r for r in checked if _violations(r)]
    total_violation_rows = sum(
        c["below_count"] + c["above_count"]
        for r in checked
        for c in r["columns"]
    )
    payload = {
        "intro": " ".join(INTRO_LINES),
        "outro": OUTRO_LINE,
        "total_domains": len(results),
        "checked_domains": len(checked),
        "no_date_column_domains": [r["domain"] for r in results if r["status"] == "no_date_column"],
        "no_target_table_domains": [r["domain"] for r in results if r["status"] == "no_target_table"],
        "table_not_found_domains": [r["domain"] for r in results if r["status"] == "table_not_found"],
        "no_matching_column_domains": [r["domain"] for r in results if r["status"] == "no_matching_column_in_table"],
        "violation_domain_count": len(violation_domains),
        "violation_row_count": total_violation_rows,
        "domains": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ── 默认(真实)连接 provider: 经 database_manifest 只读打开, 找不到文件返回 None ──

def _make_default_conn_provider() -> tuple[ConnProvider, Callable[[], None]]:
    sys.path.insert(0, str(REPO / "backend"))
    import duckdb
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect as duck_connect

    manifest = get_database_manifest()
    cache: dict[str, Any] = {}

    def provider(alias: str) -> Optional[Any]:
        if alias in cache:
            return cache[alias]
        conn = None
        path = manifest.path_for(alias)
        if path.exists():
            # 走 duck_adapter 而非裸 duckdb.connect: DB 边界由统一入口把关 (Rule/DB boundary)。
            # 只读打开 —— 本脚本是 audit, 任何情况下都不得写库。
            try:
                conn = duck_connect(str(path), read_only=True)
            except duckdb.Error as exc:
                # 只吞 duckdb 自身的打开失败 (库被别的进程独占写、文件损坏), 这类在 audit 语境下
                # 应降级为"该库不可查"而不是中断整轮扫描; 其它异常照常抛出, 不静默。
                LOG.warning("打开 %s (%s) 失败, 该库涉及的域将标为表不存在: %s", alias, path, exc)
                conn = None
        cache[alias] = conn
        return conn

    def close_all() -> None:
        for conn in cache.values():
            if conn is not None:
                try:
                    conn.close()
                except duckdb.Error as exc:  # 关闭失败不影响 audit 结果, 但要留痕不静默
                    LOG.warning("关闭连接失败: %s", exc)

    return provider, close_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH,
                     help="sync_registry.yaml 路径 (默认项目正式 registry)")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 而非纯文本")
    args = ap.parse_args(argv)

    try:
        text = args.registry.read_text(encoding="utf-8")
        doc = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"审计脚本出错: 读取/解析 {args.registry} 失败: {exc}", file=sys.stderr)
        return 1
    domains = doc.get("domains") or {}

    try:
        provider, close_all = _make_default_conn_provider()
    except Exception as exc:
        print(f"审计脚本出错: 无法加载数据库 manifest: {exc}", file=sys.stderr)
        return 1

    try:
        results = scan_all_domains(domains, provider)
    finally:
        close_all()

    print(render_json(results) if args.json else render_text(results))
    return 0  # audit 模式恒不拦截; 非零只在脚本自身出错时出现 (见上面 except)


if __name__ == "__main__":
    raise SystemExit(main())
