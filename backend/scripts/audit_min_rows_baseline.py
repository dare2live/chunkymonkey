#!/usr/bin/env python3
"""按"健康的全历史最小值"给 by_trade_date 域校准 min_rows_per_batch。

**为什么需要这个工具** (2026-08-18 实测教训):
registry 里多个域的 min_rows_per_batch 注释写着"照 histmin 校准, <=histmin 零幻影缺口"。
这个方针本身是对的 —— 底线设太高会误杀历史小 universe 时期的回填。
但它有一个盲区: **没有人验证过 histmin 那天本身是否健康**。

实锤: dc_member 的底线 7000 照 histmin=7919(20250106) 设, 而那天正是一次未识别的分页截断
(库 7,919 行/35 板块, vendor 实为 40,000 行/257 板块)。缺陷日当了 histmin, 底线就永远
拦不住同类缺陷 —— 一次事故被固化成了基准。

所以校准必须两步走: 先判基准日健不健康, 再拿它当基准。
"健康"的判据 = 该日行数 / 邻域(前后各 10 个观测日)中位数 >= --healthy-ratio。

用法:
    PYTHONPATH=backend python backend/scripts/audit_min_rows_baseline.py            # 全部 by_trade_date 域
    PYTHONPATH=backend python backend/scripts/audit_min_rows_baseline.py --domain dc_member
    PYTHONPATH=backend python backend/scripts/audit_min_rows_baseline.py --json     # 机器可读

退出码: 0=无建议变更 / 1=有域的现底线明显偏离健康 histmin (需人工裁决, 不自动改配置)。
本脚本**只给建议不改 registry**: 底线是数据契约, 改它要带理由进 commit message。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 低于健康 histmin 这个比例才认为"现底线明显偏松", 值得报出来。
# 不是任何偏离都值得改 —— 底线本就该留余量, 频繁微调只会制造噪音。
LOOSE_RATIO = 0.5


def _load_registry() -> dict:
    import yaml

    return yaml.safe_load((REPO / "backend/config/sync_registry.yaml").read_text(encoding="utf-8"))


def _conn():
    sys.path.insert(0, str(REPO / "backend"))
    import duckdb

    from services.database_manifest import get_database_manifest

    return duckdb.connect(  # rule-compliance: ok evidence=read_only 校准建议, 不写
        str(get_database_manifest().path_for("tushare_raw")), read_only=True
    )


def analyse(conn, table: str, date_col: str, healthy_ratio: float,
            *, since: str = "") -> dict | None:
    """返回该域的 histmin / 健康 histmin / 被判不健康的基准日。

    ``since`` = registry 的 ``min_rows_since``。声明了它的域是**时代分段**的:
    该日期起用 min_rows_per_batch, 之前用 min_rows_before。
    此时必须只在 since 之后的那段里求 histmin —— 拿全历史 histmin 去比今日底线,
    会把一个已被正确处理的历史低行数时代误报成"底线偏紧"。

    2026-08-21 实测教训: 本工具初版没读 min_rows_since, 于是把 margin_detail
    (since=20220104 / before=800) 与 moneyflow_ind_dc (since=20260101 / before=80)
    双双误报为偏紧 —— 而这两个域早在 2026-07-09 就用时代分段根治过, 注释里连
    "594 个 2019-2021 真实完整日成幻影缺口"都写着。工具自己的 docstring 警告过
    "基准要先被验证", 结果第一版就栽在同一件事上。
    """
    try:
        rows = conn.execute(
            f'''
            with per as (select "{date_col}" d, count(*) n from "{table}" group by 1),
                 w as (select d, n,
                              median(n) over (order by d rows between 10 preceding and 10 following) med
                       from per)
            select d, n, med from w where med > 0 order by n
            '''
        ).fetchall()
        if since:
            # 只看当前时代: 更早的低行数由 min_rows_before 负责, 不归今日底线管
            rows = [r for r in rows if str(r[0]) >= since]
    except Exception:
        return None
    if not rows:
        return None
    raw_min_day, raw_min, raw_med = rows[0]
    healthy = [(d, n) for d, n, med in rows if n >= med * healthy_ratio]
    if not healthy:
        return None
    healthy_day, healthy_min = healthy[0]
    return {
        "hist_min": int(raw_min),
        "hist_min_day": str(raw_min_day),
        "hist_min_ratio": round(raw_min / raw_med, 2) if raw_med else None,
        "healthy_hist_min": int(healthy_min),
        "healthy_hist_min_day": str(healthy_day),
        "basis_is_suspect": raw_min < raw_med * healthy_ratio,
        "days": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", help="只看一个域")
    ap.add_argument("--healthy-ratio", type=float, default=0.5,
                    help="基准日行数/邻域中位 低于此值即判该日不健康, 不可当基准 (默认 0.5)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    reg = _load_registry()
    conn = _conn()
    tables = {r[0] for r in conn.execute("select table_name from duckdb_tables()").fetchall()}

    out: list[dict] = []
    for name, spec in sorted((reg.get("domains") or {}).items()):
        if not isinstance(spec, dict) or spec.get("batch_mode") != "by_trade_date":
            continue
        if args.domain and name != args.domain:
            continue
        table = spec.get("target_table")
        if table not in tables:
            continue
        since = str(spec.get("min_rows_since") or "").replace("-", "")
        got = analyse(conn, table, spec.get("freshness_date_column") or "trade_date",
                      args.healthy_ratio, since=since)
        if not got:
            continue
        current = int(spec.get("min_rows_per_batch") or 0)
        got.update(
            domain=name, current=current, min_rows_since=since or None,
            min_rows_before=spec.get("min_rows_before"),
            loose=current < got["healthy_hist_min"] * LOOSE_RATIO,
            # 偏紧同样是缺陷, 而且更隐蔽: 底线高于历史最小值意味着**历史上真实出现过的低值日**
            # 一旦重拉就会被拒绝写入 —— 那是自己造出来的"幻影缺口", 正是零幻影缺口方针要防的反面。
            too_tight=current > got["healthy_hist_min"],
        )
        out.append(got)

    loose = [r for r in out if r["loose"]]
    tight = [r for r in out if r["too_tight"]]
    suspect = [r for r in out if r["basis_is_suspect"]]

    if args.json:
        print(json.dumps({"domains": out,
                          "loose": [r["domain"] for r in loose],
                          "too_tight": [r["domain"] for r in tight]},
                         ensure_ascii=False, indent=1))
    else:
        print(f"{'域':22} {'现底线':>9} {'健康histmin':>11} {'原始histmin':>11} {'基准日':>10} {'比值':>5}")
        print("-" * 78)
        for r in sorted(out, key=lambda x: (not x["loose"], x["domain"])):
            mark = " ⚠偏松" if r["loose"] else (" ⚠偏紧(高于histmin, 会误杀)" if r["too_tight"] else "")
            susp = " (原始基准日待核证)" if r["basis_is_suspect"] else ""
            print(f"{r['domain']:22} {r['current']:>9,} {r['healthy_hist_min']:>11,} "
                  f"{r['hist_min']:>11,} {r['hist_min_day']:>10} {r['hist_min_ratio']:>5}{mark}{susp}")
        if tight:
            print(f"\n{len(tight)} 个域的底线**高于**健康 histmin —— 历史真实低值日重拉会被拒写, "
                  "等于自造幻影缺口:")
            for r in tight:
                print(f"  {r['domain']}: 底线 {r['current']:,} > histmin {r['healthy_hist_min']:,} "
                      f"({r['healthy_hist_min_day']})")
        if suspect:
            print(f"\n{len(suspect)} 个域的原始 histmin 那天低于邻域中位 —— 这只是**粗筛**, 不等于缺陷: "
                  "低值可能是源端真实状态(实测反例: dc_member 20251029 比值 0.32, 但 vendor 逐页核证"
                  "全量就是 20,748 行)。当基准前必须向 vendor 核证那天的真实全量:")
            for r in suspect:
                print(f"  {r['domain']}: {r['hist_min_day']} 仅 {r['hist_min']:,} 行, "
                      f"为邻域中位的 {r['hist_min_ratio']}")
        if loose:
            print(f"\n{len(loose)} 个域现底线低于健康 histmin 的 {LOOSE_RATIO:.0%} —— 建议人工裁决后调整"
                  " (本脚本不改 registry: 底线是数据契约, 改它要带理由进 commit)。")
    return 1 if (loose or tight) else 0


if __name__ == "__main__":
    raise SystemExit(main())
