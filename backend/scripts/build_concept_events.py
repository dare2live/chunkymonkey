#!/usr/bin/env python3
"""概念增删事件流 detector — 相邻交易日成分 diff → fact_concept_event.

为什么存在 (analysis/concept_event_chain_mining_20260611.md):
  概念诞生/成分增删是题材工程化的客观时间戳 (东财每天增删"国产替代""去日本化"等)。
  事件流 = 主题生命周期标记 + 链谱边弱监督 + 题材共现图原料。

真相源 (宪法第 1 条): 概念成分按日快照本身 (raw_tushare_dc_member 历史回填 +
  data/concept_snapshots/*/dc_member.parquet 自养 forward), 不依赖任何中间派生表。
  事件 = 相邻两个快照日的成分集合 SYMMETRIC DIFFERENCE, 纯集合运算无外部状态。

PIT 纪律: as_of_mode 显式区分 observed (自养快照 diff, 真 PIT) vs reconstructed
  (历史回填 diff, 弱假设数据商当日发布 — 回测须做 1-3 日滞后敏感性)。
  事件 event_date = 后一个快照日 (变更首次可观测日)。

落库: fact_concept_event 写 smartmoney.duckdb (只读 raw + parquet, 与回填写锁正交)。
用法:
  PYTHONPATH=backend python backend/scripts/build_concept_events.py --source raw    # 历史回填段
  PYTHONPATH=backend python backend/scripts/build_concept_events.py --source snapshot # 自养段
  ... --rebuild   # 全量重算 (默认增量: 只算 watermark 之后的新快照日)
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("concept_events")

_REPO = Path(__file__).resolve().parents[2]
_SNAPSHOT_ROOT = _REPO / "data" / "concept_snapshots"
_EVENT_TABLE = "fact_concept_event"

# 事件类型
BORN, DEAD, ADD, DROP = "concept_born", "concept_dead", "member_add", "member_drop"


def _smartmoney_conn():
    from services.db import get_conn
    return get_conn()


def _ensure_table(conn) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_EVENT_TABLE} (
            event_date   VARCHAR,
            source       VARCHAR,    -- dc / ths / tdx / kpl
            concept_code VARCHAR,
            concept_name VARCHAR,
            event_type   VARCHAR,    -- concept_born/concept_dead/member_add/member_drop
            con_code     VARCHAR,    -- 成分股 (concept_born/dead 时为 NULL)
            as_of_mode   VARCHAR,    -- observed / reconstructed
            built_at     TIMESTAMP DEFAULT now()
        )
    """)
    conn.execute(f"""
        CREATE INDEX IF NOT EXISTS idx_concept_event_de
        ON {_EVENT_TABLE}(event_date, source, event_type)
    """)


def _membership_by_day_from_raw(conn, source_table: str) -> dict[str, dict[str, set[str]]]:
    """raw 表 → {trade_date: {concept_code: {member,...}}} (只读).

    字段方向 (2026-06-11 parquet 实测, Fable-5 复查抓反向 bug):
    ts_code = 概念板块代码 (BK0145.DC, 全市场 66 个); con_code = 成分股 (600503.SH, 5521 只)。
    """
    # 2026-06-13 修: 带点全名整体引号会被当单标识符 ("traw.raw_tushare_dc_member" 查无此表),
    # raw 源路径因此从未跑通 (此前误归因写锁); 按点分段引每节。
    qualified = ".".join(f'"{part}"' for part in source_table.split("."))
    rows = conn.execute(
        f"SELECT trade_date, ts_code AS concept, con_code AS member FROM {qualified}"
    ).fetchall()
    out: dict[str, dict[str, set[str]]] = {}
    for d, concept, member in rows:
        day = str(d).replace("-", "")
        out.setdefault(day, {}).setdefault(str(concept), set()).add(str(member))
    return out


def _membership_by_day_from_snapshots() -> dict[str, dict[str, set[str]]]:
    """parquet 自养快照 → 同结构 (dc_member.parquet 每日一个目录)."""
    import pandas as pd

    out: dict[str, dict[str, set[str]]] = {}
    for day_dir in sorted(_SNAPSHOT_ROOT.glob("[0-9]" * 8)):
        pq = day_dir / "dc_member.parquet"
        if not pq.exists():
            continue
        df = pd.read_parquet(pq)
        if "con_code" not in df.columns or "ts_code" not in df.columns:
            continue
        day = day_dir.name
        # ts_code = 概念板块 (BK*.DC) / con_code = 成分股 — 与 raw 路径同口径 (实测方向)
        for concept, member in zip(df["ts_code"], df["con_code"]):
            out.setdefault(day, {}).setdefault(str(concept), set()).add(str(member))
    return out


def _smoothed_at(by_day: dict[str, dict[str, set[str]]], days: list[str], i: int,
                 window: int) -> dict[str, set[str]]:
    """carry-forward 平滑面板: smoothed[t] = union(raw[t-window+1 .. t]).

    只用 <= t 的信息 (PIT 干净)。动机 (2026-06-13 实测): dc_member 单日薄拉取广泛存在 —
    修复 6 凹陷日后成员级 flicker 仍 85.1% (392,502/461,060 drop 在 3 日内 re-add),
    drop 最高日 (20260518 单日 35,120) 不在凹陷日清单 = 概念数筛法抓不到的成员级薄日。
    逐日裸 diff 对此结构性过敏; 平滑后单日缺席不再制造幻影 drop/re-add 对,
    真实 drop (持续缺席) 以 window-1 日滞后确认。
    """
    merged: dict[str, set[str]] = {}
    for j in range(max(0, i - window + 1), i + 1):
        for c, members in by_day[days[j]].items():
            merged.setdefault(c, set()).update(members)
    return merged


