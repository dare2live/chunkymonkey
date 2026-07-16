"""check_grain_uniqueness — grain 声明持续审计门 (R1 根因1 机械门, 2026-07-03).

owner=docs/engineering_governance.md；历史根因证据=analysis/data_foundation_root_causes_20260703.md。
根因1: grain 声明是"猜的"不是"验的" —
注册时单日抽样查不出低频多行模式 (多年度研报/多席位/双榜), 而 2026-06-22 上线的批内
drop_duplicates(grain) 把错误 grain 从良性 (多行共存) 升级为恶性 (静默销毁,
report_rc 漏 quarter / block_trade 漏 buyer,seller 两个 CRITICAL 实证)。
本门 = 全域每日 GROUP BY grain HAVING COUNT(*)>1 扫描: grain 错误在"良性期"就被抓,
不等去重上线后变数据销毁。

范围: sync_registry.yaml 全部域 (raw 表) + MART_GRAINS (加工产物表, data_layers.yaml 只声明
layer 无 grain 字段 → 此处集中镜像 builder 契约)。任何未豁免的 dup>0 = exit 1 (FAIL)。

用法:
    PYTHONPATH=backend python backend/scripts/check_grain_uniqueness.py            # 人读输出
    ... --json                                                                     # 机器读
    ... --exempt raw_tushare_top_inst:20260801                                     # repair 期已知
        待清表临时豁免 (带到期日; 过期自动恢复 FAIL — 豁免不是永久白名单)
    ... --strict                                                                   # 库不可达也算 FAIL
        (默认跳过: tushare_raw 重拉期写锁占用时 read_only attach 同样被拒, CLAUDE §4.5 2026-07-02)

接线状态由 safe_commit、Moth 断言和 live gate 验证，不在本文档字符串声称。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "backend" / "config" / "sync_registry.yaml"

# 加工产物表 grain 映射 (data_layers.yaml 只有 layer 声明无 grain 字段; grain 真相源 = 各 builder
# DDL/契约 docstring, 此处集中镜像供持续审计 — 改 builder 键结构必须同步本表):
MART_GRAINS: list[tuple[str, str, list[str]]] = [
    # (db_alias, table, grain)
    ("smartmoney", "dim_stock_segment_daily", ["stock_code", "trade_date"]),          # services/segments.py B1
    ("smartmoney", "fact_stock_form_daily", ["stock_code", "trade_date"]),            # services/technical_states B2
    ("smartmoney", "mart_sector_pulse_daily", ["chain", "sector_code", "trade_date"]),
    # services/market_pulse.py B4/v3 — chain 就是 taxonomy namespace。东财行业与东财概念
    # 分别写入 dc_industry/dc_concept；content_type 只保留供应商原标签作证据，不再参与身份或 grain。
    ("smartmoney", "mart_market_pulse_daily", ["trade_date"]),                        # services/market_pulse.py B4
]


def load_registry_specs(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """sync_registry 全域 → [{db, table, grain, origin}] (同表同 grain 去重; 同表异 grain 各查)。"""
    raw = yaml.safe_load((registry_path or REGISTRY_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    default_db = defaults.get("target_db", "tushare_raw")
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    specs: list[dict[str, Any]] = []
    for domain, entry in (raw.get("domains") or {}).items():
        entry = entry or {}
        table = entry.get("target_table")
        grain = entry.get("grain")
        if not table or not grain:
            continue  # full_refresh 静态表也有 grain; 无 grain 条目按注册纪律不存在, 防御跳过
        key = (entry.get("target_db", default_db), table, tuple(grain))
        if key in seen:
            continue  # 同表同 grain 多域 (index_member_all / _hist 同表 MERGE) 只查一次
        seen.add(key)
        specs.append({"db": key[0], "table": table, "grain": list(grain),
                      "origin": f"sync_registry:{domain}"})
    for db, table, grain in MART_GRAINS:
        specs.append({"db": db, "table": table, "grain": list(grain), "origin": "mart_grains"})
    return specs


def check_table(conn, table: str, grain: list[str]) -> dict[str, Any]:
    """单表 grain 唯一性: {status, dup_groups, excess_rows}。表缺=skipped (域注册未拉/重建期),
    grain 列缺=fail (schema 漂移, 与 sync_runner 缺 grain 列 raise 同语义)。"""
    if not conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1", [table]
    ).fetchone():
        return {"status": "skipped_missing_table", "dup_groups": 0, "excess_rows": 0}
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
    missing = [g for g in grain if g not in cols]
    if missing:
        return {"status": "fail_missing_grain_cols", "dup_groups": 0, "excess_rows": 0,
                "missing_cols": missing}
    key_sql = ", ".join(f'"{g}"' for g in grain)
    row = conn.execute(
        f'SELECT COUNT(*), COALESCE(SUM(n - 1), 0) FROM ('
        f'SELECT COUNT(*) AS n FROM "{table}" GROUP BY {key_sql} HAVING COUNT(*) > 1)'
    ).fetchone()
    dup_groups, excess = int(row[0] or 0), int(row[1] or 0)
    return {"status": "pass" if dup_groups == 0 else "fail_duplicate_grain",
            "dup_groups": dup_groups, "excess_rows": excess}


def parse_exemptions(items: list[str]) -> dict[str, str]:
    """--exempt table:YYYYMMDD → {table: expiry}。格式错 = 立即报错 (豁免必须带到期日)。"""
    out: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise SystemExit(f"--exempt 格式错: {item!r} (需 table:YYYYMMDD, 豁免必须带到期日)")
        table, expiry = item.split(":", 1)
        expiry = expiry.strip()
        if len(expiry) != 8 or not expiry.isdigit():
            raise SystemExit(f"--exempt 到期日格式错: {item!r} (需 YYYYMMDD)")
        out[table.strip()] = expiry
    return out


def run_checks(
    specs: list[dict[str, Any]],
    conn_for: Callable[[str], Any],
    exemptions: dict[str, str] | None = None,
    today: str | None = None,
    strict: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """全表扫描: 返回 (results, failures)。conn_for(db_alias) 可注入 (单测内存库)。
    exemptions: {table: 到期日 YYYYMMDD} — 未到期的 dup 降为 exempt (不 FAIL), 过期照常 FAIL。"""
    exemptions = exemptions or {}
    today = today or date.today().strftime("%Y%m%d")  # rule-compliance: ok evidence=Phase ψ.5 allowlist 豁免到期日=自然日语义 (非交易日锚, 过期自动恢复 FAIL)rade-date end_date
    conns: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    try:
        for spec in specs:
            alias = spec["db"]
            if alias not in conns:
                try:
                    conns[alias] = conn_for(alias)
                except Exception as exc:  # noqa: BLE001 — 写锁期 read_only attach 同样被拒
                    conns[alias] = None
                    results.append({**spec, "status": "db_unreachable", "dup_groups": 0,
                                    "excess_rows": 0, "error": str(exc)[:120]})
                    continue
            conn = conns[alias]
            if conn is None:
                results.append({**spec, "status": "db_unreachable", "dup_groups": 0, "excess_rows": 0})
                continue
            r = check_table(conn, spec["table"], spec["grain"])
            if r["status"] == "fail_duplicate_grain" and spec["table"] in exemptions:
                expiry = exemptions[spec["table"]]
                if today <= expiry:
                    r = {**r, "status": "exempt_until_" + expiry}
                else:
                    r = {**r, "status": "fail_exemption_expired", "expired_exemption": expiry}
            results.append({**spec, **r})
    finally:
        for c in conns.values():
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
    failures = [r for r in results if r["status"].startswith("fail")
                or (strict and r["status"] == "db_unreachable")]
    return results, failures


def _default_conn_for(alias: str):
    import duckdb

    from services.database_manifest import get_database_manifest
    return duckdb.connect(  # rule-compliance: ok evidence=read_only 审计连接 (grain 唯一性扫描, 不写)
        str(get_database_manifest().path_for(alias)), read_only=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="grain 唯一性持续审计门 (dup>0 = exit 1)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--exempt", action="append", default=[],
                    help="临时豁免 table:YYYYMMDD (repair 期已知待清表; 过期自动恢复 FAIL), 可重复")
    ap.add_argument("--strict", action="store_true",
                    help="库不可达 (写锁占用等) 也算 FAIL (默认跳过并标 db_unreachable)")
    args = ap.parse_args(argv)

    specs = load_registry_specs()
    results, failures = run_checks(specs, _default_conn_for,
                                   exemptions=parse_exemptions(args.exempt), strict=args.strict)
    if args.json:
        print(json.dumps({"results": results, "failures": len(failures)},
                         ensure_ascii=False, indent=1))
    else:
        for r in results:
            if r["status"] == "pass":
                continue  # 人读输出只列非绿 (全绿时末行汇总)
            print(f"[{r['status']}] {r['db']}.{r['table']} grain={r['grain']} "
                  f"dup_groups={r['dup_groups']} excess_rows={r['excess_rows']} ({r['origin']})")
        n_pass = sum(1 for r in results if r["status"] == "pass")
        print(f"grain-uniqueness: {n_pass}/{len(results)} pass, {len(failures)} FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
