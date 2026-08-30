"""check_continuity_integrity — 数据连续性/完整性常驻审查器 (R1 根因 2/4/6 机械门, 2026-07-03).

owner=git log --grep data_foundation_root_causes 根因2 (allow_empty 吞故障空: top_inst 16 缺日 /
block_trade 20250917 中间空洞) + 根因4 (SLA 只测"最近动过"不测"该到的到了没": 分组子榜断流
4 个月 / stk_factor_pro 停 11 天零痕迹) + 根因6 (声明-实测漂移: data_start 错位 5 域 / income 深史
2008-2021 仅 5-15% 覆盖)。把一次性审计 (data_foundation_audit_20260703.json continuity 部分)
固化为 sync_registry 全域驱动的常驻机械门。

语义 (owner 2026-07-23 — 对齐「预期空=正常」):
  PASS / typed OK   应有交易日全在库; 或空洞已按类型豁免:
                    trading calendar 外本就不期望 (周末/休市不进 expected);
                    known_empty_days = 实测 vendor 真空墓碑;
                    hk_holidays = SSE 开市但北向关闭日 (yaml 日历);
                    event_sparse = 事件稀疏域中间空日正常 (尾部 SLA 仍绑).
  WARN              annotate 遗留诚实告警 (优先改 typed 豁免, 禁 mute checker).
  FAIL              **非预期**空洞: 该有数的交易日本地缺且未豁免
                    (例: 非港股假期的 hsgt 空洞; 「日历外」= 豁免日历外的真缺).
  不停牌股无成交、非交易日无 K 线 ≠ Continuity FAIL — 那些本就不在 expected。

九类检测 (--only 单跑; calendar_today_consistency 不在 --only 枚举内, 只在 run_checks 里对 SSE
today 记录单跑一次, 结果并入 calendar_horizon 桶):
  calendar_gaps      日历缺日 (by_trade_date/by_date_range 域): data_start→最新应有交易日逐日对
                     dim_trading_calendar。中间空洞 = FAIL (间歇空响应指纹); 尾部缺日超 SLA = FAIL,
                     未超 = OK。known_empty_days 墓碑排除; gap_tolerance: annotate 降 WARN。
  cross_section      横截面异常 (同域近 60 交易日, 因果窗口): 单日行数 < 近 20 观测日滚动中位 x
                     row_dip_ratio = WARN (known_empty_days ∪ verified_low_days 墓碑排除;
                     row_dip_tolerance: true 域降 pass, 需逐域单独审查声明, 不从 gap_tolerance
                     继承——2026-07-08 教训: stk_surv 曾因 gap_tolerance 掩盖真实 page_limit 截断
                     bug, 见 git log --grep gap_root_cause); grain 含 exchange_id
                     类分组列的域, 基线组当日缺失 = FAIL (known_group_gaps 精确墓碑)。这是唯一
                     会 WARN/FAIL 的日常生产门 (对比 cross_section_full, 见下)。
  group_freshness    分组新鲜度 (声明 freshness_group_col 的域): 各组 MAX(date) 落后 > SLA x 3
                     交易日 = FAIL (分组子榜断流型); dead_groups 墓碑排除。
  declared_vs_actual data_start 声明 vs 实测 MIN(date) 偏差 > 90 自然日 = WARN (带建议修正值);
                     按年行数 / 参照完整年 < 0.3 的年份 = WARN (coverage_note 建议)。
  static_staleness   无日频语义域 (by_ts_code/by_period/by_ann_date/by_code_list/full_refresh):
                     MAX(built_at) 距最新交易日 > SLA x 5 交易日 = WARN (手动刷新域, 只警不 FAIL)。
  cross_section_full 全历史横截面塌陷扫描 (全历史, 前后各 10 日居中窗口, **非因果**——需要未来
                     数据, 故只能 --full-history 显式触发的事后巡检, 状态一律 observe_*/skipped_*,
                     不产出 fail/warn, 不参与 overall 判定)。CV 分层区分高/低信号 dip
                     (稳定域掉一半=high, 高方差域掉一半=low); known_empty_days ∪ verified_low_days
                     排除已核证日; row_dip_tolerance 域整体跳过 (该判据已逐域核证过高方差, 不必
                     全历史重查)。与 cross_section 看似重复, 实为因果窗口 vs 居中窗口的本质差别
                     ——不要合并 (见下方选型指引)。
  completeness_ref   同日行数 + 标的集合双向对账 (声明 completeness_ref 的域): 与 ref_domain 真相
                     域逐日行数比对, 偏差 > tolerance = FAIL; 行数相符再验标的集合是否相符 (防
                     "少一只+多一只"互相抵消掩盖真实缺口)。只在 verified_since 之后强制 (判据
                     自身需先被证明恒成立, 早于该日期的差额可能是 vendor 历史覆盖差异非我方缺口)。
  calendar_horizon   dim_trading_calendar 全局单跑 (非按域): today 之后已登记交易日 < 60 = FAIL
                     (2026-07-06 从孤儿 data_quality.py 迁入真正接进日常跑批, 语义与 static_staleness
                     互补——那个测"多久没刷新"往回看, 这个测"还能撑多远"往前看)。
  calendar_today_consistency  raw_tushare_trade_cal 必须登记 today 的唯一 SSE 开闭市记录, 且
                     raw 的开闭市状态须与 dim_trading_calendar"只存交易日"的语义一致 (raw→dim
                     传导断链探针); 结果并入 calendar_horizon 结果桶, 非独立 --only 类目。

判据强度阶梯 (有真相源就用最强的, 没有再退; 44 个域的作者选判据时看这段, 不必读完全文猜):
  completeness_ref   — 与真相域逐日行数 + 标的集合双向对账。最强, 但需要同粒度真相源, 目前仅
                       daily_basic/moneyflow 有资格
  min_rows_per_batch — 绝对行数底线, 校准自历史健康 histmin; 决定一天算不算 present (喂给
                       calendar_gaps)
  cross_section      — 近 60 交易日、相对近 20 日滚动中位 (因果窗口, 可当日常门)
  cross_section_full — 全历史、相对前后各 10 日的居中中位 + CV 分层 (**非因果窗口**, 需要未来
                       数据, 故只能 observe 事后巡检)
  cross_section 与 cross_section_full 看似重复, 实际是因果窗口 vs 居中窗口的本质差别——前者只看
  过去所以能当生产门, 后者需要未来数据所以只能事后巡检。不要合并它们。

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
from scripts._dip_scan import scan_full_history  # noqa: E402
from scripts._dip_severity import dip_signal_level  # noqa: E402
from services.data_sources.batch_integrity import complete_batch_dates  # noqa: E402

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
CROSS_SECTION_GROUP_COLS = ("exchange_id",)  # grain 含此类列 = 按组检测缺组 (margin exchange_id)
GAP_TOLERANCE_VALUES = {
    "none",
    "annotate",
    "hk_holidays",     # SSE-open / northbound-closed calendar (config/hk_northbound_closed_days.yaml)
    "event_sparse",    # event grain: interior empty days expected; tail SLA still binds
}
HK_NORTHBOUND_CLOSED_PATH = REPO / "backend" / "config" / "hk_northbound_closed_days.yaml"
_HK_NORTHBOUND_CLOSED_CACHE: set[str] | None = None


def load_hk_northbound_closed_days(
    path: Path | None = None,
) -> set[str]:
    """Load typed HK-northbound closed days (compact YYYYMMDD)."""
    global _HK_NORTHBOUND_CLOSED_CACHE
    cfg = path or HK_NORTHBOUND_CLOSED_PATH
    if path is None and _HK_NORTHBOUND_CLOSED_CACHE is not None:
        return set(_HK_NORTHBOUND_CLOSED_CACHE)
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    days = {
        str(d).replace("-", "")
        for d in (raw.get("days") or [])
        if d is not None and str(d).strip()
    }
    if path is None:
        _HK_NORTHBOUND_CLOSED_CACHE = set(days)
    return days

CHECK_IDS = ("calendar_gaps", "cross_section", "group_freshness",
             "declared_vs_actual", "static_staleness", "cross_section_full",
             "completeness_ref", "calendar_horizon")


# ── registry 解析 ─────────────────────────────────────────────────────────

def load_domain_specs(registry_path: Path | None = None) -> list[dict[str, Any]]:
    """sync_registry 全域 → 审查 spec 列表 (含连续性新键; gap_tolerance 非法值 = 立即报错)。"""
    raw = yaml.safe_load((registry_path or REGISTRY_PATH).read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("sync_registry defaults must be a mapping")
    from services.data_sources.formal_boundaries import formal_boundary
    from services.data_sources.margin_ingest import contract_for_spec
    from services.data_sources.sync_runner import domain_spec

    specs: list[dict[str, Any]] = []
    for domain, entry in (raw.get("domains") or {}).items():
        entry = entry or {}
        # 三层继承 (defaults → sources[source] → entry) 走唯一正版实现
        # services.data_sources.sync_runner.domain_spec ("stable public read seam",
        # 37 个调用方), 不在这里重建这条链 —— 2026-08-30 target_db 下沉 sources.<source>
        # 时这里曾手工补层漏掉, 连续性门直接崩 (ValueError: missing outer contract
        # fields: target_db); 消除重复实现 = 根治"下次配置结构再变又漏两处"。
        contract_spec = domain_spec(raw, domain)
        default_db = contract_spec.get("target_db", "tushare_raw")
        margin_contract = contract_for_spec(contract_spec)
        accepted_margin = margin_contract is not None
        boundary = formal_boundary(domain)
        accepted_security_day = (
            boundary is not None
            and isinstance(entry.get("security_day_partition"), dict)
            and bool(boundary.dataset_id)
        )
        table = (
            margin_contract.canonical_table
            if accepted_margin
            else entry.get("target_table")
        )
        if not table:
            continue
        gap_tolerance = entry.get("gap_tolerance", "none")
        if gap_tolerance not in GAP_TOLERANCE_VALUES:
            raise ValueError(
                f"sync_registry 域 {domain}: gap_tolerance={gap_tolerance!r} 非法 "
                f"(允许 {sorted(GAP_TOLERANCE_VALUES)}); 配置错必须修, 不静默按默认跑")
        # 2026-08-23 修(实测发现): verified_low_days 当初设计成 map(日期 -> 核证理由)而非
        # list, 就是为了强制留下"凭什么豁免"的证据 —— 但加载时从不校验, 传 dict 也好、传
        # list 也好都能跑通(反正只取 key), 等于没强制。这里补上校验: 类型必须是 dict,
        # 每条理由必须是有内容的字符串, 否则直接报错, 不静默降级。
        verified_low_raw = entry.get("verified_low_days")
        if verified_low_raw is not None and not isinstance(verified_low_raw, dict):
            raise ValueError(
                f"sync_registry 域 {domain}: verified_low_days 必须是「日期 -> 核证理由」的映射 "
                f"(dict), 收到 {type(verified_low_raw).__name__}; 该字段存在的意义就是强制留下"
                f"「凭什么豁免」的核证证据 —— 传 list 等于放弃这份证据, 等于没核证")
        for low_day, low_reason in (verified_low_raw or {}).items():
            if not isinstance(low_reason, str) or len(low_reason.strip()) < 10:
                raise ValueError(
                    f"sync_registry 域 {domain}: verified_low_days[{low_day!r}] 的核证理由"
                    f"{'缺失' if not isinstance(low_reason, str) or not low_reason.strip() else '过短'} "
                    f"({low_reason!r}); 必须是长度 >= 10 的非空字符串, 写清凭什么核证豁免这一天 "
                    f"—— 没有理由的豁免等于没核证, 光有 dict 结构不校验内容等于没强制")
        exec_pol = entry.get("execution_policy") or {}
        if not isinstance(exec_pol, dict):
            exec_pol = {}
        specs.append({
            "domain": domain,
            "db": entry.get("target_db", default_db),
            "table": table,
            "grain": list(entry.get("grain") or []),
            "batch_mode": entry.get("batch_mode", ""),
            "data_start": str(
                margin_contract.coverage_start
                if accepted_margin
                else (
                    (entry.get("security_day_partition") or {}).get("coverage_start")
                    if accepted_security_day
                    else entry.get("data_start", "")
                )
            ).replace("-", ""),
            "accepted_margin": accepted_margin,
            "accepted_security_day": accepted_security_day,
            "dataset_id": boundary.dataset_id if accepted_security_day else None,
            "_margin_contract": margin_contract,
            "availability_policy": (
                margin_contract.availability_policy.payload()
                if accepted_margin
                else entry.get("availability_policy")
            ),
            # Frozen/disabled domains (e.g. margin scope_blocked): lag is observed,
            # not an actionable continuity FAIL. Parallel to SLA FROZEN_STALE_OBSERVED
            # (CX-4). Does NOT claim Continuity READY / does not thaw catchup.
            "execution_policy_mode": str(exec_pol.get("mode") or "enabled"),
            "execution_policy_reason": str(exec_pol.get("reason") or ""),
            "sla": int(entry.get("freshness_sla_trading_days", 5)),  # evidence: registry 全域均声明; 缺省 5 仅防御
            "available_after": entry.get("available_after"),
            "freshness_date_column": entry.get("freshness_date_column"),
            "date_param": entry.get("date_param"),
            "known_empty_days": {str(d).replace("-", "") for d in (entry.get("known_empty_days") or [])},
            "verified_low_days": {
                str(d).replace("-", "")
                for d in (verified_low_raw or {})
            },
            "gap_tolerance": gap_tolerance,
            "freshness_group_col": entry.get("freshness_group_col"),
            # 同日行数对账声明 (ref_domain/tolerance/verified_since/evidence)。
            # spec 是**白名单**构造: 忘了在这里透传, check_completeness_ref 就会拿到 None
            # 而静默跳过每一个域 —— 门看着在跑、实际一个都没查(本次实测 pass=0 才发现)。
            "completeness_ref": entry.get("completeness_ref"),
            "dead_groups": [str(g) for g in (entry.get("dead_groups") or [])],
            # 2026-07-05 R4 gap 调查发现: known_empty_days/dead_groups 都不覆盖 cross_section 的
            # fail_missing_groups (前者只喂 calendar_gaps, 后者是永久整组豁免) — margin
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
            # 2026-07-08 从 gap_tolerance 拆分独立字段(owner=git log --grep gap_root_cause):
            # gap_tolerance 原语义只覆盖 calendar_gaps(整日缺失), 曾被泛化误用去连带抑制
            # cross_section 的 row_dip——代价是任何"因日历稀疏理由"打了 gap_tolerance 的域,
            # 其行数骤降信号会被无关判断连带静默(stk_surv 正是实例: 2026-07-05 因日历空洞
            # 是真事件稀疏打了 gap_tolerance, 但它同时存在一个从未被单独审查过的系统性
            # page_limit 截断 bug, 若沿用旧泛化逻辑会被这个不相关的标签一直掩盖)。row_dip
            # 抑制必须逐域单独审查+显式声明, 不得从 gap_tolerance 继承。
            "row_dip_tolerance": bool(entry.get("row_dip_tolerance", False)),
            "min_rows_per_batch": int(entry.get("min_rows_per_batch", 0)),
            "min_rows_since": str(entry.get("min_rows_since") or "").replace("-", ""),
            "min_rows_before": int(entry.get("min_rows_before", 1)),
            "batch_completeness": entry.get("batch_completeness") or {},
            "universe_filter": bool(entry.get("universe_filter", False)),
            "universe_filter_col": entry.get("universe_filter_col"),
            "universe_filter_prefixes": entry.get("universe_filter_prefixes"),
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


def _result(check: str, spec: dict, status: str, detail: str, fix_hint: str = "",
            audited: str | None = None) -> dict:
    """audited: **实际被审计的对象**, 与 registry 的 target_table 不同时必须传。

    2026-08-21 实测代价: formal security-day 域(daily/stock_st)审计的是
    accepted_partition, 而这里默认打印 registry 的 target_table(raw_tushare_daily)。
    那张表在 legacy_raw_plane.yaml 里是 role=fill / write=forbidden, 早已停更 ——
    照着输出去查它, 看到的 max(trade_date) 是一个月前的假象。同一天里这个坑
    把我和一个调查 agent 各送进沟里两次以上。输出必须指向真正被查的东西。
    """
    return {"check": check, "domain": spec["domain"], "db": spec["db"],
            "table": audited or spec["table"], "status": status,
            "detail": detail, "fix_hint": fix_hint}


# 对账基准域 -> 物理表。只登记**可当基准**的域: 基准必须自身完整性最强,
# 否则拿一个有缺口的域当尺子, 量出来的都是幻影。daily(K线)是项目地基真相源。
#
# **必须指向真相面, 不能指向 legacy raw 表** (2026-08-22 实测代价):
# 本门初版把基准写成 raw_tushare_daily, 而那张表在 legacy_raw_plane.yaml 里是
# role=fill / write=forbidden 的**停更表**(实测数据止于 2026-07-16, 真相面已到 2026-08-20)。
# 后果不是报错而是**静默失效**: 基准表没有那 25 天, LEFT JOIN 后一行都比不出来,
# 于是这 25 天照样计入"对账通过"的天数 —— 门显示绿, 实际什么都没查。
# 真相面 canonical_nominal_ohlcv_daily 用 dashed 日期(2026-08-20), 与本仓 compact 口径不同,
# 故 SQL 里对基准侧统一 replace(CAST(trade_date AS VARCHAR),'-','') 归一 ——
# 先 CAST 再 replace 能同时吃 DATE 型(真相面)与 VARCHAR 型(legacy/测试 fixture)两种基准表。
_REF_TABLES = {"daily": "canonical_nominal_ohlcv_daily"}

# 血缘图 (data/lineage/graph.json, 449 节点/1271 边) 惰性加载 + 模块级缓存,
# 一个进程只读一次盘 (2026-08-22 FAIL 附下游消费方接线)。哨兵用独立 bool 标记
# "是否已尝试过", 而非拿 None 兼当"未加载"和"加载失败", 否则两种状态混淆。
_LINEAGE_GRAPH_LOAD_ATTEMPTED = False
_LINEAGE_GRAPH_CACHE: Any = None


def _load_lineage_graph_cached() -> Any | None:
    """读 data/lineage/graph.json 一次并缓存; 读不到/格式变了都吞成 None。

    调用方 (_downstream_impact) 外层还有一层 try/except 兜底, 这里的 try/except
    只是为了让"没查到"和"缓存到坏结果" 不混在一起 —— 加载失败不缓存异常对象,
    缓存 None, 下次直接短路不重试 (图不会在同一进程内变好)。
    """
    global _LINEAGE_GRAPH_LOAD_ATTEMPTED, _LINEAGE_GRAPH_CACHE
    if _LINEAGE_GRAPH_LOAD_ATTEMPTED:
        return _LINEAGE_GRAPH_CACHE
    _LINEAGE_GRAPH_LOAD_ATTEMPTED = True
    try:
        from services.lineage.model import LineageGraph

        graph_path = REPO / "data" / "lineage" / "graph.json"
        _LINEAGE_GRAPH_CACHE = LineageGraph.from_dict(
            json.loads(graph_path.read_text(encoding="utf-8"))
        )
    except Exception:  # noqa: BLE001 — 图缺失/格式变化都不该拖垮本门
        _LINEAGE_GRAPH_CACHE = None
    return _LINEAGE_GRAPH_CACHE


def _downstream_impact(table: str) -> dict[str, Any] | None:
    """FAIL 项的下游数据消费方 (血缘 fan-in 查询, 2026-08-22 接线)。

    数据质量门 FAIL 时以前从不查下游 —— 人只知道"某域坏了", 不知道这条坏数据
    已经流到哪些产物里。只算 service/script 两类**真实数据消费方**: config 只是
    声明、test 不是产物, 混进"污染面"会稀释信号。

    任何异常都返回 None (图缺失 / 格式变化 / import 失败一律吞掉) —— 血缘查询
    绝不能把审计门本身的判定弄崩, 查不到下游 ≠ 门本身有问题。
    """
    try:
        from services.lineage.query import impact as _impact

        graph = _load_lineage_graph_cached()
        if graph is None:
            return None
        result = _impact(graph, table)
        by_type = result.get("consumers_by_type", {}) or {}
        consumers = sorted(
            {path for ctype in ("service", "script") for path in by_type.get(ctype, [])}
        )
        return {"consumer_count": len(consumers), "consumers": consumers[:8]}
    except Exception:  # noqa: BLE001 — 审计门判定优先, 血缘查询失败绝不上溯
        return None


def _sample_days(days: list[str], head: int = 5, tail: int = 3) -> str:
    if len(days) <= head + tail:
        return ",".join(days)
    return ",".join(days[:head]) + f",...({len(days) - head - tail} more)...," + ",".join(days[-tail:])


def _backfill_command(domain: str, missing: list[str]) -> str:
    """给出**这个域真的能跑通**的补拉命令。

    2026-08-17 实测: 此前无条件建议 `--domain X --drain`, 但 daily / stock_st 这类
    授权短窗域结构上拒绝无参数 --drain(SyncWindowError: requires explicit --start/--end),
    照提示跑必然失败 —— 一条跑不通的修复建议比不给建议更糟, 它会让人以为工具坏了。
    同时补上 env 与解释器: 裸 python 会得到 authorization_blocked(missing_token) 或
    package_missing, 因为 token 在 .env、依赖在 .venv(见 scripts/daily_update.sh)。
    """
    from services.data_sources.sync_runner import AUTHORIZED_SHORT_WINDOW_DOMAINS

    prefix = "set -a; source .env; set +a; PYTHONPATH=backend .venv/bin/python -m services.data_sources.sync_runner"
    if domain in AUTHORIZED_SHORT_WINDOW_DOMAINS:
        if not missing:
            return f"{prefix} --domain {domain} --start <YYYYMMDD> --end <YYYYMMDD>"
        # 短窗域按缺口首尾成窗补; 窗口跨度受 AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS 限制,
        # 缺口太宽时人需要自己分几段跑 —— 这里不假装能一条命令解决。
        return f"{prefix} --domain {domain} --start {missing[0]} --end {missing[-1]}"
    return f"{prefix} --domain {domain} --drain"


# ── 检测 1: 日历缺日 ─────────────────────────────────────────────────────

def check_calendar_gaps(
    conn,
    spec: dict,
    trading_days: list[str],
    latest_expected: str,
    *,
    accepted_state=None,
) -> dict:
    """data_start→latest_expected 逐交易日对账.

    expected = dim_trading_calendar 交易日 (周末/休市本就不进集合 = 正常空).
    known_empty_days / hk_holidays / event_sparse = 预期空 typed OK.
    中间非豁免空洞 = FAIL (应有却缺); 尾部超 SLA = FAIL; 尾部未超 = OK.

    gap_tolerance:
      none         — interior FAIL
      annotate     — interior WARN (legacy honesty; prefer typed replacements)
      hk_holidays  — interior days in hk_northbound_closed_days → typed pass;
                     residual non-holiday holes FAIL (never mute real missing)
      event_sparse — interior empty expected for event grain → typed pass;
                     tail SLA still FAIL when exceeded
    """
    # 输出里显示的"被审计对象"。formal 域审计 accepted_partition 而非 registry
    # target_table —— 后者(如 raw_tushare_daily)在 legacy_raw_plane.yaml 是
    # write=forbidden 的停更表, 指向它会把查问题的人送去看一个月前的假象。
    audited: str | None = None
    table = spec["table"]
    if spec.get("accepted_margin"):
        from services.data_sources.margin_state import (
            MarginStateError,
            load_margin_accepted_state,
        )

        try:
            state = accepted_state or load_margin_accepted_state(
                conn, contract=spec.get("_margin_contract")
            )
            present = set(state.dates)
        except MarginStateError as exc:
            return _result(
                "calendar_gaps",
                spec,
                "fail_accepted_state",
                f"accepted margin evidence contradictory: {exc}",
            )
        col = "AcceptedPartition"
    elif spec.get("accepted_security_day"):
        # Formal daily/ST truth = accepted_partition frontier (not legacy raw MAX).
        dataset_id = spec.get("dataset_id")
        if not dataset_id:
            return _result(
                "calendar_gaps",
                spec,
                "fail_accepted_state",
                "accepted_security_day missing dataset_id",
            )
        if not _table_exists(conn, "accepted_partition"):
            return _result(
                "calendar_gaps",
                spec,
                "skipped_missing_table",
                "accepted_partition 不存在 (formal 未 bootstrap)",
            )
        present = {
            _norm_day(r[0])
            for r in conn.execute(
                "SELECT DISTINCT partition_value FROM accepted_partition "
                "WHERE dataset_id = ?",
                [dataset_id],
            ).fetchall()
            if r[0] is not None
        }
        col = "accepted_partition.partition_value"
        # 真实审计对象 = accepted_partition 里这个 dataset_id 的分区, 不是 registry 的
        # target_table(那张 raw 表 write=forbidden 早已停更, 见 legacy_raw_plane.yaml)。
        audited = f"accepted_partition[{dataset_id}]"
    elif not _table_exists(conn, table):
        return _result("calendar_gaps", spec, "skipped_missing_table", "表不存在 (域注册未拉/重建期)")
    else:
        col = _resolve_date_col(conn, table, spec)
    if not spec.get("accepted_margin") and not spec.get("accepted_security_day") and col is None:
        return _result("calendar_gaps", spec, "skipped_no_date_col",
                       "无可解析日期列 (freshness_date_column/date_param/trade_date/end_date/ann_date 均缺)")
    if spec.get("accepted_margin") or spec.get("accepted_security_day"):
        pass
    elif spec.get("batch_completeness") or int(spec.get("min_rows_per_batch", 0)) > 0:
        # 截断批/缺市场不能用 DISTINCT(date) 洗白；与 sync drain/SLA 共用完整口径。
        present = complete_batch_dates(conn, spec)
    else:
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
            parts.append(f"中间空洞 {len(interior)} 交易日: {_sample_days(interior)}")
        elif tol == "event_sparse":
            # Typed pass: calendar-day completeness is the wrong metric for event
            # grains (dividend etc.). Checker still enforces tail SLA below.
            parts.append(
                f"event_sparse: {len(interior)} interior empty days expected "
                f"(sample {_sample_days(interior)})"
            )
        elif tol == "hk_holidays":
            hk_closed = load_hk_northbound_closed_days()
            holiday_holes = [d for d in interior if d in hk_closed]
            real_holes = [d for d in interior if d not in hk_closed]
            if real_holes:
                status = "fail_interior_gaps"
                parts.append(
                    f"非港股假期空洞 {len(real_holes)} 交易日: {_sample_days(real_holes)}"
                )
                if holiday_holes:
                    parts.append(
                        f"(另 {len(holiday_holes)} 日匹配 hk_northbound_closed_days)"
                    )
            else:
                parts.append(
                    f"hk_holidays: {len(holiday_holes)} SSE-open/northbound-closed "
                    f"days typed closed (sample {_sample_days(holiday_holes)})"
                )
        else:
            # annotate — legacy WARN; prefer hk_holidays / event_sparse when proven
            status = "warn_interior_gaps"
            parts.append(f"中间空洞 {len(interior)} 交易日: {_sample_days(interior)}")
    if len(tail) > spec["sla"]:
        parts.append(f"尾部断流 {len(tail)} 交易日 > SLA {spec['sla']} (最早缺 {tail[0]})")
        if not status.startswith("fail"):
            status = "fail_stale_tail"
    elif tail:
        parts.append(f"尾部 {len(tail)} 日未到 (SLA {spec['sla']} 内, OK)")
    # Frozen product domains: calendar lag is real but catchup is intentionally
    # blocked (wrong-scope v2 / scope_blocked). Typed observe ≠ silent skip forever
    # and ≠ Continuity READY wash — actionable domains still FAIL normally.
    if (
        status.startswith("fail")
        and str(spec.get("execution_policy_mode") or "enabled") == "disabled"
    ):
        max_present = max(present) if present else None
        reason = str(spec.get("execution_policy_reason") or "execution_disabled")
        parts.append(
            f"frozen_observe mode=disabled/{reason} "
            f"local_max={max_present or 'none'} eligible_end={hi} "
            f"catchup_blocked=true (no product thaw; no mass backfill)"
        )
        status = "observe_frozen_stale"
    detail = "; ".join(parts) if parts else f"{len(expected)} 应有交易日全在库 (date_col={col})"
    hint = ""
    if status == "observe_frozen_stale":
        hint = (
            f"域 {spec['domain']} execution_policy=disabled/"
            f"{spec.get('execution_policy_reason') or 'execution_disabled'}: "
            "continuity observes calendar lag; bounded catchup blocked until "
            "population-scope correction (not an all-due drain target)"
        )
    elif status.startswith("fail") or status.startswith("warn"):
        hint = (
            f"补拉: {_backfill_command(spec['domain'], interior or tail)}; "
            f"源端真空日 -> known_empty_days 墓碑; "
            f"港股假期域 -> hk_northbound_closed_days + gap_tolerance: hk_holidays; "
            f"事件稀疏域 -> gap_tolerance: event_sparse (禁 mute checker)"
        )
    return _result("calendar_gaps", spec, status, detail, hint, audited=audited)


# ── 检测 7: 同日行数对账 (比行数下界强得多的完整性判据) ──────────────────

def check_completeness_ref(conn, spec: dict, trading_days: list[str], latest_expected: str) -> dict:
    """按 registry 的 completeness_ref 声明, 对账该域与基准域的同日行数 + 标的集合。

    **为什么需要它** (2026-08-18 实测): min_rows_per_batch 只能检出"明显残缺",
    逻辑上不可能证明完整 —— 它回答不了"应该有多少行"。而对"每标的每交易日一行"的域,
    应有量是可推导的: 等于当日 K 线的标的数。实测 daily_basic 底线 3,000 而真值 5,197,
    用底线丢 42%% 才报警, 用对账丢 1 行就报警。

    **只在判据被证明成立的时期强制** (verified_since): 同一天里我三次差点用未核证的判据
    落地 —— 近 5 日 moneyflow 与 daily 差额恒 0, 但全历史只有 21.8%% 恒 0(2020-2024 恒 0 率
    为 0, 差额达 -23), 按"必须为 0"设门会天天报红。判据自身必须先被验证, 这是本检测的前提。

    **行数不是终点, 是集合的投影** (2026-08-22 实锤): 只比 count(*) 会被"少一只+多一只"互相
    抵消——基准 {A,B,C}、本域 {A,B,X}, 两边都是 3 行, 行数判定 pass, 但标的其实不同。行数相符
    后必须再验标的集合是否相符。标的列从 registry 的 grain 里取("除日期列外唯一一列"), 取不出
    (grain 未声明该域, 或除日期列外不止一列) 就明确跳过、不猜列名, 回落成纯行数比对。
    """
    ref = spec.get("completeness_ref") or {}
    if not ref:
        return _result("completeness_ref", spec, "skipped_not_declared", "未声明 completeness_ref")
    ref_domain = str(ref.get("ref_domain") or "")
    ref_table = _REF_TABLES.get(ref_domain)
    if not ref_table:
        return _result("completeness_ref", spec, "fail_bad_declaration",
                       f"completeness_ref.ref_domain={ref_domain!r} 无法解析为表 —— 声明本身要先对")
    since = _norm_day(str(ref.get("verified_since") or ""))
    if not since:
        return _result("completeness_ref", spec, "fail_bad_declaration",
                       "completeness_ref 缺 verified_since: 未核证生效区间的对账会把 vendor "
                       "历史覆盖差异误判成我们的缺口")
    tolerance = int(ref.get("tolerance") or 0)
    table = spec["table"]
    if not _table_exists(conn, table) or not _table_exists(conn, ref_table):
        return _result("completeness_ref", spec, "skipped_missing_table", "表不存在")
    col = _resolve_date_col(conn, table, spec)
    if col is None:
        return _result("completeness_ref", spec, "skipped_no_date_col", "无可解析日期列")

    window = [d for d in trading_days if since <= d <= latest_expected]
    if not window:
        return _result("completeness_ref", spec, "skipped_empty_window",
                       f"verified_since={since} 之后无交易日")
    lo, hi = window[0], window[-1]
    try:
        rows = conn.execute(
            f'''
            with mine as (select {col} d, count(*) n from {table}
                          where {col} between ? and ? group by 1),
                 ref as (select replace(CAST(trade_date AS VARCHAR), '-', '') d, count(*) n from {ref_table}
                         where replace(CAST(trade_date AS VARCHAR), '-', '') between ? and ? group by 1)
            select ref.d, ref.n, coalesce(mine.n, 0)
              from ref left join mine on ref.d = mine.d
             where abs(coalesce(mine.n, 0) - ref.n) > ?
             order by ref.d
            ''',
            [lo, hi, lo, hi, tolerance],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 审计边界 fail closed
        return _result("completeness_ref", spec, "fail_query", f"对账查询失败: {exc}")

    if rows:
        worst = max(rows, key=lambda r: abs(int(r[2]) - int(r[1])))
        sample = ", ".join(f"{r[0]}({int(r[2]):,}vs{int(r[1]):,})" for r in rows[:4])
        return _result(
            "completeness_ref", spec, "fail_row_count_mismatch",
            f"{len(rows)} 个交易日与 {ref_domain} 行数不符 (tolerance={tolerance}); "
            f"最大偏差 {worst[0]}: 本域 {int(worst[2]):,} vs {ref_domain} {int(worst[1]):,}; "
            f"样本 {sample}",
            f"补拉: {_backfill_command(spec['domain'], [str(r[0]) for r in rows])}; "
            f"若确认是 vendor 侧覆盖差异 -> 调 completeness_ref.verified_since 并附核证证据, "
            f"不要直接放大 tolerance 掩盖",
        )

    pass_detail = (f"{len(window)} 交易日与 {ref_domain} 同日行数一致 "
                   f"(tolerance={tolerance}, since={since})")

    # 行数相符不代表标的相符。标的列从 grain 取: 除日期列(col)外剩下的唯一一列。
    # grain 未声明该域(测试 fixture 常见)、或剩下的不是恰好一列 -> 不猜, 明确跳过集合差。
    grain = spec.get("grain") or []
    code_candidates = [g for g in grain if g != col]
    code_col = code_candidates[0] if len(code_candidates) == 1 else None
    if code_col is not None:
        mine_cols = _columns(conn, table)
        ref_cols = _columns(conn, ref_table)
        if code_col not in mine_cols or "ts_code" not in ref_cols:
            code_col = None
    if code_col is None:
        return _result("completeness_ref", spec, "pass",
                       pass_detail + "（未做集合差：grain 不支持）")

    try:
        diff_rows = conn.execute(
            f'''
            with mine_codes as (
                select {col} d, "{code_col}" c from {table} where {col} between ? and ?
            ), ref_codes as (
                select replace(CAST(trade_date AS VARCHAR), '-', '') d, ts_code c from {ref_table}
                where replace(CAST(trade_date AS VARCHAR), '-', '') between ? and ?
            ), ref_days as (   -- 基准表当日至少 1 行时, 那天才具备当基准的资格
                select distinct d from ref_codes
            ), missing as (   -- ref 有, mine 没有 (天然只覆盖 ref_days, ref_codes 就是这么来的)
                select d, c from ref_codes except select d, c from mine_codes
            ), extra as (     -- mine 有, ref 没有 —— 但只在 ref 当天真有数据时才算数;
                              -- ref 当天 0 行 (基准表停更/断流) 不能把 mine 的全部标的都判成"多出"
                              -- (2026-08-22 实测: raw_tushare_daily 20260716 后停更 [legacy/停更表,
                              -- 已被 accepted_partition 取代], 之后每个交易日 daily_basic/moneyflow
                              -- 会被误判"多 5541 个" —— 那是基准断流不是本域多出)
                select mc.d, mc.c from mine_codes mc
                where mc.d in (select d from ref_days)
                except
                select d, c from ref_codes
            )
            select d, 'missing' as kind, c from missing
            union all
            select d, 'extra' as kind, c from extra
            order by d, kind, c
            ''',
            [lo, hi, lo, hi],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 审计边界 fail closed
        return _result("completeness_ref", spec, "fail_query", f"标的集合差查询失败: {exc}")

    if not diff_rows:
        return _result("completeness_ref", spec, "pass", pass_detail)

    by_day: dict[str, dict[str, list[str]]] = {}
    for d, kind, c in diff_rows:
        by_day.setdefault(str(d), {"missing": [], "extra": []})[kind].append(str(c))
    worst_day = max(by_day, key=lambda d: len(by_day[d]["missing"]) + len(by_day[d]["extra"]))
    missing_codes, extra_codes = by_day[worst_day]["missing"], by_day[worst_day]["extra"]
    return _result(
        "completeness_ref", spec, "fail_code_set_mismatch",
        f"{len(by_day)} 个交易日与 {ref_domain} 行数相符但标的集合不符 (tolerance={tolerance}); "
        f"最严重 {worst_day}: 缺 {len(missing_codes)} 个(样本 {', '.join(missing_codes[:3])}), "
        f"多 {len(extra_codes)} 个(样本 {', '.join(extra_codes[:3])})",
        f"补拉缺失标的: {_backfill_command(spec['domain'], [worst_day])}; "
        f"多出标的需人工核实来源(可能是 universe 变更/重复注入), 不要盲目 delete 掩盖",
    )


# ── 检测 2: 横截面行数异常 (部分覆盖) ────────────────────────────────────

def check_cross_section(
    conn,
    spec: dict,
    trading_days: list[str],
    latest_expected: str,
    row_dip_ratio: float = ROW_DIP_RATIO_DEFAULT,
    *,
    accepted_state=None,
) -> dict:
    """近 60 交易日: 单日行数 < 近 20 观测日滚动中位 x ratio = WARN;
    grain 含 exchange_id 类分组列: 基线组当日缺失 = FAIL (margin SSE-only 型)。"""
    table = spec["table"]
    accepted_batches: dict[str, str] | None = None
    if spec.get("accepted_security_day"):
        # Dual-path: formal land_then_accept advances accepted_partition while legacy
        # raw may lag. Row-dip on raw would false-FAIL; calendar_gaps owns freshness.
        return _result(
            "cross_section",
            spec,
            "pass",
            "formal security-day freshness owned by accepted_partition "
            f"(dataset_id={spec.get('dataset_id')}); legacy raw row_dip not authoritative",
        )
    if spec.get("accepted_margin"):
        from services.data_sources.margin_state import load_margin_accepted_state

        try:
            state = accepted_state or load_margin_accepted_state(
                conn, contract=spec.get("_margin_contract")
            )
        except Exception as exc:  # noqa: BLE001 — audit boundary must fail closed
            return _result(
                "cross_section",
                spec,
                "fail_accepted_state",
                f"accepted margin evidence contradictory: {exc}",
            )
        if not state.partitions:
            return _result(
                "cross_section",
                spec,
                "fail_no_accepted_partitions",
                "no AcceptedPartition evidence exists for formal margin cross-section",
            )
        accepted_batches = state.batch_by_partition
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
    if accepted_batches is None:
        rows = conn.execute(
            f'SELECT "{col}", COUNT(*) FROM "{table}" WHERE "{col}" >= ? GROUP BY 1', [bound]
        ).fetchall()
        counts = {_norm_day(d): int(n) for d, n in rows if d is not None}
    else:
        rows = conn.execute(
            f'SELECT "{col}", ingest_batch_id FROM "{table}" WHERE "{col}" >= ?',
            [bound],
        ).fetchall()
        counts: dict[str, int] = {}
        for day, batch_id in rows:
            compact = _norm_day(day)
            if accepted_batches.get(compact) == str(batch_id):
                counts[compact] = counts.get(compact, 0) + 1
    seq = [(d, counts[d]) for d in window if d in counts]
    if len(seq) < ROW_DIP_MIN_OBS + 1:
        return _result("cross_section", spec, "skipped_insufficient_history",
                       f"窗口内仅 {len(seq)} 观测日 (< {ROW_DIP_MIN_OBS + 1}), 不判骤降")
    # 2026-08-23 修(实测发现): 豁免必须施加在会报警的门上。verified_low_days 此前只在
    # observe-only 的 check_cross_section_full 里被排除, 这个真正会 WARN 的日常门却只排
    # known_empty_days —— 豁免精确地施加在不需要豁免的地方, 缺失在需要豁免的地方。此前看不
    # 出来只因为 dc_member 那两天 (2025-10-29 / 2026-04-09) 早已滑出 60 日窗口; 与
    # check_cross_section_full 保持一致, 两处都排除 known_empty_days ∪ verified_low_days。
    known_empty = (spec.get("known_empty_days") or set()) | (spec.get("verified_low_days") or set())
    dips: list[str] = []
    for i, (d, n) in enumerate(seq):
        if d in known_empty:
            continue   # 已墓碑的源端单日真异常(如 cyq_perf 20260615)或已核证低值日, 不再重报同一件事
        prior = [c for _, c in seq[max(0, i - ROW_DIP_MEDIAN_WINDOW):i]]
        if len(prior) >= ROW_DIP_MIN_OBS:
            med = statistics.median(prior)
            if med > 0 and n < med * row_dip_ratio:
                dips.append(f"{d}({n}行<中位{med:.0f}x{row_dip_ratio})")

    group_missing: list[str] = []
    gcols = [g for g in spec["grain"] if g in CROSS_SECTION_GROUP_COLS]
    if gcols and gcols[0] in _columns(conn, table):
        gcol = gcols[0]
        if accepted_batches is None:
            grows = conn.execute(
                f'SELECT "{col}", "{gcol}" FROM "{table}" WHERE "{col}" >= ? GROUP BY 1, 2', [bound]
            ).fetchall()
        else:
            candidate_groups = conn.execute(
                f'SELECT "{col}", "{gcol}", ingest_batch_id '
                f'FROM "{table}" WHERE "{col}" >= ? GROUP BY 1, 2, 3',
                [bound],
            ).fetchall()
            grows = [
                (day, group)
                for day, group, batch_id in candidate_groups
                if accepted_batches.get(_norm_day(day)) == str(batch_id)
            ]
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
        # 2026-07-08 修正(owner=git log --grep gap_root_cause): 曾用 gap_tolerance 抑制
        # row_dip(2026-07-08 早前一版逻辑), 但 gap_tolerance 是为 calendar_gaps(整日缺失)设计的
        # 判断, 与 row_dip(行数骤降但非零)是不同的失效模式, 泛化复用导致真实盲区——stk_surv
        # 因日历稀疏理由早被打了 gap_tolerance, 结果它同时存在的系统性 page_limit 截断 bug
        # (丢 22%~87%)被这个不相关的标签连带掩盖, 差点无限期不被发现。row_dip 的容忍必须是
        # 逐域单独审查后显式声明的独立字段(row_dip_tolerance), 不得从 gap_tolerance 继承。
        if spec["row_dip_tolerance"]:
            return _result("cross_section", spec, "pass",
                           f"行数骤降 {len(dips)} 日 (row_dip_tolerance=true, "
                           f"已审定事件驱动天然稀疏/高方差, 非缺陷): {_sample_days(dips)}")
        return _result("cross_section", spec, "warn_row_dip",
                       f"行数骤降 {len(dips)} 日: {_sample_days(dips)}",
                       f"重拉核对是否截断/半空批: --domain {spec['domain']} --drain")
    return _result("cross_section", spec, "pass",
                   f"{len(seq)} 观测日无骤降" + (f", 分组基线完整 (col={gcols[0]})" if gcols else ""))


# ── 检测 2.5: 全历史横截面 dip 扫描 (观测性质, 不参与 overall 判定) ──────────
# check_cross_section 只看近 60 交易日, 历史异常一旦滑出窗口就永久失查
# (owner: _dip_scan.py / _dip_severity.py 已验收纯函数, 2026-08-21)。本检测把
# 两者接进日常审查, 但只在 --full-history 显式要求时跑, 且状态一律 observe_*/
# skipped_* 前缀, 不产出 fail/warn —— 历史噪音不该让日常门变红, 这是一条独立的
# "该不该去人工核实全历史" 信号, 不是可执行的日常 gate。
def check_cross_section_full(conn, spec: dict, *, window: int = 10) -> dict:
    """全历史行数塌陷扫描 + CV 分层 (稳定域掉一半=high, 高方差域掉一半=low)。

    row_dip_tolerance 域已被逐域核证过高方差 (2026-07-08 gap_root_cause 教训:
    row_dip 容忍必须逐域单独声明, 不从 gap_tolerance 继承), 本检测复用同一声明
    直接跳过——不是不查, 是那件事已经查过一次不必全历史重查。
    """
    if spec.get("row_dip_tolerance"):
        return _result("cross_section_full", spec, "skipped_row_dip_tolerance",
                       "该域已声明 row_dip_tolerance（高方差已逐行核证）")
    table = spec["table"]
    if not _table_exists(conn, table):
        return _result("cross_section_full", spec, "skipped_missing_table", "表不存在")
    col = _resolve_date_col(conn, table, spec)
    if col is None:
        return _result("cross_section_full", spec, "skipped_no_date_col", "无可解析日期列")

    dataset_id = spec.get("dataset_id")
    audited = f"accepted_partition[{dataset_id}]" if (
        spec.get("accepted_security_day") or dataset_id) else None

    dips = scan_full_history(conn, table, col,
                             known_empty=(spec.get("known_empty_days") or set()) | (spec.get("verified_low_days") or set()),
                             window=window)

    cv_row = conn.execute(
        f'with per as (select "{col}" d, count(*) n from "{table}" group by 1) '
        f'select stddev_samp(n), avg(n) from per'
    ).fetchone()
    avg_n = cv_row[1] if cv_row else None
    if not avg_n:
        cv = 1.0  # 保守当高方差, 宁可判 low 不误报 high
    else:
        cv = float(cv_row[0] or 0.0) / float(avg_n)

    high_days: list[str] = []
    low_days: list[str] = []
    for d in dips:
        level = dip_signal_level(d["rows"], d["neighbor_median"], cv)
        if level == "high":
            high_days.append(d["date"])
        elif level == "low":
            low_days.append(d["date"])

    if not high_days and not low_days:
        return _result("cross_section_full", spec, "observe_clean",
                       f"全历史无显著 dip（cv={cv:.3f}）", audited=audited)
    if high_days:
        detail = f"高信号 dip {len(high_days)} 个: {_sample_days(high_days[:6])}"
        if low_days:
            detail += f"; 另有 {len(low_days)} 个低信号 dip（高方差域正常波动）"
        return _result("cross_section_full", spec, "observe_high_signal", detail, audited=audited)
    return _result("cross_section_full", spec, "observe_low_signal",
                   f"{len(low_days)} 个 dip 均属高方差域正常波动", audited=audited)


# ── 检测 3: 分组新鲜度 (子榜断流) ────────────────────────────────────────

def check_group_freshness(conn, spec: dict, trading_days: list[str], latest_expected: str) -> dict:
    """声明 freshness_group_col 的域: 各组 MAX(date) 落后 > SLA x 3 交易日 = FAIL;
    dead_groups 墓碑组只标注不告警 (分组子榜断流、表级 SLA 探不到的根治)。"""
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
            declared_dt = date(int(declared[:4]), int(declared[4:6]), int(declared[6:8]))
            actual_dt = date(int(actual_min[:4]), int(actual_min[4:6]), int(actual_min[6:8]))
            drift = abs((declared_dt - actual_dt).days)
        except ValueError:
            drift = 0
            declared_dt = actual_dt = None
            parts.append(f"data_start={declared} 无法解析为日期")
        if drift > DECLARED_DRIFT_CAL_DAYS and declared_dt is not None and actual_dt is not None:
            # accepted coverage_start = obligation frontier, not table MIN. Pre-coverage
            # retention / prior-generation rows (actual_min < coverage_start) are expected
            # and must not be labeled declared_drift (Knife4 typed wrong-door, 2026-07-23).
            # Still WARN when actual_min > coverage_start (under-delivery inside obligation).
            pre_coverage_retention = (
                actual_dt < declared_dt
                and (spec.get("accepted_margin") or spec.get("accepted_security_day"))
            )
            if pre_coverage_retention:
                parts.append(
                    f"coverage_start={declared} vs 表 MIN({col})={actual_min} "
                    f"(pre-coverage retention {drift} 自然日, 义务窗≠全表历史起点, 非 drift)"
                )
            elif spec.get("data_start_reviewed"):
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
_RAW_STATUS_UNCHECKED = object()


def check_calendar_horizon(trading_days: list[str], today_iso: str) -> dict:
    """dim_trading_calendar 里 today 之后仍登记的交易日数 < 60 = FAIL (raw→dim 传导断链
    或 tushare 未发布次年日历); trading_days 复用 _load_calendar() 已加载的全量升序列表,
    不重复查库。"""
    spec = {"domain": "trade_cal", "db": "reference", "table": "dim_trading_calendar"}
    today = _norm_day(today_iso)
    normalized_days = sorted({_norm_day(day) for day in trading_days})
    future_n = len(normalized_days) - bisect_right(normalized_days, today)
    if future_n < CALENDAR_HORIZON_MIN_TRADING_DAYS:
        return _result(
            "calendar_horizon", spec, "fail",
            f"today={today} 之后仅剩 {future_n} 个已登记交易日 (< {CALENDAR_HORIZON_MIN_TRADING_DAYS})",
            "跑 services.calendar_builder.build_latest 并核 trade_cal sync (tushare 可能未发布次年日历)")
    return _result(
        "calendar_horizon", spec, "pass",
        f"today={today} 之后剩 {future_n} 个已登记交易日 (阈值 {CALENDAR_HORIZON_MIN_TRADING_DAYS})")


def check_calendar_today_consistency(
    trading_days: list[str],
    today_iso: str,
    raw_today_is_open: int | None,
) -> dict:
    """SSE raw 必须登记今天，且 raw 开闭市状态须与 dim 的“只存交易日”语义一致。"""
    spec = {"domain": "trade_cal", "db": "reference", "table": "dim_trading_calendar"}
    today = _norm_day(today_iso)
    if raw_today_is_open not in (0, 1):
        return _result(
            "calendar_horizon", spec, "fail",
            f"raw_tushare_trade_cal 缺 today={today} 的唯一 SSE 开闭市记录",
            "在同一 writer lease 下 full-refresh trade_cal，运行 calendar_builder 后重查")
    dim_has_today = today in {_norm_day(day) for day in trading_days}
    expected_dim = bool(raw_today_is_open)
    if dim_has_today != expected_dim:
        return _result(
            "calendar_horizon", spec, "fail",
            f"today={today} raw_is_open={raw_today_is_open} 但 dim_has_today={int(dim_has_today)}",
            "运行 services.calendar_builder.build_latest 修复 raw→dim 传导后重查")
    return _result(
        "calendar_horizon", spec, "pass",
        f"today={today} raw_is_open={raw_today_is_open} 与 dim 一致")


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
    raw_today_is_open: int | None | object = _RAW_STATUS_UNCHECKED,
    now: Any = None,
    full_history: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """全域五类检测: 返回 (results, failures)。conn_for(db_alias) 可注入 (单测内存库)。

    today 默认取 latest_expected (交易日历锚, declared_vs_actual 参照年口径) — 不用 wall-clock
    (calendar gate 纪律: 日期锚一律日历派生)。"""
    today = today or latest_expected
    conns: dict[str, Any] = {}
    conn_errors: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    from services.data_sources.sync_runner import eligible_end_date

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
            domain_latest_expected = latest_expected
            not_yet_eligible = False
            eligibility = None
            if daily:
                eligibility = eligible_end_date(
                    spec,
                    now=now,
                    trading_day_values=trading_days,
                )
                if eligibility.eligible_end:
                    domain_latest_expected = eligibility.eligible_end
                else:
                    not_yet_eligible = True
            accepted_state = None
            accepted_state_error = None
            if (
                spec.get("accepted_margin")
                and daily
                and not not_yet_eligible
                and only in (None, "calendar_gaps", "cross_section")
            ):
                from services.data_sources.margin_state import load_margin_accepted_state

                try:
                    accepted_state = load_margin_accepted_state(
                        conn, contract=spec.get("_margin_contract")
                    )
                except Exception as exc:  # noqa: BLE001 — audit boundary must fail closed
                    accepted_state_error = exc
            if daily and (only in (None, "calendar_gaps")):
                results.append(
                    _result(
                        "calendar_gaps",
                        spec,
                        "skipped_not_yet_eligible",
                        f"当前无已到可用时点的交易分区 ({eligibility.reason})",
                    )
                    if not_yet_eligible
                    else _result(
                        "calendar_gaps",
                        spec,
                        "fail_accepted_state",
                        f"accepted margin evidence contradictory: {accepted_state_error}",
                    )
                    if accepted_state_error is not None
                    else check_calendar_gaps(
                        conn,
                        spec,
                        trading_days,
                        domain_latest_expected,
                        accepted_state=accepted_state,
                    )
                )
            if daily and (only in (None, "cross_section")):
                results.append(
                    _result(
                        "cross_section",
                        spec,
                        "skipped_not_yet_eligible",
                        f"当前无已到可用时点的交易分区 ({eligibility.reason})",
                    )
                    if not_yet_eligible
                    else _result(
                        "cross_section",
                        spec,
                        "fail_accepted_state",
                        f"accepted margin evidence contradictory: {accepted_state_error}",
                    )
                    if accepted_state_error is not None
                    else check_cross_section(
                        conn,
                        spec,
                        trading_days,
                        domain_latest_expected,
                        row_dip_ratio=row_dip_ratio,
                        accepted_state=accepted_state,
                    )
                )
            if full_history and (only in (None, "cross_section_full")):
                results.append(check_cross_section_full(conn, spec))
            if spec["freshness_group_col"] and (only in (None, "group_freshness")):
                results.append(
                    _result(
                        "group_freshness",
                        spec,
                        "skipped_not_yet_eligible",
                        f"当前无已到可用时点的交易分区 ({eligibility.reason})",
                    )
                    if not_yet_eligible
                    else check_group_freshness(
                        conn, spec, trading_days, domain_latest_expected
                    )
                )
            if spec.get("completeness_ref") and (only in (None, "completeness_ref")):
                # 对账门: 只在 verified_since 之后强制, 且基准域自身必须完整(见 _REF_TABLES 注释)
                results.append(
                    check_completeness_ref(conn, spec, trading_days, domain_latest_expected)
                )
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
        if raw_today_is_open is not _RAW_STATUS_UNCHECKED:
            results.append(check_calendar_today_consistency(
                trading_days, today_iso,
                raw_today_is_open if isinstance(raw_today_is_open, int) else None,
            ))
        results.append(check_calendar_horizon(trading_days, today_iso))
    # FAIL 项附下游数据消费方 (2026-08-22 接线) —— 只查 fail, pass/skipped/observe
    # 省这个开销 (且它们本来就没有"坏数据流到哪"这个问题)。统一在这里遍历一遍,
    # 不散进每个 check_* 函数, 只改这一处。
    for r in results:
        if not r["status"].startswith("fail"):
            continue
        impact_info = _downstream_impact(r["table"])
        if not impact_info or not impact_info.get("consumer_count"):
            r["downstream"] = {"consumer_count": 0, "consumers": []}
            r["detail"] = r["detail"] + "；无下游数据消费方"
        else:
            r["downstream"] = impact_info
            preview = ", ".join(impact_info["consumers"][:3])
            r["detail"] = (
                r["detail"]
                + f"；下游数据消费方 {impact_info['consumer_count']} 个: {preview}"
            )
    failures = [r for r in results if r["status"].startswith("fail")
                or (strict and r["status"] == "db_unreachable")]
    return results, failures


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"by_check": {}, "counts": {}}
    for r in results:
        cls = ("fail" if r["status"].startswith("fail")
               else "warn" if r["status"].startswith("warn")
               else "pass" if r["status"] == "pass"
               else "observe" if r["status"].startswith("observe_")
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
    # observe_* (frozen domain lag) does not FAIL/WARN overall — recorded honesty.
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


def _load_raw_today_status() -> int | None:
    """读取今日 SSE 开闭市状态；缺失、重复冲突或非法值一律交给硬门 fail closed。"""
    from zoneinfo import ZoneInfo

    from services.data_access.resolver import connect_ro

    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")  # Phase ψ.5 allowlist: 今日 raw 日历审计，非业务 end_date
    conn = connect_ro("tushare_raw")
    try:
        rows = conn.execute(
            "SELECT DISTINCT TRY_CAST(TRY_CAST(is_open AS DOUBLE) AS INTEGER) "
            "FROM raw_tushare_trade_cal WHERE exchange = 'SSE' "
            "AND REPLACE(CAST(cal_date AS VARCHAR), '-', '') = ?",
            [today],
        ).fetchall()
    finally:
        conn.close()
    values = {row[0] for row in rows if row and row[0] in (0, 1)}
    return values.pop() if len(values) == 1 else None


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
    ap.add_argument("--full-history", action="store_true",
                    help="扫全历史 dip（默认只看近 60 交易日）；结果为观测性质，不参与 overall 判定")
    args = ap.parse_args(argv)

    specs = load_domain_specs()
    trading_days, latest_expected = _load_calendar()
    raw_today_is_open = _load_raw_today_status() if (
        args.only in (None, "calendar_horizon") and args.domain in (None, "trade_cal")
    ) else None
    results, failures = run_checks(
        specs, _default_conn_for, trading_days, latest_expected,
        only=args.only, domain=args.domain, row_dip_ratio=args.row_dip_ratio, strict=args.strict,
        raw_today_is_open=raw_today_is_open, full_history=args.full_history)
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
              f"observe={c.get('observe', 0)} "
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
