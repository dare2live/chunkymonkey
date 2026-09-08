"""check_db_invariants — DuckDB 只读数据不变量常驻审计门 (S4, 2026-09-08)。

唯一真相源 = fable S4 判据设计 (db_invariants.yaml §0-§7 逐条裁决); 本文件只负责
「怎么跑」——加载 backend/config/db_invariants.yaml、逐条独立开只读连接、比对、判三态、
决定退出码。YAML 本身只描述「查什么、比什么」，不出现 kind/type/callable/script/timeout/
depends_on 任一键 (出现即 plugin bus, CLAUDE.md 红线 11)。

三态 (不是二态)：
    PASS       — sql 返回 (checked, value)，checked>0 (或 allow_empty), value 非空，
                 且 value 与 expect 按 op 比较成立。
    FAIL       — 同上但比较不成立。
    UNVERIFIED — 查不了本身 (库不可达/写锁/超时/sql 返回列名不对/value 为 NULL/
                 checked==0 且未声明 allow_empty)。空对账不算过 (R1)——这是本门存在
                 的核心原因: 空表绿、NULLIF 出 NULL 绿、TRUNCATE 后绿, 全部必须显式
                 UNVERIFIED, 不能悄悄折叠进 PASS。

退出码 (R5, 三态必须可区分，UNVERIFIED 绝不返回 0):
    0 = 全 PASS
    1 = 至少一条 FAIL (数据不变量被违反)
    2 = 配置错 (db_invariants.yaml 加载失败) 或脚本自身崩溃
    3 = 无 FAIL 但至少一条 UNVERIFIED (查不了; 日更应 degraded, 不是绿灯)

用法:
    PYTHONPATH=backend python backend/scripts/check_db_invariants.py            # 人读输出
    ... --json                                                                  # 机器读 (stdout)
    ... --json-out data/audit/db_invariants_20260908.json                       # 落证据文件
    ... --alert-flag /tmp/chunkymonkey_ALERT_db_invariants.flag                 # 非 PASS 写, PASS 自愈删
    ... --timeout 60                                                            # 单条 SQL 超时秒数 (默认 120)
    ... --db-override reference=/tmp/scratch/reference.duckdb                   # 验收/演练用路径覆盖,
        (只在这里注入, 不进 YAML — R2 "注入打在生效点"；可重复, 对 db 与 attach 目标
        alias 都生效)

接线状态由 governance_gates.yaml runtime_checks 与 backend/tests/scripts/
test_check_db_invariants.py 验证，不在本文档字符串声称。
"""
from __future__ import annotations

import argparse
import json
import operator
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "backend" / "config" / "db_invariants.yaml"

DEFAULT_TIMEOUT_S = 120.0

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_REQUIRED_KEYS = {
    "id", "db", "invariant", "sql", "op", "expect", "allow_empty", "why", "fix", "kill_when",
}
_OPTIONAL_KEYS = {"attach"}
_ALLOWED_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS
# 出现任一 = plugin bus / 通用 DAG / YAML DSL 的开头 (红线 11)，加载期直接拒绝。
_BANNED_KEYS = {"kind", "type", "callable", "script", "timeout", "depends_on"}
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


class DbInvariantsConfigError(RuntimeError):
    """db_invariants.yaml 加载期错误 (fail-closed) —— 缺失/键集不对/别名未知都在此报。"""


# ── 加载 (fail-closed, 照 services/governance_gates.py 的形状) ────────────────────

def _manifest():
    from services.database_manifest import get_database_manifest

    return get_database_manifest()


def _require_alias(alias: Any, what: str) -> str:
    if not isinstance(alias, str) or not alias.strip():
        raise DbInvariantsConfigError(f"{what} must be a non-empty string")
    try:
        _manifest().require(alias)
    except KeyError as exc:
        raise DbInvariantsConfigError(f"{what} unknown database alias: {alias!r}") from exc
    return alias


def _require_nonempty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DbInvariantsConfigError(f"{what} must be a non-empty string")
    return value


