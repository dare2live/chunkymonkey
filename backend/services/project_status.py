"""L2 运行时状态的单一现查入口 (goal.md「治理体系重构」P2.2)。

**为什么需要它** (2026-08-11 实测): 项目此前**没有任何一条命令**能回答「数据前沿在哪」。
真相散在两个库的 `accepted_partition` 里 (tushare_raw + smartmoney), 而
`docs/README.md` 让人「查真相源或生成投影 (BOARD.md)」—— BOARD.md 第 6 行却明说
「本文件幂等…数据前沿请查 accepted 分区表，勿据此判断」。**指针指向了一个声明自己
没有这个数的文件**, 于是每个人只好手写一份, 然后各自烂掉 (同日实测: PROJECT_INDEX
同一份文档内部就有 20/46 与 23/46 两个互相矛盾的计数)。

**L2 契约** (goal.md 四层目标态): 状态每次运行都变 → **命令现查, 零文件, 禁人写**。
故本模块**不写任何文件**, 只返回对象; 渲染由调用方负责。

**边界 — 报事实, 不重复裁决**: SLA 是否超标由 `update_watermark_sla.py` 判, 数据是否
有洞由 `check_continuity_integrity.py` 判, cutover 是否生效由
`check_cutover_effective.py` 判 (本模块直接复用它, 不重写一套)。这里只做那件没人做的
事: 把散落的前沿聚到一处并算出**滞后多少个交易日** —— 单一计算点原则。

**诚实降级**: 任何一段取不到就返回 ``{"status": "unavailable", "reason": ...}``,
绝不用 0 / 空 / 上次的值冒充。查不了 ≠ 没问题。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
ALERT_FLAG_GLOB = "chunkymonkey_ALERT_*.flag"
ALERT_FLAG_DIR = Path("/tmp")
# accepted_partition 分布在哪些库 = database_manifest 的别名；逐库探测而不是写死一份
# 名单，因为「哪个库有 accepted_partition」本身会随分层迁移变化。
_CANDIDATE_DB_ALIASES = ("tushare_raw", "smartmoney", "market", "reference")


def _now_iso() -> str:
    # rule-compliance: ok evidence=报告生成时刻元数据，不作为 trade_date 使用
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _is_date_like(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 8 and text.isdigit()


def _connect(alias: str):
    """只读连库 (成员身份见 backend/config/data_module_members.yaml)。"""
    from services.database_manifest import get_database_manifest
    from services.duck_adapter import connect as duck_connect

    path = get_database_manifest().path_for(alias)
    if not Path(path).exists():
        raise FileNotFoundError(f"{alias}: {path} 不存在")
    return duck_connect(str(path), read_only=True)


def _has_table(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def calendar_anchor() -> dict[str, Any]:
    """最近一个已完成收盘的交易日 —— 一切滞后判断的锚, 真相源是交易日历。"""
    try:
        from services.calendar import latest_completed_trade_date

        day = latest_completed_trade_date()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"calendar_unreachable:{type(exc).__name__}")
    if not day:
        return _unavailable("calendar_returned_empty")
    compact = "".join(ch for ch in str(day) if ch.isdigit())[:8]
    if len(compact) != 8:
        return _unavailable(f"calendar_unparseable:{day!r}")
    return {
        "status": "ok",
        "latest_completed_trade_date": compact,
        "source": "services.calendar.latest_completed_trade_date (dim_trading_calendar)",
    }


def _trading_days_after(conn, start_exclusive: str, end_inclusive: str) -> int | None:
    """(start, end] 区间内的交易日个数 —— 滞后只按交易日计，不按自然日。"""
    try:
        from services.data_access import resolver

        cal, own = resolver.dim_read_conn(conn, "dim_trading_calendar")
        try:
            row = cal.execute(
                "SELECT count(*) FROM dim_trading_calendar "
                "WHERE is_trading = 1 AND trade_date > ? AND trade_date <= ?",
                (start_exclusive, end_inclusive),
            ).fetchone()
        finally:
            if own:
                cal.close()
    except Exception:  # noqa: BLE001 — 算不出滞后就标 None，不猜
        return None
    return int(row[0]) if row else None


def _calendar_bounds(value: str) -> str:
    """dim_trading_calendar 存 'YYYY-MM-DD'，accepted_partition 存 'YYYYMMDD'。"""
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if _is_date_like(value) else value


def accepted_frontier(anchor: str | None) -> dict[str, Any]:
    """逐 dataset 的 accepted 前沿 + 距锚点的交易日滞后。"""
    datasets: list[dict[str, Any]] = []
    probed: list[str] = []
    errors: dict[str, str] = {}
    anchor_cal = _calendar_bounds(anchor) if anchor else None

    for alias in _CANDIDATE_DB_ALIASES:
        try:
            conn = _connect(alias)
        except Exception as exc:  # noqa: BLE001
            errors[alias] = f"{type(exc).__name__}: {str(exc)[:80]}"
            continue
        try:
            if not _has_table(conn, "accepted_partition"):
                probed.append(alias)
                continue
            probed.append(alias)
            rows = conn.execute(
                "SELECT dataset_id, max(partition_value) AS frontier, count(*) AS partitions "
                "FROM accepted_partition GROUP BY 1 ORDER BY 1"
            ).fetchall()
            for row in rows:
                dataset_id, frontier, partitions = row[0], str(row[1]), int(row[2])
                lag = None
                if anchor_cal and _is_date_like(frontier):
                    lag = _trading_days_after(conn, _calendar_bounds(frontier), anchor_cal)
                datasets.append(
                    {
                        "dataset_id": dataset_id,
                        "db": alias,
                        "frontier": frontier,
                        "frontier_is_date": _is_date_like(frontier),
                        "partitions": partitions,
                        "lag_trading_days": lag,
                        "period_axis": _period_axis_note(dataset_id, frontier),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors[alias] = f"{type(exc).__name__}: {str(exc)[:80]}"
        finally:
            conn.close()

    if not probed:
        return _unavailable(f"no_database_reachable:{errors}")
    datasets.sort(key=lambda d: (-(d["lag_trading_days"] or 0), d["dataset_id"]))
    return {
        "status": "ok",
        "anchor_trade_date": anchor,
        "databases_probed": probed,
        "datasets": datasets,
        "max_lag_trading_days": max(
            (d["lag_trading_days"] for d in datasets if d["lag_trading_days"] is not None),
            default=None,
        ),
        "errors": errors or None,
        "note": "滞后=距最近已完成交易日的交易日数；期轴数据集(季报等)的该值不构成 SLA 判定",
    }


def _period_axis_note(dataset_id: str, frontier: Any) -> str | None:
    """期轴数据集: 把「落后 N 交易日」这个无意义的大数字换成「下一期什么时候才该有」。

    `org_holding_detail_period` 前沿停在 20260430 会显示「落后 69 交易日」, 看着像断流,
    实际 20260430 正是 Q1 的**法定披露截止日** —— H1 要到 08-31 才依法必须存在。日轴的
    滞后算术套在期轴上只会制造假警报, 而每次都要有人重新论证一遍「这是正常节奏」。
    真相源 = `org_holding_aif10.disclosure_deadline()` 里的监管硬约束, 不在这里重写一份。
    """
    if "_period" not in dataset_id:
        return None
    try:
        from services.org_holding_aif10 import disclosure_deadline
    except Exception:  # noqa: BLE001
        return None
    txt = str(frontier or "")
    if len(txt) != 8 or not txt.isdigit():
        return None
    iso = f"{txt[:4]}-{txt[4:6]}-{txt[6:]}"
    for period_md, label in (("03-31", "Q1"), ("06-30", "H1"), ("09-30", "Q3"), ("12-31", "年报")):
        for year in (int(txt[:4]) - 1, int(txt[:4])):
            period = f"{year}-{period_md}"
            if disclosure_deadline(period) == iso:
                nxt = {"03-31": ("06-30", year), "06-30": ("09-30", year),
                       "09-30": ("12-31", year), "12-31": ("03-31", year + 1)}[period_md]
                nxt_deadline = disclosure_deadline(f"{nxt[1]}-{nxt[0]}")
                return (f"期轴: 前沿={label}({period}) 的法定披露截止; "
                        f"下一期 {nxt[1]}-{nxt[0]} 截止 {nxt_deadline} —— 之前不构成缺口")
    return "期轴数据集: 滞后交易日数不构成 SLA 判定"


def source_watermarks(limit: int = 12) -> dict[str, Any]:
    """`mart_data_source_watermark` 投影 —— 谁最久没进新数据、谁在连续失败。"""
    try:
        conn = _connect("smartmoney")
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"smartmoney_unreachable:{type(exc).__name__}")
    try:
        if not _has_table(conn, "mart_data_source_watermark"):
            return _unavailable("mart_data_source_watermark_missing")
        rows = conn.execute(
            "SELECT data_domain, source_name, last_data_date, consecutive_failures, "
            "fallback_active, row_count FROM mart_data_source_watermark "
            "ORDER BY last_data_date NULLS FIRST, data_domain"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"watermark_query_failed:{type(exc).__name__}")
    finally:
        conn.close()

    items = [
        {
            "data_domain": r[0],
            "source_name": r[1],
            "last_data_date": r[2],
            "consecutive_failures": r[3],
            "fallback_active": bool(r[4]),
            "row_count": r[5],
        }
        for r in rows
    ]
    failing = [i for i in items if (i["consecutive_failures"] or 0) > 0 or i["fallback_active"]]
    return {
        "status": "ok",
        "total": len(items),
        "failing_or_fallback": failing,
        "oldest": items[:limit],
        "truncated_to": limit,
    }


def cutover_effectiveness() -> dict[str, Any]:
    """复用 P1 的 cutover 生效性检查, 不重写一套判据。"""
    try:
        from scripts.check_cutover_effective import evaluate

        return evaluate()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"cutover_check_failed:{type(exc).__name__}: {exc}"[:160])


def gate_distribution() -> dict[str, Any]:
    """门的分组分布 —— goal.md 把「门的实际裁决」也列为 L2 状态。"""
    try:
        from services.governance_gates import KNOWN_GROUPS, load_registry

        reg = load_registry()
        return {
            "status": "ok",
            "groups": {g: reg.names_in_group(g) for g in KNOWN_GROUPS},
            "runtime_checks": [c.id for c in reg.runtime_checks],
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"gate_registry_unavailable:{type(exc).__name__}")


def board_projection() -> dict[str, Any]:
    """轨道 / cutover **意图** / 禁令 / Phase 裁决 —— 从 config 与 lineage artifact 现查。

    与上面的 ``cutover_effectiveness()`` 成对: 这里是 yaml 声明的意图, 那里是 resolver
    的实际裁决。两者必须并排出现 —— 只报意图正是 b_pit 静默失效 13 个交易日的成因。
    """
    try:
        from scripts.agent_board_projection import collect

        d = collect()
        cutovers = d.get("cutovers") or {}
        return {
            "status": "ok",
            "track": (d.get("track") or {}).get("name"),
            "track_status": (d.get("track") or {}).get("status"),
            "cutover_intent": {
                name: (body or {}).get("cutover_allowed")
                for name, body in cutovers.items()
            },
            "phase_e_overall": (d.get("phase_e") or {}).get("overall_status"),
            "bans": len(d.get("bans") or []),
            "full_render": "scripts/chunkyctl agent-boot",
        }
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"board_projection_failed:{type(exc).__name__}")


def alert_flags() -> dict[str, Any]:
    """/tmp 告警 flag —— 上一次跑批留下的未闭合观测。"""
    try:
        flags = sorted(p.name for p in ALERT_FLAG_DIR.glob(ALERT_FLAG_GLOB))
    except OSError as exc:
        return _unavailable(f"flag_dir_unreadable:{type(exc).__name__}")
    return {"status": "ok", "present": flags, "count": len(flags)}


def collect_status() -> dict[str, Any]:
    """一条命令拿全 L2 状态 (goal.md P2.2)。不写文件, 不缓存, 每次现查。"""
    cal = calendar_anchor()
    anchor = cal.get("latest_completed_trade_date") if cal.get("status") == "ok" else None
    return {
        "kind": "project_status",
        "generated_at": _now_iso(),
        "contract": "L2 现查投影 — 零文件, 禁人写; 事实在此, 裁决仍归各自的门",
        "calendar": cal,
        "accepted_frontier": accepted_frontier(anchor),
        "source_watermarks": source_watermarks(),
        "cutovers": cutover_effectiveness(),
        "gates": gate_distribution(),
        "board": board_projection(),
        "alerts": alert_flags(),
    }


def render_text(status: dict[str, Any]) -> str:
    """人读渲染。**不做裁决措辞** —— 只报事实与滞后, 绿/红仍由各自的门说了算。"""
    lines: list[str] = [f"# project status — 现查 {status['generated_at']} (零文件)"]

    cal = status["calendar"]
    lines.append(
        f"\n## 交易日锚\n- {cal.get('latest_completed_trade_date') or cal.get('reason')}"
        f"  ({cal.get('source') or 'unavailable'})"
    )

    fr = status["accepted_frontier"]
    lines.append("\n## accepted 前沿")
    if fr.get("status") != "ok":
        lines.append(f"- unavailable: {fr.get('reason')}")
    else:
        lines.append(f"- 库: {', '.join(fr['databases_probed'])}; 最大滞后 "
                     f"{fr['max_lag_trading_days']} 交易日")
        for d in fr["datasets"]:
            lag = d["lag_trading_days"]
            lag_txt = "—" if lag is None else f"落后 {lag} 交易日"
            lines.append(f"  - {d['dataset_id']:<52} {d['frontier']:<10} {lag_txt}  [{d['db']}]")
            if d.get("period_axis"):
                lines.append(f"      ↳ {d['period_axis']}")
        if fr.get("errors"):
            lines.append(f"  - 探测失败: {fr['errors']}")
        lines.append(f"  - 注: {fr['note']}")

    wm = status["source_watermarks"]
    lines.append("\n## 源水位")
    if wm.get("status") != "ok":
        lines.append(f"- unavailable: {wm.get('reason')}")
    else:
        lines.append(f"- 共 {wm['total']} 源; 连续失败或已 fallback: {len(wm['failing_or_fallback'])}")
        for i in wm["failing_or_fallback"][:8]:
            lines.append(f"  - {i['data_domain']}/{i['source_name']} last={i['last_data_date']} "
                         f"fails={i['consecutive_failures']} fallback={i['fallback_active']}")

    cut = status["cutovers"]
    lines.append("\n## cutover 声明 vs 实际")
    if cut.get("status") == "unavailable":
        lines.append(f"- unavailable: {cut.get('reason')}")
    else:
        lines.append(f"- overall={cut.get('overall')} trade_date={cut.get('trade_date')}")
        for f in cut.get("findings", []):
            lines.append(f"  - [{f.get('status')}] {f.get('check')}: {f.get('detail')}")

    gates = status["gates"]
    lines.append("\n## 门分布")
    if gates.get("status") != "ok":
        lines.append(f"- unavailable: {gates.get('reason')}")
    else:
        for group, names in gates["groups"].items():
            lines.append(f"- {group} ({len(names)}): {' '.join(names)}")
        lines.append(f"- 运行时自检: {' '.join(gates['runtime_checks'])}")

    board = status["board"]
    lines.append("\n## 轨道 / cutover 意图")
    if board.get("status") != "ok":
        lines.append(f"- unavailable: {board.get('reason')}")
    else:
        lines.append(f"- track={board['track']} ({board['track_status']}) "
                     f"phase_e={board['phase_e_overall']} 禁令 {board['bans']} 条")
        lines.append(f"- cutover 意图(yaml): {board['cutover_intent']} — 实际裁决见上一节")
        lines.append(f"- 完整投影: {board['full_render']}")

    alerts = status["alerts"]
    lines.append("\n## 告警 flag")
    if alerts.get("status") != "ok":
        lines.append(f"- unavailable: {alerts.get('reason')}")
    elif not alerts["present"]:
        lines.append("- 无")
    else:
        for name in alerts["present"]:
            lines.append(f"- {name}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="L2 运行时状态单一现查入口 (零文件)")
    ap.add_argument("--json", action="store_true", help="机器读 JSON (agent 用这个)")
    args = ap.parse_args(argv)

    status = collect_status()
    print(json.dumps(status, ensure_ascii=False, indent=2) if args.json else render_text(status))
    # 退出码恒 0: 本命令**报状态不做裁决**。要裁决去跑对应的门
    # (continuity / watermark SLA / cutover_effective), 别让 status 变成第二套判定。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