def _diff_events(by_day: dict[str, dict[str, set[str]]], as_of_mode: str,
                 source: str, after_day: str | None, smooth_window: int = 1) -> list[tuple]:
    """平滑面板相邻日 diff → 事件行. after_day: 只产 > 该日的事件 (增量).

    smooth_window=1 = 裸 diff 语义; raw (reconstructed) 生产路径由 build() 传 3
    (见 _smoothed_at 动机 — dc_member 薄日伪影)。
    """
    days = sorted(by_day)
    events: list[tuple] = []
    prev = _smoothed_at(by_day, days, 0, smooth_window)
    for i in range(1, len(days)):
        cur_day = days[i]
        cur = _smoothed_at(by_day, days, i, smooth_window)
        if after_day and cur_day <= after_day:
            prev = cur
            continue
        prev_concepts, cur_concepts = set(prev), set(cur)
        for c in cur_concepts - prev_concepts:  # 新概念诞生 (含其全部初始成分)
            events.append((cur_day, source, c, BORN, None, as_of_mode))
        for c in prev_concepts - cur_concepts:  # 概念消失
            events.append((cur_day, source, c, DEAD, None, as_of_mode))
        for c in cur_concepts & prev_concepts:  # 存续概念的成分增删
            for m in cur[c] - prev[c]:
                events.append((cur_day, source, c, ADD, m, as_of_mode))
            for m in prev[c] - cur[c]:
                events.append((cur_day, source, c, DROP, m, as_of_mode))
        prev = cur
    return events


def _last_event_day(conn, source: str, as_of_mode: str) -> str | None:
    try:
        r = conn.execute(
            f"SELECT MAX(event_date) FROM {_EVENT_TABLE} WHERE source=? AND as_of_mode=?",
            [source, as_of_mode],
        ).fetchone()
        return r[0] if r and r[0] else None
    except Exception:  # noqa: BLE001 — 表不存在时返回 None
        return None


def build(source_kind: str, *, rebuild: bool = False) -> dict[str, Any]:
    """source_kind: 'raw' (历史回填 reconstructed) 或 'snapshot' (自养 observed)."""
    if source_kind == "raw":
        as_of_mode, source, source_table = "reconstructed", "dc", "raw_tushare_dc_member"
    elif source_kind == "snapshot":
        as_of_mode, source = "observed", "dc"
    else:
        raise ValueError(f"未知 source_kind: {source_kind}")

    conn = _smartmoney_conn()
    try:
        _ensure_table(conn)
        after = None if rebuild else _last_event_day(conn, source, as_of_mode)

        if source_kind == "raw":
            from services.database_manifest import get_database_manifest
            raw_path = get_database_manifest().path_for("tushare_raw")
            conn.execute(f"ATTACH IF NOT EXISTS '{raw_path}' AS traw (READ_ONLY)")
            has = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='raw_tushare_dc_member'"
            ).fetchone()[0]
            if not has:
                return {"source": source_kind, "status": "no_source_table", "events": 0}
            by_day = _membership_by_day_from_raw(conn, "traw.raw_tushare_dc_member")
        else:
            by_day = _membership_by_day_from_snapshots()

        if len(by_day) < 2:
            return {"source": source_kind, "status": "insufficient_days", "days": len(by_day), "events": 0}

        # raw 路径平滑窗 3 (prereg lf_v0 修订 2): 薄日伪影实测 85.1% flicker, 裸 diff 不可用
        events = _diff_events(by_day, as_of_mode, source, after,
                              smooth_window=3 if source_kind == "raw" else 1)
        if rebuild:
            conn.execute(f"DELETE FROM {_EVENT_TABLE} WHERE source=? AND as_of_mode=?", [source, as_of_mode])
        if events:
            conn.executemany(
                f"INSERT INTO {_EVENT_TABLE} "
                f"(event_date, source, concept_code, event_type, con_code, as_of_mode) "
                f"VALUES (?, ?, ?, ?, ?, ?)",
                events,
            )
        from collections import Counter
        by_type = dict(Counter(e[3] for e in events))
        result = {"source": source_kind, "as_of_mode": as_of_mode, "status": "ok",
                  "days": len(by_day), "events": len(events), "by_type": by_type,
                  "after_day": after}
        log.info("concept events %s", result)
        return result
    finally:
        conn.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=["raw", "snapshot", "both"], default="both")
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    import json
    kinds = ["raw", "snapshot"] if args.source == "both" else [args.source]
    results = []
    for k in kinds:
        try:
            results.append(build(k, rebuild=args.rebuild))
        except Exception as exc:  # noqa: BLE001 — 单源失败不挡另一源, 显式入结果
            results.append({"source": k, "status": "error", "error": str(exc)[:200]})
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if all(r.get("status") in ("ok", "no_source_table", "insufficient_days") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