def _parse_entry(index: int, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DbInvariantsConfigError(f"invariants[{index}] must be a mapping")

    keys = set(raw)
    banned_hit = keys & _BANNED_KEYS
    if banned_hit:
        raise DbInvariantsConfigError(
            f"invariants[{index}] 含禁用键 {sorted(banned_hit)} —— 本表只描述 SQL 比较, "
            "kind/type/callable/script/timeout/depends_on 任一键出现即 plugin bus "
            "(CLAUDE.md 红线 11)"
        )
    extra = keys - _ALLOWED_KEYS
    if extra:
        raise DbInvariantsConfigError(f"invariants[{index}] 含未知键: {sorted(extra)}")
    missing = _REQUIRED_KEYS - keys
    if missing:
        raise DbInvariantsConfigError(f"invariants[{index}] 缺键: {sorted(missing)}")

    entry_id = raw["id"]
    if not isinstance(entry_id, str) or not _ID_RE.match(entry_id):
        raise DbInvariantsConfigError(
            f"invariants[{index}].id 必须匹配 ^[a-z0-9_]+$: {entry_id!r}"
        )

    db = _require_alias(raw["db"], f"invariants[{entry_id}].db")

    attach_raw = raw.get("attach") or {}
    if not isinstance(attach_raw, dict):
        raise DbInvariantsConfigError(f"invariants[{entry_id}].attach must be a mapping")
    attach: dict[str, str] = {}
    for sql_alias, target in attach_raw.items():
        if not isinstance(sql_alias, str) or not sql_alias.strip():
            raise DbInvariantsConfigError(
                f"invariants[{entry_id}].attach key must be a non-empty string"
            )
        attach[sql_alias] = _require_alias(
            target, f"invariants[{entry_id}].attach[{sql_alias!r}]"
        )

    invariant = _require_nonempty_str(raw["invariant"], f"invariants[{entry_id}].invariant")
    sql = _require_nonempty_str(raw["sql"], f"invariants[{entry_id}].sql")

    op = raw["op"]
    if op not in _OPS:
        raise DbInvariantsConfigError(
            f"invariants[{entry_id}].op must be one of {sorted(_OPS)}: {op!r}"
        )

    expect = raw["expect"]
    if isinstance(expect, bool) or not isinstance(expect, (int, float, str)):
        raise DbInvariantsConfigError(
            f"invariants[{entry_id}].expect must be int/float/str: {expect!r}"
        )

    allow_empty = raw["allow_empty"]
    if not isinstance(allow_empty, bool):
        raise DbInvariantsConfigError(f"invariants[{entry_id}].allow_empty must be a bool")

    why = _require_nonempty_str(raw["why"], f"invariants[{entry_id}].why")
    fix = _require_nonempty_str(raw["fix"], f"invariants[{entry_id}].fix")
    kill_when = _require_nonempty_str(raw["kill_when"], f"invariants[{entry_id}].kill_when")

    return {
        "id": entry_id,
        "db": db,
        "attach": attach,
        "invariant": invariant.strip(),
        "sql": sql,
        "op": op,
        "expect": expect,
        "allow_empty": allow_empty,
        "why": why.strip(),
        "fix": fix.strip(),
        "kill_when": kill_when.strip(),
    }


def load_db_invariants(path: Path | None = None) -> list[dict[str, Any]]:
    """加载并校验 db_invariants.yaml；任何结构问题 fail-closed 抛 DbInvariantsConfigError。"""
    p = path or CONFIG_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise DbInvariantsConfigError(f"missing db_invariants registry: {p}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DbInvariantsConfigError(f"unreadable db_invariants registry: {exc}") from exc
    if not isinstance(raw, dict):
        raise DbInvariantsConfigError("db_invariants root must be a mapping")
    if raw.get("version") != 1:
        raise DbInvariantsConfigError("db_invariants version must be 1")
    rows = raw.get("invariants")
    if not isinstance(rows, list) or not rows:
        raise DbInvariantsConfigError("invariants must be a non-empty list")

    specs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        spec = _parse_entry(index, row)
        if spec["id"] in seen_ids:
            raise DbInvariantsConfigError(f"duplicate invariants id: {spec['id']!r}")
        seen_ids.add(spec["id"])
        specs.append(spec)
    return specs


# ── 执行 (R1 空对账不算过 / R5 三态可区分) ─────────────────────────────────────────

def _execute_with_timeout(conn, sql: str, timeout_s: float) -> dict[str, Any]:
    """在子线程跑一条 SQL；超时用 conn.raw.interrupt() 打断 (duckdb 查询会跨线程响应中断)。

    返回三种形态之一: {"cols": [...], "row": Row|None} / {"timeout": True} / {"error": exc}。
    """
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            row = cur.fetchone()
            box["cols"] = cols
            box["row"] = row
        except Exception as exc:  # noqa: BLE001 — 跨线程转交给主线程判定
            box["error"] = exc

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        try:
            conn.raw.interrupt()
        except Exception:  # noqa: BLE001 — interrupt 本身失败也不掩盖 timeout 判定
            pass
        t.join(5.0)
        return {"timeout": True}
    return box


def evaluate_spec(
    spec: dict[str, Any], conn, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> dict[str, Any]:
    """跑单条 invariant 的 sql，按 R1 判 PASS/FAIL/UNVERIFIED。不抛异常 (异常已转成 UNVERIFIED)。"""
    start = time.monotonic()
    out: dict[str, Any] = {
        "id": spec["id"],
        "db": spec["db"],
        "checked": None,
        "value": None,
        "expect": spec["expect"],
        "op": spec["op"],
    }

    def _finish(status: str, reason: str | None = None) -> dict[str, Any]:
        out["status"] = status
        out["elapsed_s"] = round(time.monotonic() - start, 6)
        if reason is not None:
            out["reason"] = reason
        return out

    try:
        outcome = _execute_with_timeout(conn, spec["sql"], timeout_s)
    except Exception as exc:  # noqa: BLE001 — 防御性兜底; _execute_with_timeout 内部已捕获
        return _finish("UNVERIFIED", f"{type(exc).__name__}: {str(exc)[:120]}")

    if outcome.get("timeout"):
        return _finish("UNVERIFIED", "timeout")
    if "error" in outcome:
        exc = outcome["error"]
        return _finish("UNVERIFIED", f"{type(exc).__name__}: {str(exc)[:120]}")

    cols = outcome.get("cols") or []
    row = outcome.get("row")
    if set(cols) != {"checked", "value"} or row is None:
        return _finish("UNVERIFIED", "sql_shape")

    checked = row["checked"]
    value = row["value"]
    out["checked"] = checked
    out["value"] = value

    if value is None:
        return _finish("UNVERIFIED", "null_value")
    if (checked is None or checked == 0) and not spec["allow_empty"]:
        return _finish("UNVERIFIED", "empty_population")

    try:
        passed = bool(_OPS[spec["op"]](value, spec["expect"]))
    except TypeError as exc:
        return _finish("UNVERIFIED", f"incomparable: {str(exc)[:120]}")

    return _finish("PASS" if passed else "FAIL")


def run_invariants(
    specs: list[dict[str, Any]],
    conn_for: Callable[[str, dict[str, str]], Any],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """逐条独立开连接跑 (R2 形状同 check_grain_uniqueness.run_checks)。

    ``conn_for(db_alias, attach_map)`` 可注入 —— 单测传内存库 (attach_map 通常被测试忽略,
    因为每个红/绿用例只关心一条 spec)；生产传 ``_default_conn_for_factory(...)`` 的闭包，
    内部按 database_manifest 解析路径并调用 audit_connect (只读, 3 秒锁等待)。
    """
    results: list[dict[str, Any]] = []
    for spec in specs:
        start = time.monotonic()
        try:
            conn = conn_for(spec["db"], spec.get("attach") or {})
        except Exception as exc:  # noqa: BLE001 — 库不可达 / 写锁占用 / ATTACH 失败
            results.append(
                {
                    "id": spec["id"],
                    "db": spec["db"],
                    "status": "UNVERIFIED",
                    "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                    "checked": None,
                    "value": None,
                    "expect": spec["expect"],
                    "op": spec["op"],
                    "elapsed_s": round(time.monotonic() - start, 6),
                }
            )
            continue
        try:
            results.append(evaluate_spec(spec, conn, timeout_s=timeout_s))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return results


def overall_status(results: list[dict[str, Any]]) -> str:
    if any(r["status"] == "FAIL" for r in results):
        return "FAIL"
    if any(r["status"] == "UNVERIFIED" for r in results):
        return "UNVERIFIED"
    return "PASS"


def exit_code_for(overall: str) -> int:
    """R5: 0=全 PASS / 1=有 FAIL / 3=无 FAIL 但有 UNVERIFIED。UNVERIFIED 绝不返回 0。"""
    return {"PASS": 0, "FAIL": 1, "UNVERIFIED": 3}[overall]


def write_alert_flag(flag_path: Path, overall: str, results: list[dict[str, Any]]) -> None:
    """非 PASS (FAIL 或 UNVERIFIED, 两者都不是 PASS) 落 flag；PASS 时自愈删除。"""
    if overall == "PASS":
        flag_path.unlink(missing_ok=True)
        return
    lines = [f"[{datetime.now().strftime('%F %T')}] db_invariants 非 PASS: overall={overall}"]
    for r in results:
        if r["status"] == "PASS":
            continue
        detail = (
            f"  [{r['status']}] {r['id']} (db={r['db']}) checked={r['checked']} "
            f"value={r['value']} {r['op']} expect={r['expect']!r}"
        )
        if r.get("reason"):
            detail += f" reason={r['reason']}"
        lines.append(detail)
    flag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── 生产入口 ───────────────────────────────────────────────────────────────────

def _default_conn_for_factory(
    overrides: dict[str, str],
) -> Callable[[str, dict[str, str]], Any]:
    from services.duck_adapter import audit_connect

    def _conn_for(db_alias: str, attach: dict[str, str]):
        manifest = _manifest()
        path = overrides.get(db_alias) or str(manifest.path_for(db_alias))
        attach_map: dict[str, Any] = {}
        for sql_alias, target_alias in attach.items():
            target_path = overrides.get(target_alias) or str(manifest.path_for(target_alias))
            attach_map[sql_alias] = {"path": target_path, "read_only": True}
        return audit_connect(path, attach=attach_map or None)

    return _conn_for


def _parse_db_overrides(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise DbInvariantsConfigError(f"--db-override 格式错: {item!r} (需 alias=path)")
        alias, path = item.split("=", 1)
        alias = alias.strip()
        if not alias or not path.strip():
            raise DbInvariantsConfigError(f"--db-override 格式错: {item!r} (alias/path 不能为空)")
        out[alias] = path.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="DuckDB 只读数据不变量常驻审计门 (三态 PASS/FAIL/UNVERIFIED, 只读不写)"
    )
    ap.add_argument("--json", action="store_true", help="JSON 输出到 stdout")
    ap.add_argument("--json-out", default=None, help="JSON 结果写文件 (证据留档)")
    ap.add_argument(
        "--alert-flag", default=None, help="非 PASS 时写告警 flag 文件, PASS 时自愈删除"
    )
    ap.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S,
        help=f"单条 SQL 超时秒数 (默认 {DEFAULT_TIMEOUT_S})",
    )
    ap.add_argument(
        "--db-override", action="append", default=[],
        help="alias=path 覆盖某个 database_manifest 别名的物理路径 "
             "(验收/演练用, 不进 YAML; 对 db 与 attach 目标 alias 都生效; 可重复)",
    )
    args = ap.parse_args(argv)

    try:
        specs = load_db_invariants()
        overrides = _parse_db_overrides(args.db_override)
    except DbInvariantsConfigError as exc:
        print(f"[db_invariants] CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2

    conn_for = _default_conn_for_factory(overrides)
    try:
        results = run_invariants(specs, conn_for, timeout_s=args.timeout)
    except Exception as exc:  # noqa: BLE001 — 崩溃归 2, 不伪装成某条 UNVERIFIED
        print(f"[db_invariants] CRASH: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    overall = overall_status(results)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "UNVERIFIED")}
    payload = {"overall": overall, "results": results, "summary": counts}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    else:
        for r in results:
            if r["status"] == "PASS":
                continue
            line = (
                f"[{r['status']}] {r['id']} (db={r['db']}) checked={r['checked']} "
                f"value={r['value']} {r['op']} expect={r['expect']!r}"
            )
            if r.get("reason"):
                line += f" reason={r['reason']}"
            print(line)
        print(
            f"db-invariants: overall={overall} pass={counts['PASS']} fail={counts['FAIL']} "
            f"unverified={counts['UNVERIFIED']} (of {len(results)})"
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
    if args.alert_flag:
        write_alert_flag(Path(args.alert_flag), overall, results)

    return exit_code_for(overall)


if __name__ == "__main__":
    raise SystemExit(main())
