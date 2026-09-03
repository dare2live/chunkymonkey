"""血缘 活库↔登记表 差 (K4 check_datasets_registry 雏形) — runtime system_health, 只报不拦。

背景 (#4 #12(i), 2026-09-04): 提交门 check_lineage_drift 改成纯登记表函数 (catalog=False,
不连活库) 之后, 活库真实表集合与登记表 (sync_registry/data_layers/data_access/
database_manifest) 声明表集合之间的差 ([arch] 审计: 36 幽灵/32 孤儿量级) 就没有任何门再报
了 —— 本检查补这个洞。这个差是"数据地基今天有没有漂"的事实观测, 不是"这次 commit 对不对",
所以装在 daily_update 运行时自检里 (system_health), 不装回提交路径。

与 2026-08-11 被撤出 runtime_checks 的旧检查不是同一个东西 (governance_gates.yaml 的
lineage_drift.why 字段记录了那次撤销的理由): 旧检查比的是「提交版 graph.json vs 重生结果」,
报的是"有没有人重生血缘"这种**开发者状态**, 与数据健康无关。本检查比的是「活库真实表 vs
登记表声明表」, 两边都是系统当下的**事实**, 与谁有没有跑过 build 完全无关 —— 是数据/配置
完整性观测, 性质上更接近 dead_references 的 E 扫 (表存在性审计) 而不是那次被撤销的检查。

ghost  = 活库存在但没有任何登记表声明它的表 (库里多出来的东西, 没人认领)
orphan = 登记表声明了这张表, 但活库不存在 (声明了没建 / 已删没退登记)

已知不精确 (K4 前的雏形, 不追求完美): 少数表名 (ingest_batch/accepted_partition 等
data_layers "infra" 层的按库私有 bookkeeping 表) 在多个物理库里各有一份同名副本, 但登记表
只能把一个表名路由到一个库 (backend/services/lineage/builder.py 的
_registry_table_specs())，因此这类表在其余库里的副本会被计成 ghost —— 这是已知的、不影响
提交门正确性的噪音 (提交门是登记表与登记表自身重生比对, 不受这个限制影响), K4 落地时随
datasets.yaml 的库粒度声明一并解决。

fail-open: 活库某个库不可达 (缺文件 / 被写锁持有, 如形态面 64 分钟重建期间) 时不当 FAIL ——
这条路径此前 (2026-08-11 那次被撤销的检查) 从未被测过, 这次用真实持锁的集成测试覆盖
(backend/tests/scripts/test_check_lineage_catalog_drift.py)。

退出码: 0=PASS(无差) 或 UNVERIFIED(活库不可达, fail-open) / 1=DEGRADED(有差)。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from services.lineage import catalog_drift  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json-out", default=None, help="写完整结果 (ghosts/orphans 全列表) 到此路径")
    args = ap.parse_args(argv)

    try:
        drift = catalog_drift()
        reachable = True
        detail = None
    except RuntimeError as exc:
        # 活库某库不可达 (缺文件 / 被写锁持有等) —— fail-open, 不当数据地基坏 (system_health
        # 不该因为跑批中的正常写锁而误报; 2026-08-11 那次被撤销的检查明确指出这条路径没测过,
        # 这里用 test G 实测锁住时的行为)。
        drift = {"ghosts": [], "orphans": []}
        reachable = False
        detail = str(exc)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_catalog_reachable": reachable,
        "detail": detail,
        "ghost_count": len(drift["ghosts"]),
        "orphan_count": len(drift["orphans"]),
        "ghosts": drift["ghosts"],
        "orphans": drift["orphans"],
    }
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    if not reachable:
        print(f"[lineage-catalog-drift] UNVERIFIED: 活库不可达 ({detail}) — fail-open, 不阻断")
        return 0
    if not drift["ghosts"] and not drift["orphans"]:
        print("[lineage-catalog-drift] PASS: 活库表集合与登记表声明一致")
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
