"""check_continuity_integrity — 数据连续性/完整性常驻审查器 (R1 根因 2/4/6 机械门, 2026-07-03).

owner=analysis/data_foundation_root_causes_20260703.md 根因2 (allow_empty 吞故障空: top_inst 16 缺日 /
block_trade 20250917 中间空洞) + 根因4 (SLA 只测"最近动过"不测"该到的到了没": ths_hot 热基子榜断流
4 个月 / stk_factor_pro 停 11 天零痕迹) + 根因6 (声明-实测漂移: data_start 错位 5 域 / income 深史
2008-2021 仅 5-15% 覆盖)。把一次性审计 (data_foundation_audit_20260703.json continuity 部分)
固化为 sync_registry 全域驱动的常驻机械门。

六类检测 (--only 单跑):
  calendar_gaps      日历缺日 (by_trade_date/by_date_range 域): data_start→最新应有交易日逐日对
                     dim_trading_calendar。中间空洞 = FAIL (间歇空响应指纹); 尾部缺日超 SLA = FAIL,
                     未超 = OK。known_empty_days 墓碑排除; gap_tolerance: annotate 降 WARN。
  cross_section      横截面异常 (同域范围): 单日行数 < 近 20 观测日滚动中位 x row_dip_ratio = WARN
                     (margin SSE-only 部分覆盖型); grain 含 exchange_id/data_type 类分组列的域,
                     基线组当日缺失 = FAIL。
  group_freshness    分组新鲜度 (声明 freshness_group_col 的域): 各组 MAX(date) 落后 > SLA x 3
                     交易日 = FAIL (ths_hot 子榜断流型); dead_groups 墓碑排除。
  declared_vs_actual data_start 声明 vs 实测 MIN(date) 偏差 > 90 自然日 = WARN (带建议修正值);
                     按年行数 / 参照完整年 < 0.3 的年份 = WARN (coverage_note 建议)。
  static_staleness   无日频语义域 (by_ts_code/by_period/by_ann_date/by_code_list/full_refresh):
                     MAX(built_at) 距最新交易日 > SLA x 5 交易日 = WARN (手动刷新域, 只警不 FAIL)。
  calendar_horizon   dim_trading_calendar 全局单跑 (非按域): today 之后已登记交易日 < 60 = FAIL
                     (2026-07-06 从孤儿 data_quality.py 迁入真正接进日常跑批, 语义与 static_staleness
                     互补——那个测"多久没刷新"往回看, 这个测"还能撑多远"往前看)。

任何 FAIL = exit 1。库不可达默认跳过 (写锁期 read_only attach 同样被拒, CLAUDE §4.5 2026-07-02),
--strict 才 FAIL。

用法:
    PYTHONPATH=backend python backend/scripts/check_continuity_integrity.py             # 人读表格
    ... --json                                                                          # 机器读
    ... --only calendar_gaps                                                            # 单类
    ... --domain moneyflow                                                              # 单域
    ... --row-dip-ratio 0.5                                                             # 骤降阈值
    ... --json-out data/audit/continuity_20260703.json                                  # 证据留档
    ... --alert-flag /tmp/chunkymonkey_ALERT_continuity.flag                            # FAIL 落 flag,
        非 FAIL 自愈清 flag (与既有 /tmp/chunkymonkey_ALERT_*.flag 告警链同模式)

wire: services/pipeline/store.py Step 2.98 (daily_update 尾部, watermark refresh 后)。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO / "backend" / "config" / "sync_registry.yaml"

# ── 判定参数 (任务规格钉死值; 来源 = data_foundation_root_causes_20260703.md R1/R4 设计) ──
DAILY_BATCH_MODES = {"by_trade_date", "by_date_range"}   # 有逐交易日语义的域 (calendar/cross_section 范围)
ROW_DIP_RATIO_DEFAULT = 0.6    # evidence: 任务规格默认值 (margin SSE-only 日 1行 vs 中位2-3行 = 0.33-0.5, 0.6 可捕; CLI --row-dip-ratio 覆盖)
ROW_DIP_WINDOW_TDAYS = 60      # evidence: 任务规格 "每域最近 60 交易日"
ROW_DIP_MEDIAN_WINDOW = 20     # evidence: 任务规格 "近 20 日滚动中位"
ROW_DIP_MIN_OBS = 5            # evidence: 中位数最少观测日 (少于此不判骤降, 防新域首周噪音; 规格未定, 保守取滚动窗 1/4)
GROUP_BASELINE_PRESENCE = 0.8  # evidence: 基线组 = 窗口内 >=80% 观测日出现的组 (偶发组不进基线, 防事件型分组误报)
GROUP_BASELINE_MIN_DAYS = 10   # evidence: 分组基线最少观测日 (窗口不足 10 日不判缺组, 防新域噪音)
GROUP_FRESHNESS_SLA_MULT = 3   # evidence: 任务规格 "任何组落后 > 域 SLA x 3 = FAIL"
STALENESS_SLA_MULT = 5         # evidence: 任务规格 "MAX(built_at) 距今 > SLA x 5 = WARN"
DECLARED_DRIFT_CAL_DAYS = 90   # evidence: 任务规格 "data_start 声明 vs 实测偏差 > 90 自然日 = WARN"
SPARSE_YEAR_RATIO = 0.3        # evidence: 任务规格 "按年行数 / 最近完整年行数 < 0.3 的年份列 coverage_note"
CROSS_SECTION_GROUP_COLS = ("exchange_id", "data_type")  # grain 含此类列 = 按组检测缺组 (margin/ths_hot 型)
GAP_TOLERANCE_VALUES = {"none", "annotate", "hk_holidays"}  # hk_holidays 预留 (源端假期校验未实现, 现按 annotate 处理)

CHECK_IDS = ("calendar_gaps", "cross_section", "group_freshness",
             "declared_vs_actual", "static_staleness", "calendar_horizon")


# ── registry 解析 ─────────────────────────────────────────────────────────

def load_domain_specs(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """sync_registry 全域 → 审查 spec 列表 (含连续性新键; gap_tolerance 非法值 = 立即报错)。"""
    raw = yaml.safe_load((registry_path or REGISTRY_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    default_db = defaults.get("target_db", "tushare_raw")
    specs: list[dict[str, Any]] = []
    for domain, entry in (raw.get("domains") or {}).items():
        entry = entry or {}
        table = entry.get("target_table")
        if not table:
            continue
        gap_tolerance = entry.get("gap_tolerance", "none")
        if gap_tolerance not in GAP_TOLERANCE_VALUES:
            raise ValueError(
                f"sync_registry 域 {domain}: gap_tolerance={gap_tolerance!r} 非法 "
                f"(允许 {sorted(GAP_TOLERANCE_VALUES)}); 配置错必须修, 不静默按默认跑")
        specs.append({
            "domain": domain,
            "db": entry.get("target_db", default_db),
            "table": table,
            "grain": list(entry.get("grain") or []),
            "batch_mode": entry.get("batch_mode", ""),
            "data_start": str(entry.get("data_start", "")).replace("-", ""),
            "sla": int(entry.get("freshness_sla_trading_days", 5)),  # evidence: registry 全域均声明; 缺省 5 仅防御
            "freshness_date_column": entry.get("freshness_date_column"),
            "date_param": entry.get("date_param"),
            "known_empty_days": {str(d).replace("-", "") for d in (entry.get("known_empty_days") or [])},
            "gap_tolerance": gap_tolerance,
            "freshness_group_col": entry.get("freshness_group_col"),
            "dead_groups": [str(g) for g in (entry.get("dead_groups") or [])],
            # 2026-07-05 R4 gap 调查发现: known_empty_days/dead_groups 都不覆盖 cross_section 的
            # fail_missing_groups (前者只喂 calendar_gaps, 后者是永久整组豁免) — margin/ths_hot
            # 需要"某日某组"级精确墓碑 (源端当日确实只回部分组, 不代表该组永久死或整表当日全空)。
            "known_group_gaps": {
                str(d).replace("-", ""): {str(g) for g in (groups or [])}
                for d, groups in (entry.get("known_group_gaps") or {}).items()
            },
            # 2026-07-08 补: declared_drift 若已人工核实(coverage_note 写明源端原因+不需改动的
            # 结论)且登记 data_start_reviewed: true, 门降级为 pass 不再每次重报——WARN 队列该是
            # "未核实"清单, 不该堆积"已核实但机制不认得"的噪音(否则下次 session 会误判成新问题
            # 反复重新调查同一件已结案的事)。仅压 declared_drift 这一条, sparse_history/其余
            # 检测不受影响(那些若仍有信号价值应继续曝光)。
            "data_start_reviewed": bool(entry.get("data_start_reviewed", False)),
        })
    return specs


# ── 日期工具 (ISO/compact/DATE 混存归一) ──────────────────────────────────

def _norm_day(v: Any) -> str:
    """任意日期表示 → compact YYYYMMDD ('2026-07-03' / date / timestamp / '20260703')。"""
    return str(v)[:10].replace("-", "")[:8]


def _iso(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1", [table]
    ).fetchone())


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}


def _resolve_date_col(conn, table: str, spec: dict) -> str | None:
    """域日期列解析: freshness_date_column > date_param > trade_date > end_date > ann_date,
    取第一个真实存在的列 (date_param 可能是 API 参数非列, 如 stk_surv trade_date)。"""
    cols = _columns(conn, table)
    for cand in (spec.get("freshness_date_column"), spec.get("date_param"),
                 "trade_date", "end_date", "ann_date"):
        if cand and cand in cols:
            return cand
    return None


def _date_bound(conn, table: str, col: str, compact_day: str) -> str:
    """WHERE 边界字面量: 按表内实际存储格式 (compact VARCHAR / ISO VARCHAR / DATE) 出对应形态。"""
    row = conn.execute(
        f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 1').fetchone()
    if row is None:
        return compact_day
    return _iso(compact_day) if "-" in str(row[0])[:10] else compact_day


def _lag_trading_days(trading_days: list[str], after: str, upto: str) -> int:
    """(after, upto] 区间内的交易日数 (trading_days 升序 compact)。"""
    return max(0, bisect_right(trading_days, upto) - bisect_right(trading_days, after))


def _result(check: str, spec: dict, status: str, detail: str, fix_hint: str = "") -> dict:
    return {"check": check, "domain": spec["domain"], "db": spec["db"],
            "table": spec["table"], "status": status, "detail": detail, "fix_hint": fix_hint}


def _sample_days(days: list[str], head: int = 5, tail: int = 3) -> str:
    if len(days) <= head + tail:
        return ",".join(days)
    return ",".join(days[:head]) + f",...({len(days) - head - tail} more)...," + ",".join(days[-tail:])


# ── 检测 1: 日历缺日 ─────────────────────────────────────────────────────

def check_calendar_gaps(conn, spec: dict, trading_days: list[str], latest_expected: str) -> dict:
    """data_start→latest_expected 逐交易日对账: 中间空洞=FAIL / 尾部超 SLA=FAIL / 墓碑排除 /
    gap_tolerance=annotate|hk_holidays 时中间空洞降 WARN (标注不失败)。"""
    table = spec["table"]
    if not _table_exists(conn, table):
        return _result("calendar_gaps", spec, "skipped_missing_table", "表不存在 (域注册未拉/重建期)")
    col = _resolve_date_col(conn, table, spec)
    if col is None:
        return _result("calendar_gaps", spec, "skipped_no_date_col",
                       "无可解析日期列 (freshness_date_column/date_param/trade_date/end_date/ann_date 均缺)")
    present = {_norm_day(r[0]) for r in
               conn.execute(f'SELECT DISTINCT "{col}" FROM "{table}"').fetchall()
               if r[0] is not None}
    lo, hi = spec["data_start"], latest_expected
    expected = trading_days[bisect_left(trading_days, lo):bisect_right(trading_days, hi)]
    if not expected:
        return _result("calendar_gaps", spec, "skipped_empty_window",
                       f"data_start={lo} 到 {hi} 无应有交易日")
    known = spec["known_empty_days"]
    # 尾部 = expected 末端连续的"既不在库也非墓碑"日 (墓碑视为已满足, 终止尾部)
    tail: list[str] = []
    for d in reversed(expected):
        if d in present or d in known:
            break
        tail.append(d)
    tail.reverse()
    tail_set = set(tail)
    interior = [d for d in expected if d not in present and d not in known and d not in tail_set]

    parts: list[str] = []
    status = "pass"
    if interior:
        tol = spec["gap_tolerance"]
        if tol == "none":
            status = "fail_interior_gaps"
        else:
            status = "warn_interior_gaps"
            if tol == "hk_holidays":
                parts.append("[gap_tolerance=hk_holidays 源端假期校验未实现, 暂按 annotate 标注]")
        parts.append(f"中间空洞 {len(interior)} 交易日: {_sample_days(interior)}")
    if len(tail) > spec["sla"]:
        parts.append(f"尾部断流 {len(tail)} 交易日 > SLA {spec['sla']} (最早缺 {tail[0]})")
        if not status.startswith("fail"):
            status = "fail_stale_tail"
    elif tail:
        parts.append(f"尾部 {len(tail)} 日未到 (SLA {spec['sla']} 内, OK)")
    detail = "; ".join(parts) if parts else f"{len(expected)} 应有交易日全在库 (date_col={col})"
    hint = ""
    if status.startswith("fail") or status.startswith("warn"):
        hint = (f"补拉: PYTHONPATH=backend python -m services.data_sources.sync_runner "
                f"--domain {spec['domain']} --drain; 源端真空日 -> known_empty_days 墓碑; "
                f"事件稀疏/源端假期域 -> gap_tolerance: annotate")
    return _result("calendar_gaps", spec, status, detail, hint)


# ── 检测 2: 横截面行数异常 (部分覆盖) ────────────────────────────────────

def check_cross_section(conn, spec: dict, trading_days: list[str], latest_expected: str,
                        row_dip_ratio: float = ROW_DIP_RATIO_DEFAULT) -> dict:
    """近 60 交易日: 单日行数 < 近 20 观测日滚动中位 x ratio = WARN;
    grain 含 exchange_id/data_type 类分组列: 基线组当日缺失 = FAIL (margin SSE-only 型)。"""
    table = spec["table"]
    if not _table_exists(conn, table):
        return _result("cross_section", spec, "skipped_missing_table", "表不存在")
    col = _resolve_date_col(conn, table, spec)
    if col is None:
        return _result("cross_section", spec, "skipped_no_date_col", "无可解析日期列")
    hi_i = bisect_right(trading_days, latest_expected)
    window = trading_days[max(0, hi_i - ROW_DIP_WINDOW_TDAYS):hi_i]
    if not window:
        return _result("cross_section", spec, "skipped_empty_window", "日历窗口为空")
    bound = _date_bound(conn, table, col, window[0])
    rows = conn.execute(
        f'SELECT "{col}", COUNT(*) FROM "{table}" WHERE "{col}" >= ? GROUP BY 1', [bound]
    ).fetchall()
    counts = {_norm_day(d): int(n) for d, n in rows if d is not None}
    seq = [(d, counts[d]) for d in window if d in counts]
    if len(seq) < ROW_DIP_MIN_OBS + 1:
        return _result("cross_section", spec, "skipped_insufficient_history",
                       f"窗口内仅 {len(seq)} 观测日 (< {ROW_DIP_MIN_OBS + 1}), 不判骤降")
    known_empty = spec["known_empty_days"]
    dips: list[str] = []
    for i, (d, n) in enumerate(seq):
        if d in known_empty:
            continue   # 已墓碑的源端单日真异常(如 cyq_perf 20260615), 不再重报同一件事
        prior = [c for _, c in seq[max(0, i - ROW_DIP_MEDIAN_WINDOW):i]]
        if len(prior) >= ROW_DIP_MIN_OBS:
            med = statistics.median(prior)
            if med > 0 and n < med * row_dip_ratio:
                dips.append(f"{d}({n}行<中位{med:.0f}x{row_dip_ratio})")

    group_missing: list[str] = []
    gcols = [g for g in spec["grain"] if g in CROSS_SECTION_GROUP_COLS]
    if gcols and gcols[0] in _columns(conn, table):
        gcol = gcols[0]
        grows = conn.execute(
            f'SELECT "{col}", "{gcol}" FROM "{table}" WHERE "{col}" >= ? GROUP BY 1, 2', [bound]
        ).fetchall()
        day_groups: dict[str, set] = {}
        for d, g in grows:
            if d is not None:
                day_groups.setdefault(_norm_day(d), set()).add(str(g))
        obs_days = [d for d in window if d in day_groups]
        if len(obs_days) >= GROUP_BASELINE_MIN_DAYS:
            freq: dict[str, int] = {}
            for d in obs_days:
                for g in day_groups[d]:
                    freq[g] = freq.get(g, 0) + 1
            dead = set(spec["dead_groups"])
            baseline = {g for g, c in freq.items()
                        if c >= GROUP_BASELINE_PRESENCE * len(obs_days) and g not in dead}
            known_group_gaps = spec["known_group_gaps"]
            for d in obs_days:
                miss = sorted(baseline - day_groups[d] - known_group_gaps.get(d, set()))
                if miss:
                    group_missing.append(f"{d}缺组[{','.join(miss)}]")

    if group_missing:
        status = "fail_missing_groups"
        detail = f"分组缺失 {len(group_missing)} 日: {_sample_days(group_missing)}"
        if dips:
            detail += f"; 另行数骤降 {len(dips)} 日: {_sample_days(dips)}"
        hint = (f"重拉核对源端 (--domain {spec['domain']} --start <日> --end <日>); "
                f"源端确实部分覆盖 -> 消费侧需口径标记 (mart n_exchanges 类)")
        return _result("cross_section", spec, status, detail, hint)
    if dips:
        # 2026-07-08 (R4 workflow 2026-07-05 遗留 follow-up 收口, owner=analysis/
        # r4_completion_20260704.md #13 "row_dip 阈值算法改进"): gap_tolerance 已表达"此域
        # 事件驱动天然稀疏/高方差, 人工审过"的判断, 但此前只喂了 calendar_gaps 一个检测维度,
        # 同一份判断没有延伸到 cross_section——两者本是同一件事(该域某些天数量本来就该少),
        # 不该只因为检测切面不同就重报。annotate/hk_holidays 域降 pass 但留可见痕迹, 不静默吞。
        if spec["gap_tolerance"] in ("annotate", "hk_holidays"):
            return _result("cross_section", spec, "pass",
                           f"行数骤降 {len(dips)} 日 (gap_tolerance={spec['gap_tolerance']}, "
                           f"已审定事件驱动天然稀疏/高方差, 非缺陷): {_sample_days(dips)}")
        return _result("cross_section", spec, "warn_row_dip",
                       f"行数骤降 {len(dips)} 日: {_sample_days(dips)}",
                       f"重拉核对是否截断/半空批: --domain {spec['domain']} --drain")
    return _result("cross_section", spec, "pass",
                   f"{len(seq)} 观测日无骤降" + (f", 分组基线完整 (col={gcols[0]})" if gcols else ""))


# ── 检测 3: 分组新鲜度 (子榜断流) ────────────────────────────────────────

def check_group_freshness(conn, spec: dict, trading_days: list[str], latest_expected: str) -> dict:
    """声明 freshness_group_col 的域: 各组 MAX(date) 落后 > SLA x 3 交易日 = FAIL;
    dead_groups 墓碑组只标注不告警 (ths_hot 热基子榜断流 4 个月表级 SLA 探不到的根治)。"""
    table = spec["table"]
    gcol = spec["freshness_group_col"]
    if not _table_exists(conn, table):
        return _result("group_freshness", spec, "skipped_missing_table", "表不存在")
    col = _resolve_date_col(conn, table, spec)
    if col is None or gcol not in _columns(conn, table):
        return _result("group_freshness", spec, "skipped_no_date_col",
                       f"日期列或分组列 {gcol} 不存在")
    rows = conn.execute(
        f'SELECT "{gcol}", MAX("{col}") FROM "{table}" GROUP BY 1').fetchall()
    threshold = spec["sla"] * GROUP_FRESHNESS_SLA_MULT
    dead = set(spec["dead_groups"])
    stalled, tombstoned = [], []
    for g, mx in rows:
        if mx is None:
            continue
        lag = _lag_trading_days(trading_days, _norm_day(mx), latest_expected)
        if lag > threshold:
            if str(g) in dead:
                tombstoned.append(f"{g}(墓碑, max={_norm_day(mx)})")
            else:
                stalled.append(f"{g}(max={_norm_day(mx)}, 落后{lag}交易日>{threshold})")
    if stalled:
        return _result("group_freshness", spec, "fail_group_stalled",
                       f"断流组: {'; '.join(stalled)}"
                       + (f"; 墓碑组: {'; '.join(tombstoned)}" if tombstoned else ""),
                       f"核源端: 子榜真死 -> registry dead_groups 墓碑; 否则补拉 --domain {spec['domain']}")
    detail = f"{len(rows)} 组全新鲜 (阈值 {threshold} 交易日)"
    if tombstoned:
        detail += f"; 墓碑组: {'; '.join(tombstoned)}"
    return _result("group_freshness", spec, "pass", detail)


# ── 检测 4: declared-vs-actual 对账 ──────────────────────────────────────

def check_declared_vs_actual(conn, spec: dict, today: str) -> dict:
    """data_start 声明 vs 实测 MIN(date) 偏差 > 90 自然日 = WARN (带建议修正值);
    深史稀疏: 年行数/参照完整年 < 0.3 的年份 = WARN (income 2008-2021 型, 防回测静默偏样本)。"""
    table = spec["table"]
    if not _table_exists(conn, table):
        return _result("declared_vs_actual", spec, "skipped_missing_table", "表不存在")
    col = _resolve_date_col(conn, table, spec)
    if col is None:
        return _result("declared_vs_actual", spec, "skipped_no_date_col", "无可解析日期列")
    row = conn.execute(f'SELECT MIN("{col}") FROM "{table}"').fetchone()
    if row is None or row[0] is None:
        return _result("declared_vs_actual", spec, "skipped_empty_table", "表 0 行")
    actual_min = _norm_day(row[0])
    declared = spec["data_start"]
    parts: list[str] = []
    hints: list[str] = []
    status = "pass"
    if declared and len(declared) == 8:
        try:
            drift = abs((date(int(declared[:4]), int(declared[4:6]), int(declared[6:8]))
                         - date(int(actual_min[:4]), int(actual_min[4:6]), int(actual_min[6:8]))).days)
        except ValueError:
            drift = 0
            parts.append(f"data_start={declared} 无法解析为日期")
        if drift > DECLARED_DRIFT_CAL_DAYS:
            if spec.get("data_start_reviewed"):
                # 2026-07-08: 人工已核实此 drift 系源端问题(coverage_note 记录原因), 不需改动
                # data_start, 不该每次重报同一件已结案的事——WARN 队列该是"未核实"清单。
                parts.append(f"声明 data_start={declared} vs 实测 MIN({col})={actual_min} "
                             f"偏差 {drift} 自然日 (已人工核实, 见 registry coverage_note, 非本项目问题)")
            else:
                status = "warn_declared_drift"
                parts.append(f"声明 data_start={declared} vs 实测 MIN({col})={actual_min} 偏差 {drift} 自然日")
                hints.append(f'建议修正 data_start: "{actual_min}" (或完成回填后复核)')

    yrows = conn.execute(
        f'SELECT substr(CAST("{col}" AS VARCHAR), 1, 4) AS y, COUNT(*) FROM "{table}" '
        f'WHERE "{col}" IS NOT NULL GROUP BY 1').fetchall()
    year_counts = {str(y): int(n) for y, n in yrows if str(y).isdigit()}
    cur_year = int(today[:4])
    full_years = sorted(int(y) for y in year_counts if int(y) < cur_year)
    if full_years:
        ref_year = full_years[-1]  # 最近完整年 = 参照分母
        ref_n = year_counts[str(ref_year)]
        first_year = int(actual_min[:4])
        sparse: list[str] = []
        for y in full_years:
            if y == ref_year:
                continue
            n = year_counts[str(y)]
            frac = 1.0
            if y == first_year and actual_min[4:] != "0101":
                start = date(y, int(actual_min[4:6]), int(actual_min[6:8]))
                frac = max(1, (date(y, 12, 31) - start).days + 1) / 365.0  # 首年按覆盖天数折算
            if ref_n > 0 and (n / frac) / ref_n < SPARSE_YEAR_RATIO:
                sparse.append(f"{y}({n}行, {(n / frac) / ref_n:.2f}x参照年{ref_year})")
        if sparse:
            # 2026-07-08: data_start_reviewed 域的深史稀疏年份与其 declared_drift 是同一份人工
            # 核实结论覆盖的同一现象(早期孤例行, coverage_note 已一并说明), 不该在压掉 drift 后
            # 又从 sparse_history 分支重新冒出同一件事——门降级不彻底=噪音仍在只是换了个标签。
            if not spec.get("data_start_reviewed"):
                status = "warn_sparse_history" if status == "pass" else status
                hints.append("registry 加 coverage_note 注明可用窗口, 防回测静默偏样本")
            parts.append(f"深史稀疏年份: {_sample_days(sparse, 6, 2)}"
                         + (" (已人工核实, 见 coverage_note)" if spec.get("data_start_reviewed") else ""))
    detail = "; ".join(parts) if parts else f"声明-实测对齐 (MIN({col})={actual_min})"
    return _result("declared_vs_actual", spec, status, detail, "; ".join(hints))


# ── 检测 5: 无日频语义域断流 ─────────────────────────────────────────────

def check_static_staleness(conn, spec: dict, trading_days: list[str], latest_expected: str) -> dict:
    """by_ts_code 等手动刷新域: MAX(built_at) 距最新交易日 > SLA x 5 交易日 = WARN (只警不 FAIL —
    stk_factor_pro 停 11 天零痕迹型; 这类域无 drain, 靠人工/专门调度刷新)。"""
    table = spec["table"]
    if not _table_exists(conn, table):
        return _result("static_staleness", spec, "skipped_missing_table", "表不存在")
    cols = _columns(conn, table)
    probe_col = "built_at" if "built_at" in cols else _resolve_date_col(conn, table, spec)
    if probe_col is None:
        return _result("static_staleness", spec, "skipped_no_date_col",
                       "无 built_at 也无可解析日期列")
    row = conn.execute(f'SELECT MAX("{probe_col}") FROM "{table}"').fetchone()
    if row is None or row[0] is None:
        return _result("static_staleness", spec, "skipped_empty_table", "表 0 行")
    mx = _norm_day(row[0])
    threshold = spec["sla"] * STALENESS_SLA_MULT
    lag = _lag_trading_days(trading_days, mx, latest_expected)
    if lag > threshold:
        return _result("static_staleness", spec, "warn_stalled",
                       f"MAX({probe_col})={mx} 落后 {lag} 交易日 > SLA x {STALENESS_SLA_MULT} = {threshold}",
                       f"手动刷新: --domain {spec['domain']}"
                       + (" --resume" if spec["batch_mode"] == "by_ts_code" else ""))
    return _result("static_staleness", spec, "pass",
                   f"MAX({probe_col})={mx} 落后 {lag} 交易日 (阈值 {threshold})")


# ── 检测 6: 日历前瞻余量 (2026-07-06 从孤儿 data_quality.py 迁入, 真正接进日常跑批) ──
# 阈值来源 (R1 根因4, 2026-07-03 原始设计): sync_registry.yaml trade_cal 注释 "检查 max(cal_date)
# > today+30" 从未落码 (静默停摆模式下 watermark 门永绿); 60 交易日 ≈ 3 个月缓冲 — 覆盖 tushare
# 年度日历发布节奏 (每年 Q4 发次年) + 人工响应期。与 static_staleness 语义互补而非重复:
# static_staleness 测"多久没刷新"(往回看), 本检测测"已登记的日历还能撑多远"(往前看) ——
# 即使日历"刚刷新过"也可能只覆盖到未来很浅, 静默限制任何"从今天起数 N 个未来交易日"的运算
# (embargo/purge 窗口等) 悄悄少算而不报错。
CALENDAR_HORIZON_MIN_TRADING_DAYS = 60


def check_calendar_horizon(trading_days: list[str], today_iso: str) -> dict:
    """dim_trading_calendar 里 today 之后仍登记的交易日数 < 60 = FAIL (raw→dim 传导断链
    或 tushare 未发布次年日历); trading_days 复用 _load_calendar() 已加载的全量升序列表,
    不重复查库。"""
    spec = {"domain": "trade_cal", "db": "reference", "table": "dim_trading_calendar"}
    future_n = len(trading_days) - bisect_right(trading_days, today_iso)
    if future_n < CALENDAR_HORIZON_MIN_TRADING_DAYS:
        return _result(
            "calendar_horizon", spec, "fail",
            f"today={today_iso} 之后仅剩 {future_n} 个已登记交易日 (< {CALENDAR_HORIZON_MIN_TRADING_DAYS})",
            "跑 services.calendar_builder.build_latest 并核 trade_cal sync (tushare 可能未发布次年日历)")
    return _result(
        "calendar_horizon", spec, "pass",
        f"today={today_iso} 之后剩 {future_n} 个已登记交易日 (阈值 {CALENDAR_HORIZON_MIN_TRADING_DAYS})")


# ── 编排 ─────────────────────────────────────────────────────────────────

def run_checks(
    specs: list[dict[str, Any]],
    conn_for: Callable[[str], Any],
    trading_days: list[str],
    latest_expected: str,
    *,
    only: str | None = None,
    domain: str | None = None,
    row_dip_ratio: float = ROW_DIP_RATIO_DEFAULT,
    today: str | None = None,
    strict: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """全域五类检测: 返回 (results, failures)。conn_for(db_alias) 可注入 (单测内存库)。

    today 默认取 latest_expected (交易日历锚, declared_vs_actual 参照年口径) — 不用 wall-clock
    (calendar gate 纪律: 日期锚一律日历派生)。"""
    today = today or latest_expected
    conns: dict[str, Any] = {}
    conn_errors: dict[str, str] = {}
    results: list[dict[str, Any]] = []

    def _conn(alias: str):
        if alias not in conns:
            try:
                conns[alias] = conn_for(alias)
            except Exception as exc:  # noqa: BLE001 — 写锁期 read_only attach 同样被拒
                conns[alias] = None
                conn_errors[alias] = str(exc)[:160]
        return conns[alias]

    try:
        for spec in specs:
            if domain and spec["domain"] != domain:
                continue
            conn = _conn(spec["db"])
            if conn is None:
                results.append(_result("all", spec, "db_unreachable",
                                       f"库不可达: {conn_errors.get(spec['db'], '写锁/缺文件')}"))
                continue
            daily = spec["batch_mode"] in DAILY_BATCH_MODES
            if daily and (only in (None, "calendar_gaps")):
                results.append(check_calendar_gaps(conn, spec, trading_days, latest_expected))
            if daily and (only in (None, "cross_section")):
                results.append(check_cross_section(conn, spec, trading_days, latest_expected,
                                                   row_dip_ratio=row_dip_ratio))
            if spec["freshness_group_col"] and (only in (None, "group_freshness")):
                results.append(check_group_freshness(conn, spec, trading_days, latest_expected))
            if spec["batch_mode"] != "full_refresh" and (only in (None, "declared_vs_actual")):
                # full_refresh 域 data_start 是占位 (注册日), 无回填语义, 不对账
                results.append(check_declared_vs_actual(conn, spec, today))
            if not daily and (only in (None, "static_staleness")):
                results.append(check_static_staleness(conn, spec, trading_days, latest_expected))
    finally:
        for c in conns.values():
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
    # calendar_horizon: 全局单跑一次, 不挂在任一 registry 域上 (直接查 trading_days 已加载的
    # dim_trading_calendar 全量列表, 不重复连库)。domain 过滤器只在显式指定其他域时跳过。
    if (only in (None, "calendar_horizon")) and (domain in (None, "trade_cal")):
        from zoneinfo import ZoneInfo
        today_iso = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        results.append(check_calendar_horizon(trading_days, today_iso))
    failures = [r for r in results if r["status"].startswith("fail")
                or (strict and r["status"] == "db_unreachable")]
    return results, failures


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"by_check": {}, "counts": {}}
    for r in results:
        cls = ("fail" if r["status"].startswith("fail")
               else "warn" if r["status"].startswith("warn")
               else "pass" if r["status"] == "pass"
               else "db_unreachable" if r["status"] == "db_unreachable"
               else "skipped")
        summary["counts"][cls] = summary["counts"].get(cls, 0) + 1
        bc = summary["by_check"].setdefault(r["check"], {})
        bc[cls] = bc.get(cls, 0) + 1
    return summary


def overall_status(results: list[dict[str, Any]], strict: bool = False) -> str:
    if any(r["status"].startswith("fail") for r in results):
        return "FAIL"
    if strict and any(r["status"] == "db_unreachable" for r in results):
        return "FAIL"
    if any(r["status"].startswith("warn") for r in results):
        return "WARN"
    return "PASS"


def write_alert_flag(flag_path: Path, overall: str, results: list[dict[str, Any]]) -> None:
    """FAIL 落 flag (session 启动 /tmp/chunkymonkey_ALERT_*.flag 检查会看到); 非 FAIL 自愈清 flag。"""
    if overall == "FAIL":
        lines = [f"[{datetime.now().strftime('%F %T')}] continuity/integrity 审查 FAIL"]
        for r in results:
            if r["status"].startswith("fail"):
                lines.append(f"  [{r['status']}] {r['check']} {r['domain']} {r['table']}: {r['detail'][:200]}")
        flag_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        flag_path.unlink(missing_ok=True)


# ── 生产入口 ─────────────────────────────────────────────────────────────

def _default_conn_for(alias: str):
    import duckdb

    from services.database_manifest import get_database_manifest
    return duckdb.connect(  # rule-compliance: ok evidence=read_only 审计连接 (连续性扫描, 不写)
        str(get_database_manifest().path_for(alias)), read_only=True)


def _load_calendar() -> tuple[list[str], str]:
    """reference dim_trading_calendar → (全部交易日 compact 升序, 最新应有交易日 compact)。"""
    from services.calendar import latest_completed_trade_date
    from services.data_access.resolver import connect_ro
    conn = connect_ro("reference")
    try:
        days = [_norm_day(r[0]) for r in conn.execute(
            "SELECT trade_date FROM dim_trading_calendar WHERE is_trading = 1 ORDER BY trade_date"
        ).fetchall()]
        latest = latest_completed_trade_date(conn)
    finally:
        conn.close()
    if not days or not latest:
        raise SystemExit("dim_trading_calendar 空或 latest_completed_trade_date 失败 — 日历地基先修 (根因3), 本审查拒绝无锚跑")
    return days, _norm_day(latest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据连续性/完整性常驻审查 (五类; 任何 FAIL = exit 1)")
    ap.add_argument("--json", action="store_true", help="JSON 输出到 stdout")
    ap.add_argument("--json-out", default=None, help="JSON 结果写文件 (证据留档)")
    ap.add_argument("--only", choices=CHECK_IDS, default=None, help="只跑单类检测")
    ap.add_argument("--domain", default=None, help="只查单域 (registry 域名)")
    ap.add_argument("--row-dip-ratio", type=float, default=ROW_DIP_RATIO_DEFAULT,
                    help=f"横截面骤降阈值 (默认 {ROW_DIP_RATIO_DEFAULT})")
    ap.add_argument("--alert-flag", default=None,
                    help="FAIL 时写告警 flag 文件, 非 FAIL 自愈删除 (告警链模式)")
    ap.add_argument("--strict", action="store_true",
                    help="库不可达 (写锁占用等) 也算 FAIL (默认跳过并标 db_unreachable)")
    args = ap.parse_args(argv)

    specs = load_domain_specs()
    trading_days, latest_expected = _load_calendar()
    results, failures = run_checks(
        specs, _default_conn_for, trading_days, latest_expected,
        only=args.only, domain=args.domain, row_dip_ratio=args.row_dip_ratio, strict=args.strict)
    overall = overall_status(results, strict=args.strict)
    payload = {"overall": overall, "latest_expected": latest_expected,
               "checks": results, "summary": summarize(results)}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    else:
        for r in results:
            if r["status"] == "pass":
                continue
            print(f"[{r['status']}] {r['check']:<20} {r['domain']:<24} {r['table']}")
            print(f"    {r['detail']}")
            if r["fix_hint"]:
                print(f"    fix: {r['fix_hint']}")
        c = payload["summary"]["counts"]
        print(f"continuity-integrity: overall={overall} "
              f"pass={c.get('pass', 0)} warn={c.get('warn', 0)} fail={c.get('fail', 0)} "
              f"skipped={c.get('skipped', 0)} db_unreachable={c.get('db_unreachable', 0)} "
              f"(latest_expected={latest_expected})")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if args.alert_flag:
        write_alert_flag(Path(args.alert_flag), overall, results)
    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
