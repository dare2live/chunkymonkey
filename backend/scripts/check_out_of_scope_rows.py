"""check_out_of_scope_rows — 范围外证券类别 (当前=B 股) 全库运行时不变量 (S3, 2026-09-12)。

背景: 业主 2026-09-12 裁定「B 股不在本项目范围, 以后也不做, 获取完的数据删除清理干净」
(scratchpad b_share_exclusion_r1.md §0/§1.4, R2 解读: 删一次 + 有轴的不再请求 + 无轴的
raw 允许再现但任何发布面 0 行; 唯一硬不变量是"全库任何时刻扫到的范围外行数 == 0")。

判据 (一句话, r1 §4.1): 对 manifest 里每个活库、``information_schema.columns`` 里
列名 ∈ ``security_code_columns`` 的每一列, 匹配任一登记类别 ``code_patterns`` 的行数
必须 == 0; 唯一例外是 ``landing_exemptions`` 登记的 (no_vendor_axis 域的 landing/raw)
表——那类表批次哈希按行重算, 按行删除在结构上不可行, 只免它自己不判 FAIL, 仍然报数
(不是 warn-nothing); 它的任何派生表不豁免, 照判 FAIL。

发现式枚举 (r1 §4.1, 与 services/lineage/builder.py::_live_tables_by_db 同一原则:
"手写一张存放点清单, 换个地方长出同一个病"): 表和列都从活库的 information_schema
现查, 不读任何手写表清单。已实测的反例——手工列 15 张表会漏
raw_tushare_dc_member.con_code 与 fact_dc_member_daily.con_code 各 10,596 行,
正是发现式枚举要堵的洞。

范围外类别 vs 股票池过滤 —— 两者绝不能合并 (r1 §2.3 C3, 头注复述防止将来有人把
股票池过滤搬进本检查):
  - 股票池 (universe_rules.yaml): 有版本号 (policy_version), 只在项目输出面
    (serve/发布) 执法, 收紧/放宽是一次要过 population_contract 门的可见政策变更。
  - 范围外类别 (本检查守的对象): 无版本号, 是业主对"这个类别永不做"的事实裁决,
    在所有表 (含 raw) 上以 0 行执法。伪装股票池条目进 vendor_scope 会立刻在 raw 上
    报 FAIL (raw 里本来就有北交所/可转债这类股票池排除但未裁定范围外的行), 是自曝
    不是绕过——这也是本检查绝不能读 universe_rules.yaml 的原因: 读了就会把两者混同。

为什么它不会"按构造永远不红" (CLAUDE.md 反馈 feedback-gate-asks-different-question-
than-it-guards.md 点名的 grain 唯一性门反例——那道门扫的是去重之后的表, 而它想守的
伤害发生在去重之前, 机制把证据销毁了): 这里相反。本检查想守的是"排除机制是否生效",
排除失败的**后果**就是范围外行落进表里, 而本检查扫的正是那张表——机制失效**制造**
证据, 不是销毁证据。具体地: 供应商静默忽略请求过滤 → 行落地 → 本检查在下一次运行时
就会扫到并 FAIL; 有人把 no_vendor_axis 域的发布路径过滤删掉 → 派生表长出范围外行 →
FAIL (派生表从不在 landing_exemptions 里); 有人误删本文件全部 classes → loader
fail-closed (见下), 退出码 2, 不会悄悄放行。

三态 + 退出码 (照抄 backend/scripts/check_db_invariants.py, R5 三态不许 UNVERIFIED
返回 0):
    PASS       — 该 (db, table, column, class) 组合命中行数 == 0。
    FAIL       — 命中行数 > 0 且该 (db, table) 不在 landing_exemptions 里。
    observed   — 命中行数 > 0 但该 (db, table) 在 landing_exemptions 里 (只报数不算 FAIL,
                 JSON 里数字仍然可见——区分于 warn-nothing)。
    UNVERIFIED — 库不可达 (缺文件/写锁/其它连接异常) / 该库可达但枚举到 0 个
                 (table, column) 组合而库里确实有 ≥1 张表 (列名集合坏了或枚举本身失效,
                 不许悄悄绿) / 单条查询本身出错 (如 CAST 失败)。

退出码:
    0 = 全部 PASS (或只有 observed, 无 FAIL 无 UNVERIFIED)
    1 = 至少一条 FAIL
    2 = 配置错 (out_of_scope_scan.yaml 加载失败, 含 classes 清空——见 kill_when) 或脚本崩溃
    3 = 无 FAIL 但至少一条 UNVERIFIED

死亡条件 (r1 §4.4): 守谁 = 数据/发布面 (业主裁决"B 股不在本项目")。守什么 = 上面那句
判据。对象消失时怎么死: ``out_of_scope_scan.yaml`` 的 ``classes`` 一旦清空 (业主某天
裁决"范围外类别名单本身不再存在", 而不是"这个类别的行清干净了"——后者不改 classes,
只是检查会一直 PASS), loader 直接 fail-closed 拒绝加载, 本脚本退出 2, 日更 degraded,
逼着删掉本文件与 governance_gates.yaml 里的登记, 不留一道空转的门。
``kill_when: out_of_scope_scan.yaml classes 清空时随之删``。

用法:
    PYTHONPATH=backend python backend/scripts/check_out_of_scope_rows.py            # 人读输出
    ... --json                                                                       # 机器读 (stdout)
    ... --json-out data/audit/out_of_scope_rows_20260912.json                        # 落证据文件
    ... --alert-flag /tmp/chunkymonkey_ALERT_out_of_scope_rows.flag                   # 非 PASS 写
    ... --db-override tushare_raw=/tmp/scratch/tushare_raw.duckdb                    # 验收/演练用
        路径覆盖 (只在这里注入, 不进 YAML; 可重复)

接线状态由 backend/config/governance_gates.yaml runtime_checks 与
backend/tests/scripts/test_check_out_of_scope_rows.py 验证, 不在本文档字符串声称。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "backend" / "config" / "out_of_scope_scan.yaml"

_TOP_KEYS = {"version", "security_code_columns", "classes", "landing_exemptions"}
_CLASS_KEYS = {"ruling", "code_patterns"}
_PATTERN_KEYS = {"prefix", "suffix"}
_EXEMPTION_KEYS = {"db", "table", "why"}
_ALLOWED_SUFFIXES = {"SH", "SZ", "BJ"}

_COL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CLASS_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PREFIX_RE = re.compile(r"^\d{2,3}$")
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OutOfScopeScanConfigError(RuntimeError):
    """out_of_scope_scan.yaml 加载期错误 (fail-closed) —— 缺失/键集不对/悬空别名都在此报。"""


@dataclass(frozen=True)
class CodePattern:
    prefix: str
    suffix: str


@dataclass(frozen=True)
class OutOfScopeClass:
    name: str
    ruling: str
    code_patterns: tuple[CodePattern, ...]
    regex: str  # 由 code_patterns 生成, 供 DuckDB regexp_matches 直接使用


@dataclass(frozen=True)
class ScanConfig:
    security_code_columns: tuple[str, ...]
    classes: tuple[OutOfScopeClass, ...]
    landing_exemptions: frozenset[tuple[str, str]]  # {(db_alias, table_name), ...}


# ── 加载 (fail-closed, 照 check_db_invariants.py 的形状) ──────────────────────────

def _manifest():
    from services.database_manifest import get_database_manifest

    return get_database_manifest()


def _pattern_regex(prefix: str, suffix: str) -> str:
    """900/SH -> r'900\\d{3}(\\.SH)?' —— 锚定总长 6 位, 后缀可选但若出现必须匹配。"""
    digits = 6 - len(prefix)
    return rf"{prefix}\d{{{digits}}}(\.{suffix})?"


def _parse_class(name: str, raw: Any) -> OutOfScopeClass:
    if not _CLASS_NAME_RE.match(name):
        raise OutOfScopeScanConfigError(f"classes 键必须匹配 ^[a-z][a-z0-9_]*$: {name!r}")
    if not isinstance(raw, dict):
        raise OutOfScopeScanConfigError(f"classes.{name} must be a mapping")
    keys = set(raw)
    if keys != _CLASS_KEYS:
        raise OutOfScopeScanConfigError(
            f"classes.{name} 键集合必须恰为 {sorted(_CLASS_KEYS)}: {sorted(keys)}"
        )
    ruling = raw["ruling"]
    if not isinstance(ruling, str) or not ruling.strip():
        raise OutOfScopeScanConfigError(f"classes.{name}.ruling must be a non-empty string")

    patterns_raw = raw["code_patterns"]
    if not isinstance(patterns_raw, list) or not patterns_raw:
        raise OutOfScopeScanConfigError(f"classes.{name}.code_patterns must be a non-empty list")

    seen: set[tuple[str, str]] = set()
    patterns: list[CodePattern] = []
    regexes: list[str] = []
    for i, p in enumerate(patterns_raw):
        if not isinstance(p, dict) or set(p) != _PATTERN_KEYS:
            raise OutOfScopeScanConfigError(
                f"classes.{name}.code_patterns[{i}] 键集合必须恰为 {sorted(_PATTERN_KEYS)}"
            )
        prefix, suffix = p["prefix"], p["suffix"]
        if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
            raise OutOfScopeScanConfigError(
                f"classes.{name}.code_patterns[{i}].prefix must match ^\\d{{2,3}}$: {prefix!r}"
            )
        if suffix not in _ALLOWED_SUFFIXES:
            raise OutOfScopeScanConfigError(
                f"classes.{name}.code_patterns[{i}].suffix must be one of "
                f"{sorted(_ALLOWED_SUFFIXES)}: {suffix!r}"
            )
        key = (prefix, suffix)
        if key in seen:
            raise OutOfScopeScanConfigError(
                f"classes.{name}.code_patterns duplicate (prefix, suffix): {key}"
            )
        seen.add(key)
        patterns.append(CodePattern(prefix=prefix, suffix=suffix))
        regexes.append(_pattern_regex(prefix, suffix))

    regex = "^(" + "|".join(sorted(regexes)) + ")$"
    return OutOfScopeClass(name=name, ruling=ruling.strip(), code_patterns=tuple(patterns), regex=regex)


def _parse_exemption(index: int, raw: Any, manifest: Any) -> tuple[str, str]:
    if not isinstance(raw, dict) or set(raw) != _EXEMPTION_KEYS:
        raise OutOfScopeScanConfigError(
            f"landing_exemptions[{index}] 键集合必须恰为 {sorted(_EXEMPTION_KEYS)}"
        )
    db, table, why = raw["db"], raw["table"], raw["why"]
    if not isinstance(db, str) or not db.strip():
        raise OutOfScopeScanConfigError(f"landing_exemptions[{index}].db must be a non-empty string")
    try:
        manifest.require(db)
    except KeyError as exc:
        raise OutOfScopeScanConfigError(
            f"landing_exemptions[{index}].db unknown database alias: {db!r}"
        ) from exc
    if not isinstance(table, str) or not _TABLE_NAME_RE.match(table):
        raise OutOfScopeScanConfigError(
            f"landing_exemptions[{index}].table must match ^[A-Za-z_][A-Za-z0-9_]*$: {table!r}"
        )
    if not isinstance(why, str) or not why.strip():
        raise OutOfScopeScanConfigError(f"landing_exemptions[{index}].why must be a non-empty string")
    return (db, table)


def load_scan_config(path: Path | None = None) -> ScanConfig:
    """加载并校验 out_of_scope_scan.yaml；任何结构问题 fail-closed 抛 OutOfScopeScanConfigError。"""
    p = path or CONFIG_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise OutOfScopeScanConfigError(f"missing out_of_scope_scan registry: {p}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise OutOfScopeScanConfigError(f"unreadable out_of_scope_scan registry: {exc}") from exc
    if not isinstance(raw, dict):
        raise OutOfScopeScanConfigError("out_of_scope_scan root must be a mapping")
    keys = set(raw)
    if keys != _TOP_KEYS:
        raise OutOfScopeScanConfigError(
            f"out_of_scope_scan 顶层键必须恰为 {sorted(_TOP_KEYS)}: {sorted(keys)}"
        )
    if raw.get("version") != 1:
        raise OutOfScopeScanConfigError("out_of_scope_scan version must be 1")

    cols_raw = raw.get("security_code_columns")
    if not isinstance(cols_raw, list) or not cols_raw:
        raise OutOfScopeScanConfigError("security_code_columns must be a non-empty list")
    columns: list[str] = []
    seen_cols: set[str] = set()
    for c in cols_raw:
        if not isinstance(c, str) or not _COL_NAME_RE.match(c):
            raise OutOfScopeScanConfigError(
                f"security_code_columns 每项须匹配 ^[a-z][a-z0-9_]*$: {c!r}"
            )
        if c in seen_cols:
            raise OutOfScopeScanConfigError(f"security_code_columns duplicate entry: {c!r}")
        seen_cols.add(c)
        columns.append(c)

    # classes 为空 = 本机制守护的对象 (范围外类别名单) 整体消失, 是 §4.4 死亡条件的
    # 触发点, 不是"今天没有范围外类别"的正常状态——不存在的合法状态是删掉本文件与
    # governance_gates.yaml 的登记, 不是留一份空 classes 的文件。
    classes_raw = raw.get("classes")
    if not isinstance(classes_raw, dict) or not classes_raw:
        raise OutOfScopeScanConfigError(
            "classes must be a non-empty mapping (为空 = 范围外类别名单整体消失, "
            "应删除 out_of_scope_scan.yaml 与 governance_gates.yaml 里的登记, 不是留空文件)"
        )
    classes = tuple(_parse_class(name, body) for name, body in classes_raw.items())

    exemptions_raw = raw.get("landing_exemptions")
    if not isinstance(exemptions_raw, list):
        raise OutOfScopeScanConfigError("landing_exemptions must be a list (可以为空列表)")
    manifest = _manifest()
    seen_exempt: set[tuple[str, str]] = set()
    for i, item in enumerate(exemptions_raw):
        key = _parse_exemption(i, item, manifest)
        if key in seen_exempt:
            raise OutOfScopeScanConfigError(f"landing_exemptions duplicate (db, table): {key}")
        seen_exempt.add(key)

    return ScanConfig(
        security_code_columns=tuple(columns),
        classes=classes,
        landing_exemptions=frozenset(seen_exempt),
    )


# ── 发现式枚举 + 扫描 ──────────────────────────────────────────────────────────────

def _count_base_tables(conn) -> int:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main' AND table_type='BASE TABLE'"
    ).fetchall()
    # 排除 _ 前缀瞬态表 (pipeline_lock 的锁探针, 建/即删), 与
    # services/lineage/builder.py::_live_tables_by_db 同一惯例。
    return sum(1 for r in rows if not str(r[0]).startswith("_"))


def _discover_columns(conn, security_code_columns: tuple[str, ...]) -> dict[str, list[str]]:
    """table_name -> 匹配到的代码列名列表 (information_schema 现查, 不读任何手写表清单)。"""
    wanted = set(security_code_columns)
    rows = conn.execute(
        "SELECT c.table_name, c.column_name FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema='main' AND t.table_type='BASE TABLE' "
        "ORDER BY c.table_name, c.column_name"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for table_name, column_name in rows:
        if str(table_name).startswith("_"):
            continue
        if str(column_name).lower() in wanted:
            out.setdefault(table_name, []).append(column_name)
    return out


def _unverified_row(db_alias: str, *, reason: str, table=None, column=None, cls=None) -> dict[str, Any]:
    return {
        "db": db_alias, "table": table, "column": column, "class": cls,
        "checked": None, "value": None, "status": "UNVERIFIED", "reason": reason,
    }


def _scan_one(conn, db_alias: str, table: str, column: str, cls: OutOfScopeClass,
              exemptions: frozenset[tuple[str, str]]) -> dict[str, Any]:
    try:
        row = conn.execute(
            f'SELECT count(*) AS checked, '
            f'count(*) FILTER (WHERE regexp_matches(CAST("{column}" AS VARCHAR), ?)) AS value '
            f'FROM "{table}"',
            [cls.regex],
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — 单条查询坏了不拖垮整个扫描, 转 UNVERIFIED
        return _unverified_row(
            db_alias, table=table, column=column, cls=cls.name,
            reason=f"{type(exc).__name__}: {str(exc)[:120]}",
        )

    checked, value = row[0], row[1]
    is_exempt = (db_alias, table) in exemptions
    if value:
        status = "observed" if is_exempt else "FAIL"
    else:
        status = "PASS"
    return {
        "db": db_alias, "table": table, "column": column, "class": cls.name,
        "checked": checked, "value": value, "status": status,
    }


def scan_database(conn, db_alias: str, config: ScanConfig) -> list[dict[str, Any]]:
    """单库扫描: 发现式枚举 (table, column) 再逐个 class 判定。不抛异常。"""
    total_tables = _count_base_tables(conn)
    discovered = _discover_columns(conn, config.security_code_columns)
    if total_tables > 0 and not discovered:
        # 库里确实有表, 但一个代码列都没发现——列名集合坏了或枚举本身失效,
        # 绝不能悄悄当 0 命中 PASS (CLAUDE.md 红线3: 缺失只能传播为缺失)。
        return [_unverified_row(db_alias, reason="enumeration_empty")]

    rows: list[dict[str, Any]] = []
    for table in sorted(discovered):
        for column in discovered[table]:
            for cls in config.classes:
                rows.append(_scan_one(conn, db_alias, table, column, cls, config.landing_exemptions))
    return rows


def run_scan(
    config: ScanConfig,
    conn_for: Callable[[str], Any],
    databases: Sequence[str],
) -> list[dict[str, Any]]:
    """逐库独立开连接扫描 (形状同 check_db_invariants.run_invariants)。

    ``conn_for(db_alias)`` 可注入——单测传内存/临时文件连接, 生产传按
    database_manifest 解析路径 + audit_connect (只读, 短锁等待) 的闭包。
    """
    rows: list[dict[str, Any]] = []
    for alias in databases:
        try:
            conn = conn_for(alias)
        except Exception as exc:  # noqa: BLE001 — 库不可达 / 写锁占用 / 缺文件
            rows.append(_unverified_row(alias, reason=f"{type(exc).__name__}: {str(exc)[:120]}"))
            continue
        try:
            rows.extend(scan_database(conn, alias, config))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return rows


def overall_status(rows: list[dict[str, Any]]) -> str:
    if any(r["status"] == "FAIL" for r in rows):
        return "FAIL"
    if any(r["status"] == "UNVERIFIED" for r in rows):
        return "UNVERIFIED"
    return "PASS"


def exit_code_for(overall: str) -> int:
    """R5 同款: 0=全 PASS(含 observed) / 1=有 FAIL / 3=无 FAIL 但有 UNVERIFIED。"""
    return {"PASS": 0, "FAIL": 1, "UNVERIFIED": 3}[overall]


def write_alert_flag(flag_path: Path, overall: str, rows: list[dict[str, Any]]) -> None:
    if overall == "PASS":
        flag_path.unlink(missing_ok=True)
        return
    lines = [f"[{datetime.now().strftime('%F %T')}] out_of_scope_rows 非 PASS: overall={overall}"]
    for r in rows:
        if r["status"] in ("PASS", "observed"):
            continue
        detail = (
            f"  [{r['status']}] db={r['db']} table={r['table']} column={r['column']} "
            f"class={r['class']} checked={r['checked']} value={r['value']}"
        )
        if r.get("reason"):
            detail += f" reason={r['reason']}"
        lines.append(detail)
    flag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── 生产入口 ───────────────────────────────────────────────────────────────────

def _default_conn_for_factory(manifest, overrides: dict[str, str]) -> Callable[[str], Any]:
    from services.duck_adapter import audit_connect

    def _conn_for(db_alias: str):
        path = overrides.get(db_alias) or str(manifest.path_for(db_alias))
        return audit_connect(path)

    return _conn_for


def _parse_db_overrides(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise OutOfScopeScanConfigError(f"--db-override 格式错: {item!r} (需 alias=path)")
        alias, path = item.split("=", 1)
        alias = alias.strip()
        if not alias or not path.strip():
            raise OutOfScopeScanConfigError(f"--db-override 格式错: {item!r} (alias/path 不能为空)")
        out[alias] = path.strip()
    return out


def _live_database_aliases(manifest) -> list[str]:
    """发现式: 扫 manifest 里全部 online 库, 不是手写别名清单。"""
    return [alias for alias, spec in sorted(manifest.databases.items()) if spec.online]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="范围外证券类别 (B 股) 全库运行时不变量 (三态 PASS/FAIL/UNVERIFIED, 只读不写)"
    )
    ap.add_argument("--json", action="store_true", help="JSON 输出到 stdout")
    ap.add_argument("--json-out", default=None, help="JSON 结果写文件 (证据留档)")
    ap.add_argument(
        "--alert-flag", default=None, help="非 PASS 时写告警 flag 文件, PASS 时自愈删除"
    )
    ap.add_argument(
        "--db-override", action="append", default=[],
        help="alias=path 覆盖某个 database_manifest 别名的物理路径 "
             "(验收/演练用, 不进 YAML; 可重复)",
    )
    args = ap.parse_args(argv)

    try:
        config = load_scan_config()
        manifest = _manifest()
        overrides = _parse_db_overrides(args.db_override)
    except OutOfScopeScanConfigError as exc:
        print(f"[out_of_scope_rows] CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2

    databases = _live_database_aliases(manifest)
    conn_for = _default_conn_for_factory(manifest, overrides)
    try:
        rows = run_scan(config, conn_for, databases)
    except Exception as exc:  # noqa: BLE001 — 崩溃归 2, 不伪装成某条 UNVERIFIED
        print(f"[out_of_scope_rows] CRASH: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    overall = overall_status(rows)
    counts = {
        s: sum(1 for r in rows if r["status"] == s)
        for s in ("PASS", "FAIL", "UNVERIFIED", "observed")
    }
    payload = {"overall": overall, "rows": rows, "summary": counts}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    else:
        for r in rows:
            if r["status"] == "PASS":
                continue
            line = (
                f"[{r['status']}] db={r['db']} table={r['table']} column={r['column']} "
                f"class={r['class']} checked={r['checked']} value={r['value']}"
            )
            if r.get("reason"):
                line += f" reason={r['reason']}"
            print(line)
        print(
            f"out-of-scope-rows: overall={overall} pass={counts['PASS']} fail={counts['FAIL']} "
            f"unverified={counts['UNVERIFIED']} observed={counts['observed']} (of {len(rows)})"
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    if args.alert_flag:
        write_alert_flag(Path(args.alert_flag), overall, rows)

    return exit_code_for(overall)


if __name__ == "__main__":
    raise SystemExit(main())
