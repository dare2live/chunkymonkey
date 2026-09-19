"""血缘 活库↔登记表 差 (K4 check_datasets_registry 雏形) — runtime system_health, 只报不拦。

背景 (#4 #12(i), 2026-09-04): 当时的提交门 check_lineage_drift 改成纯登记表函数
(catalog=False, 不连活库) 之后 —— 该门已于 2026-09-08 随 graph.json 去跟踪一并退役 ——
活库真实表集合与登记表 (sync_registry/data_layers/data_access/
database_manifest/legacy_raw_plane) 声明表集合之间的差就没有任何门再报了 —— 本检查补这个洞。
这个差是"数据地基今天有没有漂"的事实观测, 不是"这次 commit 对不对", 所以装在 daily_update
运行时自检里 (system_health), 不装回提交路径。

与 2026-08-11 被撤出 runtime_checks 的旧检查不是同一个东西 (那次撤销的理由当时记在
governance_gates.yaml 的 lineage_drift.why 字段, 该门已于 2026-09-08 退役, 理由见当次提交):
旧检查比的是「提交版 graph.json vs 重生结果」,
报的是"有没有人重生血缘"这种**开发者状态**, 与数据健康无关。本检查比的是「活库真实表 vs
登记表声明表」, 两边都是系统当下的**事实**, 与谁有没有跑过 build 完全无关 —— 是数据/配置
完整性观测, 性质上更接近 dead_references 的 E 扫 (表存在性审计) 而不是那次被撤销的检查。

ghost            = 活库存在但没有任何登记表声明它的表 (库里多出来的东西, 没人认领)
orphan           = 登记表声明了这张表, 但活库不存在 (声明了没建 / 已删没退登记)
declared_unbuilt = 登记表声明了、活库也没有, 但 sync_registry 把它标成 sync_policy=on_demand
                    且目标库从未对它做过生命周期删除 (mart_data_deletion_record 无 table_drop
                    行) —— "从未取过" 是合法状态, 不是漂移; 打印出来但不计入退出码。

2026-09-18 cut_lineage_drift 三条规则堵掉三处结构性假设 (services/lineage/builder.py 详注):
  1. 多库记账表 (mart_data_deletion_record/dim_schema_version/accepted_partition/ingest_batch)
     经 database_manifest.yaml 顶层 shared_bookkeeping_tables 名单声明"每个在线库可选出现一份",
     不再假设一表一库。
  2. on_demand 且从未被治理工具删过的表归 declared_unbuilt, 不是 orphan。
  3. legacy_raw_plane.yaml 的表清单是第四个声明源 (K3 停更 raw 表此前不在任何登记源里)。
同批还堵了一个反方向的洞: `_` 前缀不再天然豁免于活库扫描 (真正的瞬态表改用 DuckDB TEMP TABLE)。

活库某个库不可达 (缺文件 / 被写锁持有) 时判 UNVERIFIED, 退出 3 —— 不是 FAIL (不崩溃、不报漂移),
也不是 PASS (2026-09-19 改: 此前退出 0, 使「没核验」在日更 system_health 里与「核验通过」无法区分,
地基验收 R5 可被一次并发写锁骗过; 与 check_out_of_scope_rows 的 UNVERIFIED=3 同口径)。——
这条路径此前 (2026-08-11 那次被撤销的检查) 从未被测过, 这次用真实持锁的集成测试覆盖
(backend/tests/scripts/test_check_lineage_catalog_drift.py)。这与 shared_bookkeeping_tables
配置本身写错 (非 infra 名单成员) 是两回事——后者是真配置错误, 必须 fail-closed 报出来,
不能被同一个 except 悄悄降级成"活库暂时查不到" (services.lineage.LiveCatalogUnreachable 把
两者从异常类型上分开)。

退出码: 0=PASS(无差, declared_unbuilt 不影响) / 1=DEGRADED(有真 ghost/orphan) /
2=配置错误 (fail-closed, 例如 shared_bookkeeping_tables 名单成员不是 infra 层) /
3=UNVERIFIED(活库不可达, 未核验)。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.lineage import LiveCatalogUnreachable, catalog_drift  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json-out", default=None, help="写完整结果 (ghosts/orphans/declared_unbuilt 全列表) 到此路径")
    args = ap.parse_args(argv)

    try:
        drift = catalog_drift()
        reachable = True
        detail = None
    except LiveCatalogUnreachable as exc:
        # 活库某库不可达 (缺文件 / 被写锁持有等) —— 不崩溃、不报漂移, 如实记 UNVERIFIED
        # 退出 3 (没核验不等于通过; test G 用真实写锁实测这条路径)。
        drift = {"ghosts": [], "orphans": [], "declared_unbuilt": []}
        reachable = False
        detail = str(exc)
    except RuntimeError as exc:
        # 与上面不是同一件事: 这里是配置本身写错了 (例如 shared_bookkeeping_tables 名单
        # 塞了一个非 infra 表) —— fail-closed, 不许把它悄悄归进"活库暂时查不到"的 UNVERIFIED。
        print(f"[lineage-catalog-drift] FAIL: 配置错误 (fail-closed): {exc}", file=sys.stderr)
        return 2

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_catalog_reachable": reachable,
        "detail": detail,
        "ghost_count": len(drift["ghosts"]),
        "orphan_count": len(drift["orphans"]),
        "declared_unbuilt_count": len(drift["declared_unbuilt"]),
        "ghosts": drift["ghosts"],
        "orphans": drift["orphans"],
        "declared_unbuilt": drift["declared_unbuilt"],
    }
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    if not reachable:
        print(f"[lineage-catalog-drift] UNVERIFIED: 活库不可达 ({detail}) — 未核验, 不当 PASS")
        return 3
    if not drift["ghosts"] and not drift["orphans"]:
        print("[lineage-catalog-drift] PASS: 活库表集合与登记表声明一致")
        if drift["declared_unbuilt"]:
            print(f"  declared_unbuilt ({len(drift['declared_unbuilt'])}, on_demand 从未取过, 不计入漂移):")
            for d in drift["declared_unbuilt"]:
                print(f"    {d}")
        return 0

    print(
        f"[lineage-catalog-drift] DEGRADED: {len(drift['ghosts'])} 幽灵表 (活库有/登记表未声明) / "
        f"{len(drift['orphans'])} 孤儿表 (登记表声明/活库不存在)"
    )
    for g in drift["ghosts"][:20]:
        print(f"  ghost : {g}")
    if len(drift["ghosts"]) > 20:
        print(f"  ... 其余 {len(drift['ghosts']) - 20} 个幽灵表未列出 (详见 --json-out)")
    for o in drift["orphans"][:20]:
        print(f"  orphan: {o}")
    if len(drift["orphans"]) > 20:
        print(f"  ... 其余 {len(drift['orphans']) - 20} 个孤儿表未列出 (详见 --json-out)")
    if drift["declared_unbuilt"]:
        print(f"  declared_unbuilt ({len(drift['declared_unbuilt'])}, on_demand 从未取过, 不计入漂移):")
        for d in drift["declared_unbuilt"]:
            print(f"    {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
