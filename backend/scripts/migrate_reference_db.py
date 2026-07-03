"""§9 reference.duckdb 拆库迁移 — Stage A 保真建库 (架构蓝图 §9, 用户 2026-06-24 选结构拆).

████████████████████████████████████████████████████████████████████████████████
██ DEPRECATED 2026-07-03 退役 — 破坏性死路径, main 入口已堵死 (R1 根因3) ██
██ 源表 sm.dim_trading_calendar / dim_active_a_stock 等已在 §9 Stage E 物删; ██
██ 今天重跑 --build 会先物删 reference 库文件销毁现存唯一交易日历,          ██  # rule-compliance: ok evidence=deprecated warning text, 非代码路径
██ 再在 describe 已删源表时崩溃 (audit data_foundation_audit_20260703 实测)。 ██
██ 日历刷新走 services/calendar_builder.build_latest (pipeline acquire 已挂); ██
██ 本文件只留历史参考 (Stage A 迁移过程留档), 禁止执行。                      ██
████████████████████████████████████████████████████████████████████████████████

目的: 把读多写少的 universe/identity/calendar reference 表从 smartmoney 大杂烩拆出 →
reference.duckdb (只读 ATTACH 与 facts 写锁解耦), 根治 sync_runner 回填读 universe 撞 smartmoney 写锁。

分阶段 (owner=analysis/data_module_architecture_20260624.md §9 执行计划):
  Stage A (本脚本, 可逆): 保真建 reference.duckdb (EXPORT/IMPORT 非 COPY FROM DATABASE, mythos§12 防丢约束) + 5件套验收。smartmoney 不动。
  Stage B (待做, 可逆): database_manifest 加 reference alias + get_conn ATTACH reference 只读。
  Stage C (待做, 高风险可逆): smartmoney 留 view 指向 reference (读透明) + 写方 repoint reference + sync_runner 读 reference (撞锁根治) + schema DDL 移 reference (重建路径)。
  Stage D (待做, 不可逆, 需用户确认): 物删 smartmoney 旧 reference 表 (view 替代后).

REF_TABLES = 核心读多写少 reference 集 (奥卡姆: 先核心 universe+calendar, 静态config dim 待评估扩):
  dim_active_a_stock (universe 身份真相源, 25 消费方) / dim_all_ever_listed (生存者 universe) /
  dim_listing_status / dim_trading_calendar (交易日历).

用法: python backend/scripts/migrate_reference_db.py --build   # Stage A 重建+验收 (幂等, 可逆)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

# 本工具操作 dim_active_a_stock 表本身=迁移搬库, 非取 universe 选股 list,
# 已在 check_universe_filter EXEMPT_FILES 豁免 (L8 gate)。

REF_TABLES = [
    "dim_active_a_stock",
    "dim_all_ever_listed",
    "dim_listing_status",
    "dim_trading_calendar",
]
# 保真 replay: 源 (smartmoney) 的 PK/索引 (实测 2026-06-24)
EXPLICIT_PK = {"dim_trading_calendar": "trade_date"}  # PRIMARY KEY(trade_date) NOT NULL
REPLAY_INDEX = {  # idx_name → (table, col)
    "idx_daas_updated": ("dim_active_a_stock", "updated_at"),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build(smartmoney_path: str, reference_path: str) -> dict:
    """保真建 reference.duckdb (幂等重建). 返回验收报告."""
    if os.path.exists(reference_path):
        os.remove(reference_path)  # Stage A 可重建 (无 app 依赖时)

    sm = duckdb.connect(smartmoney_path, read_only=True)  # rule-compliance: ok evidence=one-off reference-split迁移工具, raw duckdb 需跨库ATTACH/EXPORT保真
    cal_cols = sm.execute("describe dim_trading_calendar").fetchall()
    sm.close()
    cal_ddl = ", ".join(
        f"{c[0]} {c[1]}" + (" NOT NULL" if c[0] == EXPLICIT_PK["dim_trading_calendar"] else "")
        for c in cal_cols
    )

    ref = duckdb.connect(reference_path)  # rule-compliance: ok evidence=one-off reference-split迁移工具, raw duckdb 需跨库ATTACH/EXPORT保真
    ref.execute(f"ATTACH '{smartmoney_path}' AS sm (READ_ONLY)")
    # PK 表显式 DDL; 其余 CREATE AS SELECT
    ref.execute(
        f"CREATE TABLE dim_trading_calendar ({cal_ddl}, "
        f"PRIMARY KEY({EXPLICIT_PK['dim_trading_calendar']}))"
    )
    ref.execute("INSERT INTO dim_trading_calendar SELECT * FROM sm.dim_trading_calendar")
    for t in REF_TABLES:
        if t == "dim_trading_calendar":
            continue
        ref.execute(f"CREATE TABLE {t} AS SELECT * FROM sm.{t}")
    for idx, (tbl, col) in REPLAY_INDEX.items():
        ref.execute(f"CREATE INDEX {idx} ON {tbl}({col})")
    ref.execute("DETACH sm")
    ref.close()

    return verify(smartmoney_path, reference_path)


def verify(smartmoney_path: str, reference_path: str) -> dict:
    """5件套验收: 行数 + 抽样值 + 约束数 + 索引数 + 读冒烟."""
    ref = duckdb.connect(reference_path, read_only=True)  # rule-compliance: ok evidence=one-off reference-split迁移工具, raw duckdb 需跨库ATTACH/EXPORT保真
    sm = duckdb.connect(smartmoney_path, read_only=True)  # rule-compliance: ok evidence=one-off reference-split迁移工具, raw duckdb 需跨库ATTACH/EXPORT保真
    report = {"tables": {}, "ok": True}
    for t in REF_TABLES:
        rn = ref.execute(f"select count(*) from {t}").fetchone()[0]
        sn = sm.execute(f"select count(*) from {t}").fetchone()[0]
        ri = ref.execute(f"select count(*) from duckdb_indexes() where table_name='{t}'").fetchone()[0]
        rc = ref.execute(f"select count(*) from duckdb_constraints() where table_name='{t}'").fetchone()[0]
        match = rn == sn
        report["tables"][t] = {"rows_ref": rn, "rows_sm": sn, "match": match, "idx": ri, "cons": rc}
        if not match:
            report["ok"] = False
    # 抽样值 + 读冒烟
    rv = ref.execute("select stock_code from dim_active_a_stock order by stock_code limit 1").fetchone()
    sv = sm.execute("select stock_code from dim_active_a_stock order by stock_code limit 1").fetchone()
    report["sample_match"] = rv == sv
    try:
        ref.execute("select count(*) from dim_trading_calendar where trade_date is not null").fetchone()
        report["pk_smoke"] = True
    except Exception:  # noqa: BLE001
        report["pk_smoke"] = False
        report["ok"] = False
    if not report["sample_match"]:
        report["ok"] = False
    ref.close()
    sm.close()
    return report


def main() -> int:
    raise SystemExit(
        "2026-07-03 退役: 源表 (smartmoney reference 副本) 已在 §9 Stage E 物删, "
        "重跑 --build 会先物删 reference 库销毁现存唯一日历再崩溃 (破坏性死路径, R1 根因3)。"  # rule-compliance: ok evidence=deprecated warning text, 非代码路径
        "日历刷新走 services/calendar_builder.build_latest (pipeline acquire Step 2.96 已挂); "
        "本文件仅留历史参考。"
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="Stage A 保真建 reference 库 + 验收")
    ap.add_argument("--verify-only", action="store_true", help="只验收已建的 reference 库")
    args = ap.parse_args()
    from services.database_manifest import get_database_manifest
    mf = get_database_manifest()
    sm_path = str(mf.path_for("smartmoney"))   # manifest 单一真相源, 不 hardcode 路径
    ref_path = str(mf.path_for("reference"))   # §9 reference alias (本次 Stage B 注册)

    if args.build:
        report = build(sm_path, ref_path)
    elif args.verify_only:
        report = verify(sm_path, ref_path)
    else:
        print("需 --build 或 --verify-only", file=sys.stderr)
        return 2

    print("=== reference 库 Stage A 验收 ===")
    for t, r in report["tables"].items():
        print(f"  {t}: rows {r['rows_ref']}=={r['rows_sm']} "
              f"[{'OK' if r['match'] else 'MISMATCH'}] idx={r['idx']} cons={r['cons']}")
    print(f"  sample_match={report['sample_match']} pk_smoke={report['pk_smoke']}")
    print(f"=== Stage A {'PASS' if report['ok'] else 'FAIL'} (smartmoney 未动=可逆) ===")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
