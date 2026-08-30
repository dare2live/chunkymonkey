#!/usr/bin/env python3
"""Watermark SLA updater + sync gap auto-alert.

ChunkyMonkey 交付标准 #1 数据管理: watermark 实填 + sync gap auto-alert.

每个 data source 跑 actual max date 跟 watermark.last_data_date 对比:
- actual > watermark → 自动 update watermark
- 有 registry 完整性契约时，watermark > verified complete frontier → 显式纠错回落
- actual - current_date > SLA threshold → alert (log + JSON report)
- watermark stale > SLA threshold → alert (即使没数据 update)

调用 ways:
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --dry-run
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --json-output /tmp/sla.json

被 daily_update.sh Step 1 调用.

SLA per source_tier (Codex R26 architecture audit doc §4.d 设计; 2026-07-02 批7 源名同步 —
tdxhub/akshare 已全退役, 现役源 = tushare + aif10/miaoxiang):
- tier 1 (tushare/miaoxiang 主源): SLA 1 trading day
- tier 2 (aif10 季频域): SLA 2 trading days
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect as duck_connect
from services.data_sources.batch_integrity import (  # noqa: E402
    VerifiedBatchFrontier,
    latest_complete_batch,
)

DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watermark_sla")


@dataclass(frozen=True)
class ActualFrontierProbe:
    actual_date: str | None
    verified_frontier: VerifiedBatchFrontier | None
    state: str
    error: str | None = None

# SLA threshold by source_tier (trading days)
# ── SLA 的**轴** (2026-08-16 立) ────────────────────────────────────────────
# 本文件长期把两种单位混成一个裸数字比较:
#   * tier 默认 与 registry 的 `freshness_sla_trading_days` —— 名字就写着**交易日**
#   * SLA_DAYS_OVERRIDE 的季报值 —— 注释写着「Mar31→Aug31 披露截止 ≈ 153d → 160d」,
#     即**自然日**
# 而实现只有一种算法 `_days_since` = (today - d).days, 纯自然日 (全文件对交易日历的
# 引用数为 0), 再用 `+3` 打「周末缓冲」补丁。
#
# 实测后果 (2023-01-01~2026-08-14, 876 个交易日, sla=1 即真实阈值=自然日>4):
#   漏报 —— 落后 2 个交易日仍静默 **95.0%** 的日子; 落后 3 个 37.6%; 落后 4 个 17.9%
#   误报 —— 域完全合规(仅落后 1 交易日)却判红 **15 次**, 全在长假后首个交易日
# 且 `if actual_days > sla:` 里**只**包着 `if actual_days > sla + 3:`(无 else),
# 于是 registry 里逐域声明的那个值**从不单独触发任何东西**, 真实阈值恒为 sla+3 自然日。
# 本轮日线断流到第 5 个交易日才被发现, 正是这个机制: 前 2-4 个交易日全程静默。
#
# 修法与 MASTER §5.1 对 availability 的要求同源: **量必须带轴**, 不是改算法。
AXIS_TRADING = "trading_days"
AXIS_CALENDAR = "calendar_days"

SLA_DAYS = {1: 1, 2: 2, 3: 3}

# Override: 季度数据 (报告期 quarterly) 单独配置 SLA, 不走 tier
# 季报披露窗: Q1=4/30 / Q2=8/31 / Q3=10/31 / Q4=4/30(年报)
# QFII 用 report_date 水位: Q1→Q2 最坏跨度 ≈ Mar31→Aug31 披露截止 ≈ 153d → 160d 覆盖
# holders 仍 100d (notice_date / page 更密)。rule-compliance: ok evidence=a-share-disclosure-window
# 这两个是**自然日**(季报披露窗, 见各自注释), 故单列轴; 其余一律交易日。
SLA_AXIS_OVERRIDE = {
    "holders_top10_float": AXIS_CALENDAR,
    "qfii_holding_quarterly": AXIS_CALENDAR,
}

SLA_DAYS_OVERRIDE = {
    # financial_gpcw_8q override 已删 2026-06-28 (fact_financial_derived U4 退役, DATA_SOURCE_QUERIES 条目同删)
    "holders_top10_float": 100,      # top10 股东季报
    "qfii_holding_quarterly": 160,   # qfii 季报 (report_date → next disclosure deadline)
}

# CX-4 / P0.1: retired DOMAIN_SPECS rows that still haunt mart_data_source_watermark.
# Allowlist-only delete — never invent tombstones from NO_QUERY_MAPPING alone.
RETIRED_WATERMARK_TOMBSTONES = frozenset(
    {
        # LHB 2026-06-29 → tushare top_list/top_inst; DOMAIN_SPECS entry removed.
        ("lhb_daily", "aif10_lhb"),
        # Owner sunset 2026-07-23: registry tombstoned; leftover wm → NO_QUERY_MAPPING.
        ("sync:stk_factor_pro", "tushare"),
        ("sync:express", "tushare"),
        ("sync:fina_mainbz", "tushare"),
        # ("sync:stk_holdernumber", "tushare") —— 2026-08-23 移除该墓碑(实测与现实矛盾):
        #   墓碑注释称 "Owner sunset 2026-07-23: registry tombstoned", 但 registry 里
        #   execution_policy 为 None、运行时裁定 mode=enabled/reason=active, 无任何退役标记;
        #   当日实跑 2789 批次 / 344,453 行 / ok=True, 数据新鲜。且它有活的消费链:
        #   services/holdernumber_assist.py -> routers/stock_dossier.py(前端股票档案页)。
        #   内容是股东户数(筹码集中度慢变量), owner 确认为策略研究的候选因子之一。
        #   后果: sync_runner 正确写入的 watermark 每次被这行墓碑删掉, 使它成为 44 个域里
        #   唯一没有新鲜度监控的域(goal.md backlog A2 记录的正是此事)。
        # K3 2026-08-28 退役六个零消费域 (commit 60187b9ad): registry 条目已物理删除,
        # 但 mart_data_source_watermark 残留行 → 每日 6 条 NO_QUERY_MAPPING 告警噪音。
        # 已实测确认六者均已不在 sync_registry.yaml (与 stk_holdernumber 那次误判相反)。
        ("sync:daily_info", "tushare"),
        ("sync:dc_daily", "tushare"),
        ("sync:hm_detail", "tushare"),
        ("sync:hm_list", "tushare"),
        ("sync:kpl_list", "tushare"),
        ("sync:ths_hot", "tushare"),
    }
)

# data_domain → actual table + date column
DATA_SOURCE_QUERIES = {
    "kline_daily": {
        "db": "market",
        "query": "SELECT MAX(CAST(date AS VARCHAR)) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'",
    },
    # (退役域条目已清, 详 ledger + git史)
    "holders_top10_float": {
        "db": "smartmoney",
        # Formal notice frontier (canonical), not frozen legacy fact report_date.
        "query": (
            "SELECT MAX(CAST(notice_date AS VARCHAR)) "
            "FROM canonical_top10_float_holders_period"
        ),
    },
    # Strangler observer only — not publication truth. Typed no_probe so unknown≠alert.
    "holders_top10_float_legacy_observer": {
        "db": "smartmoney",
        "no_probe": "legacy_observer_not_publication_truth",
    },
    "qfii_holding_quarterly": {
        "db": "smartmoney",
        "query": (
            "SELECT MAX(CAST(report_date AS VARCHAR)) FROM raw_qfii_holding_quarterly"
        ),
    },
    "industry_dc": {
        # 2026-06-23 单一供应商=东财 (Stage②): 查东财 serving 表 dim_stock_dc_industry.updated_at
        #   (daily_update Step 2.96c build_dc_industry_view 每日刷新; 东财行业=申万对齐)。
        "db": "smartmoney",
        "query": "SELECT CAST(MAX(updated_at) AS VARCHAR) FROM dim_stock_dc_industry",
    },
    # stock_blocks 域已删 (2026-06-23): 原查通达信表, 源退役; 申万行业新鲜度由 industry_sw 域跟踪。
}


class SyncRegistrySLAError(RuntimeError):
    """The registry cannot prove the complete sync-domain SLA inventory."""


def _assert_manual_domain_sla_inventory() -> None:
    """Every live DOMAIN_SPECS domain must have probe or typed no_probe."""
    from services.source_watermarks import DOMAIN_SPECS

    missing = sorted(
        {
            str(spec["data_domain"])
            for spec in DOMAIN_SPECS
            if str(spec.get("data_domain") or "") not in DATA_SOURCE_QUERIES
        }
    )
    if missing:
        raise SyncRegistrySLAError(
            "DOMAIN_SPECS missing SLA mapping (probe or no_probe): "
            + ", ".join(missing)
        )


def _sync_registry_queries(*, registry_path: Path | None = None) -> dict[str, dict]:
    """sync:* 域 SLA 查询从 sync_registry.yaml 自动生成 — registry 驱动, 不手维护.

    复审 HIGH (2026-06-11): sync:* 域无 DATA_SOURCE_QUERIES 条目 → actual=None →
    永不告警 = audit 防线对全部新数据域盲区 (静默腐烂同型)。registry 注册即自动入防线。
    """
    import yaml

    out: dict[str, dict] = {}
    reg_path = registry_path or REPO_ROOT / "backend" / "config" / "sync_registry.yaml"
    try:
        reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(reg, dict):
            raise TypeError("registry root must be a mapping")
        domains = reg.get("domains")
        if not isinstance(domains, dict) or not domains:
            raise ValueError("registry domains must be a non-empty mapping")
        defaults = reg.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise TypeError("registry defaults must be a mapping")
        from services.data_sources.formal_boundaries import formal_boundary
        from services.data_sources.margin_ingest import contract_for_spec

        sources_cfg = reg.get("sources") or {}
        if not isinstance(sources_cfg, dict):
            sources_cfg = {}
        for name, spec in domains.items():
            if not isinstance(name, str) or not name or not isinstance(spec, dict):
                raise TypeError("registry domain entries must be named mappings")
            mode = spec.get("batch_mode")
            # 与 sync_runner.domain_spec 同款三层继承链 defaults → sources[source] → entry。
            # 漏掉中间这层会让 target_db 等已下沉到源级的字段取不到, contract 构造直接抛。
            contract_spec = dict(defaults)
            contract_spec.update(sources_cfg.get(spec.get("source")) or {})
            contract_spec.update(spec)
            contract_spec["domain"] = name
            margin_contract = contract_for_spec(contract_spec)
            boundary = formal_boundary(name)
            if margin_contract is not None:
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "accepted_margin": True,
                    "_margin_contract": margin_contract,
                    "sla_days": spec.get("freshness_sla_trading_days"),
                    "parser_version": f"margin_accepted_contract_{margin_contract.contract_version}",
                }
            elif (
                boundary is not None
                and isinstance(spec.get("security_day_partition"), dict)
                and boundary.dataset_id
            ):
                # Formal daily/ST: SLA judges accepted_partition, not legacy raw MAX.
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "query": (
                        "SELECT MAX(partition_value) FROM accepted_partition "
                        f"WHERE dataset_id = '{boundary.dataset_id}'"
                    ),
                    "sla_days": spec.get("freshness_sla_trading_days"),
                    "formal_accepted_frontier": True,
                    "parser_version": f"security_day_accepted_{boundary.dataset_id}",
                }
            elif spec.get("freshness_no_probe"):
                # 季报/事件域 (forecast/income/dividend): ann_date 淡季数周无新数据=正常, 日频 SLA 会
                # 误报疲劳 (mythos§10); 完整性靠 gap drain 覆盖, 不做日频新鲜度探测。
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": spec.get("freshness_no_probe")}
            elif mode in ("by_trade_date", "by_date_range"):
                # 2026-06-22 P0-6: 日期列从 registry 读 (freshness_date_column 缺省 trade_date) —
                # 旧硬编 trade_date 撞 report_rc(report_date)/stk_surv(surv_date) 等无该列域
                # → BinderException 被 try/except 吞 = SLA 永久盲区。
                date_col = spec.get("freshness_date_column", "trade_date")
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "query": f'SELECT MAX(CAST("{date_col}" AS VARCHAR)) FROM "{spec["target_table"]}"',
                    "sla_days": spec.get("freshness_sla_trading_days"),
                }
                if (
                    spec.get("batch_completeness")
                    or int(spec.get("min_rows_per_batch", 0)) > 0
                ):
                    out[f"sync:{name}"]["verified_complete_spec"] = {
                        **spec,
                        "domain": name,
                    }
            else:
                # 季度 (by_ts_code) / 日历 (full_refresh) 无日频新鲜度语义 — 显式标注, 不静默当 OK
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": f"batch_mode={mode}"}

            # Any disabled domain: observe lag, do not light actionable sla_warn.
            # Key = execution_policy.mode only (on_demand enabled domains stay alertable).
            entry = out.get(f"sync:{name}")
            exec_pol = contract_spec.get("execution_policy") or {}
            if (
                isinstance(entry, dict)
                and isinstance(exec_pol, dict)
                and exec_pol.get("mode") == "disabled"
            ):
                entry["observe_only"] = True
                entry["observe_reason"] = str(
                    exec_pol.get("reason") or "execution_disabled"
                )
    except Exception as exc:  # noqa: BLE001 — incomplete inventory is a blocking SLA failure
        raise SyncRegistrySLAError(
            f"sync_registry unverified: {type(exc).__name__}"
        ) from exc
    return out


def _query_actual_frontier(
    conns: dict, queries: dict, data_domain: str
) -> ActualFrontierProbe:
    spec = queries.get(data_domain)
    if not spec:
        return ActualFrontierProbe(None, None, "no_mapping")
    if spec.get("no_probe"):
        return ActualFrontierProbe(None, None, "no_probe")
    conn = conns.get(spec["db"])
    if conn is None:
        return ActualFrontierProbe(None, None, "db_unavailable")
    try:
        if spec.get("accepted_margin"):
            from services.data_sources.margin_state import (
                load_margin_accepted_state,
            )

            frontier = load_margin_accepted_state(
                conn, contract=spec.get("_margin_contract")
            ).frontier
            if frontier is None:
                return ActualFrontierProbe(None, None, "no_complete_batch")
            return ActualFrontierProbe(frontier.last_date, frontier, "verified")
        if spec.get("verified_complete_spec"):
            frontier = latest_complete_batch(conn, spec["verified_complete_spec"])
            if frontier is None:
                return ActualFrontierProbe(None, None, "no_complete_batch")
            return ActualFrontierProbe(frontier.last_date, frontier, "verified")
        r = conn.execute(spec["query"]).fetchone()
        actual = r[0] if r and r[0] else None
        return ActualFrontierProbe(actual, None, "observed" if actual else "no_data")
    except Exception as e:
        log.warning(f"  query failed for {data_domain}: {e}")
        return ActualFrontierProbe(None, None, "probe_error", str(e)[:300])


def _query_actual_max_date(conns: dict, queries: dict, data_domain: str) -> str | None:
    return _query_actual_frontier(conns, queries, data_domain).actual_date


def _probe_gate(state: str) -> tuple[str | None, bool]:
    """Map probe evidence to a fail-closed status before reconciliation/SLA checks."""
    return {
        "no_mapping": ("NO_QUERY_MAPPING", True),
        "no_probe": ("NO_PROBE_RULE", False),
        # Preflight runs before the writer lease and Store reruns after release;
        # either boundary must fail closed if accepted evidence cannot be read.
        "db_unavailable": ("DB_LOCKED_UNVERIFIED", True),
        "no_complete_batch": ("NO_COMPLETE_BATCH", True),
        "no_data": ("NO_DATA", True),
        "probe_error": ("PROBE_ERROR", True),
    }.get(state, (None, False))


def _accepted_projection_drift(
    *,
    watermark_date: str | None,
    watermark_row_count: int | None,
    watermark_parser_version: str | None,
    frontier: VerifiedBatchFrontier,
    expected_parser_version: str,
) -> list[str]:
    """Audit the formal margin projection without becoming a second writer."""
    drift: list[str] = []
    compact_watermark = str(watermark_date or "").replace("-", "")
    compact_accepted = str(frontier.last_date or "").replace("-", "")
    if compact_watermark != compact_accepted:
        drift.append(f"last_data_date={compact_watermark or None}!={compact_accepted}")
    if int(watermark_row_count or 0) != int(frontier.row_count):
        drift.append(f"row_count={int(watermark_row_count or 0)}!={frontier.row_count}")
    if str(watermark_parser_version or "") != expected_parser_version:
        drift.append(
            f"parser_version={watermark_parser_version!r}!={expected_parser_version!r}"
        )
    return drift


def _registered_domain_without_watermark_result(
    conns: dict,
    queries: dict,
    data_domain: str,
    qspec: dict,
    today: date,
) -> dict:
    """Probe a registered domain even when no watermark row exists."""
    probe = _query_actual_frontier(conns, queries, data_domain)
    status, alert = _probe_gate(probe.state)
    if probe.state in ("verified", "observed"):
        status, alert = "MISSING_WATERMARK", True
    if qspec.get("observe_only") and alert:
        status = f"FROZEN_{status or 'NEVER_SYNCED'}_OBSERVED"
        alert = False
    actual_days = _days_since(probe.actual_date, today)
    return {
        "data_domain": data_domain,
        "source_name": "tushare",
        "source_tier": 2,
        "watermark_date": None,
        "actual_date": probe.actual_date,
        "actual_days_ago": actual_days,
        "watermark_days_ago": None,
        "sla_days": qspec.get("sla_days") or SLA_DAYS[2],
        "status": status or "NEVER_SYNCED",
        "alert": alert,
        "verified_complete_frontier": probe.verified_frontier is not None,
        "watermark_reconcile": None,
        "probe_state": probe.state,
        "probe_error": probe.error,
        "observe_only": bool(qspec.get("observe_only")),
    }


def _watermark_reconcile_direction(
    watermark_date: str | None,
    actual_date: str | None,
    *,
    verified_complete: bool,
) -> str | None:
    """Forward is always safe; rollback requires an explicit verified frontier."""
    if not watermark_date or not actual_date:
        return None
    watermark = str(watermark_date)[:10].replace("-", "")
    actual = str(actual_date)[:10].replace("-", "")
    if watermark < actual:
        return "forward"
    if watermark > actual and verified_complete:
        return "rollback"
    return None


def _apply_watermark_reconcile(
    conn,
    *,
    data_domain: str,
    source_name: str,
    source_tier: int,
    actual_date: str,
    verified_frontier: VerifiedBatchFrontier | None,
) -> None:
    if verified_frontier is not None:
        conn.execute(
            "UPDATE mart_data_source_watermark "
            "SET last_data_date = ?, row_count = ?, "
            "last_success_at = CAST(? AS TIMESTAMP), "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE data_domain = ? AND source_name = ? AND source_tier = ?",
            [
                verified_frontier.last_date,
                verified_frontier.row_count,
                verified_frontier.last_success_at,
                data_domain,
                source_name,
                source_tier,
            ],
        )
        return
    conn.execute(
        "UPDATE mart_data_source_watermark "
        "SET last_data_date = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE data_domain = ? AND source_name = ? AND source_tier = ?",
        [actual_date, data_domain, source_name, source_tier],
    )


# 交易日距离的 owner 是 services.calendar (交易日历真相源的既有 owner) ——
# 它可复用且不该长在脚本里; 就地实现会让本文件越过 800 行 godfile 线,
# 而那时抬棘轮就是橡皮图章(本仓刚批评过同款)。
from services.calendar import (  # noqa: E402
    load_trading_days as _load_trading_days_from,
    trading_days_since as _trading_days_since,
)


def _load_trading_days(conns):
    return _load_trading_days_from((conns or {}).get("reference"))


def _days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    s = str(date_str)[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            d = datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return (today - d).days


def _purge_retired_watermark_tombs(
    conn, *, dry_run: bool = False
) -> list[dict[str, str | int]]:
    """Delete allowlisted retired watermark PKs only (CX-4 / P0.1).

    Never deletes from live DOMAIN_SPECS or invents tombs from probe status.
    """
    from services.source_watermarks import DOMAIN_SPECS

    live_keys = {
        (str(spec["data_domain"]), str(spec["source_name"])) for spec in DOMAIN_SPECS
    }
    purged: list[dict[str, str | int]] = []
    for data_domain, source_name in sorted(RETIRED_WATERMARK_TOMBSTONES):
        if (data_domain, source_name) in live_keys:
            raise RuntimeError(
                f"refusing tombstone purge for live DOMAIN_SPECS entry "
                f"{data_domain}/{source_name}"
            )
        rows = conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date "
            "FROM mart_data_source_watermark "
            "WHERE data_domain = ? AND source_name = ?",
            [data_domain, source_name],
        ).fetchall()
        if not rows:
            continue
        if not dry_run:
            conn.execute(
                "DELETE FROM mart_data_source_watermark "
                "WHERE data_domain = ? AND source_name = ?",
                [data_domain, source_name],
            )
        for row in rows:
            purged.append(
                {
                    "data_domain": str(row[0]),
                    "source_name": str(row[1]),
                    "source_tier": int(row[2]),
                    "last_data_date": str(row[3]) if row[3] is not None else "",
                    "action": "would_delete" if dry_run else "deleted",
                }
            )
            log.info(
                "  [TOMBSTONE:%s] %s/%s tier=%s last_data_date=%s",
                "dry-run" if dry_run else "purge",
                row[0],
                row[1],
                row[2],
                row[3],
            )
    return purged


def main() -> int:
    parser = argparse.ArgumentParser(description="Watermark SLA auto-update + alert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--smartmoney-db", default=str(DEFAULT_SMARTMONEY_DB))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB))
    args = parser.parse_args()

    try:
        _assert_manual_domain_sla_inventory()
        registry_queries = _sync_registry_queries()
    except Exception as exc:  # noqa: BLE001 — never retain a stale green artifact on inventory loss
        try:
            Path(args.json_output).unlink(missing_ok=True)
        except OSError as unlink_exc:
            log.error(
                "cannot remove stale SLA artifact after registry failure: %s",
                type(unlink_exc).__name__,
            )
        log.error("sync_registry SLA inventory blocked: %s", exc)
        return 3
    queries = {**DATA_SOURCE_QUERIES, **registry_queries}

    today = date.today()  # rule-compliance: ok evidence=SLA staleness age 度量(对wall-clock计天龄, 非交易决策)
    log.info(f"=== watermark SLA check @ {today} ===")

    smart_conn = duck_connect(args.smartmoney_db, read_only=False)  # need write
    market_conn = duck_connect(args.market_db, read_only=True)
    # tushare_raw: 回填链持写锁时 read_only 也连不上 (DuckDB 排他) — 显式置 None,
    # 对应域标 DB_LOCKED_UNVERIFIED 而非静默 OK
    raw_conn = None
    try:
        from services.database_manifest import get_database_manifest

        raw_conn = duck_connect(str(get_database_manifest().path_for("tushare_raw")), read_only=True)
    except Exception as e:  # noqa: BLE001 — 锁竞争是回填期常态, 显式降级不挡 SLA 主流程
        log.warning(f"tushare_raw 不可达 (回填链占锁?): {e}")
    # reference = 交易日历真相源。连不上不致命: 交易日轴的域会判 UNVERIFIED 而非静默放行。
    ref_conn = None
    try:
        from services.database_manifest import get_database_manifest

        ref_conn = duck_connect(str(get_database_manifest().path_for("reference")), read_only=True)
    except Exception as e:  # noqa: BLE001
        log.warning(f"reference 库不可达, 交易日轴 SLA 将判 UNVERIFIED: {e}")
    conns = {
        "market": market_conn, "smartmoney": smart_conn,
        "tushare_raw": raw_conn, "reference": ref_conn,
    }
    trading_days = _load_trading_days(conns)
    if trading_days is None:
        log.warning("交易日历不可用 —— 交易日轴的域本轮判 UNVERIFIED, 不按自然日代算")
    try:
        tombstone_purges = _purge_retired_watermark_tombs(
            smart_conn, dry_run=args.dry_run
        )

        watermark_rows = smart_conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date, updated_at, "
            "row_count, parser_version "
            "FROM mart_data_source_watermark ORDER BY data_domain, source_name"
        ).fetchall()
        log.info(f"  watermark rows: {len(watermark_rows)}")

        # 格式契约硬门: last_data_date 必须是 compact8。
        # 2026-08-17 实测这张表曾混着 compact8×41 / dashed10×2 / timestamp×1 ——
        # 而 '-'(0x2D) < '0'(0x30), 于是 ORDER BY 把全表最新的域排到第 3,
        # 字符串比较 '2026-08-14' < '20260810' 为真, 更新的域被判成落后。
        # 归一已落在唯一写入口 (services.source_watermarks.upsert_watermark);
        # 这道门管的是"有人绕过写入口直接 UPDATE 进来"的情况 —— 那正是它当初怎么混进来的。
        from services.source_watermarks import normalize_watermark_day

        malformed = [
            (str(r[0]), str(r[1]), str(r[3]))
            for r in watermark_rows
            if r[3] is not None and normalize_watermark_day(r[3]) != str(r[3])
        ]
        if malformed:
            for domain, source, value in malformed:
                log.error(f"  ✗ watermark 格式违约 {domain}/{source}: {value!r} 不是 compact8")
            log.error(
                f"  {len(malformed)} 行 last_data_date 非 compact8 —— 这张表是"
                "'每个域最新到哪天'的单一真相源, 混格式会让排序与比较给出反向答案。"
                "修: 经 services.source_watermarks.upsert_watermark 重写, 不要直接 UPDATE。"
            )
            return 1

        results: list[dict] = []
        n_update = 0
        n_alert = 0
        for row in watermark_rows:
            (
                data_domain,
                source_name,
                source_tier,
                watermark_date,
                updated_at,
                watermark_row_count,
                watermark_parser_version,
            ) = row

            probe = _query_actual_frontier(
                conns, queries, data_domain
            )
            actual_date = probe.actual_date
            verified_frontier = probe.verified_frontier
            actual_days = _days_since(actual_date, today)
            watermark_days = _days_since(watermark_date, today)
            # SLA 优先序: registry per-domain > 季度 override > tier 默认
            qspec = queries.get(data_domain) or {}
            # **按轴取度量**: 交易日轴用交易日历现算, 自然日轴(季报披露窗)才用自然日龄。
            sla_axis = SLA_AXIS_OVERRIDE.get(data_domain, AXIS_TRADING)
            if sla_axis == AXIS_TRADING:
                measured_days = _trading_days_since(actual_date, today, trading_days)
                axis_unverified = measured_days is None and actual_date is not None
            else:
                measured_days = actual_days
                axis_unverified = False
            sla = (qspec.get("sla_days")
                   or SLA_DAYS_OVERRIDE.get(data_domain)
                   or SLA_DAYS.get(source_tier, 3))

            probe_status, alert = _probe_gate(probe.state)
            status = probe_status or "OK"
            if alert:
                log.warning(
                    f"  [ALERT] {data_domain}/{source_name}: probe={probe.state} "
                    f"error={probe.error or '-'}"
                )

            # 1. watermark 与 actual 对账。普通 raw MAX 只允许前推；只有 registry 完整性
            # 契约算出的 verified frontier 才能证明现水位无效并安全回落。
            reconcile = None
            projection_drift: list[str] = []
            if qspec.get("accepted_margin") and verified_frontier is not None:
                projection_drift = _accepted_projection_drift(
                    watermark_date=watermark_date,
                    watermark_row_count=watermark_row_count,
                    watermark_parser_version=watermark_parser_version,
                    frontier=verified_frontier,
                    expected_parser_version=qspec["parser_version"],
                )
                if projection_drift:
                    if qspec.get("observe_only"):
                        status = "FROZEN_PROJECTION_DRIFT_OBSERVED"
                        alert = False
                        log.info(
                            f"  [OBSERVE] {data_domain}/{source_name}: accepted "
                            f"projection drift={projection_drift} "
                            f"(observe_only={qspec.get('observe_reason')})"
                        )
                    else:
                        status = "ACCEPTED_PROJECTION_DRIFT"
                        alert = True
                        log.warning(
                            f"  [ALERT] {data_domain}/{source_name}: accepted projection "
                            f"drift={projection_drift}"
                        )
            elif not qspec.get("accepted_margin"):
                reconcile = _watermark_reconcile_direction(
                    watermark_date,
                    actual_date,
                    verified_complete=verified_frontier is not None,
                )
                if reconcile:
                    status = (
                        "INVALID_WATERMARK_FRONTIER"
                        if reconcile == "rollback"
                        else "STALE_WATERMARK"
                    )
                    if not args.dry_run:
                        _apply_watermark_reconcile(
                            smart_conn,
                            data_domain=data_domain,
                            source_name=source_name,
                            source_tier=source_tier,
                            actual_date=str(actual_date),
                            verified_frontier=verified_frontier,
                        )
                        n_update += 1
                        log.info(
                            f"  [UPDATE:{reconcile}] {data_domain}/{source_name}: "
                            f"{watermark_date} → {actual_date}"
                        )

            # 2. actual data stale vs SLA
            # 判不出交易日距离 → 明确 UNVERIFIED, 不当作通过("查不了"不等于"没问题")。
            # 2026-08-23 起 measured_days is None 有三种成因, 日志需分辨(见 calendar.py
            # trading_days_since 的实测记录), 后两种都是**真问题伪装成"零延迟"**:
            #   a. 日历表本身取不到
            #   b. today 超出日历覆盖 —— 跨年而交易日历没续订, 此时全部域同时失去 SLA 判定
            #   c. actual_date 晚于 today —— 数据里混进了未来日期(vendor 年份错位等)
            if axis_unverified:
                status = "SLA_UNVERIFIED_NO_CALENDAR"
                alert = True
                if actual_date and trading_days:
                    _probe = _parse_day_for_log(actual_date)
                    if _probe is not None and _probe > today:
                        _why = f"数据日期 {actual_date} 晚于今天 {today} — 疑源端日期错位"
                    elif today > trading_days[-1]:
                        _why = f"交易日历只覆盖到 {trading_days[-1]}, 未续订到今天 {today}"
                    else:
                        _why = "交易日历不可达"
                else:
                    _why = "交易日历不可达"
                log.warning(
                    f"  [UNVERIFIED] {data_domain}/{source_name}: {_why}, "
                    f"无法按交易日判定 SLA (actual={actual_date})"
                )
            elif measured_days is not None and measured_days > sla:
                # `+3` 周末缓冲已删: 它存在只是为了拿自然日近似交易日, 而现在是真按交易日算。
                # 原实现里外层 `> sla` 内还套着 `> sla + 3`(无 else), 等于逐域声明的 SLA
                # 值从不单独触发 —— 现在这一层就是唯一阈值。
                if qspec.get("observe_only"):
                    # Frozen/disabled domain: record lag, do not light sla_warn.
                    status = "FROZEN_STALE_OBSERVED"
                    alert = False
                    log.info(
                        f"  [OBSERVE] {data_domain}/{source_name}: actual "
                        f"{actual_date} ({measured_days} {sla_axis} ago) > SLA {sla} {sla_axis} "
                        f"(observe_only={qspec.get('observe_reason')})"
                    )
                else:
                    status = "DATA_STALE_VS_SLA"
                    alert = True
                    log.warning(
                        f"  [ALERT] {data_domain}/{source_name}: actual {actual_date} "
                        f"({measured_days} {sla_axis} ago) > SLA {sla} {sla_axis} (tier {source_tier})"
                    )

            if alert:
                n_alert += 1
            results.append({
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "watermark_date": str(watermark_date) if watermark_date else None,
                "actual_date": actual_date,
                "actual_days_ago": actual_days,
                "watermark_days_ago": watermark_days,
                "sla_days": sla,
                "sla_axis": sla_axis,
                "measured_days_on_axis": measured_days,
                "status": status,
                "alert": alert,
                "verified_complete_frontier": verified_frontier is not None,
                "watermark_reconcile": reconcile,
                "probe_state": probe.state,
                "probe_error": probe.error,
                "projection_drift": projection_drift,
                "observe_only": bool(qspec.get("observe_only")),
            })

        # registry 域 ∪ watermark 行: 从未成功 sync 的注册域没有 watermark 行 →
        # 不进上面循环 = 注册后一直没跑成会永远隐形。显式补 NEVER_SYNCED 行。
        seen_domains = {r[0] for r in watermark_rows}
        for qd, qspec in queries.items():
            if qd.startswith("sync:") and qd not in seen_domains:
                missing_result = _registered_domain_without_watermark_result(
                    conns, queries, qd, qspec, today
                )
                if missing_result["alert"]:
                    n_alert += 1
                results.append(missing_result)

        # Write JSON report
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "today": str(today),
                "dry_run": args.dry_run,
                "n_updates": n_update,
                "n_alerts": n_alert,
                "tombstone_purges": tombstone_purges,
                "sources": results,
            }, f, ensure_ascii=False, indent=2)
        log.info(f"=== SLA check done: {n_update} watermark updated, {n_alert} alerts ===")
        log.info(f"  Report: {args.json_output}")
        return 2 if n_alert > 0 else 0
    finally:
        try:
            market_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"market_conn close failed: {e}")
        try:
            smart_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"smart_conn close failed: {e}")
        if raw_conn is not None:
            try:
                raw_conn.close()
            except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
                log.warning(f"raw_conn close failed: {e}")


if __name__ == "__main__":
    sys.exit(main())
